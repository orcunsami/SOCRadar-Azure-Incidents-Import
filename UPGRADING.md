# Moving to the full version

The full version is [SOCRadar-Azure-Incidents](https://github.com/orcunsami/SOCRadar-Azure-Incidents).
It adds a second Logic App, `SOCRadar-Alarm-Sync`, which writes closures and severity back to SOCRadar.

1. Deploy it into a new resource group, or delete the `SOCRadar-Alarm-Import` Logic App from this one first.
   Both repos name the import Logic App `SOCRadar-Alarm-Import`, so one resource group holds only one of them.
2. If `ImportAllStatuses=true` was on here, the closed alarms were imported without a `Synced` label.
   Label those incidents `Synced` before the full version runs, or it treats them as analyst closures
   and writes a status back to SOCRadar for each.
3. This repo never deploys or removes `SOCRadar-Alarm-Sync`. If one exists in the resource group, disable it
   until you want the write-back.
