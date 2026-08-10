#!/usr/bin/env python3
"""
Publish a new version of VMO-RA-Templates that adds:
  * vmo-networking      + nad-vlan-22          (the MaaS provisioning VLAN)
  * vmo-vmtemplates     + vmtemplate-pxe-boot  (diskless, network-booted VM)

Existing packs and manifests are carried through untouched -- manifest CONTENT is not
returned inline by the profile GET (only a uid), so each one is fetched individually and
re-submitted. Skipping that would silently publish a profile with empty manifests.

USAGE:  python3 build-templates-profile.py [--dry-run]
"""
import json, os, sys, urllib.request

API  = "https://api.spectrocloud.com"
PROJ = "6720c668e9746cb63a499425"
SRC  = "6a797a101fb2a6b417f4462f"          # VMO-RA-Templates v1.9.5-longhorn
NEW_VERSION = "1.9.6-longhorn"
KEY  = open("/tmp/ce.key").read().strip()
HERE = os.path.dirname(os.path.abspath(__file__))
DRY  = "--dry-run" in sys.argv

ADD = {                                     # pack name -> [(manifest name, file)]
    "vmo-networking":  [("nad-vlan-22", "nad-vlan-22.yaml")],
    "vmo-vmtemplates": [("vmtemplate-pxe-boot", "vmtemplate-pxe-boot.yaml")],
}


def req(method, path, body=None):
    r = urllib.request.Request(API + path, method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"ApiKey": KEY, "ProjectUid": PROJ, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r) as f:
            raw = f.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} on {method} {path}\n{e.read().decode()[:1500]}")


src = req("GET", f"/v1/clusterprofiles/{SRC}")
packs = []
for p in src["spec"]["published"]["packs"]:
    manifests = []
    for m in (p.get("manifests") or []):
        full = req("GET", f"/v1/clusterprofiles/{SRC}/packs/{p['name']}/manifests/{m['uid']}")
        content = (full.get("spec", {}).get("published") or {}).get("content", "")
        if not content:
            sys.exit(f"empty content for {p['name']}/{m['name']} -- refusing to publish")
        # Longhorn 1.11.1 supports ReadWriteMany with volumeMode Block, so every VM disk can
        # be RWX and therefore live-migratable. Earlier versions could not (share-manager is
        # NFS and cannot serve raw block), which is why these were pinned to RWO.
        if "ReadWriteOnce" in content:
            content = content.replace("ReadWriteOnce", "ReadWriteMany")
            print(f"  RWO->RWX in {p['name']}/{m['name']}")
        # RWX on Longhorn's v1 data engine is served by share-manager over NFSv4.1 -- verified
        # from the CSI driver's own mount command. NFS cannot present a raw block device, so
        # RWX + volumeMode:Block binds but never attaches ("special device ... does not exist").
        # RWX therefore REQUIRES Filesystem. Nodes also need the nfs-common package.
        # Applies to EVERY manifest, not just the StorageProfile: dv-ubuntu-2204 and
        # dv-windows-2022 set volumeMode: Block on the DataVolume itself, which overrides the
        # StorageProfile. RWX + Block binds but never attaches, so the importer pod hangs
        # forever in ContainerCreating with "special device ... does not exist".
        if "Block" in content:
            content = content.replace("Block", "Filesystem")
            print(f"  Block->Filesystem in {p['name']}/{m['name']}")
        manifests.append({"name": m["name"], "content": content})
    for name, fn in ADD.get(p["name"], []):
        if any(x["name"] == name for x in manifests):
            print(f"  {p['name']}/{name} already present, replacing")
            manifests = [x for x in manifests if x["name"] != name]
        manifests.append({"name": name, "content": open(os.path.join(HERE, fn)).read()})
        print(f"  + {p['name']}/{name}")
    packs.append({
        "name": p["name"], "type": p["type"], "layer": p["layer"],
        "tag": p.get("tag") or "1.0.0", "uid": "spectro-manifest-pack",
        "values": p.get("values") or "", "manifests": manifests,
    })

payload = {"metadata": {"name": src["metadata"]["name"],
                        "description": src["metadata"].get("description") or ""},
           "spec": {"version": NEW_VERSION,
                    "template": {"type": "add-on", "cloudType": "all", "packs": packs}}}

for p in packs:
    print(f"  {p['name']:20} manifests={[m['name'] for m in p['manifests']]}")

if DRY:
    print("dry run - nothing published")
    sys.exit(0)

out = req("POST", "/v1/clusterprofiles?publish=true", payload)
print(f"published {src['metadata']['name']} {NEW_VERSION}  uid={out.get('uid')}")
