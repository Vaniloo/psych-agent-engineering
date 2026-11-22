const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path');
const sandbox={window:{},URL};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../html.js'),'utf8'),sandbox);
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../tool-activity.js'),'utf8'),sandbox);
const m=sandbox.window.PsychToolActivity.model();
// Evidence checks are progress steps, not fabricated tool calls.
assert.equal(m.observeKnowledge({stage:'plan',status:'running',round:1}),true);
assert.equal(m.observeKnowledge({stage:'plan',status:'ok',round:1}),true);
assert.equal(m.observeKnowledge({stage:'plan',status:'running',round:1}),false);
assert.equal(m.observeKnowledge({stage:'<script>',status:'ok',round:1}),false);
assert.equal(m.observeKnowledge({stage:'assess',status:'ok',round:3}),false);
assert.equal(m.rows().length,0);
assert.equal(m.knowledge().length,1);
assert.equal(m.update({callId:'x',name:'persist_turn',status:'running'}),false);
assert.equal(m.update({callId:'x',name:'__proto__',status:'running'}),false);
assert.equal(m.update({callId:'x',name:'memory_search',status:'fake'}),false);
m.update({callId:'first',name:'memory_search',status:'running'});
m.update({callId:'first',name:'memory_search',status:'ok',durationMs:90,summary:'找到一条原话'});
assert.equal(m.update({callId:'first',name:'memory_search',status:'running'}),false);
assert.equal(m.update({callId:'first',name:'get_dashboard',status:'ok'}),false);
assert.equal(m.rows().length,1);
m.reconcile({capabilityCalls:[{name:'risk_scan',status:'ok'},{name:'memory_search',status:'ok',durationMs:95}]});
assert.equal(m.rows().length,1);assert.equal(m.rows()[0].durationMs,95);
m.reconcile({capabilityCalls:[{name:'memory_search',status:'ok'},{name:'get_dashboard',status:'no_change'}]});
m.reconcile({capabilityCalls:[{name:'memory_search',status:'ok'},{name:'get_dashboard',status:'no_change'}]});
assert.equal(m.rows().length,2);assert.equal(m.rows()[1].status,'no_change');
m.update({callId:'another',name:'memory_search',status:'running'});m.settle();
assert.equal(m.rows()[2].status,'unconfirmed');assert.equal(m.rows()[0].status,'ok');
// Terminal metrics resolve an interrupted call without inventing a duplicate.
m.reconcile({capabilityCalls:[{name:'memory_search',status:'ok'},{name:'get_dashboard',status:'no_change'},{name:'memory_search',status:'error',durationMs:800}]});
assert.equal(m.rows().length,3);assert.equal(m.rows()[2].status,'error');
console.log('Tool activity: duplicate/out-of-order events, metrics reconciliation, system filtering and interrupted calls passed.');

const web=sandbox.window.PsychToolActivity.model();
web.update({callId:'web-1',name:'search_web',status:'ok',sources:[
  {title:'<b>Official</b>',url:'https://example.com/docs'},
  {title:'bad',url:'javascript:alert(1)'}]});
assert.equal(web.rows()[0].sources.length,1);
web.reconcile({capabilityCalls:[{name:'search_web',status:'ok',durationMs:200}]});
assert.equal(web.rows()[0].sources[0].url,'https://example.com/docs');
assert.equal(web.rows()[0].durationMs,200);

const progress=sandbox.window.PsychToolActivity.model();
assert.equal(progress.progress(),null);
assert.equal(progress.observeLoop({budget:8,calls:0}),true);
progress.observeLoop({calls:3});progress.observeLoop({budget:8,calls:1});
assert.equal(progress.progress().calls,3); // Delayed events cannot move the counter backwards.
assert.equal(progress.observeLoop({budget:8,calls:9}),false);
assert.equal(progress.observeLoop({budget:100,calls:4}),false);
class Element {
  constructor(){this.children=[];this.parts={};this.dataset={};this.classList={toggle(){}};this.innerHTML='';}
  append(...children){this.children.push(...children);} prepend(child){this.children.unshift(child);}
  querySelector(selector){return this.parts[selector]||(this.parts[selector]=new Element());}
}
sandbox.document={createElement:()=>new Element()};
const message=new Element(),activity=sandbox.window.PsychToolActivity.attach(message);
activity.observeLoop({budget:8,calls:0});
activity.update({callId:'web',name:'search_web',status:'ok',sources:[
  {title:'<img src=x onerror=alert(1)>',url:'https://example.com/reference'},
  {title:'unsafe',url:'javascript:alert(1)'}]});
activity.observeLoop({calls:1});
activity.reconcile({pipeline:{loop:{budget:8,readCalls:1}},capabilityCalls:[{name:'search_web',status:'ok'}]});
const group=message.children[0],heading=group.children[0],detail=group.children[1].children[0].querySelector('.activity-call-detail');
assert.match(heading.innerHTML,/读取 1 \/ 8/);
assert.match(detail.innerHTML,/网页来源|调用已完成/);
assert.match(detail.innerHTML,/&lt;img/);
assert.doesNotMatch(detail.innerHTML,/javascript:|<img/);
assert.match(detail.innerHTML,/noopener noreferrer/);
activity.update({callId:'web',name:'search_web',status:'ok',sources:[]});
assert.doesNotMatch(detail.innerHTML,/activity-web-sources/);
assert.equal(sandbox.window.PsychToolActivity.model().update({callId:'paused',name:'search_role_voice',status:'ok'}),false);
activity.observeKnowledge({stage:'assess',status:'ok',round:1,evidenceStatus:'partial',retained:2,retry:true,sources:[
  {title:'CDC',url:'https://www.cdc.gov/emotional-well-being/'},{title:'unsafe',url:'javascript:alert(1)'}]});
activity.observeKnowledge({stage:'retrieve',status:'running',round:2});
assert.match(heading.innerHTML,/正在核对资料/);
assert.match(group.children[2].innerHTML,/保留 2 条/);
assert.match(group.children[2].innerHTML,/将补充检索/);
assert.match(group.children[2].innerHTML,/资料来源/);
assert.match(group.children[2].innerHTML,/原文可免费访问/);
assert.doesNotMatch(group.children[2].innerHTML,/javascript:/);
assert.match(group.children[2].innerHTML,/补充检索资料/);
activity.reconcile({pipeline:{agenticRag:{enabled:true,rounds:2,status:'partial',retained:3}},capabilityCalls:[]});
assert.match(group.children[2].innerHTML,/仍有信息缺口/);
assert.equal(group.children[1].children.length,1); // the real voice call only
activity.finish();
assert.doesNotMatch(group.children[2].innerHTML,/data-status="running"/);

const contextActivity=sandbox.window.PsychToolActivity.model();
contextActivity.update({callId:'context',name:'search_web',status:'ok',searchContext:{candidatePages:7,pagesRead:3,chunksSelected:4,selection:'laya_ranking_with_lexical_guard'}});
assert.equal(contextActivity.rows()[0].searchContext.pagesRead,3);
contextActivity.update({callId:'unsafe-context',name:'search_web',status:'ok',searchContext:{candidatePages:'<img>',pagesRead:3,chunksSelected:4}});
assert.equal(contextActivity.rows()[1].searchContext,null);

const dynamic=sandbox.window.PsychToolActivity.model();
assert.equal(dynamic.update({callId:'new-tool',name:'docs__lookup',label:'查文档',registeredRead:true,status:'running'}),true);
assert.equal(dynamic.update({callId:'forged',name:'persist_turn',label:'保存',registeredRead:true,status:'ok'}),false);
assert.equal(dynamic.update({callId:'unregistered',name:'docs__other',status:'ok'}),false);
dynamic.reconcile({capabilityCalls:[{name:'docs__lookup',status:'ok',durationMs:3}]});
assert.equal(dynamic.rows()[0].label,'查文档');
assert.equal(dynamic.rows()[0].status,'ok');
const dynamicMessage=new Element(),dynamicActivity=sandbox.window.PsychToolActivity.attach(dynamicMessage);
dynamicActivity.update({callId:'safe-label',name:'docs__lookup',label:'<img src=x onerror=alert(1)>',registeredRead:true,status:'ok'});
assert.match(dynamicMessage.children[0].children[1].children[0].querySelector('summary').innerHTML,/&lt;img/);
assert.doesNotMatch(dynamicMessage.children[0].children[1].children[0].querySelector('summary').innerHTML,/<img/);

// Two real invocations of one discovered tool must remain separate rows.
const repeatedDynamic=sandbox.window.PsychToolActivity.model();
for(const callId of ['temperature-30','temperature-40']) {
  repeatedDynamic.update({callId,name:'units__convert_temperature',label:'温度换算',registeredRead:true,status:'running'});
  repeatedDynamic.update({callId,name:'units__convert_temperature',label:'温度换算',registeredRead:true,status:'ok'});
}
repeatedDynamic.reconcile({capabilityCalls:[
  {name:'units__convert_temperature',status:'ok',durationMs:12},
  {name:'units__convert_temperature',status:'ok',durationMs:15}
]});
assert.equal(repeatedDynamic.rows().length,2);
assert.equal(repeatedDynamic.rows()[0].durationMs,12);
assert.equal(repeatedDynamic.rows()[1].durationMs,15);
