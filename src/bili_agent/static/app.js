const state = { sessionId: null, result: null, sessions: [], sessionData: {}, infographicUrl: null, infographicFilename: 'bili-infographic.svg' };
const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (char) => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;'}[char]));
}

function formatDuration(seconds) {
  const total = Math.max(0, Number(seconds || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = Math.floor(total % 60);
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function modalityLabel(modality) {
  return ({cc: 'CC', asr: 'ASR', ocr: 'OCR', vision: '视觉', audio_event: '音频', metadata: '元数据'})[modality] || modality;
}

function coverPlaceholder(metadata) {
  const title = String(metadata.title || '视频');
  const initials = Array.from(title.replace(/\s+/g, '')).slice(0, 2).join('') || '视频';
  return `<div class="placeholder-cover"><span>${escapeHtml(initials)}</span><small>封面暂不可用</small></div>`;
}

function toast(message) {
  const node = $('toast');
  node.textContent = message;
  node.classList.add('show');
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => node.classList.remove('show'), 2800);
}

async function apiFetch(input, options = {}) {
  const {authRetry = false, ...fetchOptions} = options;
  const token = window.sessionStorage.getItem('bili-agent-web-token') || '';
  const headers = new Headers(fetchOptions.headers || {});
  if (token) headers.set('Authorization', `Bearer ${token}`);
  let response = await fetch(input, {...fetchOptions, headers});
  if (response.status !== 401 || authRetry) return response;
  const nextToken = window.prompt('请输入面板访问 Token');
  if (!nextToken) return response;
  window.sessionStorage.setItem('bili-agent-web-token', nextToken.trim());
  return apiFetch(input, {...options, authRetry: true});
}

async function openSettings() {
  $('settings-modal').classList.remove('hidden');
  $('settings-status').textContent = '正在读取当前配置…';
  try {
    const response = await apiFetch('/api/settings', {cache: 'no-store'});
    const settings = await response.json();
    if (!response.ok) throw new Error(settings.error || '配置读取失败');
    $('llm-api-key').value = '';
    $('llm-api-key').placeholder = settings.llm_configured ? `已配置 ${settings.llm_api_key_masked}，留空保持不变` : '输入 API Key';
    $('llm-base-url').value = settings.llm_base_url || '';
    $('llm-model').value = settings.llm_model || '';
    $('clear-api-key').checked = false;
    $('settings-status').textContent = settings.llm_configured ? '当前已配置 LLM' : '当前未配置 LLM，分析会使用降级模式';
  } catch (error) { $('settings-status').textContent = error.message; }
}

function closeSettings() { $('settings-modal').classList.add('hidden'); }

async function saveSettings(event) {
  event.preventDefault();
  const button = document.querySelector('.save-settings');
  button.disabled = true;
  try {
    const response = await apiFetch('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
      llm_api_key: $('llm-api-key').value,
      llm_base_url: $('llm-base-url').value,
      llm_model: $('llm-model').value,
      clear_api_key: $('clear-api-key').checked,
    })});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || '配置保存失败');
    closeSettings();
    toast(payload.llm_configured ? 'LLM 配置已保存并生效' : '配置已保存，当前使用降级模式');
  } catch (error) { $('settings-status').textContent = error.message; }
  finally { button.disabled = false; }
}

function closeInfographic() {
  $('infographic-modal').classList.add('hidden');
  if (state.infographicUrl) {
    URL.revokeObjectURL(state.infographicUrl);
    state.infographicUrl = null;
  }
  $('infographic-image').removeAttribute('src');
}

async function generateInfographic() {
  if (!state.sessionId) return toast('请先完成一次分析');
  const button = $('generate-infographic');
  button.disabled = true;
  button.classList.add('busy');
  try {
    const response = await apiFetch('/api/infographic', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({session_id: state.sessionId})});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || '一图流生成失败');
    if (state.infographicUrl) URL.revokeObjectURL(state.infographicUrl);
    state.infographicUrl = URL.createObjectURL(new Blob([payload.svg], {type: 'image/svg+xml;charset=utf-8'}));
    state.infographicFilename = payload.filename || 'bili-infographic.svg';
    $('infographic-image').src = state.infographicUrl;
    $('infographic-modal').classList.remove('hidden');
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; button.classList.remove('busy'); }
}

function setBusy(button, busy, label) {
  button.disabled = busy;
  button.classList.toggle('busy', busy);
  if (label) button.querySelector('span:last-child').textContent = busy ? '处理中…' : label;
}

function addMessage(role, text, sources = [], skill = '') {
  let list = $('message-list');
  if (!list) {
    $('welcome-block')?.remove();
    list = document.createElement('div');
    list.id = 'message-list';
    list.className = 'message-list';
    $('conversation').appendChild(list);
  }
  const message = document.createElement('div');
  message.className = `message ${role}`;
  const label = role === 'user' ? '' : '<div class="message-label">AI</div>';
  const skillHtml = skill ? `<div class="answer-skill">${escapeHtml(({metadata:'元数据查询',summary:'总结理解',timeline:'时间线整理',knowledge:'知识点提取',transcript:'字幕检索',evidence_qa:'证据问答'})[skill] || skill)}</div>` : '';
  const sourceHtml = sources.length ? `<div class="source-list">${sources.map((source) => `<span class="source-chip">${escapeHtml(modalityLabel(source.modality))} · ${escapeHtml(source.page_title)} · ${formatDuration(source.start)}</span>`).join('')}</div>` : '';
  message.innerHTML = `${label}<div class="message-bubble">${skillHtml}${escapeHtml(text)}${sourceHtml}</div>`;
  list.appendChild(message);
  $('conversation').scrollTop = $('conversation').scrollHeight;
}

function renderDetails(result) {
  const metadata = result.metadata;
  $('crumb-title').textContent = metadata.title;
  $('composer-hint').textContent = '继续追问，答案会基于时间线证据并标注来源';
  $('composer-form').classList.add('hidden');
  $('question-form').classList.remove('hidden');
  $('video-card').className = 'video-card';
  const usablePic = metadata.pic && !/transparent\.png/i.test(metadata.pic);
  const cover = usablePic ? `<img src="/api/cover?url=${encodeURIComponent(metadata.pic)}" alt="视频封面">` : coverPlaceholder(metadata);
  $('video-card').innerHTML = `<div class="video-cover">${cover}</div><div class="video-title">${escapeHtml(metadata.title)}</div>`;
  if (usablePic) {
    const image = $('video-card').querySelector('img');
    image?.addEventListener('error', () => {
      const wrapper = image.closest('.video-cover');
      if (wrapper) wrapper.innerHTML = coverPlaceholder(metadata);
    }, {once: true});
  }
  $('metadata-block').classList.remove('hidden');
  $('metadata-block').innerHTML = `<div><div class="meta-label">UP主</div><div class="meta-value">${escapeHtml(metadata.author)}</div></div><div><div class="meta-label">分P</div><div class="meta-value">${metadata.pages.length} 个</div></div><div><div class="meta-label">总时长</div><div class="meta-value">${formatDuration(metadata.duration_seconds)}</div></div><div><div class="meta-label">状态</div><div class="meta-value">${result.degraded ? '降级模式' : '已完成'}</div></div>`;
  $('summary-text').classList.remove('muted');
  $('summary-text').textContent = result.summary.overall_summary;
  $('chapter-count').textContent = result.summary.chapters.length;
  $('chapter-list').innerHTML = result.summary.chapters.length ? result.summary.chapters.map((chapter) => `<div class="chapter-item"><span class="chapter-time">${escapeHtml(chapter.timestamp)}</span><span class="chapter-title">${escapeHtml(chapter.title)}</span></div>`).join('') : '<div class="muted">暂无章节信息</div>';
  const timeline = result.timeline || [];
  $('evidence-count').textContent = timeline.length;
  $('evidence-list').innerHTML = timeline.length ? timeline.slice(0, 80).map((item) => `<div class="evidence-item"><div class="evidence-head"><span class="evidence-modality ${escapeHtml(item.modality)}">${escapeHtml(modalityLabel(item.modality))}</span><span class="evidence-time">${formatDuration(item.start)}</span></div><div class="evidence-text">${escapeHtml(item.content)}</div></div>`).join('') : '<div class="muted">暂无多模态证据</div>';
}

async function analyze(video) {
  const button = $('analyze-button');
  setBusy(button, true, '开始分析');
  try {
    const response = await apiFetch('/api/analyze', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({video, enable_asr: $('asr-enabled').checked, enable_multimodal: $('multimodal-enabled').checked}) });
    const created = await response.json();
    if (!response.ok) throw new Error(created.error || '分析失败');
    const payload = await waitForJob(created.job_id);
    if (payload.status !== 'completed') throw new Error(payload.message || '分析失败');
    const resultPayload = {session_id: payload.session_id, result: payload.result, markdown: payload.markdown};
    renderAnalysis(resultPayload);
  } catch (error) { toast(error.message); }
  finally { setBusy(button, false, '开始分析'); }
}

async function waitForJob(jobId) {
  let lastMessage = '';
  while (true) {
    const response = await apiFetch(`/api/jobs/${encodeURIComponent(jobId)}`, {cache: 'no-store'});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || '任务状态获取失败');
    if (payload.message && payload.message !== lastMessage) {
      lastMessage = payload.message;
      $('composer-hint').textContent = `${payload.message}${payload.progress ? ` · ${payload.progress}%` : ''}`;
    }
    if (payload.status === 'completed' || payload.status === 'failed') return payload;
    await new Promise((resolve) => window.setTimeout(resolve, 700));
  }
}

function renderAnalysis(payload) {
    state.sessionId = payload.session_id;
    state.result = payload.result;
    window.lastMarkdown = payload.markdown;
    state.sessionData[state.sessionId] = payload;
    state.sessions = [{id: state.sessionId, title: state.result.metadata.title}, ...state.sessions.filter((session) => session.id !== state.sessionId)];
    renderSessions();
    renderDetails(state.result);
    addMessage('assistant', state.result.summary.overall_summary, [], 'summary');
    const notices = state.result.pages.filter((page) => page.transcript.source === 'none' && page.transcript.notice).map((page) => `P${page.page.page_index + 1}：${page.transcript.notice}`);
    if (notices.length) addMessage('assistant', `字幕未覆盖的分P：\n${notices.join('\n')}`);
    toast(state.result.degraded ? '分析完成，当前使用降级总结' : '分析完成');
}

async function ask(question) {
  if (!state.sessionId) return toast('请先输入视频链接');
  addMessage('user', question);
  $('question-input').value = '';
  try {
    const response = await apiFetch('/api/ask', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({session_id: state.sessionId, question}) });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || '问答失败');
    addMessage('assistant', payload.answer.answer, payload.answer.sources || [], payload.answer.skill || 'evidence_qa');
  } catch (error) { addMessage('assistant', `处理失败：${error.message}`); }
}

function renderSessions() {
  const list = $('session-list');
  if (!state.sessions.length) return;
  list.innerHTML = state.sessions.map((session) => `<button class="session-item ${session.id === state.sessionId ? 'active' : ''}" data-session-id="${escapeHtml(session.id)}" title="${escapeHtml(session.title)}">${escapeHtml(session.title)}</button>`).join('');
  list.querySelectorAll('.session-item').forEach((button) => button.addEventListener('click', () => switchSession(button.dataset.sessionId)));
}

function switchSession(sessionId) {
  const payload = state.sessionData[sessionId];
  if (!payload) return;
  state.sessionId = sessionId;
  state.result = payload.result;
  window.lastMarkdown = payload.markdown;
  $('conversation').innerHTML = '<div class="message-list" id="message-list"></div>';
  renderDetails(state.result);
  addMessage('assistant', state.result.summary.overall_summary, [], 'summary');
  renderSessions();
}

function resetSession() {
  state.sessionId = null; state.result = null;
  $('conversation').innerHTML = `<div class="welcome-block" id="welcome-block"><div class="welcome-kicker">VIDEO RESEARCH DESK</div><h1>把一个视频，<br><em>变成你的知识库。</em></h1><p>输入一个链接，开始这段视频研究。</p><div class="starter-row"><button class="starter" data-question="这个视频主要讲了什么？">概括这个视频 <span>↗</span></button><button class="starter" data-question="视频中最重要的三个知识点是什么？">提取知识点 <span>↗</span></button></div></div>`;
  $('composer-form').classList.remove('hidden'); $('question-form').classList.add('hidden'); $('video-input').value = ''; $('crumb-title').textContent = '新会话'; $('composer-hint').textContent = '先输入视频链接，建立分析会话';
  $('video-card').className = 'video-card empty-card'; $('video-card').innerHTML = '<div class="video-cover placeholder-cover"><span>BV</span></div><div class="empty-title">等待一个视频</div><div class="empty-copy">分析完成后，这里会显示视频信息和章节。</div>';
  $('metadata-block').classList.add('hidden'); $('summary-text').className = 'summary-text muted'; $('summary-text').textContent = '暂无内容'; $('chapter-count').textContent = '0'; $('chapter-list').innerHTML = '<div class="muted">分析后显示章节</div>'; $('evidence-count').textContent = '0'; $('evidence-list').innerHTML = '<div class="muted">分析后显示字幕、ASR、OCR 和视觉证据</div>'; renderSessions(); bindStarters();
}

function bindStarters() { document.querySelectorAll('.starter').forEach((button) => button.addEventListener('click', () => { if (!state.sessionId) return toast('请先输入视频链接'); ask(button.dataset.question); })); }

$('composer-form').addEventListener('submit', (event) => { event.preventDefault(); const value = $('video-input').value.trim(); if (value) analyze(value); else toast('请先输入视频链接'); });
$('question-form').addEventListener('submit', (event) => { event.preventDefault(); const value = $('question-input').value.trim(); if (value) ask(value); });
$('new-session').addEventListener('click', resetSession);
$('settings-button').addEventListener('click', openSettings);
$('close-settings').addEventListener('click', closeSettings);
$('cancel-settings').addEventListener('click', closeSettings);
$('settings-form').addEventListener('submit', saveSettings);
$('generate-infographic').addEventListener('click', generateInfographic);
$('close-infographic').addEventListener('click', closeInfographic);
$('close-infographic-secondary').addEventListener('click', closeInfographic);
$('download-infographic').addEventListener('click', () => {
  if (!state.infographicUrl) return toast('请先生成一图流');
  const link = document.createElement('a');
  link.href = state.infographicUrl;
  link.download = state.infographicFilename;
  link.click();
});
$('toggle-inspector').addEventListener('click', () => $('inspector').classList.toggle('hidden'));
$('close-inspector').addEventListener('click', () => $('inspector').classList.add('hidden'));
$('download-note').addEventListener('click', () => { if (!state.result) return toast('请先完成一次分析'); const blob = new Blob([state.result ? window.lastMarkdown || `# ${state.result.metadata.title}\n\n${state.result.summary.overall_summary}` : ''], {type: 'text/markdown;charset=utf-8'}); const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = `${state.result.metadata.title || 'bili-note'}.md`; link.click(); URL.revokeObjectURL(link.href); });
bindStarters();
