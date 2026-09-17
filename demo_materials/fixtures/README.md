# Demo raw-reading fixtures

These three JSON files simulate only the application reading contract for the
three user-provided, fictional quotation samples. They are not real SDK output
and contain no fee IDs, confirmed customer names, AI answers or memory results.
The displayed provenance is **デモ用解析データ／SDK未接続**.

MockSDKReader reads manifest.json at construction. It does not open or process
the third quotation at startup. When the user uploads a file, the reader checks
the actual uploaded bytes against the declared SHA256 and the manifest; it then
checks the selected JSON's exact SHA256 before binding the current document ID.
File names never choose the input. Changed or unknown bytes are rejected.
The reviewed manifest and fixture files are trusted application assets, not a
cryptographic signing system.

Each evidence_unit is this application's internal F1 protocol. Excel blocks are
complete original cells; numeric raw_text is the stored numeric cell value, with
its original display format retained separately. Shared company A3 and document
transport/service scope A9 explicitly list the five applicable row IDs. The raw
full A9 value is retained; no normalized SEA or N/A is fabricated.

PDF fields were checked against the single original page and its native text
layer. Full labels, number tokens, currencies, units and table-row attribution
are retained. The printed fullwidth slash separates the two complete service/
extra-term fields; their complete combined original is retained in metadata.
Only page-level preview is available. No coordinates or field highlight are
invented. PDF has no explicit document-wide transport-mode field, so that
condition remains missing and must not enable confirmed-learning reuse.

fixture_integrity=VERIFIED means exact fixture bytes matched the reviewed
manifest. text_verification=VERIFIED within MOCK_SDK means the AI can be checked
against those fixture fields, not that a real SDK verified a source document.
The original uploaded PDF/Excel remains the preview source. Real SDK execution
and W0 stay unchanged.

Preparation of the third raw fixture is explicitly authorized for this phase.
No fixture preparation/test creates a business case, invokes AI, writes learning
records or enters the third file into the business pipeline.

Validation:
    PYTHONPATH=backend .venv/bin/python -B -m unittest discover -s tests -p test_demo_mock_reader.py -v

This checks fixture identity, raw-cell and PDF-row correspondence, current upload
binding, rejection of modified inputs, path/symlink safety and no SDK import.
It does not demonstrate the two business rounds or actual OpenAI calls.
