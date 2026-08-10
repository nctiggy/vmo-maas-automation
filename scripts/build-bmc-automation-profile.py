#!/usr/bin/env python3
"""
Build the VMO-BMC-Automation add-on cluster profile in SA-Craig-Smith.

Everything in this profile is already applied by hand to cluster 6a78c6b7fb12ab2ff23f15d8
and verified working end-to-end (auto-vm1 -> MAC 02:00:00:00:ab:f1, Redfish PowerState=Off).
This packages it so it survives a cluster rebuild.

WHAT IT DOES: a VM created in the `vms` namespace automatically gets
  1. a persistent MAC          (KubeMacPool mutating webhook)
  2. a BMC credential Secret   (Kyverno generate)
  3. a VirtualMachineBMC       (Kyverno generate -> kubevirtBMC spins up <vm>-virtbmc)
  4. a Redfish TLS Ingress     (Kyverno generate -> MaaS can reach it; MaaS forces https)
...so MaaS can enlist/commission/deploy it with zero manual steps.

USAGE
  # 1. find the Kyverno OCI pack, then:
  export KYVERNO_UID=<pack uid> KYVERNO_REG=<registry uid> KYVERNO_TAG=<version>
  export KYVERNO_TYPE=oci            # or spectro / helm
  python3 build-bmc-automation-profile.py

  # dry run (prints the payload, creates nothing):
  DRYRUN=1 python3 build-bmc-automation-profile.py

PREREQ ON THE TARGET CLUSTER: kubevirtBMC must already be installed (it owns the
VirtualMachineBMC CRD that the policy generates against). The policy sets
failurePolicy: Ignore, so if the CRD is absent VM creation still succeeds -- you just
get no BMC.

ORDERING: install-priority 45 for the two operators, 50 for the policy, so Kyverno's
webhook is up before the ClusterPolicy is admitted. The policy will still reconcile if
it lands early -- Kyverno re-validates -- but this avoids a transient failed state.
"""
import json, os, sys, urllib.request

API   = "https://api.spectrocloud.com"
PROJ  = "6720c668e9746cb63a499425"          # SA-Craig-Smith
KEY   = open(os.path.expanduser("/tmp/ce.key")).read().strip()
HERE  = os.path.dirname(os.path.abspath(__file__))
DRY   = os.environ.get("DRYRUN")

def req(method, path, body=None):
    r = urllib.request.Request(API + path, method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"ApiKey": KEY, "ProjectUid": PROJ, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r) as f:
            raw = f.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} on {method} {path}\n{e.read().decode()[:2000]}")

def manifest_pack(name, prio, manifests):
    """A manifest-type pack: no upstream chart, we ship the YAML inline.

    uid MUST be the literal "spectro-manifest-pack". Manifest packs don't exist in any
    registry (a catalog sweep of all 888 packs finds zero of type=manifest), but the API
    still rejects the profile with "Parameter 'pack '<name>' uid' should not be empty"
    if it's absent. Verified against a throwaway profile create.
    """
    return {
        "name": name, "type": "manifest", "layer": "addon", "tag": "1.0.0",
        "uid": "spectro-manifest-pack",
        "values": f"pack:\n  namespace: \"\"\n  spectrocloud.com/install-priority: \"{prio}\"\n",
        "manifests": [{"name": n, "content": c} for n, c in manifests],
    }

# ---- pack 1: KubeMacPool v0.51.1 ----------------------------------------------------
# Local vetted copy of the upstream release manifest. The ONLY delta from upstream is that
# RANGE_END is quoted in the kubemacpool-mac-range-config ConfigMap: upstream ships
# RANGE_START quoted but RANGE_END bare, and Go's YAML 1.1 parser reads a bare
# 02:FF:FF:FF:FF:FF as a sexagesimal integer -- ConfigMap data values must be strings, so
# Palette rejects the manifest. Pool is upstream's default (02:00:00:00:00:00 - 02:FF:FF:FF:FF:FF).
kubemacpool = open(os.path.join(HERE, "kubemacpool.yaml")).read()

# The target namespace and BOTH opt-in labels. Each half of the automation is opted in
# independently and neither is inferred from the other:
#   mutatevirtualmachines.kubemacpool.io  -> KubeMacPool assigns a persistent MAC
#   bmc.spectrocloud.com/autogen          -> the Kyverno policy stamps the SMBIOS serial and
#                                            generates the Secret / VirtualMachineBMC / Ingress
# Shipping both here is what makes the profile self-contained. Missing the kubemacpool label
# gives BMCs on VMs whose MAC changes on every VMI restart (breaking MaaS commissioning);
# missing the autogen label gives persistent MACs and no BMC at all. Both failures are silent.
VMS_NS = """apiVersion: v1
kind: Namespace
metadata:
  name: vms
  labels:
    mutatevirtualmachines.kubemacpool.io: allocate
    bmc.spectrocloud.com/autogen: enabled
"""

# ---- pack 2: the Kyverno RBAC + ClusterPolicy --------------------------------------
policy = open(os.path.join(HERE, "kyverno-kubevirtbmc.yaml")).read()

# ---- pack 3: Kyverno itself, from the registry -------------------------------------
kyv_uid  = os.environ.get("KYVERNO_UID")
kyv_reg  = os.environ.get("KYVERNO_REG")
kyv_tag  = os.environ.get("KYVERNO_TAG")
kyv_type = os.environ.get("KYVERNO_TYPE", "oci")
if not (kyv_uid and kyv_reg and kyv_tag):
    if not DRY:
        sys.exit("set KYVERNO_UID / KYVERNO_REG / KYVERNO_TAG (and KYVERNO_TYPE) first.\n"
                 "Find them with:\n"
                 "  curl -s \"$API/v1/packs?filters=metadata.name=<name>&limit=50\" -H \"ApiKey: $KEY\" \\\n"
                 "    | jq '.items[]|{v:.spec.version,uid:.metadata.uid,reg:.spec.registryUid,t:.spec.type}'")
    kyv_uid, kyv_reg, kyv_tag, kyv_vals = "<uid>", "<reg>", "<tag>", "<fetched at build time>"
else:
    # Kyverno's own values: fetched live so we ship the COMPLETE defaults (partial values
    # fail validation), then we only flip what we need.
    kyv_vals = req("GET", f"/v1/packs/{kyv_uid}?includePackValues=true")["packValues"][0]["values"]
    if "install-priority" not in kyv_vals:
        # The pack already ships a `pack:` block (namespace, content.images). Inject the
        # priority INTO it -- prepending a second `pack:` key would be duplicate-key YAML.
        lines = kyv_vals.split("\n")
        for n, l in enumerate(lines):
            if l.rstrip() == "pack:":
                lines.insert(n + 1, '  spectrocloud.com/install-priority: "45"')
                break
        else:
            lines = ['pack:', '  spectrocloud.com/install-priority: "45"'] + lines
        kyv_vals = "\n".join(lines)

packs = [
    {"uid": kyv_uid, "registryUid": kyv_reg, "name": "kyverno",
     "type": kyv_type, "layer": "addon", "tag": kyv_tag, "values": kyv_vals},
    manifest_pack("kubemacpool", 45, [("vms-namespace", VMS_NS), ("kubemacpool", kubemacpool)]),
    manifest_pack("kubevirtbmc-autogen", 50, [("policy", policy)]),
    # Priority 55: after the policy, since it reconciles the MaaS side of what the policy
    # produces. Nothing breaks if it lands early -- it simply finds no machines to act on.
    manifest_pack("maas-reconciler", 55, [
        ("reconciler", open(os.path.join(HERE, "maas-reconciler-pack.yaml")).read()),
        ("reconciler-script", open(os.path.join(HERE, "maas-reconciler-cm.yaml")).read()),
    ]),
]

# Profile variables consumed by the reconciler pack. The API key is sensitive so Palette
# masks it; the others are plain so they are readable/overridable per cluster.
VARIABLES = [
    {"name": "maasUrl", "displayName": "MaaS URL", "format": "string",
     "description": "Base URL of the MaaS region controller, e.g. http://172.19.0.46:5240/MAAS",
     "defaultValue": "http://172.19.0.46:5240/MAAS", "required": True},
    {"name": "maasApiKey", "displayName": "MaaS API key", "format": "string",
     "description": "MaaS API key (consumer:token:secret) for an admin user",
     "isSensitive": True, "required": True},
    {"name": "redfishDomain", "displayName": "Redfish domain", "format": "string",
     "description": "Domain the per-VM Redfish ingresses are published under",
     "defaultValue": "redfish.craigcloud.com", "required": True},
]

payload = {
    "metadata": {"name": "VMO-BMC-Automation",
                 "description": "Auto-assign persistent MACs and auto-generate kubevirtBMC "
                                "+ Redfish ingress for every VM in the vms namespace, so MaaS "
                                "can manage KubeVirt VMs as bare-metal machines."},
    "spec": {"version": "1.0.7", "template": {"type": "add-on", "cloudType": "all", "packs": packs}},
}

# ---- validate every shipped manifest before publishing -----------------------------
# A duplicate `data:` key in the kubemacpool ConfigMap shipped silently in 1.0.0/1.0.1 and
# only surfaced as a YAML error in the Palette UI. Fail the build instead.
def _nodup(loader, node, deep=False):
    seen = set()
    for k, _ in node.value:
        key = loader.construct_object(k, deep=deep)
        if key in seen:
            raise AssertionError(f"duplicate key {key!r}")
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)

try:
    import yaml
    class _L(yaml.SafeLoader): pass
    _L.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _nodup)
    for p in packs:
        for m in p.get("manifests", []):
            try:
                docs = [d for d in yaml.load_all(m["content"], Loader=_L) if d]
            except Exception as e:
                sys.exit(f"INVALID YAML in pack {p['name']} manifest {m['name']}: {e}")
            print(f"  ok  {p['name']}/{m['name']}: {len(docs)} docs")
except ImportError:
    print("  (pyyaml absent - skipping manifest validation)")

if DRY:
    for p in payload["spec"]["template"]["packs"]:
        print(f"  {p['name']:22} type={p['type']:9} tag={p.get('tag')} "
              f"values={len(p.get('values',''))}B manifests={len(p.get('manifests',[]))}")
    sys.exit(0)

out = req("POST", "/v1/clusterprofiles?publish=true", payload)
# POST drops spec.variables silently -- they must be attached separately (returns 204).
req("PUT", f"/v1/clusterprofiles/{out['uid']}/variables", {"variables": VARIABLES})
print(f"  attached {len(VARIABLES)} profile variables "
      f"({', '.join(v['name'] for v in VARIABLES)})")
print(f"created VMO-BMC-Automation {payload["spec"]["version"]}  uid={out.get('uid')}")
print(f"attach to a cluster:  PUT /v1/spectroclusters/<cluster>/profiles  "
      f'-d \'{{"profiles":[{{"uid":"{out.get("uid")}"}}]}}\'')
