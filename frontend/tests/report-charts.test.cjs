const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const path=require('node:path');
const sandbox={window:{}};vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../report-charts.js'),'utf8'),sandbox);
const chart=sandbox.window.PsychReportCharts;
const data={periodStart:'2026-09-10',periodEnd:'2026-10-09',evidence:[
 {messageId:1,text:'压力也有一点开心',createdAt:'2026-09-15T10:00:00'},
 {messageId:2,text:'轻松了',createdAt:'2026-10-08T10:00:00'},
 {messageId:3,text:'窗口外压力',createdAt:'2026-09-01T10:00:00'}],emotionObservations:[
 {messageId:1,category:'tense',quote:'压力'}, {messageId:1,category:'tense',quote:'压力'},
 {messageId:1,category:'positive',quote:'开心'}, {messageId:2,category:'calm',quote:'轻松'},
 {messageId:999,category:'sad',quote:'压力'}, {messageId:2,category:'sad',quote:'不存在'},
 {messageId:3,category:'tense',quote:'压力'}]};
const m=chart.model(data);assert.equal(m.expressionCount,2);assert.equal(m.observationCount,3);
assert.equal(m.groups[0].ids.length,1);assert.equal(m.bins.length,6);
assert.equal(m.bins[0].items.length,0);assert.equal(m.bins[1].ids.length,1);assert.equal(m.bins[1].items.length,2);
assert.equal(m.bins[5].from,'2026-10-05');assert.equal(m.bins[5].to,'2026-10-09');
assert.equal(chart.model({...data,emotionObservations:[]}).observationCount,0);
assert.ok(chart.render({...data,emotionObservations:[]}).includes('暂时不绘制分布'));
assert.ok(!chart.render(data).includes('height:NaN'));console.log('Chart aggregation passed: grounded counts, duplicate handling, dates, gaps, empty states.');
