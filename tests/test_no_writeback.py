#!/usr/bin/env python3
"""This repo imports alarms and never writes anything back to SOCRadar.

That is the whole product: a customer who adopts the integration step by step starts with
the import only, and nothing here may change the status or severity of an alarm in SOCRadar.
ARM validation cannot see it, because it validates JSON shape and never runs an action.

Checked on every template the repo ships:
  - the deployable root template carries exactly one Logic App, the import one;
  - there is no Sync playbook, no Sync parameter and no Synced label anywhere;
  - every call a workflow makes to platform.socradar.com is a GET. The one optional
    IOC-Enrichment playbook also POSTs to a read-only lookup endpoint, and that single
    endpoint is allow-listed there and nowhere else.

Run:  python3 tests/test_no_writeback.py
"""

import glob
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(REPO, "azuredeploy.json")
PLAYBOOKS = sorted(glob.glob(os.path.join(REPO, "Playbooks", "*", "azuredeploy.json")))
IOC_PLAYBOOK = os.path.join(REPO, "Playbooks", "SOCRadar-IOC-Enrichment", "azuredeploy.json")

# A POST that only looks something up. Allowed in the IOC-Enrichment playbook and nowhere else.
READ_ONLY_POSTS = {(IOC_PLAYBOOK, "/api/ioc_enrichment/get/indicator_details")}

# Gone for good: the write-back endpoints and everything named after Sync.
FORBIDDEN = ["SyncSeverity", "SyncPlaybookName", "SOCRadar-Alarm-Sync", "Update_SOCRadar",
             "alarms/status/change", "alarm/severity", "Synced"]

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

# 2. No Sync playbook directory.
if os.path.isdir(os.path.join(REPO, "Playbooks", "SOCRadar-Alarm-Sync")):
    failures.append("Playbooks/SOCRadar-Alarm-Sync exists")

# 3. No write-back strings in any template.
for path in [ROOT] + PLAYBOOKS:
    text = open(path, encoding="utf-8").read()
    for token in FORBIDDEN:
        if token in text:
            failures.append(f"{rel(path)}: contains {token!r}")

# 4. Every call to SOCRadar is a GET, bar the allow-listed read-only lookup.
seen_get = 0
for path in [ROOT] + PLAYBOOKS:
    for action, method, uri in socradar_calls(load(path)):
        if method == "GET":
            seen_get += 1
        elif (path, uri) in READ_ONLY_POSTS and method == "POST":
            continue
        else:
            failures.append(f"{rel(path)}: {action} sends {method} {uri} to SOCRadar")

if seen_get == 0:
    failures.append("no GET to SOCRadar found in any template - this check has gone blind")

if failures:
    print("NO-WRITEBACK CHECK FAILED\n")
    for item in failures:
        print("  - " + item)
    print(f"\n{len(failures)} problem(s).")
    sys.exit(1)

print(f"No write-back: 1 Logic App, {seen_get} SOCRadar GET call(s), no Sync string in {1 + len(PLAYBOOKS)} template(s).")
