const { test } = require('node:test');
const assert = require('node:assert/strict');
const C = require('../content/core.js');
const PDF = new TextEncoder().encode('%PDF-1.7\nexample fixture\n%%EOF');
const ID = '012345abcdef';

test('provider API identifiers match the companion backend contract, including GLM', async () => {
  const fs = require('node:fs'), path = require('node:path');
  const source = fs.readFileSync(path.resolve(__dirname, '../../app/core/provider_credentials.py'), 'utf8');
  const backendIDs = [...source.matchAll(/"([a-z]+)": ProviderSpec\(/g)].map(m => m[1]);
  assert.ok(backendIDs.length >= 5);
  const calls = [];
  const client = new C.ZoteroServiceClient({ baseURL: 'https://test.example', transport: async r => { calls.push(r); return { status: 200, data: { ok: true } }; } });
  for (const provider of backendIDs) {
    assert.ok(C.PROVIDERS.includes(provider), provider);
    await client.start(ID, { provider, quality: 'balanced' });
    assert.ok(calls.at(-1).url.endsWith('backend=' + provider));
  }
  await client.saveModel('glm', 'glm-test');
  assert.equal(calls.at(-1).url, 'https://test.example/api/provider-credentials/glm');
});

test('origin validation prevents credential-bearing, remote plaintext and path URLs', () => {
  for (const url of ['file:///etc/passwd', 'http://example.com', 'https://u:p@host', 'https://host/api', 'https://host/?token=x', 'https://host/#x']) assert.throws(() => C.origin(url));
  for (const url of ['https://example.com', 'http://127.0.0.1:18001', 'http://[::1]:18001']) assert.equal(C.origin(url), url);
});
test('multipart keeps binary bytes and sanitizes filename header injection', () => {
  const body = C.multipart(PDF, 'evil"\r\nX-Token: data.pdf', 'boundary');
  const text = new TextDecoder().decode(body);
  assert.ok(text.includes('evil___X-Token_ data.pdf')); assert.equal(text.split('Content-Disposition:').length, 2);
  assert.ok(text.endsWith('\r\n--boundary--\r\n')); assert.ok(text.includes(new TextDecoder().decode(PDF)));
});
test('long Unicode titles fit filesystem components and long upload names retain PDF extension', () => {
  const title = '化学研究🧬'.repeat(60);
  const safe = C.safeName(title);
  assert.ok(new TextEncoder().encode(safe + ' - 中英对照.pdf').length < 255);
  assert.equal(safe.includes('\uFFFD'), false);
  const body = new TextDecoder().decode(C.multipart(PDF, 'x'.repeat(240) + '.pdf', 'boundary'));
  assert.match(body, /filename="x{100}\.pdf"/);
});
test('HTML or truncated downloads cannot enter library', async () => {
  for (const bytes of [new TextEncoder().encode('<html>sign in</html>'), PDF.slice(0, -5), new Uint8Array(0)]) assert.throws(() => C.pdfBytes(bytes));
  assert.equal(C.pdfBytes(PDF), PDF);
});
test('native file bytes from another JS realm are valid, other typed arrays are rejected', () => {
  const vm = require('node:vm');
  const bytes = vm.runInNewContext('new Uint8Array(' + JSON.stringify([...PDF]) + ')');
  assert.equal(bytes instanceof Uint8Array, false);
  assert.equal(C.pdfBytes(bytes), bytes);
  assert.throws(() => C.pdfBytes(new Uint16Array(PDF)));
  assert.throws(() => C.pdfBytes({ length: PDF.length, [Symbol.toStringTag]: 'Uint8Array' }));
});
test('API never accepts arbitrary IDs or output variants', async () => {
  const client = new C.ZoteroServiceClient({ baseURL: 'https://test.example', transport: () => { throw Error('must not be called'); } });
  await assert.rejects(client.paper('../users')); await assert.rejects(client.download(ID, '../../login'));
});
test('auth errors are actionable and do not expose response bodies or tokens', async () => {
  const client = new C.ZoteroServiceClient({ baseURL: 'https://test.example', token: 'secret', transport: async r => {
    assert.equal(r.headers.Authorization, 'Bearer secret'); return { status: 401, data: 'secret' };
  } });
  await assert.rejects(client.connect(), e => e.code === 'HTTP_401' && !e.message.includes('secret'));
});
test('canonical paper aliases are bounded and validated', async () => {
  const client = new C.ZoteroServiceClient({ baseURL: 'https://test.example', transport: async () => ({ status: 200, data: { id: ID, canonical_paper_id: ID } }) });
  assert.equal((await client.paper(ID)).id, ID);
  client.transport = async () => ({ status: 200, data: { id: ID, canonical_paper_id: 'bad' } });
  await assert.rejects(client.paper(ID));
});
function fixture({ failStart = false, restored = [] } = {}) {
  let state = 'pending', imports = 0, starts = 0, uploads = 0, saved = restored, result = null, sequence = 0;
  const p = () => ({ id: ID, translation_status: state, translation_progress: state === 'completed' ? 1 : .5, has_dual: state === 'completed' });
  const client = { upload: async () => { uploads++; return p(); }, paper: async () => p(), start: async () => { starts++; if (failStart) throw new C.ClientError('lost', 'NETWORK'); state = 'translating'; }, download: async () => PDF };
  const store = { load: async () => saved, save: async tasks => { saved = structuredClone(tasks); } };
  const host = { uuid: () => String(++sequence), checkImport: async () => {}, findImported: async () => result, importPDF: async () => { imports++; return result = 77; } };
  const make = () => new C.ZoteroTranslationController({ client, store, host, namespace: 'account-a' });
  return { make, state: s => state = s, source: { id: 1, key: '1:ABCD', hash: 'hash-a', title: 'A paper', filename: 'a.pdf', bytes: PDF }, options: { provider: 'deepseek', quality: 'balanced', variant: 'dual' }, counts: () => ({ imports, starts, uploads }), saved: () => saved, host };
}
test('selected PDF uploads once, restart polls without retranslation, result imports exactly once', async () => {
  const f = fixture(); let c = await f.make().init();
  const t = await c.submit(f.source, f.options); await c.submit(f.source, f.options);
  assert.equal(t.state, 'translating'); assert.deepEqual(f.counts(), { uploads: 1, starts: 1, imports: 0 });
  c.shutdown(); c = await f.make().init(); f.state('completed'); await c.refresh(); await c.refresh();
  assert.equal(c.tasks[0].state, 'saved'); assert.equal(c.tasks[0].resultID, 77);
  assert.deepEqual(f.counts(), { uploads: 1, starts: 1, imports: 1 });
});
test('ambiguous POST is never retried by polling or restart', async () => {
  const f = fixture({ failStart: true }); let c = await f.make().init();
  await c.submit(f.source, f.options); assert.equal(c.tasks[0].state, 'start_unknown');
  c.shutdown(); c = await f.make().init(); await c.refresh(); await c.refresh();
  assert.equal(c.tasks[0].state, 'start_unknown'); assert.equal(f.counts().starts, 1);
});
test('repair and failed QA states stay visible and never import incomplete outputs', async () => {
  const f = fixture(); const c = await f.make().init(); await c.submit(f.source, f.options);
  for (const state of ['repairing', 'repair_pending', 'failed']) { f.state(state); await c.refresh(); assert.equal(c.tasks[0].state, state); }
  assert.equal(f.counts().imports, 0); assert.equal(f.counts().starts, 1);
});
test('different PDF content and variants get distinct tasks; accounts remain isolated', async () => {
  const f = fixture(); const c = await f.make().init(); await c.submit(f.source, f.options);
  await c.submit({ ...f.source, hash: 'new-content' }, f.options);
  await c.submit(f.source, { ...f.options, variant: 'translated' });
  assert.equal(c.tasks.length, 3);
  const restored = [...f.saved(), { namespace: 'someone-else', id: 'hidden' }];
  assert.equal((await fixture({ restored }).make().init()).tasks.length, 3);
});
test('no import into read-only/deleted source; existing imported receipt recovers crash', async () => {
  const f = fixture(); const c = await f.make().init(); await c.submit(f.source, f.options);
  f.state('completed'); f.host.checkImport = async () => { throw Error('read-only'); };
  await c.refresh(); assert.equal(f.counts().imports, 0); assert.equal(c.tasks[0].message, 'read-only');
  f.host.findImported = async () => 99; await c.refresh(); assert.equal(c.tasks[0].resultID, 99); assert.equal(f.counts().imports, 0);
});
test('changed source cannot be submitted as a retry of an old task', async () => {
  const f = fixture(); const c = await f.make().init(); const t = await c.submit(f.source, f.options);
  await assert.rejects(c.retry(t, { ...f.source, hash: 'changed' }));
});
