/* ===== DOM 引用 ===== */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const form          = $('#generate-form');
const submitBtn     = $('#submit-btn');
const resetButton   = $('#reset-button');
const exportRow     = $('#export-row');
const inputSection  = $('#input-section');
const outlineSection = $('#outline-section');
const scriptSection = $('#script-section');
const resultSection = $('#result-section');
const stepsBar      = $$('.steps-bar .step');
const stepLines     = $$('.steps-bar .step-line');

let activeJobId = null;
let pollTimer   = null;
let logCount    = 0;
let currentScriptText = '';
let selectedEditScene = null;
let currentResultMeta = null;
let currentOutline = null;
let currentUserPayload = null;

/* ===== 风格标签选择器（分类） ===== */
const STYLE_CATEGORIES = [
  { label: '热门题材', items: ['霸总', '甜宠', '重生', '复仇', '穿书', '穿越', '古风逆袭', '闪婚', '替嫁', '豪门恩怨'] },
  { label: '悬疑 / 惊悚', items: ['都市悬疑', '悬疑推理', '惊悚', '谍战', '犯罪', '密室逃脱', '心理博弈', '法医探案'] },
  { label: '古风 / 仙侠', items: ['仙侠', '修仙', '玄幻', '武侠', '宫斗', '权谋', '妖怪志', '江湖恩仇', '神话改编'] },
  { label: '现代都市', items: ['职场', '商战', '校园', '家庭伦理', '体育竞技', '医疗', '律政', '娱乐圈', '美食'] },
  { label: '特殊设定', items: ['科幻', '赛博朋克', '末世', '无限流', '年代', '军旅', '异能', '时间循环', '平行世界', '丧尸'] },
  { label: '情感基调', items: ['轻喜剧', '黑色幽默', '虐恋', '治愈', '暗黑', '热血', '催泪', '逆袭爽文', '双强对决'] },
];
const selectedStyles = new Set(['都市悬疑']);
const styleTagsEl = $('#style-tags');
const stylesHidden = $('#styles-hidden');

function renderStyleTags() {
  styleTagsEl.innerHTML = STYLE_CATEGORIES.map(cat =>
    `<div class="tag-category"><span class="tag-category-label">${cat.label}</span>${cat.items.map(s =>
      `<span class="tag-item${selectedStyles.has(s) ? ' selected' : ''}" data-style="${s}">${s}</span>`
    ).join('')}</div>`
  ).join('');
  stylesHidden.value = JSON.stringify([...selectedStyles]);
}
styleTagsEl.addEventListener('click', (e) => {
  const tag = e.target.closest('.tag-item');
  if (!tag) return;
  const style = tag.dataset.style;
  if (selectedStyles.has(style)) {
    selectedStyles.delete(style);
  } else {
    selectedStyles.add(style);
  }
  renderStyleTags();
});
renderStyleTags();

/* ===== 自定义下拉框 ===== */
const toneWrap    = $('#tone-select-wrap');
const toneTrigger = $('#tone-trigger');
const toneOptions = $('#tone-options');
const toneHidden  = $('#writing_tone');

toneTrigger.addEventListener('click', () => toneWrap.classList.toggle('open'));
toneTrigger.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toneWrap.classList.toggle('open'); } });
toneOptions.addEventListener('click', (e) => {
  const opt = e.target.closest('.option');
  if (!opt) return;
  toneOptions.querySelectorAll('.option').forEach(o => o.classList.remove('selected'));
  opt.classList.add('selected');
  toneTrigger.querySelector('.select-value').textContent = opt.dataset.value;
  toneHidden.value = opt.dataset.value;
  toneWrap.classList.remove('open');
});
document.addEventListener('click', (e) => {
  if (!toneWrap.contains(e.target)) toneWrap.classList.remove('open');
});

/* ===== 步骤条管理 ===== */
function setStep(stepName) {
  const order = ['input', 'outline-review', 'script-review', 'result'];
  const idx = order.indexOf(stepName);
  stepsBar.forEach((el, i) => {
    el.classList.remove('active', 'done');
    if (i < idx) el.classList.add('done');
    else if (i === idx) el.classList.add('active');
  });
  stepLines.forEach((line, i) => {
    line.classList.toggle('active', i < idx);
  });
}

/* ===== 区段切换（带动画） ===== */
const allSections = () => [inputSection, outlineSection, scriptSection, resultSection];
function showSection(sectionEl) {
  allSections().forEach(s => {
    if (s !== sectionEl && !s.classList.contains('hidden')) {
      s.classList.add('hidden');
    }
  });
  sectionEl.classList.remove('hidden');
  sectionEl.classList.remove('section-enter');
  void sectionEl.offsetWidth;
  sectionEl.classList.add('section-enter');
}

/* ===== 状态标签（支持指定目标） ===== */
function setStatus(badgeEl, status, text) {
  if (!badgeEl) return;
  badgeEl.className = `status ${status}`;
  badgeEl.textContent = text;
}

/* ===== 日志 ===== */
function detectAgent(msg) {
  if (/planner/i.test(msg)) return 'planner';
  if (/writer/i.test(msg)) return 'writer';
  if (/reviewer/i.test(msg)) return 'reviewer';
  if (/director/i.test(msg)) return 'director';
  if (/video/i.test(msg)) return 'video';
  return '';
}

function appendLog(logsEl, message) {
  const item = document.createElement('div');
  const agent = detectAgent(message);
  item.className = `log-item${agent ? ' ' + agent : ''}`;
  item.textContent = message;
  logsEl.appendChild(item);
  logsEl.scrollTop = logsEl.scrollHeight;
}

function updateAgentChips(containerEl, logs) {
  const chips = containerEl.querySelectorAll('.agent-chip');
  const activeAgents = new Set();
  const doneAgents = new Set();
  logs.forEach(log => {
    const msg = log.message || '';
    if (/planner/i.test(msg)) { activeAgents.add('planner'); if (/完成|失败/.test(msg)) doneAgents.add('planner'); }
    if (/writer/i.test(msg))  { activeAgents.add('writer');  if (/初稿|兜底|补尾|失败/.test(msg)) doneAgents.add('writer'); }
    if (/reviewer/i.test(msg)){ activeAgents.add('reviewer');if (/通过|完成|保留|失败/.test(msg)) doneAgents.add('reviewer'); }
    if (/director/i.test(msg)){ activeAgents.add('director');if (/输出|最终/.test(msg)) doneAgents.add('director'); }
  });
  chips.forEach(chip => {
    const a = chip.dataset.agent;
    chip.classList.remove('active', 'done');
    if (doneAgents.has(a)) chip.classList.add('done');
    else if (activeAgents.has(a)) chip.classList.add('active');
  });
}

function updateProgress(barEl, logs, isDone, total) {
  if (isDone) { barEl.style.width = '100%'; return; }
  total = total || 4;
  const pct = Math.min((logs.length / total) * 100, 95);
  barEl.style.width = pct + '%';
}

/* ===== HTML 转义 ===== */
function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/* ===== 解析剧本场景（支持多集） ===== */
function parseScenes(scriptText) {
  const raw = String(scriptText || '').trim();
  if (!raw) return [];
  const normalized = raw
    .replace(/\r\n/g, '\n')
    .replace(/^【场景\s*(\d+)】/gm, '场景$1')
    .replace(/^场景号\s*(\d+)/gm, '场景号：$1');
  const lines = normalized.split('\n');
  const results = [];
  let currentEp = 0;
  let segBuf = [];
  const epRe = /^第(\d+)集[：:]/;
  const sceneRe = /^(?:场景\s*\d+(?:\s|[:：]|$)|场景号[:：]?\s*\d+)/;
  const sepRe = /^={5,}/;

  function flushScene() {
    if (!segBuf.length) return;
    const meaningful = segBuf.filter(l => l.trim());
    if (!meaningful.length) { segBuf = []; return; }
    const text = segBuf.join('\n');
    const titleLine = meaningful.find(l => sceneRe.test(l.trim())) || meaningful[0];
    const numMatch = titleLine.match(/(?:场景\s*|场景号[:：]?\s*)(\d+)/);
    const sceneNum = numMatch ? parseInt(numMatch[1]) : 1;
    const compositeId = currentEp > 0 ? `${currentEp}.${sceneNum}` : String(sceneNum);
    const preview = meaningful.filter(l => !sceneRe.test(l.trim())).slice(0, 2).join(' ').substring(0, 80);
    results.push({ number: compositeId, episode: currentEp, sceneNum, title: titleLine.trim(), preview, raw: text });
    segBuf = [];
  }

  for (const line of lines) {
    const trimmed = line.trim();
    if (sepRe.test(trimmed)) { flushScene(); continue; }
    const epMatch = trimmed.match(epRe);
    if (epMatch) {
      flushScene();
      currentEp = parseInt(epMatch[1]);
      results.push({ number: `ep${currentEp}`, episode: currentEp, sceneNum: 0, title: trimmed, preview: '', raw: line, isEpisodeHeader: true });
      continue;
    }
    if (sceneRe.test(trimmed) && segBuf.length) { flushScene(); }
    segBuf.push(line);
  }
  flushScene();
  return results;
}

/* ===== 剧本渲染（共享逻辑） ===== */
function renderScriptInto(container, scriptText) {
  const raw = String(scriptText || '').trim();
  if (!raw) {
    container.innerHTML = '<div class="script-empty">暂无剧本内容</div>';
    return;
  }
  const scenes = parseScenes(scriptText);
  if (!scenes.length) {
    container.innerHTML = `<div class="script-layout"><div class="scene-card"><div class="scene-body">${escapeHtml(raw).replace(/\n/g, '<br>')}</div></div></div>`;
    return;
  }
  const html = scenes.map((scene, idx) => {
    if (scene.isEpisodeHeader) {
      return `<div class="episode-header" style="animation-delay:${idx * 0.03}s">${escapeHtml(scene.title)}</div>`;
    }
    const lines = scene.raw.split('\n').map(l => l.trimEnd()).filter(l => l.trim());
    const sceneLabel = scene.episode > 0 ? `场景 ${scene.number}` : `场景 ${scene.sceneNum}`;
    const body = lines.map(line => {
      const escaped = escapeHtml(line);
      if (/^(?:场景\s*\d+|场景号[:：]?\s*\d+)/.test(line.trim())) return '';
      if (/^[\u4e00-\u9fa5A-Za-z0-9_·&]+[：:]/.test(line.trim())) {
        return `<div class="script-line" style="color:var(--text);font-weight:500">${escaped}</div>`;
      }
      return `<div class="script-line">${escaped}</div>`;
    }).filter(Boolean).join('');
    return `<section class="scene-card" style="animation-delay:${idx * 0.03}s"><div class="scene-title">${escapeHtml(sceneLabel)}</div><div class="scene-body">${body}</div></section>`;
  }).join('');
  container.innerHTML = `<div class="script-layout">${html}</div>`;
}

function renderScript(scriptText) {
  currentScriptText = scriptText;
  const scriptOutput = $('#script-output');
  if (!scriptOutput) return;
  renderScriptInto(scriptOutput, scriptText);
  renderEditSceneCards(scriptText);
}

/* ===== 重置 ===== */
function resetAll() {
  $('#outline-logs').innerHTML = '';
  $('#script-logs').innerHTML = '';
  const so = $('#script-output');
  if (so) so.innerHTML = '<div class="script-empty">生成后的剧本会显示在这里</div>';
  const op = $('#outline-panel');
  if (op) op.innerHTML = '';
  exportRow.innerHTML = '';
  $('#outline-progress-bar').style.width = '0%';
  $('#script-progress-bar').style.width = '0%';
  $$('.agent-chip').forEach(c => c.classList.remove('active', 'done'));
  setStatus($('#outline-status-badge'), 'idle', '待开始');
  setStatus($('#script-status-badge'), 'idle', '待开始');
  setStep('input');
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  submitBtn.disabled = false;
  logCount = 0;
  currentScriptText = '';
  selectedEditScene = null;
  currentResultMeta = null;
  currentOutline = null;
  currentUserPayload = null;
  resetEditPanel();
  $('#outline-review-wrap').style.display = 'none';
  $('#outline-chat-area').style.display = 'none';
  $('#outline-chat-history').innerHTML = '';
  $('#confirm-outline-btn').style.display = 'none';
  $('#script-review-panel').style.display = 'none';
  $('#script-chat-area').style.display = 'none';
  $('#script-chat-history').innerHTML = '';
  $('#confirm-script-btn').style.display = 'none';
}

/* ===== 大纲渲染（审阅面板用） ===== */
function renderOutlineContent(targetEl, outline) {
  const reversals = (outline.reversals || []).map(item => {
    const text = typeof item === 'object' ? (item.summary || JSON.stringify(item)) : String(item);
    return `<li>${escapeHtml(text)}</li>`;
  }).join('');
  const acts = (outline.three_act_outline || []).map(item => {
    if (typeof item === 'object') {
      return `<li><strong>${escapeHtml(item.act || '')}</strong>：${escapeHtml(item.summary || '')}</li>`;
    }
    return `<li>${escapeHtml(String(item))}</li>`;
  }).join('');
  targetEl.innerHTML = `
    <div style="margin-bottom:8px"><strong>标题：</strong>${escapeHtml(outline.title || '')}</div>
    <hr style="border:none;border-top:1px solid rgba(255,255,255,0.06);margin:12px 0">
    <div><strong>开场钩子：</strong>${escapeHtml(outline.opening_hook || '')}</div>
    <div><strong>核心冲突：</strong>${escapeHtml(outline.core_conflict || '')}</div>
    <div><strong>反转设计：</strong><ul>${reversals}</ul></div>
    <div><strong>三幕结构：</strong><ul>${acts}</ul></div>
    <div><strong>结尾钩子：</strong>${escapeHtml(outline.ending_hook || '')}</div>
  `;
}

/* ===== 完整结果大纲渲染（最终成果面板） ===== */
function renderFinalOutline(result) {
  currentResultMeta = {
    title: result.title || '',
    outline: result.outline || null,
    request_meta: result.request_meta || {},
    exports: result.exports || null,
  };
  const outline = result.outline || {};
  const requestMeta = result.request_meta || {};
  const scriptStatus = result.script_status || {};
  const video = result.video || {};
  const warnings = result.warnings || [];
  const styles = (requestMeta.styles || []).join('、');
  const reversals = (outline.reversals || []).map(item => {
    const text = typeof item === 'object' ? (item.summary || JSON.stringify(item)) : String(item);
    return `<li>${escapeHtml(text)}</li>`;
  }).join('');
  const acts = (outline.three_act_outline || []).map(item => {
    if (typeof item === 'object') return `<li><strong>${escapeHtml(item.act || '')}</strong>：${escapeHtml(item.summary || '')}</li>`;
    return `<li>${escapeHtml(String(item))}</li>`;
  }).join('');
  const review = result.review || {};
  const completeIcon = scriptStatus.is_complete ? '&#10003;' : '&#10007;';
  const hasEpisodes = (scriptStatus.target_episodes || 0) > 1;
  const completenessText = hasEpisodes
    ? `${completeIcon} ${scriptStatus.actual_episodes || 0}/${scriptStatus.target_episodes || 0} 集，共 ${scriptStatus.actual_scene_count || 0} 场`
    : `${completeIcon} 目标 ${scriptStatus.target_scene_count || 0} 场 / 实际 ${scriptStatus.actual_scene_count || 0} 场`;
  const outlinePanel = $('#outline-panel');
  outlinePanel.innerHTML = `
    <div style="margin-bottom:8px"><strong>标题：</strong>${escapeHtml(result.title || '')}</div>
    <div><strong>风格：</strong>${escapeHtml(styles)} / ${escapeHtml(requestMeta.writing_tone || '')}</div>
    <div><strong>规格：</strong>${requestMeta.episodes || 1} 集 &times; ${requestMeta.episode_duration || 60} 秒</div>
    <div><strong>完整性：</strong>${completenessText}</div>
    <hr style="border:none;border-top:1px solid rgba(255,255,255,0.06);margin:12px 0">
    <div><strong>开场钩子：</strong>${escapeHtml(outline.opening_hook || '')}</div>
    <div><strong>核心冲突：</strong>${escapeHtml(outline.core_conflict || '')}</div>
    <div><strong>反转设计：</strong><ul>${reversals}</ul></div>
    <div><strong>三幕结构：</strong><ul>${acts}</ul></div>
    <div><strong>结尾钩子：</strong>${escapeHtml(outline.ending_hook || '')}</div>
    ${review.feedback ? `<div style="margin-top:8px"><strong>审校反馈：</strong>${escapeHtml(review.feedback)}</div>` : ''}
    ${warnings.length ? `<div style="margin-top:8px;padding:10px 12px;border:1px solid rgba(251,191,36,0.28);background:rgba(251,191,36,0.08);border-radius:10px"><strong>告警：</strong>${warnings.map(item => escapeHtml(item)).join('；')}</div>` : ''}
    ${video.output_video_path ? `<div style="margin-top:10px;padding:12px;border:1px solid rgba(52,211,153,0.18);background:rgba(52,211,153,0.06);border-radius:10px"><div><strong>视频输出：</strong>${escapeHtml(video.output_video_path || '')}</div><div><strong>字幕文件：</strong>${escapeHtml(video.subtitle_path || '')}</div><div><strong>时间线镜头数：</strong>${escapeHtml(video.timeline_count || 0)}</div><div><strong>Provider：</strong>${escapeHtml((video.notes || []).join(' | '))}</div></div>` : ''}
  `;
}

/* ===== 视频面板 ===== */
function renderVideoPanel(video) {
  const videoPanel = $('#video-panel');
  const videoTabBtn = $('#video-tab-btn');
  if (!video || !video.output_video_path) {
    if (videoTabBtn) videoTabBtn.style.display = 'none';
    return;
  }
  if (videoTabBtn) videoTabBtn.style.display = '';
  const notes = (video.notes || []);
  const providerNote = notes.find(n => n.startsWith('video_provider=')) || '';
  const durationNote = notes.find(n => n.startsWith('total_duration=')) || '';
  const isJimeng = providerNote.includes('jimeng');

  let html = `<div style="margin-bottom:14px">`;
  html += `<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">`;
  html += `<span style="font-size:1.5em">${isJimeng ? '&#127909;' : '&#127910;'}</span>`;
  html += `<strong style="font-size:1.1em">${isJimeng ? '即梦 AI 视频' : '视频输出'}</strong>`;
  html += `</div>`;

  html += `<div style="padding:14px;background:rgba(52,211,153,0.06);border:1px solid rgba(52,211,153,0.18);border-radius:10px;margin-bottom:12px">`;
  html += `<div><strong>输出路径：</strong><code style="font-size:0.85em;word-break:break-all">${escapeHtml(video.output_video_path)}</code></div>`;
  html += `<div><strong>字幕文件：</strong><code style="font-size:0.85em">${escapeHtml(video.subtitle_path || '')}</code></div>`;
  html += `<div><strong>时间线镜头数：</strong>${video.timeline_count || 0}</div>`;
  if (durationNote) html += `<div><strong>总时长：</strong>${durationNote.split('=')[1] || ''}s</div>`;
  html += `<div><strong>Provider：</strong>${escapeHtml(notes.join(' | '))}</div>`;
  html += `</div>`;

  if (isJimeng) {
    html += `<div style="padding:12px;background:rgba(139,92,246,0.06);border:1px solid rgba(139,92,246,0.18);border-radius:10px;margin-bottom:12px">`;
    html += `<div style="font-weight:600;margin-bottom:6px">即梦 AI 渲染详情</div>`;
    html += `<div>• 接口：CVSync2AsyncSubmitTask 异步提交</div>`;
    html += `<div>• 模型：jimeng_t2v_v30</div>`;
    html += `<div>• 流程：剧本文案 → 火山引擎视频生成 → 语音音画合成 → 字幕拼接</div>`;
    html += `<div>• 输出：竖屏短剧成片</div>`;
    html += `</div>`;
  }
  html += `</div>`;
  videoPanel.innerHTML = html;
}

/* ===== 导出 ===== */
function renderExports(exportsData) {
  exportRow.innerHTML = '';
  if (!exportsData) return;
  if (exportsData.docx) {
    const a = document.createElement('a');
    a.className = 'export-link'; a.href = `/exports/${exportsData.docx}`;
    a.innerHTML = '&#128196; Word'; exportRow.appendChild(a);
  }
  if (exportsData.pdf) {
    const a = document.createElement('a');
    a.className = 'export-link'; a.href = `/exports/${exportsData.pdf}`;
    a.innerHTML = '&#128203; PDF'; exportRow.appendChild(a);
  }
}

/* ===== Tabs（支持局部作用域） ===== */
function initTabs(container) {
  if (!container) return;
  container.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      container.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      container.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      const panel = container.querySelector(`#${btn.dataset.tab}`);
      if (panel) panel.classList.add('active');
    });
  });
}
initTabs($('#script-review-panel'));
initTabs($('#result-section'));

/* ===== 场景编辑面板 ===== */
function renderEditSceneCards(scriptText) {
  const container = $('#edit-scene-cards');
  if (!container) return;
  const scenes = parseScenes(scriptText).filter(s => !s.isEpisodeHeader);
  if (!scenes.length) {
    container.innerHTML = '<div class="script-empty">暂无可编辑的场景</div>';
    return;
  }
  container.innerHTML = scenes.map(scene => `
    <div class="edit-scene-card${selectedEditScene === scene.number ? ' selected' : ''}" data-scene="${scene.number}" data-scene-num="${scene.sceneNum}">
      <div class="edit-scene-number">${scene.number}</div>
      <div class="edit-scene-preview">
        <strong>${escapeHtml(scene.title)}</strong><br>
        ${escapeHtml(scene.preview)}...
      </div>
    </div>
  `).join('');
}

function selectEditScene(sceneId) {
  selectedEditScene = sceneId;
  $$('.edit-scene-card').forEach(card => {
    card.classList.toggle('selected', card.dataset.scene === String(sceneId));
  });
  const info = $('#edit-selected-info');
  if (info) info.style.display = 'flex';
  const label = $('#edit-selected-scene-label');
  if (label) label.textContent = `场景 ${sceneId}`;
  const input = $('#edit-instruction');
  const btn = $('#edit-submit-btn');
  if (input) { input.disabled = false; input.placeholder = `输入对场景${sceneId}的修改指令...`; input.focus(); }
  if (btn) btn.disabled = false;
}

function deselectEditScene() {
  selectedEditScene = null;
  $$('.edit-scene-card').forEach(c => c.classList.remove('selected'));
  const info = $('#edit-selected-info');
  if (info) info.style.display = 'none';
  const input = $('#edit-instruction');
  if (input) { input.disabled = true; input.placeholder = '请先选择要修改的场景'; }
  const btn = $('#edit-submit-btn');
  if (btn) btn.disabled = true;
}

function resetEditPanel() {
  const container = $('#edit-scene-cards');
  if (container) container.innerHTML = '<div class="script-empty">生成剧本后，场景列表会显示在这里</div>';
  const history = $('#edit-chat-history');
  if (history) history.innerHTML = '';
  deselectEditScene();
}

function appendChatMsg(historyEl, role, message) {
  const msg = document.createElement('div');
  msg.className = `edit-chat-msg ${role}`;
  msg.textContent = message;
  historyEl.appendChild(msg);
  historyEl.scrollTop = historyEl.scrollHeight;
}

const editSceneCards = $('#edit-scene-cards');
if (editSceneCards) {
  editSceneCards.addEventListener('click', (e) => {
    const card = e.target.closest('.edit-scene-card');
    if (!card) return;
    const sceneId = card.dataset.scene;
    if (sceneId) selectEditScene(sceneId);
  });
}
const deselectBtn = $('#edit-deselect-btn');
if (deselectBtn) deselectBtn.addEventListener('click', deselectEditScene);

/* ===== 提交场景编辑 ===== */
async function submitSceneEdit() {
  const instruction = $('#edit-instruction').value.trim();
  if (!instruction || !selectedEditScene) return;

  const btn = $('#edit-submit-btn');
  const input = $('#edit-instruction');
  btn.disabled = true;
  input.disabled = true;
  input.value = '';

  const chatHistory = $('#edit-chat-history');
  appendChatMsg(chatHistory, 'user', `修改场景${selectedEditScene}：${instruction}`);
  appendChatMsg(chatHistory, 'loading', '正在修改中，请稍候...');

  try {
    const response = await fetch('/api/edit_scene', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scene_number: selectedEditScene,
        instruction: instruction,
        script: currentScriptText,
        title: currentResultMeta?.title || '',
        outline: currentResultMeta?.outline || null,
        request_meta: currentResultMeta?.request_meta || {},
      }),
    });
    const data = await response.json();

    const loadingMsg = chatHistory.querySelector('.edit-chat-msg.loading:last-child');
    if (loadingMsg) loadingMsg.remove();

    if (!response.ok || !data.ok) {
      appendChatMsg(chatHistory, 'error', data.error || '修改失败，请重试');
      btn.disabled = false; input.disabled = false;
      return;
    }

    appendChatMsg(chatHistory, 'assistant', `场景${selectedEditScene}已修改完成`);
    renderScript(data.script);
    renderEditSceneCards(data.script);
    if (currentResultMeta) {
      currentResultMeta.title = data.title || currentResultMeta.title;
      currentResultMeta.exports = data.exports || currentResultMeta.exports;
    }
    if (data.exports) renderExports(data.exports);
  } catch (error) {
    const loadingMsg = chatHistory.querySelector('.edit-chat-msg.loading:last-child');
    if (loadingMsg) loadingMsg.remove();
    appendChatMsg(chatHistory, 'error', '网络错误，请确认服务是否运行');
  }
  btn.disabled = false; input.disabled = false; input.focus();
}

const editSubmitBtn = $('#edit-submit-btn');
if (editSubmitBtn) editSubmitBtn.addEventListener('click', submitSceneEdit);
const editInstructionInput = $('#edit-instruction');
if (editInstructionInput) editInstructionInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitSceneEdit(); }
});

/* ===== 独立生成视频 ===== */
$('#generate-video-btn').addEventListener('click', async () => {
  const btn = $('#generate-video-btn');
  btn.disabled = true;
  btn.textContent = '视频生成中...';
  try {
    const response = await fetch('/api/generate_video', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        script: currentScriptText,
        title: currentResultMeta?.title || '',
        request_meta: currentResultMeta?.request_meta || {},
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      alert(data.error || '视频生成失败');
      btn.disabled = false; btn.innerHTML = '<span class="btn-icon">&#127909;</span> 生成视频';
      return;
    }
    pollVideoJob(data.job_id);
  } catch (error) {
    alert('网络错误，请确认服务是否运行');
    btn.disabled = false; btn.innerHTML = '<span class="btn-icon">&#127909;</span> 生成视频';
  }
});

async function pollVideoJob(jobId) {
  try {
    const response = await fetch(`/api/jobs/${jobId}`);
    const data = await response.json();
    if (data.status === 'running' || data.status === 'queued') {
      setTimeout(() => pollVideoJob(jobId), 2000); return;
    }
    const btn = $('#generate-video-btn');
    btn.disabled = false; btn.innerHTML = '<span class="btn-icon">&#127909;</span> 生成视频';
    if (data.status === 'completed' && data.result && data.result.video) {
      renderVideoPanel(data.result.video);
      const videoTabBtn = $('#video-tab-btn');
      if (videoTabBtn) { videoTabBtn.style.display = ''; videoTabBtn.click(); }
    } else if (data.status === 'failed') {
      alert(`视频生成失败：${(data.error || '未知错误').split('\n')[0]}`);
    }
  } catch (error) {
    const btn = $('#generate-video-btn');
    btn.disabled = false; btn.innerHTML = '<span class="btn-icon">&#127909;</span> 生成视频';
  }
}

/* ============================================================
 *  新工作流轮询：大纲生成 → 审阅 → 正文生成 → 审阅 → 导出
 * ============================================================ */

/* --- 步骤 1: 轮询大纲生成任务 --- */
async function pollOutlineJob(jobId) {
  const logsEl = $('#outline-logs');
  const barEl = $('#outline-progress-bar');
  const badgeEl = $('#outline-status-badge');
  let response, data;
  try {
    response = await fetch(`/api/jobs/${jobId}`);
    data = await response.json();
  } catch (error) {
    setStatus(badgeEl, 'failed', '网络中断');
    submitBtn.disabled = false;
    return;
  }
  if (response.status === 404) {
    setStatus(badgeEl, 'failed', '任务不存在');
    submitBtn.disabled = false;
    return;
  }

  const logs = data.logs || [];
  if (logs.length !== logCount) {
    logsEl.innerHTML = '';
    logs.forEach(log => appendLog(logsEl, log.message));
    logCount = logs.length;
  }
  updateAgentChips(outlineSection, logs);
  updateProgress(barEl, logs, data.status === 'completed' || data.status === 'failed', 4);

  if (data.status === 'running' || data.status === 'queued') {
    setStatus(badgeEl, 'running', '大纲生成中...');
    pollTimer = setTimeout(() => pollOutlineJob(jobId), 1200);
    return;
  }

  if (data.status === 'failed') {
    setStatus(badgeEl, 'failed', `失败：${(data.error || '未知错误').split('\n')[0]}`);
    submitBtn.disabled = false;
    return;
  }

  if (data.status === 'completed') {
    const result = data.result || {};
    currentOutline = result.outline || {};
    setStatus(badgeEl, 'completed', '大纲已生成，请审阅');

    renderOutlineContent($('#outline-review-content'), currentOutline);
    $('#outline-review-wrap').style.display = '';
    $('#outline-chat-area').style.display = '';
    $('#confirm-outline-btn').style.display = '';
    submitBtn.disabled = false;
  }
}

/* --- 步骤 2: 大纲修改提交 --- */
async function submitOutlineEdit() {
  const input = $('#outline-edit-input');
  const btn = $('#outline-edit-btn');
  const instruction = input.value.trim();
  if (!instruction) return;

  btn.disabled = true;
  input.disabled = true;
  input.value = '';

  const chatHistory = $('#outline-chat-history');
  appendChatMsg(chatHistory, 'user', instruction);
  appendChatMsg(chatHistory, 'loading', '正在修改大纲...');

  try {
    const response = await fetch('/api/edit_outline', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ instruction }),
    });
    const data = await response.json();

    const loadingMsg = chatHistory.querySelector('.edit-chat-msg.loading:last-child');
    if (loadingMsg) loadingMsg.remove();

    if (!response.ok || !data.ok) {
      appendChatMsg(chatHistory, 'error', data.error || '修改失败，请重试');
    } else {
      currentOutline = data.outline;
      renderOutlineContent($('#outline-review-content'), currentOutline);
      appendChatMsg(chatHistory, 'assistant', '大纲已根据您的意见修改');
    }
  } catch (error) {
    const loadingMsg = chatHistory.querySelector('.edit-chat-msg.loading:last-child');
    if (loadingMsg) loadingMsg.remove();
    appendChatMsg(chatHistory, 'error', '网络错误，请确认服务是否运行');
  }
  btn.disabled = false;
  input.disabled = false;
  input.focus();
}

$('#outline-edit-btn').addEventListener('click', submitOutlineEdit);
$('#outline-edit-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitOutlineEdit(); }
});

/* --- 步骤 3: 确认大纲 → 生成正文 --- */
$('#confirm-outline-btn').addEventListener('click', async () => {
  const btn = $('#confirm-outline-btn');
  btn.disabled = true;
  btn.textContent = '正在提交...';

  setStep('script-review');
  showSection(scriptSection);
  setStatus($('#script-status-badge'), 'running', '正文生成中...');
  logCount = 0;

  try {
    const response = await fetch('/api/confirm_outline', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    const data = await response.json();

    if (!response.ok) {
      setStatus($('#script-status-badge'), 'failed', data.error || '提交失败');
      btn.disabled = false;
      btn.innerHTML = '确认大纲 &#8594;';
      return;
    }

    activeJobId = data.job_id;
    appendLog($('#script-logs'), '任务已提交，Writer + Reviewer 开始工作...');
    pollScriptJob(activeJobId);
  } catch (error) {
    setStatus($('#script-status-badge'), 'failed', '网络错误');
    btn.disabled = false;
    btn.innerHTML = '确认大纲 &#8594;';
  }
});

/* --- 步骤 3b: 轮询正文生成任务 --- */
async function pollScriptJob(jobId) {
  const logsEl = $('#script-logs');
  const barEl = $('#script-progress-bar');
  const badgeEl = $('#script-status-badge');
  let response, data;
  try {
    response = await fetch(`/api/jobs/${jobId}`);
    data = await response.json();
  } catch (error) {
    setStatus(badgeEl, 'failed', '网络中断');
    return;
  }
  if (response.status === 404) {
    setStatus(badgeEl, 'failed', '任务不存在');
    return;
  }

  const logs = data.logs || [];
  if (logs.length !== logCount) {
    logsEl.innerHTML = '';
    logs.forEach(log => appendLog(logsEl, log.message));
    logCount = logs.length;
  }
  updateAgentChips(scriptSection, logs);
  updateProgress(barEl, logs, data.status === 'completed' || data.status === 'failed', 8);

  if (data.status === 'running' || data.status === 'queued') {
    setStatus(badgeEl, 'running', '正文生成中...');
    pollTimer = setTimeout(() => pollScriptJob(jobId), 1200);
    return;
  }

  if (data.status === 'failed') {
    setStatus(badgeEl, 'failed', `失败：${(data.error || '未知错误').split('\n')[0]}`);
    return;
  }

  if (data.status === 'completed') {
    const result = data.result || {};
    setStatus(badgeEl, 'completed', '正文已生成，请审阅');

    currentResultMeta = {
      title: result.title || '',
      outline: result.outline || currentOutline || null,
      request_meta: result.request_meta || {},
      exports: result.exports || null,
      script_status: result.script_status || {},
      review: result.review || {},
    };
    currentScriptText = result.script || '';

    renderOutlineContent($('#script-overview-panel'), result.outline || currentOutline || {});
    renderScript(result.script || '');

    $('#script-review-panel').style.display = '';
    $('#script-chat-area').style.display = '';
    $('#confirm-script-btn').style.display = '';
  }
}

/* --- 步骤 4: 剧本整体修改提交 --- */
async function submitScriptReview() {
  const input = $('#script-review-input');
  const btn = $('#script-review-btn');
  const instruction = input.value.trim();
  if (!instruction) return;

  btn.disabled = true;
  input.disabled = true;
  input.value = '';

  const chatHistory = $('#script-chat-history');
  appendChatMsg(chatHistory, 'user', instruction);
  appendChatMsg(chatHistory, 'loading', '正在修改剧本...');

  try {
    const response = await fetch('/api/review_script', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ instruction }),
    });
    const data = await response.json();

    const loadingMsg = chatHistory.querySelector('.edit-chat-msg.loading:last-child');
    if (loadingMsg) loadingMsg.remove();

    if (!response.ok || !data.ok) {
      appendChatMsg(chatHistory, 'error', data.error || '修改失败，请重试');
    } else {
      currentScriptText = data.script;
      renderScript(data.script);
      if (data.exports) renderExports(data.exports);
      if (currentResultMeta) {
        currentResultMeta.title = data.title || currentResultMeta.title;
        currentResultMeta.exports = data.exports || currentResultMeta.exports;
      }
      appendChatMsg(chatHistory, 'assistant', '剧本已根据您的意见修改');
    }
  } catch (error) {
    const loadingMsg = chatHistory.querySelector('.edit-chat-msg.loading:last-child');
    if (loadingMsg) loadingMsg.remove();
    appendChatMsg(chatHistory, 'error', '网络错误，请确认服务是否运行');
  }
  btn.disabled = false;
  input.disabled = false;
  input.focus();
}

$('#script-review-btn').addEventListener('click', submitScriptReview);
$('#script-review-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitScriptReview(); }
});

/* --- 步骤 5: 确认剧本 → 导出 --- */
$('#confirm-script-btn').addEventListener('click', async () => {
  const btn = $('#confirm-script-btn');
  btn.disabled = true;
  btn.textContent = '正在导出...';

  try {
    const response = await fetch('/api/confirm_script', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    const data = await response.json();

    if (!response.ok || !data.ok) {
      alert(data.error || '导出失败');
      btn.disabled = false;
      btn.innerHTML = '确认剧本 &#8594;';
      return;
    }

    setStep('result');
    const finalResult = {
      title: data.title,
      script: data.script,
      outline: currentResultMeta?.outline || currentOutline || {},
      request_meta: currentResultMeta?.request_meta || {},
      exports: data.exports,
      script_status: data.script_status || currentResultMeta?.script_status || {},
      review: currentResultMeta?.review || {},
    };
    renderFinalOutline(finalResult);
    const finalScriptOutput = $('#final-script-output');
    if (finalScriptOutput) {
      renderScriptInto(finalScriptOutput, data.script || '');
    }
    renderExports(data.exports);
    setTimeout(() => showSection(resultSection), 400);
  } catch (error) {
    alert('网络错误，请确认服务是否运行');
    btn.disabled = false;
    btn.innerHTML = '确认剧本 &#8594;';
  }
});

/* ===== 表单提交：开始新工作流 → 生成大纲 ===== */
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  resetAll();
  setStep('outline-review');
  showSection(outlineSection);
  setStatus($('#outline-status-badge'), 'running', '大纲生成中...');
  submitBtn.disabled = true;

  currentUserPayload = {
    theme: $('#theme').value,
    keywords: $('#keywords').value,
    audience: $('#audience').value,
    styles: [...selectedStyles],
    writing_tone: toneHidden.value,
    episodes: $('#episodes').value,
    episode_duration: $('#episode_duration').value,
    extra_requirements: $('#extra_requirements').value,
    generate_video: $('#generate_video').checked,
  };

  let response, data;
  try {
    response = await fetch('/api/generate_outline', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(currentUserPayload),
    });
    data = await response.json();
  } catch (error) {
    setStatus($('#outline-status-badge'), 'failed', '请求失败：服务未启动或连接中断');
    submitBtn.disabled = false;
    return;
  }

  if (!response.ok) {
    setStatus($('#outline-status-badge'), 'failed', data.error || '请求失败');
    submitBtn.disabled = false;
    return;
  }

  activeJobId = data.job_id;
  appendLog($('#outline-logs'), '任务创建成功，Planner 开始生成大纲...');
  pollOutlineJob(activeJobId);
});

/* ===== 辅助按钮 ===== */
resetButton.addEventListener('click', () => {
  form.reset();
  $('#episodes').value = 1;
  $('#episode_duration').value = 60;
  selectedStyles.clear();
  selectedStyles.add('都市悬疑');
  renderStyleTags();
  toneHidden.value = '影视化强张力';
  toneTrigger.querySelector('.select-value').textContent = '影视化强张力';
  toneOptions.querySelectorAll('.option').forEach(o => {
    o.classList.toggle('selected', o.dataset.value === '影视化强张力');
  });
  resetAll();
  showSection(inputSection);
});

$('#back-to-input').addEventListener('click', () => {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  setStep('input');
  showSection(inputSection);
  submitBtn.disabled = false;
});

$('#back-to-outline').addEventListener('click', () => {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  setStep('outline-review');
  showSection(outlineSection);
});

$('#new-creation').addEventListener('click', () => {
  resetAll();
  showSection(inputSection);
});

/* ===== 主题切换 ===== */
(function initThemeSwitcher() {
  const saved = localStorage.getItem('app-theme') || 'rose';
  document.documentElement.setAttribute('data-theme', saved);
  $$('#theme-switcher .theme-dot').forEach(dot => {
    dot.classList.toggle('active', dot.dataset.theme === saved);
  });
  $('#theme-switcher').addEventListener('click', (e) => {
    const dot = e.target.closest('.theme-dot');
    if (!dot) return;
    const theme = dot.dataset.theme;
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('app-theme', theme);
    $$('#theme-switcher .theme-dot').forEach(d => d.classList.toggle('active', d === dot));
  });
})();
