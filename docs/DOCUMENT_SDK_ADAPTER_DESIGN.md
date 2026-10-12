> 2026-10-12 SDK 构件更新：当前默认包为 `/Users/kyoen/Projects/aiDemo/document_processing_sdk-1.8.0-py3-none-any.whl`，元数据版本仍为 1.8.0，SHA256 为 `781161e4af3b0b45a1ca8a7745e774e1a2362eb01bcc30956e51619514fcc4e0`。启动、Adapter、Docker 构建和 probe 引用已同步；本次未重建或重启服务，新包运行尚未验证。下列历史验收仍对应当时的旧构件。

# Fixed wheel 1.8.0: public output contract and minimal DocumentSdkAdapter design

案件 No.202608310011。此轮是固定wheel的真实SDK样例核对及适配器设计，不是网页Real SDK接入。没有改变现有OpenAI、F1、memory、DB、Mock fixture或UI；OpenAI调用0。SDK仓库禁止访问，本次未访问。

## Fixed environment

- Wheel: `/Users/kyoen/Projects/sdk/document_processing_sdk-1.8.0-py3-none-any.whl`
- Required and observed SHA256: `d791e0602ef138f50a065cd4cc5d3dca801226822b74328230f2faa2c0c75666`
- Distribution `document-processing-sdk==1.8.0`, import `document_sdk`; wheel metadata requires Python `>=3.12,<3.13`.
- Python 3.12.13 downloaded into this project's ignored `.demo-runtime/sdk-wheel-20260913T130938Z/python`; separate `.venv` installed the fixed wheel with `[pdf,excel]`. Existing `.venv` and services unchanged.
- Exact installed dependencies: `tools/sdk_probe_requirements.lock`; install, freeze, check and module-file integrity evidence are in the Review Bundle. All 265 installed `document_sdk` files match wheel members.
- Probe: `tools/probe_document_sdk.py`, invoked with isolated Python `-I -B`, a minimal credential-free child environment, and Python audit hooks blocking network, credential files, forbidden SDK repository and subprocess creation. No blocked attempts were observed. These are Python audit evidence plus local API choice, not a claim of kernel-level tracing.

## Actual public API results

All imports in the probe use `document_sdk.excel` or `document_sdk.pdf` exports. `function.__module__` in evidence records the implementation module reported by Python, not a private API called by the application.

| File | Raw cell/text and value | Coordinates and structure | Source and limitations |
|---|---|---|---|
| 01_A社_初回見積.xlsx | `read_sheet`: 64 nonempty cells. B16=`配送手数料`, E16=`JPY`, F16 numeric `8000`. `inspect_workbook_semantic_details`: 200 physical-cell records including styled/empty cells; F16 `raw_cached_value="8000"`, no formula. | Sheet `見積明細`, nonempty range A1:H23; exact A1 cell references, merged ranges, data types, number formats. `detect_table_regions` returns one A5:H16 candidate, header candidates 5/6/7 and data start 8. The explicit quote header values are actually in row11; candidate includes surrounding metadata. | Each ExcelCell has ExcelSourceReference(file_id,sheet_name,cell_range), workbook has SHA256 fingerprint. No formal Excel Table object in the semantic details. Table candidate is not a confirmed quotation schema. |
| 02_B社_初回見積.pdf | `extract_native_text`: one full-page text block. `analyze_pdf(ADVANCED_LOCAL)`: complete table cell texts, e.g. last row `DELIVERY CHARGE`, `JPY`, `9,000.00`. | One landscape page. Advanced result has one AVAILABLE table, 6 rows × 6 columns (one header + five lines), explicit zero-based row/column indices and spans. Native-only result has no tables. | Native source gives file_id and one-based page with bounding_box=null. Advanced cells have table_id/page/row/column binding but no public cell bbox. Document availability is PARTIAL; exact limitations below. No invented coordinate highlight. |
| 03_A社_次回見積.xlsx | Same structural APIs: 64 nonempty, 200 physical cells. B16=`配送手数料`, E16=`JPY`, F16 numeric `9500`; physical `raw_cached_value="9500"`. Other prices are freshly read too. | Same sheet/range and table-candidate limitation as first Excel. Coordinate F16 is a finding, never an adapter constant or lookup answer. | New file SHA and cell source. No learned fee name or business category in SDK output. |

Both Excel examples have zero formula cells. `read_sheet` distinguishes formula text, cache availability, data_type and number_format; it does not calculate formulas. `semantic_source_details` means source-level OOXML structure, not fee classification or OpenAI inference. A formatting pattern such as `#,##0.00"   "` is retained, but is not a verified currency designation.

The PDF advanced JSON preserves:

- `status="COMPLETED"`, `availability="PARTIAL"`.
- `limitations=["FAMILY_DEFERRED","OCR_NOT_EXECUTED","READING_ORDER_UNRESOLVED","ambiguous_hierarchy_parent","role_requires_review"]`.
- `family=null`, `family_status="REVIEW_REQUIRED"`, `family_auto_assignment=false`.
- Table `availability="AVAILABLE"`, all 36 cells AVAILABLE, table limitations empty.
- `document_table_count=0`, `transition_count=0`; these do not erase the one page-local table.
- `provenance.source_parser="pdfplumber"`; bounded internal limits profile is recorded by the SDK. No OCR/Hybrid/cloud route was selected.

Two fresh processes per file produced 11 pairs of byte-identical raw public results (22 successful API calls total). SDK serializers were used directly; no volatile keys or inconvenient limitations were removed. This verifies repeatability of these three samples in this exact wheel/dependency environment only.

## Amount and currency semantics

SDK returns Excel numeric values and PDF strings, alongside literal `通貨` / `単価` header text. It does not return a confirmed business `amount`, ISO-validated `currency`, `raw_label` role, unified fee/category, company match or memory decision. The table shows source values, not business interpretation by this probe.

For example, Excel F16=9500 is an SDK numeric cell fact. Calling it the quotation unit price requires an application binding to the observed `見積単価` column. PDF `9,000.00` is a cell's text; decimal/grouping conversion belongs to the existing deterministic application validator after a reliable column/record binding. SDK-native values must not be re-extracted or overwritten by AI.

## Minimal adapter design (not activated or implemented this round)

`DocumentSdkAdapter` is an application class, not an SDK API. Its eventual public method remains the existing `read(path, document) -> ReadResult`. No separate persistence, business master, model client or fee lookup belongs in it.

1. Revalidate uploaded SHA and byte limit; verify installed wheel version/hash. Import only installed public APIs. Bind caller document_id to SDK fingerprint/source_sha256; never assume logical SDK file_id always equals a fingerprint.
2. Excel: `inspect_workbook` → bounded `read_sheet(...,formula_view="both")`; retain semantic physical-cell details when lexical numeric source is needed. Treat `detect_table_regions` only as a candidate. Find a unique full header row by its observed label set and typed shape, never by fixed row11 or file hash. Ambiguous/multiple headers stay unresolved.
3. PDF: explicitly `analyze_pdf(...,mode=ADVANCED_LOCAL, limits=...)`. Preserve entire availability/limitations. Use only AVAILABLE table/cell evidence with explicit unique row/column membership. Native page text can support page preview/context, never arbitrary substring-based price or field ownership.
4. A small deterministic header-role resolver supplies application field names from complete observed headers. For example `費目表記`/`DESCRIPTION / 費目表記` may bind raw_label, `見積単価`/`単価` may bind raw_value, `通貨` currency, `課金単位`/`単位` billing_unit. This is an explicitly versioned application rule, not a claimed SDK semantic result. It must inspect actual headers/rows, not use expected sample prices, names, file hashes or addresses as answers.
5. Complete original cell values become blocks. For Excel numeric values, preserve both typed value and physical lexical source when available; do not use floating-point formatting as proof of source text. For PDF, use the full table cell text. Null, unavailable, merged/ambiguous and formula/cache cases retain explicit limitations.
6. Map to current application F1 evidence_unit only after field and record identity is established. `kind=EXCEL_CELL` or application `SDK_FIELD` describes evidence granularity, not a new SDK public type. Do not allow AI to create this metadata.
7. Unknown company/transport/applicability fields stay unknown. Do not copy historical Mock fields, split an arbitrary page string into a verified company, infer N/A, or treat unknown as a wildcard. The PDF's combined `サービス範囲・付帯条件` remains complete raw evidence; splitting it into charge_scope/extra_terms requires separately reviewed deterministic provenance-preserving rules and cannot be certified by substring matching.
8. Return valid bounded ReadResult blocks plus explicit unresolved findings according to the application's agreed result channel. **Design gap:** current ReadResult has no dedicated reader-issues member; before activation choose a minimal issue channel/compatible extension and its tests. Do not silently drop unbound columns/records or feed them into AI extraction. This round leaves the contract and runtime unchanged.

| SDK fact | Planned existing application field | Boundary |
|---|---|---|
| Workbook fingerprint / PDF source_sha256 | document_sha256; source_ref.document_sha256 | Must equal uploaded bytes |
| Caller document ID | source_ref.document_id | Caller-owned, not SDK-invented |
| Excel source sheet/cell | source_ref.kind=EXCEL_RANGE, VERIFIED locator | Single full scalar cell; preserve merge ambiguity |
| PDF page + table ID + row/column/span | source_ref.kind=PDF_PAGE, PAGE_ONLY locator; retained table identity | No bbox in public advanced cell model; do not fabricate coordinates |
| Complete field/cell text or exact lexical numeric source | raw_text, complete evidence_unit | Only after verified column-role binding |
| Excel physical row / PDF table row | scoped record_id | Unique per sheet/table and document; cross-row values cannot substitute |
| Version, wheel SHA, public API/mode | sdk_identity | REAL_SDK only for actual wheel execution, no Mock fallback |
| Raw SDK limitations and unbound business context | extraction issues / review state | No silent promotion to source_ready or learning eligibility |

Company binding and applicability are material remaining adapter work. The three raw SDK outputs are now available, but this does **not** prove that existing strict memory eligibility can be satisfied for all rows. A unified fee such as `配送費` is neither read nor preset by this adapter design.

## Errors and lifetime

The used synchronous APIs return SDK-owned Pydantic models/tuples/JSON, not live workbook/PDF objects. Bounded inspection of the used wheel functions shows try/finally closing Excel workbooks/streams and native PDF page/text-page objects; the PDF document is inside its open_pdf context. Do not invent a public close method on returned result models. Separate probe processes also terminate after each sample.

Resource-limit and unsupported-source exceptions must become controlled adapter errors or extraction-unresolved states with preserved public diagnostics; no automatic retry, cloud routing or Mock fallback. Current sample runs did not fail; broad malformed-file, leak/performance and SDK regression suites were not run. No SDK test files/repository were accessed.

## Re-run only the raw probe

Installed interpreter in this run:

```sh
.demo-runtime/sdk-wheel-20260913T130938Z/.venv/bin/python -I -B tools/probe_document_sdk.py --sample 02_B社_初回見積.pdf --output /private/tmp/sanzo-sdk-pdf-probe-new
```

The output directory must be new. It contains unchanged SDK public serializers' output plus separate manifest/call diagnostics. The fixed wheel/source hashes and Python requirement are checked before import. No webpage launch, AI credentials or database connection is needed.

Current webpage remains on its previous Mock/memory-first processing chain. No real SDK adapter is wired into IntegrationRuntime, no new real SDK/AI business rounds are claimed, and historical W0/overall PARTIAL evidence is unchanged. The present task's fixed-wheel public-API probe is complete; PDF's own PARTIAL remains PARTIAL.
