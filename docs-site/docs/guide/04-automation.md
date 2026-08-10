---
title: 4. Install the automation
description: Kyverno, KubeMacPool, the generate policy and the reconciler CronJob.
---

# 4. Install the automation

Four things go on the cluster. Install them as a Palette cluster profile, or with `kubectl` — the
objects are identical.

=== "Cluster profile (Palette)"

    The repo ships an exported profile and the script that builds it.

    ```bash
    git clone https://github.com/<you>/vmo-maas-automation
    cd vmo-maas-automation

    export PALETTE_API_KEY=...        # tenant API key
    export KYVERNO_UID=... KYVERNO_REG=... KYVERNO_TAG=1.18.1 KYVERNO_TYPE=oci
    python3 scripts/build-bmc-automation-profile.py
    ```

    Then attach it to your cluster, supplying the three profile variables:

    ```bash
    curl -X PUT "https://api.spectrocloud.com/v1/spectroclusters/$CLUSTER/profiles" \
      -H "ApiKey: $PALETTE_API_KEY" -H "ProjectUid: $PROJECT" \
      -H 'Content-Type: application/json' -d '{"profiles":[
        {"uid":"'"$EXISTING_INFRA"'"},
        {"uid":"'"$NEW_PROFILE_UID"'","variables":[
          {"name":"maasUrl","value":"http://10.0.19.46:5240/MAAS"},
          {"name":"maasApiKey","value":"<consumer:token:secret>"},
          {"name":"redfishDomain","value":"redfish.example.com"}]}]}'
    ```

    !!! tip "Finding the Kyverno pack"
        Name-filtered pack queries do not see the whole catalog. Use the search endpoint and
        paginate on `listmeta.continue`:

        ```bash
        curl -sX POST "https://api.spectrocloud.com/v1/packs/search?limit=50" \
          -H "ApiKey: $PALETTE_API_KEY" -H 'Content-Type: application/json' \
          -d '{"filter":{},"sort":[]}' \
          | jq -r '.items[]|select(.spec.name=="kyverno")|.spec.registries[]
                   |"\(.name) \(.uid) \(.latestVersion) \(.latestPackUid)"'
        ```

=== "kubectl"

    ```bash
    git clone https://github.com/<you>/vmo-maas-automation
    cd vmo-maas-automation

    # 1. Kyverno
    kubectl apply -f https://github.com/kyverno/kyverno/releases/download/v1.13.4/install.yaml
    kubectl -n kyverno rollout status deploy --timeout=300s

    # 2. KubeMacPool
    kubectl apply -f manifests/kubemacpool-v0.51.1.yaml

    # 3. the policy (RBAC + ClusterPolicy)
    kubectl apply -f manifests/kyverno-policy.yaml

    # 4. the reconciler
    kubectl create namespace maas-automation
    kubectl -n maas-automation create secret generic maas-api \
      --from-literal=MAAS_URL=http://10.0.19.46:5240/MAAS \
      --from-literal=MAAS_API_KEY='<consumer:token:secret>' \
      --from-literal=REDFISH_DOMAIN=redfish.example.com
    kubectl apply -f manifests/maas-reconciler-configmap.yaml
    kubectl apply -f manifests/maas-reconciler.yaml
    ```

## Opt a namespace in

Nothing happens until a namespace carries **both** labels. They are independent switches:

```bash
kubectl create namespace vms
kubectl label ns vms mutatevirtualmachines.kubemacpool.io=allocate
kubectl label ns vms bmc.spectrocloud.com/autogen=enabled
```

| Label | Grants |
|---|---|
| `mutatevirtualmachines.kubemacpool.io=allocate` | Persistent MAC addresses |
| `bmc.spectrocloud.com/autogen=enabled` | SMBIOS identity + BMC + Redfish ingress |

!!! warning "Missing one of these fails silently"
    Only the KubeMacPool label → VMs get stable MACs and no BMC, so MaaS can never power them.

    Only the autogen label → VMs get a BMC, but the MAC changes on every power cycle, so
    commissioning fails after a 30-minute timeout with every script `Aborted`.

## Point the policy at your domain

The Redfish hostnames are baked into the policy. Change them before applying:

```bash
sed -i 's/redfish\.craigcloud\.com/redfish.example.com/g' manifests/kyverno-policy.yaml
```

??? note "Where the BMC password comes from"

    The shipped policy generates a Secret containing a **single shared password** for every VM's
    BMC. That is what lets the commissioning script authenticate without talking to Kubernetes.

    Set it before you apply:

    ```bash
    sed -i 's/password: "CHANGE-ME"/password: "your-lab-password"/' manifests/kyverno-policy.yaml
    ```

    For anything beyond a lab, the policy carries two commented alternatives — a per-VM random
    password via Kyverno's `random()`, or cloning a Secret populated by External Secrets/Vault.
    Both are documented inline in `manifests/kyverno-policy.yaml`. Note that a per-VM password
    means the commissioning script can no longer derive it, so you would move power configuration
    to the API-side pattern instead.

## Verify

```bash
kubectl -n kyverno get deploy                       # 4/4 available
kubectl -n kubemacpool-system get deploy            # 2/2 available
kubectl get clusterpolicy kubevirtbmc-autogen       # READY = True, 3 generate + 1 mutate
kubectl -n maas-automation get cronjob              # schedule */2 * * * *
```

Force a reconciler run rather than waiting for the schedule:

```bash
kubectl -n maas-automation create job --from=cronjob/maas-reconciler manual-1
kubectl -n maas-automation logs -l job-name=manual-1
```

Expected on an empty MaaS:

```
MaaS http://10.0.19.46:5240/MAAS: 0 machines, 1 pools
done: 0 changed, 0 skipped
```

!!! tip "Dry run first"
    Set `DRY_RUN=true` in the Secret (or the CronJob env) to have it report what it *would* do
    without changing anything.

---

**Next:** [Add the commissioning script →](05-commissioning-script.md)
