---
title: 3. Install the automation
description: Kyverno, KubeMacPool, the generate policy and the reconciler CronJob.
---

# 3. Install the automation

Four things go on the cluster. Install them as a Palette cluster profile, or with `kubectl` — the
objects are identical.

## Find the Kyverno pack

Only needed for the cluster-profile path. `scripts/find-pack.py` searches the whole catalog:

```console
$ export PALETTE_API_KEY=... PROJECT_UID=...
$ ./scripts/find-pack.py kyverno --exact --type oci
PACK     TYPE  VERSION     REGISTRY                    PACK UID                  REGISTRY UID
kyverno  oci   1.12.2      SA Registry                 66512a6a3e8a61e56145468d  643e91e5009c583a818d2b6e
kyverno  oci   3.9.0-rc.1  Kyverno                     6a712d2d6ebd7da625474016  64cbf018632639649b74477f
kyverno  oci   1.18.1      Palette Community Registry  6a7315287d84c8b6b4cb1a45  64eaff5630402973c4e1856a
```

!!! warning "A pack name is not unique"
    The same pack is usually published to several registries at different versions — one of the
    three above is a release candidate. Pick deliberately, then narrow with `--registry`:

    ```bash
    eval "$(./scripts/find-pack.py kyverno --exact --type oci \
              --registry 'Community' --export)"
    ```

    `--export` **refuses** to emit anything while more than one pack matches, and lists the
    candidates instead. It will not guess for you.

??? note "Why a helper script rather than a documented API call?"

    The obvious query silently lies:

    ```bash
    GET /v1/packs?filters=metadata.name=kyverno     # returns only the helm publications
    ```

    It does not see the whole catalog, so you conclude the OCI pack does not exist.
    `POST /v1/packs/search` does see everything — but it paginates on a `continue` token, not an
    offset, so the natural `offset=` loop returns page one forever.

## Install

=== "Cluster profile (Palette)"

    ```bash
    git clone https://github.com/nctiggy/vmo-maas-automation
    cd vmo-maas-automation

    export PALETTE_API_KEY=...        # tenant API key
    eval "$(./scripts/find-pack.py kyverno --exact --type oci --registry Community --export)"
    python3 scripts/build-bmc-automation-profile.py
    ```

    Then attach it, supplying the three profile variables:

    ```bash
    curl -X PUT "https://api.spectrocloud.com/v1/spectroclusters/$CLUSTER/profiles" \
      -H "ApiKey: $PALETTE_API_KEY" -H "ProjectUid: $PROJECT_UID" \
      -H 'Content-Type: application/json' -d '{"profiles":[
        {"uid":"'"$EXISTING_INFRA"'"},
        {"uid":"'"$NEW_PROFILE_UID"'","variables":[
          {"name":"maasUrl","value":"http://10.0.19.46:5240/MAAS"},
          {"name":"maasApiKey","value":"<consumer:token:secret>"},
          {"name":"redfishDomain","value":"redfish.example.com"}]}]}'
    ```

=== "kubectl"

    ```bash
    git clone https://github.com/nctiggy/vmo-maas-automation
    cd vmo-maas-automation

    # 1. Kyverno
    kubectl apply -f https://github.com/kyverno/kyverno/releases/download/v1.13.4/install.yaml
    kubectl -n kyverno rollout status deploy --timeout=300s

    # 2. KubeMacPool
    kubectl apply -f manifests/kubemacpool-v0.51.1.yaml

    # 3. the policy (RBAC + ClusterPolicy)
    kubectl apply -f manifests/kyverno-policy.yaml

    # 4. the reconciler
    kubectl apply -f manifests/maas-reconciler-configmap.yaml
    kubectl apply -f manifests/maas-reconciler.yaml
    ```

## Create the MaaS credential

The reconciler reads the MaaS endpoint and API key from a Secret.

=== "kubectl"

    ```bash
    kubectl create namespace maas-automation

    kubectl -n maas-automation create secret generic maas-api \
      --from-literal=MAAS_URL=http://10.0.19.46:5240/MAAS \
      --from-literal=MAAS_API_KEY='<consumer:token:secret>' \
      --from-literal=REDFISH_DOMAIN=redfish.example.com
    ```

=== "Manifest"

    ```yaml title="maas-api-secret.yaml"
    apiVersion: v1
    kind: Namespace
    metadata:
      name: maas-automation
    ---
    apiVersion: v1
    kind: Secret
    metadata:
      name: maas-api
      namespace: maas-automation
    type: Opaque
    stringData:
      MAAS_URL: "http://10.0.19.46:5240/MAAS"
      MAAS_API_KEY: "<consumer:token:secret>"
      REDFISH_DOMAIN: "redfish.example.com"
    ```

    ```bash
    kubectl apply -f maas-api-secret.yaml
    ```

    !!! warning "Do not commit this one"
        `stringData` is plain text. Keep it out of git, or generate it from your secret manager.

## Opt a namespace in

Nothing happens until a namespace carries **both** labels. They are independent switches.

=== "kubectl"

    ```bash
    kubectl create namespace vms
    kubectl label ns vms mutatevirtualmachines.kubemacpool.io=allocate
    kubectl label ns vms bmc.spectrocloud.com/autogen=enabled
    ```

=== "Manifest"

    ```yaml title="vms-namespace.yaml"
    apiVersion: v1
    kind: Namespace
    metadata:
      name: vms
      labels:
        # KubeMacPool: assign a MAC that survives VM restarts
        mutatevirtualmachines.kubemacpool.io: allocate
        # Kyverno: stamp SMBIOS identity and generate the BMC objects
        bmc.spectrocloud.com/autogen: enabled
    ```

    ```bash
    kubectl apply -f vms-namespace.yaml
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

    The generated Secret ends up looking like this — you never create it yourself:

    ```yaml
    apiVersion: v1
    kind: Secret
    metadata:
      name: web1-bmc-creds        # <vm>-bmc-creds
      namespace: vms
    type: Opaque
    stringData:
      username: admin
      password: your-lab-password
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

=== "kubectl"

    ```bash
    kubectl -n maas-automation create job --from=cronjob/maas-reconciler manual-1
    kubectl -n maas-automation logs -l job-name=manual-1
    ```

=== "Manifest"

    ```yaml title="reconciler-once.yaml"
    apiVersion: batch/v1
    kind: Job
    metadata:
      name: maas-reconciler-manual-1
      namespace: maas-automation
    spec:
      ttlSecondsAfterFinished: 600
      template:
        spec:
          restartPolicy: Never
          containers:
            - name: reconciler
              image: python:3.12-slim
              command: ["python3", "/app/maas-reconciler.py"]
              env:
                - {name: DRY_RUN, value: "true"}   # drop this to apply changes
              envFrom:
                - secretRef: {name: maas-api}
              volumeMounts:
                - {name: script, mountPath: /app, readOnly: true}
          volumes:
            - name: script
              configMap: {name: maas-reconciler-script}
    ```

    ```bash
    kubectl apply -f reconciler-once.yaml
    kubectl -n maas-automation logs job/maas-reconciler-manual-1
    ```

Expected on an empty MaaS:

```
MaaS http://10.0.19.46:5240/MAAS: 0 machines, 1 pools
done: 0 changed, 0 skipped
```

!!! tip "Dry run first"
    Set `DRY_RUN=true` in the Secret or the Job to have it report what it *would* do without
    changing anything.

---

**Next:** [Add the commissioning script →](04-commissioning-script.md)
