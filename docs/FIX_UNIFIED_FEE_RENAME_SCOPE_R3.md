# Stable fee rename (R3)

A unified fee keeps its `fee_id` when its name changes. `fees.name_version` points to the current name; immutable `fee_name_versions` records the name history. `items.current_fee_name_snapshot` / `current_fee_name_version` record the name the specific item adopted. Old cases and exports read those snapshots. Original facts and alias memory keys are unchanged.

The existing row confirmation API accepts explicit `RENAME_FEE` (expected fee ID and name version required), `RECLASSIFY_MEMBERS` (selected members only), or backward compatible `CONFIRM_MEMBERS`. A rename updates the selected current row and one logical name event. Absent ACTIVE aliases retain their mapping and resolve the latest name on future imports. Conflicted/revoked/reclassified aliases are never restored by rename. The simple confirmation dialog shows rename intent and keeps an explicit lightweight reclassification path.

All affected writers use case lock → tenant fee-catalog transaction lock → Memory locks. Import commit rechecks current Memory and effective fee name after acquiring the catalog lock, including imports that read an old name earlier. Amount-only updates retain their existing name snapshot. Same stable identity with different adopted historical names remains a distinct historical view.

Migration `007_fee_name_versions.sql` is additive and transactionally backfills existing displayed names as `BASELINE_MIGRATION`, not SDK evidence or new human confirmation. Repeated migration is a no-op. Keep a private database backup before applying. Do not drop snapshots or restore a backup over newer business records without separate restoration authorization.

`FeeIdentityRepairService.apply` is an explicit operator application service, not a general HTTP merge feature. Its supplied exact plan must prove the unchanged original-to-branch correction, item/name/Memory versions, same tenant/origin, and all affected ACTIVE alias states. It appends an immutable repair event, preserves historical IDs/versions, and updates only the named target case and proven active alias. It never infers equivalence from name similarity or suffixes.

Start the existing application from the project directory:

```sh
.demo-runtime/sdk-business-r1/.venv/bin/python tools/start_real_demo.py
```

Browser: http://127.0.0.1:8765 . Existing launch configuration, fixed SDK wheel and cumulative AI authorization remain unchanged. R3 acceptance uses the existing per-task `pause_ai=true` guard; no actual AI requests are authorized for this stage.
