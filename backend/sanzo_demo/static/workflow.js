"use strict";
// Browser orchestration only: existing upload/process/task APIs and PostgreSQL remain authoritative.
function pendingFiles() { return state.pendingByCase?.get(state.selectedCase) || []; }
function savedSession(name, fallback) {
  try { return JSON.parse(sessionStorage.getItem(name)) ?? fallback; } catch { return fallback; }
}
function savePause() {
  try {
    sessionStorage.setItem("sanzo-ai-pause", JSON.stringify(state.aiPause));
    sessionStorage.setItem("sanzo-ai-ack", JSON.stringify([...state.acknowledgedFailures]));
  } catch { /* Persistent task metadata still restores this case's diagnostics. */ }
}
function aiFailure(task) {
  const code = task?.error_code || "";
  if (!code.startsWith("AI_")) return null;
  const event = [...(task.events || [])].reverse().find(e => e.metadata?.failed_ai_attempt);
  const meta = event?.metadata?.failed_ai_attempt || {};
  const category = code === "AI_PROCESSING_PAUSED" ? "PAUSED_LOCAL" : code === "AI_REQUEST_BUDGET_EXHAUSTED" ? "LOCAL_BUDGET"
    : ["TEMPORARY_RATE_LIMIT", "ACCOUNT_QUOTA", "UNKNOWN_429"].includes(meta.failure_category) ? meta.failure_category
    : code === "AI_HTTP_429" ? "UNKNOWN_429" : "OTHER_AI";
  const read = event?.metadata?.read_completed === true ||
    task.events?.some(e => e.metadata?.read_evidence);
  const purpose = meta.purpose || (event?.metadata?.ai_metadata?.calls?.length ? "fee_classification" : "");
  return { taskId: task.task_id, documentId: task.document_id, category, code,
    until: typeof meta.retry_not_before === "string" && Number.isFinite(Date.parse(meta.retry_not_before)) ? Date.parse(meta.retry_not_before) : null,
    requestId: meta.request_id || "", providerCode: meta.provider_error_code || "",
    providerType: meta.provider_error_type || "", read: Boolean(read), purpose };
}
function aiFailureText(failure, includeTechnical = true) {
  if (!failure) return "";
  if (failure.category === "PAUSED_LOCAL") return "原文読み取りは完了しました。AI 分類は保留中です（呼出しなし）。利用状況・待機時間を確認後、必要な場合のみ手動で再開してください。";
  const stage = failure.purpose === "fee_classification" ? "AI の費目分類"
    : failure.purpose === "quotation_extraction" ? "AI の明細抽出" : "AI リクエスト";
  const prefix = (failure.read ? "原文の読み取りは完了しています。" : "") + stage + "に失敗しました。";
  const messages = {
    TEMPORARY_RATE_LIMIT: "OpenAI の一時的なレート制限です。",
    ACCOUNT_QUOTA: "OpenAI アカウントの残高・使用上限に関するエラーです。請求・利用状況を確認するまで再試行しません。",
    LOCAL_BUDGET: "この Demo の承認済み累計リクエスト上限に達しました。再起動しても枠は増えません。",
    UNKNOWN_429: "OpenAI が HTTP 429 を返しましたが、原因を特定できません。保存済みの診断情報を確認してください。過去の応答内容は補完しません。",
    OTHER_AI: "接続状況と表示されたエラーを確認してください。"
  };
  let text = prefix + messages[failure.category];
  if (failure.until && failure.category !== "ACCOUNT_QUOTA" && failure.category !== "LOCAL_BUDGET") {
    const seconds = Math.max(0, Math.ceil((failure.until - Date.now()) / 1000));
    text += seconds ? " あと " + seconds + " 秒待ってください。" : " 待機時間が過ぎました。必要な場合は手動で再開してください。";
  } else if (failure.category === "TEMPORARY_RATE_LIMIT") text += " 待機時間は応答にありません。時間をおいて手動で再開してください。";
  text += " 自動再試行は行いません。";
  if (!includeTechnical) return text;
  if (failure.providerCode) text += " code=" + failure.providerCode;
  if (failure.providerType) text += " type=" + failure.providerType;
  if (failure.requestId) text += " request_id=" + failure.requestId;
  return text + " [" + failure.code + "]";
}
function observeAIFailures() {
  const latest = new Map();
  for (const task of state.snapshot?.tasks || []) {
    const previous = latest.get(task.document_id);
    if (!previous || String(task.created_at || task.updated_at).localeCompare(String(previous.created_at || previous.updated_at)) >= 0) latest.set(task.document_id, task);
  }
  for (const task of latest.values()) {
    const failure = aiFailure(task);
    if (failure && ["TEMPORARY_RATE_LIMIT", "ACCOUNT_QUOTA", "UNKNOWN_429", "LOCAL_BUDGET"].includes(failure.category)
        && !state.acknowledgedFailures.has(failure.taskId)) {
      if (!state.aiPause || failure.category === "ACCOUNT_QUOTA" || failure.category === "LOCAL_BUDGET" ||
          (failure.until || 0) > (state.aiPause.until || 0)) state.aiPause = failure;
    }
  }
  const pauseTask = state.aiPause && latest.get(state.aiPause.documentId);
  if (pauseTask && ["AVAILABLE", "REVIEW_REQUIRED"].includes(pauseTask.status)) state.aiPause = null;
  savePause();
}
function activeAIPause() {
  const budget = state.status?.ai?.budget;
  if (budget && budget.requests_used >= budget.max_requests) return { category: "LOCAL_BUDGET", code: "AI_REQUEST_BUDGET_EXHAUSTED" };
  const pause = state.aiPause;
  if (pause && (["ACCOUNT_QUOTA", "LOCAL_BUDGET"].includes(pause.category) || pause.until > Date.now())) return pause;
  return null;
}
function localReadBlockedByAIPause() {
  // Real SDK + confirmed memory needs no AI. A paused unknown-only request is
  // blocked after the server's memory lookup, not before local facts can run.
  return state.status?.reader_mode !== "REAL_SDK" && Boolean(activeAIPause());
}
function renderAIPause() {
  const host = $("ai-pause");
  if (!host) return;
  host.replaceChildren();
  const pause = activeAIPause() || state.aiPause;
  host.hidden = !pause;
  if (!pause) return;
  host.append(node("p", "", aiFailureText(pause, false)));
  const details = node("details", ""); details.append(node("summary", "", "詳細を見る"), node("p", "", aiFailureText(pause))); host.append(details);
  host.append(node("p", "", "後続の AI 処理は停止しています。アップロード済みの資料と完了した結果は保持しています。再開には明示的な操作が必要です。"));
  if (pause.category === "ACCOUNT_QUOTA") {
    const ack = button("アカウントの利用状況を確認済み", () => {
      if (!window.confirm("OpenAI の請求・利用状況を確認しましたか？この操作だけでは AI を再実行しません。")) return;
      state.acknowledgedFailures.add(pause.taskId);
      state.aiPause = null; savePause(); renderAIPause(); setBusy(state.busy);
    });
    ack.id = "ack-ai-account"; ack.disabled = state.busy; host.append(ack);
  }
}
function renderPendingFiles() {
  const list = $("pending-files");
  if (!list) return;
  list.replaceChildren();
  const entries = pendingFiles();
  $("pending-count").textContent = entries.length + " 件の待ちリスト";
  for (const entry of entries) {
    const row = node("li", "pending-file");
    row.dataset.pendingId = entry.id;
    row.append(node("span", "pending-filename", entry.file.name));
    row.append(node("span", "pending-state", entry.message || (entry.documentId ? "アップロード済み・処理待ち" : "アップロード待ち")));
    const remove = button("削除", () => {
      if (state.busy || state.selecting) return;
      state.pendingByCase.set(state.selectedCase, entries.filter(e => e.id !== entry.id));
      renderPendingFiles(); setBusy(false);
    });
    remove.disabled = state.busy || state.selecting;
    row.append(remove); list.append(row);
  }
  renderAIPause();
}
async function addSelectedFiles(files, caseId) {
  state.selecting = true; setBusy(state.busy);
  try {
    const entries = state.pendingByCase.get(caseId) || [];
    for (const file of files) {
      if (file.size > 20 * 1024 * 1024) { showNotice(file.name + "：20 MiB の上限を超えています。", true); continue; }
      const bytes = await file.arrayBuffer();
      const digest = await crypto.subtle.digest("SHA-256", bytes);
      const hash = [...new Uint8Array(digest)].map(n => n.toString(16).padStart(2, "0")).join("");
      if (entries.some(e => e.hash === hash)) continue;
      const allowed = state.status?.reader?.supported_sha256;
      const unsupported = state.status?.reader_mode === "MOCK_SDK" && Array.isArray(allowed) && !allowed.includes(hash);
      entries.push({ id: requestId(), file, hash, documentId: null, unsupported,
        message: unsupported ? "この Mock モードは指定の三つのサンプルのみ対応しています。内容が変更されたファイルも対象外です。" : "アップロード待ち" });
    }
    state.pendingByCase.set(caseId, entries);
  } finally { state.selecting = false; renderPendingFiles(); setBusy(state.busy); }
}
async function refreshRuntimeStatus() {
  state.status = await api("/api/status");
  renderStatus();
}
async function submitAndWait(document) {
  const pause = activeAIPause();
  if (pause && state.status?.reader_mode !== "REAL_SDK") throw new Error(aiFailureText(pause));
  let task = latestTask(document.document_id);
  if (!task || terminalTaskStates.has(task.status)) {
    let request = state.processRequests.get(document.document_id);
    if (!request) {
      request = { key: requestId(), fieldBindings: {}, pauseAI: Boolean(activeAIPause()) };
      state.processRequests.set(document.document_id, request);
    }
    // Keep the same key on an uncertain response; never auto-resubmit.
    const accepted = await api("/api/documents/" + encodeURIComponent(document.document_id) + "/process",
      { method: "POST", body: JSON.stringify({ idempotency_key: request.key, field_bindings: request.fieldBindings || {}, pause_ai: request.pauseAI || false }) });
    task = accepted.task || accepted;
  }
  const deadline = Date.now() + 150000;
  while (true) {
    const fetched = await api("/api/tasks/" + encodeURIComponent(task.task_id));
    task = fetched.task || fetched;
    await loadSnapshot({ keepVisible: true });
    if (terminalTaskStates.has(task.status)) break;
    if (Date.now() >= deadline) throw new Error("処理は継続中です。再送せず「状態を更新」で確認してください。");
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  state.processRequests.delete(document.document_id);
  await refreshRuntimeStatus();
  return task;
}
async function startPendingBatch() {
  if (state.busy || state.selecting) return;
  await mutation(async () => {
    const caseId = state.selectedCase;
    if (!caseId) throw new Error("見積比較を選択してください。");
    await refreshRuntimeStatus();
    const pause = activeAIPause();
    if (pause && state.status?.reader_mode !== "REAL_SDK") throw new Error(aiFailureText(pause));
    await loadSnapshot({ keepVisible: true });
    const entries = pendingFiles().filter(e => !e.unsupported);
    if (!entries.length) throw new Error("指定サンプルを待ちリストに追加してください。");
    // All originals are saved first. A later 429 never discards waiting documents.
    for (const entry of entries) {
      const existing = (state.snapshot?.documents || []).find(d => d.sha256 === entry.hash);
      if (existing) entry.documentId = existing.document_id;
      if (entry.documentId) continue;
      entry.message = "アップロード中"; renderPendingFiles();
      const form = new FormData(); form.append("file", entry.file);
      try {
        const document = await api("/api/cases/" + encodeURIComponent(caseId) + "/documents", { method: "POST", body: form });
        entry.documentId = document.document_id;
        entry.message = "アップロード済み・処理待ち";
      } catch (error) {
        // A lost response may already have committed. Reconcile by current-file hash,
        // without issuing a second upload in this action.
        await loadSnapshot({ keepVisible: true });
        const recovered = state.snapshot.documents.find(d => d.sha256 === entry.hash);
        if (recovered) { entry.documentId = recovered.document_id; entry.message = "アップロード済み・処理待ち"; }
        else { entry.message = "アップロード未確認：" + errorText(error); }
      }
      await loadSnapshot({ keepVisible: true }); renderPendingFiles();
    }
    for (const entry of entries) {
      if (!entry.documentId) continue;
      const document = state.snapshot.documents.find(d => d.document_id === entry.documentId);
      const previous = latestTask(entry.documentId);
      if (previous && ["AVAILABLE", "REVIEW_REQUIRED"].includes(previous.status)) { entry.message = "処理済み・結果を保持"; continue; }
      entry.message = "解析中"; renderPendingFiles();
      const task = await submitAndWait(document);
      const failure = aiFailure(task);
      entry.message = failure ? aiFailureText(failure) : taskStatusText(task.status);
      renderPendingFiles();
      if (failure && (task.error_code === "AI_HTTP_429" || failure.category === "LOCAL_BUDGET")) {
        showNotice("AI 処理を一時停止しました。後続資料はアップロード済みです。", true);
        return;
      }
      if (["FAILED", "BLOCKED", "INTERRUPTED"].includes(task.status)) {
        showNotice("処理を停止しました。資料と完了済み結果を保持しています。", true);
        return;
      }
    }
    renderPendingFiles();
    showNotice("待ちリストの処理が完了しました。各資料の確認事項をご確認ください。");
  });
}
function initUploadWorkflow() {
  state.pendingByCase = new Map();
  state.selecting = false;
  state.selectionChain = Promise.resolve();
  state.aiPause = savedSession("sanzo-ai-pause", null);
  state.acknowledgedFailures = new Set(savedSession("sanzo-ai-ack", []));
  $("files").addEventListener("change", event => {
    const files = Array.from(event.target.files || []), caseId = state.selectedCase;
    event.target.value = ""; // Native replacement cannot erase our own queue.
    if (!caseId || state.busy) return;
    state.selecting = true;
    state.selectionChain = state.selectionChain.then(() => addSelectedFiles(files, caseId)).catch(error => showNotice(errorText(error), true));
  });
  $("upload-form").addEventListener("submit", event => { event.preventDefault(); startPendingBatch(); });
  setInterval(() => { renderAIPause(); setBusy(state.busy); }, 1000);
}
