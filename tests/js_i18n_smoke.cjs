const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const catalog = JSON.parse(fs.readFileSync(path.join(root, 'app/locales/en.json'), 'utf8'));
for (const english of [false, true]) {
  let asked = null;
  const context = vm.createContext({
    document: {
      getElementById: id => id === 'harnoq-i18n' ? {textContent: JSON.stringify(english ? catalog : {})} : null,
      querySelector: () => null, querySelectorAll: () => [], addEventListener: () => {},
    },
    window: {addEventListener: () => {}, confirm: text => {asked = text; return false;}},
  });
  for (const file of ['i18n.js', 'app.js']) vm.runInContext(fs.readFileSync(path.join(root, 'app/static', file), 'utf8'), context);
  assert.equal(vm.runInContext('uiText("保存")', context), english ? 'Save' : '保存');
  assert.equal(vm.runInContext('uiText`已创建 ${"中文 {0} <script>"}`', context), (english ? 'Created ' : '已创建 ') + '中文 {0} <script>');
  assert.equal(vm.runInContext('uiText("unknown-key")', context), 'unknown-key');
  assert.equal(vm.runInContext('uiText("toString")', context), 'toString');
  assert.equal(vm.runInContext('confirmSoftDelete("中文项目")', context), false);
  assert.match(asked, english ? /^Delete “中文项目”/ : /^删除「中文项目」/);
  assert.match(asked, english ? /can be restored/ : /可以恢复/);
  assert.equal(vm.runInContext('confirmHardDelete("TASK-1")', context), false);
  assert.match(asked, english ? /cannot be undone/ : /无法恢复/);
}
console.log('OK: both locales, interpolation, fallback, and deletion confirmation');
