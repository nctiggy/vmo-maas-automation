# MaaS-managed KubeVirt VMs

Create a KubeVirt VM. A few minutes later it is a commissioned, `Ready` machine in
[MaaS](https://maas.io) — with working Redfish power control, named after the VM, and filed under
its tenant's resource pool. No manual steps.

MaaS is built to manage *physical* servers: it expects PXE boot, a BMC it can power-cycle, and a
stable identity across reboots. A KubeVirt VM has none of those by default. This gives it all three.

```
VM created in an opted-in namespace
  ├─ KubeMacPool      persistent MAC
  ├─ Kyverno mutate   <vm>.<namespace> into the SMBIOS serial
  └─ Kyverno generate Secret + VirtualMachineBMC + Redfish Ingress
        ↓
     PXE boot → MaaS enlists → commissioning script writes power config
        ↓
     reconciler renames, pools and commissions
        ↓
     Ready machine, MaaS owns power and boot order
```

## Documentation

Full walkthrough — prerequisites through to your first `Ready` machine:

```bash
python3 -m venv .venv
.venv/bin/pip install mkdocs-material
cd docs-site && ../.venv/bin/mkdocs serve
# http://127.0.0.1:8000
```

## Layout

| Path | Contents |
|---|---|
| `docs-site/` | The documentation site (MkDocs Material) |
| `manifests/` | Everything installable with `kubectl` |
| `scripts/` | The MaaS commissioning script, the reconciler, and the profile builders |
| `profiles/` | Exported Palette cluster profiles |

### Scripts

| Script | Runs where | Purpose |
|---|---|---|
| `scripts/31-kubevirt-redfish-bmc.py` | Inside MaaS, at enlistment | Works out which VM it is and writes the Redfish power configuration back to MaaS |
| `scripts/maas-reconciler.py` | Cluster CronJob, every 2 min | Renames machines after their VM, creates per-namespace resource pools, commissions anything in `New` |

Both are standard library only — no dependencies, no image build. The reconciler is also embedded
in `manifests/maas-reconciler-configmap.yaml`; edit the script and regenerate, don't edit the
ConfigMap.

## Components

| Component | Job |
|---|---|
| [KubeMacPool](https://github.com/k8snetworkplumbingwg/kubemacpool) | A MAC that survives VM restarts — without it MaaS loses the machine on every power cycle |
| [Kyverno](https://kyverno.io) | Stamps VM identity into SMBIOS, generates the BMC objects |
| [kubevirtBMC](https://github.com/starbops/kubevirtbmc) | Serves Redfish for each VM — power and boot-device control |
| MaaS commissioning script | Hands MaaS the power configuration during enlistment |
| Reconciler CronJob | Naming, tenant pools, and commissioning |

## Before you start

This assumes a working KubeVirt/VMO deployment already exists — nodes, storage and the VM
platform are out of scope.

Two prerequisites are commonly assumed and commonly absent:

- **A working MaaS**, serving DHCP and PXE on a provisioning VLAN your VMs can reach at layer 2.
  VM interfaces must use `bridge` binding, not masquerade.
- **`enlist_commissioning=true` in MaaS.** Enlistment is the only window in which the power
  configuration can be written.

See the [prerequisites](docs-site/docs/guide/01-prerequisites.md) page for the rest.

## Configure before applying

Nothing here contains credentials. Set these first:

| Where | What |
|---|---|
| `manifests/kyverno-policy.yaml` | `redfish.craigcloud.com` → your domain; `password: "CHANGE-ME"` |
| `scripts/31-kubevirt-redfish-bmc.py` | `REDFISH_DOMAIN`, `BMC_PASS` |
| `maas-api` Secret | MaaS URL and admin API key |

## Status

Built and verified end to end on Palette VMO (KubeVirt v1.7.0) with MaaS 3.5.13.
Both scripts, the policy and the CronJob are running in that environment.

## Licence

MIT
