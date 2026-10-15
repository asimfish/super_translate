/* Native Zotero adapter. UI strings and remote metadata are inserted as text. */
var SuperTranslateZotero = {
  id: pluginID, rootURI, windows: new Set(), panels: new Map(), menus: [], controller: null,
  catalogs: [], credentials: [], timer: null, running: false, disposed: false, readerButtons: new Set(),
  pref(key, fallback = "") { return Services.prefs.getStringPref(`extensions.supertranslate.${key}`, fallback); },
  setPref(key, value) { Services.prefs.setStringPref(`extensions.supertranslate.${key}`, String(value)); },
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
    this.controller?.shutdown();
    const client = await this.client();
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
    this.controller = await new SuperTranslateCore.ZoteroTranslationController({ client, namespace, store,
      host: { uuid: () => this.uuid(), findImported: t => this.findImported(t), checkImport: t => this.checkImport(t), importPDF: (t, b) => this.importPDF(t, b) },
      changed: () => this.refreshPanels(),
    }).init();
    this.catalogs = []; this.credentials = [];
    this.refreshPanels();
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
      src: "content/preferences.xhtml", scripts: ["content/preferences.js"], stylesheets: ["content/style.css"], image: "content/icon.svg" });
    this.menus.push(Zotero.MenuManager.registerMenu({ menuID: "supertranslate-translate", pluginID: this.id, target: "main/library/item", menus: [
      { menuType: "menuitem", l10nID: "supertranslate-menu-translate", icon: this.rootURI + "content/icon.svg",
        onShowing: (_, c) => c.setVisible(c.items?.some(i => i.isRegularItem() || i.isPDFAttachment())),
        onCommand: (_, c) => this.guard(() => this.translateItems(c.items || [])) },
    ] }));
    this.menus.push(Zotero.MenuManager.registerMenu({ menuID: "supertranslate-settings", pluginID: this.id, target: "main/menubar/tools", menus: [
      { menuType: "menuitem", l10nID: "supertranslate-menu-settings", onCommand: () => this.openSettings() },
    ] }));
    this.readerHandler = ({ reader, doc, append }) => {
      const button = doc.createElement("button"); button.textContent = "译";
      button.title = "SuperTranslate · 翻译整篇论文"; button.setAttribute("aria-label", button.title);
      button.style.cssText = "border:0;border-radius:5px;background:transparent;color:inherit;padding:4px 8px;cursor:pointer;font-size:14px";
      button.addEventListener("click", () => this.guard(async () => this.translateItems([await Zotero.Items.getAsync(reader.itemID)])));
      this.readerButtons.add(button);
      append(button);
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
    this.timer = Cc["@mozilla.org/timer;1"].createInstance(Ci.nsITimer);
    this.timer.initWithCallback(async () => {
      try { if (!this.running) await this.controller?.refresh(); }
      catch (_) { /* Persistence/network failures are shown by tasks; no raw credentials logged. */ }
      finally { this.schedule(); }
    }, 5000, Ci.nsITimer.TYPE_ONE_SHOT);
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
    this.running = true;
    const errors = [];
    try {
      for (const item of items) {
        try {
          const source = await this.source(item);
          await this.checkImport({ attachmentID: source.id, sourceKey: source.key });
          await this.controller.submit(source, { provider: this.pref("provider", "deepseek"), quality: this.pref("quality", "balanced"), variant: this.pref("variant", "dual") });
        } catch (e) { errors.push(`${item.getField("title").slice(0, 70)}：${e.message}`); }
      }
    } finally { this.running = false; this.refreshPanels(); }
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
    const label = task.variant === "dual" ? "中英对照" : "中文译文";
    const filename = SuperTranslateCore.safeName(task.title) + ` - ${label}.pdf`;
    const path = PathUtils.join(folder, filename);
    try {
      await IOUtils.write(path, bytes, { permissions: 0o600 });
      const title = `${label} · ${task.title}`;
      const journalPath = PathUtils.join(Zotero.Profile.dir, "supertranslate", this.marker(task).replace(":", "-") + ".json");
      await IOUtils.writeJSON(journalPath, { title, digest: this.hash(bytes) }, { tmpPath: journalPath + ".tmp", permissions: 0o600 });
      const imported = await Zotero.Attachments.importFromFile({ file: path, libraryID: source.libraryID,
        parentItemID: source.parentItemID || undefined,
        collections: source.parentItemID ? undefined : source.getCollections(), title });
      imported.addTag(this.marker(task)); await imported.saveTx();
      return imported.id;
    } finally { await IOUtils.remove(folder, { recursive: true, ignoreAbsent: true }); }
  },
  async loadModels() {
    const [catalogs, credentials] = await Promise.all([this.controller.client.models(), this.controller.client.credentials()]);
    if (!Array.isArray(catalogs) || !Array.isArray(credentials)) throw new Error("服务的模型目录格式不正确");
    this.catalogs = catalogs; this.credentials = credentials; this.refreshPanels();
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
  renderPanel(args) {
    const { body, item } = args;
    if (!item || this.disposed) return;
    this.panels.set(body, args);
    const doc = body.ownerDocument;
    body.replaceChildren();
    const css = this.element(doc, "link"); css.rel = "stylesheet"; css.href = this.rootURI + "content/style.css"; body.append(css);
    const root = this.element(doc, "div", null, "st-root st-panel"); body.append(root);
    const top = this.element(doc, "div", null, "st-brand");
    top.append(this.element(doc, "span", "论文翻译", "st-title"), this.element(doc, "span", "保留版式", "st-badge")); root.append(top);
    root.append(this.element(doc, "p", "从原文到译文，留在你的文献库。", "st-muted"));
    const connected = !!this.controller?.client.token;
    if (!connected) {
      root.append(this.element(doc, "p", "连接已有的 SuperTranslate 账号，即可翻译当前 PDF。", "st-hint"));
      root.append(this.button(doc, "连接翻译服务", () => this.openSettings(), "st-button st-primary")); return;
    }
    const provider = this.pref("provider", "deepseek");
    const catalog = this.catalogs.find(c => c.provider === provider);
    root.append(this.element(doc, "div", `${catalog?.label || provider} · ${catalog?.selected_model || "账号默认模型"}`, "st-model"));
    const actions = this.element(doc, "div", null, "st-actions");
    const submit = this.button(doc, "翻译并保存附件", () => this.translateItems([item]), "st-button st-primary");
    submit.disabled = this.running || args.editable === false;
    actions.append(submit, this.button(doc, "设置", () => this.openSettings(), "st-button st-quiet")); root.append(actions);
    const taskList = this.element(doc, "div", null, "st-task-list"); root.append(taskList);
    const ids = new Set(item.isRegularItem() ? item.getAttachments() : [item.id]);
    const tasks = (this.controller?.tasks || []).filter(t => ids.has(t.attachmentID));
    for (const task of tasks.slice(0, 8)) taskList.append(this.taskCard(doc, task));
    root.append(this.element(doc, "p", "PDF 将发送至你配置的翻译服务。译文自动附在原论文下，原文和批注保留。", "st-footnote"));
  },
  taskCard(doc, task) {
    const card = this.element(doc, "div", null, "st-task");
    const head = this.element(doc, "div", null, "st-task-head");
    head.append(this.element(doc, "strong", task.variant === "dual" ? "中英对照 PDF" : "中文 PDF"),
      this.element(doc, "span", SuperTranslateCore.labels[task.state] || task.state, `st-status ${task.state === "saved" ? "st-success" : ""}`)); card.append(head);
    const progress = this.element(doc, "progress"); progress.max = 1; progress.value = task.progress; progress.setAttribute("aria-label", "翻译进度"); card.append(progress);
    const message = this.element(doc, "p", task.message, "st-message"); message.setAttribute("aria-live", "polite"); card.append(message);
    const actions = this.element(doc, "div", null, "st-actions");
    if (task.resultID) actions.append(this.button(doc, "打开译文", () => Zotero.getMainWindow().ZoteroPane.viewAttachment(task.resultID)));
    else {
      actions.append(this.button(doc, "刷新状态", () => this.controller.refresh(), "st-button st-quiet"));
      if (["interrupted", "failed", "start_unknown", "sync_error", "completed", "pending"].includes(task.state)) {
        actions.append(this.button(doc, "重试", async () => this.controller.retry(task, await this.source(await Zotero.Items.getAsync(task.attachmentID))), "st-button st-quiet"));
      }
    }
    card.append(actions); return card;
  },
  refreshPanels() {
    for (const [body, args] of [...this.panels]) {
      if (!body.isConnected) this.panels.delete(body);
      else this.renderPanel(args);
    }
  },
};
