const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path');
const sandbox={window:{}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../chat-renderer.js'),'utf8'),sandbox);
const renderer=sandbox.window.PsychChatRenderer;
for(const protocol of ['<｜DSML｜function_calls>','< | | DSML | | calls>','<tool_call>{"name":"search_weather"}</tool_call>']){
  const target={};renderer.render(target,protocol);
  assert.match(target.textContent,/不能作为查询结果/);
  assert.doesNotMatch(renderer.visibleText(protocol),/DSML|search_weather/);
}
const ordinary='广州资料可看 [来源](https://example.com)，x < 3。';
assert.equal(renderer.visibleText(ordinary),ordinary);
assert.equal(renderer.visibleText(''), '');
console.log('Reply rendering: historical internal protocol is hidden; ordinary Markdown/math stays intact.');
