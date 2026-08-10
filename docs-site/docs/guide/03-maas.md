---
title: 3. Deploy MaaS
description: Get MaaS serving DHCP and PXE on the provisioning VLAN.
---

# 3. Deploy MaaS

If you already run MaaS, you only need to confirm the two settings at the bottom of this page.

## Install

=== "Snap (Ubuntu)"

    ```bash
    sudo snap install maas --channel=3.5/stable
    sudo snap install maas-test-db --channel=3.5/stable

    sudo maas init region+rack \
      --database-uri maas-test-db:/// \
      --maas-url http://<MAAS_IP>:5240/MAAS

    sudo maas createadmin --username admin --email you@example.com
    ```

=== "Package (Ubuntu)"

    ```bash
    sudo apt-get update
    sudo apt-get install -y maas
    sudo maas createadmin --username admin --email you@example.com
    ```

## Network layout

MaaS needs an interface on the provisioning VLAN. A two-NIC layout keeps management traffic
separate from provisioning:

| Interface | VLAN | Purpose |
|---|---|---|
| `mgmt` | management | Web UI and API |
| `prov` | provisioning | DHCP, PXE/TFTP, commissioning |

??? note "Naming interfaces predictably with netplan"

    Cloud images enumerate NICs as `ens18`, `ens19`, … in an order that can change. Match on MAC
    and rename, so your configuration does not drift:

    ```yaml title="/etc/netplan/60-maas.yaml"
    network:
      version: 2
      ethernets:
        mgmt:
          match: {macaddress: "aa:bb:cc:dd:ee:01"}
          set-name: mgmt
          addresses: [10.0.19.46/24]
          routes: [{to: default, via: 10.0.19.1}]
          nameservers: {addresses: [10.0.19.1]}
        prov:
          match: {macaddress: "aa:bb:cc:dd:ee:02"}
          set-name: prov
          addresses: [10.0.22.5/24]
    ```

## Enable DHCP on the provisioning VLAN

Do this in the UI under **Subnets → your provisioning VLAN → Configure DHCP**, or:

```bash
maas login admin http://<MAAS_IP>:5240/MAAS "$(sudo maas apikey --username admin)"

# find the VLAN and the rack controller
maas admin subnets read | jq -r '.[] | "\(.cidr)  vlan=\(.vlan.id)"'
maas admin rack-controllers read | jq -r '.[].system_id'

maas admin vlan update <FABRIC_ID> <VID> \
  dhcp_on=true primary_rack=<RACK_SYSTEM_ID>
```

## Sync a boot image

Commissioning needs at least one image. Syncing only what you need keeps it quick:

```bash
maas admin boot-source-selections create 1 \
  os=ubuntu release=noble arches=amd64 subarches='*' labels='*'
maas admin boot-resources import
```

## Two settings that matter

```bash
# 1. commission machines as they enlist -- this is the window in which the
#    commissioning script writes the power configuration
maas admin maas set-config name=enlist_commissioning value=true

# 2. the release commissioning runs from
maas admin maas get-config name=commissioning_distro_series
```

!!! warning "Without `enlist_commissioning`, none of this works"
    The commissioning script only ever gets a chance to run during enlistment. If enlistment
    commissioning is off, machines enlist as `New` with no power configuration and nothing can
    advance them.

## Verify

```bash
# DHCP is answering on the provisioning VLAN
maas admin vlan read <FABRIC_ID> <VID> | jq '.dhcp_on'      # true

# an image is available
maas admin boot-resources read | jq -r '.[].name' | sort -u
```

---

**Next:** [Install the automation →](04-automation.md)
