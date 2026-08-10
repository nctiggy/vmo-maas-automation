---
title: Components
description: Every object this creates, and where each manifest lives.
---

# Components

## Cluster-wide

| Object | Namespace | From | Purpose |
|---|---|---|---|
| Kyverno (4 deployments) | `kyverno` | registry pack / upstream | Policy engine |
| KubeMacPool (2 deployments) | `kubemacpool-system` | `manifests/kubemacpool-v0.51.1.yaml` | Persistent MAC allocation |
| `kyverno:generate-kubevirtbmc` | cluster | `manifests/kyverno-policy.yaml` | Aggregated RBAC so Kyverno may generate |
| `kubevirtbmc-autogen` | cluster | `manifests/kyverno-policy.yaml` | The mutate + 3 generate rules |
| `maas-reconciler` CronJob | `maas-automation` | `manifests/maas-reconciler.yaml` | Rename, pool, commission |
| `maas-reconciler-script` | `maas-automation` | `manifests/maas-reconciler-configmap.yaml` | The reconciler source |
| `maas-api` Secret | `maas-automation` | you create it | MaaS URL, API key, Redfish domain |

## Per VM, generated automatically

| Object | Name | Created by |
|---|---|---|
| Secret | `<vm>-bmc-creds` | Kyverno `gen-bmc-secret` |
| VirtualMachineBMC | `<vm>` | Kyverno `gen-vmbmc` |
| Ingress | `<vm>-redfish` | Kyverno `gen-redfish-ingress` |
| Pod + Service | `<vm>-virtbmc` | kubevirtBMC, from the VirtualMachineBMC |

The virtbmc Service exposes Redfish on `80/TCP` and IPMI on `623/UDP`, in the VM's own namespace.

## The MAC pool

KubeMacPool ships with a range in `kubemacpool-mac-range-config`:

```yaml
data:
  RANGE_START: "02:00:00:00:00:00"
  RANGE_END:   "02:FF:FF:FF:FF:FF"
```

About 1.1 trillion addresses — there is no reason to narrow it. A MAC is released when the VM
object is deleted; there is no manual release.

!!! warning "Quote them"
    Upstream ships `RANGE_START` quoted and `RANGE_END` bare. Go's YAML 1.1 parser reads a bare
    `02:FF:FF:FF:FF:FF` as a **sexagesimal integer**, and ConfigMap `data` values must be strings —
    so some tools reject the manifest. Note that PyYAML resolves them as strings, so validating
    with Python alone will not reproduce the failure.

## The scripts

Both are plain Python with **no dependencies beyond the standard library**.

| Script | Runs | Notes |
|---|---|---|
| `scripts/31-kubevirt-redfish-bmc.py` | In MaaS, during enlistment | Tagged `bmc-config, enlisting` |
| `scripts/maas-reconciler.py` | As a CronJob, every 2 min | Talks to the MaaS REST API directly |

??? note "Why the reconciler does not use the `maas` CLI"

    The CLI is distributed as a snap and expects a logged-in profile on disk. Neither travels into
    a container. MaaS's REST API uses OAuth 1.0 with **PLAINTEXT** signatures, which is only a few
    header fields:

    ```python
    'OAuth oauth_version="1.0", oauth_signature_method="PLAINTEXT", '
    f'oauth_consumer_key="{CK}", oauth_token="{TK}", oauth_signature="&{TS}", '
    f'oauth_nonce="{secrets.token_hex(8)}", oauth_timestamp="{int(time.time())}"'
    ```

    The signature is literally `&<token_secret>` — the consumer secret is empty.

??? note "One API gotcha worth knowing"

    `GET /machines/` does **not** include `power_parameters`. The field is simply absent, so
    filtering on it there silently matches nothing. Fetch it per machine:

    ```
    GET /machines/<system_id>/?op=power_parameters
    ```

## The PXE VmTemplate

`manifests/vmtemplate-pxe-boot.yaml` — a diskless VM that network-boots from MaaS.

| Setting | Value | Why |
|---|---|---|
| `runStrategy` | `Manual` | Created powered-off, like a server in a rack. MaaS owns power after first boot |
| interface | `bridge` on `default/vlan-22` | MaaS must see the real MAC |
| `bootOrder` | NIC 1, disk 2 | A blank disk must netboot to enlist at all |
| disk source | `blank` | MaaS installs the OS over the network |
| accessModes | `ReadWriteMany` | So it can live-migrate |
| `macAddress` | *unset* | KubeMacPool assigns it |
| `firmware.serial` | *unset* | The Kyverno mutate rule stamps it |
| `volumeMode` | *unset* | Inherits Filesystem from the StorageProfile |
