# Agent implementation contract

Read README and docs/01–05 plus docs/07 first. Business baseline is the current conversation-derived scope, not an older proposal slide deck.

- Separate business repository from `document-processing-sdk`. Never modify, stage, reset, test, or signal that repository or its running corpus jobs under this task.
- No customer-maintained master. Corrections in the cost-table workflow create reusable original-label → human-confirmed-fee mappings.
- Prices/date/source are current-document facts, not reusable fee-label memory. Unknown conditions are not wildcards. Same raw alias plus applicable scope/context only; conflicting/revoked knowledge blocks automatic reuse.
- AI output is an unconfirmed suggestion. Do not replace deterministic confirmed mappings with a fresh model guess; do not promote inferred aliases or bulk table approval into human-verified learning.
- Protocol names in this package belong to this application. Verify actual SDK public API before integration. Placeholder adapters fail closed; do not disguise them as real parsing.
- This delivery implements only the offline SQLite learning reference. Production stack is React/TypeScript + Python/FastAPI + PostgreSQL + private storage + worker + permitted OpenAI. No model-training platform, independent vector DB, or microservices by default.
- Request versus implementation status must stay separate. No hidden scope expansion, fixed accuracy/price/timeline promises, automatic external uploads, or active credentials in code.
- First task is W0 only, from docs/06_Codex_W0_指令.txt. After W0, stop for independent review; do not implement all phases in one run.
- Every future limited investigation, implementation, or validation instruction MUST finish with a Review Bundle ZIP + external .zip.sha256 + VERIFICATION.txt, preserving SUCCESS/FAIL/BLOCKED and missing tests. Include exact baselines, patches, commands/exit codes, safe evidence, internal SHA; exclude customer originals, keys, caches, environments, and unrelated SDK corpus evidence. CRC/internal+external hashes/member safety required. Return three absolute paths and STOP. If packaging fails preserve evidence and report actual errors rather than inventing paths.
- No commit, push, merge, tag, release, production deployment, purchases, or email sending without separate authorization.

## v1.1.0 delivery/process change (2026-09-11)
- No dedicated PoC phase or separate PoC implementation. W3 is the three-file sales Demo: two synthetic Excel workbooks and one synthetic PDF. It includes a thin upload/list/correction/preview/save/output UI, not all production management screens.
- Round 1 uploads A's Excel + B's PDF; round 2 uploads A's later Excel in a new case with the same alias/context but new price. Do not preload round-2 learning, force a fake AI mistake, or substitute cache redisplay for correction reuse.
- Optional real-document checks reuse this Demo only when needed and permitted. They are not a mandatory extra step before quoting, nor evidence that all customer files are supported.
- Meeting handoff: Shimanos proposal ends by inviting Morita to demonstrate; customer questions follow the Demo. See docs/07.
- This revision changes documents and process only. Existing reference Python code and JSON contracts are unchanged; it does not authorize execution on the user's computer or claim the real Web Demo is implemented.
