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
