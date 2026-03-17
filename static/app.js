const form = document.getElementById('generate-form');
const resetButton = document.getElementById('reset-button');
const logsEl = document.getElementById('logs');
const statusBadge = document.getElementById('status-badge');
const scriptOutput = document.getElementById('script-output');
const outlinePanel = document.getElementById('outline-panel');
const exportRow = document.getElementById('export-row');
const stylesSelect = document.getElementById('styles');
const selectedStylesEl = document.getElementById('selected-styles');
const resultSection = document.getElementById('result-section');
const resultTransition = document.getElementById('result-transition');
const processSection = document.getElementById('process-section');

let activeJobId = null;
let pollTimer = null;

function setStatus(status, text) {
  statusBadge.className = `status ${status}`;
  statusBadge.textContent = text;
}

function appendLog(message) {
  const item = document.createElement('div');
  item.className = 'log-item';
  item.textContent = message;
  logsEl.appendChild(item);
  logsEl.scrollTop = logsEl.scrollHeight;
}

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

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

  const html = segments.map((segment) => {
    const lines = segment
      .split('\n')
      .map((line) => line.trimEnd())
      .filter((line) => line.trim() !== '');
    const title = escapeHtml(lines.shift() || '剧本');
    const body = lines.map((line) => `<div class="script-line">${escapeHtml(line)}</div>`).join('');
    return `<section class="scene-card"><div class="scene-title">${title}</div><div class="scene-body">${body}</div></section>`;
  }).join('');

  scriptOutput.innerHTML = `<div class="script-layout">${html}</div>`;
}

function resetView() {
  logsEl.innerHTML = '';
  scriptOutput.innerHTML = '生成后的剧本会显示在这里。';
  outlinePanel.innerHTML = '';
  exportRow.innerHTML = '';
  resultTransition.textContent = '';
  resultTransition.className = 'result-transition';
  processSection.classList.remove('hidden');
  resultSection.classList.add('hidden');
  setStatus('idle', '待开始');
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

function renderSelectedStyles() {
  const selected = Array.from(stylesSelect.selectedOptions).map(option => option.value);
  if (!selected.length) {
    selectedStylesEl.innerHTML = '<span class="style-pill empty">当前未选择风格</span>';
    return;
  }
  selectedStylesEl.innerHTML = selected.map(style => `<span class="style-pill">${style}</span>`).join('');
}

function announceResultReady() {
  resultTransition.textContent = '生成完成，正在跳转到结果区...';
  resultTransition.className = 'result-transition visible';
  window.setTimeout(() => {
    processSection.classList.add('hidden');
    resultSection.classList.remove('hidden');
    resultSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, 260);
  window.setTimeout(() => {
    resultTransition.className = 'result-transition';
    resultTransition.textContent = '';
  }, 1800);
}

function renderOutline(result) {
  const outline = result.outline || {};
  const requestMeta = result.request_meta || {};
  const llmMeta = result.llm || {};
  const scriptStatus = result.script_status || {};
  const styles = (requestMeta.styles || []).join('、');
  const reversals = (outline.reversals || []).map(item => `<li>${item}</li>`).join('');
  const acts = (outline.three_act_outline || []).map(item => `<li><strong>${item.act}</strong>：${item.summary}</li>`).join('');
  const review = result.review || {};
  outlinePanel.innerHTML = `
    <div><strong>标题：</strong>${result.title || ''}</div>
    <div><strong>剧本风格：</strong>${styles || ''}</div>
    <div><strong>文字风格：</strong>${requestMeta.writing_tone || ''}</div>
    <div><strong>集数 / 时长：</strong>${requestMeta.episodes || ''} 集 / ${requestMeta.episode_duration || ''} 秒</div>
    <div><strong>LLM模式：</strong>${llmMeta.strict_api ? '严格API' : '标准模式'} / ${llmMeta.last_call_mode || ''}</div>
    <div><strong>模型：</strong>${llmMeta.model || ''}</div>
    <div><strong>Writer生成方式：</strong>${(result.generation || {}).writer_mode || ''}</div>
    <div><strong>Reviewer审校方式：</strong>${review.mode || ''}</div>
    <div><strong>剧本完整性：</strong>${scriptStatus.is_complete ? '完整' : '不完整'} / 目标场景 ${scriptStatus.target_scene_count || 0} / 实际场景 ${scriptStatus.actual_scene_count || 0}</div>
    <div><strong>开场钩子：</strong>${outline.opening_hook || ''}</div>
    <div><strong>核心冲突：</strong>${outline.core_conflict || ''}</div>
    <div><strong>反转设计：</strong><ul>${reversals}</ul></div>
    <div><strong>三幕结构：</strong><ul>${acts}</ul></div>
    <div><strong>结尾 Hook：</strong>${outline.ending_hook || ''}</div>
    <div><strong>审校结果：</strong>${review.feedback || ''}</div>
  `;
}

function renderExports(exportsData) {
  exportRow.innerHTML = '';
  if (!exportsData) {
    return;
  }
  if (exportsData.docx) {
    const docx = document.createElement('a');
    docx.className = 'export-link';
    docx.href = `/exports/${exportsData.docx}`;
    docx.textContent = '导出 Word';
    exportRow.appendChild(docx);
  }
  if (exportsData.pdf) {
    const pdf = document.createElement('a');
    pdf.className = 'export-link';
    pdf.href = `/exports/${exportsData.pdf}`;
    pdf.textContent = '导出 PDF';
    exportRow.appendChild(pdf);
  }
}

async function pollJob(jobId) {
  let response;
  let data;
  try {
    response = await fetch(`/api/jobs/${jobId}`);
    data = await response.json();
  } catch (error) {
    setStatus('failed', '网络中断，请确认服务是否运行');
    return;
  }
  if (response.status === 404) {
    setStatus('failed', '任务不存在');
    return;
  }

  logsEl.innerHTML = '';
  (data.logs || []).forEach(log => appendLog(log.message));

  if (data.status === 'running' || data.status === 'queued') {
    setStatus('running', '生成中');
    pollTimer = setTimeout(() => pollJob(jobId), 1500);
    return;
  }

  if (data.status === 'failed') {
    resultTransition.textContent = '';
    resultTransition.className = 'result-transition';
    processSection.classList.remove('hidden');
    resultSection.classList.add('hidden');
    setStatus('failed', `生成失败：${data.error || '未知错误'}`);
    return;
  }

  if (data.status === 'completed') {
    const result = data.result || {};
    const scriptStatus = result.script_status || {};
    setStatus(
      scriptStatus.is_complete ? 'completed' : 'failed',
      scriptStatus.is_complete ? '生成完成' : '生成结果不完整'
    );
    renderOutline(result);
    renderScript(result.script || '');
    renderExports(result.exports);
    announceResultReady();
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  resetView();
  processSection.classList.remove('hidden');
  resultSection.classList.add('hidden');
  setStatus('running', '任务已提交');

  const payload = {
    theme: document.getElementById('theme').value,
    keywords: document.getElementById('keywords').value,
    audience: document.getElementById('audience').value,
    styles: Array.from(stylesSelect.selectedOptions).map(option => option.value),
    writing_tone: document.getElementById('writing_tone').value,
    episodes: document.getElementById('episodes').value,
    episode_duration: document.getElementById('episode_duration').value,
    extra_requirements: document.getElementById('extra_requirements').value,
  };

  let response;
  let data;
  try {
    response = await fetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    data = await response.json();
  } catch (error) {
    setStatus('failed', '请求失败：服务未启动或连接中断');
    return;
  }

  if (!response.ok) {
    setStatus('failed', data.error || '请求失败');
    return;
  }

  activeJobId = data.job_id;
  appendLog('任务创建成功，Director 即将开始调度各 Agent');
  pollJob(activeJobId);
});

stylesSelect.addEventListener('change', renderSelectedStyles);

resetButton.addEventListener('click', () => {
  form.reset();
  document.getElementById('episodes').value = 1;
  document.getElementById('episode_duration').value = 60;
  Array.from(stylesSelect.options).forEach(option => {
    option.selected = option.value === '都市悬疑';
  });
  document.getElementById('writing_tone').value = '影视化强张力';
  renderSelectedStyles();
  resetView();
});

renderSelectedStyles();
