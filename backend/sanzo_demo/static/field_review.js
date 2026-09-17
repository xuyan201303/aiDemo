"use strict";
// A human assigns roles to complete cells returned by the REAL reader.
// No fee, price, currency, typed source value, or reusable master is accepted.
async function reviewDocumentFieldSources(document) {
  if (state.status?.reader_mode !== "REAL_SDK") return {};
  const evidence = await api("/api/documents/" + encodeURIComponent(document.document_id) + "/field-sources");
  if (!evidence.candidates.length || !evidence.missing_roles.length) return {};
  const dialog = $("field-review-dialog"), host = $("field-review-fields");
  host.replaceChildren();
  $("field-review-filename").textContent = document.original_filename;
  const controls = new Map();
  for (const role of evidence.missing_roles) {
    const label = node("label", "", role === "company" ? "この資料の見積会社" : "この資料に適用する輸送範囲");
    const select = node("select", ""); select.id = "source-role-" + role;
    const empty = node("option", "", "未確認のままにする"); empty.value = ""; select.append(empty);
    for (const candidate of evidence.candidates) {
      const locator = candidate.source_ref.locator;
      const option = node("option", "", candidate.raw_text + "（" + locator.sheet_name + "!" + locator.A1_range + "）");
      option.value = candidate.source_id; select.append(option);
    }
    label.append(select); host.append(label); controls.set(role, select);
    const preview = button("原文を見る →", () => {
      const candidate = evidence.candidates.find(c => c.source_id === select.value);
      openPreview(document.document_id, candidate ? { source_ref: candidate.source_ref,
        observation: { document_id: document.document_id } } : null);
    });
    host.append(preview);
  }
  return new Promise((resolve, reject) => {
    let saved = false;
    const form = $("field-review-form");
    const onSubmit = event => {
      event.preventDefault();
      const bindings = Object.fromEntries([...controls].filter(([, input]) => input.value).map(([name, input]) => [name, input.value]));
      if (Object.keys(bindings).length !== new Set(Object.values(bindings)).size) {
        formError("field-review-error", "会社と輸送範囲には、それぞれ対応する原文を選んでください。"); return;
      }
      saved = true; dialog.close(); resolve(bindings);
    };
    const onClose = () => {
      form.removeEventListener("submit", onSubmit); dialog.removeEventListener("close", onClose);
      if (!saved) reject(new Error("原文の確認を中止しました。アップロード済みの資料は保持しています。"));
    };
    formError("field-review-error", "");
    form.addEventListener("submit", onSubmit); dialog.addEventListener("close", onClose); dialog.showModal();
  });
}
