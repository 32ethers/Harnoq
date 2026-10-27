// Minimal DOM stub: enough to run app.js top-level and then its
// DOMContentLoaded init list against a page where nothing matches.
//
// This exists because app.js has no build step and no unit tests, so a name
// that no longer resolves — a function deleted while its call site stayed —
// is invisible until a browser throws. `node --check` only parses. Running
// the file catches the whole class: one ReferenceError in the init list kills
// every page-render call after it, so the UI silently does nothing.
const noop = () => {};
let domReady = null;
const stubEl = {
  addEventListener: noop, removeEventListener: noop, appendChild: noop,
  append: noop, replaceChildren: noop,
  classList: { add: noop, remove: noop, contains: () => false },
  dataset: {}, style: {}, querySelector: () => null, querySelectorAll: () => [],
  setAttribute: noop, textContent: "", innerHTML: "", value: "", href: "",
  focus: noop, click: noop, remove: noop, closest: () => null, elements: {},
};
global.document = {
  addEventListener: (evt, cb) => { if (evt === "DOMContentLoaded") domReady = cb; },
  removeEventListener: noop,
  querySelector: () => null, querySelectorAll: () => [],
  getElementById: () => null,
  createElement: () => ({ ...stubEl }),
  createRange: () => ({ selectNodeContents: noop }),
  body: { ...stubEl }, documentElement: { ...stubEl },
};
global.window = {
  addEventListener: noop,
  location: { pathname: "/" },
  getSelection: () => ({ removeAllRanges: noop, addRange: noop }),
  confirm: () => false,
};
global.navigator = { clipboard: { writeText: async () => {} } };
global.fetch = async () => ({ ok: true, json: async () => ({}) });
global.marked = { parse: (s) => s };
global.DOMPurify = { sanitize: (s) => s };

require('vm').runInThisContext(require('fs').readFileSync(require('path').join(require('path').dirname(process.argv[2]), 'i18n.js'), 'utf8'));
require(process.argv[2]);
if (!domReady) {
  console.error("app.js registered no DOMContentLoaded handler");
  process.exit(1);
}
domReady();
console.log("OK");
