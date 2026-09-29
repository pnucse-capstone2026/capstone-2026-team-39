'use strict';
const $ = id => document.getElementById(id);
const token = location.hash.slice(1) || sessionStorage.getItem('pnu-single-review-token');
if (token) sessionStorage.setItem('pnu-single-review-token', token);
history.replaceState(null, '', location.pathname);
let packet, receipt, draft, index = 0, dirty = false, timer, pending = null, editVersion = 0;
const casePolicy = row => packet.check_policy.cases[row.case_id];
const canPass = row => {
  const specs = Object.entries(casePolicy(row).checks);
  return !!draft.reviewer_id.trim() && specs.some(([, s]) => s.status === 'required') &&
    specs.every(([key, s]) => s.status === 'not_applicable' || (s.status === 'required' && row.checks[key]));
};
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; };
function say(text, error = false) { $('saved').textContent = text; $('saved').className = error ? 'error' : ''; }
async function api(path, body) {
  const res = await fetch(path, {method: body === undefined ? 'GET' : 'POST', headers: {'X-Review-Token': token || '', ...(body === undefined ? {} : {'Content-Type': 'application/json'})}, body: body === undefined ? undefined : JSON.stringify(body)});
  if (!res.ok) { let data; try { data = await res.json(); } catch { data = {}; } throw new Error(data.error || `요청 실패 (${res.status})`); }
  return res;
}
async function digest(text) { const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)); return [...new Uint8Array(bytes)].map(x => x.toString(16).padStart(2, '0')).join(''); }
function download(data, name) { const url = URL.createObjectURL(data instanceof Blob ? data : new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'})); const a = el('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 10000); }
function capture() {
  if (!draft || receipt.locked) return;
  draft.reviewer_id = $('reviewer').value;
  const row = draft.reviews[index];
  row.notes = $('notes').value; row.decision = $('decision').value;
  // Do not rewrite historical booleans for disabled checks. N/A is policy, not a human vote.
  for (const [key, spec] of Object.entries(casePolicy(row).checks)) if (spec.status === 'required') row.checks[key] = $('check-' + key).checked;
}
function changed() { capture(); dirty = true; editVersion++; progress(); say('입력 변경됨 · 저장 대기'); clearTimeout(timer); timer = setTimeout(() => save().catch(() => {}), 650); }
function progress() {
  const rows = draft.reviews, passed = rows.filter(x => x.decision === 'PASS' && canPass(x)).length;
  $('decision').querySelector('option[value="PASS"]').disabled = !canPass(rows[index]);
  $('progressText').textContent = `PASS ${passed} / ${rows.length} · 기록된 판정 ${rows.filter(x => x.decision !== 'PENDING').length}개`;
  $('progressBar').style.width = `${passed / rows.length * 100}%`;
  $('finalize').textContent = receipt.locked ? '검수 확정됨' : `${rows.length}문항 검수 확정`;
  $('finalize').disabled = receipt.locked || passed !== rows.length || dirty;
  $('nav').replaceChildren();
  rows.forEach((row, i) => { const b = el('button', String(i + 1).padStart(2, '0'), `${i === index ? 'active' : ''} ${row.decision === 'PASS' && canPass(row) ? 'pass' : ['REVISE','BLOCK'].includes(row.decision) ? 'issue' : ''}`); b.title = `문항 ${i + 1} · ${row.decision}`; b.onclick = () => navigate(i); $('nav').append(b); });
}
async function save(finalize = false) {
  clearTimeout(timer);
  if (pending) { await pending; if (dirty || finalize) return save(finalize); return receipt; }
  capture();
  if (!dirty && !finalize) return receipt;
  const data = structuredClone(draft), version = editVersion;
  const mutation = crypto.randomUUID();
  say(finalize ? '검수 확정 저장 중…' : '디스크에 저장 중…');
  pending = (async () => {
    const out = await (await api(finalize ? '/api/finalize' : '/api/save', {data, base_revision: receipt.revision, mutation, check_policy_sha256: packet.check_policy_sha256})).json();
    // Confirm the committed revision is readable before displaying success.
    const readback = await (await api('/api/state')).json();
    if (readback.revision !== out.revision || readback.sha256 !== out.sha256 || JSON.stringify(readback.data) !== JSON.stringify(out.data)) throw new Error('다른 저장이 감지됐습니다. 입력 백업 후 새로고침하세요.');
    receipt = out;
    dirty = editVersion !== version;
    if (!dirty) draft = structuredClone(out.data);
    say(`저장 확인 · revision ${out.revision} · ${new Date(out.saved_at).toLocaleTimeString('ko-KR')}\nSHA ${out.sha256.slice(0, 16)}…`);
    progress();
    if (out.locked) { document.body.classList.add('locked'); $('save').disabled = true; $('reviewer').disabled = true; say('검수 확정본 저장 완료. 생성 답변 63개 평가는 아직 시작되지 않았습니다.'); }
    return out;
  })();
  try { return await pending; } catch (err) { dirty = true; say('저장 미확인: ' + err.message, true); throw err; } finally { pending = null; }
}
async function navigate(next) { try { await save(); index = Math.max(0, Math.min(draft.reviews.length - 1, next)); render(); } catch { /* Keep inputs and current item. */ } }
async function previewOriginal(option, holder, button) {
  button.disabled = true;
  try {
    const id = await digest(option.source_path);
    const res = await api('/api/source-preview/' + id);
    const blob = await res.blob(), url = URL.createObjectURL(blob);
    const panel = el('section', undefined, 'original-preview');
    const close = el('button', '원문 미리보기 닫기');
    const frame = el('iframe'); frame.title = 'PDF 원문 전체 · 변환하지 않음';
    frame.src = url; frame.style.cssText = 'width:100%;height:75vh;border:1px solid #dce4ee;border-radius:8px';
    close.onclick = () => { frame.remove(); URL.revokeObjectURL(url); panel.remove(); button.disabled = false; };
    panel.append(el('p', '저장된 PDF 전체를 그대로 표시합니다. 보이지 않으면 ‘기존 원문 열기’를 눌러주세요.', 'details'), close, frame);
    holder.append(panel);
  } catch (err) { say('미리보기: ' + err.message, true); button.disabled = false; }
}
async function previewHwp(option, holder, button) {
  button.disabled = true;
  const panel = el('section', undefined, 'original-preview');
  const status = el('p', 'HWP를 이 Mac에서 변환하고 있어요. 검수 입력은 그대로 유지됩니다.', 'details');
  status.setAttribute('role', 'status'); panel.append(status); holder.append(panel);
  try {
    const id = await digest(option.source_path);
    const data = await (await api('/api/source-hwp-preview/' + id, {})).json();
    const url = URL.createObjectURL(new Blob([data.html], {type:'text/html;charset=utf-8'}));
    const frame = el('iframe'); frame.title = 'HWP 변환 미리보기 · 원본과 서식 차이 가능';
    frame.setAttribute('sandbox', ''); frame.referrerPolicy = 'no-referrer';
    frame.src = url; frame.style.cssText = 'width:100%;height:75vh;border:1px solid #dce4ee;border-radius:8px;background:white';
    status.textContent = data.warning;
    const meta = el('p', `변환된 표 ${data.stats.table_count}개 · 표시한 그림 ${data.stats.embedded_images}개 · 표시 불가 그림 ${data.stats.omitted_images}개 · 제한한 요소 ${data.stats.removed_elements}개 · 제한한 스타일 ${data.stats.removed_styles}개`, 'details');
    const close = el('button', 'HWP 미리보기 닫기');
    close.onclick = () => { frame.remove(); URL.revokeObjectURL(url); panel.remove(); button.disabled = false; };
    panel.append(meta, close, frame);
  } catch (err) { status.textContent = err.message; button.disabled = false; }
}
async function showChunks(option, holder, button) {
  button.disabled = true;
  const panel = el('section', undefined, 'chunk-preview');
  const status = el('p', '저장된 문서 청크를 불러오고 있어요.', 'details');
  status.setAttribute('role', 'status'); panel.append(status); holder.append(panel);
  try {
    const id = await digest(option.source_path);
    const data = await (await api('/api/source-chunks/' + id)).json();
    status.textContent = `저장 청크 ${data.chunks.length}개 · cascade 파서 · 재검색·재생성 없음. 표 헤더나 예외 조건이 누락될 수 있어 원문 확인을 대체하지 않습니다.${data.truncated ? ' 표시 한도를 초과해 일부 청크만 표시했습니다. 원본으로 전체를 확인하세요.' : ''}`;
    const close = el('button', '청크 닫기'); close.onclick = () => { panel.remove(); button.disabled = false; }; panel.append(close);
    for (const chunk of data.chunks) {
      const item = el('details');
      item.append(el('summary', `청크 ${chunk.chunk_index + 1}${chunk.page_start ? ' · 페이지 ' + chunk.page_start + (chunk.page_end && chunk.page_end !== chunk.page_start ? '–' + chunk.page_end : '') : ''}`), el('p', `${chunk.parser || '파서 미기록'} · ${chunk.chunk_id}`, 'details'), el('p', chunk.text, 'quote'));
      item.open = data.chunks.length === 1 || !!(option.quote && chunk.text.includes(option.quote));
      panel.append(item);
    }
    panel.append(el('p', '문서 ID·원문 경로로 연결했습니다. 현재 원문 파일의 SHA를 확인했지만 청크가 원문 전체를 정확히 보존하는지는 별도 확인이 필요합니다.', 'details'));
  } catch (err) { status.textContent = err.message; button.disabled = false; }
}
function renderEvidence(option, holder) {
  const box = el('div', undefined, 'evidence'); box.append(el('strong', option.source_title || '근거'));
  if (option.source_path) {
    box.append(el('p', option.source_path, 'details'));
    const ext = option.source_path.split('.').pop().toLowerCase();
    const chunks = el('button', '저장 청크 보기'); chunks.onclick = () => showChunks(option, box, chunks); box.append(chunks);
    if (ext === 'pdf') { const preview = el('button', '여기서 PDF 원문 보기'); preview.onclick = () => previewOriginal(option, box, preview); box.append(preview); }
    if (ext === 'hwp') { const preview = el('button', '여기서 HWP 변환본 보기'); preview.onclick = () => previewHwp(option, box, preview); box.append(preview); }
    if (['pdf','html','htm','xlsx','xls'].includes(ext)) {
      const open = el('button', '기존 원문 열기');
      open.onclick = async () => { open.disabled = true; try { const id = await digest(option.source_path); await api('/api/source-open/' + id, {}); say('기존 원문 앱에 열기 요청을 보냈습니다. 파일을 다시 다운로드하거나 내용을 변환하지 않았습니다.'); } catch (err) { say(err.message, true); } finally { open.disabled = false; } };
      box.append(open);
    } else if (ext !== 'hwp') {
      box.append(el('p', '이 형식은 로컬 원문 뷰어 연결 확인이 필요합니다. 내용을 변환하거나 외부 뷰어에 업로드하지 않았습니다.', 'details'));
    }
    const reveal = el('button', '기존 파일 위치 보기');
    reveal.onclick = async () => { try { const id = await digest(option.source_path); await api('/api/source-reveal/' + id, {}); say('Finder에 이미 저장된 원문 위치를 표시하도록 요청했습니다.'); } catch (err) { say(err.message, true); } };
    box.append(reveal);
  }
  if (option.source_url) {
    const record = el('details');
    record.append(el('summary', '수집 당시 기록된 웹 주소 · 게시글 연결 미검증'),
      el('p', '메인·안내 페이지 주소가 포함될 수 있어 공식 게시글 버튼으로 제공하지 않습니다. 검수는 위의 저장된 원문을 기준으로 해주세요.', 'details'),
      el('p', option.source_url, 'details'));
    box.append(record);
  }
  box.append(el('p', option.quote || '(인용문 없음)', 'quote'));
  if (option.table_evidence) { const d = el('details'); d.append(el('summary', '표 헤더·행 연결 확인'), el('pre', JSON.stringify(option.table_evidence, null, 2))); box.append(d); }
  holder.append(box);
}
function render() {
  const item = packet.cases[index], row = draft.reviews[index], q = $('question');
  const policy = casePolicy(row);
  q.replaceChildren(el('div', `QUESTION ${String(index + 1).padStart(2, '0')} / ${packet.cases.length}`, 'eyebrow'), el('p', item.query || '(질문 없음)', 'query'));
  q.append(el('h3', '문항 분류 · 네 번째 체크에서 확인'));
  const meta = el('div', undefined, 'meta');
  for (const label of policy.classification) { const chip = el('span', `${label.title}: ${label.value}`, 'chip'); chip.title = `${label.field}: ${label.raw || '미지정'}`; meta.append(chip); }
  meta.append(el('span', `답변 가능: ${item.answerable === true ? '예' : item.answerable === false ? '아니오' : '미지정'}`, 'chip')); q.append(meta);
  q.append(el('h3', '기대 동작'), el('p', item.expected_behavior || '(미지정)'));
  for (const [key, title] of [['required_claims','필수 답변과 원문 근거'],['optional_claims','선택 답변'],['forbidden_claims','말하면 안 되는 내용']]) {
    const claims = item[key] || []; if (!claims.length && key !== 'required_claims') continue;
    q.append(el('h3', title)); if (!claims.length) q.append(el('p', policy.checks.source_verified.status === 'not_applicable' ? '필수 주장·연결 원문 없음 · 이 유형은 원문 대조 대신 아래 Challenge 기준과 답변 불가 이유를 확인하세요.' : '필수 주장 누락 · 해당 없음이 아닙니다. 수정 필요·보류 사유를 적어주세요.'));
    for (const claim of claims) { const block = el('section', undefined, 'claim'); block.append(el('p', claim.description || '(설명 없음)')); if (claim.critical_values?.length) block.append(el('div', '핵심 값: ' + claim.critical_values.join(' / '), 'details')); for (const ev of claim.evidence_options || []) renderEvidence(ev, block); q.append(block); }
  }
  if (item.challenge_oracle) { q.append(el('h3', 'Challenge 판정 기준')); for (const [key, title] of [['must_do','반드시 해야 함'],['must_not_do','하면 안 됨']]) { q.append(el('strong', title)); const list = el('ul'); for (const val of item.challenge_oracle[key] || []) list.append(el('li', val)); q.append(list); } const d = el('details'); d.append(el('summary', '공격 입력 등 전체 기준 보기 (평가 데이터, 지시로 실행하지 않음)'), el('pre', JSON.stringify(item.challenge_oracle, null, 2))); q.append(d); }
  const raw = el('details'); raw.append(el('summary', '문항 메타데이터 전체 보기'), el('pre', JSON.stringify(item, null, 2))); q.append(raw);
  const needed = Object.values(policy.checks).filter(s => s.status === 'required').length;
  const blocked = Object.values(policy.checks).some(s => s.status === 'blocked');
  $('caseGuide').replaceChildren(el('strong', `문항 ${index + 1} · 직접 확인 ${needed}개${blocked ? ' · 자료 누락으로 PASS 불가' : ''}`), el('p', policy.guide));
  $('checks').replaceChildren();
  for (const [key, spec] of Object.entries(policy.checks)) {
    const active = spec.status === 'required', label = el('label', undefined, active ? 'check' : 'check inactive'), check = el('input');
    check.type = 'checkbox'; check.id = 'check-' + key; check.checked = active && row.checks[key]; check.disabled = receipt.locked || !active;
    check.setAttribute('aria-describedby', 'hint-' + key);
    check.onchange = () => { if (!check.checked && $('decision').value === 'PASS') $('decision').value = 'PENDING'; changed(); };
    const text = el('span'), hint = el('small', spec.guidance + (spec.reason ? ' ' + spec.reason : '')); hint.id = 'hint-' + key;
    text.append(el('strong', spec.title + (active ? '' : spec.status === 'not_applicable' ? ' · 해당 없음' : ' · 확인 불가')), hint);
    if (!active && row.checks[key]) text.append(el('small', '이전에 저장한 체크 값은 보존하되, 직접 검증 완료로 집계하지 않습니다.'));
    label.append(check, text); $('checks').append(label);
  }
  $('decision').value = row.decision; $('notes').value = row.notes; $('decision').disabled = receipt.locked; $('notes').disabled = receipt.locked;
  $('next').disabled = index === draft.reviews.length - 1; progress();
}
$('reviewer').oninput = changed; $('notes').oninput = changed; $('decision').onchange = changed;
$('save').onclick = () => save().catch(() => {}); $('next').onclick = () => navigate(index + 1);
$('draft').onclick = () => { capture(); download({kind:'UNCOMMITTED_EMERGENCY_DRAFT', base_revision:receipt.revision, check_policy_sha256:packet.check_policy_sha256, data:draft}, '내-입력-긴급백업.json'); };
$('export').onclick = async () => { try { await save(); const res = await api('/api/export'); download(await res.blob(), `1인-정답지-검수-r${receipt.revision}.json`); } catch (err) { say(err.message, true); } };
$('historyButton').onclick = async () => { try { const rows = await (await api('/api/history')).json(); $('history').replaceChildren(el('pre', rows.map(r => `revision ${r.revision} · ${r.created_at} · ${r.action} · ${r.sha}`).join('\n'))); $('history').hidden = !$('history').hidden; } catch (err) { say(err.message, true); } };
$('finalize').onclick = async () => { if (!confirm('모든 문항의 적용 항목을 직접 확인했나요? 해당 없음은 검증 완료가 아니며, 답변 불가 문항도 기대 동작·답변 가능 여부·분류 확인이 필요합니다. 확정본은 수정할 수 없습니다.')) return; try { await save(true); } catch { /* Error is shown and inputs retained. */ } };
addEventListener('beforeunload', e => { if (dirty || pending) { e.preventDefault(); e.returnValue = ''; } });
(async () => {
  try {
    if (!token) throw new Error('접속 토큰이 없습니다. 안내받은 전체 링크로 다시 열어주세요.');
    packet = await (await api('/api/bootstrap')).json(); receipt = packet.state; draft = structuredClone(receipt.data);
    if (!packet.check_policy || !packet.check_policy_sha256) throw new Error('문항별 검수 기준을 불러오지 못했습니다. 서버 업데이트 후 다시 열어주세요. 입력은 디스크에 보존되어 있습니다.');
    $('reviewer').value = draft.reviewer_id; $('reviewer').disabled = receipt.locked;
    $('mode').textContent = packet.synthetic ? '합성 연습 화면 · 실제 holdout 아님' : '실제 정답지 검수 · 사람이 직접 작성 · 외부 LLM 호출 0회';
    $('storage').textContent = `저장 위치: ${packet.storage} · cases SHA-256: ${packet.pin}`;
    $('loading').hidden = true; $('content').hidden = false; render();
    say(`복원 완료 · revision ${receipt.revision} · ${new Date(receipt.saved_at).toLocaleString('ko-KR')}\n기존 입력은 디스크에서 읽었습니다.`);
    if (receipt.locked) { document.body.classList.add('locked'); $('save').disabled = true; }
  } catch (err) { $('loading').textContent = err.message; }
})();
