#!/usr/bin/env python3
"""
Reconcile MaaS machines that are backed by KubeVirt VMs:
  * name them  "<vm>-<namespace>"  to match the VM they represent
  * place them in a resource pool named after the namespace

Runs as a Kubernetes CronJob. Uses ONLY the Python standard library against the MaaS
REST API, so it needs no image build -- a stock python:*-slim works.

WHY POLLING AND NOT AN EVENT HOOK
---------------------------------
MaaS has no outbound webhooks (verified: the API exposes `events`, which is read-only and
pollable, and `notifications`, which are UI banners). Nothing can push "a machine enlisted"
to us. The trigger we care about -- enlistment -- is also asynchronous and outside our
control: a VM might PXE boot seconds or hours after being created. So this is a converging
reconcile loop, not a hook, and it is safe to run repeatedly.

WHY NOT THE `maas` CLI
----------------------
The CLI is distributed as a snap and expects a logged-in profile on disk. Neither travels
into a container. MaaS's REST API uses OAuth 1.0 with PLAINTEXT signatures, which is a
handful of header fields -- see auth_header().

SAFETY
------
Only machines whose power_address matches "<vm>.<namespace>.<REDFISH_DOMAIN>" are touched;
that string is written exclusively by the 31-kubevirt-redfish-bmc commissioning script. A
physical machine cannot match -- its BMC address is an IP or a site DNS name from the
built-in IPMI detection. The regex is anchored, so a lookalike domain
("...redfish.craigcloud.com.attacker.io") does not match either. Everything else is left
entirely alone: no rename, no pool change, nothing.

CONFIG (environment)
    MAAS_URL       e.g. http://172.19.0.46:5240/MAAS
    MAAS_API_KEY   consumer:token:secret
    REDFISH_DOMAIN default redfish.craigcloud.com
    DRY_RUN        "true" to report without changing anything
"""

import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MAAS_URL = os.environ.get("MAAS_URL", "").rstrip("/")
API_KEY = os.environ.get("MAAS_API_KEY", "")
REDFISH_DOMAIN = os.environ.get("REDFISH_DOMAIN", "redfish.craigcloud.com")
DRY = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
TIMEOUT = int(os.environ.get("TIMEOUT", "30"))

DEFAULT_POOL = "default"

# Commission machines that are sitting in "New".
#
# Enlistment commissioning is NOT a full commissioning cycle: it leaves the machine at
# "New", and MaaS will not advance further on its own. Reaching "Ready" needs an explicit
# commission, which power-cycles the machine through its BMC -- so it can only be done once
# power configuration exists, which is exactly what 31-kubevirt-redfish-bmc has just
# supplied. That makes this the right place to close the loop.
#
# Only ever commissions from "New". Never touches Ready / Deployed / Commissioning / Failed,
# so a machine that fails commissioning is left alone for a human rather than being retried
# in a loop.
AUTO_COMMISSION = os.environ.get("AUTO_COMMISSION", "true").lower() in ("1", "true", "yes")
COMMISSIONABLE = {"New"}

if not MAAS_URL or not API_KEY:
    sys.exit("MAAS_URL and MAAS_API_KEY must be set")
try:
    CK, TK, TS = API_KEY.split(":")
except ValueError:
    sys.exit("MAAS_API_KEY must be 'consumer:token:secret'")

API = f"{MAAS_URL}/api/2.0"
ADDR_RE = re.compile(
    r"^(?P<vm>[a-z0-9][-a-z0-9]*)\.(?P<ns>[a-z0-9][-a-z0-9]*)\."
    + re.escape(REDFISH_DOMAIN) + r"$",
    re.I,
)


def auth_header():
    """MaaS accepts OAuth 1.0 PLAINTEXT: the signature is literally '&<token_secret>'
    (the consumer secret is empty). Nonce and timestamp must be fresh per request."""
    return (
        'OAuth oauth_version="1.0", oauth_signature_method="PLAINTEXT", '
        f'oauth_consumer_key="{CK}", oauth_token="{TK}", oauth_signature="&{TS}", '
        f'oauth_nonce="{secrets.token_hex(8)}", oauth_timestamp="{int(time.time())}"'
    )


def call(method, path, **fields):
    data = urllib.parse.urlencode(fields).encode() if fields else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Authorization": auth_header(), "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {detail}") from None


def main():
    machines = call("GET", "/machines/")
    pools = {p["name"]: p for p in call("GET", "/resourcepools/")}
    print(f"MaaS {MAAS_URL}: {len(machines)} machines, {len(pools)} pools"
          + ("  [DRY RUN]" if DRY else ""))

    taken = {m["hostname"]: m["system_id"] for m in machines}
    changed = skipped = 0

    for m in machines:
        # Cheap pre-filter: only Redfish machines can possibly be ours, and this avoids an
        # extra API call per physical/IPMI machine.
        if m.get("power_type") != "redfish":
            continue

        sid = m["system_id"]
        try:
            addr = (call("GET", f"/machines/{sid}/?op=power_parameters") or {}).get(
                "power_address", "")
        except RuntimeError as e:
            print(f"  warn   {m['hostname']}: could not read power parameters: {e}")
            continue

        match = ADDR_RE.match(addr or "")
        if not match:
            continue  # not one of ours

        vm, ns = match.group("vm").lower(), match.group("ns").lower()
        host = m["hostname"]
        want = f"{vm}-{ns}"

        # ---- name ------------------------------------------------------------
        # Always "<vm>-<namespace>". MaaS hostnames are unique across the whole MaaS while
        # a VM name is only unique within its namespace, so a bare VM name would be
        # first-come-first-served between tenants.
        if host != want:
            if want in taken and taken[want] != sid:
                # Not provably unique: vm="web1-team"/ns="a" and vm="web1"/ns="team-a" both
                # render "web1-team-a". Renaming onto a held name would fail, so skip loudly.
                print(f"  skip   {host}: desired name {want!r} held by another machine")
                skipped += 1
            else:
                print(f"  {'would rename' if DRY else 'rename'} {host} -> {want}")
                if not DRY:
                    call("PUT", f"/machines/{sid}/", hostname=want)
                    taken.pop(host, None)
                    taken[want] = sid
                host = want
                changed += 1

        # ---- pool ------------------------------------------------------------
        current = ((m.get("pool") or {}).get("name")) or DEFAULT_POOL
        if current == ns:
            continue
        if current != DEFAULT_POOL:
            # Someone deliberately filed this machine elsewhere; don't fight them.
            print(f"  skip   {host}: already in pool {current!r}")
            skipped += 1
            continue

        if ns not in pools:
            print(f"  {'would create' if DRY else 'create'} pool {ns!r}")
            if not DRY:
                pools[ns] = call("POST", "/resourcepools/", name=ns,
                                 description=f"KubeVirt namespace {ns}")
        print(f"  {'would move' if DRY else 'move'}   {host}: {current!r} -> {ns!r}")
        if not DRY:
            call("PUT", f"/machines/{sid}/", pool=ns)
        changed += 1

    # ---- commission anything still sitting in New ----------------------------
    # Done in a second pass, re-reading state, so a machine renamed/pooled above is seen
    # with its current status rather than the one captured before those writes.
    if AUTO_COMMISSION:
        for m in call("GET", "/machines/"):
            if m.get("power_type") != "redfish" or m.get("status_name") not in COMMISSIONABLE:
                continue
            sid = m["system_id"]
            try:
                addr = (call("GET", f"/machines/{sid}/?op=power_parameters") or {}).get(
                    "power_address", "")
            except RuntimeError:
                continue
            if not ADDR_RE.match(addr or ""):
                continue  # not one of ours -- never auto-commission physical hardware
            print(f"  {'would commission' if DRY else 'commission'} {m['hostname']} "
                  f"(status {m['status_name']})")
            if not DRY:
                try:
                    call("POST", f"/machines/{sid}/?op=commission")
                except RuntimeError as e:
                    # Losing a race with MaaS moving the machine on is fine; report and move on.
                    print(f"  warn   {m['hostname']}: commission refused: {e}")
                    skipped += 1
                    continue
            changed += 1

    print(f"done: {changed} changed, {skipped} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
