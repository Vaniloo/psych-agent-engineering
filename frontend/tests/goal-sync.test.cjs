const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path');
class Element {
  constructor(){this.children=[];this.dataset={};this.className='';this.innerHTML='';this.isConnected=true;}
  setAttribute(){} addEventListener(){} appendChild(child){this.children.push(child);}
  querySelector(selector){
    const match=selector.match(/data-synced-checkin="(\d+)"/);
    return this.children.find(c=>match?c.dataset.syncedCheckin===match[1]:selector==='.message-goal-updates'&&c.className==='message-goal-updates')||null;
  }
}
const sandbox={window:{},document:{createElement:()=>new Element()}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../html.js'),'utf8'),sandbox);
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../goals-view.js'),'utf8'),sandbox);
const view=sandbox.window.PsychGoalsView,message=new Element();
const entry={id:7,goalTitle:'学习Python',actionTitle:'学一个知识点',outcome:'done',planningStatus:'pending'};
view.showSynced([entry],message);const group=message.children[0],notice=group.children[0];
assert.match(notice.innerHTML,/正在判断目标/);
view.showSynced([{...entry,planningStatus:'ready',reflection:{goalCompleted:false,nextStep:'明天学 <script>不可执行</script>'}}],message);
assert.equal(group.children.length,1);assert.match(notice.innerHTML,/下一步已保存/);assert.match(notice.innerHTML,/&lt;script&gt;/);
const ready=notice.innerHTML;
view.showSynced([entry],message);view.showSynced([{...entry,planningStatus:undefined}],message);
assert.equal(notice.innerHTML,ready);
view.showSynced([{...entry,planningStatus:'ready',reflection:{goalCompleted:true}}],message);
assert.match(notice.innerHTML,/目标已达成/);assert.equal(group.children.length,1);
notice.dataset.undone='true';const undone=notice.innerHTML;
view.showSynced([{...entry,planningStatus:'ready',reflection:{goalCompleted:false,nextStep:'不能恢复撤销'}}],message);
assert.equal(notice.innerHTML,undone);
console.log('Goal sync: pending/final upsert, duplicate and stale events, escaped next step, completion and undo passed.');

const closing=new Element();
view.showSynced([{id:12,goalTitle:'Java',actionTitle:'Java',outcome:'goal_completed'}],closing);
assert.match(closing.children[0].children[0].innerHTML,/不再追加步骤/);
view.showSynced([{id:13,goalTitle:'Java',actionTitle:'Java',outcome:'revised'}],closing);
assert.match(closing.children[0].children[1].innerHTML,/指定步骤已修改/);
