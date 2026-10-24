const { test } = require('node:test');
const assert = require('node:assert/strict');
const C = require('../content/core.js');
const ID = '012345abcdef';
const paper = { id: ID, translation_status: 'repairing', translation_progress: .99,
  translation_stage: '修复已暂停，等待引擎更新后自动重试', translation_error: '译后检查未通过：5 个错误',
  has_dual: true, has_translated: true, has_qa_report: true };
const job = { paper_id: ID, status: 'repair_pending', attempt_count: 3, max_attempts: 3 };

function setup({ jobs = [job], jobStatus = 200 } = {}) {
  const requests = [], imports = [];
  const client = new C.ZoteroServiceClient({ baseURL: 'https://test.example', token: 'test-token', transport: async r => {
    requests.push(r);
    assert.equal(r.headers.Authorization, 'Bearer test-token');
    return r.url.endsWith('/jobs') ? { status: jobStatus, data: jobs } : { status: 200, data: paper };
  } });
  const task = { id: 'task', namespace: 'account', paperID: ID, state: 'repairing', variant: 'dual' };
  const controller = new C.ZoteroTranslationController({ client, namespace: 'account',
    store: { load: async () => [task], save: async () => {} },
    host: { findImported: async () => { imports.push('called'); } } });
  return { client, task, controller, requests, imports };
}

test('captured repairing paper with an exhausted job displays paused repair, retains stage and draft', async () => {
  const f = setup(); await f.controller.init(); await f.controller.refresh();
  assert.equal(f.task.state, 'repair_pending');
  assert.equal(f.task.stage, paper.translation_stage);
  assert.equal(f.task.message, paper.translation_error);
  assert.equal(f.task.attempts, 3); assert.equal(f.task.maxAttempts, 3);
  assert.equal(f.task.available, true); assert.equal(f.task.hasQAReport, true);
  assert.deepEqual(f.imports, []);
  assert.ok(f.requests.every(r => r.method === 'GET'));
  assert.match(C.labels.repair_pending, /暂停/);
});
test('older service without jobs still shows its paused stage and never imports failed QA', async () => {
  const f = setup({ jobStatus: 404 }); await f.controller.init(); await f.controller.refresh();
  assert.equal(f.task.state, 'repair_pending'); assert.equal(f.task.stage, paper.translation_stage);
  assert.deepEqual(f.imports, []);
});
test('job from another paper cannot supply repair attempt metadata', async () => {
  const f = setup({ jobs: [{ ...job, paper_id: 'abcdef012345' }] });
  await f.controller.init(); await f.controller.refresh();
  assert.equal(f.task.attempts, null);
});
test('a resumed job is not labelled paused and clears previous attempt metadata on completion', () => {
  const f = setup();
  f.controller.apply(f.task, { ...paper, latest_job: job });
  f.controller.apply(f.task, { ...paper, translation_stage: '重新生成译文', latest_job: { ...job, status: 'running' } });
  assert.equal(f.task.state, 'repairing');
  f.controller.apply(f.task, { ...paper, translation_status: 'completed', translation_error: '', translation_stage: '完成', has_qa_report: true });
  assert.equal(f.task.state, 'completed'); assert.equal(f.task.attempts, null);
});
test('QA details use authenticated bounded GETs and validate object IDs', async () => {
  const f = setup();
  await f.client.qaReport(ID);
  assert.equal(f.requests.at(-1).url, 'https://test.example/api/papers/' + ID + '/qa-report');
  await assert.rejects(async () => f.client.qaReport('../other'));
});
