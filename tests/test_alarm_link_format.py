#!/usr/bin/env python3
"""The "View in SOCRadar" link has one shape, everywhere it is built.

The platform opens an alarm from `app/company/<id>/alarm-management?...&field=alarmId&...&value=<id>`.
The older `company/<id>/alarm/<id>` form returns 404. The link is built in the templates, in
an analytic rule and in the shipped KQL file; this test fails when any of them goes back to the
retired form or loses the live one.
"""

import glob
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RETIRED = '"/alarm/", AlarmId'
LIVE = '/alarm-management?tab=approved&field=alarmId&operator=equals&value=", AlarmId)'

failures = []
checks = 0


def check(condition, message):
    global checks
    checks += 1
    if not condition:
        failures.append(message)


def rel(path):
    return os.path.relpath(path, REPO)


builders = glob.glob(os.path.join(REPO, "Analytic Rules", "*.yaml")) + [os.path.join(REPO, "socradar-kql-queries.kql")]
templates = [os.path.join(REPO, "azuredeploy.json")] + glob.glob(os.path.join(REPO, "Playbooks", "*", "azuredeploy.json"))
check(len(builders) >= 2, "no analytic rule or KQL file found: this check has gone blind")

for path in builders + templates:
    text = open(path, encoding="utf-8").read().replace('\\"', '"')
    check(RETIRED not in text, "%s still builds the retired /alarm/<id> link" % rel(path))

# Files that build an AlarmUrl or SOCRadarLink must build the live one.
for path in builders:
    text = open(path, encoding="utf-8").read()
    if "platform.socradar.com" in text and "CompanyId" in text:
        check(LIVE in text, "%s builds a platform link but not the live alarm-management form" % rel(path))

print("\n".join("  - %s" % f for f in failures))
if failures:
    print("%d problem(s) in %d checks." % (len(failures), checks))
    sys.exit(1)
print("alarm link format: one live shape in templates, rule and KQL (%d checks)" % checks)
