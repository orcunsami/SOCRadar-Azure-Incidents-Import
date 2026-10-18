#!/usr/bin/env python3
"""Every template here must be the parent's template minus the Sync half, and nothing else.

This repo was cut from SOCRadar-Azure-Incidents at the commit in PARENT_SHA. Two copies of a
4000-line template drift apart one hand edit at a time, so this check fetches the parent's
templates at that commit and lists every difference. A difference is fine only if it is in
ALLOWED below, with the reason it exists. Anything else is an edit nobody meant, and an
allowance that no longer matches a difference is stale and fails too, so the list cannot rot.

When the parent gets a fix, run with --latest: it compares against the parent's master, and
the new differences are the fixes to port. Then bump PARENT_SHA.

A fetch that fails exits 2 and never passes: not being able to look is not a clean result.
PARENT_DIR=<checkout of the parent> reads the files from disk instead of the network.

Run:  python3 tools/check_parent_subset.py [--latest]
"""

import glob
import json
import os
import sys
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = "https://raw.githubusercontent.com/orcunsami/SOCRadar-Azure-Incidents/{ref}/{path}"

# file -> [(kind, path prefix, why)]. Paths read "resources[type|name] > properties > ...".
ALLOWED = {}

SEP = " > "


def parent_sha():
    for line in open(os.path.join(REPO, "PARENT_SHA"), encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    raise SystemExit("PARENT_SHA is empty")


def fetch(ref, path):
    local = os.environ.get("PARENT_DIR")
    if local:
        with open(os.path.join(local, path), encoding="utf-8") as fh:
            return json.load(fh)
    req = urllib.request.Request(RAW.format(ref=ref, path=path.replace(" ", "%20")),
                                 headers={"User-Agent": "Mozilla/5.0 check_parent_subset"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def keyed(items):
    """A resources array matches by type and name, so a removed entry does not shift the rest."""
    if items and all(isinstance(i, dict) and "type" in i and "name" in i for i in items):
        return {f"[{i['type']}|{i['name']}]": i for i in items}
    return None


def diff(old, new, path=()):
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            if key not in new:
                yield "removed", path + (key,)
            elif key not in old:
                yield "added", path + (key,)
            else:
                yield from diff(old[key], new[key], path + (key,))
    elif isinstance(old, list) and isinstance(new, list):
        a, b = keyed(old), keyed(new)
        if a is not None and b is not None:
            for key in sorted(set(a) | set(b)):
                if key not in b:
                    yield "removed", path + (key,)
                elif key not in a:
                    yield "added", path + (key,)
                else:
                    yield from diff(a[key], b[key], path + (key,))
        elif old != new:
            yield "changed", path
    elif old != new:
        yield "changed", path


def main():
    latest = "--latest" in sys.argv
    ref = "master" if latest else parent_sha()
    files = ["azuredeploy.json"] + sorted(
        os.path.relpath(p, REPO).replace(os.sep, "/")
        for p in glob.glob(os.path.join(REPO, "Playbooks", "*", "azuredeploy.json")))
    problems, allowed_hits, compared = [], set(), 0
    for rel in files:
        try:
            parent = fetch(ref, rel)
        except Exception as exc:  # network, 404, bad JSON: all "could not look"
            print(f"BAKILAMADI: parent {rel} @ {ref}: {exc}")
            return 2
        with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
            child = json.load(fh)
        compared += 1
        for kind, path in diff(parent, child):
            text = SEP.join(path)
            hit = next((a for a in ALLOWED.get(rel, []) if a[0] == kind and text.startswith(a[1])), None)
            if hit:
                allowed_hits.add((rel, hit[0], hit[1]))
            else:
                problems.append(f"{rel}: {kind} {text}")
    for rel, entries in ALLOWED.items():
        for kind, prefix, why in entries:
            if (rel, kind, prefix) not in allowed_hits:
                problems.append(f"{rel}: stale allowance {kind} {prefix!r} ({why})")
    if problems:
        print(f"PARENT SUBSET CHECK FAILED (parent {ref[:12]})\n")
        for item in problems[:60]:
            print("  - " + item)
        if len(problems) > 60:
            print(f"  ... and {len(problems) - 60} more")
        print(f"\n{len(problems)} problem(s).")
        return 1
    print(f"Parent subset: {compared} template(s) match the parent @ {ref[:12]} apart from "
          f"{sum(len(v) for v in ALLOWED.values())} documented difference(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
