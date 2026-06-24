/* Paper China - Frontend Application */

let papers = [];
let currentPaper = null;
let selectedFiles = [];
let searchTimer = null;
let translationPollId = null;
let pagination = { offset: 0, limit: 50, total: 0 };
let currentLoadId = 0; // Track which paper is being loaded to prevent race conditions

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
  uploadPaperWithProgress(file, tags, onProgress) {
    return new Promise((resolve, reject) => {
      const form = new FormData();
      form.append('file', file);
      if (tags) form.append('tags', tags);
      const xhr = new XMLHttpRequest();
      xhr.upload.addEventListener('progress', e => {
        if (e.lengthComputable) onProgress(e.loaded / e.total);
      });
      xhr.addEventListener('load', () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText));
        } else {
          try { reject(new Error(JSON.parse(xhr.responseText).detail || 'Upload failed')); }
          catch { reject(new Error('Upload failed')); }
        }
      });
      xhr.addEventListener('error', () => reject(new Error('Network error')));
      xhr.open('POST', '/api/papers/upload');
      xhr.send(form);
    });
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
  async cancelTranslation(id) {
    const res = await fetch(`/api/papers/${id}/cancel`, { method: 'POST' });
    if (!res.ok) throw new Error(await errorDetail(res, 'Cancel failed'));
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
  selectedFiles = [];
  document.getElementById('upload-preview').classList.add('hidden');
  document.getElementById('upload-progress').classList.add('hidden');
  document.getElementById('drop-zone').classList.remove('hidden');
  document.getElementById('file-input').value = '';
}

// === Paper Library ===
async function loadPapers() {
  const search = document.getElementById('search-input').value;
  const status = document.getElementById('status-filter').value;
  const skeleton = document.getElementById('loading-skeleton');
  const paperList = document.getElementById('paper-list');

  // Show skeleton on first load
  if (skeleton && paperList && paperList.children.length === 0) {
    skeleton.classList.remove('hidden');
  }

  try {
    const data = await api.listPapers(search, status, pagination.offset, pagination.limit);
    papers = data.papers;
    pagination.total = data.total;
    renderPaperList();
    renderPagination();
    updateStats();
  } catch (e) {
    console.error('Failed to load papers:', e);
    const container = document.getElementById('paper-list');
    if (container) container.innerHTML = '<div class="empty-state"><p style="color:var(--error)">加载失败</p><button class="btn btn-primary" onclick="loadPapers()">重试</button></div>';
  } finally {
    // Hide skeleton after data loads
    if (skeleton) skeleton.classList.add('hidden');
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

let batchTranslating = false;

async function batchTranslate() {
  if (batchTranslating) return;

  const pending = papers.filter(p => p.translation_status === 'pending' || p.translation_status === 'failed');
  if (pending.length === 0) {
    toastWarning('没有待翻译的论文');
    return;
  }

  if (!confirm(`确定翻译 ${pending.length} 篇论文？`)) return;

  batchTranslating = true;
  const quality = document.getElementById('quality-preset')?.value || 'balanced';

  try {
    // Submit all translations in parallel (server runs them in background)
    const results = await Promise.allSettled(
      pending.map(p => api.translatePaper(p.id, '', quality))
    );
    const success = results.filter(r => r.status === 'fulfilled').length;
    const failed = results.length - success;

    toastSuccess(`已提交 ${success} 篇翻译任务${failed > 0 ? `，${failed} 篇提交失败` : ''}。翻译在后台进行中。`);
    loadPapers();
  } finally {
    batchTranslating = false;
  }
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
      ${p.translation_status === 'translating' ? `
        <div class="progress-bar" style="margin-top:8px">
          <div class="progress-fill" style="width:${Math.round(p.translation_progress * 100)}%"></div>
        </div>
      ` : ''}
      ${p.translation_status === 'completed' ? `
        <div class="meta" style="margin-top:4px;color:var(--success)">✓ 翻译完成 ${formatDate(p.updated_at)}</div>
      ` : ''}
      ${p.translation_status === 'failed' && p.translation_error ? `
        <div class="meta" style="margin-top:4px;color:var(--error);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(p.translation_error)}">⚠ ${esc(p.translation_error)}</div>
      ` : ''}
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
    const files = Array.from(e.dataTransfer.files).filter(f => f.name.toLowerCase().endsWith('.pdf'));
    if (files.length > 0) addFiles(files);
  });
}

function handleFileSelect(input) {
  const files = Array.from(input.files).filter(f => f.name.toLowerCase().endsWith('.pdf'));
  if (files.length > 0) addFiles(files);
  input.value = '';
}

function addFiles(files) {
  const maxSize = 100 * 1024 * 1024;
  for (const f of files) {
    if (f.size > maxSize) { toastWarning(`${f.name} 过大（最大 100MB）`); continue; }
    if (f.size === 0) { toastWarning(`${f.name} 为空`); continue; }
    if (!selectedFiles.some(s => s.name === f.name && s.size === f.size)) {
      selectedFiles.push(f);
    }
  }
  if (selectedFiles.length === 0) return;
  document.getElementById('drop-zone').classList.add('hidden');
  document.getElementById('upload-preview').classList.remove('hidden');
  renderFileList();
}

function renderFileList() {
  const container = document.getElementById('file-list');
  container.innerHTML = selectedFiles.map((f, i) => `
    <div class="file-item">
      <span class="file-item-name">${esc(f.name)}</span>
      <span class="file-item-size">${formatSize(f.size)}</span>
      <button class="file-item-remove" data-action="remove-file" data-index="${i}" title="移除">&times;</button>
    </div>
  `).join('');
  const btn = document.getElementById('btn-do-upload');
  btn.textContent = selectedFiles.length > 1 ? `上传 ${selectedFiles.length} 篇` : '上传';
}

function removeFile(index) {
  selectedFiles.splice(index, 1);
  if (selectedFiles.length === 0) {
    cancelUpload();
  } else {
    renderFileList();
  }
}

function cancelUpload() {
  selectedFiles = [];
  document.getElementById('drop-zone').classList.remove('hidden');
  document.getElementById('upload-preview').classList.add('hidden');
  document.getElementById('upload-progress').classList.add('hidden');
}

let uploading = false;

async function doUpload() {
  if (selectedFiles.length === 0 || uploading) return;
  uploading = true;

  const tags = document.getElementById('upload-tags').value;
  const prog = document.getElementById('upload-progress');
  const fill = document.getElementById('upload-progress-fill');
  const status = document.getElementById('upload-status');

  document.getElementById('upload-preview').classList.add('hidden');
  prog.classList.remove('hidden');

  let success = 0;
  let failed = 0;
  const total = selectedFiles.length;

  for (let i = 0; i < total; i++) {
    const file = selectedFiles[i];
    fill.style.width = '0%';
    fill.style.background = '';
    status.textContent = `上传中 (${i + 1}/${total}): ${file.name}`;

    try {
      await api.uploadPaperWithProgress(file, tags, pct => {
        fill.style.width = `${Math.round(pct * 100)}%`;
      });
      success++;
    } catch (e) {
      failed++;
      toastError(`${file.name} 上传失败: ${e.message}`);
    }
  }

  fill.style.width = '100%';
  fill.style.background = failed > 0 ? 'var(--warning)' : 'var(--success)';
  status.textContent = `完成！${success} 篇成功${failed > 0 ? `，${failed} 篇失败` : ''}`;
  toastSuccess(`上传完成：${success} 篇成功${failed > 0 ? `，${failed} 篇失败` : ''}`);
  selectedFiles = [];
  setTimeout(() => showLibrary(), 1000);
  uploading = false;
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
const MIN_SCALE = 0.5;
const MAX_SCALE = 3.0;
const OVERSCAN_PX = 600; // render pages within this distance of viewport

function getRenderScale(panel) {
  const container = document.getElementById(`pdf-container-${panel}`);
  if (!container) return RENDER_SCALE;
  const containerWidth = container.clientWidth - 20; // padding
  const metrics = pageMetrics[panel];
  if (!metrics || metrics.length === 0) return RENDER_SCALE;
  // Scale so the widest page fits the container
  const maxPageWidth = Math.max(...metrics.map(m => m.width / RENDER_SCALE));
  const fitScale = containerWidth / maxPageWidth;
  return Math.max(MIN_SCALE, Math.min(MAX_SCALE, fitScale));
}

// Configure PDF.js worker
if (typeof pdfjsLib !== 'undefined') {
  pdfjsLib.GlobalWorkerOptions.workerSrc = '/static/js/pdf.worker.min.js';
}

async function openReader(paperId) {
  // Cancel any ongoing load
  const loadId = ++currentLoadId;

  // Show loading state immediately
  showView('reader');
  document.getElementById('reader-title').textContent = '加载中...';
  for (const panel of ['original', 'translated']) {
    const container = document.getElementById(`pdf-container-${panel}`);
    if (container) {
      container.innerHTML = '<div style="color:#aaa;padding:40px;text-align:center;">加载中...</div>';
    }
  }

  try {
    currentPaper = await api.getPaper(paperId);
  } catch (e) {
    toastError('无法加载论文');
    showLibrary();
    return;
  }

  // If a newer load started, abort this one
  if (loadId !== currentLoadId) return;

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
  if (loadId !== currentLoadId) return;

  if (currentPaper.has_translated) {
    placeholder.classList.add('hidden');
    document.getElementById('pdf-container-translated').classList.remove('hidden');
    await loadPdfDocument('translated', `/api/papers/${paperId}/view/translated`);
    if (loadId !== currentLoadId) return;
    document.getElementById('btn-download-mono').classList.remove('hidden');
  } else {
    placeholder.classList.remove('hidden');
    document.getElementById('pdf-container-translated').classList.add('hidden');
    document.getElementById('btn-download-mono').classList.add('hidden');

    // Show error details and retry button for failed translations
    const placeholderIcon = placeholder.querySelector('.placeholder-icon');
    const placeholderText = placeholder.querySelector('p');
    if (currentPaper.translation_status === 'failed') {
      if (placeholderIcon) placeholderIcon.textContent = '⚠️';
      if (placeholderText) {
        placeholderText.textContent = currentPaper.translation_error
          ? `翻译失败: ${currentPaper.translation_error}`
          : '翻译失败';
        placeholderText.style.color = 'var(--error)';
      }
    } else {
      if (placeholderIcon) placeholderIcon.textContent = '🌐';
      if (placeholderText) {
        placeholderText.textContent = '点击"翻译"按钮生成中文译文';
        placeholderText.style.color = '';
      }
    }
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

    // Phase 1: Measure all page dimensions at base scale
    const baseMetrics = [];
    for (let i = 1; i <= pdf.numPages; i++) {
      const page = await pdf.getPage(i);
      const viewport = page.getViewport({ scale: RENDER_SCALE });
      baseMetrics.push({ height: viewport.height, width: viewport.width, pageNum: i });
    }

    // Calculate adaptive scale to fit container width
    const containerWidth = container.clientWidth - 20;
    const maxPageWidth = Math.max(...baseMetrics.map(m => m.width));
    const adaptiveScale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, containerWidth / maxPageWidth * RENDER_SCALE));

    const metrics = [];
    const gap = 4;
    for (let i = 0; i < baseMetrics.length; i++) {
      const scale = adaptiveScale / RENDER_SCALE;
      const w = baseMetrics[i].width * scale;
      const h = baseMetrics[i].height * scale;
      const top = metrics.length > 0
        ? metrics[metrics.length - 1].top + metrics[metrics.length - 1].height + gap
        : 0;
      metrics.push({
        top, height: h, width: w,
        baseWidth: baseMetrics[i].width, baseHeight: baseMetrics[i].height,
        pageNum: baseMetrics[i].pageNum,
      });
    }
    pageMetrics[panel] = metrics;
    pageMetrics[panel]._adaptiveScale = adaptiveScale;
    pageMetrics[panel]._baseScale = RENDER_SCALE;

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
  const scale = pageMetrics[panel]._adaptiveScale || RENDER_SCALE;
  const viewport = page.getViewport({ scale });

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

// Scroll sync with page-based alignment
let scrollRafId = null;

function setupSmoothScrollSync(panel) {
  const container = document.getElementById(`pdf-container-${panel}`);
  const otherPanel = panel === 'original' ? 'translated' : 'original';

  const listener = () => {
    if (!syncScrollEnabled || scrollSyncing) return;
    if (!pdfDocs[otherPanel]) return;

    if (scrollRafId) return;
    scrollRafId = requestAnimationFrame(() => {
      scrollRafId = null;

      const scrollTop = container.scrollTop;
      const metrics = pageMetrics[panel];
      const otherMetrics = pageMetrics[otherPanel];
      if (!metrics || !otherMetrics || metrics.length === 0 || otherMetrics.length === 0) return;

      // Find which page is currently visible (top of viewport)
      let currentPageIdx = 0;
      for (let i = metrics.length - 1; i >= 0; i--) {
        if (metrics[i].top <= scrollTop + 20) {
          currentPageIdx = i;
          break;
        }
      }

      // Calculate fractional position within the current page
      const pageTop = metrics[currentPageIdx].top;
      const pageHeight = metrics[currentPageIdx].height;
      const fraction = pageHeight > 0 ? Math.max(0, Math.min(1, (scrollTop - pageTop) / pageHeight)) : 0;

      // Map to corresponding page in the other panel
      const otherIdx = Math.min(currentPageIdx, otherMetrics.length - 1);
      const otherPageTop = otherMetrics[otherIdx].top;
      const otherPageHeight = otherMetrics[otherIdx].height;

      scrollSyncing = true;
      const otherContainer = document.getElementById(`pdf-container-${otherPanel}`);
      otherContainer.scrollTop = otherPageTop + fraction * otherPageHeight;

      updatePageInfo(panel, scrollTop);
      updatePageInfo(otherPanel, otherContainer.scrollTop);

      requestAnimationFrame(() => { scrollSyncing = false; });
    });
  };
  container.addEventListener('scroll', listener, { passive: true });
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

async function cancelTranslation() {
  if (!currentPaper) return;
  try {
    await api.cancelTranslation(currentPaper.id);
    toastInfo('翻译取消请求已发送');
  } catch (e) {
    toastError(e.message);
  }
}

// === Translation ===
let translating = false;

async function startTranslate() {
  if (!currentPaper) return;
  const quality = document.getElementById('quality-preset')?.value || 'balanced';
  doTranslateDirect(currentPaper.id, '', quality);
}

async function quickTranslate(paperId) {
  // Set currentPaper for title display in progress panel
  if (!currentPaper || currentPaper.id !== paperId) {
    currentPaper = papers.find(p => p.id === paperId) || null;
  }
  doTranslateDirect(paperId, '', 'balanced');
}

async function doTranslateDirect(paperId, backend, quality) {
  if (translating) return;
  translating = true;
  try {
    await api.translatePaper(paperId, backend, quality);
    pollTranslationStatus(paperId);
  } catch (e) {
    toastError(e.message);
  } finally {
    translating = false;
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
  const titleEl = document.getElementById('trans-title');
  prog.classList.remove('hidden');

  // Start elapsed time timer
  const startTime = Date.now();

  // Show paper title in progress panel
  if (titleEl && currentPaper) {
    titleEl.textContent = currentPaper.title;
  }
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
      const pct = Math.round(Math.max(0, Math.min(1, paper.translation_progress)) * 100);
      fill.style.width = `${pct}%`;
      if (percentEl) percentEl.textContent = `${pct}%`;

      // Display server-side translation log
      if (paper.translation_log && logEl) {
        const logLines = paper.translation_log.split('\n').filter(l => l.trim());
        logEl.innerHTML = logLines.map(l => {
          const cls = l.includes('失败') ? 'error' : l.includes('完成') ? 'success' : '';
          return `<div class="log-entry ${cls}">${esc(l)}</div>`;
        }).join('');
        logEl.scrollTop = logEl.scrollHeight;
      }

      const elapsed = Math.floor((Date.now() - startTime) / 1000);
      const elapsedStr = elapsed < 60 ? `${elapsed}s` : `${Math.floor(elapsed / 60)}m${elapsed % 60}s`;

      if (paper.translation_status === 'completed') {
        clearInterval(translationPollId);
        translationPollId = null;
        statusEl.textContent = `翻译完成 (${elapsedStr})`;
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
        statusEl.textContent = `翻译失败 (${elapsedStr})`;
        fill.style.background = 'var(--error)';
        loadPapers();
      } else {
        statusEl.textContent = `翻译中... ${elapsedStr}`;
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
function downloadFile(url) {
  const a = document.createElement('a');
  a.href = url;
  a.download = '';
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

function downloadTranslated() {
  if (currentPaper) downloadFile(`/api/papers/${currentPaper.id}/download/translated`);
}
function downloadDual() {
  if (currentPaper) downloadFile(`/api/papers/${currentPaper.id}/download/dual`);
}
function downloadTranslatedById(id) {
  downloadFile(`/api/papers/${id}/download/translated`);
}
function downloadDualById(id) {
  downloadFile(`/api/papers/${id}/download/dual`);
}

// === Delete ===
async function confirmDelete(id, title) {
  if (!confirm(`确定删除 "${title}"？`)) return;
  try {
    await api.deletePaper(id);
    toastSuccess('论文已删除');
    loadPapers();
    if (currentPaper && currentPaper.id === id) showLibrary();
  } catch (e) {
    toastError('删除失败');
  }
}

// === Resizer ===
let resizeTimer = null;

function initResizer() {
  const resizer = document.getElementById('resizer');
  const left = document.getElementById('left-panel');
  const right = document.getElementById('right-panel');
  if (!resizer) return;

  let startX, startLeftW;

  resizer.addEventListener('mousedown', e => {
    e.preventDefault();
    startX = e.clientX;
    startLeftW = left.getBoundingClientRect().width;
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', () => {
      document.removeEventListener('mousemove', onMove);
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
      reRenderPanel('original');
      reRenderPanel('translated');
    }, { once: true });
  });

  function onMove(e) {
    const dx = e.clientX - startX;
    const containerW = left.parentElement.getBoundingClientRect().width - 6;
    const newLeftW = Math.max(200, Math.min(containerW - 200, startLeftW + dx));
    left.style.flex = `0 0 ${newLeftW}px`;
    right.style.flex = '1';

    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      reRenderPanel('original');
      reRenderPanel('translated');
    }, 200);
  }

  // Double-click to reset to 50/50 split
  resizer.addEventListener('dblclick', () => {
    left.style.flex = '1';
    right.style.flex = '1';
    reRenderPanel('original');
    reRenderPanel('translated');
  });
}

function reRenderPanel(panel) {
  const pdf = pdfDocs[panel];
  if (!pdf) return;
  const container = document.getElementById(`pdf-container-${panel}`);
  if (!container) return;

  // Clear rendered pages and re-calculate scale
  renderedPages[panel] = new Set();
  const metrics = pageMetrics[panel];
  if (!metrics || metrics.length === 0) return;

  // Use base dimensions (at RENDER_SCALE) to avoid compounding mutations
  const baseScale = metrics._baseScale || RENDER_SCALE;
  const containerWidth = container.clientWidth - 20;
  const maxPageWidth = Math.max(...metrics.map(m => m.baseWidth));
  const adaptiveScale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, containerWidth / maxPageWidth));
  pageMetrics[panel]._adaptiveScale = adaptiveScale;

  // Update wrapper sizes from base dimensions
  const gap = 4;
  const wrappers = pageWrappers[panel];
  const scaleRatio = adaptiveScale / baseScale;
  for (let i = 0; i < metrics.length; i++) {
    const w = metrics[i].baseWidth * scaleRatio;
    const h = metrics[i].baseHeight * scaleRatio;
    metrics[i].width = w;
    metrics[i].height = h;
    metrics[i].top = i === 0 ? 0 : metrics[i - 1].top + metrics[i - 1].height + gap;
    if (wrappers[i]) {
      wrappers[i].style.width = w + 'px';
      wrappers[i].style.height = h + 'px';
      wrappers[i].innerHTML = ''; // Clear canvas
    }
  }

  // Re-render visible pages
  renderVisiblePages(panel, container);
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
  if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  return (bytes / (1024 * 1024 * 1024)).toFixed(1) + ' GB';
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

// === Toast Notifications ===
function showToast(message, type = 'info', duration = 3000) {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'toast-container';
    document.body.appendChild(container);
  }

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  container.appendChild(toast);

  // Auto remove after duration
  setTimeout(() => {
    toast.classList.add('toast-out');
    toast.addEventListener('animationend', () => toast.remove());
  }, duration);
}

function toastSuccess(msg) { showToast(msg, 'success'); }
function toastError(msg) { showToast(msg, 'error', 5000); }
function toastWarning(msg) { showToast(msg, 'warning', 4000); }
function toastInfo(msg) { showToast(msg, 'info'); }

// === Event Delegation ===
const actionHandlers = {
  'show-upload': showUpload,
  'show-library': showLibrary,
  'batch-translate': batchTranslate,
  'do-upload': doUpload,
  'cancel-upload': cancelUpload,
  'start-translate': startTranslate,
  'cancel-translate': cancelTranslation,
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
  'remove-file': (e) => {
    const index = parseInt(e.target.dataset.index, 10);
    if (!isNaN(index)) removeFile(index);
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

// === Keyboard Shortcuts ===
document.addEventListener('keydown', (e) => {
  // Escape: go back to library from upload/reader views
  if (e.key === 'Escape') {
    const uploadView = document.getElementById('upload-view');
    const readerView = document.getElementById('reader-view');
    if (uploadView?.classList.contains('active') || readerView?.classList.contains('active')) {
      showLibrary();
      e.preventDefault();
    }
  }

  // Ctrl/Cmd + U: open upload view
  if ((e.ctrlKey || e.metaKey) && e.key === 'u') {
    const readerView = document.getElementById('reader-view');
    if (!readerView?.classList.contains('active')) {
      showUpload();
      e.preventDefault();
    }
  }

  // Ctrl/Cmd + Enter: start translation (in reader view)
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    const readerView = document.getElementById('reader-view');
    if (readerView?.classList.contains('active') && currentPaper) {
      startTranslate();
      e.preventDefault();
    }
  }

  // Ctrl/Cmd + Shift + S: toggle sync scroll (in reader view)
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'S') {
    const readerView = document.getElementById('reader-view');
    if (readerView?.classList.contains('active')) {
      toggleSyncScroll();
      e.preventDefault();
    }
  }
});

// === Init ===
document.addEventListener('DOMContentLoaded', () => {
  initDropZone();
  initResizer();
  loadPapers();
});
