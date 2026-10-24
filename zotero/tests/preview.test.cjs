const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const C = require('../content/core.js');

function nativeAdapter() {
  const writes = [], opened = [], alerts = [];
  const task = { paperID: '012345abcdef', title: '../../paper', variant: 'dual', state: 'repair_pending', available: true };
  const context = { pluginID: 'test', rootURI: 'file:///test/', SuperTranslateCore: C,
    PathUtils: path.posix,
    IOUtils: { makeDirectory: async (p, o) => writes.push({ directory: p, ...o }), write: async (p, b, o) => writes.push({ path: p, ...o }) },
    Zotero: { getTempDirectory: () => ({ path: '/tmp/zotero' }), launchFile: p => opened.push(p), getMainWindow: () => ({}) },
    Services: { prompt: { alert: (_, title, text) => alerts.push({ title, text }) } } };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../content/plugin.js'), 'utf8'), context);
  const app = context.SuperTranslateZotero;
  app.uuid = () => 'random';
  app.controller = { tasks: [task], client: { paper: async () => ({}), download: async () => new Uint8Array([1]) },
    apply: () => {}, save: async () => {} };
  return { app, task, writes, opened, alerts };
}

test('draft preview writes a private labelled temporary PDF, without importing or completing its task', async () => {
  const f = nativeAdapter();
  const output = await f.app.openDraft(f.task);
  assert.ok(output.startsWith('/tmp/zotero/supertranslate-preview-random/待修复草稿 - '));
  assert.equal(path.posix.dirname(output), '/tmp/zotero/supertranslate-preview-random');
  assert.equal(f.writes[0].permissions, 0o700); assert.equal(f.writes[1].permissions, 0o600);
  assert.deepEqual(f.opened, [output]);
  assert.equal(f.task.state, 'repair_pending'); assert.equal(f.task.resultID, undefined);
});
test('an account switch during a draft download prevents local disclosure', async () => {
  const f = nativeAdapter();
  f.app.controller.client.download = async () => { f.app.controller = {}; return new Uint8Array([1]); };
  await assert.rejects(f.app.openDraft(f.task), /账号已切换/);
  assert.deepEqual(f.writes, []); assert.deepEqual(f.opened, []);
});
test('QA report uses literal bounded text and rejects a task from another account', async () => {
  const f = nativeAdapter();
  f.app.controller.client.qaReport = async () => ({ status: 'failed', error_count: 1, issues: [
    { page: 8, severity: 'error', message: '<script>untrusted</script>' } ] });
  await f.app.showQA(f.task);
  assert.match(f.alerts[0].text, /质检尚未通过/);
  assert.match(f.alerts[0].text, /第 8 页/);
  assert.ok(f.alerts[0].text.includes('<script>untrusted</script>'));
  await assert.rejects(f.app.showQA({ ...f.task }), /账号或任务已变化/);
});

test('completed reports with only warnings show passed with the warning count', async () => {
  const f = nativeAdapter();
  f.app.controller.client.qaReport = async () => ({ status: 'warning', error_count: 0, warning_count: 1,
    issues: [{ page: 6, severity: 'warning', message: 'table-like text grid' }] });
  await f.app.showQA(f.task);
  assert.match(f.alerts[0].text, /质检通过 · 0 个错误，1 个提示/);
  assert.match(f.alerts[0].text, /第 6 页 · 提示/);
});

test('warning status with errors or unknown QA status never shows passed', async () => {
  for (const report of [
    { status: 'warning', error_count: 1, issues: [{ severity: 'error', page: 8 }] },
    { status: 'pending', error_count: 0, issues: [] },
  ]) {
    const f = nativeAdapter(); f.app.controller.client.qaReport = async () => report;
    await f.app.showQA(f.task); assert.match(f.alerts[0].text, /质检尚未通过/);
  }
});
