#!/usr/bin/env python3
"""This repo imports alarms and never writes anything back to SOCRadar.

That is the whole product: a customer who adopts the integration step by step starts with
the import only, and nothing here may change the status or severity of an alarm in SOCRadar.
ARM validation cannot see it, because it validates JSON shape and never runs an action.

Checked on every template the repo ships:
  - the deployable root template carries exactly one Logic App, the import one;
  - there is no Sync playbook, no Sync parameter and no Synced label anywhere;
  - every call a workflow makes to platform.socradar.com is a GET, no exception: the
    optional IOC-Enrichment playbook of the bidirectional repo POSTs to a lookup endpoint,
    so it is not shipped here either;
  - the README says the same: every Deploy button installs THIS repo (a button left on the
    full repo would silently deploy the Sync half), and the words sync, write-back and
    bidirectional appear nowhere in its text (link addresses excepted). The move to the full
    version lives in UPGRADING.md.

Run:  python3 tests/test_no_writeback.py
"""

import glob
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(REPO, "azuredeploy.json")
PLAYBOOKS = sorted(glob.glob(os.path.join(REPO, "Playbooks", "*", "azuredeploy.json")))

# Gone for good: the write-back endpoints and everything named after Sync.
FORBIDDEN = ["SyncSeverity", "SyncPlaybookName", "SOCRadar-Alarm-Sync", "Update_SOCRadar",
             "alarms/status/change", "alarm/severity", "Synced", "ioc_enrichment"]

failures = []


def rel(path):
    return os.path.relpath(path, REPO)


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def walk_actions(actions, prefix=""):
    for name, body in (actions or {}).items():
        yield prefix + name, body
        if not isinstance(body, dict):
            continue
        yield from walk_actions(body.get("actions"), prefix + name + "/")
        branch = body.get("else")
        if isinstance(branch, dict):
            yield from walk_actions(branch.get("actions"), prefix + name + "/else/")
        for case in (body.get("cases") or {}).values():
            yield from walk_actions(case.get("actions"), prefix + name + "/")


def workflows(template):
    return [r for r in template.get("resources", []) if r.get("type") == "Microsoft.Logic/workflows"]


def socradar_calls(template):
    """Yield (action path, METHOD, uri path) for every HTTP action aimed at socradar.com."""
    for resource in workflows(template):
        for path, body in walk_actions(resource["properties"]["definition"].get("actions")):
            if body.get("type") != "Http":
                continue
            inputs = body.get("inputs") or {}
            uri = str(inputs.get("uri", ""))
            if "socradar.com" not in uri.lower():
                continue
            yield path, str(inputs.get("method", "")).upper(), "/" + uri.split("socradar.com/", 1)[-1].split("?")[0]


# 1. The deployable template carries the import Logic App and nothing else.
root = load(ROOT)
found = workflows(root)
if len(found) != 1:
    failures.append(f"{rel(ROOT)}: {len(found)} Logic Apps, expected exactly 1 (the import)")
elif "ImportPlaybookName" not in json.dumps(found[0].get("name")):
    failures.append(f"{rel(ROOT)}: the one Logic App is not the import playbook")

# 2. Neither the Sync playbook nor the IOC-Enrichment playbook is shipped.
for name in ("SOCRadar-Alarm-Sync", "SOCRadar-IOC-Enrichment"):
    if os.path.isdir(os.path.join(REPO, "Playbooks", name)):
        failures.append(f"Playbooks/{name} exists")

# 3. No write-back strings in any template.
for path in [ROOT] + PLAYBOOKS:
    text = open(path, encoding="utf-8").read()
    for token in FORBIDDEN:
        if token in text:
            failures.append(f"{rel(path)}: contains {token!r}")

# 4. Every call to SOCRadar is a GET.
seen_get = 0
for path in [ROOT] + PLAYBOOKS:
    for action, method, uri in socradar_calls(load(path)):
        if method == "GET":
            seen_get += 1
        else:
            failures.append(f"{rel(path)}: {action} sends {method} {uri} to SOCRadar")

# 5. The README installs and describes this repo.
OLD_REPO = "orcunsami%2FSOCRadar-Azure-Incidents%2F"
THIS_REPO = "orcunsami%2FSOCRadar-Azure-Incidents-Import%2F"
readme = open(os.path.join(REPO, "README.md"), encoding="utf-8").read()
if OLD_REPO in readme:
    failures.append("README.md: a Deploy button still points at the full repo")
buttons = re.findall(r"deploytoazurebutton\)\]\((https://portal\.azure\.com/[^)]+)\)", readme)
if not buttons:
    failures.append("README.md: no Deploy button found - this check has gone blind")
for url in buttons:
    if THIS_REPO not in url:
        failures.append(f"README.md: a Deploy button does not point at this repo: {url[:90]}")
prose = re.sub(r"\]\([^)]*\)", "]", readme)  # link targets are addresses, not description
for match in re.finditer(r"sync|write-?back|written back|bi-?directional", prose, flags=re.I):
    line = prose.count("\n", 0, match.start()) + 1
    failures.append(f"README.md: {match.group(0)!r} on line {line} of the text: this repo describes no write-back")

if seen_get == 0:
    failures.append("no GET to SOCRadar found in any template - this check has gone blind")

if failures:
    print("NO-WRITEBACK CHECK FAILED\n")
    for item in failures:
        print("  - " + item)
    print(f"\n{len(failures)} problem(s).")
    sys.exit(1)

print(f"No write-back: 1 Logic App, {seen_get} SOCRadar GET call(s), no Sync string in {1 + len(PLAYBOOKS)} template(s).")
