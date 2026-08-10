#!/usr/bin/env python3
"""
Find a Palette pack's UID and registry, across every registry it is published to.

    ./find-pack.py kyverno
    ./find-pack.py kyverno --type oci        # only OCI publications
    ./find-pack.py kyverno --export          # emit shell exports to eval

WHY THIS EXISTS
---------------
The obvious query does not work:

    GET /v1/packs?filters=metadata.name=kyverno

That endpoint does not see the whole catalog. For kyverno it returns only the helm
publications and silently omits the OCI one, so you conclude the pack does not exist.

`POST /v1/packs/search` does see everything, but it paginates on a `continue` token rather
than an offset -- passing `offset=` returns page 1 forever. It also reports, per pack, every
registry that pack is published to along with the latest version and pack UID in each, which
is exactly what the profile builders need.

CONFIG
    PALETTE_API_KEY   required
    PROJECT_UID       optional; some registries are only visible in a project context
    PALETTE_API       optional; defaults to https://api.spectrocloud.com
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API = os.environ.get("PALETTE_API", "https://api.spectrocloud.com").rstrip("/")
KEY = os.environ.get("PALETTE_API_KEY", "")
PROJECT = os.environ.get("PROJECT_UID", "")


def _headers():
    h = {"ApiKey": KEY, "Content-Type": "application/json", "Accept": "application/json"}
    if PROJECT:
        h["ProjectUid"] = PROJECT
    return h


def search_packs(name_filter=None):
    """Yield every pack in the catalog, following the continue token.

    Note the pagination style: `listmeta.continue`, NOT an offset. Using offset silently
    returns the first page over and over.
    """
    cont = ""
    while True:
        url = f"{API}/v1/packs/search?limit=50" + (f"&continue={cont}" if cont else "")
        req = urllib.request.Request(url, method="POST", data=b'{"filter":{},"sort":[]}',
                                     headers=_headers())
        try:
            page = json.loads(urllib.request.urlopen(req, timeout=60).read())
        except urllib.error.HTTPError as e:
            sys.exit(f"HTTP {e.code} from packs/search: {e.read().decode()[:300]}")
        items = page.get("items") or []
        for item in items:
            spec = item.get("spec") or {}
            if name_filter and name_filter.lower() not in (spec.get("name") or "").lower():
                continue
            yield spec
        cont = (page.get("listmeta") or {}).get("continue") or ""
        if not cont or not items:
            return


def main():
    ap = argparse.ArgumentParser(description="Find a Palette pack's UID and registry.")
    ap.add_argument("name", help="pack name, or a substring of it")
    ap.add_argument("--type", help="only this pack type (oci / helm / spectro)")
    ap.add_argument("--exact", action="store_true", help="require an exact name match")
    ap.add_argument("--registry", help="disambiguate by registry name (substring)")
    ap.add_argument("--export", action="store_true",
                    help="print shell exports; refuses if more than one pack matches")
    ap.add_argument("--prefix", default="KYVERNO",
                    help="variable prefix for --export (default KYVERNO)")
    args = ap.parse_args()

    if not KEY:
        sys.exit("PALETTE_API_KEY must be set")

    rows = []
    for spec in search_packs(args.name):
        if args.exact and (spec.get("name") or "").lower() != args.name.lower():
            continue
        if args.type and (spec.get("type") or "") != args.type:
            continue
        for reg in (spec.get("registries") or []):
            rows.append({
                "name": spec.get("name"), "type": spec.get("type"),
                "registry": reg.get("name"), "registry_uid": reg.get("uid"),
                "version": reg.get("latestVersion"), "pack_uid": reg.get("latestPackUid"),
            })

    if not rows:
        sys.exit(f"no pack matching {args.name!r}"
                 + (f" of type {args.type!r}" if args.type else ""))

    if args.registry:
        rows = [r for r in rows
                if args.registry.lower() in (r["registry"] or "").lower()]
        if not rows:
            sys.exit(f"no publication of {args.name!r} in a registry matching "
                     f"{args.registry!r}")

    rows.sort(key=lambda r: (r["name"] != args.name, r["type"] != "oci", r["name"]))

    if args.export:
        # A pack name is NOT unique. The same pack is commonly published to several
        # registries at different versions -- kyverno appears in at least three, one of
        # which is a release candidate. Picking one silently would quietly build a profile
        # against the wrong thing, so refuse and make the caller choose.
        if len(rows) > 1:
            print(f"{len(rows)} packs match {args.name!r} -- narrow it down with "
                  f"--exact / --type / --registry:\n", file=sys.stderr)
            for r in rows:
                print(f"  {r['name']} {r['version']} ({r['type']}) "
                      f"in {r['registry']}", file=sys.stderr)
            return 2
        b = rows[0]
        pfx = args.prefix.upper()
        print(f"export {pfx}_UID={b['pack_uid']}")
        print(f"export {pfx}_REG={b['registry_uid']}")
        print(f"export {pfx}_TAG={b['version']}")
        print(f"export {pfx}_TYPE={b['type']}")
        print(f"# {b['name']} {b['version']} ({b['type']}) from {b['registry']}",
              file=sys.stderr)
        return 0

    w = max(len(r["name"]) for r in rows)
    print(f"{'PACK'.ljust(w)}  {'TYPE':<8} {'VERSION':<12} {'REGISTRY':<28} "
          f"{'PACK UID':<26} REGISTRY UID")
    for r in rows:
        print(f"{(r['name'] or '').ljust(w)}  {(r['type'] or ''):<8} "
              f"{(r['version'] or ''):<12} {(r['registry'] or ''):<28} "
              f"{(r['pack_uid'] or ''):<26} {r['registry_uid'] or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
