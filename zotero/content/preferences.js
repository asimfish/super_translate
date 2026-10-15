window.SuperTranslatePreferences = {
  async init(win) {
    const doc = win.document, app = Zotero.SuperTranslate;
    const $ = id => doc.getElementById(id);
    if (!$('st-origin') || $('st-origin').dataset.bound) return;
    $('st-origin').dataset.bound = "true";
    $('st-origin').value = app.pref("origin");
    const labels = { deepseek: "DeepSeek", kimi: "Kimi", openai: "OpenAI", anthropic: "Anthropic", glm: "智谱 GLM", gemini: "Gemini", qwen: "Qwen", doubao: "豆包" };
    for (const [id, label] of Object.entries(labels)) {
      const option = app.element(doc, "option", label); option.value = id; $('st-provider').append(option);
    }
    for (const key of ['provider', 'quality', 'variant']) {
      $('st-' + key).value = app.pref(key);
      $('st-' + key).addEventListener('change', () => { app.setPref(key, $('st-' + key).value); if (key === 'provider') fillModel(); app.refreshPanels(); });
    }
    const status = (id, text, error = false) => { $(id).textContent = text; $(id).classList.toggle('st-error', error); };
    const bind = (id, target, action) => $(id).addEventListener('click', async () => {
      $(id).disabled = true; status(target, '正在处理…');
      try { await action(); } catch (e) { status(target, e.message, true); }
      finally { $(id).disabled = false; }
    });
    const fillModel = () => {
      const provider = $('st-provider').value;
      if (app.catalogs.length) for (const option of $('st-provider').options) {
        option.disabled = !app.catalogs.some(c => c.provider === option.value);
        option.textContent = labels[option.value] + (option.disabled ? '（服务尚未支持）' : '');
      }
      const catalog = app.catalogs.find(c => c.provider === provider);
      const credential = app.credentials.find(c => c.provider === provider);
      $('st-model').value = catalog?.selected_model || credential?.model || '';
      $('st-model-list').replaceChildren();
      for (const model of catalog?.models || []) {
        const o = app.element(doc, 'option'); o.value = model; $('st-model-list').append(o);
      }
      status('st-model-status', credential ? (credential.configured ? '已配置 · ' : '尚未配置 API Key · ') + (catalog?.warning || `模型目录：${catalog?.source || '服务默认'}`) : '连接后可加载模型列表');
    };
    const tasks = () => {
      $('st-tasks').replaceChildren();
      for (const task of app.controller.tasks.slice(0, 40)) {
        const title = app.element(doc, 'p', task.title, 'st-footnote'); $('st-tasks').append(title, app.taskCard(doc, task));
      }
      if (!app.controller.tasks.length) $('st-tasks').append(app.element(doc, 'p', '还没有任务。回到文献库，选择一篇带 PDF 的论文开始。', 'st-muted'));
    };
    bind('st-login', 'st-connection', async () => {
      const baseURL = $('st-origin').value.trim();
      const client = await app.client(baseURL, '');
      let receipt;
      try { receipt = await client.login($('st-username').value.trim(), $('st-password').value); }
      finally { $('st-password').value = ''; }
      await app.saveToken(client.origin, receipt.token, receipt.username);
      app.setPref('origin', client.origin); await app.configure();
      status('st-connection', `已连接 · ${receipt.username || '当前账号'}`);
      await app.loadModels(); fillModel(); tasks();
    });
    bind('st-connect', 'st-connection', async () => {
      const client = await app.client($('st-origin').value);
      await client.connect(); app.setPref('origin', client.origin); await app.configure();
      status('st-connection', client.token ? '连接正常，账号可用。' : '服务可访问，请登录后翻译。');
      await app.loadModels(); fillModel(); tasks();
    });
    bind('st-logout', 'st-connection', async () => {
      const o = app.controller.client.origin;
      for (const login of await Services.logins.searchLoginsAsync({ origin: o, httpRealm: 'SuperTranslate' })) Services.logins.removeLogin(login);
      await app.configure(); tasks(); fillModel(); status('st-connection', '已退出。服务端正在执行的翻译继续保留，重新登录后可同步。');
    });
    bind('st-models', 'st-model-status', async () => { await app.loadModels(); fillModel(); });
    bind('st-save-model', 'st-model-status', async () => {
      try { await app.controller.client.saveModel($('st-provider').value, $('st-model').value.trim(), $('st-api-key').value.trim()); }
      finally { $('st-api-key').value = ''; }
      await app.loadModels(); fillModel(); status('st-model-status', '账号默认模型已保存。');
    });
    bind('st-refresh', 'st-model-status', async () => { await app.controller.refresh(); tasks(); status('st-model-status', '任务状态已更新。'); });
    tasks(); fillModel();
    status('st-connection', app.controller.client.token ? '已保存登录凭据，可点击检测连接。' : '尚未连接。填写服务地址和账号即可开始。');
  },
};
