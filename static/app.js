/* ===== DOM 引用 ===== */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const form          = $('#generate-form');
const submitBtn     = $('#submit-btn');
const resetButton   = $('#reset-button');
const logsEl        = $('#logs');
const statusBadge   = $('#status-badge');
const scriptOutput  = $('#script-output');
const outlinePanel  = $('#outline-panel');
const exportRow     = $('#export-row');
const inputSection  = $('#input-section');
const processSection = $('#process-section');
const resultSection = $('#result-section');
const progressBar   = $('#progress-bar');
const stepsBar      = $$('.steps-bar .step');
const stepLines     = $$('.steps-bar .step-line');

let activeJobId = null;
let pollTimer   = null;
let logCount    = 0;

/* ===== 风格标签选择器 ===== */
const STYLE_OPTIONS = [
  '都市悬疑', '霸总', '重生', '复仇', '甜宠', '古风逆袭',
  '修仙', '玄幻', '仙侠', '武侠', '末世', '年代',
  '穿越', '穿书', '校园', '谍战', '权谋', '宫斗',
  '职场', '商战', '科幻', '赛博朋克', '无限流', '悬疑推理', '惊悚', '轻喜剧'
];
const selectedStyles = new Set(['都市悬疑']);
const styleTagsEl = $('#style-tags');
const stylesHidden = $('#styles-hidden');

function renderStyleTags() {
  styleTagsEl.innerHTML = STYLE_OPTIONS.map(s =>
    `<span class="tag-item${selectedStyles.has(s) ? ' selected' : ''}" data-style="${s}">${s}</span>`
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
  const order = ['input', 'process', 'result'];
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
function showSection(sectionEl) {
  [inputSection, processSection, resultSection].forEach(s => {
    if (s !== sectionEl && !s.classList.contains('hidden')) {
      s.classList.add('hidden');
    }
  });
  sectionEl.classList.remove('hidden');
  sectionEl.classList.remove('section-enter');
  void sectionEl.offsetWidth;
  sectionEl.classList.add('section-enter');
  sectionEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ===== 状态标签 ===== */
function setStatus(status, text) {
  statusBadge.className = `status ${status}`;
  statusBadge.textContent = text;
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

function appendLog(message) {
  const item = document.createElement('div');
  const agent = detectAgent(message);
  item.className = `log-item${agent ? ' ' + agent : ''}`;
  item.textContent = message;
  logsEl.appendChild(item);
  logsEl.scrollTop = logsEl.scrollHeight;
}

function updateAgentChips(logs) {
  const chips = $$('.agent-chip');
  const activeAgents = new Set();
  const doneAgents = new Set();
  logs.forEach(log => {
    const msg = log.message || '';
    if (/planner/i.test(msg)) { activeAgents.add('planner'); if (/完成|失败/.test(msg)) doneAgents.add('planner'); }
    if (/writer/i.test(msg))  { activeAgents.add('writer');  if (/生成初稿|兜底|补尾/.test(msg)) doneAgents.add('writer'); }
    if (/reviewer/i.test(msg)){ activeAgents.add('reviewer');if (/通过|完成|保留/.test(msg)) doneAgents.add('reviewer'); }
    if (/director/i.test(msg)){ activeAgents.add('director');if (/输出|最终/.test(msg)) doneAgents.add('director'); }
  });
  chips.forEach(chip => {
    const a = chip.dataset.agent;
    chip.classList.remove('active', 'done');
    if (doneAgents.has(a)) chip.classList.add('done');
    else if (activeAgents.has(a)) chip.classList.add('active');
  });
}

function updateProgress(logs, isDone) {
  if (isDone) { progressBar.style.width = '100%'; return; }
  const total = 8;
  const pct = Math.min((logs.length / total) * 100, 95);
  progressBar.style.width = pct + '%';
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

/* ===== 剧本渲染 ===== */
function renderScript(scriptText) {
  const raw = String(scriptText || '').trim();
  if (!raw) {
    scriptOutput.innerHTML = '<div class="script-empty">暂无剧本内容</div>';
    return;
  }
  const normalized = raw
    .replace(/\r\n/g, '\n')
    .replace(/^【场景\s*(\d+)】/gm, '场景$1')
    .replace(/^场景号\s*(\d+)/gm, '场景号：$1');
  const segments = normalized.split(/(?=^场景\s*\d+(?:\s|[:：]|$))/m).filter(Boolean);

  if (!segments.length) {
    scriptOutput.innerHTML = `<div class="script-layout"><div class="scene-card"><div class="scene-body">${escapeHtml(raw).replace(/\n/g, '<br>')}</div></div></div>`;
    return;
  }

  const html = segments.map((segment, idx) => {
    const lines = segment.split('\n').map(l => l.trimEnd()).filter(l => l.trim());
    const title = escapeHtml(lines.shift() || '剧本');
    const body = lines.map(line => {
      const escaped = escapeHtml(line);
      if (/^[\u4e00-\u9fa5A-Za-z0-9_·]+[：:]/.test(line)) {
        return `<div class="script-line" style="color:var(--text);font-weight:500">${escaped}</div>`;
      }
      return `<div class="script-line">${escaped}</div>`;
    }).join('');
    return `<section class="scene-card" style="animation-delay:${idx * 0.05}s"><div class="scene-title">${title}</div><div class="scene-body">${body}</div></section>`;
  }).join('');

  scriptOutput.innerHTML = `<div class="script-layout">${html}</div>`;
}

/* ===== 重置 ===== */
function resetAll() {
  logsEl.innerHTML = '';
  scriptOutput.innerHTML = '<div class="script-empty">生成后的剧本会显示在这里</div>';
  outlinePanel.innerHTML = '';
  exportRow.innerHTML = '';
  progressBar.style.width = '0%';
  $$('.agent-chip').forEach(c => c.classList.remove('active', 'done'));
  setStatus('idle', '待开始');
  setStep('input');
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  submitBtn.disabled = false;
  logCount = 0;
}

/* ===== 大纲渲染 ===== */
function renderOutline(result) {
  const outline = result.outline || {};
  const requestMeta = result.request_meta || {};
  const llmMeta = result.llm || {};
  const scriptStatus = result.script_status || {};
  const video = result.video || {};
  const warnings = result.warnings || [];
  const styles = (requestMeta.styles || []).join('、');
  const reversals = (outline.reversals || []).map(item => `<li>${escapeHtml(String(item))}</li>`).join('');
  const acts = (outline.three_act_outline || []).map(item => `<li><strong>${escapeHtml(item.act || '')}</strong>：${escapeHtml(item.summary || '')}</li>`).join('');
  const review = result.review || {};
  const completeIcon = scriptStatus.is_complete ? '&#10003;' : '&#10007;';
  outlinePanel.innerHTML = `
    <div style="margin-bottom:8px"><strong>标题：</strong>${escapeHtml(result.title || '')}</div>
    <div><strong>风格：</strong>${escapeHtml(styles)} / ${escapeHtml(requestMeta.writing_tone || '')}</div>
    <div><strong>规格：</strong>${requestMeta.episodes || 1} 集 &times; ${requestMeta.episode_duration || 60} 秒</div>
    <div><strong>模型：</strong>${escapeHtml(llmMeta.model || '')} (${llmMeta.strict_api ? 'Strict' : 'Standard'})</div>
    <div><strong>完整性：</strong>${completeIcon} 目标 ${scriptStatus.target_scene_count || 0} 场 / 实际 ${scriptStatus.actual_scene_count || 0} 场</div>
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

/* ===== Tabs ===== */
$$('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    $$('.tab-btn').forEach(b => b.classList.remove('active'));
    $$('.tab-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    const panel = $(`#${btn.dataset.tab}`);
    if (panel) panel.classList.add('active');
  });
});

/* ===== 轮询任务 ===== */
async function pollJob(jobId) {
  let response, data;
  try {
    response = await fetch(`/api/jobs/${jobId}`);
    data = await response.json();
  } catch (error) {
    setStatus('failed', '网络中断，请确认服务是否运行');
    submitBtn.disabled = false;
    return;
  }
  if (response.status === 404) {
    setStatus('failed', '任务不存在');
    submitBtn.disabled = false;
    return;
  }

  const logs = data.logs || [];
  if (logs.length !== logCount) {
    logsEl.innerHTML = '';
    logs.forEach(log => appendLog(log.message));
    logCount = logs.length;
  }
  updateAgentChips(logs);
  updateProgress(logs, data.status === 'completed' || data.status === 'failed');

  if (data.status === 'running' || data.status === 'queued') {
    setStatus('running', '创作中...');
    pollTimer = setTimeout(() => pollJob(jobId), 1200);
    return;
  }

  if (data.status === 'failed') {
    setStatus('failed', `失败：${(data.error || '未知错误').split('\n')[0]}`);
    submitBtn.disabled = false;
    return;
  }

  if (data.status === 'completed') {
    const result = data.result || {};
    const scriptStatus = result.script_status || {};
    if (scriptStatus.is_complete || result.video) {
      setStatus('completed', '创作完成');
      setStep('result');
      renderOutline(result);
      renderScript(result.script || '');
      renderExports(result.exports);
      renderVideoPanel(result.video || null);
      setTimeout(() => showSection(resultSection), 600);
    } else {
      setStatus('failed', '生成结果不完整，建议重试');
      renderOutline(result);
      renderScript(result.script || '');
    }
    submitBtn.disabled = false;
  }
}

/* ===== 提交 ===== */
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  resetAll();
  setStep('process');
  showSection(processSection);
  setStatus('running', '任务提交中...');
  submitBtn.disabled = true;

  const payload = {
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
    response = await fetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    data = await response.json();
  } catch (error) {
    setStatus('failed', '请求失败：服务未启动或连接中断');
    submitBtn.disabled = false;
    return;
  }

  if (!response.ok) {
    setStatus('failed', data.error || '请求失败');
    submitBtn.disabled = false;
    return;
  }

  activeJobId = data.job_id;
  appendLog('任务创建成功，Director 即将开始调度各 Agent...');
  pollJob(activeJobId);
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

$('#new-creation').addEventListener('click', () => {
  resetAll();
  showSection(inputSection);
});
