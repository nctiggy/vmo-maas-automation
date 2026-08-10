---
title: RA profile defects
description: Issues found deploying the VMO reference architecture profiles.
---

# VMO Reference Architecture — deployment defects

Found deploying **VMO-RA-Infra-Agent-PXKE-Longhorn / VMO-RA-Core-PXKE-Agent / VMO-RA-Templates
(1.9.x-longhorn)** in agent mode onto 3× Ubuntu nodes (6 vCPU / 10 GB / 150 GB), 2026-08-09 → 10.

Every item was hit on a clean deploy of the shipped profiles, and each is reproduced below with
the actual error text. All have been fixed in a local profile version, so the "fix" shown is what
we are now running successfully — not a proposal.

Ordered by how badly it hurts a customer.

---

## A. Blocks the deploy outright

These fail loudly. Annoying, but you know immediately.

### A1. prometheus-operator references image tags that are not mirrored

`prometheus-operator 83.5.0` (Core profile) points at newer tags than exist in the mirror, so the
pack never pulls.

| image | pack references | available in mirror |
|---|---|---|
| busybox | `1.37.0` | 1.31.1 |
| alertmanager | `v0.32.0` | v0.30.0 |
| kube-webhook-certgen | `1.8.0` | 1.7.3 |
| thanos (2 references) | `v0.41.0` | v0.40.1 |
| prometheus | `v3.11.2` | v3.8.1 |

**Fix:** mirror the referenced tags, or pin the pack to tags that exist.

### A2. Longhorn `csi.kubeletRootDir: ~` autodetect times out

The autodetect pod gets ~7 seconds, which is not enough on a cold node. Longhorn CSI never
registers and every PVC stays `Pending`.

**Fix:** ship the path explicitly.
```yaml
csi:
  kubeletRootDir: /var/lib/kubelet     # not "~"
```

### A3. `storageOverProvisioningPercentage: 100` is too low for CDI

CDI allocates a scratch PVC roughly the size of the target for every import, so a 50 GiB golden
image needs ~100 GiB+ of Longhorn allocation. At 100% Longhorn returns `ReplicaSchedulingFailure`
and golden-image imports never start.

**Fix:** default to `600`, or document CDI's ~2× scratch requirement beside the setting.

---

## B. Silently broken — looks healthy, isn't

**This is the group worth prioritising.** Each leaves the cluster reporting green while something
a customer depends on is quietly unavailable.

### B1. The `-longhorn` templates ship RWX + `volumeMode: Block`, which can never attach

Longhorn's v1 data engine serves ReadWriteMany through share-manager over **NFS**, and NFS cannot
present a raw block device. The PVC **binds normally**, then never attaches:

```
pvc  Bound  [ReadWriteMany]  Block
MapVolume.MapPodDevice failed for volume "pvc-80f5fe0b-..." :
  rpc error: code = Internal desc = failed to bind mount ...
  Output: mount: ... special device .../staging/pvc-80f5fe0b-... does not exist
```

The consumer sits in `Pending` / `ContainerCreating` indefinitely. Because the PVC reads `Bound`
and the StorageClass is healthy, every summary view says things are fine.

Re-verified **after** installing `nfs-common` (B3), using a plain busybox pod with `volumeDevices`
— no KubeVirt involved — and it fails identically. The missing NFS client is *not* the cause.

Affects `storageprofile-cdi`, `dv-ubuntu-2204`, `dv-windows-2022`.

**Fix:** `volumeMode: Filesystem` wherever the access mode is RWX.

### B2. The DataVolumes override the StorageProfile

Correcting `storageprofile-cdi` alone does **not** fix B1. `dv-ubuntu-2204` and `dv-windows-2022`
set `volumeMode: Block` on the DataVolume itself, and that wins. Symptom after the StorageProfile
was already correct: importer pods hung in `ContainerCreating` for 11 minutes with the same
"special device" error.

**Fix:** don't set `volumeMode` on the DataVolumes (let them inherit), or set it consistently in
both places.

### B3. The node image has no NFS client, so RWX cannot work at all

Longhorn RWX is an NFS mount, so every node needs `mount.nfs`. It is absent from the node image
(`dpkg -l nfs-common` → `un` on all three nodes):

```
Mounting command: /usr/local/sbin/nsmounter
Mounting arguments: mount -t nfs -o vers=4.1,noresvport,timeo=600,retrans=5,softerr ...
Output: mount: ... bad option; for several filesystems (e.g. nfs, cifs) you might need
        a /sbin/mount.<type> helper program.
```

The VM stays in `Scheduling` indefinitely. **Live migration is therefore impossible out of the
box** — without RWX, VM disks are RWO and cannot balance across nodes.

**Fix:** add `nfs-common` (Ubuntu) / `nfs-utils` (RHEL) to the BYOOS layer or node image.
Verify with `ls /sbin/mount.nfs`.

> **B1 and B3 are independent, and BOTH must be fixed.**
>
> | | without `nfs-common` | with `nfs-common` |
> |---|---|---|
> | RWX + **Filesystem** | fails: `bad option; need /sbin/mount.<type>` | **works** — this is what gave us live migration |
> | RWX + **Block** | fails: `special device ... does not exist` | fails: *identical error* |
>
> Since the shipped profile asks for Block, installing the NFS client on its own changes nothing a
> customer would notice.

### B4. `serverTLSBootstrap` is off, so `kubectl logs` / `exec` fail with a SAN error

The apiserver is hardened to verify kubelet serving certificates
(`--kubelet-certificate-authority=/etc/kubernetes/pki/ca.crt`,
`--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname`), but the kubelet is left to
self-sign, so its serving certificate carries no node-IP SAN:

```
x509: cannot validate certificate for 172.19.0.41 because it doesn't contain any IP SANs
```

Workloads run fine and the cluster looks healthy — only `kubectl logs` and `kubectl exec` break,
which is precisely what an operator reaches for when diagnosing something else.

**Fix:** in the edge-k8s pack's kubelet config:
```yaml
serverTLSBootstrap: true
rotateCertificates: true
```

Verified working — the serving cert is then issued by the cluster CA with the right SANs:
```
issuer  = CN = kubernetes
subject = O = system:nodes, CN = system:node:poctest-vmo-agent-1
SAN     = DNS:poctest-vmo-agent-1, IP Address:172.19.0.41
kubectl logs -> OK
```

**Second half of the fix, currently missing.** Kubernetes does **not** auto-approve kubelet
*serving* CSRs. The cluster ships:

```
kubeadm:node-autoapprove-bootstrap             -> ...certificatesigningrequests:nodeclient
kubeadm:node-autoapprove-certificate-rotation  -> ...certificatesigningrequests:selfnodeclient
```

…and **nothing bound to `system:certificates.k8s.io:certificatesigningrequests:selfnodeserver`**.
The initial CSRs were approved, but on rotation the replacement CSR will sit `Pending` and
logs/exec will break again — silently, on a timer, long after anyone connects it to this change.
Ship an approver binding (or a CSR-approving controller) together with the setting.

---

## C. Platform issues hit during the same deploy (not RA profile content)

| # | Issue | Impact |
|---|---|---|
| C1 | `cloudConfig.vip` is silently dropped by the edge-native cluster API — returns HTTP 201, all conditions green, deploy is dead. The correct field is `controlPlaneEndpoint: {host, type}`. Nodes log `invalid vip. Cannot proceed with upgrade`. | Cluster hangs with no error surface. Cannot be repaired in place while Provisioning — needs delete/recreate. The `spectrocloud-clusters` skill documents the wrong field. |
| C2 | Cross-tenant profile import rejects masked defaults: `Variable 'grafanaAdminPassword' default value should not be masked`. | Blocks import until the `********` default is cleared by hand. |
| C3 | palette-webhook cert deadlock — `CSR not signed by referenced private key`, secret `palette-webhook-service-cert` never created. | Required manually deleting the stale CertificateRequest and Certificate, then restarting `palette-lite-controller-manager`. |
| C4 | Kyverno `generate` rule `match` blocks are immutable. A reconciler retries the rejected patch forever and the pack sits in `Error` permanently. | Any change to which resources a policy targets needs delete/recreate, not update. Worth a note wherever policies ship as manifest packs. |

---

## Not defects — worth knowing

- **Manifest packs require the literal `uid: spectro-manifest-pack`.** Without it the API rejects
  the profile with `Parameter 'pack '<name>' uid' should not be empty` — despite a sweep of all
  888 packs finding zero of `type=manifest` in any registry.
- **`GET /v1/packs?filters=metadata.name=X` does not see the whole catalog.** Use
  `POST /v1/packs/search` paginated on `listmeta.continue`; it also reports every registry a pack
  is published to. This is how the OCI Kyverno pack was found after name-filtered queries kept
  returning only the helm one.
- **The Palette Community Registry has two UIDs in circulation** —
  `64eaff5630402973c4e1856a` and `64eaff453040297344bcad5d` — holding different pack sets.
- **A "Ready" pack is not proof it is running what you published.** After a profile version swap
  the reconciler can recreate an object from its cached older pack and report Ready. Verify the
  live object, not the pack condition.
