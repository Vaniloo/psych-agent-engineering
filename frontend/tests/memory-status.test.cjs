const vm = require('node:vm'), fs = require('node:fs'), assert = require('node:assert/strict');
class Element {
  constructor(tag) {this.tag = tag; this.children = []; this.dataset = {}; this.hidden = false; this.isConnected = true; this.listeners = {};}
  setAttribute() {}
  append(...children) {this.children.push(...children);}
  insertBefore(element) {this.children.push(element);}
  querySelector() {return null;}
  addEventListener(type, action) {this.listeners[type] = action;}
}
async function fixture(responses, current = () => true) {
  const timers = new Map(); let timerId = 0, calls = 0;
  const sandbox = {window: {}, document: {createElement: tag => new Element(tag)}, Date, AbortController,
    setTimeout: cb => {timers.set(++timerId, cb); return timerId;}, clearTimeout: id => timers.delete(id)};
  vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname, '../memory-status.js'), 'utf8'), sandbox);
  const message = new Element('article');
  const badge = sandbox.window.PsychMemoryStatus.attach(message, {isCurrent: current,
    fetchStatus: async () => {calls++; const value = responses.shift(); if(value instanceof Error) throw value; return value;}});
  async function tick() {const [id, action] = timers.entries().next().value || []; timers.delete(id); if(action) await action();}
  return {badge, group: message.children[0], tick, calls: () => calls, message, describe: sandbox.window.PsychMemoryStatus.describe};
}
(async () => {
  const f = await fixture([{status: 'pending'}, {status: 'updated', changedCount: 1}]);
  f.badge.pending(); assert.equal(f.group.dataset.status, 'pending'); assert.equal(f.calls(), 0);
  f.badge.complete('owned-request', true); await f.tick(); assert.equal(f.group.dataset.status, 'pending');
  await f.tick(); assert.equal(f.group.dataset.status, 'updated'); assert.equal(f.calls(), 2);
  const normal = await fixture([{status:'indexed'}]); normal.badge.complete('recent', true); await normal.tick();
  assert.equal(normal.group.dataset.status, 'indexed');
  assert.match(normal.describe({status:'indexed'}).label, /近期/);
  const failed = await fixture([]); failed.badge.complete('not-saved', false); await failed.tick();
  assert.equal(failed.calls(), 0); assert.equal(failed.group.dataset.status, 'not_saved');
  const timeout = await fixture(Array.from({length:10}, () => new Error('offline')));
  timeout.badge.complete('saved', true); for(let i=0;i<11;i++) await timeout.tick();
  assert.equal(timeout.calls(), 10); assert.equal(timeout.group.dataset.status, 'unconfirmed');
  const left = await fixture([{status:'updated'}]); left.badge.complete('other-session', true); left.message.isConnected = false;
  await left.tick(); assert.equal(left.calls(), 0);
  let auth = true; const logout = await fixture([{status:'updated'}], () => auth);
  logout.badge.complete('old-account', true); auth = false; await logout.tick(); assert.equal(logout.calls(), 0);
  const missing = await fixture([{status:'invented_success'}]); missing.badge.complete('unknown-state', true); await missing.tick();
  assert.equal(missing.group.dataset.status, 'unconfirmed');
  assert.match(missing.describe({status:'unchanged',retentionOptout:true}).detail, /没有写入长期记忆/);
  console.log('Memory status: no optimistic success, bounded retry, owner/session changes and recent vs long-term states passed.');
})().catch(error => {console.error(error); process.exitCode=1;});
