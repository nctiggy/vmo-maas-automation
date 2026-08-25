---
title: 1. Prerequisites
description: What must already exist before installing the automation.
---

# 1. Prerequisites

This assumes you already have a working KubeVirt/VMO deployment — nodes, storage and networking
are yours to run. What follows is only what *this* automation depends on.

## Cluster

| Requirement | Notes |
|---|---|
| Kubernetes with **KubeVirt** | Tested against KubeVirt v1.7.0 |
| **Multus** CNI | Needed for bridged VM interfaces. Masquerade networking will not work — see below |
| **kubevirtBMC** | Owns the `VirtualMachineBMC` CRD. Bundled with some Palette VMO builds -- **check before assuming**: `kubectl get crd virtualmachinebmcs.bmc.kubevirt.io`. If it is absent, install it (see below) |
| **cert-manager** | Required by kubevirtBMC's webhooks, and the easiest way to issue the Redfish wildcard certificate |
| An **ingress controller** | Publishes each VM's Redfish endpoint over TLS |

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

## MaaS

A working **MaaS** region+rack controller, serving DHCP and PXE on the provisioning VLAN.

If you do not have one yet, [step 2](02-maas.md) walks through a minimal install. If you already
run MaaS, you only need the setting below.

Enlistment commissioning must be **on**. It is the only window in which the power configuration
can be written — see [How it works](../reference/how-it-works.md).

```bash
maas admin maas get-config name=enlist_commissioning   # must be true
maas admin maas set-config name=enlist_commissioning value=true
```

## If kubevirtBMC is not bundled

Some VMO builds ship it, some do not. Check first:

```bash
kubectl get crd virtualmachinebmcs.bmc.kubevirt.io
```

If that returns `NotFound`, install the upstream release. Note the project moved from
`starbops/kubevirtbmc` to `kubevirtbmc/kubevirtbmc`; the old repo has no install asset:

```bash
kubectl apply -f https://github.com/kubevirtbmc/kubevirtbmc/releases/download/v0.9.0/kubevirtbmc-install.yaml
kubectl -n kubevirtbmc-system rollout status deploy/kubevirtbmc-controller-manager --timeout=180s
```

It installs its webhooks with cert-manager `Certificate` objects, so cert-manager must already
be running or the controller never becomes ready.

## Which ingress controller you have

The policy generates one Ingress per VM, and **an Ingress naming a class with no controller is
never claimed** -- it sits with an empty `ADDRESS` and requests fall through to whatever
catch-all route the cluster has. Check what you actually run:

```bash
kubectl get ingressclass
```

**VMO Launchpad ships Traefik**, not ingress-nginx. The policy in this repo now defaults to
`traefik`; if your cluster runs something else, change `ingressClassName` in the
`gen-redfish-ingress` rule to match. See
[the ingress notes in troubleshooting](../reference/troubleshooting.md#the-redfish-ingress-returns-302-or-someone-elses-page).

## Access you will need

| Thing | Used for |
|---|---|
| Cluster admin `kubeconfig` | Installing the automation |
| MaaS **admin API key** | The reconciler; found under your MaaS user preferences |

---

**Next:** [Deploy MaaS →](02-maas.md)
