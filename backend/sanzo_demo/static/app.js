"use strict";

const state = { cases: [], snapshot: null, selectedCase: "", status: null, manualDocument: null, editItem: null, editRow: null, requestKeys: new Map(), processRequests: new Map(), taskPollTimer: null, pollDeadline: null, snapshotSequence: 0, busy: false };
const $ = id => document.getElementById(id);
const conditionLabels = { transport_mode: "輸送", equipment: "機材", billing_unit: "単位", charge_scope: "範囲", extra_terms: "追加条件" };
const statusLabels = { ACTIVE: "再利用可能", MEMORY_APPLIED: "前回確認を反映", REVIEW_REQUIRED: "確認が必要", AI_REQUIRED: "費目確認待ち", AI_SUGGESTED: "費目候補・未確認", READING_REQUIRED: "読み取りが必要", HUMAN_CONFIRMED: "今回の確認を保存", MANUAL_CONFIRMED: "人工入力", CONFIRMED: "今回の確認を保存", CONFLICTED: "対応が競合", REVOKED: "対応を撤回" };

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}
function button(text, action, className = "text-button") {
  const element = node("button", className, text);
  element.type = "button";
  element.addEventListener("click", action);
  return element;
}
function asObject(value) {
  if (typeof value === "string") { try { return JSON.parse(value); } catch { return {}; } }
  return value || {};
}
function observation(item) { return asObject(item.observation); }
function context(item) { return asObject(observation(item).context); }
function itemStatus(item) {
  const initial = asObject(item.initial_classification);
  const status = item.saved_status || initial.kind || initial.status || (item.current_fee_id ? "HUMAN_CONFIRMED" : "AI_REQUIRED");
  return !item.current_fee_id && item.ai_suggested_name && ["AI_REQUIRED", "AI_SUGGESTED", "AI_IDENTIFIED"].includes(status) ? "AI_SUGGESTED" : status;
}
function statusText(value) { return statusLabels[value] || String(value || "確認が必要"); }
function errorText(error) { return error && error.message ? error.message : "処理に失敗しました。"; }
function connectionReason(connection) {
  const reasons = {
    SDK_PATH_AND_CONTRACT_MISSING: "読み取り用 SDK の接続条件が未確認です。",
    AUTHORIZATION_MODEL_AND_CREDENTIAL_CONFIGURATION_MISSING: "資料送信の許可と AI 接続設定が未確認です。"
  };
  return reasons[connection.reason] || (connection.reason ? taskErrorText(connection.reason) : (connection.ready ? "" : "接続条件を確認中"));
}
function showNotice(message, error = false) {
  const element = $("notice");
  element.textContent = message;
  element.classList.toggle("error", error);
  element.hidden = !message;
}
function formError(id, message) { $(id).textContent = message; $(id).hidden = !message; }
function requestId() {
  return globalThis.crypto && crypto.randomUUID ? crypto.randomUUID() : "demo-" + Date.now() + "-" + Math.random().toString(16).slice(2);
}
async function api(path, options = {}) {
  const headers = { ...options.headers };
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...options, headers });
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = data && data.detail !== undefined ? data.detail : data;
    const message = typeof detail === "string" ? detail : JSON.stringify(detail);
    const error = new Error(message || "HTTP " + response.status);
    error.status = response.status;
    throw error;
  }
  return data;
}
function setBusy(busy) {
  state.busy = busy;
  $("upload-button").disabled = busy || state.selecting || !state.selectedCase || !pendingFiles().some(e => !e.unsupported) || localReadBlockedByAIPause();
  $("files").disabled = busy || state.selecting || !state.selectedCase;
  $("manual-submit").disabled = busy;
  $("save-correction").disabled = busy;
  $("edit-fee-name").disabled = busy || Boolean(state.pendingConfirmation);
  $("replace-mapping").disabled = busy || Boolean(state.pendingConfirmation);
  $("reclassify-members").disabled = busy || Boolean(state.pendingConfirmation);
  $("refresh-item").disabled = busy;
  $("revoke-button").disabled = busy || Boolean(state.pendingConfirmation) || !state.editItem || !hasMemory(state.editItem);
  $("revoke-member").disabled = busy || Boolean(state.pendingConfirmation);
  $("revoke-reason").disabled = busy || Boolean(state.pendingConfirmation);
  document.querySelectorAll(".row-confirm-button").forEach(element => { element.disabled = busy; });
  $("new-case-button").disabled = busy;
  $("case-select").disabled = busy;
  document.querySelectorAll(".process-button").forEach(element => { element.disabled = busy || state.selecting || localReadBlockedByAIPause(); });
}
function hasMemory(item) {
  return Boolean(item && (item.current_memory_version || item.memory_resolution?.kind === "LEGACY_PROJECTION"));
}
function renderStatus() {
  const panel = $("connection-panel");
  panel.replaceChildren();
  const status = state.status || {};
  const sdk = status.sdk || {};
  const ai = status.ai || {};
  const mock = status.reader_mode === "MOCK_SDK";
  const reader = status.reader || sdk;
  const ready = Boolean(reader.ready);
  if (mock) panel.append(node("div", "debug-banner", "Demoモード：文書解析部分はMock SDKを使用しています。AIによる費目認識、手動修正、確認結果の保存・再利用は実処理です。"));
  panel.classList.toggle("ready", ready);
  panel.append(node("div", "connection-title", ready ? "資料を追加し、「比較表を作成」を選択してください。結果の費目名を確認して保存できます。" : "資料の読み取り設定を確認してください。"));
  $("upload-mode-copy").textContent = mock ? "現在の Demo Mock は指定の三つのサンプルのみ対応しています。" : "PDF・Excelをまとめて追加できます";
  const details = node("details", "connection-details");
  details.append(node("summary", "", "詳細を見る"));
  for (const [name, connection] of [[mock ? "デモ原文 fixture" : "SDK", reader], ["OpenAI API", ai]]) {
    const line = node("div", "connection-detail");
    line.append(node("strong", "", name + (connection.ready ? " 装配済み（実行結果は資料ごと）" : " 接続待ち")));
    line.append(node("span", "", connectionReason(connection)));
    details.append(line);
  }
  panel.append(details);
  if (Array.isArray(status.missing_conditions) && status.missing_conditions.length) {
    const missing = node("ul", "connection-missing");
    status.missing_conditions.forEach(condition => missing.append(node("li", "", typeof condition === "string" ? condition : condition.message || condition.code || "接続条件を確認してください")));
    details.append(missing);
  }
  if (ai.budget) details.append(node("div", "connection-footnote",
    "累計リクエスト枠：" + ai.budget.requests_used + " / " + ai.budget.max_requests
    + "（全プロセス・再起動を通算。失敗・中断で消費した枠も戻りません）"));
  if (!ready) details.append(node("div", "connection-footnote", "接続設定の準備と実際の読み取り成功は別です。未完了の処理を成功として表示しません。"));
  if (status.debug_manual === true) details.append(node("div", "debug-banner", "内部デバッグ有効：人工入力は SDK・AI の実行結果ではありません。"));
}
const terminalTaskStates = new Set(["AVAILABLE", "REVIEW_REQUIRED", "FAILED", "BLOCKED", "INTERRUPTED"]);
const taskStatusLabels = {
  RECEIVED: "資料を受け付けました · 読み取り待ち",
  QUEUED: "読み取りを待っています",
  PENDING: "読み取りを待っています",
  RUNNING: "処理中",
  READING: "原文を読み取り中",
  SDK_READING: "原文を読み取り中",
  CLASSIFYING: "費目を識別中",
  AI_CLASSIFYING: "費目を識別中",
  SAVING: "読み取り結果を保存中",
  AVAILABLE: "読み取り完了",
  REVIEW_REQUIRED: "読み取り結果の確認が必要です",
  FAILED: "読み取りに失敗しました",
  BLOCKED: "接続条件が不足しています",
  INTERRUPTED: "処理が中断されました"
};
function taskStatusText(value) {
  if (value === "READING" && state.status?.reader_mode === "MOCK_SDK") return "デモ原文 fixture を照合中";
  return taskStatusLabels[value] || "処理状態：" + String(value);
}
function taskErrorText(value) {
  return {
    INTEGRATION_PREREQUISITES_MISSING: "接続条件を確認してから再試行してください。",
    "MOCK_SDK_UNSUPPORTED_FILE: 当前模拟模式仅支持三份指定样例": "現在のシミュレーションは指定の三つのサンプルのみ対応しています。",
    MOCK_SDK_UPLOAD_HASH_MISMATCH: "アップロード原件の SHA が一致しません。旧 fixture は使いません。",
    MOCK_SDK_FIXTURE_HASH_MISMATCH: "fixture の完全性を確認できません。処理を停止しました。",
    AI_REQUEST_BUDGET_EXHAUSTED: "承認された累計呼出し上限に達しました。再起動しても枠は増えません。",
    AI_CREDENTIAL_UNAVAILABLE: "指定されたバックエンド認証元を利用できません。",
    AI_TIMEOUT: "AI 呼出しが時間切れになりました。自動再試行はしません。",
    AI_PROCESSING_PAUSED: "原文読み取りは完了しました。未確認費目の AI 処理は停止中です。利用状況・待機時間を確認後、必要な場合のみ再開してください。",
    SDK_NOT_CONFIGURED: "SDK の接続設定が必要です。",
    SDK_PATH_AND_PUBLIC_CONTRACT_NOT_VERIFIED: "SDK の場所と読み取り契約が未確認です。",
    SAMPLES_OR_INSTRUCTION_MISSING: "実行指示または入力サンプルが見つかりません。",
    OPENAI_AUTHORIZATION_NOT_CONFIRMED: "AI への資料送信の許可が未確認です。",
    OPENAI_MODEL_NOT_CONFIGURED: "使用する AI モデルが未設定です。",
    OPENAI_CREDENTIAL_NOT_CONFIGURED: "AI の認証設定が未完了です。"
  }[value] || String(value);
}
function latestTask(documentId) {
  return (state.snapshot?.tasks || []).filter(task => task.document_id === documentId).sort((a, b) => String(b.created_at || b.updated_at || "").localeCompare(String(a.created_at || a.updated_at || "")))[0];
}
function activeTasks() { return (state.snapshot?.tasks || []).filter(task => !terminalTaskStates.has(task.status)); }
function scheduleTaskPolling() {
  clearTimeout(state.taskPollTimer);
  state.taskPollTimer = null;
  if (!state.selectedCase || !activeTasks().length) return;
  if (!state.pollDeadline) state.pollDeadline = Date.now() + 90000;
  if (Date.now() >= state.pollDeadline) {
    renderSnapshot();
    return;
  }
  state.taskPollTimer = setTimeout(pollTasks, 1000);
}
async function pollTasks() {
  state.taskPollTimer = null;
  if (state.busy) { scheduleTaskPolling(); return; }
  const caseId = state.selectedCase;
  const tasks = activeTasks();
  if (!tasks.length) return;
  const results = await Promise.allSettled(tasks.map(task => api("/api/tasks/" + encodeURIComponent(task.task_id))));
  if (caseId !== state.selectedCase || !state.snapshot) return;
  let finished = false;
  let failedFetch = false;
  for (const result of results) {
    if (result.status === "rejected") { failedFetch = true; continue; }
    const task = result.value.task || result.value;
    const index = state.snapshot.tasks.findIndex(existing => existing.task_id === task.task_id);
    if (index >= 0) state.snapshot.tasks[index] = task;
    if (terminalTaskStates.has(task.status)) {
      finished = true;
      state.processRequests.delete(task.document_id);
    }
  }
  if (failedFetch) showNotice("処理状態を取得できませんでした。処理自体の失敗とは限りません。状態の確認を続けます。", true);
  if (finished) {
    try { await loadSnapshot({ keepVisible: true }); }
    catch (error) { showNotice("結果一覧を取得できませんでした。「状態を更新」で再確認してください。", true); scheduleTaskPolling(); }
  } else {
    renderSnapshot();
    scheduleTaskPolling();
  }
}
async function refreshTaskStatus() {
  await mutation(async () => {
    state.pollDeadline = Date.now() + 90000;
    await loadSnapshot({ keepVisible: true });
    showNotice("処理の最新状態を取得しました。");
  });
}
async function processDocument(document) {
  await mutation(async () => {
    await refreshRuntimeStatus();
    const task = await submitAndWait(document);
    showNotice(aiFailure(task) ? aiFailureText(aiFailure(task)) : taskStatusText(task.status),
      ["FAILED", "BLOCKED", "INTERRUPTED"].includes(task.status));
  });
}
async function loadCases(preferred) {
  const result = await api("/api/cases");
  state.cases = Array.isArray(result) ? result : result.cases || [];
  const selected = preferred || state.selectedCase;
  const select = $("case-select");
  select.replaceChildren(node("option", "", "案件を選択してください"));
  select.options[0].value = "";
  for (const item of state.cases) {
    const option = node("option", "", item.name || item.case_id);
    option.value = item.case_id;
    select.append(option);
  }
  state.selectedCase = state.cases.some(item => item.case_id === selected) ? selected : "";
  select.value = state.selectedCase;
  await loadSnapshot();
}
async function loadSnapshot({ keepVisible = false } = {}) {
  const caseId = state.selectedCase;
  const sequence = ++state.snapshotSequence;
  clearTimeout(state.taskPollTimer);
  state.taskPollTimer = null;
  if (!keepVisible) {
    state.snapshot = null;
    renderSnapshot();
  }
  const snapshot = caseId ? await api("/api/cases/" + encodeURIComponent(caseId)) : null;
  if (sequence !== state.snapshotSequence || caseId !== state.selectedCase) return;
  state.snapshot = snapshot;
  observeAIFailures();
  renderSnapshot();
  scheduleTaskPolling();
}
function renderSnapshot() {
  const snapshot = state.snapshot;
  const documents = snapshot ? snapshot.documents || [] : [];
  const items = snapshot ? snapshot.items || [] : [];
  const caseData = snapshot ? snapshot.case || {} : {};
  $("case-meta").textContent = snapshot ? "保存済み" : "";
  $("export-button").disabled = !documents.length && !items.length;
  $("item-count").textContent = items.length + " 明細";
  $("no-documents").hidden = documents.length > 0;
  $("no-documents").textContent = state.selectedCase ? "Excel または PDF を追加してください。" : "案件を作成し、見積資料を追加してください。";
  $("documents").replaceChildren();
  documents.forEach(document => {
    const card = node("article", "document-card");
    card.dataset.documentId = document.document_id;
    const filename = document.original_filename || document.document_id;
    const isPDF = filename.toLowerCase().endsWith(".pdf");
    card.append(node("div", "file-badge" + (isPDF ? " pdf" : ""), isPDF ? "PDF" : "XLSX"));
    const body = node("div", "document-body");
    const name = node("div", "document-name", filename);
    name.title = filename;
    body.append(name);
    const task = latestTask(document.document_id);
    const jobStatus = task?.status || document.latest_job_state || document.state || document.status || "RECEIVED";
    const aiError = aiFailure(task);
    const statusLine = node("div", "document-state", aiError ? (aiError.category === "PAUSED_LOCAL" ? "AI 分類を保留（呼出しなし）" : aiError.purpose === "fee_classification" ? "AI の費目分類に失敗しました" : aiError.purpose === "quotation_extraction" ? "AI の明細抽出に失敗しました" : "AI リクエストに失敗しました") : taskStatusText(jobStatus));
    statusLine.dataset.status = jobStatus;
    statusLine.classList.toggle("task-complete", jobStatus === "AVAILABLE");
    statusLine.classList.toggle("task-error", ["FAILED", "BLOCKED", "INTERRUPTED"].includes(jobStatus));
    body.append(statusLine);

    const technical = node("details", "document-details");
    technical.append(node("summary", "", "詳細を見る"));
    const successfulCalls = task?.result_json?.ai_metadata?.calls || [];
    if (successfulCalls.length && successfulCalls.every(call => call.execution_kind === "OPENAI_API" && call.http_status === 200)) {
      const extractCount = successfulCalls.filter(call => call.purpose === "quotation_extraction").length;
      const classifyCount = successfulCalls.filter(call => call.purpose === "fee_classification").length;
      technical.append(node("div", "task-detail", "AI処理済 · 抽出 " + extractCount + " 回／費目分類 " + classifyCount + " 回"));
    } else if (task?.result_json?.ai_metadata?.fact_materialization && ["AVAILABLE", "REVIEW_REQUIRED"].includes(jobStatus) &&
        Object.keys(task?.result_json?.ai_metadata?.memory_lookup_results || {}).length > 0 &&
        Object.values(task.result_json.ai_metadata.memory_lookup_results).every(item => item.decision === "MEMORY_APPLIED")) {
      technical.append(node("div", "task-detail", "確認済みの対応を適用 · AI呼出しなし"));
    } else technical.append(node("div", "task-detail", jobStatus === "FAILED" ? "AI処理は完了していません（下記の実際のエラーを確認）" : "AI処理済の記録はまだありません"));
    if (task?.events?.some(event => event.metadata?.read_evidence?.origin === "MOCK_SDK")) {
      technical.append(node("div", "task-detail", "fixture 完全性照合済／実 SDK 読取は未実行"));
    }
    const errorCode = task?.error_code || document.error_code || document.blocked_reason;
    if (errorCode && ["FAILED", "BLOCKED", "INTERRUPTED"].includes(jobStatus)) {
      body.append(node("div", "task-detail", aiError ? aiFailureText(aiError, false) : "処理を完了できませんでした。詳細を確認してください。"));
      technical.append(node("div", "task-detail", aiError ? aiFailureText(aiError) : taskErrorText(errorCode)));
    }
    for (const issue of task?.issues || []) {
      const text = typeof issue === "string" ? issue : [issue.code, issue.reason, issue.limitation, issue.field, issue.resolution, issue.message].filter(Boolean).join("：");
      if (text) technical.append(node("div", "task-detail", text));
    }
    body.append(technical);
    const actions = node("div", "document-actions");
    actions.append(button("原文をプレビュー ↗", () => openPreview(document.document_id)));
    const running = task && !terminalTaskStates.has(task.status);
    if (running) {
      actions.append(button("状態を更新", () => refreshTaskStatus()));
    } else if (!["AVAILABLE", "REVIEW_REQUIRED"].includes(jobStatus)) {
      const retry = ["FAILED", "BLOCKED", "INTERRUPTED"].includes(jobStatus);
      const process = button(retry ? (aiError ? "AI 処理を手動で再試行" : "処理を再試行") : "解析を開始", () => processDocument(document), "text-button process-button");
      process.dataset.documentId = document.document_id;
      process.disabled = state.busy || localReadBlockedByAIPause();
      actions.append(process);
    }
    if (state.status?.debug_manual === true) actions.append(button("＋ 人工入力（内部デバッグ）", () => openManual(document), "text-button debug-action"));
    body.append(actions);
    if (running && state.pollDeadline && Date.now() >= state.pollDeadline) body.append(node("div", "task-detail", "処理は継続中です。「状態を更新」で再確認できます。"));
    card.append(body);
    $("documents").append(card);
  });
  renderComparison(items);
  renderPendingFiles();
  setBusy(state.busy);
}
function renderComparison(items) {
  const host = $("comparison-table");
  host.replaceChildren();
  $("empty-comparison").hidden = Boolean(items.length);
  if (!items.length) return;
  const projection = state.snapshot?.comparison;
  if (!projection || projection.version !== 1) {
    host.append(node("p", "subtle", "比較情報を取得できません。画面を更新してください。"));
    return;
  }
  const companies = projection.companies;
  const byId = new Map(items.map(item => [item.item_id, item]));

  const table = node("table", "compare-table");
  table.setAttribute("aria-label", "費目と適用条件別の単価比較");
  table.style.minWidth = Math.max(600, 235 + companies.length * 245) + "px";
  const head = node("thead");
  const heading = node("tr");
  heading.append(node("th", "", "統一費目"));
  companies.forEach(company => heading.append(node("th", "", company)));
  head.append(heading);
  table.append(head);
  const body = node("tbody");
  for (const group of projection.rows) {
    const row = node("tr");
    row.dataset.rowId = group.row_id;
    const first = node("td", "unified-fee-cell");
    first.append(node("p", "fee-name", group.display_name));
    first.append(node("span", "row-status" + (group.confirmation_status.startsWith("✓") ? " applied" : ""), group.confirmation_status));
    first.append(button("確認・修正", () => { if (!state.busy) openRowEdit(group); }, "text-button row-confirm-button"));
    row.append(first);
    for (const company of companies) {
      const cell = node("td", "price-cell");
      const quotes = group.cells_by_forwarder[company] || [];
      if (!quotes.length) { const empty = node("div", "no-quote", "—"); empty.setAttribute("aria-label", "見積なし"); empty.title = "見積なし"; cell.append(empty); }
      for (const quote of quotes) { const item = byId.get(quote.item_id); if (item) cell.append(renderQuote(item, quote)); }
      row.append(cell);
    }
    body.append(row);
  }
  table.append(body);
  host.append(table);
}
function renderQuote(item, projected) {
  const card = node("article", "quote-card compact-quote");
  card.dataset.itemId = item.item_id;
  const price = node("span", "quote-price", formatPrice(projected.price));
  price.append(node("span", "quote-currency", projected.currency));
  card.append(price);
  return card;
}
function formatPrice(value) {
  const raw = String(value ?? "—");
  const match = /^(-?)(\d+)(\.\d+)?$/.exec(raw);
  return match ? match[1] + match[2].replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (match[3] || "") : raw;
}
function sourceDetails(item) {
  const ref = asObject(item?.source_ref);
  const locator = asObject(ref.locator);
  const obs = observation(item || {});
  const documentId = item?.document_id || obs.document_id || ref.document_id;
  const document = (state.snapshot?.documents || []).find(entry => entry.document_id === documentId);
  const filename = document?.original_filename || ref.original_filename || "原文資料";
  const params = new URLSearchParams();
  let position = "位置未取得";
  const usable = ["VERIFIED", "PAGE_ONLY"].includes(ref.locator_status);
  if (usable && ref.kind === "PDF_PAGE" && Number.isInteger(locator.page_number) && locator.page_number > 0) {
    params.set("page", String(locator.page_number));
    position = locator.page_number + " ページ" + (ref.locator_status === "PAGE_ONLY" ? "（ページ内の範囲は未確定）" : "");
  } else if (usable && ref.kind === "EXCEL_RANGE" && typeof locator.sheet_name === "string" && locator.sheet_name) {
    params.set("sheet", locator.sheet_name);
    if (ref.locator_status === "VERIFIED" && typeof locator.A1_range === "string" && locator.A1_range) {
      params.set("cell", locator.A1_range);
      position = locator.sheet_name + "!" + locator.A1_range;
    } else position = locator.sheet_name + "（セル位置は未取得）";
  }
  return { documentId, filename, position, params, located: params.size > 0 };
}
function openPreview(documentId, item = null) {
  if (!documentId) { showNotice("この明細には原文資料が登録されていません。", true); return; }
  const document = (state.snapshot?.documents || []).find(entry => entry.document_id === documentId);
  const source = item ? sourceDetails(item) : null;
  $("preview-filename").textContent = source ? source.filename + " · " + source.position : document?.original_filename || documentId;
  $("preview-note").textContent = source?.located
    ? "今回の資料の取得済み位置を表示します。PDF はページ画像、Excel はセル内容の簡易表示です。"
    : "アップロードした原文を表示します。明細の位置は未取得のため、資料全体を確認してください。PDF はページ画像、Excel はセル内容の簡易表示です。";
  if (state.status?.reader_mode === "MOCK_SDK") {
    $("preview-note").textContent += " デモ用解析データ／SDK未接続。原件の表示と fixture の読取結果は別です。";
  }
  const query = source?.params.toString();
  $("preview-frame").src = "/api/documents/" + encodeURIComponent(documentId) + "/preview" + (query ? "?" + query : "");
  if (!$("preview-dialog").open) $("preview-dialog").showModal();
}
function openManual(document) {
  if (state.status?.debug_manual !== true) return;
  state.manualDocument = document;
  $("manual-form").reset();
  $("manual-document-name").textContent = document.original_filename;
  formError("manual-error", "");
  $("manual-dialog").showModal();
}
// The server normalizes fee names using Python NFC + whitespace splitting.
// No case folding, translation, or compatibility-character folding is applied.
function normalizedFeeName(value) {
  return value.normalize("NFC").replace(/[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/g, " ").replace(/^ | $/g, "");
}
// A background case refresh must not replace the names/versions this dialog showed.
function availableFees() { return state.editFeeCatalog || state.snapshot?.fees || []; }
function feeReference(fee) {
  if (!fee?.fee_id || !Number.isInteger(fee.name_version) || fee.name_version < 1) return null;
  return { fee_id: fee.fee_id, name_version: fee.name_version, display_name: feeName(fee) };
}
function sameFeeReference(left, right) {
  return Boolean(left && right && left.fee_id === right.fee_id && left.name_version === right.name_version
    && normalizedFeeName(left.display_name) === normalizedFeeName(right.display_name));
}
function feeName(fee) { return fee.display_name || fee.name || ""; }
function renameAvailable(row = state.editRow) {
  return row?.rename_allowed === true && Boolean(row.rename_fee_id) && Number.isInteger(row.rename_name_version) && row.rename_name_version > 0;
}
function confirmationIntent(name, selected = null) {
  const row = state.editRow;
  const confirmedIds = new Set(row.member_items.map(item => item.current_fee_id).filter(Boolean));
  if (state.reclassifyMembers || (selected && confirmedIds.size && [...confirmedIds].some(id => id !== selected.fee_id))) return "RECLASSIFY_MEMBERS";
  if (renameAvailable(row) && name !== state.editInitialFeeName && selected?.fee_id !== row.rename_fee_id) return "RENAME_FEE";
  return "CONFIRM_MEMBERS";
}
function renderConfirmationIntent() {
  const name = normalizedFeeName($("edit-fee-name").value);
  const selected = availableFees().find(fee => fee.fee_id === state.selectedFeeId && normalizedFeeName(feeName(fee)) === name);
  const intent = confirmationIntent(name, selected);
  const help = $("edit-intent-help");
  help.textContent = intent === "RENAME_FEE" ? "この費目の統一名称を変更します。学習済みの表記にも次回から反映されます。"
    : intent === "RECLASSIFY_MEMBERS" ? "この明細の分類だけを変更します。" : "";
  if (state.feeReviewRequired) {
    const latest = state.feeReviewRequired.latest;
    help.textContent = latest ? "最新の統一費目名：" + latest.display_name + "。候補を選び直して確認してください。"
      : "選択した費目は現在利用できません。候補を選び直して確認してください。";
  }
  help.hidden = !help.textContent;
  $("reclassify-members").hidden = !renameAvailable();
  $("reclassify-members").textContent = state.reclassifyMembers ? "統一名称の変更に戻る" : "この明細の分類を変更";
  $("reclassify-members").setAttribute("aria-pressed", String(Boolean(state.reclassifyMembers)));
}
function hideFeeCandidates() {
  $("fee-candidates").hidden = true;
  $("edit-fee-name").setAttribute("aria-expanded", "false");
}
function renderFeeCandidates() {
  const host = $("fee-candidates"); host.replaceChildren();
  if (state.busy || state.pendingConfirmation) { hideFeeCandidates(); return; }
  const query = normalizedFeeName($("edit-fee-name").value);
  let fees = availableFees().filter(fee => normalizedFeeName(feeName(fee)).includes(query));
  if (state.feeReviewRequired) {
    const latest = state.feeReviewRequired.latest;
    if (latest) fees = [latest, ...fees.filter(fee => fee.fee_id !== latest.fee_id)];
    else fees = availableFees();
  }
  for (const fee of fees) {
    const duplicate = availableFees().filter(f => normalizedFeeName(feeName(f)) === normalizedFeeName(feeName(fee))).length > 1;
    const option = button(feeName(fee) + (duplicate ? "（ID: " + fee.fee_id + "）" : ""), () => {
      state.selectedFeeId = fee.fee_id;
      state.selectedFeeReference = feeReference(fee);
      state.feeReviewRequired = null;
      $("edit-fee-name").value = feeName(fee);
      hideFeeCandidates(); renderConfirmationIntent(); formError("edit-error", ""); $("edit-fee-name").focus();
    }, "fee-candidate");
    option.setAttribute("role", "option"); option.dataset.feeId = fee.fee_id;
    if (Number.isInteger(fee.name_version)) option.dataset.nameVersion = String(fee.name_version);
    option.setAttribute("aria-selected", String(state.selectedFeeId === fee.fee_id));
    option.addEventListener("keydown", event => {
      const options = [...host.children], index = options.indexOf(option);
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault(); options[(index + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length]?.focus();
      }
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); hideFeeCandidates(); $("edit-fee-name").focus(); }
    });
    host.append(option);
  }
  host.hidden = !fees.length;
  $("edit-fee-name").setAttribute("aria-expanded", String(Boolean(fees.length)));
}
function correctionDraft() {
  const matches = availableFees().filter(fee => normalizedFeeName(feeName(fee)) === normalizedFeeName($("edit-fee-name").value));
  const seen = state.selectedFeeReference || (matches.length === 1 ? feeReference(matches[0]) : null);
  return { feeId: state.selectedFeeId || "", feeName: $("edit-fee-name").value,
    selectedFeeReference: seen, renameReference: state.editRenameReference || null,
    reviewReference: state.feeReviewRequired?.seen || null,
    reclassifyMembers: Boolean(state.reclassifyMembers),
    replaceMapping: $("replace-mapping").checked, revokeReason: $("revoke-reason").value };
}
function openRowEdit(row, draft = null, requestKeys = null) {
  if (!draft) state.editReturnFocus = document.activeElement;
  // Pin the displayed set, prices and all expected versions until explicit refresh.
  state.editRow = structuredClone(row);
  state.editFeeCatalog = structuredClone(state.snapshot?.fees || []);
  state.selectedFeeReference = null;
  state.feeReviewRequired = null;
  state.editRenameReference = null;
  state.editCaseId = state.selectedCase;
  state.editCaseRevision = row.member_items[0].case_revision ?? state.snapshot.case.case_revision;
  state.requestKeys = requestKeys || new Map(); state.pendingConfirmation = null;
  state.reclassifyMembers = false;
  $("edit-form").reset(); $("edit-details").open = false;
  formError("edit-error", ""); $("refresh-item").hidden = true;
  $("edit-unified-name").textContent = row.display_name;
  $("edit-status").textContent = row.confirmation_status;
  $("edit-member-count").textContent = "表示中の " + row.member_items.length + " 件の原文を、同じ統一費目名として確認します。";
  $("edit-dimensions").textContent = Object.entries(row.differentiating_conditions).map(([key, value]) => (conditionLabels[key] || key) + "：" + value).join(" ／ ");
  $("edit-dimensions").hidden = !$("edit-dimensions").textContent;
  const host = $("edit-members"); host.replaceChildren();
  const detailsHost = $("edit-member-details"); detailsHost.replaceChildren();
  const revokeSelect = $("revoke-member"); revokeSelect.replaceChildren();
  const companies = state.snapshot.comparison.companies;
  const members = [...row.member_items].sort((a, b) => companies.indexOf(a.forwarder) - companies.indexOf(b.forwarder));
  for (const item of members) {
    const card = node("section", "row-member"); card.dataset.memberId = item.item_id;
    const source = sourceDetails(item);
    const heading = node("div", "member-heading");
    const labels = node("div", "member-labels");
    const longCompany = [...item.forwarder].length > 16;
    if (longCompany) labels.append(node("span", "member-company", item.forwarder));
    labels.append(node("strong", "member-original", (longCompany ? "" : item.forwarder + " ") + "原文：" + (observation(item).raw_label || "未取得")));
    heading.append(labels, node("span", "member-price", formatPrice(item.current_price) + " " + (item.currency || "")));
    const footer = node("div", "member-source-row");
    const ref = asObject(item.source_ref), locator = asObject(ref.locator);
    const shortPosition = source.located && ref.kind === "PDF_PAGE" ? locator.page_number + "ページ" : source.position;
    const sourceText = node("span", "member-source", source.filename + " / " + shortPosition);
    sourceText.title = sourceText.textContent;
    const sourceLink = button("", () => openPreview(source.documentId, item), "text-button member-preview");
    sourceLink.append(sourceText, node("span", "source-arrow", " ↗"));
    sourceLink.title = "原文を見る：" + sourceText.textContent;
    footer.append(sourceLink);
    card.append(heading, footer); host.append(card);
    const details = node("section", "member-conditions"); details.dataset.detailMemberId = item.item_id;
    details.append(node("h3", "", item.forwarder + "：" + (observation(item).raw_label || "未取得")));
    const conditions = node("dl", "edit-facts");
    for (const [label, value] of [["出典", source.filename + "｜" + source.position], ["AI提案", item.ai_suggested_name || "提案なし"], ["確認名", item.current_fee_name || "未確認"],
      ...Object.entries(conditionLabels).map(([key, label]) => [label, context(item)[key] || "未確認"])]) {
      conditions.append(node("dt", "", label), node("dd", "", value));
    }
    details.append(conditions);
    details.append(node("p", "member-eligibility", item.learning_eligible ? "条件・出典は再利用の対象です。競合・撤回と記録版は保存時に確認します。" : "条件・出典が不足しています。今回の費目名は確認できますが、自動学習の記録は作りません。"));
    details.append(node("p", "subtle", "明細 v" + item.version + " ／ 確認記録 v" + (item.current_memory_version ?? 0) + (item.current_memory_status ? " ／ " + statusText(item.current_memory_status) : "")));
    detailsHost.append(details);
    const option = node("option", "", item.forwarder + "：" + observation(item).raw_label); option.value = item.item_id; revokeSelect.append(option);
  }
  if (members.some(item => item.input_kind === "MOCK_SDK")) detailsHost.append(node("p", "subtle", "デモ用解析データ／SDK未接続。原文照合はfixtureに対する検証です。"));
  state.editItem = members.find(item => hasMemory(item)) || members[0];
  revokeSelect.value = state.editItem.item_id;
  const confirmed = members.filter(item => item.current_fee_id);
  const confirmedIds = new Set(confirmed.map(item => item.current_fee_id));
  // Adopted names belong to each member's audit history; row identity is fee_id.
  const nameConflict = confirmedIds.size > 1;
  state.selectedFeeId = !nameConflict && confirmedIds.size === 1 ? [...confirmedIds][0] : "";
  const catalogName = feeName(availableFees().find(fee => fee.fee_id === state.selectedFeeId) || {});
  $("edit-fee-name").value = nameConflict || row.ambiguous ? "" : catalogName || (row.label === "費目未確認" ? "" : row.label);
  state.editInitialFeeName = normalizedFeeName($("edit-fee-name").value);
  const selectedFee = availableFees().find(fee => fee.fee_id === state.selectedFeeId
    && normalizedFeeName(feeName(fee)) === state.editInitialFeeName);
  state.selectedFeeReference = feeReference(selectedFee);
  if (renameAvailable(row)) state.editRenameReference = {
    fee_id: row.rename_fee_id, name_version: row.rename_name_version, display_name: state.editInitialFeeName
  };
  const warnings = [];
  if (nameConflict) warnings.push("確認済み名称が一致していません。統一する名称を選んでください。");
  const conflicts = Object.keys(conditionLabels).filter(key => new Set(members.map(item => context(item)[key]).filter(value => typeof value === "string" && value.trim())).size > 1);
  if (conflicts.length) warnings.push("適用条件が一致しません：" + conflicts.map(key => conditionLabels[key]).join("・") + "。比較行を確認してください。");
  if (row.ambiguous) warnings.push("対応候補が複数あります。原文と適用条件を確認してください。");
  if (members.some(item => item.current_memory_status === "CONFLICTED" || item.current_memory_status === "REVOKED")) warnings.push("確認記録に競合・撤回があります。詳細で確認してください。");
  if (members.some(item => observation(item).source_ready !== true && !item.learning_eligible)) warnings.push("出典を検証できない資料があります。原文を確認してください。");
  if (members.some(item => !item.learning_eligible)) warnings.push("条件・出典が未確認の資料があります。名称の保存と次回の自動適用は別に判定します。");
  $("edit-warning").textContent = warnings.join("\n"); $("edit-warning").hidden = !warnings.length;
  state.editInitialDraft = JSON.stringify(correctionDraft());
  if (draft) {
    state.selectedFeeId = draft.feeId; $("edit-fee-name").value = draft.feeName;
    state.selectedFeeReference = draft.selectedFeeReference ? structuredClone(draft.selectedFeeReference) : null;
    const seen = draft.reviewReference || draft.selectedFeeReference || draft.renameReference;
    if (seen) {
      const latest = feeReference(availableFees().find(fee => fee.fee_id === seen.fee_id));
      if (!sameFeeReference(seen, latest)) state.feeReviewRequired = { seen: structuredClone(seen), latest };
    }
    state.reclassifyMembers = Boolean(draft.reclassifyMembers);
    $("replace-mapping").checked = draft.replaceMapping; $("revoke-reason").value = draft.revokeReason;
  }
  hideFeeCandidates(); renderConfirmationIntent(); setBusy(state.busy);
  if (!$("edit-dialog").open) $("edit-dialog").showModal();
}
function closeRowEdit() {
  if (state.busy) return;
  if (state.pendingConfirmation) {
    formError("edit-error", "保存結果が未確認です。同じ要求を再送して結果を確認してください。"); return;
  }
  if (state.editInitialDraft !== JSON.stringify(correctionDraft()) && !window.confirm("未保存の変更を破棄しますか？")) return;
  $("edit-dialog").close();
}
function restoreRowFocus() {
  const original = state.editReturnFocus;
  if (original?.isConnected) original.focus({ preventScroll: true });
  else [...document.querySelectorAll("[data-row-id]")].find(el => el.dataset.rowId === state.editRow?.row_id)?.querySelector(".row-confirm-button")?.focus({ preventScroll: true });
}
function correctionSavedNotice(response) {
  const members = Array.isArray(response.members) ? response.members : [response];
  if (members.length && members.every(member => member.memory_effect === "ACTIVE")) {
    return "統一費目名を保存しました。同じ原文・条件の次回以降の読み取りに反映されます。";
  }
  if (members.length && members.every(member => member.memory_effect === "UNCHANGED_AMOUNT_ONLY")) {
    return "単価を保存しました。費目名の確認記録は変更していません。";
  }
  return "名称は保存されましたが、一部の学習結果に確認が必要なため、次回は自動適用されません。";
}
function submittedKey(operation, payload) {
  // A lost response can be retried with the exact same idempotency key.
  // New versions are used only after an explicit refresh, then explicit Save.
  const signature = operation + ":" + JSON.stringify(payload);
  if (!state.requestKeys.has(signature)) state.requestKeys.set(signature, requestId());
  return state.requestKeys.get(signature);
}
function versions(item) {
  return {
    expected_case_revision: item.case_revision ?? state.snapshot?.case?.case_revision ?? state.snapshot?.case?.revision,
    expected_item_version: item.version,
    expected_memory_version: item.current_memory_version ?? 0
  };
}
async function mutation(action, errorId) {
  if (state.busy) return;
  setBusy(true);
  if (errorId) formError(errorId, "");
  try { await action(); }
  catch (error) {
    let message = errorText(error);
    if (error.status === 409) message += "\nほかの更新があります。「最新を読み直す」で確認してから、もう一度保存してください。";
    if (errorId && $(errorId).closest("dialog")?.open) formError(errorId, message); else showNotice(message, true);
  }
  finally { setBusy(false); }
}

$("new-case-button").addEventListener("click", () => { $("new-case-form").hidden = false; $("case-name").focus(); });
$("cancel-case").addEventListener("click", () => { $("new-case-form").hidden = true; });
$("case-select").addEventListener("change", async event => {
  state.selectedCase = event.target.value;
  state.pollDeadline = null;
  await mutation(async () => { await loadSnapshot(); showNotice(""); });
});
$("new-case-form").addEventListener("submit", event => {
  event.preventDefault();
  mutation(async () => {
    const name = $("case-name").value.trim();
    if (!name) throw new Error("案件名を入力してください。");
    const result = await api("/api/cases", { method: "POST", body: JSON.stringify({ name }) });
    const created = result.case || result;
    await loadCases(created.case_id);
    $("new-case-form").reset();
    $("new-case-form").hidden = true;
    showNotice("新しい案件を作成しました。この案件の資料を追加してください。");
  });
});
initUploadWorkflow();
$("manual-preview-button").addEventListener("click", () => openPreview(state.manualDocument?.document_id));
$("manual-form").addEventListener("submit", event => {
  event.preventDefault();
  mutation(async () => {
    const data = new FormData($("manual-form"));
    const body = {
      raw_label: String(data.get("raw_label") || "").trim(),
      unit_price: String(data.get("unit_price") || "").trim(),
      currency: String(data.get("currency") || "").trim().toUpperCase(),
      forwarder_id: String(data.get("forwarder_id") || "").trim() || null,
      context: Object.fromEntries(Object.keys(conditionLabels).map(key => [key, String(data.get(key) || "").trim() || null])),
      source_acknowledgment: "I_CONFIRMED_ORIGINAL_LABEL_AND_CONTEXT_FROM_UPLOADED_DOCUMENT"
    };
    if (!$("source-acknowledgment").checked) throw new Error("資料の原文と条件の確認が必要です。");
    await api("/api/documents/" + encodeURIComponent(state.manualDocument.document_id) + "/manual-lines", { method: "POST", body: JSON.stringify(body) });
    $("manual-dialog").close();
    await loadSnapshot();
    showNotice("人工入力の明細を保存しました。金額は今回入力した資料の値です。");
  }, "manual-error");
});
$("revoke-member").addEventListener("change", () => {
  state.editItem = state.editRow.member_items.find(item => item.item_id === $("revoke-member").value);
  $("revoke-button").disabled = state.busy || Boolean(state.pendingConfirmation) || !hasMemory(state.editItem);
});
$("edit-fee-name").addEventListener("input", () => { state.selectedFeeId = ""; state.selectedFeeReference = null; formError("edit-error", ""); renderConfirmationIntent(); renderFeeCandidates(); });
$("reclassify-members").addEventListener("click", () => {
  if (state.busy || state.pendingConfirmation) return;
  state.reclassifyMembers = !state.reclassifyMembers;
  hideFeeCandidates(); renderConfirmationIntent(); formError("edit-error", ""); $("edit-fee-name").focus();
});
$("edit-fee-name").addEventListener("compositionstart", () => { state.editComposing = true; });
$("edit-fee-name").addEventListener("compositionend", () => { state.editComposing = false; state.imeEndedAt = performance.now(); });
$("edit-fee-name").addEventListener("keydown", event => {
  if (event.key === "Enter" && (event.isComposing || event.keyCode === 229 || state.editComposing || performance.now() - (state.imeEndedAt || -1000) < 100)) event.preventDefault();
  if (event.key === "ArrowDown" && !event.isComposing) { event.preventDefault(); renderFeeCandidates(); $("fee-candidates").firstElementChild?.focus(); }
  if (event.key === "Escape" && !$("fee-candidates").hidden) { event.preventDefault(); event.stopPropagation(); hideFeeCandidates(); }
});
$("edit-dialog").addEventListener("click", event => { if (!event.target.closest(".fee-name-field")) hideFeeCandidates(); });
$("edit-dialog").addEventListener("focusout", event => { if (!event.relatedTarget?.closest(".fee-name-field")) hideFeeCandidates(); });
$("edit-dialog").addEventListener("cancel", event => { event.preventDefault(); closeRowEdit(); });
$("edit-dialog").addEventListener("close", restoreRowFocus);
$("edit-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (state.busy || state.editComposing || performance.now() - (state.imeEndedAt || -1000) < 100) return;
  formError("edit-error", ""); hideFeeCandidates();
  if (!state.pendingConfirmation) {
    if (state.feeReviewRequired) {
      formError("edit-error", "名称が更新されています。表示された最新の候補を選び直してください。");
      renderFeeCandidates(); return;
    }
    const name = normalizedFeeName($("edit-fee-name").value);
    if (!name) { formError("edit-error", "統一費目名を入力してください。"); return; }
    if ([...name].length > 1024) { formError("edit-error", "統一費目名は1024文字以内で入力してください。"); return; }
    const matches = availableFees().filter(fee => normalizedFeeName(feeName(fee)) === name);
    let selected = matches.find(fee => fee.fee_id === state.selectedFeeId);
    if (!selected && matches.length > 1) { formError("edit-error", "同じ名称の候補が複数あります。入力欄の候補から選択してください。"); renderFeeCandidates(); return; }
    if (!selected && renameAvailable() && !state.reclassifyMembers && matches.some(fee => fee.fee_id !== state.editRow.rename_fee_id)) {
      formError("edit-error", "同じ名称の別の費目があります。分類を変える場合は候補から選択してください。"); renderFeeCandidates(); return;
    }
    selected ||= matches.length === 1 ? matches[0] : null;
    const row = state.editRow;
    const intent = confirmationIntent(name, selected);
    const body = { intent, expected_case_revision: state.editCaseRevision,
      members: row.member_items.map(item => ({ item_id: item.item_id, expected_item_version: item.version,
        expected_memory_version: item.current_memory_version ?? 0,
        ...(item.memory_resolution?.kind === "LEGACY_PROJECTION"
          ? { expected_memory_evidence: item.memory_resolution.evidence_versions } : {}) })),
      replace_existing_mapping: $("replace-mapping").checked };
    // Names only; never use prices or currencies from the DOM.
    if (intent === "RENAME_FEE") {
      body.expected_fee_id = row.rename_fee_id;
      body.expected_name_version = row.rename_name_version;
      body.new_fee_name = name;
    } else if (selected || (state.selectedFeeId && name === normalizedFeeName(row.label))) {
      const reference = selected ? feeReference(selected) : state.selectedFeeReference;
      if (!reference || normalizedFeeName(reference.display_name) !== name) {
        formError("edit-error", "費目名の版を確認できません。最新を読み直して候補を選び直してください。（詳細を見る）");
        $("refresh-item").hidden = false; return;
      }
      body.target_fee_id = reference.fee_id;
      body.expected_fee_id = reference.fee_id;
      body.expected_name_version = reference.name_version;
    } else body.new_fee_name = name;
    body.idempotency_key = submittedKey("row-confirm:" + row.row_id, body);
    state.pendingConfirmation = { path: "/api/cases/" + encodeURIComponent(state.editCaseId) + "/comparison/" + encodeURIComponent(row.row_id) + "/confirmation", body };
  }
  setBusy(true); $("save-correction").textContent = "保存中…";
  let result;
  try {
    const pending = state.pendingConfirmation;
    result = await api(pending.path, { method: "POST", body: JSON.stringify(pending.body) });
  } catch (error) {
    $("refresh-item").hidden = false;
    if (error.status && error.status < 500) {
      state.pendingConfirmation = null;
      formError("edit-error", error.status === 409
        ? "他の更新が反映されています。最新状態を読み直して確認してください。（詳細を見る）"
        : "保存できませんでした。入力と対象の明細を確認してください。（HTTP " + error.status + "）");
    } else formError("edit-error", "保存結果を確認できません。同じ要求を再送して結果を確認してください。入力は保持しています。");
  } finally { setBusy(false); $("save-correction").textContent = state.pendingConfirmation ? "同じ要求で再確認" : "確認して保存"; }
  if (!result) return;
  state.pendingConfirmation = null; setBusy(false); $("save-correction").textContent = "確認して保存";
  $("edit-dialog").close();
  let message = correctionSavedNotice(result);
  try { await loadSnapshot(); restoreRowFocus(); }
  catch { message += " 比較表を取得できませんでした。画面を更新してください。"; }
  showNotice(message);
});
$("refresh-item").addEventListener("click", () => mutation(async () => {
  const rowId = state.editRow.row_id;
  const draft = correctionDraft(), requestKeys = state.requestKeys;
  await loadSnapshot();
  if (state.pendingConfirmation) {
    formError("edit-error", "最新表示を取得しました。保存結果は「同じ要求で再確認」で確認してください。要求の版とキーは保持しています。"); return;
  }
  const row = state.snapshot.comparison.rows.find(entry => entry.row_id === rowId);
  if (!row) throw new Error("対象の明細構成が変わりました。入力は保持しています。閉じて比較行を確認してください。");
  openRowEdit(row, draft, requestKeys);
  formError("edit-error", state.feeReviewRequired ? "最新の費目名を表示しました。候補を選び直してください。入力は保持しています。"
    : "最新状態を取得しました。入力内容と各社の原文を確認して保存してください。");
}, "edit-error").then(() => {
  if (state.feeReviewRequired && !state.pendingConfirmation) renderFeeCandidates();
}));
$("revoke-button").addEventListener("click", () => mutation(async () => {
  const reason = $("revoke-reason").value.trim();
  if (!reason) throw new Error("撤回理由を入力してください。");
  const item = state.editItem;
  const body = { ...versions(item), reason };
  body.idempotency_key = submittedKey("revoke:" + item.item_id, body);
  await api("/api/items/" + encodeURIComponent(item.item_id) + "/revoke", { method: "POST", body: JSON.stringify(body) });
  $("edit-dialog").close();
  await loadSnapshot();
  showNotice("確認記録を撤回しました。既存案件の表示は維持し、新規明細には自動適用しません。");
}, "edit-error"));
$("export-button").addEventListener("click", () => {
  if (state.selectedCase) window.location.assign("/api/cases/" + encodeURIComponent(state.selectedCase) + "/export.xlsx");
});
document.querySelectorAll(".close-dialog").forEach(element => element.addEventListener("click", () => {
  const dialog = element.closest("dialog");
  if (dialog.id === "edit-dialog") { closeRowEdit(); return; }
  dialog.close();
  if (dialog.id === "preview-dialog") $("preview-frame").removeAttribute("src");
}));
$("preview-dialog").addEventListener("close", () => $("preview-frame").removeAttribute("src"));

(async function initialize() {
  setBusy(true);
  try {
    const results = await Promise.allSettled([api("/api/status"), loadCases()]);
    if (results[0].status === "fulfilled") state.status = results[0].value;
    else { state.status = { sdk: { reason: "サーバーに接続できません" }, ai: { reason: "接続状態を取得できません" } }; showNotice(errorText(results[0].reason), true); }
    renderStatus();
    renderSnapshot();
    if (results[1].status === "rejected") showNotice(errorText(results[1].reason), true);
  } catch (error) { showNotice(errorText(error), true); }
  finally { setBusy(false); }
})();
