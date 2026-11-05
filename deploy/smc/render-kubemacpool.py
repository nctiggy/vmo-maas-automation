#!/usr/bin/env python3
"""Render the smc KubeMacPool manifest from an upstream release.

    ./deploy/smc/render-kubemacpool.py v0.52.1 > deploy/smc/kubemacpool.yaml

Applies the three smc changes to upstream config/release/kubemacpool.yaml and refuses to
emit anything if one of them no longer matches (upstream reshaped the file):
  1. VM and pod webhooks OPT-IN (namespace label ...=allocate). Upstream ships the VM webhook
     opt-out, which rewrites MACs on every VM in every namespace on its next UPDATE.
  2. Pool 02:4B:4D:00:00:00-02:4B:4D:FF:FF:FF, both ends quoted (an unquoted 02:FF:... is a
     YAML 1.1 sexagesimal int to Go's parser).
  3. Image pinned to the release tag, IfNotPresent, instead of :latest/Always.
"""
import re, sys, urllib.request

tag = sys.argv[1]
url = f"https://raw.githubusercontent.com/k8snetworkplumbingwg/kubemacpool/{tag}/config/release/kubemacpool.yaml"
s = urllib.request.urlopen(url, timeout=30).read().decode()

def sub(pattern, repl, text, count):
    new, n = re.subn(pattern, repl, text, flags=re.M)
    if n != count:
        sys.exit(f"render-kubemacpool: expected {count} match(es) for {pattern!r}, got {n}")
    return new

s = sub(r"^(\s*)RANGE_START: .*$", r'\1RANGE_START: "02:4B:4D:00:00:00"', s, 1)
s = sub(r"^(\s*)RANGE_END: .*$", r'\1RANGE_END: "02:4B:4D:FF:FF:FF"', s, 1)
s = sub(r"quay\.io/kubevirt/kubemacpool:latest", f"quay.io/kubevirt/kubemacpool:{tag}", s, 2)
s = sub(r"imagePullPolicy: Always", "imagePullPolicy: IfNotPresent", s, 2)
for hook in ("mutatepods", "mutatevirtualmachines"):
    s = sub(rf"(- key: {hook}\.kubemacpool\.io\n\s*operator: )\w+(\n\s*values:\n\s*- )\w+",
            r"\1In\2allocate", s, 1)

print(f"# KubeMacPool {tag} for smc, rendered by deploy/smc/render-kubemacpool.py. Do not edit;")
print("# re-render. Opt-in webhooks, pool 02:4B:4D, image pinned to the tag.")
sys.stdout.write(s)
