/* Native Zotero adapter. UI strings and remote metadata are inserted as text. */
var SuperTranslateZotero = {
  id: pluginID, rootURI, windows: new Set(), panels: new Map(), menus: [], controller: null,
  stylePath: "content/style.css?v=" + (typeof pluginVersion === "undefined" ? "dev" : encodeURIComponent(pluginVersion)),
  catalogs: [], credentials: [], timer: null, running: false, disposed: false, readerButtons: new Set(),
  providerLabels: { "": "跟随系统默认", deepseek: "DeepSeek", kimi: "Kimi", openai: "OpenAI", anthropic: "Anthropic", glm: "智谱 GLM", gemini: "Gemini", qwen: "通义千问 Qwen", doubao: "豆包" },
  pref(key, fallback = "") { return Services.prefs.getStringPref(`extensions.supertranslate.${key}`, fallback); },
  setPref(key, value) { Services.prefs.setStringPref(`extensions.supertranslate.${key}`, String(value)); },
  accountPref(key, fallback = "") { return this.pref(`account.${this.controller.namespace}.${key}`, fallback); },
  setAccountPref(key, value) { this.setPref(`account.${this.controller.namespace}.${key}`, value); },
  selectedModel(provider) { return this.catalogs.find(c => c.provider === provider)?.selected_model || this.credentials.find(c => c.provider === provider)?.model || ""; },
  selectionDirty() {
    const d = this.selection;
    return !!d && (d.provider !== this.accountPref("provider", "deepseek") || d.quality !== this.accountPref("quality", "balanced") ||
      (d.provider && d.model != null && d.model !== this.selectedModel(d.provider)));
  },
  async applySelection(apiKey = "") {
    const controller = this.controller, draft = { ...this.selection };
    if (draft.provider && (!draft.model || apiKey || draft.model !== this.selectedModel(draft.provider))) {
      await controller.client.saveModel(draft.provider, draft.model, apiKey);
      if (controller !== this.controller || controller.closed) return;
      await this.loadModels();
    }
    if (controller !== this.controller || controller.closed) return;
    this.setAccountPref("provider", draft.provider); this.setAccountPref("quality", draft.quality);
    this.refreshPanels();
  },
  uuid() { return Services.uuid.generateUUID().toString().slice(1, -1); },
  hash(bytes) {
    const h = Cc["@mozilla.org/security/hash;1"].createInstance(Ci.nsICryptoHash);
    h.init(h.SHA256); h.update(bytes, bytes.length);
    return Array.from(h.finish(false), c => c.charCodeAt(0).toString(16).padStart(2, "0")).join("");
  },
  async token(baseURL) {
    const list = await Services.logins.searchLoginsAsync({ origin: SuperTranslateCore.origin(baseURL), httpRealm: "SuperTranslate" });
    return list[0]?.password || "";
  },
  async saveToken(baseURL, token, username) {
    if (!token || /[\x00-\x20\x7f]/.test(token)) throw new Error("服务返回了无效的登录凭据");
    const o = SuperTranslateCore.origin(baseURL);
    const list = await Services.logins.searchLoginsAsync({ origin: o, httpRealm: "SuperTranslate" });
    const login = Cc["@mozilla.org/login-manager/loginInfo;1"].createInstance(Ci.nsILoginInfo);
    login.init(o, null, "SuperTranslate", username || "account", token, "", "");
    if (list.length) Services.logins.modifyLogin(list[0], login);
    else await Services.logins.addLoginAsync(login);
  },
  async transport({ url, method, body, headers, binary, timeout = 30000, maxBytes = 5 * 1024 * 1024 }) {
    const xhr = await Zotero.HTTP.request(method, url, {
      headers, body, timeout, responseType: binary ? "arraybuffer" : "json",
      successCodes: false, followRedirects: false, errorDelayMax: 0, anon: true,
      noCache: true, logBodyLength: 0,
      requestObserver: request => { request.onprogress = e => { if (e.loaded > maxBytes) request.abort(); }; },
    });
    return { status: xhr.status, data: binary ? new Uint8Array(xhr.response) : xhr.response };
  },
  async client(baseURL = this.pref("origin"), token) {
    return new SuperTranslateCore.ZoteroServiceClient({ baseURL, token: token ?? await this.token(baseURL), transport: a => this.transport(a) });
  },
  async configure() {
    const generation = this.configuration = (this.configuration || 0) + 1;
    this.controller?.shutdown();
    const client = await this.client();
    if (generation !== this.configuration || this.disposed) return;
    const namespace = this.hash(new TextEncoder().encode(client.origin + "\n" + client.token));
    const folder = PathUtils.join(Zotero.Profile.dir, "supertranslate");
    await IOUtils.makeDirectory(folder, { permissions: 0o700, ignoreExisting: true });
    const path = PathUtils.join(folder, `tasks-${namespace}.json`);
    const store = {
      load: async () => {
        if (!(await IOUtils.exists(path))) return [];
        const value = await IOUtils.readJSON(path);
        if (value.version !== 1 || !Array.isArray(value.tasks)) throw new Error("任务记录无法读取，请保留文件并联系维护者。");
        return value.tasks;
      },
      save: tasks => IOUtils.writeJSON(path, { version: 1, tasks }, { tmpPath: path + ".tmp", permissions: 0o600 }),
    };
    const controller = new SuperTranslateCore.ZoteroTranslationController({ client, namespace, store,
      host: { uuid: () => this.uuid(), findImported: t => this.findImported(t), checkImport: t => this.checkImport(t), importPDF: (t, b) => this.importPDF(t, b) },
      changed: () => this.refreshPanels(),
    });
    await controller.init();
    if (generation !== this.configuration || this.disposed) { controller.shutdown(); return; }
    this.controller = controller;
    this.catalogs = []; this.credentials = [];
    this.modelsLoadedAt = 0; this.modelsPromise = null;
    this.selection = { provider: this.accountPref("provider", "deepseek"), quality: this.accountPref("quality", "balanced"), model: null };
    this.refreshPanels();
    if (client.token) this.loadModels().catch(() => {});
  },
  async startup() {
    await this.configure();
    this.paneID = Zotero.ItemPaneManager.registerSection({ paneID: "supertranslate", pluginID: this.id,
      header: { l10nID: "supertranslate-section", icon: this.rootURI + "content/icon.svg" },
      sidenav: { l10nID: "supertranslate-section", icon: this.rootURI + "content/icon.svg" },
      onItemChange: ({ item, setEnabled }) => setEnabled(!!item && (item.isRegularItem() || item.isPDFAttachment())),
      onRender: args => this.renderPanel(args),
      onDestroy: ({ body }) => this.panels.delete(body),
    });
    this.preferenceID = Zotero.PreferencePanes.register({ pluginID: this.id, id: "supertranslate-preferences", label: "论文翻译",
      src: "content/preferences.xhtml", scripts: ["content/preferences.js"], stylesheets: [this.stylePath], image: "content/icon.svg" });
    this.menus.push(Zotero.MenuManager.registerMenu({ menuID: "supertranslate-translate", pluginID: this.id, target: "main/library/item", menus: [
      { menuType: "menuitem", l10nID: "supertranslate-menu-translate", icon: this.rootURI + "content/icon.svg",
        onShowing: (_, c) => c.setVisible(c.items?.some(i => i.isRegularItem() || i.isPDFAttachment())),
        onCommand: (_, c) => this.guard(() => this.translateItems(c.items || [])) },
    ] }));
    this.menus.push(Zotero.MenuManager.registerMenu({ menuID: "supertranslate-settings", pluginID: this.id, target: "main/menubar/tools", menus: [
      { menuType: "menuitem", l10nID: "supertranslate-menu-settings", onCommand: () => this.openSettings() },
    ] }));
    this.readerHandler = ({ reader, doc, append }) => {
      const control = this.element(doc, "span", null, "st-root st-reader-control");
      control.style.cssText = "display:inline-flex;align-items:center;gap:5px";
      control.dataset.stReaderItemID = String(reader.itemID);
      const button = doc.createElement("button"); button.textContent = "译";
      button.title = "SuperTranslate · 翻译整篇论文"; button.setAttribute("aria-label", button.title);
      button.style.cssText = "border:0;border-radius:5px;background:transparent;color:inherit;padding:4px 8px;cursor:pointer;font-size:14px";
      button.addEventListener("click", () => this.guard(async () => this.translateItems([await Zotero.Items.getAsync(reader.itemID)])));
      const progress = this.element(doc, "span", null, "st-reader-progress"); progress.hidden = true;
      control.append(button, progress); this.readerButtons.add(control);
      append(control); this.refreshReaderProgress();
    };
    Zotero.Reader.registerEventListener("renderToolbar", this.readerHandler, this.id);
    for (const win of Zotero.getMainWindows()) this.addWindow(win);
    this.schedule();
  },
  addWindow(win) {
    if (this.windows.has(win)) return;
    this.windows.add(win);
    const link = win.document.createElementNS("http://www.w3.org/1999/xhtml", "link");
    link.rel = "localization"; link.href = "supertranslate.ftl"; link.id = "supertranslate-l10n";
    (win.document.head || win.document.documentElement).append(link);
  },
  removeWindow(win) { this.windows.delete(win); win.document.getElementById("supertranslate-l10n")?.remove(); },
  schedule() {
    if (this.disposed) return;
    this.timer?.cancel();
    this.timer = Cc["@mozilla.org/timer;1"].createInstance(Ci.nsITimer);
    this.timer.initWithCallback(async () => {
      try {
        await this.controller?.refresh();
        if (this.controller?.client.token && Date.now() - this.modelsLoadedAt > 300000) await this.loadModels();
      }
      catch (_) { /* Persistence/network failures are shown by tasks; no raw credentials logged. */ }
      finally { this.schedule(); }
    }, this.controller?.tasks.some(t => ["pending", "translating", "repairing", "start_unknown", "sync_error", "completed"].includes(t.state)) ? 5000 : 30000, Ci.nsITimer.TYPE_ONE_SHOT);
  },
  shutdown() {
    this.disposed = true; this.timer?.cancel(); this.controller?.shutdown();
    if (this.paneID) Zotero.ItemPaneManager.unregisterSection(this.paneID);
    for (const id of this.menus) Zotero.MenuManager.unregisterMenu(id);
    if (this.readerHandler) Zotero.Reader.unregisterEventListener("renderToolbar", this.readerHandler);
    for (const button of this.readerButtons) button.remove();
    this.readerButtons.clear();
    for (const win of [...this.windows]) this.removeWindow(win);
    this.panels.clear();
  },
  openSettings() { Zotero.Utilities.Internal.openPreferences("supertranslate-preferences"); },
  async guard(action) {
    try { return await action(); }
    catch (e) { Services.prompt.alert(Zotero.getMainWindow(), "SuperTranslate", e.message || "操作失败，请重试。"); }
  },
  async attachment(item) {
    if (item?.isPDFAttachment()) return item;
    if (item?.isRegularItem()) {
      const attachments = await Zotero.Items.getAsync(item.getAttachments());
      return attachments.find(a => a.isPDFAttachment() && !a.getTags().some(t => t.tag.startsWith("supertranslate:")));
    }
    return null;
  },
  async source(item) {
    const attachment = await this.attachment(item);
    if (!attachment) throw new Error("这条文献还没有 PDF 附件。请先添加或下载 PDF。");
    if (attachment.getTags().some(t => t.tag.startsWith("supertranslate:"))) throw new Error("这是已生成的译文，请选择原始论文。");
    const path = await attachment.getFilePathAsync();
    if (!path || !(await IOUtils.exists(path))) throw new Error("PDF 尚未下载到本机。请先在 Zotero 中打开附件，再翻译。");
    const stat = await IOUtils.stat(path);
    if (stat.size > 100 * 1024 * 1024) throw new Error("PDF 超过 100 MB，请使用较小的文件。");
    const bytes = SuperTranslateCore.pdfBytes(await IOUtils.read(path));
    const parent = attachment.parentItemID ? await Zotero.Items.getAsync(attachment.parentItemID) : attachment;
    return { id: attachment.id, key: `${attachment.libraryID}:${attachment.key}`, hash: this.hash(bytes),
      title: parent.getField("title") || attachment.getField("title"), filename: PathUtils.filename(path), bytes };
  },
  async translateItems(items) {
    if (this.running) throw new Error("正在提交所选论文，请稍候。");
    if (!items.length) return;
    if (items.length > 20) throw new Error("每次最多选择 20 篇论文。");
    if (!this.controller.client.token) { this.openSettings(); throw new Error("请先连接论文翻译服务。"); }
    if (this.selectionDirty()) throw new Error("供应商或模型尚未应用，请先点击「应用翻译设置」。");
    const controller = this.controller;
    const options = { provider: this.selection?.provider ?? this.pref("provider", "deepseek"), quality: this.selection?.quality ?? this.pref("quality", "balanced"), variant: "translated" };
    this.running = true;
    this.revealProgress = true;
    this.refreshPanels();
    const errors = [];
    try {
      await SuperTranslateCore.mapLimit(items, 2, async item => {
        try {
          if (controller !== this.controller || controller.closed) return;
          const source = await this.source(item);
          await this.checkImport({ attachmentID: source.id, sourceKey: source.key });
          if (controller !== this.controller || controller.closed) return;
          await controller.submit(source, options);
        } catch (e) { errors.push(`${item.getField("title").slice(0, 70)}：${e.message}`); }
      });
    } finally { this.running = false; this.refreshPanels(); this.revealProgress = false; if (this.timer) this.schedule(); }
    if (errors.length) throw new Error(errors.join("\n\n"));
  },
  async checkImport(task) {
    const source = await Zotero.Items.getAsync(task.attachmentID);
    if (!source || source.deleted || `${source.libraryID}:${source.key}` !== task.sourceKey) throw new Error("原 PDF 已移除，无法关联译文。");
    const library = Zotero.Libraries.get(source.libraryID);
    if (!library.editable || !library.filesEditable) throw new Error("这个文献库没有保存附件的权限。请复制到我的文库后翻译。");
    if (task.sourceHash) {
      const path = await source.getFilePathAsync();
      if (!path || !(await IOUtils.exists(path)) || (await IOUtils.stat(path)).size > 100 * 1024 * 1024 || this.hash(await IOUtils.read(path)) !== task.sourceHash) {
        throw new Error("原 PDF 已变化或已移除，译文保留在服务端。请恢复原 PDF 后再归档。");
      }
    }
    return source;
  },
  marker(task) { return `supertranslate:${this.hash(new TextEncoder().encode(task.namespace + task.sourceKey + task.sourceHash + task.variant))}`; },
  async findImported(task) {
    const source = await this.checkImport(task);
    const search = new Zotero.Search(); search.libraryID = source.libraryID;
    search.addCondition("tag", "is", this.marker(task));
    const ids = await search.search();
    for (const id of ids) {
      const item = await Zotero.Items.getAsync(id);
      if (item?.isPDFAttachment() && !item.deleted && (item.parentItemID || null) === (source.parentItemID || null) && await item.fileExists()) return id;
    }
    // Zotero's import API commits its own transaction. Recover the narrow crash
    // window before tagging by matching the durable intent AND the PDF digest.
    const journalPath = PathUtils.join(Zotero.Profile.dir, "supertranslate", this.marker(task).replace(":", "-") + ".json");
    if (await IOUtils.exists(journalPath)) {
      const journal = await IOUtils.readJSON(journalPath);
      const candidates = new Zotero.Search(); candidates.libraryID = source.libraryID;
      candidates.addCondition("title", "is", journal.title);
      for (const id of await candidates.search()) {
        const item = await Zotero.Items.getAsync(id);
        if (!item?.isPDFAttachment() || item.deleted || (item.parentItemID || null) !== (source.parentItemID || null)) continue;
        const path = await item.getFilePathAsync();
        if (path && await IOUtils.exists(path) && (await IOUtils.stat(path)).size <= 300 * 1024 * 1024 && this.hash(await IOUtils.read(path)) === journal.digest) {
          item.addTag(this.marker(task)); await item.saveTx(); return id;
        }
      }
    }
    return null;
  },
  async importPDF(task, bytes) {
    const source = await this.checkImport(task);
    const folder = PathUtils.join(Zotero.getTempDirectory().path, "supertranslate-" + this.uuid());
    await IOUtils.makeDirectory(folder, { permissions: 0o700 });
    const title = task.variant === "dual" ? `中英对照 · ${task.title}` : `中文版《${task.title}》`;
    const filename = task.variant === "dual"
      ? SuperTranslateCore.safeName(task.title) + " - 中英对照.pdf"
      : `中文版《${SuperTranslateCore.safeName(task.title)}》.pdf`;
    const path = PathUtils.join(folder, filename);
    try {
      await IOUtils.write(path, bytes, { permissions: 0o600 });
      const journalPath = PathUtils.join(Zotero.Profile.dir, "supertranslate", this.marker(task).replace(":", "-") + ".json");
      await IOUtils.writeJSON(journalPath, { title, digest: this.hash(bytes) }, { tmpPath: journalPath + ".tmp", permissions: 0o600 });
      const imported = await Zotero.Attachments.importFromFile({ file: path, libraryID: source.libraryID,
        parentItemID: source.parentItemID || undefined,
        collections: source.parentItemID ? undefined : source.getCollections(), title });
      imported.addTag(this.marker(task)); await imported.saveTx();
      return imported.id;
    } finally { await IOUtils.remove(folder, { recursive: true, ignoreAbsent: true }); }
  },
  async loadModels(refreshProvider = "") {
    const controller = this.controller;
    if (this.modelsPromise) {
      await this.modelsPromise;
      if (controller !== this.controller || controller.closed) return;
      if (!refreshProvider) return;
    }
    const request = (async () => {
      if (refreshProvider) await controller.client.refreshModels(refreshProvider);
      const [catalogs, credentials] = await Promise.all([controller.client.models(), controller.client.credentials()]);
      if (controller !== this.controller || controller.closed) return;
      if (!Array.isArray(catalogs) || !Array.isArray(credentials)) throw new Error("服务的模型目录格式不正确");
      this.catalogs = catalogs; this.credentials = credentials; this.modelsLoadedAt = Date.now();
      if (this.selection?.model == null) this.selection.model = this.selectedModel(this.selection.provider);
      this.refreshPanels();
    })();
    this.modelsPromise = request;
    try { await request; } finally { if (this.modelsPromise === request) this.modelsPromise = null; }
  },
  renderSelection(doc) {
    const box = this.element(doc, "div", null, "st-selection");
    const d = this.selection;
    if (!d) return box;
    const provider = this.element(doc, "select"); provider.setAttribute("aria-label", "API 供应商");
    for (const [id, label] of Object.entries(this.providerLabels)) { const o = this.element(doc, "option", label); o.value = id; provider.append(o); }
    provider.value = d.provider;
    provider.addEventListener("change", () => { d.provider = provider.value; d.model = this.selectedModel(d.provider); this.refreshPanels(true); });
    box.append(this.element(doc, "label", "API 供应商"), provider);
    if (d.provider) {
      const model = this.element(doc, "input"); model.value = d.model || ""; model.placeholder = "模型 ID / 豆包 Endpoint ID";
      model.setAttribute("aria-label", "翻译模型");
      const list = this.element(doc, "datalist"); list.id = "st-models-" + this.uuid(); model.setAttribute("list", list.id);
      for (const id of this.catalogs.find(c => c.provider === d.provider)?.models || []) { const o = this.element(doc, "option"); o.value = id; list.append(o); }
      model.addEventListener("input", () => { d.model = model.value.trim(); });
      box.append(this.element(doc, "label", "翻译模型"), model, list);
    }
    const quality = this.element(doc, "select"); quality.setAttribute("aria-label", "翻译质量");
    for (const [id, label] of [["balanced", "均衡 · 推荐"], ["quality", "高质量"], ["fast", "快速 · Google（不使用所选模型）"]]) {
      const o = this.element(doc, "option", label); o.value = id; quality.append(o);
    }
    quality.value = d.quality; quality.addEventListener("change", () => { d.quality = quality.value; this.refreshPanels(true); }); box.append(this.element(doc, "label", "翻译质量"), quality);
    box.append(this.button(doc, "应用翻译设置", () => this.applySelection(), "st-button st-primary"));
    if (d.provider) box.append(this.button(doc, "更新模型目录", () => this.loadModels(d.provider), "st-button st-quiet"));
    box.append(this.element(doc, "p", "更改后先应用。模型与悬窗、网页共享；快速档使用 Google。", "st-footnote"));
    return box;
  },
  async openLibraryPDF(paper) {
    const controller = this.controller;
    const p = await controller.client.paper(paper.id);
    if (!p.has_translated) throw new Error("这篇文档尚无中文译文。");
    const bytes = await controller.client.download(p.id, "translated");
    if (controller !== this.controller || controller.closed) return;
    const folder = PathUtils.join(Zotero.getTempDirectory().path, "supertranslate-library-" + this.uuid());
    await IOUtils.makeDirectory(folder, { permissions: 0o700 });
    const path = PathUtils.join(folder, `${p.translation_status === "completed" ? "中文版" : "待修复草稿"}《${SuperTranslateCore.safeName(p.title)}》.pdf`);
    await IOUtils.write(path, bytes, { permissions: 0o600 }); Zotero.launchFile(path);
  },
  element(doc, tag, text, className) {
    const node = doc.createElementNS("http://www.w3.org/1999/xhtml", tag);
    if (text != null) node.textContent = text;
    if (className) node.className = className;
    return node;
  },
  button(doc, text, action, className = "st-button") {
    const b = this.element(doc, "button", text, className); b.type = "button";
    b.addEventListener("click", () => this.guard(async () => { b.disabled = true; try { await action(); } finally { if (b.isConnected) b.disabled = false; } }));
    return b;
  },
  progressView(doc, task, compact = false) {
    const pending = ["queued", "uploading", "starting", "start_unknown", "pending"].includes(task.state);
    const known = Number.isFinite(task.progress) && !pending;
    const percent = known ? Math.round(Math.max(0, Math.min(1, task.progress)) * 1000) / 10 : null;
    const paused = ["repair_pending", "failed", "cancelled", "interrupted", "sync_error"].includes(task.state);
    const text = percent == null ? (task.state === "uploading" ? "上传中" : "等待进度") : `${percent}%`;
    const stage = task.stage || task.message || SuperTranslateCore.labels[task.state] || "";
    const view = this.element(doc, "div", null, `st-progress${compact ? " st-progress-compact" : ""}${paused ? " st-progress-paused" : ""}`);
    view.title = `${SuperTranslateCore.labels[task.state] || "翻译"} · ${text} · ${stage}`;
    const caption = this.element(doc, "div", null, "st-progress-caption");
    const captionText = compact && paused ? `${text} · ${task.state === "repair_pending" ? "暂停" : SuperTranslateCore.labels[task.state]}` : text;
    caption.append(this.element(doc, "strong", captionText, "st-progress-value"));
    if (!compact) caption.append(this.element(doc, "span", stage, "st-progress-stage"));
    const meter = this.element(doc, "div", null, `st-progress-track${percent == null && !paused ? " st-progress-unknown" : ""}`);
    meter.setAttribute("role", "progressbar"); meter.setAttribute("aria-label", "翻译进度");
    meter.setAttribute("aria-valuemin", "0"); meter.setAttribute("aria-valuemax", "100");
    meter.setAttribute("aria-valuetext", view.title);
    if (percent != null) meter.setAttribute("aria-valuenow", percent);
    const fill = this.element(doc, "div", null, "st-progress-fill"); fill.style.width = percent == null ? (paused ? "0%" : "30%") : `${percent}%`;
    // Zotero's reader document cannot load jar: stylesheets. Its small meter
    // uses local styles, like the existing reader button, without new privileges.
    if (compact) {
      view.style.cssText = `display:inline-block;width:${paused ? 96 : 64}px;margin-right:3px;vertical-align:middle`;
      caption.style.cssText = "display:block;margin-bottom:2px;white-space:nowrap";
      caption.firstChild.style.cssText = "font:600 10px/12px system-ui,sans-serif;color:inherit";
      meter.style.cssText = "display:block;width:100%;height:4px;overflow:hidden;border-radius:4px;background:rgba(127,127,127,.25)";
      fill.style.display = "block"; fill.style.height = "100%"; fill.style.borderRadius = "inherit";
      fill.style.background = paused ? "#b77920" : "#637be8";
    }
    meter.append(fill); view.append(caption, meter); return view;
  },
  taskList(doc, item) {
    const list = this.element(doc, "div", null, "st-task-list");
    const ids = new Set(item.isRegularItem() ? item.getAttachments() : [item.id]);
    const tasks = (this.controller?.tasks || []).filter(t => ids.has(t.attachmentID) || ids.has(t.resultID));
    for (const task of tasks.slice(0, 8)) list.append(this.taskCard(doc, task));
    return list;
  },
  refreshReaderProgress() {
    for (const control of this.readerButtons) {
      if (!control.isConnected && control.dataset.stMounted) { this.readerButtons.delete(control); continue; }
      if (control.isConnected) control.dataset.stMounted = "true";
      const id = Number(control.dataset.stReaderItemID);
      const task = this.controller?.tasks.find(t => t.attachmentID === id || t.resultID === id);
      const status = control.querySelector(".st-reader-progress");
      if (!status) continue;
      status.hidden = !task;
      if (!task) {
        control.dataset.stProgressSnapshot = ""; status.replaceChildren();
        control.querySelector("button").title = "SuperTranslate · 翻译整篇论文";
        continue;
      }
      const snapshot = JSON.stringify([task.state, task.progress, task.stage, task.message]);
      if (snapshot === control.dataset.stProgressSnapshot) continue;
      control.dataset.stProgressSnapshot = snapshot;
      const view = this.progressView(control.ownerDocument, task, true);
      status.replaceChildren(view);
      control.querySelector("button").title = "SuperTranslate · " + view.title;
    }
  },
  renderPanel(args) {
    const { body, item } = args;
    if (!item || this.disposed) return;
    this.panels.set(body, args);
    const doc = body.ownerDocument;
    const expanded = body.querySelector("details")?.open || false;
    body.replaceChildren();
    const css = this.element(doc, "link"); css.rel = "stylesheet"; css.href = this.rootURI + this.stylePath; body.append(css);
    const root = this.element(doc, "div", null, "st-root st-panel"); body.append(root);
    const top = this.element(doc, "div", null, "st-brand");
    top.append(this.element(doc, "span", "论文翻译", "st-title"), this.element(doc, "span", "保留版式", "st-badge")); root.append(top);
    root.append(this.element(doc, "p", "从原文到译文，留在你的文献库。", "st-muted"));
    const connected = !!this.controller?.client.token;
    if (!connected) {
      root.append(this.element(doc, "p", "连接已有的 SuperTranslate 账号，即可翻译当前 PDF。", "st-hint"));
      root.append(this.button(doc, "连接翻译服务", () => this.openSettings(), "st-button st-primary")); return;
    }
    root.append(this.taskList(doc, item));
    const provider = this.selection?.provider ?? "deepseek";
    const catalog = this.catalogs.find(c => c.provider === provider);
    root.append(this.element(doc, "div", `${catalog?.label || this.providerLabels[provider]} · ${catalog?.selected_model || "账号默认模型"}`, "st-model"));
    const picker = this.element(doc, "details", null, "st-picker");
    picker.open = expanded;
    picker.append(this.element(doc, "summary", "切换供应商、模型与质量"), this.renderSelection(doc)); root.append(picker);
    const actions = this.element(doc, "div", null, "st-actions");
    const submit = this.button(doc, "翻译并保存中文版", () => this.translateItems([item]), "st-button st-primary");
    submit.disabled = this.running || args.editable === false;
    actions.append(submit, this.button(doc, "设置", () => this.openSettings(), "st-button st-quiet")); root.append(actions);
    root.append(this.element(doc, "p", "PDF 将发送至你配置的翻译服务。译文自动附在原论文下，原文和批注保留。", "st-footnote"));
  },
  taskCard(doc, task) {
    const card = this.element(doc, "div", null, "st-task");
    const head = this.element(doc, "div", null, "st-task-head");
    head.append(this.element(doc, "strong", task.variant === "dual" ? "中英对照 PDF" : "中文 PDF"),
      this.element(doc, "span", SuperTranslateCore.labels[task.state] || task.state, `st-status ${task.state === "saved" ? "st-success" : ""}`)); card.append(head);
    card.append(this.progressView(doc, task));
    if (task.state === "repair_pending") {
      const attempts = task.attempts != null ? `已自动尝试 ${task.attempts}${task.maxAttempts ? "/" + task.maxAttempts : ""} 次。` : "";
      card.append(this.element(doc, "p", attempts + "当前未继续处理，需等待服务端修复。刷新只同步状态，不会重新翻译。", "st-notice st-error"));
    }
    if (task.message && task.message !== task.stage) {
      const message = this.element(doc, "p", task.message, "st-message"); message.setAttribute("aria-live", "polite"); card.append(message);
    }
    const actions = this.element(doc, "div", null, "st-actions");
    if (task.resultID) actions.append(this.button(doc, "打开译文", () => Zotero.getMainWindow().ZoteroPane.viewAttachment(task.resultID)));
    else {
      actions.append(this.button(doc, "刷新状态", () => this.controller.refresh(), "st-button st-quiet"));
      if (["interrupted", "failed", "cancelled", "start_unknown", "sync_error", "completed", "pending"].includes(task.state)) {
        actions.append(this.button(doc, "重试", async () => this.controller.retry(task, await this.source(await Zotero.Items.getAsync(task.attachmentID))), "st-button st-quiet"));
      }
      if (task.paperID && task.state === "translating" && !task.cancelRequested) actions.append(this.button(doc, "取消翻译", () => this.controller.cancel(task), "st-button st-quiet"));
    }
    if (!task.resultID && task.available && ["repairing", "repair_pending", "failed"].includes(task.state)) {
      actions.append(this.button(doc, "查看待修复译文", () => this.openDraft(task)));
      card.append(this.element(doc, "p", "待修复译文尚未通过质检，仅供预览；不会自动归档为正式译文。", "st-footnote"));
    }
    if (task.hasQAReport) actions.append(this.button(doc, "质检详情", () => this.showQA(task), "st-button st-quiet"));
    card.append(actions); return card;
  },
  async openDraft(task) {
    const controller = this.controller;
    if (!controller.tasks.includes(task)) throw new Error("账号或任务已变化，请刷新后重试。");
    const paper = await controller.client.paper(task.paperID);
    controller.apply(task, paper); await controller.save();
    if (!task.available) throw new Error("服务端尚未保留可查看的译文，请稍后刷新。");
    const bytes = await controller.client.download(task.paperID, task.variant);
    if (this.disposed || controller !== this.controller) throw new Error("账号已切换，请重新打开译文。");
    const folder = PathUtils.join(Zotero.getTempDirectory().path, "supertranslate-preview-" + this.uuid());
    await IOUtils.makeDirectory(folder, { permissions: 0o700 });
    const label = task.variant === "dual" ? "中英对照" : "中文";
    const path = PathUtils.join(folder, "待修复草稿 - " + SuperTranslateCore.safeName(task.title) + ` - ${label}.pdf`);
    await IOUtils.write(path, bytes, { permissions: 0o600 });
    Zotero.launchFile(path);
    return path;
  },
  async showQA(task) {
    const controller = this.controller;
    if (!controller.tasks.includes(task)) throw new Error("账号或任务已变化，请刷新后重试。");
    const report = await controller.client.qaReport(task.paperID);
    if (!report || !Array.isArray(report.issues)) throw new Error("服务返回的质检报告格式不正确。");
    if (this.disposed || controller !== this.controller) return;
    const issues = report.issues.slice(0, 50).map(i => `第 ${Number(i.page) || "?"} 页 · ${i.severity === "error" ? "错误" : "提示"}\n${String(i.message || i.code || "").slice(0, 500)}`);
    const passed = ["passed", "warning"].includes(report.status) && report.error_count === 0;
    const summary = passed ? "质检通过" : "质检尚未通过，译文仍需修复";
    const counts = `${Number(report.error_count) || 0} 个错误，${Number(report.warning_count) || 0} 个提示`;
    Services.prompt.alert(Zotero.getMainWindow(), "SuperTranslate · 质检详情", [task.title, summary + " · " + counts, ...issues].join("\n\n"));
  },
  refreshPanels(force = false) {
    this.refreshReaderProgress();
    let revealed = false;
    for (const [body, args] of [...this.panels]) {
      if (!body.isConnected) this.panels.delete(body);
      else {
        if (force || !body.contains(body.ownerDocument.activeElement) || !["input", "select", "textarea"].includes(body.ownerDocument.activeElement?.localName)) this.renderPanel(args);
        else body.querySelector(".st-task-list")?.replaceWith(this.taskList(body.ownerDocument, args.item));
        if (this.revealProgress) {
          const card = body.querySelector(".st-task");
          if (card) {
            const section = body.closest("item-pane-custom-section");
            if (section) section.collapsed = false;
            card.scrollIntoView({ block: "nearest" }); revealed = true;
          }
        }
      }
    }
    if (revealed) this.revealProgress = false;
  },
};
