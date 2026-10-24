var chromeHandle;
var pluginScope;

async function startup({ id, version, rootURI }) {
  await Zotero.initializationPromise;
  chromeHandle = Cc["@mozilla.org/addons/addon-manager-startup;1"]
    .getService(Ci.amIAddonManagerStartup)
    .registerChrome(Services.io.newURI(rootURI + "manifest.json"), [
      ["content", "supertranslate", rootURI + "content/"],
    ]);
  pluginScope = { Zotero, Services, ChromeUtils, Cc, Ci, IOUtils, PathUtils,
    TextEncoder, TextDecoder, URL, rootURI, pluginID: id, pluginVersion: version };
  Services.scriptloader.loadSubScript(rootURI + "content/core.js", pluginScope);
  Services.scriptloader.loadSubScript(rootURI + "content/plugin.js", pluginScope);
  Zotero.SuperTranslate = pluginScope.SuperTranslateZotero;
  await Zotero.SuperTranslate.startup();
}
function onMainWindowLoad({ window }) { Zotero.SuperTranslate?.addWindow(window); }
function onMainWindowUnload({ window }) { Zotero.SuperTranslate?.removeWindow(window); }
function shutdown() {
  Zotero.SuperTranslate?.shutdown();
  delete Zotero.SuperTranslate;
  pluginScope = null;
  chromeHandle?.destruct();
  chromeHandle = null;
}
function install() {}
function uninstall() {}
