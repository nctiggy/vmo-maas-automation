---
title: 5. Create your first VM
description: Create a VM, watch it become a Ready machine in MaaS, and know what to check at each step.
---

# 5. Create your first VM

## Add the provisioning network

The VM needs a bridged interface on the provisioning VLAN:

```yaml title="manifests/nad-vlan-22.yaml"
apiVersion: "k8s.cni.cncf.io/v1"
kind: NetworkAttachmentDefinition
metadata:
  name: "vlan-22"
  namespace: default
spec:
  config: |-
    {
      "cniVersion": "0.3.1",
      "name": "vlan-22",
      "type": "bridge",
      "bridge": "br0",
      "vlan": 22,
      "ipam": {}
    }
```

=== "Manifest"

    ```bash
    kubectl apply -f manifests/nad-vlan-22.yaml
    ```

=== "Inline"

    ```bash
    kubectl apply -f - <<'EOF'
    apiVersion: "k8s.cni.cncf.io/v1"
    kind: NetworkAttachmentDefinition
    metadata:
      name: "vlan-22"
      namespace: default
    spec:
      config: |-
        {"cniVersion":"0.3.1","name":"vlan-22","type":"bridge",
         "bridge":"br0","vlan":22,"ipam":{}}
    EOF
    ```

!!! warning "Qualify the NAD reference across namespaces"
    The NAD lives in `default`, but VMs live in tenant namespaces. An unqualified
    `networkName: vlan-22` resolves in the **VM's own** namespace and fails with:

    ```
    failed to locate network attachment definition vms/vlan-22
    ```

    Always write `networkName: default/vlan-22`.

## Create the VM

=== "From the PXE template"

    If you installed the `pxe-boot` VmTemplate, create from it in the VMO UI, or:

    ```bash
    kubectl get vmtemplate pxe-boot
    ```

=== "Directly"

    ```yaml title="my-first-vm.yaml"
    apiVersion: kubevirt.io/v1
    kind: VirtualMachine
    metadata:
      name: web1
      namespace: vms
    spec:
      runStrategy: Manual        # MaaS owns power from here on
      dataVolumeTemplates:
        - metadata: {name: web1-root}
          spec:
            source: {blank: {}}          # MaaS installs the OS over the network
            storage:
              resources: {requests: {storage: 40Gi}}
              storageClassName: <your-storageclass>
      template:
        spec:
          domain:
            cpu: {cores: 2}
            resources: {requests: {memory: 4Gi}}
            devices:
              disks:
                - {name: root, disk: {bus: virtio}, bootOrder: 2}
              interfaces:
                - {name: pxe, bridge: {}, model: virtio, bootOrder: 1}
          networks:
            - name: pxe
              multus: {networkName: default/vlan-22}
          volumes:
            - {name: root, dataVolume: {name: web1-root}}
    ```

    Note what is **not** there: no `macAddress` and no `firmware.serial`. Those are filled in
    for you.

    ```bash
    kubectl apply -f my-first-vm.yaml
    ```

## Check the automation fired

Within a few seconds:

```console
$ kubectl get vm -n vms web1 \
    -o jsonpath='serial={.spec.template.spec.domain.firmware.serial}  mac={.spec.template.spec.domain.devices.interfaces[0].macAddress}{"\n"}'
serial=web1.vms  mac=02:c2:51:56:4f:4e

$ kubectl get secret,vmbmc,ingress -n vms | grep web1
secret/web1-bmc-creds                       Opaque
virtualmachinebmc.bmc.kubevirt.io/web1      web1   web1-bmc-creds   True
ingress.networking.k8s.io/web1-redfish      nginx  web1.vms.redfish.example.com
```

Confirm the BMC answers:

```bash
curl -sk -u admin:'<your-password>' \
  https://web1.vms.redfish.example.com/redfish/v1/Systems/1 | jq '{Name,PowerState}'
```

```json
{ "Name": "vms/web1", "PowerState": "Off" }
```

## Boot it once

The template is `runStrategy: Manual`, so the VM is created powered-off — like a server arriving
in a rack. Power it on through its own BMC:

```bash
curl -sk -u admin:'<your-password>' -X POST \
  -H 'Content-Type: application/json' -d '{"ResetType":"On"}' \
  https://web1.vms.redfish.example.com/redfish/v1/Systems/1/Actions/ComputerSystem.Reset
```

From here everything is automatic.

## Watch it become Ready

```bash
watch -n 20 'maas admin machines read | \
  jq -r ".[] | \"\(.hostname)  \(.status_name)  \(.power_type // \"-\")\""'
```

You should see, over roughly five to ten minutes:

```
(nothing)                                  ← PXE booting
brave-mule       Commissioning   -         ← enlisted, running the scripts
brave-mule       New             redfish   ← power configuration written
web1-vms         Commissioning   redfish   ← reconciler renamed + commissioned it
web1-vms         Ready           redfish   ← done
```

| Stage | Who did it |
|---|---|
| Appears at all | The VM PXE booted and MaaS enlisted it |
| `power_type` becomes `redfish` | The commissioning script |
| Renamed to `web1-vms`, pool `vms` | The reconciler CronJob |
| `New` → `Commissioning` → `Ready` | The reconciler triggered a real commission |

??? note "It sat at `New` and never moved"

    Enlistment commissioning leaves a machine at `New` — that is expected. Reaching `Ready` needs
    a real commission, which is what the reconciler does on its next pass (up to 2 minutes).

    If it stays there, run the reconciler by hand and read the output:

    ```bash
    kubectl -n maas-automation create job --from=cronjob/maas-reconciler check-1
    kubectl -n maas-automation logs -l job-name=check-1
    ```

??? note "Confirm MaaS really has power control"

    ```console
    $ maas admin machine query-power-state <system_id>
    { "state": "on" }
    ```

    That call goes out over Redfish through the ingress, using the configuration the commissioning
    script wrote. If it works, the whole chain works.

## Deploy an OS

The machine is now an ordinary MaaS machine:

```bash
maas admin machine deploy <system_id> distro_series=noble
```

MaaS power-cycles it through Redfish and sets a one-time PXE boot override — kubevirtBMC
translates both into real KubeVirt operations, so the VM behaves like hardware throughout.

---

Done. Every VM you create in an opted-in namespace now follows the same path.
