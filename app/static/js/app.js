/* Paper China - Frontend Application */

let papers = [];
let currentPaper = null;
let selectedFile = null;
let searchTimer = null;
let translationPollId = null;
let pagination = { offset: 0, limit: 50, total: 0 };

// === API ===
async function errorDetail(res, fallback) {
  try { return (await res.json()).detail || fallback; } catch { return fallback; }
}

const api = {
  async listPapers(search = '', status = '', offset = 0, limit = 50) {
    const params = new URLSearchParams();
    if (search) params.set('search', search);
    if (status) params.set('status', status);
    params.set('offset', offset);
    params.set('limit', limit);
    const res = await fetch(`/api/papers/?${params}`);
    if (!res.ok) throw new Error(await errorDetail(res, 'Failed to load papers'));
    return res.json();
  },
  async uploadPaper(file, tags = '') {
    const form = new FormData();
    form.append('file', file);
    if (tags) form.append('tags', tags);
    const res = await fetch('/api/papers/upload', { method: 'POST', body: form });
    if (!res.ok) throw new Error(await errorDetail(res, 'Upload failed'));
    return res.json();
  },
  async getPaper(id) {
    const res = await fetch(`/api/papers/${id}`);
    if (!res.ok) throw new Error(await errorDetail(res, 'Paper not found'));
    return res.json();
  },
  async translatePaper(id, backend = '', quality = 'balanced') {
    const params = new URLSearchParams();
    if (backend) params.set('backend', backend);
    if (quality) params.set('quality', quality);
    const res = await fetch(`/api/papers/${id}/translate?${params}`, { method: 'POST' });
    if (!res.ok) throw new Error(await errorDetail(res, 'Translation failed'));
    return res.json();
  },
  async deletePaper(id) {
    const res = await fetch(`/api/papers/${id}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(await errorDetail(res, 'Delete failed'));
    return res.json();
  },
  async updatePaper(id, data) {
    const res = await fetch(`/api/papers/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error(await errorDetail(res, 'Update failed'));
    return res.json();
  },
  async getStats() {
    const res = await fetch('/api/stats');
    if (!res.ok) throw new Error(await errorDetail(res, 'Failed to load stats'));
    return res.json();
  }
};

// === Views ===
function showView(name) {
  // Clean up polling when leaving reader
  if (name !== 'reader' && translationPollId) {
    clearInterval(translationPollId);
    translationPollId = null;
  }
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById(`${name}-view`).classList.add('active');
}

function showLibrary() {
  showView('library');
  loadPapers();
}

function showUpload() {
  showView('upload');
  selectedFile = null;
  document.getElementById('upload-preview').classList.add('hidden');
  document.getElementById('upload-progress').classList.add('hidden');
  document.getElementById('drop-zone').classList.remove('hidden');
  document.getElementById('file-input').value = '';
}

// === Paper Library ===
async function loadPapers() {
  const search = document.getElementById('search-input').value;
  const status = document.getElementById('status-filter').value;
  try {
    const data = await api.listPapers(search, status, pagination.offset, pagination.limit);
    papers = data.papers;
    pagination.total = data.total;
    renderPaperList();
    renderPagination();
    updateStats();
  } catch (e) {
    console.error('Failed to load papers:', e);
  }
}

async function updateStats() {
  try {
    const stats = await api.getStats();
    const statsEl = document.getElementById('stats-bar');
    if (statsEl) {
      statsEl.textContent = `${stats.total_papers} 篇论文 | ${stats.completed_translations} 已翻译`;
    }
  } catch (e) {
    // ignore
  }
}

async function batchTranslate() {
  const pending = papers.filter(p => p.translation_status === 'pending' || p.translation_status === 'failed');
  if (pending.length === 0) {
    alert('没有待翻译的论文');
    return;
  }

  if (!confirm(`确定翻译 ${pending.length} 篇论文？`)) return;

  const quality = document.getElementById('quality-preset')?.value || 'balanced';
  let success = 0;
  let failed = 0;

  for (const paper of pending) {
    try {
      await api.translatePaper(paper.id, '', quality);
      success++;
    } catch (e) {
      failed++;
    }
  }

  alert(`已提交 ${success} 篇翻译任务${failed > 0 ? `，${failed} 篇提交失败` : ''}。翻译在后台进行中，请稍后刷新查看结果。`);
  loadPapers();
}

function debounceSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadPapers, 300);
}

function renderPaperList() {
  const container = document.getElementById('paper-list');
  const empty = document.getElementById('empty-state');

  if (papers.length === 0) {
    container.innerHTML = '';
    empty.classList.remove('hidden');
    return;
  }
  empty.classList.add('hidden');

  container.innerHTML = papers.map(p => `
    <div class="paper-card" data-paper-id="${esc(p.id)}" data-action="open-reader">
      <div class="title">${esc(p.title)}</div>
      <div class="meta">
        <span>${p.page_count} 页</span>
        <span>${formatSize(p.file_size)}</span>
        <span>${formatDate(p.created_at)}</span>
      </div>
      <span class="status status-${sanitizeClass(p.translation_status)}">${statusLabel(p.translation_status)}</span>
      ${p.tags ? `<div class="meta" style="margin-top:6px">🏷 ${esc(p.tags)}</div>` : ''}
      <div class="actions" data-action="stop-propagation">
        ${p.translation_status === 'pending' || p.translation_status === 'failed' ?
          `<button class="btn btn-sm btn-primary" data-action="quick-translate" data-paper-id="${esc(p.id)}">翻译</button>` : ''}
        ${p.has_translated ?
          `<button class="btn btn-sm btn-outline" data-action="download-translated-by-id" data-paper-id="${esc(p.id)}">译文</button>` : ''}
        ${p.has_dual ?
          `<button class="btn btn-sm btn-outline" data-action="download-dual-by-id" data-paper-id="${esc(p.id)}">双语</button>` : ''}
        <button class="btn btn-sm btn-outline" data-action="confirm-delete" data-paper-id="${esc(p.id)}" data-paper-title="${esc(p.title)}">删除</button>
      </div>
    </div>
  `).join('');
}

function renderPagination() {
  const container = document.getElementById('pagination');
  if (!container) return;

  const totalPages = Math.ceil(pagination.total / pagination.limit);
  const currentPage = Math.floor(pagination.offset / pagination.limit) + 1;

  if (totalPages <= 1) {
    container.innerHTML = '';
    return;
  }

  let html = '<div class="pagination-controls">';
  if (currentPage > 1) {
    html += `<button class="btn btn-sm btn-outline" data-action="go-to-page" data-page="${currentPage - 1}">上一页</button>`;
  }
  html += `<span class="pagination-info">${currentPage} / ${totalPages} (${pagination.total} 篇)</span>`;
  if (currentPage < totalPages) {
    html += `<button class="btn btn-sm btn-outline" data-action="go-to-page" data-page="${currentPage + 1}">下一页</button>`;
  }
  html += '</div>';
  container.innerHTML = html;
}

function goToPage(page) {
  pagination.offset = (page - 1) * pagination.limit;
  loadPapers();
}

// === Upload ===
function initDropZone() {
  const zone = document.getElementById('drop-zone');
  if (!zone) return;
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.classList.remove('dragover');
    const file = e.dataTransfer.files[0];
    if (file && file.name.toLowerCase().endsWith('.pdf')) {
      selectFile(file);
    }
  });
}

function handleFileSelect(input) {
  if (input.files[0]) selectFile(input.files[0]);
}

function selectFile(file) {
  selectedFile = file;
  document.getElementById('drop-zone').classList.add('hidden');
  document.getElementById('upload-preview').classList.remove('hidden');
  document.getElementById('file-name').textContent = file.name;
  document.getElementById('file-size').textContent = formatSize(file.size);
}

function cancelUpload() {
  selectedFile = null;
  document.getElementById('drop-zone').classList.remove('hidden');
  document.getElementById('upload-preview').classList.add('hidden');
  document.getElementById('file-input').value = '';
}

async function doUpload() {
  if (!selectedFile) return;
  const tags = document.getElementById('upload-tags').value;
  const prog = document.getElementById('upload-progress');
  const fill = document.getElementById('upload-progress-fill');
  const status = document.getElementById('upload-status');

  document.getElementById('upload-preview').classList.add('hidden');
  prog.classList.remove('hidden');
  fill.style.width = '30%';
  status.textContent = '上传中...';

  try {
    fill.style.width = '60%';
    await api.uploadPaper(selectedFile, tags);
    fill.style.width = '100%';
    status.textContent = '上传成功！';
    setTimeout(() => showLibrary(), 800);
  } catch (e) {
    status.textContent = `上传失败: ${e.message}`;
    fill.style.background = 'var(--error)';
  }
}

// === Reader (PDF.js with Virtual Scrolling) ===
let syncScrollEnabled = true;
let pdfDocs = { original: null, translated: null };
let scrollSyncing = false;
let pageMetrics = { original: [], translated: [] }; // cached {top, height} per page
let renderedPages = { original: new Set(), translated: new Set() };
let pageWrappers = { original: [], translated: [] };
let renderObservers = { original: null, translated: null };
let scrollListeners = { original: null, translated: null };
const RENDER_SCALE = 1.5;
const OVERSCAN_PX = 600; // render pages within this distance of viewport

// Configure PDF.js worker
if (typeof pdfjsLib !== 'undefined') {
  pdfjsLib.GlobalWorkerOptions.workerSrc = '/static/js/pdf.worker.min.js';
}

async function openReader(paperId) {
  try {
    currentPaper = await api.getPaper(paperId);
  } catch (e) {
    alert('无法加载论文');
    return;
  }
  showView('reader');
  document.getElementById('reader-title').textContent = currentPaper.title;

  const placeholder = document.getElementById('translate-placeholder');

  // Clean up previous observers and scroll listeners
  if (renderObservers.original) renderObservers.original.disconnect();
  if (renderObservers.translated) renderObservers.translated.disconnect();
  for (const panel of ['original', 'translated']) {
    const el = document.getElementById(`pdf-container-${panel}`);
    if (el && scrollListeners[panel]) {
      el.removeEventListener('scroll', scrollListeners[panel]);
      scrollListeners[panel] = null;
    }
  }
  pdfDocs = { original: null, translated: null };
  pageMetrics = { original: [], translated: [] };
  renderedPages = { original: new Set(), translated: new Set() };
  pageWrappers = { original: [], translated: [] };

  // Load original PDF
  await loadPdfDocument('original', `/api/papers/${paperId}/view/original`);

  if (currentPaper.has_translated) {
    placeholder.classList.add('hidden');
    document.getElementById('pdf-container-translated').classList.remove('hidden');
    await loadPdfDocument('translated', `/api/papers/${paperId}/view/translated`);
    document.getElementById('btn-download-mono').classList.remove('hidden');
  } else {
    placeholder.classList.remove('hidden');
    document.getElementById('pdf-container-translated').classList.add('hidden');
    document.getElementById('btn-download-mono').classList.add('hidden');
  }

  currentPaper.has_dual
    ? document.getElementById('btn-download-dual').classList.remove('hidden')
    : document.getElementById('btn-download-dual').classList.add('hidden');

  if (currentPaper.translation_status === 'translating') {
    pollTranslationStatus(paperId);
  }
}

async function loadPdfDocument(panel, url) {
  const container = document.getElementById(`pdf-container-${panel}`);
  container.textContent = '';
  const loadingDiv = document.createElement('div');
  loadingDiv.style.cssText = 'color:#aaa;padding:20px;';
  loadingDiv.textContent = '加载中...';
  container.appendChild(loadingDiv);

  try {
    const loadingTask = pdfjsLib.getDocument(url);
    const pdf = await loadingTask.promise;
    pdfDocs[panel] = pdf;

    container.innerHTML = '';

    // Phase 1: Measure all page dimensions quickly (needed for scrollbar sizing)
    const metrics = [];
    for (let i = 1; i <= pdf.numPages; i++) {
      const page = await pdf.getPage(i);
      const viewport = page.getViewport({ scale: RENDER_SCALE });
      const gap = 4; // matches the 4px gap in CSS
      const top = metrics.length > 0
        ? metrics[metrics.length - 1].top + metrics[metrics.length - 1].height + gap
        : 0;
      metrics.push({ top, height: viewport.height, width: viewport.width, pageNum: i });
    }
    pageMetrics[panel] = metrics;

    // Phase 2: Create placeholder wrappers with reserved height
    const wrappers = [];
    for (let i = 0; i < metrics.length; i++) {
      const wrapper = document.createElement('div');
      wrapper.className = 'pdf-page-wrapper';
      wrapper.style.width = metrics[i].width + 'px';
      wrapper.style.height = metrics[i].height + 'px';
      wrapper.style.flexShrink = '0';
      wrapper.dataset.pageIdx = i;
      container.appendChild(wrapper);
      wrappers.push(wrapper);
    }
    pageWrappers[panel] = wrappers;

    // Phase 3: Set up intersection observer for lazy rendering
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        const idx = parseInt(entry.target.dataset.pageIdx);
        if (entry.isIntersecting) {
          renderPage(panel, idx);
        }
      }
    }, {
      root: container,
      rootMargin: `${OVERSCAN_PX}px 0px ${OVERSCAN_PX}px 0px`
    });
    for (const wrapper of wrappers) {
      observer.observe(wrapper);
    }
    renderObservers[panel] = observer;

    // Also do an initial pass for pages near the top
    await renderVisiblePages(panel, container);

    // Setup scroll sync
    setupSmoothScrollSync(panel);

  } catch (e) {
    container.textContent = '';
    const errDiv = document.createElement('div');
    errDiv.style.cssText = 'color:#f44336;padding:20px;';
    errDiv.textContent = '加载失败: ' + (e.message || '未知错误');
    container.appendChild(errDiv);
  }
}

async function renderVisiblePages(panel, container) {
  const scrollTop = container.scrollTop;
  const viewBottom = scrollTop + container.clientHeight;
  const metrics = pageMetrics[panel];

  for (let i = 0; i < metrics.length; i++) {
    const pageBottom = metrics[i].top + metrics[i].height;
    if (pageBottom >= scrollTop - OVERSCAN_PX && metrics[i].top <= viewBottom + OVERSCAN_PX) {
      await renderPage(panel, i);
    }
  }
}

async function renderPage(panel, idx) {
  if (renderedPages[panel].has(idx)) return;
  renderedPages[panel].add(idx);

  const pdf = pdfDocs[panel];
  if (!pdf) return;

  const wrapper = pageWrappers[panel][idx];
  if (!wrapper) return;

  const pageNum = pageMetrics[panel][idx].pageNum;
  const page = await pdf.getPage(pageNum);
  const viewport = page.getViewport({ scale: RENDER_SCALE });

  const canvas = document.createElement('canvas');
  const dpr = window.devicePixelRatio || 1;
  canvas.width = viewport.width * dpr;
  canvas.height = viewport.height * dpr;
  canvas.style.width = viewport.width + 'px';
  canvas.style.height = viewport.height + 'px';

  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  await page.render({ canvasContext: ctx, viewport }).promise;

  // Replace wrapper content
  wrapper.innerHTML = '';
  wrapper.appendChild(canvas);
}

// Scroll sync with rAF throttling
let scrollRafId = null;

function setupSmoothScrollSync(panel) {
  const container = document.getElementById(`pdf-container-${panel}`);
  const otherPanel = panel === 'original' ? 'translated' : 'original';

  const listener = () => {
    if (!syncScrollEnabled || scrollSyncing) return;
    if (!pdfDocs[otherPanel]) return;

    if (scrollRafId) return; // already scheduled
    scrollRafId = requestAnimationFrame(() => {
      scrollRafId = null;

      const scrollTop = container.scrollTop;
      const maxScroll = container.scrollHeight - container.clientHeight;
      const scrollPct = maxScroll > 0 ? scrollTop / maxScroll : 0;

      scrollSyncing = true;
      const otherContainer = document.getElementById(`pdf-container-${otherPanel}`);
      const otherMaxScroll = otherContainer.scrollHeight - otherContainer.clientHeight;
      otherContainer.scrollTop = scrollPct * otherMaxScroll;

      // Trigger lazy render for the other panel too
      renderVisiblePages(otherPanel, otherContainer);

      // Update page info using cached metrics (no getBoundingClientRect)
      updatePageInfo(panel, scrollTop);
      updatePageInfo(otherPanel, otherContainer.scrollTop);

      requestAnimationFrame(() => { scrollSyncing = false; });
    });
  };
  container.addEventListener('scroll', listener);
  scrollListeners[panel] = listener;
}

function updatePageInfo(panel, scrollTop) {
  const metrics = pageMetrics[panel];
  if (!metrics || metrics.length === 0) return;

  let currentPage = 1;
  for (let i = 0; i < metrics.length; i++) {
    if (scrollTop >= metrics[i].top - 50) {
      currentPage = i + 1;
    }
  }

  const infoEl = document.getElementById(`page-info-${panel}`);
  if (infoEl) infoEl.textContent = `第 ${currentPage} / ${metrics.length} 页`;
}

function toggleSyncScroll() {
  syncScrollEnabled = !syncScrollEnabled;
  const btn = document.getElementById('btn-sync-scroll');
  if (btn) {
    btn.textContent = syncScrollEnabled ? '同步滚动: 开' : '同步滚动: 关';
    btn.classList.toggle('btn-primary', syncScrollEnabled);
    btn.classList.toggle('btn-outline', !syncScrollEnabled);
  }
}

// === Translation ===
function showTranslateDialog(paperId) {
  const dialog = document.createElement('div');
  dialog.className = 'modal-overlay';
  dialog.innerHTML = `
    <div class="modal">
      <h3>选择翻译引擎</h3>
      <select id="translate-backend">
        <option value="google">Google (免费，无需API Key)</option>
        <option value="deepseek">DeepSeek (需要API Key)</option>
        <option value="openai">OpenAI (需要API Key)</option>
        <option value="deepl">DeepL (需要API Key)</option>
        <option value="ollama">Ollama (本地)</option>
      </select>
      <div class="modal-actions">
        <button class="btn btn-primary" data-action="do-translate-modal" data-paper-id="${esc(paperId)}">开始翻译</button>
        <button class="btn btn-outline" data-action="close-modal">取消</button>
      </div>
    </div>
  `;
  dialog.addEventListener('click', (e) => {
    if (e.target === dialog) dialog.remove();
  });
  document.body.appendChild(dialog);
}

async function doTranslate(paperId, btn) {
  const backend = document.getElementById('translate-backend').value;
  btn.closest('.modal-overlay').remove();
  try {
    await api.translatePaper(paperId, backend);
    pollTranslationStatus(paperId);
  } catch (e) {
    alert(e.message);
  }
}

async function startTranslate() {
  if (!currentPaper) return;
  const quality = document.getElementById('quality-preset')?.value || 'balanced';
  doTranslateDirect(currentPaper.id, '', quality);
}

async function quickTranslate(paperId) {
  doTranslateDirect(paperId, '', 'balanced');
}

async function doTranslateDirect(paperId, backend, quality) {
  try {
    await api.translatePaper(paperId, backend, quality);
    pollTranslationStatus(paperId);
  } catch (e) {
    alert(e.message);
  }
}

function pollTranslationStatus(paperId) {
  // Clear any existing poll
  if (translationPollId) {
    clearInterval(translationPollId);
    translationPollId = null;
  }

  const prog = document.getElementById('translation-progress');
  const fill = document.getElementById('trans-progress-fill');
  const statusEl = document.getElementById('trans-status');
  const percentEl = document.getElementById('trans-percent');
  const logEl = document.getElementById('trans-log');
  prog.classList.remove('hidden');
  if (logEl) logEl.innerHTML = '';
  addTransLog('开始翻译...');

  const MAX_POLLS = 300; // 10 minutes at 2s intervals
  const MAX_CONSECUTIVE_ERRORS = 10; // stop after 20s of consecutive failures
  let pollCount = 0;
  let consecutiveErrors = 0;

  translationPollId = setInterval(async () => {
    pollCount++;
    if (pollCount > MAX_POLLS) {
      clearInterval(translationPollId);
      translationPollId = null;
      statusEl.textContent = '翻译超时';
      addTransLog('翻译超时，请稍后重试', 'error');
      fill.style.background = 'var(--error)';
      loadPapers();
      return;
    }

    try {
      const paper = await api.getPaper(paperId);
      consecutiveErrors = 0;
      const pct = Math.round(paper.translation_progress * 100);
      fill.style.width = `${pct}%`;
      if (percentEl) percentEl.textContent = `${pct}%`;

      if (paper.translation_status === 'completed') {
        clearInterval(translationPollId);
        translationPollId = null;
        statusEl.textContent = '翻译完成';
        addTransLog('翻译完成！', 'success');
        setTimeout(() => {
          prog.classList.add('hidden');
          if (currentPaper && currentPaper.id === paperId) {
            openReader(paperId);
          }
          loadPapers();
        }, 1500);
      } else if (paper.translation_status === 'failed') {
        clearInterval(translationPollId);
        translationPollId = null;
        statusEl.textContent = '翻译失败';
        addTransLog(`错误: ${paper.translation_error || '未知错误'}`, 'error');
        fill.style.background = 'var(--error)';
        loadPapers();
      } else {
        statusEl.textContent = '翻译中...';
        if (pct > 0 && pct % 10 === 0) {
          addTransLog(`进度 ${pct}%`);
        }
      }
    } catch (e) {
      consecutiveErrors++;
      if (consecutiveErrors >= MAX_CONSECUTIVE_ERRORS) {
        clearInterval(translationPollId);
        translationPollId = null;
        statusEl.textContent = '连接中断';
        addTransLog('无法连接服务器，请检查网络后重试', 'error');
        fill.style.background = 'var(--error)';
        loadPapers();
      }
    }
  }, 2000);
}

function addTransLog(msg, cls = '') {
  const logEl = document.getElementById('trans-log');
  if (!logEl) return;
  const entry = document.createElement('div');
  entry.className = `log-entry ${cls}`;
  const time = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  entry.textContent = `[${time}] ${msg}`;
  logEl.appendChild(entry);
  logEl.scrollTop = logEl.scrollHeight;
}

// === Downloads ===
function downloadTranslated() {
  if (currentPaper) window.open(`/api/papers/${currentPaper.id}/download/translated`);
}
function downloadDual() {
  if (currentPaper) window.open(`/api/papers/${currentPaper.id}/download/dual`);
}
function downloadTranslatedById(id) {
  window.open(`/api/papers/${id}/download/translated`);
}
function downloadDualById(id) {
  window.open(`/api/papers/${id}/download/dual`);
}

// === Delete ===
async function confirmDelete(id, title) {
  if (!confirm(`确定删除 "${title}"？`)) return;
  try {
    await api.deletePaper(id);
    loadPapers();
    if (currentPaper && currentPaper.id === id) showLibrary();
  } catch (e) {
    alert('删除失败');
  }
}

// === Resizer ===
function initResizer() {
  const resizer = document.getElementById('resizer');
  const left = document.getElementById('left-panel');
  const right = document.getElementById('right-panel');
  if (!resizer) return;

  let startX, startLeftW;
  resizer.addEventListener('mousedown', e => {
    startX = e.clientX;
    startLeftW = left.getBoundingClientRect().width;
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', () => {
      document.removeEventListener('mousemove', onMove);
    }, { once: true });
  });

  function onMove(e) {
    const dx = e.clientX - startX;
    const containerW = left.parentElement.getBoundingClientRect().width - 6;
    const newLeftW = Math.max(200, Math.min(containerW - 200, startLeftW + dx));
    left.style.flex = `0 0 ${newLeftW}px`;
    right.style.flex = '1';
  }
}

// === Helpers ===
const _escDiv = document.createElement('div');
function esc(s) {
  _escDiv.textContent = s || '';
  return _escDiv.innerHTML.replace(/'/g, '&#39;');
}

function sanitizeClass(s) {
  return String(s || '').replace(/[^a-zA-Z0-9_-]/g, '');
}

function formatSize(bytes) {
  if (bytes == null || isNaN(bytes)) return '0 B';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`;
}

function statusLabel(s) {
  const map = { pending: '待翻译', translating: '翻译中', completed: '已完成', failed: '失败' };
  return map[s] || esc(s);
}

// === Event Delegation ===
const actionHandlers = {
  'show-upload': showUpload,
  'show-library': showLibrary,
  'batch-translate': batchTranslate,
  'do-upload': doUpload,
  'cancel-upload': cancelUpload,
  'start-translate': startTranslate,
  'toggle-sync-scroll': toggleSyncScroll,
  'download-translated': downloadTranslated,
  'download-dual': downloadDual,
  'debounce-search': debounceSearch,
  'load-papers': loadPapers,
  'handle-file-select': (e) => handleFileSelect(e.target),
  'open-reader': (e) => {
    const id = e.target.closest('[data-paper-id]')?.dataset.paperId;
    if (id) openReader(id);
  },
  'stop-propagation': (e) => e.stopPropagation(),
  'quick-translate': (e) => {
    const id = e.target.closest('[data-paper-id]')?.dataset.paperId;
    if (id) quickTranslate(id);
  },
  'download-translated-by-id': (e) => {
    const id = e.target.closest('[data-paper-id]')?.dataset.paperId;
    if (id) downloadTranslatedById(id);
  },
  'download-dual-by-id': (e) => {
    const id = e.target.closest('[data-paper-id]')?.dataset.paperId;
    if (id) downloadDualById(id);
  },
  'confirm-delete': (e) => {
    const el = e.target.closest('[data-paper-id]');
    if (el) confirmDelete(el.dataset.paperId, el.dataset.paperTitle || '');
  },
  'go-to-page': (e) => {
    const page = parseInt(e.target.dataset.page, 10);
    if (page > 0) goToPage(page);
  },
  'do-translate-modal': (e) => {
    const id = e.target.closest('[data-paper-id]')?.dataset.paperId;
    if (id) doTranslate(id, e.target);
  },
  'close-modal': (e) => {
    e.target.closest('.modal-overlay')?.remove();
  },
};

document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-action]');
  const action = el?.dataset.action;
  if (action && actionHandlers[action]) {
    actionHandlers[action](e);
  }
});

document.addEventListener('input', (e) => {
  const action = e.target.dataset.action;
  if (action && actionHandlers[action]) {
    actionHandlers[action](e);
  }
});

document.addEventListener('change', (e) => {
  const action = e.target.dataset.action;
  if (action && actionHandlers[action]) {
    actionHandlers[action](e);
  }
});

// === Init ===
document.addEventListener('DOMContentLoaded', () => {
  initDropZone();
  initResizer();
  loadPapers();
});
