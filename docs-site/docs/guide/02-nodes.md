---
title: 2. Prepare the nodes
description: Install the NFS client and confirm shared storage can actually attach.
---

# 2. Prepare the nodes

Only needed if you want VMs to live-migrate between hosts. Skip it and everything else still
works — your VM disks will just be `ReadWriteOnce` and pinned to one node.

## Install the NFS client

=== "Ubuntu / Debian"

    ```bash
    for NODE in node1 node2 node3; do
      ssh "$NODE" 'sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive \
        apt-get install -y -qq nfs-common && ls /sbin/mount.nfs'
    done
    ```

=== "RHEL / Rocky"

    ```bash
    for NODE in node1 node2 node3; do
      ssh "$NODE" 'sudo dnf install -y -q nfs-utils && ls /sbin/mount.nfs'
    done
    ```

=== "macOS (admin workstation)"

    ```bash
    # Nothing to install locally — this runs on the cluster nodes.
    # Just confirm you can reach them:
    for NODE in node1 node2 node3; do ssh "$NODE" 'ls /sbin/mount.nfs'; done
    ```

!!! warning "Put this in your node image, not just on the running nodes"
    Installing with a package manager fixes the nodes you have. A rebuilt node comes back without
    it and RWX silently breaks again. Add `nfs-common` / `nfs-utils` to the OS layer of your
    infrastructure profile or your golden image.

## Storage must be RWX **and** Filesystem

This is the part that catches people. On Longhorn's v1 data engine, `ReadWriteMany` is served over
NFS — and NFS cannot present a raw block device.

| Access mode | Volume mode | Result |
|---|---|---|
| ReadWriteOnce | Block | Works. No live migration |
| ReadWriteMany | **Filesystem** | **Works, and migrates** |
| ReadWriteMany | Block | **Binds, then never attaches** |

!!! danger "RWX + Block fails by hanging, not by erroring"
    The PVC reports `Bound` and the StorageClass looks healthy, so every summary view says things
    are fine. The pod sits in `ContainerCreating` forever with:

    ```
    MapVolume.MapPodDevice failed ... special device .../staging/pvc-... does not exist
    ```

    A bound PVC is not evidence that RWX works. Attach one to something.

Check every place `volumeMode` is set — the **DataVolume overrides the StorageProfile**, so
fixing only the StorageProfile is not enough:

```bash
kubectl get storageprofile <storageclass> \
  -o jsonpath='{.spec.claimPropertySets}{"\n"}'
kubectl get dv -A -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name,MODE:.spec.storage.volumeMode
```

## Verify

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: rwx-check, namespace: default}
spec:
  accessModes: [ReadWriteMany]
  volumeMode: Filesystem
  storageClassName: longhorn
  resources: {requests: {storage: 1Gi}}
EOF

kubectl run rwx-check --image=busybox:1.37.0 --restart=Never \
  --overrides='{"spec":{"containers":[{"name":"c","image":"busybox:1.37.0","command":["sh","-c","touch /m/ok && echo RWX-OK && sleep 5"],"volumeMounts":[{"name":"v","mountPath":"/m"}]}],"volumes":[{"name":"v","persistentVolumeClaim":{"claimName":"rwx-check"}}]}}'

kubectl logs rwx-check          # expect: RWX-OK
kubectl delete pod/rwx-check pvc/rwx-check
```

If that prints `RWX-OK`, live migration will work. If the pod hangs in `ContainerCreating`, revisit
the NFS client and the volume mode.

---

**Next:** [Deploy MaaS →](03-maas.md)
