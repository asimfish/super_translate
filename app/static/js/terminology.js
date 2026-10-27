/* Extensible terminology management; depends on app.js auth/view helpers. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let categories = [], libraries = [], entries = [], papers = [];
  let libraryId = '', entryId = '', offset = 0, total = 0, generation = 0;
  let profileGeneration = 0, entryGeneration = 0, filterTimer, paperTimer;
  let busy = false;
  const token = () => localStorage.getItem('paperChinaApiToken') || '';
  const status = (text, error = false) => {
    $('term-status').textContent = text;
    $('term-status').dataset.error = String(error);
  };
  function clearWorkspace() {
    generation++; profileGeneration++; entryGeneration++;
    categories = []; libraries = []; entries = []; papers = [];
    libraryId = ''; entryId = ''; offset = total = 0;
    clearTimeout(filterTimer); clearTimeout(paperTimer);
    ['term-library-list', 'term-entry-body', 'term-preview', 'term-extra-libraries'].forEach(id => $(id).replaceChildren());
    ['term-library-name', 'term-library-description', 'term-source', 'term-translation',
      'term-aliases', 'term-notes', 'term-reference', 'term-import-content', 'term-category-name',
      'term-query', 'term-paper-search', 'term-new-library-name'].forEach(id => { $(id).value = ''; });
    ['term-category-filter', 'term-category-parent', 'term-category-rename', 'term-library-category',
      'term-profile-category', 'term-profile-paper'].forEach(id => $(id).replaceChildren());
    $('term-library-title').textContent = '选择术语库';
    $('term-entry-count').textContent = '';
    $('term-editing').textContent = '新增词条';
    $('term-pager').textContent = '';
    $('term-custom-controls').classList.add('hidden');
    $('term-profile-inherited').textContent = '';
    $('term-use-builtin').checked = true;
    $('term-import-file').value = '';
    $('term-library-enabled').checked = true;
    $('term-import-format').value = 'csv';
    $('term-import-conflict').value = 'keep';
    status('');
  }
  async function request(path, method = 'GET', data) {
    const account = token(), turn = generation;
    const response = await apiFetch('/api/terminology' + path, {
      method, headers: data ? { 'Content-Type': 'application/json' } : {},
      body: data ? JSON.stringify(data) : undefined,
    }, false);
    if (response.status === 401) {
      clearWorkspace();
      await requestLogin();
      throw new Error('登录已更新，请重新打开术语库后操作');
    }
    if (turn !== generation || account !== token()) throw new Error('账号或页面已切换，请重新打开术语库');
    if (!response.ok) throw new Error(await errorDetail(response, '术语操作失败'));
    return response.json();
  }
  function pathOf(id, seen = new Set()) {
    const node = categories.find(row => row.id === id);
    if (!node || seen.has(id)) return '';
    seen.add(id);
    return (node.parent_id ? pathOf(node.parent_id, seen) + ' / ' : '') + node.name;
  }
  function categoryOptions(select, firstLabel) {
    const old = select.value;
    select.innerHTML = `<option value="">${esc(firstLabel)}</option>` + categories
      .slice().sort((a, b) => pathOf(a.id).localeCompare(pathOf(b.id), 'zh'))
      .map(row => `<option value="${esc(row.id)}">${esc(pathOf(row.id))}</option>`).join('');
    if (categories.some(row => row.id === old)) select.value = old;
  }
  function renderCategories() {
    categoryOptions($('term-category-filter'), '所有学科 / 领域');
    categoryOptions($('term-category-parent'), '新建学科（根分类）');
    categoryOptions($('term-category-rename'), '选择要改名或删除的分类');
    categoryOptions($('term-library-category'), '通用库（所有论文）');
    categoryOptions($('term-profile-category'), '未指定学科 / 领域');
  }
  function renderLibraries() {
    const filter = $('term-category-filter').value;
    const filtered = libraries.filter(row => !filter || row.category_id === filter ||
      pathOf(row.category_id).startsWith(pathOf(filter) + ' / '));
    $('term-library-list').innerHTML = filtered.map(row => `
      <button class="term-library" data-term-action="select-library" data-id="${esc(row.id)}" aria-pressed="${row.id === libraryId}">
        <strong>${esc(row.name)}</strong><small>${row.count} 词条 · ${row.readonly ? '只读' :
          row.paper_id ? '论文专用' : pathOf(row.category_id) || '通用'}${row.enabled ? '' : ' · 已停用'}</small>
      </button>`).join('') || '<p class="term-muted">这个分类还没有术语库，可直接创建。</p>';
  }
  async function refresh(selectId) {
    [categories, libraries] = await Promise.all([request('/categories'), request('/libraries')]);
    renderCategories(); renderLibraries();
    if (selectId) await selectLibrary(selectId);
    else if (libraryId && libraries.some(row => row.id === libraryId)) await selectLibrary(libraryId);
    else if (libraries.length) await selectLibrary(libraries[0].id);
    await loadProfile();
  }
  async function open(forPaper = false) {
    clearWorkspace();
    showView('terminology');
    status('正在加载术语库…');
    papers = await request('/papers');
    if (forPaper && currentPaper && !papers.some(row => row.id === currentPaper.id)) papers.unshift(currentPaper);
    renderPapers(forPaper && currentPaper ? currentPaper.id : '');
    await refresh();
    status('按学科维护可复用库，论文专用库自动覆盖通用译法。');
  }
  function renderPapers(selected = '') {
    $('term-profile-paper').innerHTML = '<option value="">新上传论文的默认设置</option>' + papers.map(row =>
      `<option value="${esc(row.id)}">${esc(row.title)}</option>`).join('');
    $('term-profile-paper').value = selected;
  }
  async function selectLibrary(id) {
    const library = libraries.find(row => row.id === id);
    if (!library) return;
    libraryId = id; offset = 0; entryId = '';
    $('term-query').value = '';
    $('term-entry-form').reset(); $('term-editing').textContent = '新增词条';
    $('term-library-title').textContent = library.name;
    $('term-library-name').value = library.name;
    $('term-library-description').value = library.description;
    $('term-library-category').value = library.category_id || '';
    $('term-library-category').disabled = !!library.paper_id;
    $('term-library-enabled').checked = library.enabled;
    $('term-custom-controls').classList.toggle('hidden', library.readonly);
    renderLibraries();
    await loadEntries();
  }
  async function loadEntries() {
    const id = libraryId, turn = ++entryGeneration;
    if (!id) return;
    const params = new URLSearchParams({ q: $('term-query').value, offset, limit: 50 });
    const result = await request(`/libraries/${encodeURIComponent(id)}/entries?${params}`);
    if (id !== libraryId || turn !== entryGeneration) return;
    entries = result.entries; total = result.total;
    if (offset > 0 && offset >= total) {
      offset = Math.max(0, Math.floor((total - 1) / 50) * 50);
      return loadEntries();
    }
    if (!$('term-query').value) {
      const row = libraries.find(library => library.id === id);
      if (row) row.count = total;
      renderLibraries();
    }
    const readonly = libraries.find(row => row.id === id)?.readonly;
    $('term-entry-count').textContent = `${total} 个词条${readonly ? ' · 内置语料只读，可导出复制' : ''}`;
    $('term-entry-body').innerHTML = entries.map(row => `<tr>
      <td>${esc(row.source)}${row.aliases.length ? `<small>别名：${esc(row.aliases.join(' | '))}</small>` : ''}</td>
      <td>${esc(row.translation)}${row.notes ? `<small>${esc(row.notes)}</small>` : ''}${row.reference ? `<small>来源：${esc(row.reference)}</small>` : ''}</td>
      <td>${readonly ? '<span class="term-muted">只读</span>' : `<button class="btn btn-sm btn-text" data-term-action="edit-entry" data-id="${esc(row.id)}">编辑</button><button class="btn btn-sm btn-text" data-term-action="delete-entry" data-id="${esc(row.id)}">删除</button>`}</td>
      </tr>`).join('') || '<tr><td colspan="3" class="term-muted">暂无词条，可以新增或批量导入。</td></tr>';
    $('term-pager').textContent = `${total ? offset + 1 : 0}–${Math.min(offset + 50, total)} / ${total}`;
    $('term-prev').disabled = offset === 0; $('term-next').disabled = offset + 50 >= total;
  }
  async function loadProfile() {
    const id = $('term-profile-paper').value, turn = ++profileGeneration;
    const profile = await request(id ? `/papers/${encodeURIComponent(id)}/profile` : '/profile');
    if (turn !== profileGeneration || id !== $('term-profile-paper').value) return;
    $('term-profile-category').value = profile.category_id || '';
    $('term-use-builtin').checked = profile.use_builtin;
    $('term-profile-inherited').textContent = profile.inherited ? '正在继承默认设置；保存后为这篇论文单独设置。' :
      id ? '这是本篇论文的独立设置。' : '未单独设置的论文和悬窗新上传 PDF 会使用此设置。';
    const reusable = libraries.filter(row => !row.readonly && !row.paper_id);
    const byId = new Map(reusable.map(row => [row.id, row]));
    const ordered = [...profile.library_ids.map(key => byId.get(key)).filter(Boolean),
      ...reusable.filter(row => !profile.library_ids.includes(row.id))];
    $('term-extra-libraries').innerHTML = ordered.map(row => `<label><input type="checkbox" value="${esc(row.id)}"
      ${profile.library_ids.includes(row.id) ? 'checked' : ''}> ${esc(row.name)}${row.enabled ? '' : '（已停用）'}</label>`).join('') ||
      '<span class="term-muted">分类库会自动选用，也可以额外选用跨学科的库。</span>';
    await preview();
  }
  async function preview() {
    const id = $('term-profile-paper').value, turn = profileGeneration;
    const result = await request('/resolve' + (id ? `?paper_id=${encodeURIComponent(id)}` : ''));
    if (turn !== profileGeneration || id !== $('term-profile-paper').value) return;
    $('term-preview').innerHTML = `<strong>已保存的设置：${result.count} 个有效术语</strong><br>${
      result.use_builtin ? '含内置 AI 术语；' : '已关闭内置 AI 术语；'}${
      esc(result.libraries.map(row => row.name).join(' → ') || '没有自定义库')}<br><span class="term-muted">翻译仅发送当前段落命中的术语。修改后需重新翻译 PDF。</span>${
      result.conflicts.length ? `<p>同词覆盖 ${result.conflicts.length} 项（最多显示 100 项）：</p><ul>${result.conflicts.map(row =>
        `<li>${esc(row.source)}：${esc(row.previous)}（${esc(row.previous_library)}） → ${esc(row.translation)}（${esc(row.library)}）</li>`).join('')}</ul>` : ''}`;
  }
  async function saveProfile() {
    const id = $('term-profile-paper').value;
    await request(id ? `/papers/${encodeURIComponent(id)}/profile` : '/profile', 'PUT', {
      category_id: $('term-profile-category').value || null,
      library_ids: [...$('term-extra-libraries').querySelectorAll('input:checked')].map(input => input.value),
      use_builtin: $('term-use-builtin').checked,
    });
    await loadProfile(); status('术语设置已保存，将用于下一次翻译。');
  }
  async function createLibrary(paper = false) {
    const paperId = $('term-profile-paper').value;
    if (paper && !paperId) throw new Error('先在上方选一篇论文，再创建论文专用库');
    const name = $('term-new-library-name').value.trim();
    if (!name) throw new Error('请输入新库名称');
    const created = await request('/libraries', 'POST', { name, paper_id: paper ? paperId : null,
      category_id: paper ? null : $('term-category-filter').value || null });
    $('term-new-library-name').value = '';
    $('term-category-filter').value = '';
    await refresh(created.id); status('术语库已创建，可以新增词条或批量导入。');
  }
  async function exportLibrary() {
    const id = libraryId, name = libraries.find(row => row.id === id)?.name || '术语库';
    let cursor = 0, part = 1;
    do {
      const payload = await request(`/libraries/${encodeURIComponent(id)}/export?offset=${cursor}`);
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' });
      const url = URL.createObjectURL(blob), anchor = document.createElement('a');
      anchor.href = url; anchor.download = `${name.replace(/[\\/:*?"<>|]/g, '_')}-${part++}.json`;
      anchor.click(); URL.revokeObjectURL(url);
      cursor = payload.next_offset;
    } while (cursor !== null);
    status(`已导出 ${part - 1} 个 JSON 文件，每个文件都可直接导入其他库。`);
  }
  const handlers = {
    open: () => open(), 'open-paper': () => open(true),
    'select-library': event => selectLibrary(event.target.closest('[data-id]').dataset.id),
    'category-filter': renderLibraries,
    'create-library': () => createLibrary(), 'create-paper-library': () => createLibrary(true),
    'save-profile': saveProfile,
    'reset-profile': async () => {
      const id = $('term-profile-paper').value;
      if (!id) throw new Error('先选择一篇论文');
      await request(`/papers/${encodeURIComponent(id)}/profile`, 'DELETE');
      await loadProfile(); status('本篇论文已恢复继承默认设置。');
    },
    'save-library': async () => {
      const row = libraries.find(row => row.id === libraryId);
      await request(`/libraries/${encodeURIComponent(libraryId)}`, 'PUT', {
        name: $('term-library-name').value, description: $('term-library-description').value,
        category_id: row.paper_id ? null : $('term-library-category').value || null,
        paper_id: row.paper_id, enabled: $('term-library-enabled').checked,
      });
      await refresh(libraryId); status('库设置已保存。');
    },
    'delete-library': async () => {
      if (!confirm('删除这个术语库及其词条？已生成的 PDF 不受影响。')) return;
      await request(`/libraries/${encodeURIComponent(libraryId)}`, 'DELETE');
      libraryId = ''; await refresh(); status('术语库已删除。');
    },
    'create-category': async () => {
      await request('/categories', 'POST', { name: $('term-category-name').value,
        parent_id: $('term-category-parent').value || null });
      $('term-category-name').value = ''; await refresh(); status('分类已创建。');
    },
    'rename-category': async () => {
      const node = categories.find(row => row.id === $('term-category-rename').value);
      if (!node) throw new Error('先选择要改名的分类');
      await request(`/categories/${node.id}`, 'PUT', { name: $('term-category-name').value, parent_id: node.parent_id });
      await refresh(); status('分类已改名。');
    },
    'delete-category': async () => {
      const id = $('term-category-rename').value;
      if (!id) throw new Error('先选择要删除的空分类');
      if (!confirm('删除这个空分类？含库、子分类或绑定的分类需要先解除关联。')) return;
      await request(`/categories/${id}`, 'DELETE'); await refresh(); status('分类已删除。');
    },
    template: async event => {
      const name = event.target.closest('[data-template]').dataset.template;
      const row = await request(`/templates/${name}`, 'POST');
      await refresh(row.id); status('模板已添加；重复添加会保留你已修改的词条。');
    },
    'edit-entry': event => {
      const row = entries.find(item => item.id === event.target.closest('[data-id]').dataset.id);
      if (!row) return;
      entryId = row.id;
      ['source', 'translation', 'notes', 'reference'].forEach(key => { $('term-' + key).value = row[key]; });
      $('term-aliases').value = row.aliases.join(' | ');
      $('term-editing').textContent = '编辑词条';
      $('term-entry-details').open = true; $('term-source').focus();
    },
    'cancel-entry': () => { entryId = ''; $('term-entry-form').reset(); $('term-editing').textContent = '新增词条'; },
    'delete-entry': async event => {
      if (!confirm('删除这个词条？')) return;
      await request(`/libraries/${libraryId}/entries/${event.target.closest('[data-id]').dataset.id}`, 'DELETE');
      await loadEntries(); await preview(); status('词条已删除。');
    },
    'import-entries': async () => {
      const result = await request(`/libraries/${libraryId}/import`, 'POST', {
        content: $('term-import-content').value, format: $('term-import-format').value,
        conflict: $('term-import-conflict').value,
      });
      await refresh(libraryId);
      status(`导入完成：新增 ${result.added}，更新 ${result.updated}，保留已有 ${result.skipped}。`);
    },
    export: exportLibrary,
    prev: async () => { offset = Math.max(0, offset - 50); await loadEntries(); },
    next: async () => { offset += 50; await loadEntries(); },
  };
  async function perform(action, event) {
    if (busy) return;
    const turn = generation;
    busy = true;
    document.querySelectorAll('[data-term-action]').forEach(button => { button.disabled = true; });
    try { await handlers[action](event); }
    catch (error) { if (turn === generation || action.startsWith('open')) status(error.message, true); }
    finally {
      busy = false;
      document.querySelectorAll('[data-term-action]').forEach(button => { button.disabled = false; });
      $('term-prev').disabled = offset === 0; $('term-next').disabled = offset + 50 >= total;
    }
  }
  function handleTermClick(event) {
    const element = event.target.closest('[data-term-action]');
    // Select menus use their native click behavior and the change listener.
    if (element?.matches('select')) return;
    const action = element?.dataset.termAction;
    if (action && handlers[action]) { event.preventDefault(); perform(action, event); }
  }
  document.addEventListener('click', handleTermClick);
  $('term-entry-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    busy = true; const button = $('term-save-entry'); button.disabled = true;
    try {
      const payload = { source: $('term-source').value, translation: $('term-translation').value,
        aliases: $('term-aliases').value.split('|').map(value => value.trim()).filter(Boolean),
        notes: $('term-notes').value, reference: $('term-reference').value };
      await request(`/libraries/${libraryId}/entries${entryId ? '/' + entryId : ''}`, entryId ? 'PUT' : 'POST', payload);
      entryId = ''; $('term-entry-form').reset(); $('term-editing').textContent = '新增词条';
      await loadEntries(); await preview(); status('词条已保存。');
    } catch (error) { status(error.message, true); }
    finally { busy = false; button.disabled = false; }
  });
  $('term-query').addEventListener('input', () => {
    clearTimeout(filterTimer); offset = 0;
    filterTimer = setTimeout(() => loadEntries().catch(error => status(error.message, true)), 250);
  });
  $('term-category-filter').addEventListener('change', renderLibraries);
  $('term-profile-paper').addEventListener('change', () => loadProfile().catch(error => status(error.message, true)));
  $('term-profile-category').addEventListener('change', () => {
    if ($('term-profile-category').value) $('term-use-builtin').checked = false;
    status('设置尚未保存，点击「保存设置」后才用于翻译。');
  });
  $('term-paper-search').addEventListener('input', () => {
    clearTimeout(paperTimer);
    const turn = generation, query = $('term-paper-search').value;
    paperTimer = setTimeout(async () => {
      try {
        const result = await request('/papers?q=' + encodeURIComponent(query));
        if (turn !== generation || query !== $('term-paper-search').value) return;
        const selected = $('term-profile-paper').value;
        const previous = papers.find(row => row.id === selected);
        papers = result;
        if (previous && !papers.some(row => row.id === selected)) papers.unshift(previous);
        renderPapers(selected);
      } catch (error) { status(error.message, true); }
    }, 300);
  });
  $('term-import-file').addEventListener('change', async event => {
    const file = event.target.files[0], turn = generation;
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) { status('文件不能超过 2 MiB', true); return; }
    const content = await file.text();
    if (turn !== generation) return;
    $('term-import-content').value = content;
    $('term-import-format').value = file.name.toLowerCase().endsWith('.json') ? 'json' : 'csv';
    status('文件已读取；选择冲突策略后点击「导入词条」。');
  });
  $('btn-logout')?.addEventListener('click', () => { clearWorkspace(); showView('library'); });
  window.addEventListener('storage', event => { if (event.key === 'paperChinaApiToken') { clearWorkspace(); showView('library'); } });
})();
