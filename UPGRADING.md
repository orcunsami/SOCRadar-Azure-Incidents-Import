# Moving to the full version

The full version is [SOCRadar-Azure-Incidents](https://github.com/orcunsami/SOCRadar-Azure-Incidents).
It adds a second Logic App, `SOCRadar-Alarm-Sync`, which writes closures and severity back to SOCRadar.

1. Redeploy it over the same resource group with the same parameters (same `WorkspaceName`,
   `WorkspaceResourceGroup`, `DeployNewWorkspace`). The import Logic App has the same name in both
   repos, so it is updated in place and `SOCRadar-Alarm-Sync` is added next to it.
2. Do not delete the `SOCRadar-Alarm-Import` Logic App first. Its role assignment stays behind with a
   dead principal and the next deployment fails with `RoleAssignmentUpdateNotPermitted` (see
   "Redeploy to fix a failed deployment" in the README).
3. Do not delete the resource group either. With `DeployNewWorkspace=true` the workspace lives in it,
   and deleting it deletes the workspace and every incident.
4. This repo never adds the `Synced` label. The full version's `SOCRadar-Alarm-Sync` picks up every
   SOCRadar incident that is closed, was modified in the last 24 hours and has no `Synced` label, and writes a status
   back to SOCRadar for it. That covers closed alarms imported with `ImportAllStatuses=true` and
   every incident an analyst closed here. Label the closed ones `Synced` before you redeploy if you
   do not want their status written back.
5. `SyncSeverity` does not exist here and defaults to `true` in the full version, so a redeploy with
   the same parameters also turns on the severity write-back. Pass `SyncSeverity=false` if you want
   status only.
6. The full version deploys its Logic Apps `Enabled`. A redeploy re-enables an import Logic App you had
   disabled, and starts `SOCRadar-Alarm-Sync` straight away.
7. This repo never deploys or removes `SOCRadar-Alarm-Sync`. If one already exists in the resource
   group, disable it until you want the write-back.
