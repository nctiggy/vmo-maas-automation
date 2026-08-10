---
title: 1. Prerequisites
description: What must already exist, including two things people wrongly assume are true.
---

# 1. Prerequisites

## Cluster

| Requirement | Notes |
|---|---|
| Kubernetes with **KubeVirt** | Tested against KubeVirt v1.7.0 |
| **Multus** CNI | Needed for bridged VM interfaces. Masquerade networking will not work — see below |
| **kubevirtBMC** | Owns the `VirtualMachineBMC` CRD. Ships with Palette VMO; otherwise install it separately |
| An **ingress controller** | Publishes each VM's Redfish endpoint over TLS |
| **Shared storage** with RWX | Only needed if you want VMs to live-migrate |

## Network

You need a **provisioning VLAN** that both MaaS and your VMs can reach at layer 2. MaaS serves
DHCP and PXE on it, and the VM must get its address from MaaS — not from another DHCP server.

!!! warning "Bridge, not masquerade"
    VM interfaces must use `bridge` binding on a Multus NAD attached to the provisioning VLAN.

    With masquerade binding the VM's traffic is NAT'd behind the pod IP, so MaaS never sees the
    VM's real MAC. DHCP and PXE will appear to work, but commissioning cannot match the machine it
    enlisted, and every script times out with `Aborted / exit=None` after 30 minutes.

## DNS

Each VM gets a Redfish endpoint at `<vm>.<namespace>.redfish.<your-domain>`. You need a
**wildcard DNS record** pointing that at your ingress controller.

!!! note "A wildcard record covers this, a wildcard certificate does not"
    DNS wildcards match multiple labels, so `*.redfish.example.com` resolves
    `web1.team-a.redfish.example.com` fine.

    TLS wildcards match exactly **one** label, so a `*.redfish.example.com` certificate does *not*
    cover it. MaaS does not verify the Redfish certificate, so this works as-is — but if you need
    real certificate validation, issue a per-namespace wildcard (`*.team-a.redfish.example.com`)
    or use a SAN list.

!!! danger "Do not use a `.local` domain"
    `systemd-resolved` reserves `.local` for mDNS and refuses to send those queries to a unicast
    DNS server. It will fail on every Ubuntu host even when your router serves the record
    correctly, and the failure looks like a DNS outage rather than a policy decision.

## The two everyone misses

### An NFS client on every node

If you want RWX storage — and you do, if you want VMs to live-migrate — every node needs the NFS
client. Longhorn serves ReadWriteMany through share-manager over NFSv4.1, and without a
`mount.nfs` helper the mount fails and the VM never starts.

=== "Check"

    ```bash
    # run on every node
    ls /sbin/mount.nfs || echo "MISSING"
    ```

=== "Ubuntu / Debian"

    ```bash
    sudo apt-get update
    sudo apt-get install -y nfs-common
    ```

=== "RHEL / Rocky"

    ```bash
    sudo dnf install -y nfs-utils
    ```

### Enlistment commissioning turned on

MaaS must be allowed to commission machines as they enlist — that is the window in which the
power configuration gets written.

```bash
maas admin maas get-config name=enlist_commissioning   # must be true
maas admin maas set-config name=enlist_commissioning value=true
```

## Access you will need

| Thing | Used for |
|---|---|
| Cluster admin `kubeconfig` | Installing the automation |
| MaaS **admin API key** | The reconciler; found under your MaaS user preferences |
| SSH to each node | Installing the NFS client |

---

**Next:** [Prepare the nodes →](02-nodes.md)
