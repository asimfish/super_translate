const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const C = require('../content/core.js');

function adapter() {
  const imported = [], writes = [];
  const context = { pluginID: 'test', rootURI: 'file:///test/', SuperTranslateCore: C,
    PathUtils: path.posix,
    IOUtils: { makeDirectory: async () => {}, write: async (p, b) => writes.push({ path: p, bytes: b }),
      writeJSON: async () => {}, remove: async () => {} },
    Zotero: { Profile: { dir: '/profile' }, getTempDirectory: () => ({ path: '/tmp/zotero' }),
      Attachments: { importFromFile: async options => { imported.push(options); return { id: 42, addTag() {}, async saveTx() {} }; } } },
    Services: {} };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../content/plugin.js'), 'utf8'), context);
  const app = context.SuperTranslateZotero;
  app.uuid = () => 'random'; app.hash = () => 'digest'; app.marker = () => 'supertranslate:test';
  app.checkImport = async () => ({ libraryID: 1, parentItemID: 7 });
  return { app, imported, writes };
}

test('native submission saves Chinese even when an older profile preferred bilingual output', async () => {
  const { app } = adapter(); const submitted = [];
  app.pref = (key, fallback) => key === 'variant' ? 'dual' : fallback;
  app.source = async () => ({ id: 1, key: '1:SOURCE', title: 'A paper' });
  app.refreshPanels = () => {};
  app.controller = { client: { token: 'test' }, submit: async (source, options) => submitted.push(options) };
  await app.translateItems([{ getField: () => 'A paper' }]);
  assert.equal(submitted.length, 1); assert.equal(submitted[0].variant, 'translated');
});

test('Chinese imports use the requested title, safe PDF filename and original parent', async () => {
  const { app, imported, writes } = adapter();
  const bytes = new Uint8Array([37, 80, 68, 70]);
  await app.importPDF({ title: 'A paper: a/b', variant: 'translated' }, bytes);
  assert.equal(imported[0].title, '中文版《A paper: a/b》');
  assert.equal(path.posix.basename(imported[0].file), '中文版《A paper_ a_b》.pdf');
  assert.equal(imported[0].parentItemID, 7); assert.equal(writes[0].bytes, bytes);
});
