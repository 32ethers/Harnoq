// Runs the REAL app.js diagram builders under jsdom with fake data and feeds
// every definition they produce to mermaid's own parser.
//
// Why this exists: a malformed diagram definition is invisible to pytest and to
// `node --check` — it only fails when a browser tries to draw it, and then it
// takes the whole diagram down. Parsing is the part that can be checked without
// a browser; jsdom cannot do the drawing (no getBBox), so pixels still need a
// human look.
//
// Needs jsdom, which is NOT a project dependency:
//   npm install jsdom && node tests/js_diagram_smoke.mjs
// tests/test_app_js_smoke.py runs it automatically when jsdom resolves.
import fs from "fs";

// jsdom is not a project dependency (this repo has no node build step at all),
// so it may live anywhere. HARNOQ_JSDOM points at an installed copy; otherwise
// a normal bare import is tried.
async function loadJsdom() {
  const candidates = [process.env.HARNOQ_JSDOM, "jsdom"].filter(Boolean);
  for (const candidate of candidates) {
    try {
      return (await import(candidate)).JSDOM;
    } catch (err) {
      if (candidate === candidates[candidates.length - 1]) throw err;
    }
  }
}

const JSDOM = await loadJsdom();

import path from "path";
const REPO = path.resolve(import.meta.dirname, "..");
const dom = new JSDOM(`<!doctype html><html><body>
  <div id="features-list-page" data-project-id="1"></div>
  <div id="fl-graph-section"></div>
  <div id="fdi-chain-graph"></div>
  <div id="crl-impact-graph"></div>
</body></html>`, { pretendToBeVisual: true, runScripts: "dangerously" });

const tag = dom.window.document.createElement("script");
tag.textContent = fs.readFileSync(`${REPO}/app/static/vendor/mermaid.min.js`, "utf8");
dom.window.document.body.appendChild(tag);
const mermaid = dom.window.mermaid;

// Capture definitions instead of rendering (jsdom has no getBBox), then parse.
const captured = [];
dom.window.mermaid = {
  initialize: () => {},
  render: async (id, def) => { captured.push(def); return { svg: "<svg></svg>" }; },
};
dom.window.DOMPurify = { sanitize: (s) => s };
dom.window.marked = { parse: (s) => s };

const app = dom.window.document.createElement("script");
app.textContent = fs.readFileSync(`${REPO}/app/static/i18n.js`, "utf8") + "\n" + fs.readFileSync(`${REPO}/app/static/app.js`, "utf8");
dom.window.document.body.appendChild(app);
dom.window.document.dispatchEvent(new dom.window.Event("DOMContentLoaded"));

// `const`/`function` in a classic script live in global LEXICAL scope, not on
// window — a second script can still see them.
const expose = dom.window.document.createElement("script");
expose.textContent = `window.__app = { renderFeaturesGraph, renderFeatureChainGraph,
  renderChangeImpactGraph, featuresListState, featureDetailState };`;
dom.window.document.body.appendChild(expose);

const w = dom.window.__app;
w.document = dom.window.document;
w.featuresListState.projectId = "1";
await w.renderFeaturesGraph([
  { id: 1, feature_number: "FEATURE-1", name: "索引地基 | v2", subdomain_type: "core", depends_on_feature_ids: [] },
  { id: 2, feature_number: "FEATURE-2", name: '带"引号"的名字', subdomain_type: "generic", depends_on_feature_ids: [1] },
  { id: 3, feature_number: "FEATURE-3", name: "很长很长很长很长很长很长很长很长的功能名字", subdomain_type: "supporting", depends_on_feature_ids: [1, 2] },
]);

w.featureDetailState.feature = { id: 2, project_id: 1, feature_number: "FEATURE-2", name: "浏览" };
w.featureDetailState.requirementItems = [
  { id: 5, requirement_item_number: "REQ-5", statement: "当用户提交空密码,系统应拒绝", design_case_id: 3, feature_id: 2 },
  { id: 6, requirement_item_number: "REQ-6", statement: "未连入设计的需求项", design_case_id: null, feature_id: 2 },
];
w.featureDetailState.designCases = [
  { id: 3, design_number: "DESIGN-3", problem_statement: "分页方案", feature_id: 2 },
  { id: 4, design_number: "DESIGN-4", problem_statement: "没有需求项的设计", feature_id: 2 },
];
w.featureDetailState.tasks = [{ id: 7, task_number: "TASK-7", title: "实现分页", design_case_id: 3 }];
await w.renderFeatureChainGraph();

await w.renderChangeImpactGraph(w.document.getElementById("crl-impact-graph"), "REQ-6 需求项", [
  { object_type: "design_case", object_number: "DESIGN-3", status: "confirmed", response: "x" },
  { object_type: "development_task", object_number: "TASK-7", status: "completed", response: "y" },
  { object_type: "development_task", object_number: "TASK-8", status: "in_progress", response: "z" },
]);

console.log(`captured ${captured.length} definitions`);
let failed = 0;
for (const [i, def] of captured.entries()) {
  try {
    await mermaid.parse(def);
    console.log(`  [${i}] parse OK (${def.split("\n").length} lines)`);
  } catch (e) {
    failed++;
    console.log(`  [${i}] PARSE FAIL: ${e.message}\n---\n${def}\n---`);
  }
}
if (captured.length !== 3) { console.log("expected 3 diagrams"); failed++; }
process.exit(failed ? 1 : 0);
