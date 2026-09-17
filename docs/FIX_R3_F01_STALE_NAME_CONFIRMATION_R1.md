# R3 F01: preserve the name the operator actually confirmed

A row confirmation or member reclassification selecting an existing unified fee
must supply `expected_fee_id` equal to `target_fee_id` and the positive integer
`expected_name_version` that the operator saw. The existing row API fields are
reused; no migration, item-correction API or fee-name history format changes.

`RowConfirmationService.confirm()` first returns an exact successful command-ledger
replay, using the existing request digest. A fully missing expected pair remains
structurally parseable only for this compatibility. A fresh existing-target save
without the pair is rejected with HTTP 422, and no member, Memory, history or
command record is written. Partially supplied, wrongly typed or mismatched pairs
are also rejected rather than silently adopting a different name.

For a fresh request, the service takes the existing case lock and tenant fee-catalog
transaction lock. It then reads the current active tenant fee and compares the
expected name version before any member mutation. If another case renamed the
fee while this dialog was open, or while this request waited for the lock,
HTTP 409 is returned and the whole save remains unapplied. The operator must
review the refreshed name and make a new explicit save. An unavailable or
foreign fee retains the existing scope-error response.

The version check also detects a name changing X → Y → X: equal current text
alone cannot establish that the operator saw the current version. Existing
case, item, Memory, legacy-evidence, idempotency and source checks remain in
force. The new expected fields participate in the existing request digest;
changing them under the same successful request ID remains a conflict.

`RENAME_FEE` keeps its existing stable-identity/name-version contract. First
confirmation or explicit reclassification to a newly created fee has no
existing target version and does not send this pair. Historical item snapshots,
readonly prices, canonical learning, current-file facts and old exports are
unchanged. The legacy item-correction and amount-only APIs are not broadened or
altered by this row-level fix.

Verification distinguishes real isolated PostgreSQL and two-page browser checks
from SDK/OpenAI calls. This bounded fix introduces no SDK call, real AI request,
budget change, production migration or bulk data repair.
