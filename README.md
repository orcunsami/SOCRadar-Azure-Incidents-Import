# SOCRadar Alarms for Microsoft Sentinel

Imports SOCRadar alarms into Microsoft Sentinel as incidents. SOCRadar is only read, never changed: closing an incident in Microsoft Sentinel does not change the alarm in SOCRadar. The full version of this integration is [SOCRadar-Azure-Incidents](https://github.com/orcunsami/SOCRadar-Azure-Incidents).

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Forcunsami%2FSOCRadar-Azure-Incidents-Import%2Fmaster%2Fazuredeploy.json)

## Architecture

### Alarm Import

Pulls alarms from SOCRadar and opens Microsoft Sentinel incidents. Each incident is titled
`[SOCRadar] #<alarm id> - <title>` and labelled with the alarm id, type, subtype and SOCRadar
severity. OPEN alarms only by default. See [Import window and
de-duplication](#import-window-and-de-duplication) for how it picks its window and skips alarms
it has already imported.

```mermaid
flowchart LR
    A["SOCRadar Platform<br/>Alarms API"] --> B["SOCRadar-Alarm-Import<br/>Logic App"]
    B --> C["Microsoft Sentinel<br/>Incidents"]
```

### Analytics

Alarms and audit events are also written to custom Log Analytics tables. Hunting queries, analytic rules, and the workbook read from them. All three (audit, alarms table, workbook) are toggleable at deploy time.

## Prerequisites

- Microsoft Sentinel workspace -- `WorkspaceName` is either an existing workspace or a new
  name only when you set `DeployNewWorkspace` to `true`, which creates it as part of this deployment
- SOCRadar API key and Company ID

## Parameters

### Required

| Parameter | Description |
|-----------|-------------|
| `WorkspaceName` | Microsoft Sentinel workspace name (not the GUID) |
| `SocradarApiKey` | Your SOCRadar API key |
| `CompanyId` | Your SOCRadar company ID |

A wrong `WorkspaceName` with `DeployNewWorkspace=false` now fails before anything is created.
The deployment resolves the workspace in a `precheck-workspace-exists` step that every other
resource waits on, so the resource group is left empty and the only failed operation is that
one step. Measured on a fresh resource group: `0` resources afterwards.

This used to half-install. Depending on the workspace resource was not enough, because ARM
counts a resource whose `condition` is false as a satisfied dependency -- so the checkpoint
storage account, the Data Collection Endpoint, the workbook, the Microsoft Sentinel API
connection and an **enabled** SOCRadar-Alarm-Import Logic App were all created, and only then did
the deployment fail. If you are looking at a resource group in that state from an earlier
attempt, redeploying over it with the correct name reconciles it; the Logic App in it is
billable until then, so disable it, or delete the resource group if the workspace is not in it.

`DeployNewWorkspace` defaults to `false`, so a typo fails instead of creating a second, empty
workspace under the misspelled name. Set it `true` to create the workspace in this deployment;
the pre-check is skipped in that mode, because the workspace may not exist yet.

**Note:** with the default, the workspace in `WorkspaceName` must already exist in the resource group, unless you set `DeployNewWorkspace` to `true`.

### Redeploy to fix a failed deployment -- do not delete the Logic App first

Redeploying over the same resource group is safe and is the way to fix a failed or partial
deployment. Deleting the Logic App and starting again is **not**, and this is the one recovery
step that makes things worse.

The role assignments are named after the workspace and the playbook, not after the identity
they grant. Deleting a Logic App leaves its assignment behind at workspace scope with a
principal that no longer exists. The next deployment creates a new Logic App with a new
identity, computes the same assignment name, and Azure refuses to move an existing assignment
to a different principal:

```
RoleAssignmentUpdateNotPermitted
Tenant ID, application ID, principal ID, and scope are not allowed to be updated.
```

To recover, delete the orphaned assignments and redeploy. An orphan is the one with no
`principalName` -- its principal is gone:

```bash
WS=$(az monitor log-analytics workspace show -g <workspace-rg> -n <workspace> --query id -o tsv)
az role assignment list --scope "$WS" \
  --query "[?principalName==null].{name:name, role:roleDefinitionName}" -o table
az role assignment delete --ids "$WS/providers/Microsoft.Authorization/roleAssignments/<name>"
```

After that, the same deployment succeeds and the Logic App comes back.

The pre-check cannot catch this -- ARM can assert that a resource exists, not that it is
absent. Renaming the assignments is not an option either: Azure rejects a second assignment for
the same identity, role and scope with `RoleAssignmentExists`, so a new naming scheme would
break redeployment for every existing install. Both measured.

The same collision is why two installs cannot share one workspace. For several SOCRadar
companies in one workspace, use the MSSP edition, which is built for it.

### Cross-RG: Microsoft Sentinel must already be onboarded, and AlertBacked is not available

When `WorkspaceResourceGroup` points somewhere else, this template does not onboard the
workspace -- both the Microsoft Sentinel solution and its onboarding state are only created in
the deployment resource group. A `precheck-external-workspace` step therefore reads the
workspace's onboarding state and stops the deployment if Microsoft Sentinel is not there:

```
Microsoft Sentinel was not found on the workspace '<name>'
```

This used to succeed. The deployment went green and the integration was dead, because a
workspace can carry the Microsoft Sentinel solution and still have no onboarding state.

`AlertBacked` is the default, so a cross-RG deployment has to set `IncidentMode=Direct`. Without
it, the same step rejects the deployment during template validation, before anything is created:

```
The provided value for the template parameter 'IncidentMode' is not valid.
The value 'AlertBacked' is not part of the allowed value(s): 'Direct'.
```

Alert-backed mode needs `SOCRadar_Alarms_CL` and its data collection rule, and this template
only creates those in its own resource group -- but the analytics rule that queries the table
is gated on `IncidentMode` alone, so it would have been created against a table that does not
exist. Deploy into the workspace's own resource group to use alert-backed mode.

### Optional

| Parameter | Default | Description |
|-----------|---------|-------------|
| `WorkspaceLocation` | RG region | Workspace region (e.g., `northeurope`). Defaults to the resource group's region, which is wrong whenever the workspace lives elsewhere -- see [Cross-Region / Cross-RG](#cross-region--cross-rg) |
| `WorkspaceResourceGroup` | deployment RG | Set if workspace is in a different RG. Changes what gets deployed -- see [Cross-Region / Cross-RG](#cross-region--cross-rg) |
| `DeployNewWorkspace` | `false` | `false` requires the workspace to exist and fails on a misspelled name before anything is created. `true` creates `WorkspaceName` when it does not exist yet; an existing workspace of that name is left as it is: the workspace resource states no settings, and a live check found tags, pricing tier, retention, daily cap and feature flags unchanged after redeploying. Ignored when `WorkspaceResourceGroup` is not the deployment RG. With `true`, an existing workspace in a region other than `WorkspaceLocation` fails with `InvalidResourceLocation` and nothing is created -- set `WorkspaceLocation` to its region. |
| `SentinelRoleLevel` | `Responder` | `Responder` (least-privilege) or `Contributor`. Applies to the Logic App's identity while `EnableIoCEnrichment` is `false`; with it `true` the identity gets Contributor so it can add entities |
| `PollingIntervalMinutes` | `5` | How often to check for alarms (1-60). Also sets the floor of the import window |
| `InitialLookbackMinutes` | `600` | Lookback window when there is no checkpoint yet (10 hours) |
| `ImportAllStatuses` | `false` | `true` imports RESOLVED / FALSE_POSITIVE / MITIGATED too, as already-closed incidents -- see [Importing closed alarms](#importing-closed-alarms) |
| `IncidentMode` | `AlertBacked` | `AlertBacked` writes the alarms to `SOCRadar_Alarms_CL` and a scheduled analytics rule raises one alert and one incident per alarm, which is what makes them show up in the Microsoft Defender portal. `Direct` creates the incidents from the import Logic App; they never reach the Defender portal, and it is the only value that works when the workspace is in another resource group -- see [Incident mode](#incident-mode) |
| `EnableIoCEnrichment` | `true` | Attach IP/domain/URL indicators from the alarm to the incident as entities (see [IoC Entity Enrichment](#ioc-entity-enrichment)) |
| `EnableAuditLogging` | `true` | Writes audit events to `SOCRadarAuditLog_CL` |
| `EnableCredentialRule` | `true` | `AlertBacked` only. `false` switches off the rule that raises an incident for every credential and botnet alarm; those alarms get no incident from any rule -- see [The five rules](#the-five-rules) |
| `EnableAlarmsTable` | `true` | Stores alarm fields in `SOCRadar_Alarms_CL`. The workbook and four of the five hunting queries need it. Forced on by `IncidentMode=AlertBacked` |
| `EnableWorkbook` | `true` | Deploys the SOCRadar Dashboard workbook. Needs `EnableAlarmsTable=true` -- with the table off, the workbook is skipped even when this is `true` |
| `TableRetentionDays` | `365` | Retention for custom tables (30-730) |

## Existing installations

**`IncidentMode` now defaults to `AlertBacked`.** Redeploying an install that ran `Direct` without
setting the parameter switches it: the import window is read once more and every alarm in it gets
a second, rule-created incident. To keep the previous behaviour, pass `IncidentMode=Direct`
explicitly. See [Incident mode](#incident-mode).

Deployments made before `DeployNewWorkspace` existed stated a pricing tier on the workspace
resource, and a template overwrites every field it states. If the target workspace was on a
**commitment tier**, that deployment reset it to `PerGB2018` (pay-as-you-go).

Check the current tier:

```bash
az monitor log-analytics workspace show -g <resource-group> -n <workspace> \
  --query "{sku:sku.name, lastSkuUpdate:sku.lastSkuUpdate}" -o json
```

If `lastSkuUpdate` lines up with when you first deployed this integration and the tier isn't
the one you picked, reset your commitment tier from **Log Analytics workspaces > Usage and
estimated costs > Pricing tier**. The current template states no workspace-level settings at
all, so redeploying or upgrading an existing install -- with `DeployNewWorkspace` `true` or `false`
-- cannot change its pricing tier, retention or daily cap; a mutation test against a
live workspace (non-default 90-day retention, `DeployNewWorkspace=true`) confirmed both the
retention and `sku.lastSkuUpdate` came back untouched after redeploying.

Upgrading also resets the import window once. The checkpoint lives in a storage account named
after the resource group and workspace, so an upgrade in place reuses the same account and keeps
the checkpoint; a deployment into a *different* resource group starts with an empty one and the
first run falls back to `InitialLookbackMinutes`. Either way the de-duplication snapshot stops
that from re-importing anything.

## Moving to the full version

To move to the full version, redeploy [SOCRadar-Azure-Incidents](https://github.com/orcunsami/SOCRadar-Azure-Incidents) over this same resource group with the same parameters, which updates the import Logic App in place; [UPGRADING.md](UPGRADING.md) has the steps and one label caveat.

Do not delete the Logic App or the resource group first, and never run the two repos as separate installs in one workspace: they name the import Logic App and its role assignments identically, so the second install fails or overwrites the first.

## Rotating the API key

No redeployment needed. The key is the `SocradarApiKey` parameter of the Logic App
`SOCRadar-Alarm-Import`; there is no Key Vault or connection behind it.

### Azure portal

1. Open the Logic App: **Resource groups > your resource group > SOCRadar-Alarm-Import**.
2. In the left menu, under **Development Tools**, open **Logic app code view**.
3. Press `Cmd+F` / `Ctrl+F` and search for this exact text, quotes and braces included:
   ```
   "SocradarApiKey": {}
   ```
   It matches once, in the top-level `"parameters"` block near the end of the file. A search for
   `SocradarApiKey` alone also hits the places that use the key; leave those as they are.
4. Put the new key in it:
   ```json
   "SocradarApiKey": {"value": "<new key>"},
   ```
5. Click **Save**. The portal confirms *Successfully saved workflow*.
6. The line goes back to `{}`. That is expected: the portal never shows a stored key, and saving
   without touching the line keeps the old key.

### Check that it worked

The Logic App runs every `PollingIntervalMinutes` (5 by default), so the next scheduled run uses
the new key. To not wait, open the Logic App **Overview** and use **Run trigger**. Open the run
under **Run history**: a run that was already in progress finishes with the old key. HTTP 401
means the key does not belong to this company, 402 means no credit, an inactive key or an invalid
key. The key is not written to the run history: every action that sends it has its inputs hidden.
Nothing runs while the Logic App is **Disabled**.

### Azure CLI

Same result without the portal. Logic App `PATCH` does not accept the parameter and
`az logic workflow update` has no parameter option, so read the Logic App, change the one value
and `PUT` it back:

```bash
RG=<resource-group>; NAME=SOCRadar-Alarm-Import
read -rs NEWKEY; export NEWKEY
URI="https://management.azure.com$(az logic workflow show -g $RG -n $NAME --query id -o tsv)?api-version=2019-05-01"
az rest --method GET --uri "$URI" > /tmp/wf.json
python3 - <<'PY' > /tmp/body.json
import json, os
w = json.load(open("/tmp/wf.json")); p = w["properties"]
p["parameters"]["SocradarApiKey"] = {"value": os.environ["NEWKEY"]}
print(json.dumps({"location": w["location"], "tags": w.get("tags"), "identity": w["identity"],
                  "properties": {"state": p["state"], "definition": p["definition"],
                                 "parameters": p["parameters"]}}))
PY
az rest --method PUT --uri "$URI" --body @/tmp/body.json --query properties.provisioningState -o tsv
rm -f /tmp/wf.json /tmp/body.json; unset NEWKEY
```

The script keeps the new key in `/tmp/body.json` until the last `rm`. Keep `identity` in the
body: the role assignments belong to that managed identity. The state (`Enabled` or `Disabled`)
is kept as it was.

## What Gets Deployed

- **SOCRadar-Alarm-Import** Logic App -- imports alarms as incidents. It only reads from SOCRadar
- **Checkpoint storage account** -- one Standard_LRS account with a single `ImportState` table
  holding one row per company, so the import knows where it left off. Shared-key access is
  disabled; the Logic App reads and writes it with its own managed identity
- **SOCRadar_Alarms_CL** custom table (optional)
- **SOCRadarAuditLog_CL** audit table (optional)
- **SOCRadar Dashboard** workbook (optional, needs the alarms table): alarms by severity, status and type, open and closed alarms (latest state per alarm), every alarm with its incident number, status and classification (alert-backed mode). The incident panel reads `SecurityAlert` and `SecurityIncident` and stays empty where those tables do not exist yet
- **Five hunting queries** under **Microsoft Sentinel > Hunting** (see [Hunting Queries](#hunting-queries))
- Data Collection Endpoint and Rules for custom tables
- **Workspace** -- when `DeployNewWorkspace=true` (not the default) and no workspace of that name exists in the deployment RG, with the subscription default tier; an existing one is left untouched
- **Microsoft Sentinel onboarding** -- applied whenever the workspace is in the deployment RG, new or existing
- Role assignments giving the Logic App identity least privilege: Log Analytics Reader,
  Monitoring Metrics Publisher on each DCR, Storage Table Data Contributor on the checkpoint
  account, and the Microsoft Sentinel role from `SentinelRoleLevel`
  (the import identity is raised to Contributor only while `EnableIoCEnrichment=true`)

One deployment installs all of the above. The templates under `Playbooks/` exist for
environments that deploy the pieces separately -- see [below](#deploying-playbooks-separately-not-recommended).

## Incident Labels

Every imported incident carries these labels. The hunting queries and the full
version read them, so they are a contract, not decoration.

| Label | Example | Purpose |
|---|---|---|
| `SOCRadar` | `SOCRadar` | Marks the incident as ours |
| `SOCRadar-Alarm-<id>` | `SOCRadar-Alarm-104658646` | The machine-readable alarm id |
| alarm main type | `Domain` | From `alarm_type_details.alarm_main_type` |
| alarm sub type | `Impersonating Domain` | From `alarm_type_details.alarm_sub_type`, when the alarm has one |
| `SOCRadar-Severity-<LEVEL>` | `SOCRadar-Severity-CRITICAL` | The SOCRadar severity as SOCRadar reports it, including CRITICAL, which Microsoft Sentinel has no level for |

In `AlertBacked` mode the incidents carry only `SOCRadar`; an automation rule cannot set per-alarm
labels. The alarm id is on the incident's URL entity instead -- see [Incident mode](#incident-mode).

The alarm id can be read from `SOCRadar-Alarm-<id>`, from the incident title, and last from the
incident's URL entity (`.../alarm-management?...&value=<id>`), so alert-backed incidents, which carry no per-alarm
labels, still resolve it. The obvious field for this, `providerIncidentId`, cannot be used: Azure overwrites both it and `providerName` on
any incident created through the API, silently and without an error.

## Import window and de-duplication

**The window** decides how far back to ask SOCRadar for alarms. It comes from a checkpoint row
in the deployed storage table (`PartitionKey` = your company id, `RowKey` = `import`), stamped
with the time the run *started*:

- No row yet -- first run, or a deployment into a new resource group -- falls back to
  `InitialLookbackMinutes` (600).
- Otherwise the gap since the last successful run, plus 15 minutes of overlap, with a floor of
  `max(PollingIntervalMinutes x 6, 60)` minutes and a ceiling of 7 days.
- The row is written only after the run has read every page. A run that dies halfway leaves the
  window where it was, so nothing is skipped.
- On a fresh deployment the first write can fail while the role assignment propagates. That is
  recorded in the run history and is not fatal: the window stays on the fallback and the next
  run writes the row.
- Cost is negligible -- a handful of rows a day in a Standard_LRS table.

**De-duplication** decides whether an alarm already has an incident. Before importing, the run
lists every `[SOCRadar]`-titled incident in the workspace and skips any alarm whose id is
already there, which is what makes a widened window harmless.

Earlier versions derived the window from the newest existing incident's title. That coupled the
window to the incident list, so a workspace whose incidents had been cleaned up would re-import
from scratch.

## Incident mode

`IncidentMode` decides who creates the incident. The default is `AlertBacked`.

**`Direct`** is what the sections above describe: the import Logic App creates one incident per
alarm through the Microsoft Sentinel API. Those incidents never appear in the Microsoft Defender
portal's unified queue, which only lists incidents backed by an alert.

**`AlertBacked`** (default) makes the import write each alarm to `SOCRadar_Alarms_CL` and nothing else. A
set of five scheduled analytics rules (every 10 minutes, deployed with this mode) raises one
alert per row, and Microsoft Sentinel -- or Microsoft Defender XDR, when the workspace is
onboarded to it -- creates the incident. An automation rule adds the `SOCRadar` label so they are recognisable as SOCRadar's.

### The five rules

Each alarm goes to exactly one rule, chosen by its SOCRadar type (main type and sub type
together; the same sub type exists under two main types). Every rule has its own entities and
custom details, so an analyst or an automation rule can act on what matters for that kind of
alarm.

| Rule | Takes | Entities (after the alarm link) | Custom details (next to the shared set) | MITRE |
|---|---|---|---|---|
| `SOCRadar - Lookalike and phishing domain` | Impersonating Domain, Phishing Domain state changes | phishing domain (DNS), its IP | `SuspiciousDomain`, `SuspiciousIp`, `PhishingScore`, `PhishingKeyword` | ResourceDevelopment, InitialAccess |
| `SOCRadar - Credential exposure` | stolen credentials on dark web bot markets, botnet, blackmarket botnet | machine name (Host), device IP | `CredentialOrigin`, `HasMachineInfo`, `MachineName`, `DeviceIp`, `DeviceCountry`, `MalwareFamily`, `AssetMatch` | CredentialAccess |
| `SOCRadar - Data leak and PII exposure` | code repository, dark web, IM platform, public storage, PII, malware analysis service | the leak location (URL) | `LeakChannel`, `LeakSource`, `LeakUrl`, `AttackType`, `MatchedAsset` | Reconnaissance, Exfiltration |
| `SOCRadar - Vulnerability and CVE` | vulnerability monitoring and vulnerability intelligence | affected IP, affected domain | `CveId`, `CvssScore`, `AffectedSoftware`, `AffectedIp`, `AffectedDomain` | Reconnaissance, InitialAccess |
| `SOCRadar - Other alarms` | everything else, including any type SOCRadar adds later | first IP and URL found in the alarm, the phishing domain if the text names one | `SuspiciousDomain` | none |

The shared custom details are `AlarmId`, `AlarmMainType`, `AlarmSubType`, `SocradarSeverity`,
`AlarmStatus`, `AlarmCategory` (which rule took it) and `MonitoredAsset` (your asset or brand
label from the alarm; never an entity, except on vulnerability alarms where the affected asset
is what has to be fixed). Severity comes from SOCRadar for every rule. VIP alarms have no rule of
their own: SOCRadar's alarm data held none to base one on, so they fall into `Other alarms`.

The credential rule is the largest by volume. `EnableCredentialRule=false` switches it off: those
alarms stay in `SOCRadar_Alarms_CL` (hunting queries and the workbook still see them) but get no
incident, and no other rule picks them up. Rule names, severity, tactics and the rest can be
edited in Microsoft Sentinel; a redeploy overwrites those edits.

### Sample automation rules

`DeploySampleAutomationRules=true` (default `false`, `AlertBacked` only) adds two example
automation rules to copy from:

| Rule | Runs on | Does |
|---|---|---|
| `SOCRadar sample - phishing incidents: label and raise severity` | incidents of the phishing rule | adds the label `SOCRadar-Phishing`, sets severity High |
| `SOCRadar sample - incidents with a suspicious domain: label` | incidents whose alert has a `SuspiciousDomain` custom detail | adds the label `SOCRadar-SuspiciousDomain` |

Neither adds the `SOCRadar` label: the labeling rule (order 1) does that.

Notes from deploying these on a live workspace:

- A custom-detail condition has to be a `PropertyArray` condition, not a plain property condition
  (`Property 'IncidentCustomDetailsKey' can only be used as a nested condition in a condition of type
  'PropertyArray'`). `IncidentCustomDetailsKey` goes in an array of type `CustomDetails`,
  `IncidentCustomDetailsValue` in an array of type `CustomDetailValues`, and the platform rejects the
  key inside a values condition. A condition on a value therefore cannot name the detail it belongs to.
  A value-only condition (`CustomDetailValues`, `Contains`) did not match in a live test, even though
  one of the alert's custom detail values contained the text, so the samples use the key condition.
- To branch on a specific rule, use the rule id (`IncidentRelatedAnalyticRuleIds`) as the first sample does:
  one rule is one alarm type.

The credential rule never copies the exposed credential (user name, URL, password) into an alert.

### Microsoft Defender portal

Microsoft ends the Microsoft Sentinel experience in the Azure portal on 31 March 2027; after
that date workspaces are managed only in the Microsoft Defender portal. A workspace opened in
the Defender portal lists incidents backed by an alert. Incidents created through the API or by a
Logic App do not reach it, and after that date they have no portal to be seen in.

| | `AlertBacked` | `Direct` |
|---|---|---|
| Incident in the Defender queue | yes (provider shown as Microsoft XDR) | no |
| Incident in the Azure portal | yes | yes, until 31 March 2027 |
| Entities (alarm link, IP, phishing domain, URL) | on the alert, filled when the incident is created | bookmark based, Azure portal only |
| Custom details (`AlarmId`, `AlarmMainType`, `AlarmSubType`, `SocradarSeverity`, `AlarmStatus`, `SuspiciousDomain`, `MonitoredAsset`) | on the alert, usable in automation rules | none |
| Incident delay after the alarm is imported | about 20 minutes (the rule waits 12 minutes) | within the polling interval |
| Description | plain text (Defender shows it raw) | markdown |

Measured on a workspace connected to Defender: an incident closed in the Defender portal reached
Sentinel in about 6 seconds. Defender may rename an incident when its correlation merges alerts.

What is different in `AlertBacked`:

- The alarms table is deployed whatever `EnableAlarmsTable` says, and the workspace has to be in
  the deployment resource group: the table is never created cross-RG and the rule fails to
  deploy without it.
- De-duplication is an `alarm-<id>` row per ingested alarm in the checkpoint table, not the
  incident list. The rows stay (one per alarm ever ingested) and cost next to nothing.
- The import writes at most `48 / (10 / PollingIntervalMinutes + 1)` new alarms per run (16 at
  the default 5 minutes) and holds the checkpoint when it hits that cap, so a backlog drains
  over the following runs instead of landing in one rule run. The reason is a documented
  platform limit: a rule run with more than 50 customised values drops every customised title
  and severity for that run (a 58-row run kept them in our test, so read the cap as a guard,
  not as the edge). The rule runs every 10 minutes over rows that are at least 12 minutes old, so the cap keeps
  the rows that become eligible in one run under that. A run that still exceeds the limit gets the rule's default name and Medium severity;
  the alarm id is on the incident's URL entity either way.
- Each alarm is written once and the incident is Microsoft Sentinel's to create. If the platform
  skips a rule run (seen once on a fresh test workspace, for about 25 minutes: alerts written, no
  incidents), those alarms are not re-alerted and never get an incident. `Direct` has no such
  dependency.
- Incident title `[SOCRadar] #<id> - <title>`; severity mapped as in Direct mode (CRITICAL/HIGH ->
  High, MEDIUM -> Medium, everything else -> Low); label `SOCRadar` only. The description is a
  report of plain "Field: value" lines (alarm id/type/severity/status/date, the alarm text, and
  a Content block of up to 5 "Field: value" lines of at most 160 characters -- suspicious
  domain, compromised IPs/domains, malware family/path, asset, tags), budgeted to stay inside the alert description's byte limit; it has no table and no markdown escaping
  because the Defender portal shows the description as raw text. It ends with a `SOCRadar severity: <LEVEL>`
  line, then the link to the alarm.
- The import extracts the first IPv4, domain and URL indicator from the alarm text and content
  (the same regex technique Direct mode uses for its bookmark enrichment) and writes them to the row. The
  catch-all rule maps the IP and the URL from them (a SOCRadar platform link is dropped, it is not an
  indicator); the typed rules read their own fields from the alarm instead (see
  [The five rules](#the-five-rules)). The domain is different: the first regex domain is alphabetical and on lookalike
  alarms it is often a name server, so the DNS entity is the phishing domain instead
  (`SuspiciousDomain`, read from the `Phishing Domain:` line of the alarm text, falling back to
  the `phishing_domain` payload key; empty for alarm types that have none). The customer's own
  brand (`alarm_asset`) is never an entity; it is the `MonitoredAsset` custom detail, next to
  `SuspiciousDomain`, so an automation rule can branch on them. An `AlertBacked` incident's
  Entities tab is populated the same run the incident is created. Together with `AlarmUrl` a rule
  has 2 to 4 entity mappings; a live deploy measured the platform ceiling at 5 (not the 10
  originally assumed), so this is one indicator per type, not three. This extraction always runs in `AlertBacked` mode and
  does not check `EnableIoCEnrichment` -- that parameter only gates Direct mode's bookmark-based
  enrichment (which needs the elevated Contributor role); the alert-backed path maps entities
  through the rule itself and needs no such elevation, only one extra Log Analytics query per
  alarm. Incident grouping keys off the `AlarmId` custom detail rather than an entity, so two
  different alarms that happen to share an IoC indicator do not merge into one incident.
- One alert per alarm, best effort. The platform runs a rule's query about 6 minutes behind the
  clock, so the rule runs every 10 minutes, only considers a row once it is 12 minutes old, and
  drops alarms that already have an alert (it reads `SecurityAlert` for the `AlarmId` custom
  detail). In the live test a closed incident was not followed by a second one for the same
  alarm. If the previous alert is ever not visible yet, the duplicate alert joins the same
  incident (grouping is by `AlarmId`). The cost is delay: in the live test an incident showed up
  about 20 minutes after the import (the 12 to 25 minute range is an estimate), not 3 to 11. An
  alarm stays eligible for a day after it is written; if the rule does not run for longer than
  that, the alarm never gets an alert.
- Upgrading a single-rule `AlertBacked` install to the five rules: the old rule keeps its id and
  becomes `Other alarms`; four new rules are added next to it, and the labeling automation rule
  now lists all five. An alarm the old rule alerted in its last run before the redeploy can be
  alerted once more by its new typed rule if that rule's first run comes before the first alert
  is readable (up to about 10 minutes; not measured on an upgrade), which opens a second incident.
  Close it. Rows written before the upgrade lack the payload keys the typed rules read (the
  alarms DCR keeps them from the redeploy on), so for at most a day their typed entities and
  custom details are empty. Older versions: the rule is updated in place and
  skips alarms whose earlier alert carries the `AlarmId` custom detail. An alarm the old rule
  alerted moments before the redeploy may still be alerted once more, and if its first incident
  was already closed that opens a second one. Not measured; close it.
- Measured on a workspace connected to Microsoft Defender XDR: the incident kept the
  `[SOCRadar] #<id>` title and the description. Defender can rename an incident when its
  correlation merges alerts; the alarm id is on the URL entity either way.
- Closed alarms never become incidents here. With `ImportAllStatuses=true` they still land in
  the table, but the rule only fires on `OPEN`.
- The IoC entity enrichment reaches these incidents on the next import run, through the same
  title match it uses for Direct incidents.
- `PollingIntervalMinutes` below 5 lowers the per-run cap (4 at 1 minute) rather than the
  rule interval, which cannot go below 5.

Switching an existing install from `Direct` to `AlertBacked` re-ingests the current import
window once, and every alarm in it gets a second, rule-created incident; close the old ones.
Switching back does the same in reverse.

## Importing closed alarms

With `ImportAllStatuses=false` (default) only OPEN alarms are imported, as active incidents
through the Microsoft Sentinel connector.

With `ImportAllStatuses=true` every other status is imported too, as an incident that is created
already closed with the classification mapped from the alarm status:

| SOCRadar status | Microsoft Sentinel classification | Classification reason |
|---|---|---|
| `FALSE_POSITIVE` | `FalsePositive` | `InaccurateData` |
| `MITIGATED` | `BenignPositive` | `SuspiciousButExpected` |
| `RESOLVED` | `TruePositive` | `SuspiciousActivity` |
| anything else (e.g. `INVESTIGATING`) | `Undetermined` | none |

These incidents are created closed and are not touched again. SOCRadar is not called again
for them.

## Severity mapping

The import maps SOCRadar CRITICAL and HIGH to a High incident, MEDIUM to Medium, and everything
else, INFO included, to Low. Microsoft Sentinel's Informational is not used, and it has no
Critical. The level SOCRadar reported stays on the incident as the `SOCRadar-Severity-<LEVEL>`
label.

## IoC Entity Enrichment

Microsoft Sentinel's incident API accepts no entities directly, so an incident's **Entities** tab
is fed only by alerts and bookmarks. With `EnableIoCEnrichment` (default `true`), the import
playbook extracts IPv4/domain/URL indicators from the alarm, writes them to a bookmark with
entity mappings, and relates that bookmark to the incident.

- Up to 100 deduplicated indicators per incident; hashes are not extracted; `socradar.com` and
  filename-like values are excluded.
- Bookmark write needs **Microsoft Sentinel Contributor**, which Responder does not include --
  the import playbook's identity is raised to Contributor only while this is `true`.
- Entities can take a few minutes to appear after the incident is created.
- Enrichment failures never block the import -- the incident and audit log are unaffected.

Set `EnableIoCEnrichment=false` to keep the import identity on `SentinelRoleLevel` (Responder).

## Role Selection

The Logic App runs with a Managed Identity:

- **Responder** (default) -- enough for create / update / close / classify.
- **Contributor** -- only if you rely on automation rules that need elevated access.

`SentinelRoleLevel` does not apply to the import identity while `EnableIoCEnrichment` is true --
see [IoC Entity Enrichment](#ioc-entity-enrichment).

## Deploying Playbooks Separately (not recommended)

Use the one-click template above for new installs. The templates under `Playbooks/` are kept
for existing separated deployments:

- The standalone Import playbook has the same IoC entity enrichment as the combined template
  (`EnableIoCEnrichment`, default `true`) -- see [IoC Entity Enrichment](#ioc-entity-enrichment).
  Its identity is raised to Contributor the same way. Set `EnableIoCEnrichment=false` if you
  want it to stay on `SentinelRoleLevel`.
- It deploys its own checkpoint storage account. The name is derived from the resource group
  and the workspace, so an import playbook deployed into the same resource group as a combined
  install shares that install's account and checkpoint row; deployed into another resource
  group it keeps its own.
- If you enable the custom tables but leave `AlarmsDcrResourceId` / `AuditDcrResourceId` empty,
  the deployment still succeeds while every ingestion call returns 403 and the tables stay
  silently empty.
- `IncidentMode=AlertBacked` works here too, but the analytics rule it deploys needs
  `SOCRadar_Alarms_CL` to exist already: deploy `SOCRadar-Alarms-Infrastructure` first and pass
  its outputs, as below.

If you still deploy them separately, deploy the infrastructure templates first and pass their
DCR resource ID outputs into the import playbook:

```bash
az deployment group create -g <resource-group> \
  --template-file Playbooks/SOCRadar-Alarms-Infrastructure/azuredeploy.json \
  --parameters WorkspaceName=<workspace> WorkspaceLocation=<region>
# take alarmsDcrId from the deployment output

az deployment group create -g <resource-group> \
  --template-file Playbooks/SOCRadar-Alarm-Import/azuredeploy.json \
  --parameters WorkspaceName=<workspace> SocradarApiKey=<key> CompanyId=<id> \
               EnableAlarmsTable=true DceEndpoint=<dceEndpoint> \
               AlarmsDcrImmutableId=<alarmsDcrImmutableId> AlarmsDcrResourceId=<alarmsDcrId>
```

`tools/check_template_drift.py` compares these standalone templates against `azuredeploy.json`
on every push and PR so they cannot silently fall behind.

Before pushing, run `tools/check_commit_hygiene.sh`: it fails when a commit message in
`master..HEAD` carries a company id, a workspace or resource group name, an alarm id or an AI
signature. It can also run as a `commit-msg` hook (`--msg-file`).

## Hunting Queries

Five queries are deployed with the integration and appear under **Microsoft Sentinel >
Hunting**. Nothing to import.

| Query | Reads | Needs |
|---|---|---|
| SOCRadar Alarm Overview | `SOCRadar_Alarms_CL` | `EnableAlarmsTable=true` |
| SOCRadar Critical Alarms | `SOCRadar_Alarms_CL` | `EnableAlarmsTable=true` |
| SOCRadar Alarm Trends | `SOCRadar_Alarms_CL` | `EnableAlarmsTable=true` |
| SOCRadar Incident Correlation | `SecurityIncident` | nothing -- always deployed |
| SOCRadar Audit Analysis | `SOCRadarAuditLog_CL` | `EnableAuditLogging=true` |

The custom tables keep a fixed column list, so a query naming a column outside it would return
nothing forever without failing. `tests/test_hunting_queries.py` checks every column each query
reads against the tables the deployment actually creates.

`socradar-kql-queries.kql` holds these and other queries as plain KQL, for pasting into Logs.

## Analytic Rules

Two scheduled rules ship in `Analytic Rules/` as YAML. They are not created by the
deployment -- import them once the custom tables have data: **Microsoft Sentinel > Analytics >
Import**, then select the files. Both need `EnableAlarmsTable=true`.

| Rule | Fires when |
|---|---|
| `SOCRadarCriticalAlarmDetection.yaml` | An open alarm arrives with HIGH or CRITICAL severity |
| `SOCRadarAlarmVolumeSpike.yaml` | Hourly alarm count for a type exceeds 3x that type's 7-day hourly average and is above 5 |

## Cross-Region / Cross-RG

- Different region -> set `WorkspaceLocation` to the workspace's own region. A workspace's
  region is independent of its resource group's region, so read it from the workspace
  Overview blade instead of assuming they match -- the parameter defaults to the resource
  group's region, which is wrong whenever they differ. Deploying with the wrong value fails with:

  ```
  LinkedResourceNotFound: Linked Workspace '/subscriptions/.../workspaces/<name>'
  was not found in location '<region>'
  ```

  If the workspace name is correct, the region is the cause: set `WorkspaceLocation` to the
  workspace's region and redeploy -- the message names the resource, not the actual cause.
  (The template cannot read the region off the workspace for you: ARM rejects the
  `reference()` function in a resource's `location` field.)
- Different resource group -> set `WorkspaceResourceGroup`. This deploys **the incident import
  only**. The Logic App, the Microsoft Sentinel connection and the checkpoint storage
  account go into the deployment RG, and the role assignments the identities need go into the
  workspace RG. Everything analytics-related is skipped, in both resource groups: the custom
  tables, the Data Collection Endpoint and Rules, the workbook and all five hunting queries.
  `EnableAuditLogging` and `EnableAlarmsTable` are forced off inside the import Logic App to
  match, so nothing tries to ingest into a table that was never created -- you get no silent 403s, and no
  analytics. The Microsoft Sentinel onboarding state is also skipped, so the workspace must
  already be onboarded. `IncidentMode=AlertBacked` is not available cross-RG for the same
  reason: the alarms table it needs is never created there and the deployment fails at the
  analytics rule.
- `DeployNewWorkspace` only works in the deployment resource group -- a workspace cannot be
  created in another RG from this template.

## Post-Deployment

The Logic App starts 3 minutes after deployment, so role assignments have time to propagate.

## Removing the integration

Deleting the resource group removes everything, including the checkpoint storage account. If you
delete resources individually, remember the storage account -- it is the one resource whose name
is derived rather than fixed:

```bash
az storage account list -g <resource-group> --query "[?starts_with(name,'srinc')].name" -o tsv
```

If the workspace is in **another** resource group, deleting the integration resource group leaves
the two role assignments behind at workspace scope with no principal to grant. Azure keeps them,
and a later install from a different resource group then fails with
`RoleAssignmentUpdateNotPermitted` -- the assignment name is derived from the workspace, the
playbook name and the role, so the new identity collides with the orphan. That deployment failure
is not a clean rollback either: the Logic App, storage account and API connection it created
before the role step are left enabled. Remove the orphans first, or reuse the original resource
group name:

```bash
az role assignment list --scope <workspace-resource-id> \
  --query "[?principalName==null].{name:name,role:roleDefinitionName}" -o table
az role assignment delete --ids <id>
```

## Support

- **Detailed Documentation (SOCRadar customers):** [Microsoft Azure Sentinel Integration](https://help.socradar.io/hc/en-us/articles/41316851769745-Microsoft-Azure-Sentinel-Integration-Bi-Directional)
- **Support email:** integration@socradar.io
