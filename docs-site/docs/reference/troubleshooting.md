---
title: Troubleshooting
description: Symptoms, causes and checks — starting with the failures that look like success.
---

# Troubleshooting

## Failures that look like success

These are the expensive ones. Everything reports healthy and nothing happens.

### Nothing is generated when I create a VM

**Check the namespace labels.** Both are required and neither implies the other.

```console
$ kubectl get ns vms -o jsonpath='{.metadata.labels}{"\n"}'
{"bmc.spectrocloud.com/autogen":"enabled","mutatevirtualmachines.kubemacpool.io":"allocate"}
```

| Missing label | Symptom |
|---|---|
| `mutatevirtualmachines.kubemacpool.io` | VM gets a BMC, but its MAC changes on every power cycle → commissioning times out after 30 minutes with every script `Aborted` |
| `bmc.spectrocloud.com/autogen` | VM gets a stable MAC and no BMC at all — MaaS can never power it |

### The machine enlists but has no power configuration

**The commissioning script was probably never delivered.** Check whether it ran at all:

```bash
maas admin node-script-results read <system_id> | \
  jq -r '.[].results[] | "\(.name) \(.status_name)"'
```

If `31-kubevirt-redfish-bmc` is **absent from the list** — not failed, absent — it was never sent
to the machine. Check its tags:

```console
$ maas admin node-scripts read type=commissioning | \
    jq -r '.[]|select(.name|test("kubevirt"))|.tags'
["bmc-config","enlisting","node"]
```

The **`enlisting`** tag is mandatory. MaaS filters the tarball it hands an enlisting machine to
`default=True OR tags contains "enlisting"`, and `default=True` is reserved for built-in scripts.

If the script *did* run, read its output — it explains itself:

```bash
maas admin node-script-result download <system_id> current-commissioning \
  filters=31-kubevirt-redfish-bmc filetype=txt
```

### A pack says Ready but is running old content

After a profile version swap, a reconciler can recreate an object from its **cached** older pack
and report Ready. Verify the live object, not the pack condition:

```bash
kubectl get clusterpolicy kubevirtbmc-autogen -o jsonpath='{.status.rulecount}{"\n"}'
kubectl get ingress -A -o custom-columns=NAME:.metadata.name,HOST:.spec.rules[0].host
```

---

### The VM never PXE-boots (VMO Launchpad template)

**Symptom.** The VM powers on and drops to a boot failure or an EFI shell. It never sends a
DHCP/PXE request, so MaaS never sees it.

**Cause.** The NIC has no `bootOrder`. KubeVirt builds the boot list **only** from devices that
carry a `bootOrder` -- a device without one is not "last", it is *excluded*. A template with
`bootOrder: 2` on the disk and nothing on the interface therefore has a boot list of exactly one
entry: the blank disk.

The VMO Launchpad template editor does not preserve `bootOrder` on an interface, so a template
that was correct when applied with `kubectl` can come back without it after being edited or
recreated through the UI. **Check after any UI edit:**

```bash
kubectl get vmtemplate <template> \
  -o jsonpath='{.spec.template.spec.domain.devices.interfaces}{"\n"}'
# want: [{"bootOrder":1,"bridge":{},"model":"virtio","name":"pxe"}]
```

**Fix.** Put the NIC first and the disk second, on the template and on any VM already created
from it:

```bash
kubectl patch vmtemplate <template> --type=json \
  -p '[{"op":"add","path":"/spec/template/spec/domain/devices/interfaces/0/bootOrder","value":1}]'

kubectl -n <ns> patch vm <vm> --type=json \
  -p '[{"op":"add","path":"/spec/template/spec/domain/devices/interfaces/0/bootOrder","value":1},
       {"op":"add","path":"/spec/template/spec/domain/devices/disks/0/bootOrder","value":2}]'
```

A VM already running must be restarted for the change to take effect -- `bootOrder` is part of
the VMI spec, which is fixed at launch. The MAC survives the restart because KubeMacPool pinned
it, so MaaS still recognises the machine.

### The machine loops: enlist, commission, revert to New

**Symptom.** The VM PXE-boots, enlists, starts commissioning, then MaaS logs

```text
Failed to query node's BMC — Aborting COMMISSIONING and reverting to NEW. Unable to power
```

and the whole cycle repeats. `maas admin machine read <sid>` shows `power_state: unknown`.

**Cause.** The BMC password MaaS holds does not match the one in the VM's generated Secret.
There are two independent copies of that password and they must agree:

| Where | What sets it |
|---|---|
| `power_pass` on the MaaS machine | `BMC_PASS` in the **MaaS-stored** commissioning script `31-kubevirt-redfish-bmc` |
| `<vm>-bmc-creds` Secret | the `gen-bmc-secret` rule in the Kyverno policy |

The script uploaded into MaaS is a *copy*. Editing the script in this repo does not change it,
so the two drift apart silently and every VM loops.

**Check both:**

```bash
maas admin machine power-parameters <sid>                  # power_user / power_pass
maas admin node-script download 31-kubevirt-redfish-bmc | grep '^BMC_PASS'
kubectl -n <ns> get secret <vm>-bmc-creds -o jsonpath='{.data.password}' | base64 -d
```

**Confirm the BMC itself is healthy** before changing anything — this proves the ingress and
the credential separately:

```bash
curl -sk https://<vm>.<ns>.redfish.craigcloud.com/redfish/v1/ | head -c 80   # ServiceRoot, no auth
curl -sk -u admin:<pass> https://<vm>.<ns>.redfish.craigcloud.com/redfish/v1/Systems
# "Unauthorized" here means the password is wrong, not that the BMC is broken
```

**Fix.** Make them the same. Aligning the policy to whatever the MaaS script already uses means
MaaS needs no edit and new VMs work on first enlistment. To repair a machine already stuck:

```bash
maas admin machine update <sid> power_type=redfish \
  power_parameters_power_user=admin \
  power_parameters_power_pass='<the BMC secret value>' \
  power_parameters_power_address='<vm>.<ns>.redfish.craigcloud.com' \
  power_parameters_node_id=1

maas admin machine query-power-state <sid>     # want: {"state": "on"}
maas admin machine commission <sid>
```

### The Redfish ingress returns 302, or someone else's page

**Symptom.** `curl https://<vm>.<ns>.redfish.craigcloud.com/redfish/v1/` returns a redirect to a
login page instead of the Redfish ServiceRoot, and MaaS cannot drive power.

**Check the Ingress has an address at all:**

```bash
kubectl -n <ns> get ingress <vm>-redfish
# ADDRESS empty  ->  no controller claimed it
```

Two causes, both of which leave the Ingress unclaimed and let the request fall through to a
catch-all route (VMO Launchpad has one: `vmo-manager` matches ``PathPrefix(`/`)`` with no host,
which is where the `/auth/login` redirect comes from):

**1. Wrong ingress class.** The class in the policy must match a controller that exists.
VMO Launchpad ships **Traefik**:

```bash
kubectl get ingressclass          # traefik, on a Launchpad cluster
```

Set `ingressClassName` in the `gen-redfish-ingress` rule accordingly. Patching the generated
Ingress by hand does **not** stick -- Kyverno synchronises it back. Change the policy, then
delete the Ingress so it regenerates.

**2. The TLS secret does not exist.** The rule references `redfish-wildcard-tls`. If it is
missing Traefik logs

```text
Error configuring TLS  error="secret <ns>/redfish-wildcard-tls does not exist"
```

and never builds the HTTPS router. Issue the certificate once per namespace -- a wildcard covers
exactly one label, so it must be scoped to the namespace segment:

```yaml
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: redfish-wildcard
  namespace: <ns>
spec:
  secretName: redfish-wildcard-tls
  dnsNames: ["*.<ns>.redfish.craigcloud.com"]
  issuerRef:
    name: platform-ca-issuer      # whatever ClusterIssuer your platform provides
    kind: ClusterIssuer
```

Confirm the whole path from the MaaS host, which is the client that matters:

```bash
curl -sk https://<vm>.<ns>.redfish.craigcloud.com/redfish/v1/ | head -c 120
# want: {"@odata.context":"/redfish/v1/$metadata#ServiceRoot.ServiceRoot", ...
```

A `405` to a `HEAD` request is also a healthy answer -- the Redfish service wants `GET`.

### The BMC rejects the credentials

The policy generates `<vm>-bmc-creds` with the placeholder password `CHANGE-ME`, so
`/redfish/v1/Systems` answers `Unauthorized` until you set a real one. Set it in the
`gen-bmc-secret` rule before you register anything with MaaS, and use the same value in the
machine's power configuration.

### An Ingress is left behind after a VM is deleted

Deleting a VM removes its Service, but the generated Ingress can remain and keep pointing at the
gone Service. Traefik then logs `Cannot create service  error="service not found"` on every
reconcile. Delete the orphan:

```bash
kubectl -n <ns> delete ingress <old-vm>-redfish
```

## Ordinary failures

### The VM will not start

```
failed to locate network attachment definition vms/vlan-22
```

Qualify the NAD across namespaces: `networkName: default/vlan-22`.

### Commissioning fails, every script `Aborted / exit=None`

The MAC changed between enlistment and commissioning. Compare:

```bash
kubectl get vmi -n vms <vm> -o jsonpath='{.status.interfaces[0].mac}{"\n"}'
maas admin machine read <system_id> | jq -r '.boot_interface.mac_address'
```

If they differ, KubeMacPool is not applying — check the namespace label.

### Kyverno rejects the policy

```
requires permissions list,get for resource v1/Secret
```

The aggregated ClusterRole is missing, or the controllers have not picked it up. Apply it and
restart them — aggregation is not instant and permissions are cached:

```bash
kubectl apply -f manifests/kyverno-policy.yaml
kubectl -n kyverno rollout restart deploy
```

### `changes of immutable fields of a rule spec in a generate rule is disallowed`

A generate rule's `match` block cannot be changed. Delete and recreate:

```bash
kubectl delete clusterpolicy kubevirtbmc-autogen
kubectl apply -f manifests/kyverno-policy.yaml
```

Under a reconciler, publish the corrected version **first**, then delete — otherwise the reconciler
recreates the old one from cache.

### `Commission is not available because of the current state of the node`

Expected, not a fault. The machine is already commissioning. Wait for it to reach `New` or `Ready`.

### The machine sits at `New`

Enlistment commissioning leaves machines at `New` by design. The reconciler advances them within
2 minutes. If not:

```bash
kubectl -n maas-automation create job --from=cronjob/maas-reconciler debug-1
kubectl -n maas-automation logs -l job-name=debug-1
```

### `kubectl logs` / `exec` fail with `doesn't contain any IP SANs`

The kubelet is self-signing its serving certificate while the API server verifies it. Set
`serverTLSBootstrap: true` and `rotateCertificates: true` in the kubelet configuration.

!!! warning "Also add a CSR approver"
    Kubernetes does **not** auto-approve kubelet *serving* CSRs. Most clusters bind
    `nodeclient` and `selfnodeclient` but not
    `system:certificates.k8s.io:certificatesigningrequests:selfnodeserver`. Without it the initial
    certificates are fine but **rotation stalls**, and logs/exec break again weeks later.

    ```bash
    kubectl get csr | grep -i pending
    ```

---

## Useful one-liners

```bash
# everything generated for one VM
kubectl get secret,vmbmc,ingress,pod -n vms | grep <vm>

# does the BMC answer?
curl -sk -u admin:'<pw>' https://<vm>.<ns>.redfish.example.com/redfish/v1/Systems/1 | jq '{Name,PowerState}'

# can MaaS drive power?
maas admin machine query-power-state <system_id>

# what the reconciler thinks
kubectl -n maas-automation logs -l app=maas-reconciler --tail=20

# reconciler dry run
kubectl -n maas-automation set env cronjob/maas-reconciler DRY_RUN=true
```
