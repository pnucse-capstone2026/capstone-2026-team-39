"use strict";
const $ = id => document.getElementById(id);
const clone = value => structuredClone(value);
const stable = value => JSON.stringify(sort(value));
function sort(value) { return Array.isArray(value) ? value.map(sort) : value && typeof value === "object" ? Object.fromEntries(Object.keys(value).sort().map(k => [k, sort(value[k])])) : value; }
const pretty = value => JSON.stringify(value, null, 2);
const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let token, packet, scopes, data, receipt, backupKey, savedText, pending = null, busy = false, ready = false, blocked = false, timer;
let caseIndex = 7, runIndex = 0, recoverable = null, recoveryRaw = null, selectedHistory = null;
const fields = ["support", "completeness", "postprocessing", "judge_agreement", "note"];
const item = () => packet.items[caseIndex];
const run = () => item().runs[runIndex];
const label = () => data.labels.reviews.find(r => r.answer_id === run().answer_id);
const reviewStatus = r => r.support && r.completeness && r.postprocessing !== "not_checked" && r.note.trim() ? "REVIEWED" : "PENDING";
const dirty = () => data && stable(data) !== savedText;
function status(message, state = "pending") { $("save-status").textContent = message; $("save-status").dataset.state = state; }
function enable(value) { $("review-fields").disabled = !value; $("reviewer").disabled = !value; for (const id of ["save-now", "export", "history-button"]) $(id).disabled = !value; }
async function api(path, body) {
  const abort = new AbortController(), timeout = setTimeout(() => abort.abort(), 10000);
  try {
    const response = await fetch(path, {method: body === undefined ? "GET" : "POST", headers: {"X-Review-Token": token, ...(body === undefined ? {} : {"Content-Type":"application/json"})}, body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store", signal: abort.signal});
    const result = await response.json();
    if (!response.ok) { const error = Error(result.error || `HTTP ${response.status}`); error.status = response.status; throw error; }
    return result;
  } finally { clearTimeout(timeout); }
}
async function verifyReceipt(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(stable(value.data)));
  const hex = [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2,"0")).join("");
  if (hex !== value.sha256 || !Number.isInteger(value.revision)) throw Error("저장 응답의 해시가 맞지 않습니다.");
}
function backupLocal() {
  const value = {data, base_revision:receipt.revision, packet_sha256:data.labels.packet_sha256};
  const text = pretty(value);
  localStorage.setItem(backupKey, text);
  if (localStorage.getItem(backupKey) !== text) throw Error("브라우저 임시 백업 확인 실패");
}
function progress() {
  const done = Object.values(data.scope_reviews).filter(r => r.decision && r.reason.trim()).length;
  $("progress").textContent = `평가 범위 작성 ${done}/3`;
  $("receipt").textContent = receipt ? `마지막 파일 확인: ${new Date(receipt.saved_at).toLocaleString("ko-KR")} · 버전 ${receipt.revision} · SHA ${receipt.sha256.slice(0,12)} · ${receipt.backup_path}` : "";
}
function changed() {
  if (!ready || blocked) return;
  try { backupLocal(); status("입력 보관됨 · 컴퓨터 파일에 저장 중…"); }
  catch (error) { blocked = true; enable(false); status(`${error.message}. 입력은 화면에 남아 있습니다. 긴급 텍스트를 보관하세요.`, "error"); $("recovery").classList.remove("hidden"); return; }
  progress(); clearTimeout(timer); timer = setTimeout(flush, 350);
}
async function flush() {
  clearTimeout(timer);
  if (!ready || blocked || busy || (!dirty() && !pending)) return !dirty();
  busy = true; $("export").disabled = true;
  if (!pending) pending = {data:clone(data), base_revision:receipt.revision, mutation:crypto.randomUUID()};
  try {
    const result = await api("/api/save", pending);
    await verifyReceipt(result);
    if (stable(result.data) !== stable(pending.data)) throw Error("저장된 입력이 전송한 입력과 다릅니다.");
    receipt = result; savedText = stable(result.data); pending = null;
    backupLocal(); progress();
    status(dirty() ? "이전 입력 저장 확인 · 새 입력 저장 중…" : `파일 저장 확인 · 버전 ${receipt.revision}`, dirty() ? "pending" : "success");
  } catch (error) {
    if (error.status === 409) { blocked = true; enable(false); $("recovery").classList.remove("hidden"); $("use-draft").disabled = true; $("use-server").disabled = false; $("recovery-note").textContent = "다른 탭과 충돌했습니다. 내 입력 텍스트를 먼저 보관한 뒤 서버 기록으로 계속할 수 있습니다. 자동으로 덮어쓰지 않습니다."; }
    status(`파일 저장 미확인: ${error.name === "AbortError" ? "서버 응답 시간이 초과됐습니다." : error.message} 입력은 유지됩니다. ‘지금 파일에 저장’으로 재시도하세요.`, "error");
    $("recovery").classList.remove("hidden");
  } finally {
    busy = false; $("export").disabled = blocked || Boolean(pending) || dirty();
    if (!blocked && !pending && dirty()) timer = setTimeout(flush, 0);
  }
  return !dirty() && !pending;
}
function visibleIndices() { return packet.items.map((x,i) => i).filter(i => $("all-cases").checked || scopes[packet.items[i].review_id]); }
function render() {
  const current = item(), record = run(), row = label(), indices = visibleIndices();
  $("case-select").innerHTML = indices.map(i => `<option value="${i}">${escapeHTML(packet.items[i].review_id)} · ${escapeHTML(packet.items[i].question.slice(0,55))}</option>`).join("");
  $("case-select").value = String(caseIndex); $("case-meta").textContent = `${current.review_id} · ${current.case_id} · ${record.generation_run_id}`;
  $("question").textContent = current.question; $("draft").textContent = record.raw_draft; $("answer").textContent = record.answer; $("cited-answer").textContent = record.cited_answer ?? "기록 없음";
  $("sources").innerHTML = record.contexts.map((c,i) => `<details class="source" ${i===0 ? "open" : ""}><summary>#${escapeHTML(c.source_number ?? i+1)} · ${escapeHTML(c.source_title || "제목 없음")}<small>${escapeHTML(c.chunk_id)}</small></summary><div class="copy">${escapeHTML(c.text)}</div><small>${escapeHTML(c.source_url || "")}</small></details>`).join("");
  $("claims").textContent = pretty({claims:record.claims, postprocessing:record.postprocessing});
  $("judge-panel").open = false; $("judge").textContent = pretty({judge:record.judge, required_claims:current.required_claims, guard:record.judge_guard});
  $("scope-box").classList.toggle("hidden", !scopes[current.review_id]);
  if (scopes[current.review_id]) { $("review-heading").textContent = scopes[current.review_id]; $("scope-reason").value = data.scope_reviews[current.review_id].reason; document.querySelectorAll('[name="scope"]').forEach(r => r.checked = r.value === data.scope_reviews[current.review_id].decision); }
  fields.forEach(k => $(k).value = row[k]); $("reviewer").value = data.labels.reviewer;
  $("prev").disabled = indices.indexOf(caseIndex) === 0; $("next").disabled = indices.indexOf(caseIndex) === indices.length-1;
  document.querySelectorAll("[data-run]").forEach(button => button.setAttribute("aria-pressed", String(Number(button.dataset.run) === runIndex)));
  progress();
}
function navigate(index) { caseIndex = index; runIndex = 0; render(); }
$("reviewer").addEventListener("input", () => { data.labels.reviewer = $("reviewer").value; changed(); });
$("scope-reason").addEventListener("input", () => { data.scope_reviews[item().review_id].reason = $("scope-reason").value; changed(); });
document.querySelectorAll('[name="scope"]').forEach(radio => radio.addEventListener("change", () => { data.scope_reviews[item().review_id].decision = radio.value; changed(); }));
fields.forEach(key => $(key).addEventListener(key === "note" ? "input" : "change", () => { const row = label(); fields.forEach(k => row[k] = $(k).value); row.status = reviewStatus(row); changed(); }));
$("case-select").addEventListener("change", () => navigate(Number($("case-select").value)));
for (const [id,step] of [["prev",-1],["next",1]]) $(id).addEventListener("click", () => { const indices = visibleIndices(); navigate(indices[indices.indexOf(caseIndex)+step]); });
$("all-cases").addEventListener("change", () => { if (!visibleIndices().includes(caseIndex)) caseIndex = visibleIndices()[0]; render(); });
document.querySelectorAll("[data-run]").forEach(button => button.addEventListener("click", () => { runIndex = Number(button.dataset.run); render(); }));
$("judge-panel").querySelector("summary").addEventListener("click", event => {
  if (!ready || blocked) { event.preventDefault(); return; }
  if (!$("judge-panel").open && label().judge_revealed_at === null) { const row = label(); row.judge_revealed_at = new Date().toISOString(); row.labels_before_judge = Object.fromEntries(["support","completeness","postprocessing","note"].map(k => [k,row[k]])); changed(); }
});
$("save-now").addEventListener("click", flush);
$("emergency").addEventListener("click", () => { $("emergency-panel").classList.remove("hidden"); $("emergency-text").value = pretty({current_input:data ?? null, previous_browser_backup:recoveryRaw}); });
$("copy-emergency").addEventListener("click", () => { $("emergency-text").focus(); $("emergency-text").select(); });
window.addEventListener("beforeunload", event => { if (dirty() || pending) { event.preventDefault(); event.returnValue = ""; } });
document.addEventListener("visibilitychange", () => { if (document.hidden && ready) flush(); });

function archiveLocal(value) {
  if (!value) return;
  const key = `${backupKey}:archive:${crypto.randomUUID()}`;
  localStorage.setItem(key, value);
  if (localStorage.getItem(key) !== value) throw Error("기존 임시 입력의 별도 보관에 실패했습니다.");
}
function activate(value) {
  receipt = value; data = clone(value.data); savedText = stable(value.data); pending = null;
  ready = true; blocked = false; enable(true); render();
  $("workspace").classList.remove("hidden"); $("recovery").classList.add("hidden");
  status(`파일 저장 확인 · 버전 ${value.revision}`, "success");
}
function preview(value) { return pretty({reviewer:value.labels.reviewer, scope_reviews:value.scope_reviews, notes:value.labels.reviews.filter(r => r.note || r.support || r.completeness)}); }
function retainJudgeAudit(value, latest) {
  for (const row of value.labels.reviews) {
    const prior = latest.labels.reviews.find(r => r.answer_id === row.answer_id);
    if (prior?.judge_revealed_at) { row.judge_revealed_at = prior.judge_revealed_at; row.labels_before_judge = clone(prior.labels_before_judge); }
  }
}
$("use-draft").addEventListener("click", async () => {
  if (!recoverable || !confirm("임시 입력을 새 버전으로 저장할까요? 현재 서버 버전도 기록에 남습니다.")) return;
  try {
    const latest = await api("/api/state"); await verifyReceipt(latest);
    archiveLocal(recoveryRaw); const draft = clone(recoverable.data); retainJudgeAudit(draft, latest.data);
    activate(latest); data = draft; render(); backupLocal(); await flush();
  } catch (error) { blocked = true; enable(false); status(`복원 미완료: ${error.message}. 기존 기록은 삭제하지 않았습니다.`, "error"); $("recovery").classList.remove("hidden"); }
});
$("use-server").addEventListener("click", async () => {
  if (!packet || !confirm("현재 임시 입력을 브라우저의 별도 백업에 보관한 뒤, 서버 기록으로 화면을 바꿀까요? 필요한 입력은 먼저 긴급 텍스트로 복사하세요.")) return;
  try {
    const latest = await api("/api/state"); await verifyReceipt(latest);
    archiveLocal(recoveryRaw); archiveLocal(pretty({data, pending}));
    activate(latest); backupLocal(); recoverable = null; recoveryRaw = null;
  } catch (error) { blocked = true; enable(false); status(`전환 중단: ${error.message}. 현재 입력을 지우지 않습니다.`, "error"); }
});
$("export").addEventListener("click", async () => {
  try {
    if (!await flush()) throw Error("먼저 ‘파일 저장 확인’을 기다려주세요.");
    const result = await api("/api/export", {});
    $("export-result").textContent = `백업 파일 저장 확인 · 버전 ${result.revision} · ${result.path} · SHA ${result.sha256}`;
    const response = await fetch(`/api/export/${result.filename}`, {headers:{"X-Review-Token":token}, cache:"no-store"});
    if (!response.ok) throw Error("파일은 저장됐지만 다운로드 응답을 받지 못했습니다.");
    const bytes = await response.arrayBuffer(), digest = await crypto.subtle.digest("SHA-256", bytes);
    if ([...new Uint8Array(digest)].map(b => b.toString(16).padStart(2,"0")).join("") !== result.sha256) throw Error("다운로드 데이터의 해시가 다릅니다. 서버 백업 경로를 확인하세요.");
    const url = URL.createObjectURL(new Blob([bytes], {type:"application/json;charset=utf-8"}));
    const link = document.createElement("a"); link.href = url; link.download = result.filename; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    $("export-result").textContent += " · 브라우저 다운로드 요청도 보냈습니다. 다운로드 완료 여부는 Chrome에서 확인해주세요.";
  } catch (error) { status(`백업 안내: ${error.message}`, "error"); }
});
$("history-button").addEventListener("click", async () => {
  try {
    const rows = await api("/api/history"); selectedHistory = null; $("restore").disabled = true;
    $("history-select").innerHTML = rows.map(r => `<option value="${r.revision}">버전 ${r.revision} · ${escapeHTML(new Date(r.created_at).toLocaleString("ko-KR"))} · ${escapeHTML(r.action)}</option>`).join("");
    $("history-preview").textContent = "선택 기록을 먼저 확인해주세요."; $("history").classList.remove("hidden");
  } catch (error) { status(`기록 불러오기 실패: ${error.message}`, "error"); }
});
$("history-select").addEventListener("change", () => { selectedHistory = null; $("restore").disabled = true; $("history-preview").textContent = "선택 기록을 먼저 확인해주세요."; });
$("history-preview-button").addEventListener("click", async () => {
  try {
    const value = await api(`/api/revision/${Number($("history-select").value)}`); await verifyReceipt(value);
    selectedHistory = value; $("history-preview").textContent = preview(value.data);
    $("restore").disabled = value.revision >= receipt.revision || dirty() || Boolean(pending) || blocked;
  } catch (error) { status(`기록 확인 실패: ${error.message}`, "error"); }
});
$("restore").addEventListener("click", async () => {
  if (!selectedHistory || dirty() || pending || busy || blocked || !confirm(`버전 ${selectedHistory.revision}의 입력을 새 버전으로 복원할까요? 현재 버전 ${receipt.revision}은 삭제되지 않습니다.`)) return;
  enable(false); blocked = true;
  try {
    const value = await api("/api/restore", {target:selectedHistory.revision, base_revision:receipt.revision, mutation:crypto.randomUUID()});
    await verifyReceipt(value); archiveLocal(localStorage.getItem(backupKey)); activate(value); backupLocal(); $("history").classList.add("hidden");
  } catch (error) { status(`복원 결과 미확인: ${error.message}. 새로고침 후 서버 버전을 확인하세요. 기존 버전은 남습니다.`, "error"); }
});
async function boot() {
  enable(false);
  try {
    if (window.top !== window.self) throw Error("미리보기 안에서는 작성할 수 없습니다. 제공된 주소를 Chrome의 새 탭으로 열어주세요.");
    token = location.hash.slice(1);
    if (!/^[A-Za-z0-9_-]{40,64}$/.test(token)) throw Error("검토 전용 링크가 필요합니다. 서버 실행 시 표시된 전체 주소를 열어주세요.");
    const result = await api("/api/bootstrap"); await verifyReceipt(result.state);
    packet = result.packet; scopes = result.scopes; receipt = result.state;
    data = clone(receipt.data); savedText = stable(data);
    backupKey = `pnu-durable-review:v1:${location.host}:${data.labels.packet_sha256}`;
    // Read before ANY write. A read/parse failure must never seed an empty form over old input.
    recoveryRaw = localStorage.getItem(backupKey);
    if (recoveryRaw !== null) {
      try {
        recoverable = JSON.parse(recoveryRaw);
        if (recoverable.packet_sha256 !== data.labels.packet_sha256 || !recoverable.data?.scope_reviews || !Array.isArray(recoverable.data?.labels?.reviews)) throw Error("임시 입력의 형식 또는 문항 연결이 다릅니다.");
      } catch (error) {
        recoverable = null; blocked = true; $("use-draft").disabled = true;
        $("recovery-note").textContent = `기존 브라우저 입력을 해석하지 못했습니다: ${error.message}. 원본은 그대로 보관했습니다. 긴급 텍스트를 먼저 복사해주세요.`;
        $("recovery-preview").textContent = recoveryRaw; $("recovery").classList.remove("hidden");
        throw Error("기존 임시 입력 확인 실패 — 빈 값으로 덮어쓰지 않고 입력을 잠갔습니다.");
      }
      if (stable(recoverable.data) !== savedText) {
        blocked = true; render(); $("workspace").classList.remove("hidden");
        $("recovery-note").textContent = `브라우저에 서버 버전 ${receipt.revision}과 다른 임시 입력이 있습니다. 자동 덮어쓰기는 하지 않습니다. 아래 입력을 확인한 뒤 복원 여부를 선택해주세요.`;
        $("recovery-preview").textContent = preview(recoverable.data); $("recovery").classList.remove("hidden"); status("임시 입력 복원 확인이 필요합니다.", "error"); return;
      }
    }
    // Verify storage availability using a separate disposable key, never by replacing the backup.
    const check = `${backupKey}:check:${crypto.randomUUID()}`;
    localStorage.setItem(check, "ok"); if (localStorage.getItem(check) !== "ok") throw Error("브라우저 임시 백업 사용 불가"); localStorage.removeItem(check);
    activate(receipt);
  } catch (error) { blocked = true; enable(false); status(`입력을 시작하지 마세요: ${error.message}`, "error"); $("recovery").classList.remove("hidden"); if (!recoverable) $("use-draft").disabled = true; }
}
boot();
