#!/usr/bin/env python3
"""Break the repo on purpose and prove the two gates that define it notice.

tests/test_no_writeback.py and tools/check_parent_subset.py are only worth anything if a
write-back that creeps back in, or a template edited by hand, turns them red. Each mutation
is applied to a throwaway copy of the repo and the gate must exit non-zero. A mutation that
survives means the gate is BLIND and the run exits non-zero. The unmutated copy must pass
first, or the run says so and stops. Bytecode is disabled for the child runs (EXP-AZURE-0179).

    python3 tests/mutate_no_writeback.py [--only writeback|subset]
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = "azuredeploy.json"
IMPORT = "Playbooks/SOCRadar-Alarm-Import/azuredeploy.json"

WRITE_ACTION = {
    "type": "Http", "runAfter": {},
    "inputs": {"method": "POST", "uri": "https://platform.socradar.com/api/company/1/alarms/status/change",
               "body": {"status": 2}},
}


def load(root, rel):
    with open(os.path.join(root, rel), encoding="utf-8") as fh:
        return json.load(fh)


def save(root, rel, data):
    with open(os.path.join(root, rel), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=4)


def first_workflow(template):
    return next(r for r in template["resources"] if r["type"] == "Microsoft.Logic/workflows")


def with_template(rel, edit):
    def apply(root):
        data = load(root, rel)
        edit(data)
        save(root, rel, data)
    return apply


def add_write_action(rel):
    return with_template(rel, lambda t: first_workflow(t)["properties"]["definition"]["actions"]
                         .update({"Zz_Write_Back": copy.deepcopy(WRITE_ACTION)}))


def get_becomes_post(t):
    actions = first_workflow(t)["properties"]["definition"]["actions"]
    stack = [actions]
    while stack:
        node = stack.pop()
        for key, body in node.items():
            if key == "Get_SOCRadar_Page":
                body["inputs"]["method"] = "POST"
                return
            if isinstance(body, dict):
                for sub in ("actions",):
                    if sub in body:
                        stack.append(body[sub])
    raise SystemExit("mutation target Get_SOCRadar_Page not found")


def second_workflow(t):
    t["resources"].append(copy.deepcopy(first_workflow(t)))


def sync_parameter(t):
    t["parameters"]["SyncSeverity"] = {"type": "bool", "defaultValue": True}


def ioc_post_in_import(t):
    first_workflow(t)["properties"]["definition"]["actions"]["Zz_Lookup"] = {
        "type": "Http", "runAfter": {},
        "inputs": {"method": "POST", "uri": "https://platform.socradar.com/api/ioc_enrichment/get/indicator_details"}}


def concat_post(t):
    first_workflow(t)["properties"]["definition"]["actions"]["Zz_Concat"] = {
        "type": "Http", "runAfter": {},
        "inputs": {"method": "POST",
                   "uri": "@{concat('https://platform.socradar', '.com/api/company/1/alarms/status/change')}"}}


def param_uri_post(t):
    first_workflow(t)["properties"]["definition"]["actions"]["Zz_Param"] = {
        "type": "Http", "runAfter": {}, "inputs": {"method": "PATCH", "uri": "@parameters('AnyUrl')"}}


def switch_default_write(t):
    first_workflow(t)["properties"]["definition"]["actions"]["Zz_Switch"] = {
        "type": "Switch", "runAfter": {}, "expression": "x", "cases": {},
        "default": {"actions": {"Zz_Write": copy.deepcopy(WRITE_ACTION)}}}


def workflow_type_action(t):
    first_workflow(t)["properties"]["definition"]["actions"]["Zz_Child"] = {
        "type": "Workflow", "runAfter": {}, "inputs": {"host": {"workflow": {"id": "x"}}}}


def nested_logic_app(t):
    t["resources"].append({"type": "Microsoft.Resources/deployments", "apiVersion": "2022-09-01",
                           "name": "zz-nested", "properties": {"mode": "Incremental", "template": {
                               "resources": [copy.deepcopy(first_workflow(t))]}}})


def userinfo_uri(t):
    first_workflow(t)["properties"]["definition"]["actions"]["Zz_User"] = {
        "type": "Http", "runAfter": {},
        "inputs": {"method": "POST", "uri": "@{parameters('ManagementBaseUrl')}@platform.socradar.com/x"}}


def http_trigger(t):
    first_workflow(t)["properties"]["definition"]["triggers"]["Zz_Hook"] = {"type": "Http", "inputs": {}}


def lowercase_type(t):
    wf = copy.deepcopy(first_workflow(t))
    wf["type"] = "microsoft.logic/Workflows"
    t["resources"].append(wf)


def web_site(t):
    t["resources"].append({"type": "Microsoft.Web/sites", "name": "zz", "properties": {}})


def other_connector(t):
    first_workflow(t)["properties"]["definition"]["actions"]["Zz_Conn"] = {
        "type": "ApiConnection", "runAfter": {},
        "inputs": {"host": {"connection": {"name": "@parameters('$connections')['office365']['connectionId']"}},
                   "method": "post", "path": "/x"}}


def override_header(t):
    first_workflow(t)["properties"]["definition"]["actions"]["Zz_Over"] = {
        "type": "Http", "runAfter": {},
        "inputs": {"method": "GET", "uri": "https://platform.socradar.com/api/x",
                   "headers": {"X-HTTP-Method-Override": "POST"}}}


def ioc_dir(root):
    os.makedirs(os.path.join(root, "Playbooks", "SOCRadar-IOC-Enrichment"))
    with open(os.path.join(root, "Playbooks", "SOCRadar-IOC-Enrichment", "azuredeploy.json"), "w") as fh:
        fh.write('{"resources": []}')


def sync_dir(root):
    os.makedirs(os.path.join(root, "Playbooks", "SOCRadar-Alarm-Sync"))
    with open(os.path.join(root, "Playbooks", "SOCRadar-Alarm-Sync", "azuredeploy.json"), "w") as fh:
        fh.write('{"resources": []}')


def edit_import_default(t):
    t["parameters"]["PollingIntervalMinutes"]["defaultValue"] = 999


def drop_import_action(t):
    actions = first_workflow(t)["properties"]["definition"]["actions"]
    del actions[next(iter(actions))]


def drop_a_variable(t):
    t["variables"].pop(next(iter(t["variables"])))


def readme_edit(fn):
    def apply(root):
        path = os.path.join(root, "README.md")
        text = open(path, encoding="utf-8").read()
        new = fn(text)
        assert new != text, "README mutation changed nothing"
        open(path, "w", encoding="utf-8").write(new)
    return apply


def readme_old_button(text):
    return text.replace("orcunsami%2FSOCRadar-Azure-Incidents-Import%2F", "orcunsami%2FSOCRadar-Azure-Incidents%2F", 1)


def readme_mentions_sync(text):
    return text.replace("## Prerequisites", "The `SyncSeverity` switch is described here.\n\n## Prerequisites", 1)


def readme_mentions_bidirectional(text):
    return text.replace("## Prerequisites", "This is the bidirectional edition.\n\n## Prerequisites", 1)


def readme_no_buttons(text):
    return text.replace("deploytoazurebutton", "deploybutton")


def edit_standalone_playbook(t):
    t["parameters"]["CompanyId"]["metadata"] = {"description": "edited by hand"}


WRITEBACK = [
    ("write action in the root Import workflow", add_write_action(ROOT)),
    ("write action in the standalone Import playbook", add_write_action(IMPORT)),
    ("Get_SOCRadar_Page turned into a POST", with_template(ROOT, get_becomes_post)),
    ("a second Logic App in the root template", with_template(ROOT, second_workflow)),
    ("a Sync playbook directory", sync_dir),
    ("a SyncSeverity parameter", with_template(ROOT, sync_parameter)),
    ("a POST to a SOCRadar URI built with concat", with_template(ROOT, concat_post)),
    ("a PATCH to a URI that comes from a parameter", with_template(ROOT, param_uri_post)),
    ("a write action in a Switch default branch", with_template(ROOT, switch_default_write)),
    ("a Workflow-type action", with_template(ROOT, workflow_type_action)),
    ("a Logic App hidden in a nested deployment", with_template(ROOT, nested_logic_app)),
    ("a URI that hides socradar.com behind a user-info part", with_template(ROOT, userinfo_uri)),
    ("an Http trigger", with_template(ROOT, http_trigger)),
    ("a Logic App typed in lower case", with_template(ROOT, lowercase_type)),
    ("a Microsoft.Web/sites resource", with_template(ROOT, web_site)),
    ("an ApiConnection to another connector", with_template(ROOT, other_connector)),
    ("a GET with a method-override header", with_template(ROOT, override_header)),
    ("an IOC-Enrichment playbook directory", ioc_dir),
    ("a lookup POST in the Import playbook", with_template(IMPORT, ioc_post_in_import)),
    ("a README Deploy button back on the old repo", readme_edit(readme_old_button)),
    ("the README describes a Sync switch", readme_edit(readme_mentions_sync)),
    ("the README calls itself bidirectional", readme_edit(readme_mentions_bidirectional)),
    ("the README loses its Deploy buttons", readme_edit(readme_no_buttons)),
]
SUBSET = [
    ("a parameter default edited by hand", with_template(ROOT, edit_import_default)),
    ("an Import action deleted", with_template(ROOT, drop_import_action)),
    ("a variable deleted", with_template(ROOT, drop_a_variable)),
    ("a standalone playbook edited by hand", with_template(IMPORT, edit_standalone_playbook)),
]
GATES = {
    "writeback": (os.path.join("tests", "test_no_writeback.py"), WRITEBACK),
    "subset": (os.path.join("tools", "check_parent_subset.py"), SUBSET),
}


def run(root, script):
    proc = subprocess.run([sys.executable, "-B", "-I", os.path.join(root, script)],
                          capture_output=True, text=True, timeout=180)
    # A crash also exits 1. Only the gate's own verdict line counts as a catch.
    return proc.returncode, ("CHECK FAILED" in proc.stdout)


def fresh_copy():
    tmp = tempfile.mkdtemp(prefix="mutate-nwb-")
    dst = os.path.join(tmp, "repo")
    shutil.copytree(REPO, dst, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    return tmp, dst


def main():
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    blind = []
    for name, (script, mutations) in GATES.items():
        if only and only != name:
            continue
        tmp, dst = fresh_copy()
        try:
            base, _ = run(dst, script)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if base != 0:
            print(f"[{name}] unmutated copy exits {base}: the gate is already red, nothing to prove yet")
            return 3
        for label, apply in mutations:
            tmp, dst = fresh_copy()
            try:
                apply(dst)
                rc, verdict = run(dst, script)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            caught = rc == 1 and verdict
            print(f"[{name}] {'caught' if caught else 'SURVIVED'}  {label} (rc={rc}, verdict line={verdict})")
            if not caught:
                blind.append(f"{name}: {label}")
    if blind:
        print(f"\n{len(blind)} mutation(s) survived: the gate is blind to them.")
        return 1
    print("\nEvery mutation was caught.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
