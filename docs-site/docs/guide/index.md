---
title: Deployment Guide
description: Everything needed to go from an empty cluster to a Ready machine in MaaS.
---

# Deployment guide

Six steps, in order. Each one ends with a check you can run before moving on.

| # | Step | What you end up with |
|---|---|---|
| 1 | [Prerequisites](01-prerequisites.md) | A cluster and a network that can actually support this |
| 2 | [Deploy MaaS](02-maas.md) *(skip if you have MaaS)* | MaaS serving DHCP and PXE on the provisioning VLAN |
| 3 | [Deploy MaaS](02-maas.md) | MaaS serving DHCP and PXE on the provisioning VLAN |
| 4 | [Install the automation](03-automation.md) | Kyverno, KubeMacPool, the policy and the reconciler running |
| 5 | [Commissioning script](04-commissioning-script.md) | MaaS able to configure power for itself |
| 6 | [Create your first VM](05-first-vm.md) | A `Ready` machine in MaaS |

!!! tip "Two ways to install"
    Every step gives you both a **cluster profile** path (Palette-managed, survives rebuilds) and a
    **kubectl** path (plain manifests, works anywhere). Use whichever fits — they install the same
    objects.

!!! warning "Read step 1 properly"
    Two prerequisites are commonly assumed and commonly absent: a provisioning VLAN that actually
    reaches your VMs at layer 2, and `enlist_commissioning` turned on in MaaS. Skipping either
    produces failures that look like something else entirely.
