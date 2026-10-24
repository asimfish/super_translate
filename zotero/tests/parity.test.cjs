const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm'), fs=require('node:fs');
const C=require('../content/core.js');
const ID='012345abcdef';
const gate=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
function controller(tasks,client){let writes=0,changes=0;const c=new C.ZoteroTranslationController({namespace:'a',client,host:{},store:{load:async()=>tasks,save:async()=>{writes++;}},changed:()=>changes++});return {c,counts:()=>({writes,changes})};}
function adapter(){
 const prefs=new Map(); const context={pluginID:'test',rootURI:'',SuperTranslateCore:C,Services:{prefs:{getStringPref:(k,f)=>prefs.get(k)??f,setStringPref:(k,v)=>prefs.set(k,v)}}};
 vm.createContext(context);vm.runInContext(fs.readFileSync(require.resolve('../content/plugin.js'),'utf8'),context);
 const app=context.SuperTranslateZotero; app.refreshPanels=()=>{};return app;
}
test('all 20 papers synchronize with bounded concurrency and concurrent refresh clicks coalesce',async()=>{
 const tasks=Array.from({length:20},(_,i)=>({id:''+i,namespace:'a',paperID:i.toString(16).padStart(12,'0'),state:'translating',variant:'translated'}));
 let active=0,maximum=0,calls=0;const first=gate(),release=gate();
 const f=controller(tasks,{paper:async id=>{calls++;active++;maximum=Math.max(maximum,active);if(active===4)first.resolve();await release.promise;active--;return {id,translation_status:'translating',translation_progress:.5};}});
 await f.c.init();const p=f.c.refresh(),same=f.c.refresh();assert.equal(p,same);await first.promise;assert.equal(calls,4);release.resolve();await p;
 assert.equal(maximum,4);assert.equal(calls,20);assert.ok(f.c.tasks.every(t=>t.state==='translating'&&t.progress===.5));
 const before=f.counts();await f.c.refresh();assert.deepEqual(f.counts(),before,'unchanged polls must not rewrite disk or repaint');
});
test('two attachment tasks sharing one remote paper fetch it once per refresh',async()=>{
 let calls=0;const tasks=[1,2].map(id=>({id,namespace:'a',paperID:ID,state:'translating'}));
 const f=controller(tasks,{paper:async id=>{calls++;return {id,translation_status:'translating'};}});await f.c.init();await f.c.refresh();assert.equal(calls,1);
});
test('shutdown during upload never starts a translation on the old account',async()=>{
 const uploaded=gate(),ready=gate();let starts=0;
 const f=controller([],{upload:async()=>{ready.resolve();await uploaded.promise;return {id:ID};},paper:async()=>({id:ID,translation_status:'pending'}),start:async()=>starts++});f.c.host.uuid=()=>ID;
 await f.c.init();const p=f.c.submit({id:1,key:'1:A',hash:'h',bytes:new Uint8Array()}, {provider:'deepseek',quality:'balanced',variant:'translated'});
 await ready.promise;f.c.shutdown();uploaded.resolve();await p;assert.equal(starts,0);assert.equal(f.c.tasks[0].paperID,ID);
});
test('cancel waits for acknowledgement, survives restart, and retry is explicit',async()=>{
 let state='translating',cancels=0,starts=0;
 const task={id:'t',namespace:'a',paperID:ID,sourceHash:'h',state:'translating',provider:'deepseek',quality:'balanced'};
 const client={paper:async()=>({id:ID,translation_status:state}),cancel:async()=>{cancels++;},start:async()=>{starts++;state='translating';}};
 const f=controller([task],client);await f.c.init();await f.c.cancel(task);assert.equal(task.state,'translating');assert.equal(task.cancelRequested,true);
 state='failed';await f.c.refresh();assert.equal(task.state,'cancelled');assert.equal(starts,0);assert.equal(cancels,1);
 await f.c.retry(task,{hash:'h'});assert.equal(task.state,'translating');assert.equal(starts,1);assert.equal(task.cancelRequested,undefined);
});
test('cancel network failure does not falsely report cancellation',async()=>{
 const task={id:'t',namespace:'a',paperID:ID,state:'translating'};const f=controller([task],{cancel:async()=>{throw Error('offline');}});await f.c.init();
 await assert.rejects(f.c.cancel(task),/offline/);assert.equal(task.cancelRequested,undefined);assert.equal(task.state,'translating');
});
test('model refresh and library use floating client endpoints, search is encoded, default provider omits backend',async()=>{
 const calls=[];const client=new C.ZoteroServiceClient({baseURL:'https://service.example',transport:async r=>{calls.push(r);return {status:200,data:r.url.includes('/api/papers/?')?{papers:[{id:ID}],total:1}:{ok:true}};}});
 await client.refreshModels('deepseek');assert.match(calls.at(-1).url,/\/models\/deepseek\/refresh$/);assert.equal(calls.at(-1).method,'POST');
 await client.listPapers('a&status=failed','recent',50);assert.match(calls.at(-1).url,/search=a%26status%3Dfailed&sort=recent&offset=50/);
 await client.libraryHistory(ID);assert.match(calls.at(-1).url,/library-history$/);
 await client.organizeLibrary();assert.equal(calls.at(-1).method,'POST');
 await client.cancel(ID);assert.match(calls.at(-1).url,/\/cancel$/);
 await client.start(ID,{provider:'',quality:'balanced'});assert.ok(!calls.at(-1).url.includes('backend='));
 await client.start(ID,{provider:'deepseek',quality:'fast'});assert.ok(!calls.at(-1).url.includes('backend='));
 assert.throws(()=>client.refreshModels('../users'));await assert.rejects(client.listPapers('', 'invalid',0));
});
test('unsaved native model blocks upload; preferences remain scoped to account',async()=>{
 const app=adapter();let uploads=0;
 app.controller={namespace:'a',client:{token:'t'},submit:async()=>uploads++};app.source=async()=>({id:1,key:'1:A'});app.checkImport=async()=>{};
 app.catalogs=[{provider:'deepseek',selected_model:'deepseek-v4-pro'}];app.selection={provider:'deepseek',model:'deepseek-v4-flash',quality:'balanced'};
 await assert.rejects(app.translateItems([{getField:()=> 'Paper'}]),/尚未应用/);assert.equal(uploads,0);
 let saved;app.controller.client.saveModel=async(p,m)=>saved={p,m};app.loadModels=async()=>{app.catalogs[0].selected_model='deepseek-v4-flash';};
 await app.applySelection();assert.deepEqual(saved,{p:'deepseek',m:'deepseek-v4-flash'});assert.equal(app.selectionDirty(),false);
 app.setAccountPref('provider','qwen');app.controller={namespace:'b'};assert.equal(app.accountPref('provider','deepseek'),'deepseek');
});
test('model response from a previous account never populates the current account',async()=>{
 const app=adapter(),waiting=gate();
 app.controller={namespace:'a',client:{models:async()=>{await waiting.promise;return [{provider:'openai',selected_model:'private'}];},credentials:async()=>[]}};
 const pending=app.loadModels();app.controller={namespace:'b'};waiting.resolve();await pending;assert.equal(app.catalogs.length,0);
});
test('native 20-item batch submits at most two at a time and preserves Chinese-only options',async()=>{
 const app=adapter(),first=gate(),release=gate();let active=0,max=0,count=0;
 app.controller={namespace:'a',client:{token:'t'},submit:async(s,o)=>{count++;active++;max=Math.max(max,active);if(count===2)first.resolve();assert.equal(o.variant,'translated');await release.promise;active--;}};
 app.source=async i=>({id:i.id});app.checkImport=async()=>{};
 const batch=app.translateItems(Array.from({length:20},(_,id)=>({id,getField:()=>''})));await first.promise;assert.equal(count,2);release.resolve();await batch;
 assert.equal(count,20);assert.equal(max,2);assert.equal(app.running,false);
});
test('published floating protocol fixture matches every native operation and translation options',async()=>{
 const contract=require('./fixtures/floating-client-contract.json');assert.deepEqual(C.PROVIDERS,contract.providers);
 const calls=[];const client=new C.ZoteroServiceClient({baseURL:'https://example.test',token:'session',transport:async r=>{calls.push(r);return {status:200,data:{ok:true}};}});
 for(const [operation,args,method,path] of contract.operations){await client[operation](...args);const req=calls.at(-1);assert.equal(req.method,method,operation);assert.equal(new URL(req.url).pathname,path,operation);assert.equal(req.headers.Authorization,'Bearer session');}
 await client.start(ID,{provider:'deepseek',quality:'balanced'});assert.deepEqual(Object.fromEntries(new URL(calls.at(-1).url).searchParams),contract.translation_query);
});
test('queued catalogue refresh does not run for a newly switched account',async()=>{
 const app=adapter(),release=gate();let refreshes=0;
 const old={namespace:'a',client:{models:async()=>{await release.promise;return [];},credentials:async()=>[],refreshModels:async()=>refreshes++}};
 app.controller=old;const first=app.loadModels(),second=app.loadModels('deepseek');app.controller={namespace:'b'};release.resolve();await Promise.all([first,second]);assert.equal(refreshes,0);
});
test('a cancel click while polling waits for that poll and still reaches the service',async()=>{
 const started=gate(),release=gate();let cancels=0,first=true;
 const task={id:'t',namespace:'a',paperID:ID,state:'translating'};
 const f=controller([task],{paper:async()=>{if(first){first=false;started.resolve();await release.promise;}return {id:ID,translation_status:'translating'};},cancel:async()=>cancels++});
 await f.c.init();const poll=f.c.refresh();await started.promise;const cancel=f.c.cancel(task);release.resolve();await Promise.all([poll,cancel]);assert.equal(cancels,1);assert.equal(task.cancelRequested,true);
});
