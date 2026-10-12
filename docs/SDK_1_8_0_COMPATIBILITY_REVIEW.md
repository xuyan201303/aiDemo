# SDK 1.8.0 compatibility review — 2026-10-12

Status: **PASS for isolated SDK/application regression; deployment and live AI/browser acceptance NOT_RUN.** No deployment or service restart is authorized by this report.

## Baseline and artifact

- Repository: `/Users/kyoen/Projects/aiDemo`; HEAD `375291053bca0590e195f2a7ab0f97e22d25c1a6`. Initial worktree was clean. Changes were made from this baseline; previous temporary patches were not restored.
- Wheel: `/Users/kyoen/Projects/aiDemo/document_processing_sdk-1.8.0-py3-none-any.whl`.
- Version: `1.8.0`; SHA-256: `781161e4af3b0b45a1ca8a7745e774e1a2362eb01bcc30956e51619514fcc4e0`.
- Python: `3.12.13`, fresh `/opt/sdk18-stable-venv` inside the isolated test container. All 296 installed SDK package files matched the designated wheel. No host Python or existing venv was changed.
- Input originals: `01_A社_初回見積.xlsx`, `02_B社_初回見積.pdf`, **`03_C社_次回見積.xlsx`**. The third workbook's embedded fictional company still names A; its actual delivery amount is 9500. Its filename was not treated as company evidence. Exact input hashes are in the bundle.

## Responsibility and mapping

The stable `document_sdk.pdf.analyze_pdf` API returns `unified_result.v4`. Its `UnifiedPdfTable`, `UnifiedPdfTableCell`, page table inventory and optional table source identity are sufficient for page-level candidate presentation. This implementation does not call `analyze_pdf_v5`, replay Canonical validators, inspect deep Canonical cell membership, infer coordinates, or implement a B-specific parser.

The Adapter retains the exact SDK serializer output and original limitations. It accepts AVAILABLE and LIMITED_EVIDENCE tables with source identity and matching page inventory. It maps readable AVAILABLE, unmerged cells using the existing complete-header business mapping. Missing sources, contradictory page membership and unmappable cells remain explicit errors. Public table/cell IDs and grid indices become application record references; PDF location stays PAGE_ONLY, without invented cell rectangles.

Cell text readability and table authority are separate. Each mapped source retains `sdk_table.table_availability` and limitations; LIMITED_EVIDENCE is never promoted. The existing source/readiness/memory gates now consult that table state, while the existing unconfirmed name-proposal path remains usable for readable labels. This required five small production-file changes:

- `document_sdk_adapter.py`: public state/source mapping only.
- `fee_policy.py`: shared evidence predicate; no change to normalization, condition keys, tenant scope, conflict/revocation rules or price exclusion from memory.
- `extraction.py`: source/context readiness respects the same evidence predicate.
- `ingestion.py`: limited but readable labels can request an unconfirmed, price-free name proposal.
- `review_state.py`: candidates remain pending original review; manual current-source confirmation remains separate from future reuse.

No UI buttons/pages, business taxonomy, price ownership, completeness rules, migrations or formal data were changed. Four test files were updated/added. Older PDF tests expecting candidate-only learning were changed to assert current correction succeeds without creating memory; AVAILABLE-file learning tests remain intact.

## Actual commands and results

Host orchestration (exact Docker argv, exits and logs are in `verified/commands.json`):

```sh
TMPDIR=/Volumes/SANZO_TEST/private/tmp python3 -B /Volumes/SANZO_TEST/private/tmp/aiDemo-sdk18-stable-20261012/verified/RUN_ISOLATED.py
```

The runner used a unique internal Docker network, an independent `postgres:16` database with no published ports, read-only project mount and external evidence/temp/database storage. It installed the wheel into a newly created Python 3.12 venv:

```sh
/opt/sdk18-stable-venv/bin/python -B -m pip --isolated install --no-cache-dir --no-index --no-deps --force-reinstall /workspace/document_processing_sdk-1.8.0-py3-none-any.whl
/opt/sdk18-stable-venv/bin/python -B -m pip check
/opt/sdk18-stable-venv/bin/python -B /evidence/TEST_RUNNER.py --tests
git diff --check
```

All commands above returned 0. Final unittest run: **116 PASS, 0 FAIL, 0 ERROR, 0 SKIP**. Modules: `test_demo_sdk18_compatibility`, `test_demo_real_adapter`, `test_demo_real_sdk_pipeline`, `test_demo_fee_policy`, `test_demo_quote_confirmations`, `test_demo_comparison_projection`, `test_demo_ingestion_pipeline`. SDK reading uses the actual installed wheel; only semantic name classification is explicitly offline MECHANISM_TEST, with human test choices reused from existing fee-policy tests. No paid/external AI calls were made.

| Check | Result and evidence |
|---|---|
| Real A/B/current-third SDK execution | PASS; exact serializers, installed file validation, `verified/tests.log`, `A_SDK_OUTPUTS.json`, `B_PUBLIC_OUTPUT.json`, `third_SDK_OUTPUTS.json` |
| B candidate preservation | PASS; actual 6×6/36-cell table yields 5 business candidates, with LIMITED_EVIDENCE unchanged |
| A memory + B provisional alignment | PASS with offline semantic classifier; 5 comparison rows with 2 members each, unconfirmed B canonical suggestions; no automatic B adoption |
| Original source and human correction | PASS; actual PDF preview, current name correction and source-confirmation event; immutable source/page-only status and SDK limitations preserved |
| Candidate learning/reuse prevention | PASS; no new B memory slots after name/source confirmation; another B upload does not reuse memory |
| Normal AVAILABLE learning/reuse | PASS; current third workbook reuses confirmed A names; target amount comes from its own source, 9500, while old 8000 snapshot stays identical |
| Excel output | PASS; comparison main sheet checked cell-for-cell against projection; every detail amount/currency matches the current quote; third-file export is also covered by existing real pipeline tests |
| Missing/conflicting sources, merged/ambiguous/unavailable cells | PASS; explicit unresolved results, no fabricated fields |
| Formal services/data | Services' IDs, start timestamps and restart counts are identical before/after; formal DB was not used for testing |

Earlier attempts are preserved separately: a test-classifier vocabulary field mistake and an Excel trailing-empty-cell assertion mistake were corrected in test code. Those runs remain FAIL in their original logs; they are not relabelled PASS. Final checks require padded cells to be empty and compare every actual business cell.

## Remaining limits and review stop

- B's `BORDERLESS_TABLE_INTENT_AMBIGUOUS` and other SDK limitations remain visible. Its combined `サービス範囲・付帯条件` column has no existing separate business-field binding; it is retained in raw SDK output/original preview, not split into invented `charge_scope`/`extra_terms`. Those fields remain unknown and document review remains unresolved.
- Stable v4 has no dedicated cell-level source/bbox projection for precise row highlighting. PAGE_ONLY candidate review works; precise highlighting would require an approved stable interface, not reading private Canonical internals or adopting experimental v5.
- Human confirmation of this quotation does not make a LIMITED_EVIDENCE candidate eligible for future automatic learning. B's automatic reusable learning remains **BLOCKED by evidence state**; no new qualification rule was invented to lift it.
- Live AI semantic accuracy and actual browser operation are **NOT_RUN**. The offline 5-row alignment verifies app routing, offered-ID validation and projection, not model accuracy. Existing records/services were not updated or reprocessed; the current running page still uses its previously deployed code.
- This is sufficient for code review of candidate compatibility. It is not a claim that B's structure/conditions are fully resolved or that production deployment is ready. Stop at ChatGPT review; any deployment, restart or existing-data reprocessing requires subsequent authorization.

Evidence root: `/Volumes/SANZO_TEST/private/tmp/aiDemo-sdk18-stable-20261012`. Review Bundle contains final code/diff, public-contract evidence, all attempt outcomes, final commands/logs/output proofs and hashes. It excludes original input files, wheel, database files, environments and credentials.
