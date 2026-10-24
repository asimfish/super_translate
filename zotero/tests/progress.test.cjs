const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm'),fs=require('node:fs');
const C=require('../content/core.js');
class Element {
  constructor(tag,doc){this.localName=tag;this.ownerDocument=doc;this.children=[];this.attributes={};this.dataset={};this.style={};this.className='';this.isConnected=true;this._text='';}
  get firstChild(){return this.children[0]||null;}
  set textContent(value){this._text=String(value??'');this.children=[];}
  get textContent(){return this._text+this.children.map(n=>n.textContent).join('');}
  append(...nodes){for(const n of nodes){n.parentNode=this;this.children.push(n);}}
  replaceChildren(...nodes){this._text='';this.children=[];this.append(...nodes);}
  replaceWith(node){const p=this.parentNode;p.children[p.children.indexOf(this)]=node;node.parentNode=p;}
  setAttribute(k,v){this.attributes[k]=String(v);}
  getAttribute(k){return this.attributes[k]??null;}
  addEventListener(){}
  contains(n){return this===n||this.children.some(c=>c.contains(n));}
  querySelectorAll(selector){const out=[];const match=n=>selector[0]==='.'?n.className.split(' ').includes(selector.slice(1)):selector==='[role="progressbar"]'?n.getAttribute('role')==='progressbar':n.localName===selector;for(const c of this.children){if(match(c))out.push(c);out.push(...c.querySelectorAll(selector));}return out;}
  querySelector(s){return this.querySelectorAll(s)[0]||null;}
  closest(){return null;}
  scrollIntoView(){this.scrolled=true;}
}
function setup(){
 const context={pluginID:'test',rootURI:'file:///test/',SuperTranslateCore:C,Services:{},Zotero:{}};
 vm.createContext(context);vm.runInContext(fs.readFileSync(require.resolve('../content/plugin.js'),'utf8'),context);
 const doc={activeElement:null,createElementNS:(_,tag)=>new Element(tag,doc)};
 const app=context.SuperTranslateZotero;
 return {app,doc};
}
const task=(extras={})=>({id:'task',title:'Paper',attachmentID:744,variant:'translated',state:'translating',progress:.895,stage:'译后检查 1/1',message:'译后检查 1/1',...extras});
test('task card exposes actual percentage, stage and accessible progress',()=>{
 const {app,doc}=setup();const card=app.taskCard(doc,task());
 assert.match(card.textContent,/89\.5%/);assert.match(card.textContent,/译后检查 1\/1/);
 const meter=card.querySelector('[role="progressbar"]');assert.ok(meter);assert.equal(meter.getAttribute('aria-valuenow'),'89.5');
 assert.equal(meter.querySelector('.st-progress-fill').style.width,'89.5%');
});
test('unknown upload progress, zero percent, paused and completed have distinct truthful states',()=>{
 const {app,doc}=setup();
 const uploading=app.taskCard(doc,task({state:'uploading',progress:0,stage:'',message:'正在上传 PDF'}));
 assert.equal(uploading.querySelector('[role="progressbar"]').getAttribute('aria-valuenow'),null);assert.match(uploading.textContent,/上传中/);
 const zero=app.taskCard(doc,task({progress:0}));assert.equal(zero.querySelector('[role="progressbar"]').getAttribute('aria-valuenow'),'0');
 const paused=app.taskCard(doc,task({state:'repair_pending',progress:.99,stage:'修复已暂停',message:'质检未通过'}));
 assert.match(paused.textContent,/99%/);assert.match(paused.textContent,/修复已暂停/);assert.ok(paused.querySelector('.st-progress-paused'));
 const saved=app.taskCard(doc,task({state:'saved',progress:1,resultID:738}));assert.match(saved.textContent,/100%/);
 for(const value of [NaN,Infinity,undefined]){const unknown=app.taskCard(doc,task({progress:value}));assert.ok(!/NaN|Infinity/.test(unknown.textContent));assert.equal(unknown.querySelector('[role="progressbar"]').getAttribute('aria-valuenow'),null);}
});
test('progress remains ahead of model settings and translation controls',()=>{
 const {app,doc}=setup();app.uuid=()=> 'u';app.selection={provider:'deepseek',model:'m',quality:'balanced'};
 app.controller={client:{token:'session'},tasks:[task()]};
 const body=new Element('section',doc),item={id:745,isRegularItem:()=>true,getAttachments:()=>[744]};
 app.renderPanel({body,item});const root=body.querySelector('.st-panel');
 assert.ok(root.children.indexOf(root.querySelector('.st-task-list'))<root.children.indexOf(root.querySelector('.st-model')));
});
test('focusing a model field does not freeze the task progress',()=>{
 const {app,doc}=setup(),t=task({progress:.375});
 app.uuid=()=> 'u';app.selection={provider:'deepseek',model:'m',quality:'balanced'};app.controller={client:{token:'session'},tasks:[t]};
 const body=new Element('section',doc),item={id:745,isRegularItem:()=>true,getAttachments:()=>[744]};
 app.renderPanel({body,item});const input=body.querySelector('input');doc.activeElement=input;
 t.progress=.895;t.stage='译后检查';t.message=t.stage;app.refreshPanels();
 assert.equal(body.querySelector('input'),input,'model editor must retain focus and draft');
 assert.match(body.querySelector('.st-task-list').textContent,/89\.5%/);
});
test('reader badge follows current paper progress and paused state',()=>{
 const {app,doc}=setup();const wrapper=new Element('span',doc);wrapper.dataset.stReaderItemID='744';
 const button=new Element('button',doc),status=new Element('span',doc);status.className='st-reader-progress';wrapper.append(button,status);
 app.readerButtons.add(wrapper);const t=task({progress:.375});app.controller={tasks:[t]};
 app.refreshReaderProgress();assert.match(status.textContent,/37\.5%/);
 t.progress=.895;t.stage='译后检查';app.refreshReaderProgress();assert.match(status.textContent,/89\.5%/);assert.match(button.title,/译后检查/);
 t.state='repair_pending';t.stage='修复已暂停';app.refreshReaderProgress();assert.match(button.title,/修复已暂停/);assert.match(status.textContent,/暂停/);
 app.controller={tasks:[]};app.refreshReaderProgress();assert.equal(status.hidden,true);assert.equal(status.textContent,'');assert.equal(button.title,'SuperTranslate · 翻译整篇论文');
});
test('a reader control can be registered before the toolbar attaches it',()=>{
 const {app,doc}=setup(),control=new Element('span',doc),button=new Element('button',doc),status=new Element('span',doc);
 control.dataset.stReaderItemID='744';status.className='st-reader-progress';control.append(button,status);control.isConnected=false;
 app.readerButtons.add(control);app.controller={tasks:[task()]};app.refreshReaderProgress();
 assert.equal(app.readerButtons.size,1);assert.match(status.textContent,/89\.5%/);
 control.isConnected=true;app.refreshReaderProgress();control.isConnected=false;app.refreshReaderProgress();assert.equal(app.readerButtons.size,0);
});
