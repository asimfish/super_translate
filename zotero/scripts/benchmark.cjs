// Read-only controller benchmark; simulated latency, no translation jobs or files.
const C=require(process.argv[2] || '../content/core.js'),{performance}=require('node:perf_hooks');
(async()=>{let result=[];for(let run=0;run<8;run++){let active=0,max=0,writes=0;
const tasks=Array.from({length:20},(_,i)=>({id:String(i),namespace:'bench',paperID:i.toString(16).padStart(12,'0'),state:'translating',variant:'translated'}));
const c=await new C.ZoteroTranslationController({namespace:'bench',store:{load:async()=>tasks,save:async()=>{writes++;}},host:{},client:{paper:async id=>{active++;max=Math.max(max,active);await new Promise(r=>setTimeout(r,40));active--;return {id,translation_status:'translating',translation_progress:.5,translation_stage:'翻译中'};}}}).init();
const start=performance.now();await c.refresh();result.push({ms:Math.round((performance.now()-start)*10)/10,max,writes});}
const values=result.slice(1).map(x=>x.ms).sort((a,b)=>a-b),mean=values.reduce((a,b)=>a+b)/values.length;
console.log(JSON.stringify({workload:'20 distinct translating papers, 40ms mock service latency, 1 warmup + 7 runs',runs:result,median_ms:values[3],stdev_ms:Math.sqrt(values.reduce((s,x)=>s+(x-mean)**2,0)/values.length)},null,2));})();
