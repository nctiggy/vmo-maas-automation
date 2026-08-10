#!/usr/bin/env python3
# --- Start MAAS 1.0 script metadata ---
# name: 31-kubevirt-redfish-bmc
# title: KubeVirt Redfish BMC auto-configuration
# description: Populates Redfish power configuration for KubeVirt VMs whose BMC is provided
#   by kubevirtBMC. Physical machines are skipped without side effects.
# script_type: commissioning
# tags: bmc-config, enlisting
# packages: {apt: [dmidecode]}
# timeout: 00:03:00
# --- End MAAS 1.0 script metadata ---
#
# =============================================================================
# WHY THIS EXISTS
# =============================================================================
# MaaS auto-populates power config during commissioning via any script tagged
# `bmc-config`: MaaS exports BMC_CONFIG_PATH, the script writes a small YAML
# document there, and MaaS applies it to the machine's power configuration.
#
# The built-in 30-maas-01-bmc-config detects Redfish by reading SMBIOS Type 42
# (the DMTF "Redfish Host Interface" record) with `dmidecode -t 42`. Physical
# servers advertise their BMC there in firmware. KubeVirt VMs have no Type 42
# record -- their BMC lives OUTSIDE the guest, as a kubevirtBMC pod reached over
# an ingress -- so the built-in correctly finds nothing and writes nothing.
#
# This script fills that gap for VMs only.
#
# =============================================================================
# SAFETY FOR PHYSICAL MACHINES  (this is the important part)
# =============================================================================
# MaaS runs commissioning scripts in name order, and this is numbered 31 so it
# runs AFTER the built-in 30-maas-01-bmc-config. Three independent properties
# keep physical hardware completely unaffected:
#
#   1. HARD GUARD. The first thing this does is read the SMBIOS system
#      manufacturer. KubeVirt sets it to the literal string "KubeVirt"
#      (verified: virsh dumpxml shows <entry name='manufacturer'>KubeVirt).
#      Anything else -- Dell, HPE, Supermicro, Lenovo, a bare-metal Intel NUC --
#      exits immediately at status 0 having written NOTHING.
#
#   2. NO WRITE MEANS NO CHANGE. MaaS only applies power config if a bmc-config
#      script actually writes to BMC_CONFIG_PATH. The built-in behaves the same
#      way -- it writes only inside `if bmc.detected():`. So on a physical
#      machine, whatever 30-maas-01-bmc-config discovered (IPMI, Redfish via
#      Type 42, HP Moonshot, Wedge) is left completely untouched by this script.
#
#   3. IT CANNOT CLOBBER A REAL BMC. Because it exits before doing anything on
#      non-KubeVirt hardware, there is no code path in which this script
#      overwrites a physical machine's discovered IPMI/Redfish credentials.
#
# Deliberately NOT using the `for_hardware:` metadata field. for_hardware limits
# a script to matching hardware, which sounds ideal here, but MaaS evaluates it
# against hardware it already knows about -- which it does not reliably have on
# a machine's FIRST enlistment. A for_hardware miss on first boot would mean the
# VM never gets power config at all, which is the failure we are trying to fix.
# The in-script guard above is deterministic and runs every time. If you would
# rather this never even execute on physical machines, add:
#     # for_hardware: system_vendor:KubeVirt
# to the metadata block and re-test the first-enlistment path specifically.
#
# =============================================================================
# WHY THE `enlisting` TAG IS MANDATORY  (this is the whole ballgame)
# =============================================================================
# There is a chicken-and-egg problem: MaaS needs power configuration to power a
# machine on for commissioning, but this script -- which supplies that power
# configuration -- only runs during commissioning. The ONLY place it can break
# the cycle is the commissioning that happens automatically at ENLISTMENT, when
# the machine PXE-booted itself and is already running.
#
# MaaS filters the script tarball it hands an enlisting machine
# (metadataserver/api.py):
#
#     if Config.objects.get_config("enlist_commissioning"):
#         qs = qs.filter(Q(default=True) | Q(tags__overlap=["enlisting"]))
#
# `default=True` is reserved for MaaS's own built-in scripts and cannot be set on
# a user script. So WITHOUT the `enlisting` tag this script is never delivered to
# the machine at all -- it does not run, does not fail, and does not appear in the
# commissioning results. Verified: the first test run enlisted cleanly, passed
# commissioning, and simply had no power configuration.
#
# Requires the MaaS setting `enlist_commissioning` to be true (it is by default).
#
# =============================================================================
# HOW THE VM IS IDENTIFIED
# =============================================================================
# The Redfish endpoint is per-VM (<vm>.redfish.<domain>), so the script must know
# the VM's name. During commissioning the hostname is a MaaS-generated name
# ("famous-marten"), NOT the VM name -- so hostname is unusable.
#
# Instead the VM name is carried in the SMBIOS system serial number, set by
# KubeVirt's spec.template.spec.domain.firmware.serial. A Kyverno mutate rule
# stamps it automatically at VM creation. If the serial does not look like a
# usable name (e.g. it is still a random UUID because the mutate rule did not
# apply), the script refuses to guess and exits without writing.
# =============================================================================

import os
import re
import ssl
import sys
import base64
import json
import urllib.request
from subprocess import check_output, CalledProcessError

# --- environment-specific settings -------------------------------------------
REDFISH_DOMAIN = "redfish.craigcloud.com"   # <vm>.<this> -- matches the Kyverno-generated Ingress
BMC_USER = "admin"
BMC_PASS = "CHANGE-ME"                      # must match the Kyverno policy's generated Secret
NODE_ID = "1"                               # Redfish /Systems/<id>; kubevirtBMC exposes a single system

# Reachability of the BMC is checked but is ADVISORY ONLY -- see the long note at
# the verification step for why a failure here must not stop us writing the config.
# Set True only if you want a commissioning failure when the ephemeral environment
# cannot reach the BMC (rarely what you want).
STRICT_BMC_CHECK = False

EXPECTED_VENDOR = "KubeVirt"
# The serial carries "<vm-name>.<namespace>" so that two tenants can each own a VM of the
# same name without colliding in MaaS or in DNS. Both halves must be valid DNS labels.
LABEL = r"[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?"
NAME_RE = re.compile(rf"^{LABEL}\.{LABEL}$", re.I)
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def dmi(key):
    try:
        return check_output(["dmidecode", "-s", key], timeout=30).decode().strip()
    except (CalledProcessError, OSError, Exception) as e:
        print(f"ERROR: could not read DMI '{key}': {e}")
        return ""


def main():
    # ---- guard 1: only KubeVirt VMs, everything else is untouched ------------
    vendor = dmi("system-manufacturer")
    print(f"INFO: SMBIOS system manufacturer = {vendor!r}")
    if vendor != EXPECTED_VENDOR:
        print(f"INFO: not a {EXPECTED_VENDOR} VM -- this machine's BMC is handled by the "
              f"built-in 30-maas-01-bmc-config. Skipping without changes.")
        return 0

    # ---- identify the VM ----------------------------------------------------
    serial = dmi("system-serial-number")
    print(f"INFO: SMBIOS system serial = {serial!r}")
    if not serial or serial.lower() in ("none", "not specified", "unknown"):
        print("ERROR: SMBIOS serial is empty. The Kyverno mutate rule that stamps "
              "firmware.serial with the VM name has not been applied to this VM.")
        return 1 if STRICT_BMC_CHECK else 0
    if UUID_RE.match(serial):
        print("ERROR: SMBIOS serial is a bare UUID, which means firmware.serial was NOT "
              "set to the VM name (KubeVirt's default). Refusing to guess a Redfish "
              "address. Apply the kubevirt-bmc-serial mutate rule and recreate the VM.")
        return 1 if STRICT_BMC_CHECK else 0
    if not NAME_RE.match(serial):
        print(f"ERROR: SMBIOS serial {serial!r} is not of the form <vm-name>.<namespace>. "
              "Refusing to guess a Redfish address.")
        return 1 if STRICT_BMC_CHECK else 0

    vm_name, namespace = serial.split(".", 1)
    address = f"{serial}.{REDFISH_DOMAIN}"
    print(f"INFO: VM {vm_name!r} in namespace {namespace!r}")
    print(f"INFO: derived Redfish address = {address}")

    # ---- advisory reachability check ----------------------------------------
    # IMPORTANT: this check is INFORMATIONAL and must not gate writing the config.
    #
    # This script runs inside the ephemeral commissioning environment, which sits on
    # the provisioning VLAN and uses MaaS as its resolver. MaaS's power operations,
    # however, run from the RACK CONTROLLER -- a different host, on a different
    # network, with a different resolver. Those are not the same reachability
    # question, and only the rack controller's matters.
    #
    # Observed exactly this: the commissioning environment returned "Temporary
    # failure in name resolution" for the BMC hostname while the rack controller
    # resolved it fine and drove power operations without trouble. Refusing to write
    # the config on that basis broke working automation for no reason.
    #
    # So: attempt it, report it, carry on. A failure here is a hint to go look at
    # provisioning-VLAN DNS, not a reason to leave the machine with no power config.
    # The ingress terminates TLS with a self-signed cert and MaaS does not verify the
    # Redfish certificate, so neither do we -- but we DO authenticate, so a 200 also
    # confirms the credentials.
    url = f"https://{address}/redfish/v1/Systems/{NODE_ID}"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    token = base64.b64encode(f"{BMC_USER}:{BMC_PASS}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            body = json.loads(r.read())
        print(f"INFO: Redfish OK -- PowerState={body.get('PowerState')} Name={body.get('Name')}")
    except Exception as e:
        print(f"WARNING: Redfish endpoint {url} did not answer from the commissioning "
              f"environment: {e}")
        print("WARNING: this is advisory -- MaaS drives power from the rack controller, "
              "not from here. Writing the power configuration anyway.")
        if STRICT_BMC_CHECK:
            print("ERROR: STRICT_BMC_CHECK is set; refusing to write power configuration.")
            return 1

    # ---- hand the power config back to MaaS ---------------------------------
    path = os.environ.get("BMC_CONFIG_PATH")
    if not path:
        print('ERROR: environment variable "BMC_CONFIG_PATH" not defined. This script must '
              'carry the `bmc-config` tag for MaaS to export it.')
        return 1

    # Written by hand rather than with PyYAML so this script needs no Python
    # dependency beyond the stdlib in the ephemeral commissioning environment.
    # All values are quoted -- power_pass in particular may contain characters
    # (#, :, leading zeros) that bare YAML would mangle or retype.
    def q(v):
        return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'

    with open(path, "w") as f:
        f.write(f"power_type: {q('redfish')}\n")
        f.write(f"power_address: {q(address)}\n")
        f.write(f"power_user: {q(BMC_USER)}\n")
        f.write(f"power_pass: {q(BMC_PASS)}\n")
        f.write(f"node_id: {q(NODE_ID)}\n")

    print(f"INFO: wrote Redfish power configuration for {serial} to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# NOTE ON CREDENTIALS
# This uses the one shared BMC password that the Kyverno policy stamps into every
# VM's Secret. That is what makes fully-unattended population possible: the script
# can know the password without talking to Kubernetes. If you move to per-VM random
# passwords (Option A in the Kyverno policy), this script can no longer derive them
# and you would need either MaaS script parameters per machine, or to switch to the
# API-side pattern where the thing that creates the VM also registers the machine in
# MaaS with its power credentials.
