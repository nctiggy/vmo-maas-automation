# smc: post-demo upgrade

Prepared 11/5/26. Nothing here has been applied to smc. Run it after the demo.

| Component | On smc now | After this |
|---|---|---|
| kubevirtBMC | v0.11.0 (latest) | unchanged |
| Kyverno | v1.19.2 (latest) | unchanged |
| Kyverno policy | `ClusterPolicy kubevirtbmc-autogen` (deprecated API) | `MutatingPolicy` x2 + `GeneratingPolicy` (`kyverno-cel-policy.yaml`) |
| KubeMacPool | v0.51.4 | v0.52.1 |

Before starting, check for newer releases than the ones above:

```bash
for r in kubevirtbmc/kubevirtbmc kyverno/kyverno k8snetworkplumbingwg/kubemacpool; do
  echo "$r $(gh api repos/$r/releases/latest --jq .tag_name)"; done
```

## 0. Preconditions

```bash
export KUBECONFIG=~/.kube/smc.conf
kubectl get ns -l bmc.spectrocloud.com/autogen=enabled          # today: maas-vms
kubectl get vm -A -o custom-columns=NS:.metadata.namespace,VM:.metadata.name,MAC:.spec.template.spec.domain.devices.interfaces[0].macAddress > /tmp/vm-macs-before.txt
```

On maas-01, no opted-in machine should be Commissioning or Deploying. Step 2 briefly removes each VM's BMC.

## 1. KubeMacPool v0.51.4 -> v0.52.1

`kubemacpool.yaml` is rendered by `render-kubemacpool.py v0.52.1`. The renderer keeps both webhooks opt-in, the 02:4B:4D pool, and the pinned image, and it fails if upstream changed shape.

```bash
kubectl apply --server-side -f deploy/smc/kubemacpool.yaml
kubectl -n kubemacpool-system rollout status deploy --timeout=240s
# both webhooks must still say In [allocate]
kubectl get mutatingwebhookconfiguration kubemacpool-mutator \
  -o jsonpath='{range .webhooks[*]}{.name} {.namespaceSelector.matchExpressions[2]}{"\n"}{end}'
kubectl get vm -A -o custom-columns=NS:.metadata.namespace,VM:.metadata.name,MAC:.spec.template.spec.domain.devices.interfaces[0].macAddress | diff /tmp/vm-macs-before.txt - && echo "no MAC changed"
```

Rollback: `kubectl apply --server-side -f` the v0.51.4 file from the `smc-deploy` branch.

## 2. Policy: ClusterPolicy -> MutatingPolicy + GeneratingPolicy

Tested in kind on 11/5/26 against Kyverno 1.19.2 with the smc CRDs: serial, `runStrategy: Manual` on CREATE, `spec.running` VMs skipped, unlabelled namespaces ignored, deleted objects regenerated, clean delete with the VM, and this exact hand-off.

Two findings drive the steps:

- **Kyverno blocks relabelling an object it generated** (`protect-generation-labels`), so the new policy cannot adopt the old policy's Secrets, BMCs and routes. If they are left in place they leak when their VM is deleted. Let the old policy delete them, then have the new one regenerate them. In kind the regeneration took under 5 s. Each VM's BMC is down for that window.
- **The new policy carries no ownerReferences.** With them, a deleted VM's Secret loops forever: garbage collection deletes it and Kyverno regenerates it every ~15 s. `synchronize` alone cleans up correctly.

```bash
P=$(kubectl -n maas-automation get secret bmc-shared-password -o jsonpath='{.data.password}' | base64 -d)

# a. new policies alongside the old one (both stamp the same serial and runStrategy)
sed "s/CHANGE-ME/$P/" deploy/smc/kyverno-cel-policy.yaml | kubectl apply -f -
kubectl get mutatingpolicy,generatingpolicy            # READY true

# b. remove the old policy; it deletes what it generated (no orphan flag)
kubectl delete clusterpolicy kubevirtbmc-autogen

# c. immediately touch every VM in an opted-in namespace so the new policy regenerates
for ns in $(kubectl get ns -l bmc.spectrocloud.com/autogen=enabled -o name | cut -d/ -f2); do
  kubectl -n $ns annotate vm --all bmc.spectrocloud.com/migrated="$(date +%s)" --overwrite
done
unset P
```

Verify:

```bash
# 3 objects per VM, all owned by the new policy
kubectl get secret,virtualmachinebmc,httproute -A -l generate.kyverno.io/policy-name=kubevirtbmc-generate
kubectl get deploy -A | grep virtbmc                   # one per VM, Ready
# from maas-01, for each opted-in machine
maas admin machine query-power-state <system_id>       # "on" or "off", no error
```

Then create a throwaway VM from `test-vm.yaml` under a new name and confirm it reaches Ready in MaaS without intervention.

**Rollback.** Deleting the GeneratingPolicy deletes everything it generated, so reapply the old policy first:

```bash
sed "s/password: \"CHANGE-ME\"/password: \"$P\"/" deploy/smc/kyverno-policy.yaml | kubectl apply -f -
kubectl delete mutatingpolicy kubevirtbmc-serial kubevirtbmc-runstrategy
kubectl delete generatingpolicy kubevirtbmc-generate
# then repeat step c so the old policy regenerates
```

## Not done here

- The vmo-maas-automation Palette profile builder still packages the `ClusterPolicy`.
- `manifests/kyverno-policy.yaml` (the generic, non-smc policy) still uses `ClusterPolicy`. Its ownerReferences are safe there: in kind, deleting a VM under the `ClusterPolicy` removed all three objects and none came back.
