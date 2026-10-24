window.SuperTranslatePreferences = {
  async init(win) {
    const doc = win.document, app = Zotero.SuperTranslate;
    const $ = id => doc.getElementById(id);
    if (!$('st-origin') || $('st-origin').dataset.bound) return;
    $('st-origin').dataset.bound = 'true'; $('st-origin').value = app.pref('origin');
    const status = (id, text, error = false) => { $(id).textContent = text; $(id).classList.toggle('st-error', error); };
    const bind = (id, target, action) => $(id).addEventListener('click', async () => {
      $(id).disabled = true; status(target, '正在处理…');
      try { await action(); } catch (e) { status(target, e.message, true); }
      finally { $(id).disabled = false; }
    });
    let taskSnapshot = '', selectionSnapshot = '', libraryController = app.controller, offset = 0, generation = 0;
    const redraw = () => {
      if (libraryController !== app.controller) {
        libraryController = app.controller; generation++; offset = 0;
        $('st-library-results').replaceChildren(); status('st-library-status', '账号已切换，请重新搜索。');
      }
      const next = JSON.stringify([app.controller.namespace, app.selection, app.catalogs]);
      const controls = $('st-selection-controls');
      if (next !== selectionSnapshot && !controls.contains(doc.activeElement)) {
        controls.replaceChildren(app.renderSelection(doc)); selectionSnapshot = next;
      }
      const query = $('st-task-search').value.trim().toLocaleLowerCase();
      const snapshot = JSON.stringify([app.controller.namespace, app.controller.tasks, query]);
      if (snapshot === taskSnapshot || ($('st-tasks').contains(doc.activeElement) && ['input', 'select', 'textarea'].includes(doc.activeElement?.localName))) return;
      taskSnapshot = snapshot; $('st-tasks').replaceChildren();
      const matching = app.controller.tasks.filter(t => !query || t.title.toLocaleLowerCase().includes(query));
      let day = '';
      for (const task of matching.slice(0, 100)) {
        const date = (task.createdAt || '').slice(0, 10) || '未注明日期';
        if (date !== day) { $('st-tasks').append(app.element(doc, 'h3', date, 'st-date')); day = date; }
        const title = app.element(doc, 'p', task.title, 'st-document-title'); $('st-tasks').append(title, app.taskCard(doc, task));
      }
      if (!matching.length) $('st-tasks').append(app.element(doc, 'p', query ? '没有匹配的任务。' : '选择带有 PDF 的论文，点击侧栏「翻译并保存中文版」。', 'st-muted'));
    };
    const connected = async () => {
      await app.loadModels(); redraw();
      status('st-connection', app.controller.client.token ? '连接正常，模型与共享文档库可用。' : '服务可访问，请登录。');
    };
    bind('st-login', 'st-connection', async () => {
      const client = await app.client($('st-origin').value.trim(), '');
      let receipt;
      try { receipt = await client.login($('st-username').value.trim(), $('st-password').value); }
      finally { $('st-password').value = ''; }
      await app.saveToken(client.origin, receipt.token, receipt.username);
      app.setPref('origin', client.origin); await app.configure(); await connected();
    });
    bind('st-connect', 'st-connection', async () => {
      const client = await app.client($('st-origin').value);
      await client.connect(); app.setPref('origin', client.origin); await app.configure(); await connected();
    });
    bind('st-logout', 'st-connection', async () => {
      const o = app.controller.client.origin;
      for (const login of await Services.logins.searchLoginsAsync({ origin: o, httpRealm: 'SuperTranslate' })) Services.logins.removeLogin(login);
      await app.configure(); redraw(); status('st-connection', '已退出。重新登录后可继续同步服务端任务。');
    });
    bind('st-save-key', 'st-model-status', async () => {
      try {
        if (!app.selection.provider || !$('st-api-key').value.trim()) throw new Error('请选择供应商并填写 API Key。');
        await app.applySelection($('st-api-key').value.trim()); redraw(); status('st-model-status', '供应商配置已保存。');
      } finally { $('st-api-key').value = ''; }
    });
    bind('st-refresh', 'st-model-status', async () => { await app.controller.refresh(); redraw(); status('st-model-status', '任务状态已更新。'); });
    $('st-task-search').addEventListener('input', redraw);
    const find = async () => {
      const controller = app.controller, request = ++generation;
      const page = await controller.client.listPapers($('st-library-search').value.trim(), $('st-library-sort').value, offset);
      if (controller !== app.controller || request !== generation || app.disposed) return;
      const results = $('st-library-results'); results.replaceChildren();
      status('st-library-status', `共 ${page.total} 篇 · ${offset + (page.papers.length ? 1 : 0)}–${offset + page.papers.length} · 每页 50 篇`);
      $('st-library-prev').disabled = offset === 0; $('st-library-next').disabled = offset + page.papers.length >= page.total;
      let day = '';
      for (const paper of page.papers) {
        const date = (paper.last_imported_at || paper.created_at || '').slice(0, 10) || '未注明日期';
        if (day !== date) { results.append(app.element(doc, 'h3', date, 'st-date')); day = date; }
        const card = app.element(doc, 'article', null, 'st-task');
        card.append(app.element(doc, 'strong', paper.title, 'st-document-title'));
        card.append(app.element(doc, 'p', `${paper.original_filename || ''} · ${paper.import_count || 1} 次导入 · 版本 ${paper.version_number || 1}/${paper.version_count || 1}`, 'st-muted'));
        card.append(app.element(doc, 'span', Zotero.SuperTranslate.controller === controller ? (app.controller.tasks.find(t => t.paperID === paper.id)?.message || paper.translation_stage || paper.translation_status) : '', 'st-status'));
        const actions = app.element(doc, 'div', null, 'st-actions');
        if (paper.has_translated) actions.append(app.button(doc, paper.translation_status === 'completed' ? '打开中文版' : '预览待修复草稿', () => {
          if (controller !== app.controller) throw new Error('账号已变化，请重新搜索。');
          return app.openLibraryPDF(paper);
        }));
        const history = app.element(doc, 'div', null, 'st-history');
        actions.append(app.button(doc, '导入历史与版本', async () => {
          const data = await controller.client.libraryHistory(paper.id);
          if (controller !== app.controller || request !== generation) return;
          if (!Array.isArray(data.imports) || !Array.isArray(data.versions)) throw new Error('文档历史格式不正确。');
          history.replaceChildren();
          for (const entry of data.imports.slice(0, 100)) history.append(app.element(doc, 'p', `${(entry.imported_at || '').replace('T', ' ').slice(0, 19)} · ${entry.filename}`, 'st-footnote'));
          for (const version of data.versions.slice(0, 100)) {
            const row = app.element(doc, 'p', `${(version.created_at || '').replace('T', ' ').slice(0, 19)} · ${version.title} · ${version.translation_status}`, 'st-footnote');
            row.append(app.button(doc, '查看此版本', () => {
              if (controller !== app.controller) throw new Error('账号已变化，请重新搜索。');
              return app.openLibraryPDF(version);
            }, 'st-button st-quiet')); history.append(row);
          }
        }, 'st-button st-quiet'));
        card.append(actions, history); results.append(card);
      }
    };
    bind('st-library-find', 'st-library-status', async () => { offset = 0; await find(); });
    bind('st-library-prev', 'st-library-status', async () => { offset = Math.max(0, offset - 50); await find(); });
    bind('st-library-next', 'st-library-status', async () => { offset += 50; await find(); });
    bind('st-library-organize', 'st-library-status', async () => {
      const controller = app.controller;
      const receipt = await controller.client.organizeLibrary();
      if (controller !== app.controller) return;
      offset = 0; await find();
      status('st-library-status', `已整理 ${receipt.indexed} 篇，合并 ${receipt.duplicates} 个重复内容，更新 ${receipt.renamed} 个标题。`);
    });
    $('st-library-search').addEventListener('keydown', e => { if (e.key === 'Enter') $('st-library-find').click(); });
    $('st-library-prev').disabled = true; $('st-library-next').disabled = true;
    redraw();
    const timer = win.setInterval(() => {
      if (!$('st-tasks')?.isConnected || Zotero.SuperTranslate !== app || app.disposed) { win.clearInterval(timer); return; }
      redraw();
    }, 1000);
    win.addEventListener('unload', () => win.clearInterval(timer), { once: true });
    status('st-connection', app.controller.client.token ? '已保存登录凭据。侧栏可直接切换模型，文档库支持搜索与版本查询。' : '填写服务地址和账号，登录即可开始。');
  },
};
