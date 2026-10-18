/* Shared protocol and durable task controller. No Zotero globals or dependencies. */
var SuperTranslateCore = (() => {
  "use strict";
  const PROVIDERS = ["deepseek", "kimi", "openai", "anthropic", "glm", "gemini", "qwen", "doubao"];
  const ACTIVE = new Set(["queued", "uploading", "starting", "pending", "translating", "repairing", "repair_pending"]);
  class ClientError extends Error {
    constructor(message, code = "CLIENT") { super(message); this.name = "ClientError"; this.code = code; }
  }
  function origin(value) {
    let u;
    try { u = new URL(String(value).trim()); } catch (_) { throw new ClientError("请输入完整的服务地址，例如 https://translate.example.com"); }
    if (u.username || u.password || u.search || u.hash || u.pathname !== "/") throw new ClientError("服务地址只能包含协议、主机和端口");
    if (u.protocol !== "https:" && !(u.protocol === "http:" && ["127.0.0.1", "localhost", "[::1]"].includes(u.hostname))) {
      throw new ClientError("远程服务须使用 HTTPS；本机服务支持 HTTP");
    }
    return u.origin;
  }
  function paperID(id) {
    if (typeof id !== "string" || !/^[a-f0-9]{12}$/.test(id)) throw new ClientError("服务返回了无效的文档编号", "PROTOCOL");
    return id;
  }
  function pdfBytes(bytes, max = 100 * 1024 * 1024) {
    if (!ArrayBuffer.isView(bytes) || Object.prototype.toString.call(bytes) !== "[object Uint8Array]" || bytes.length < 8 || bytes.length > max ||
        String.fromCharCode(...bytes.slice(0, 5)) !== "%PDF-" ||
        !new TextDecoder().decode(bytes.slice(-1024)).includes("%%EOF")) {
      throw new ClientError("文件不是完整的 PDF，或超出大小限制", "PDF");
    }
    return bytes;
  }
  function safeName(title) {
    const clean = String(title || "论文").replace(/[\x00-\x1f\x7f/\\:*?"<>|]/g, "_");
    let result = "", length = 0;
    for (const char of clean) {
      const size = new TextEncoder().encode(char).length;
      if (length + size > 180 || result.length + char.length > 100) break;
      result += char; length += size;
    }
    return result.replace(/[. ]+$/g, "") || "论文";
  }
  function multipart(bytes, filename, boundary) {
    const name = safeName(String(filename).replace(/\.pdf$/i, "")) + ".pdf";
    const head = new TextEncoder().encode(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${name}"\r\nContent-Type: application/pdf\r\n\r\n`);
    const tail = new TextEncoder().encode(`\r\n--${boundary}--\r\n`);
    const body = new Uint8Array(head.length + bytes.length + tail.length);
    body.set(head); body.set(bytes, head.length); body.set(tail, head.length + bytes.length);
    return body;
  }
  class ZoteroServiceClient {
    constructor({ baseURL, token = "", transport }) {
      this.origin = origin(baseURL);
      if (/[\x00-\x20\x7f]/.test(token)) throw new ClientError("登录凭据格式无效");
      this.token = token; this.transport = transport;
    }
    async request(path, method = "GET", body, extra = {}) {
      const headers = { Accept: "application/json", ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}), ...extra.headers };
      let response;
      try { response = await this.transport({ url: this.origin + path, method, body, ...extra, headers }); }
      catch (_) { throw new ClientError("连接中断，请检查服务地址或本机连接。已提交的任务会在重连后继续同步。", "NETWORK"); }
      if (response.status < 200 || response.status >= 300) {
        const status = response.status;
        throw new ClientError(status === 401 || status === 403 ? "登录已失效或无访问权限，请在设置中重新登录。" :
          status === 409 ? "服务正在处理此论文，请刷新状态。" :
          status === 404 ? "服务中未找到此文档或接口。" : `服务请求失败（HTTP ${status}），请稍后重试。`, `HTTP_${status}`);
      }
      return response.data;
    }
    json(path, method, data) { return this.request(path, method, JSON.stringify(data), { headers: { "Content-Type": "application/json" } }); }
    connect() { return this.request("/api/papers/?limit=1"); }
    login(username, password) { return this.json("/api/auth/login", "POST", { username, password }); }
    models() { return this.request("/api/provider-credentials/models"); }
    credentials() { return this.request("/api/provider-credentials"); }
    saveModel(provider, model, apiKey = "") {
      if (!PROVIDERS.includes(provider) || !/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$/.test(model)) throw new ClientError("请选择有效的供应商和模型");
      return this.json(`/api/provider-credentials/${provider}`, "PUT", { model, ...(apiKey ? { api_key: apiKey } : {}) });
    }
    async upload(bytes, name, boundary) {
      const p = await this.request("/api/papers/upload", "POST", multipart(pdfBytes(bytes), name, boundary), { headers: { "Content-Type": `multipart/form-data; boundary=${boundary}` }, timeout: 180000 });
      paperID(p?.id); return p;
    }
    async paper(id) {
      const seen = new Set();
      for (let i = 0; i < 5; i++) {
        paperID(id);
        if (seen.has(id)) throw new ClientError("文档关联循环", "PROTOCOL");
        seen.add(id);
        const p = await this.request(`/api/papers/${id}`);
        if (p?.id !== id) throw new ClientError("服务返回了不匹配的文档", "PROTOCOL");
        if (!p.canonical_paper_id || p.canonical_paper_id === id) {
          // Paper status aggregates active repair and paused repair. Only the
          // durable job distinguishes them; older servers still supply a stage.
          p.latest_job = null;
          if (["repairing", "repair_pending"].includes(p.translation_status)) {
            try {
              const jobs = await this.request(`/api/papers/${id}/jobs`);
              if (Array.isArray(jobs) && jobs[0]?.paper_id === id) p.latest_job = jobs[0];
            } catch (_) { /* Keep the paper's stage when optional job details are unavailable. */ }
          }
          return p;
        }
        id = p.canonical_paper_id;
      }
      throw new ClientError("文档关联层级异常", "PROTOCOL");
    }
    start(id, { provider, quality }) {
      paperID(id);
      if (!PROVIDERS.includes(provider) || !["fast", "balanced", "quality"].includes(quality)) throw new ClientError("翻译配置无效");
      const q = `quality=${quality}&preserve_graphics_text=true&qa_mode=single` + (quality === "fast" ? "" : `&backend=${provider}`);
      return this.request(`/api/papers/${id}/translate?${q}`, "POST");
    }
    async download(id, variant) {
      paperID(id);
      if (!["translated", "dual"].includes(variant)) throw new ClientError("译文类型无效");
      return pdfBytes(await this.request(`/api/papers/${id}/download/${variant}`, "GET", undefined,
        { binary: true, timeout: 180000, maxBytes: 300 * 1024 * 1024, headers: { Accept: "application/pdf" } }), 300 * 1024 * 1024);
    }
    qaReport(id) { return this.request(`/api/papers/${paperID(id)}/qa-report`); }
  }
  class ZoteroTranslationController {
    constructor({ client, store, host, namespace, changed = () => {} }) {
      Object.assign(this, { client, store, host, namespace, changed });
      this.tasks = []; this.busy = new Set(); this.closed = false; this.saveChain = Promise.resolve();
    }
    async init() {
      this.tasks = (await this.store.load()).filter(t => t.namespace === this.namespace);
      for (const t of this.tasks) {
        if (["queued", "uploading"].includes(t.state) && !t.paperID) { t.state = "interrupted"; t.message = "上次上传中断，点击重试。"; }
        if (t.state === "starting") { t.state = "start_unknown"; t.message = "提交结果待确认，正在同步服务状态。"; }
      }
      this.changed(); return this;
    }
    async save() {
      const snapshot = JSON.parse(JSON.stringify(this.tasks));
      this.saveChain = this.saveChain.catch(() => {}).then(() => this.store.save(snapshot));
      await this.saveChain; this.changed();
    }
    async submit(source, options) {
      const existing = this.tasks.find(t => t.sourceKey === source.key && t.sourceHash === source.hash && t.variant === options.variant);
      if (existing) return existing;
      if (this.closed) throw new ClientError("插件正在关闭");
      const task = { id: this.host.uuid(), namespace: this.namespace, sourceKey: source.key, sourceHash: source.hash,
        attachmentID: source.id, title: source.title, variant: options.variant, provider: options.provider, quality: options.quality,
        state: "queued", progress: 0, createdAt: new Date().toISOString(), message: "准备上传" };
      this.tasks.unshift(task); await this.save();
      await this.run(task, source); return task;
    }
    async run(task, source) {
      if (this.closed || this.busy.has(task.id)) return;
      this.busy.add(task.id);
      try {
        if (!task.paperID) {
          task.state = "uploading"; task.message = "正在上传 PDF"; await this.save();
          const p = await this.client.upload(source.bytes, source.filename, `SuperTranslate-${this.host.uuid()}`);
          task.paperID = p.id; await this.save();
        }
        const p = await this.client.paper(task.paperID);
        task.paperID = p.id;
        if (p.translation_status === "pending" || p.translation_status === "failed") {
          task.state = "starting"; task.message = "正在提交翻译"; await this.save();
          try { await this.client.start(task.paperID, task); }
          catch (e) { task.state = "start_unknown"; task.message = "提交结果待确认。请刷新状态，避免重复提交。"; await this.save(); throw e; }
          task.state = "translating"; task.message = "已提交，正在排队";
        } else this.apply(task, p);
        await this.save();
        if (task.state === "completed") await this.importResult(task);
      } catch (e) {
        if (task.state !== "start_unknown") task.state = task.paperID ? "sync_error" : "interrupted";
        task.message = e.message; await this.save();
      } finally { this.busy.delete(task.id); }
    }
    apply(task, p) {
      if (!["pending", "translating", "completed", "failed", "repairing", "repair_pending"].includes(p.translation_status)) throw new ClientError("未知的服务状态", "PROTOCOL");
      task.paperID = p.id; task.state = p.translation_status;
      task.stage = String(p.translation_stage || "").slice(0, 600);
      const job = p.latest_job?.paper_id === p.id ? p.latest_job : null;
      if (task.state === "repairing" && (job?.status === "repair_pending" ||
          (!job && task.stage.startsWith("修复已暂停")))) task.state = "repair_pending";
      task.attempts = Number.isSafeInteger(job?.attempt_count) && job.attempt_count >= 0 ? job.attempt_count : null;
      task.maxAttempts = Number.isSafeInteger(job?.max_attempts) && job.max_attempts > 0 ? job.max_attempts : null;
      task.hasQAReport = p.has_qa_report === true;
      task.progress = Math.max(0, Math.min(1, Number(p.translation_progress) || 0));
      task.message = (p.translation_error || p.translation_stage || labels[task.state] || "").slice(0, 600);
      task.available = task.variant === "dual" ? p.has_dual : p.has_translated;
      task.updatedAt = new Date().toISOString();
    }
    async refresh() {
      for (const task of this.tasks) {
        if (this.closed) break;
        if (!task.paperID || this.busy.has(task.id) || task.state === "saved") continue;
        this.busy.add(task.id);
        try {
          const p = await this.client.paper(task.paperID);
          const uncertain = task.state === "start_unknown";
          this.apply(task, p);
          if (uncertain && p.translation_status === "pending") { task.state = "start_unknown"; task.message = "服务尚未启动此任务，可点击重试提交。"; }
          if (task.state === "completed") await this.importResult(task);
        } catch (e) { task.message = e.message; /* Keep the durable state; never replay a POST here. */ }
        finally { this.busy.delete(task.id); }
      }
      await this.save();
    }
    async importResult(task) {
      if (this.closed) return;
      if (!task.available) { task.message = "翻译完成，但所选译文尚不可下载。请刷新或检查服务。"; return; }
      const imported = await this.host.findImported(task);
      if (imported) { task.resultID = imported; task.state = "saved"; task.message = "译文已归档"; await this.save(); return; }
      await this.host.checkImport(task);
      task.message = "正在保存译文附件"; await this.save();
      const bytes = await this.client.download(task.paperID, task.variant);
      if (this.closed) return;
      task.resultID = await this.host.importPDF(task, bytes);
      task.state = "saved"; task.progress = 1; task.message = "译文已归档"; await this.save();
    }
    async retry(task, source) {
      if (source.hash !== task.sourceHash) throw new ClientError("原 PDF 已变化，请重新发起翻译");
      if (["translating", "repairing", "repair_pending"].includes(task.state)) return this.refresh();
      return this.run(task, source);
    }
    shutdown() { this.closed = true; }
  }
  const labels = { queued: "排队中", uploading: "上传中", starting: "提交中", start_unknown: "待确认", pending: "等待翻译", translating: "翻译中", repairing: "系统修复中", repair_pending: "修复已暂停", completed: "等待归档", saved: "已完成", failed: "翻译失败", interrupted: "上传中断", sync_error: "等待重连" };
  return { PROVIDERS, ACTIVE, ClientError, origin, paperID, pdfBytes, safeName, multipart, ZoteroServiceClient, ZoteroTranslationController, labels };
})();
if (typeof module !== "undefined") module.exports = SuperTranslateCore;
