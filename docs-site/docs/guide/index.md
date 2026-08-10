---
title: Deployment Guide
description: Everything needed to go from an empty cluster to a Ready machine in MaaS.
---

# Deployment guide

Six steps, in order. Each one ends with a check you can run before moving on.

| # | Step | What you end up with |
|---|---|---|
| 1 | [Prerequisites](01-prerequisites.md) | A cluster and a network that can actually support this |
| 2 | [Prepare the nodes](02-nodes.md) | NFS client installed, so shared storage and live migration work |
| 3 | [Deploy MaaS](03-maas.md) | MaaS serving DHCP and PXE on the provisioning VLAN |
| 4 | [Install the automation](04-automation.md) | Kyverno, KubeMacPool, the policy and the reconciler running |
| 5 | [Commissioning script](05-commissioning-script.md) | MaaS able to configure power for itself |
| 6 | [Create your first VM](06-first-vm.md) | A `Ready` machine in MaaS |

!!! tip "Two ways to install"
    Every step gives you both a **cluster profile** path (Palette-managed, survives rebuilds) and a
    **kubectl** path (plain manifests, works anywhere). Use whichever fits — they install the same
    objects.

!!! warning "Read step 1 properly"
    Two of the prerequisites are things people assume are already true and are not: the NFS client
    on every node, and a provisioning VLAN that reaches your VMs. Skipping them produces failures
    that look like something else entirely.
