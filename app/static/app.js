// Browser dashboard renderer.
//
// Web routes under /ui return HTML shells only. Dynamic reads and writes go
// through the existing REST/JSON API so business rules stay in app/services/*
// behind app/api/* instead of being duplicated in app/web/views.py.

const TASK_STATUS_ORDER = [
  "claimable",
  "claimed",
  "pre_reviewing",
  "rejected_to_design",
  "needs_revision",
  "ready_for_development",
  "in_progress",
  "ready_for_pr",
  "completed",
];

const DESIGN_CONFIRM_FIELDS = [
  ["problem_statement", uiText("问题陈述")],
  ["selected_solution", uiText("选定方案")],
  ["selection_reason", uiText("选择理由")],
  ["risks", uiText("风险")],
  ["test_strategy", uiText("测试策略")],
  ["acceptance_criteria", uiText("验收标准")],
];

const detailState = {
  projectId: null,
  project: null,
  inputs: [],
  units: [],
  features: [],
  designs: [],
  tasks: [],
  constitution: null,
  glossary: [],
  deliveryGate: null,
  deliveryItemCounts: {},
};

function qs(selector, root = document) {
  return root.querySelector(selector);
}

function qsa(selector, root = document) {
  return Array.from(root.querySelectorAll(selector));
}

function clear(element) {
  element.replaceChildren();
}

function show(element) {
  element.classList.remove("hidden");
}

function hide(element) {
  element.classList.add("hidden");
}

// Version comparisons are shared by every versioned entity.  Keep their
// output in one modal so a long diff never pushes the detail page around.
function closeVersionDiffModal() {
  const modal = qs("#version-diff-modal");
  const content = qs("#version-diff-modal-content");
  if (modal) hide(modal);
  if (content) clear(content);
}

function openVersionDiffModal(title) {
  const modal = qs("#version-diff-modal");
  const titleEl = qs("#version-diff-modal-title");
  const content = qs("#version-diff-modal-content");
  if (!modal || !titleEl || !content) return null;
  titleEl.textContent = title;
  clear(content);
  show(modal);
  return content;
}

function initVersionDiffModal() {
  const modal = qs("#version-diff-modal");
  if (!modal) return;
  const closeButton = qs("#version-diff-modal-close");
  if (closeButton) closeButton.addEventListener("click", closeVersionDiffModal);
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeVersionDiffModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.classList.contains("hidden")) {
      closeVersionDiffModal();
    }
  });
}

const TOAST_AUTO_DISMISS_MS = 8000;

// Page-level errors (e.g. from an action button far down a long page) render
// into a per-page inline error div that can scroll out of view — a toast
// guarantees the message is seen regardless of scroll position.
function showToast(message, { type = "error" } = {}) {
  const container = qs("#toast-container");
  if (!container) return;
  const toast = el("div", `toast toast-${type}`);
  toast.setAttribute("role", "alert");
  const text = el("div", "toast-message", message);
  const closeBtn = el("button", "toast-close", "×");
  closeBtn.type = "button";
  closeBtn.setAttribute("aria-label", uiText("关闭"));
  const dismiss = () => toast.remove();
  closeBtn.addEventListener("click", dismiss);
  toast.append(text, closeBtn);
  container.appendChild(toast);
  setTimeout(dismiss, TOAST_AUTO_DISMISS_MS);
}

// Safety net: any exception that reaches here slipped past every local
// try/catch (a bug in a specific handler, not a missing show*Error call) —
// still surface it as a toast instead of failing silently.
window.addEventListener("unhandledrejection", (event) => {
  const reason = event.reason;
  const message = reason && reason.message ? reason.message : String(reason);
  showToast(uiText`未处理的错误：${message}`);
});
window.addEventListener("error", (event) => {
  if (!event.error) return; // resource load errors (img/script) carry no `.error`
  showToast(uiText`未处理的错误：${event.error.message || event.error}`);
});

function textOr(value, fallback = "-") {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

function previewText(value, limit = 120) {
  const text = textOr(value, "").replace(/\s+/g, " ").trim();
  if (!text) return "-";
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

function cssToken(value) {
  return textOr(value, "unknown").replace(/[^a-zA-Z0-9_-]/g, "-");
}

// Render user/AI-authored markdown into a read-only element. Always sanitize:
// the content is untrusted, so marked → DOMPurify → innerHTML. Falls back to
// plain text if the vendored libs failed to load, so content is never lost.
function renderMarkdown(target, value) {
  if (!target) return;
  const text = value == null ? "" : String(value);
  target.classList.add("markdown-body");
  if (!text.trim()) {
    target.textContent = "-";
    return;
  }
  if (typeof marked === "undefined" || typeof DOMPurify === "undefined") {
    target.textContent = text;
    return;
  }
  try {
    // Most Harnoq detail content is authored like notes/docs. Preserve single
    // line breaks so pasted requirement/design text reads naturally even when
    // the author did not insert blank lines between every paragraph.
    // Some designs were stored with the two characters backslash-n instead of
    // a real break (a model serialized newlines that way). marked treats those
    // as ordinary text, collapsing a whole criteria block onto one line, so
    // turn them into real breaks first. Only \n — \u/\\ in JSON-backed fields
    // are legitimate and must survive (see app/core/agent_text.py).
    const source = text.replace(/\r\n/g, "\n").replace(/\\n/g, "\n");
    const html = marked.parse(source, {
      gfm: true,
      breaks: true,
    });
    target.innerHTML = DOMPurify.sanitize(html);
  } catch (_err) {
    target.textContent = text;
  }
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function setStageState(stageId, state) {
  const stage = document.getElementById(stageId);
  if (stage) stage.dataset.state = state;
}

function statusBadge(status) {
  const badge = el("span", `badge badge-${cssToken(status)}`, textOr(status));
  return badge;
}

function featureStatusBadges(feature) {
  const container = el("span", "feature-status-badges");
  const design = feature.design_status || feature.progress || "planned";
  const implementation = feature.implementation_status || "not_started";
  const designLabels = { planned: uiText("未开始"), in_design: uiText("设计中"), confirmed: uiText("已确认") };
  const implementationLabels = { not_started: uiText("未开始"), in_progress: uiText("进行中"), implemented: uiText("已完成") };
  container.appendChild(el("span", `badge badge-${cssToken(design)}`, uiText`设计：${designLabels[design] || design}`));
  container.appendChild(document.createTextNode(" "));
  container.appendChild(el("span", `badge badge-${cssToken(implementation)}`, uiText`实现：${implementationLabels[implementation] || implementation}`));
  return container;
}

function gateStatusBadge(status, deletedAt) {
  return statusBadge(deletedAt ? "deleted" : status);
}

// A suspect object is one whose upstream moved after it was settled. It is a
// prompt, not a state: it rides ALONGSIDE the status badge instead of replacing
// it, because the object's own status still describes the object (that
// conflation is what retired needs_review — see app/services/lifecycle.py).
function suspectBadge(item) {
  if (!item || !item.suspect) return null;
  const upstream = (item.suspect_upstream || [])
    .map((edge) => edgeChangeText(edge))
    .join("、");
  const badge = el("span", "badge badge-suspect", uiText("上游已变"));
  if (upstream) badge.title = uiText`依据的版本已过期：${upstream}`;
  return badge;
}

const _UPSTREAM_LABELS = {
  feature: uiText("功能"),
  design_case: uiText("设计用例"),
};

function upstreamLabel(edge) {
  return `${_UPSTREAM_LABELS[edge.upstream_type] || edge.upstream_type}#${edge.upstream_id}`;
}

// A suspect edge is either "upstream got revised" (versions moved) or
// "upstream is gone" (soft-deleted or purged) — the two read very differently
// to a human, so they get worded differently instead of both showing a bare
// version number that doesn't exist for the second kind.
function edgeChangeText(edge) {
  if (edge.reason === "upstream_deleted") {
    return uiText`${upstreamLabel(edge)}（已被删除）`;
  }
  return `${upstreamLabel(edge)} v${edge.basis_version_number}→v${edge.current_version_number}`;
}

// The "I read the upstream change and this still holds" button. Deliberately
// worded as a judgement, not a dismissal.
//
// ``needReason`` is set for a task that has already SHIPPED: there the same
// click asserts that merged code still satisfies a design that has changed,
// and the server refuses it without a stated reason. Prompting here is what
// keeps the UI from being the one caller that cannot comply.
function realignButton(basePath, reload, showError, needReason = false) {
  return opButton(
    uiText("上游已复核"),
    "action-button",
    () => {
      let body = {};
      if (needReason) {
        const reason = window.prompt(
          uiText("这个任务已交付。对齐基线等于声称「已合并的代码仍然满足改后的设计」，") +
            uiText("而没有任何东西会重新验证它。\n\n") +
            uiText("请写明你核对了什么。若已不满足，应改用「打回重做」。\n\n理由：")
        );
        if (reason === null || !reason.trim()) return false;
        body = { reason: reason.trim() };
      }
      return apiJson(`${basePath}/realign-basis`, jsonOptions("POST", body));
    },
    reload,
    showError
  );
}

function suspectHintText(item) {
  const upstream = (item.suspect_upstream || [])
    .map((edge) => edgeChangeText(edge))
    .join("、");
  return uiText`上游已修改：${upstream}。先读那处改动，若此处仍然成立就点「上游已复核」；` +
    uiText("不成立则重新打开修改。它只是提示，不挡任何操作。");
}

function appendSuspectHint(hintEl, item) {
  if (!hintEl || !item || !item.suspect) return;
  hintEl.textContent = `${hintEl.textContent ? hintEl.textContent + " " : ""}${suspectHintText(item)}`;
}

function isEditableGateStatus(status) {
  return status === "draft" || status === "needs_clarification";
}

function chip(value, extraClass = "") {
  const className = extraClass ? `chip ${extraClass}` : "chip";
  return el("span", className, textOr(value));
}

function businessNumber(item, key, fallback = uiText("未编号")) {
  return item && item[key] ? item[key] : fallback;
}

// A business number rendered as a link to another entity's detail page. Falls
// back to plain text when the target can't be resolved (e.g. the referenced
// entity was deleted, or has no detail page yet), so a broken ref still shows
// the raw value instead of a dead link.
function numberLink(text, href) {
  const label = textOr(text);
  if (!href || label === "-") return label;
  const anchor = el("a", "number-link", label);
  anchor.href = href;
  return anchor;
}

// A comma-separated run of business-number links, for detail fields that show
// a set of related entities (e.g. a REQ's source requirements). ``refs`` is a
// list of {text, href}; a null href renders as plain text. Returns a fragment
// suitable for replaceChildren, or a "-" text node when empty.
function numberLinkList(refs) {
  if (!refs || !refs.length) return document.createTextNode("-");
  const frag = document.createDocumentFragment();
  refs.forEach((ref, index) => {
    if (index) frag.appendChild(document.createTextNode(", "));
    const node = numberLink(ref.text, ref.href);
    frag.appendChild(typeof node === "string" ? document.createTextNode(node) : node);
  });
  return frag;
}


function featureListHref(projectId, featureId) {
  if (!featureId) return null;
  return `/ui/features/${featureId}`;
}

function featureRefFor(feature, projectId) {
  if (!feature) return { text: "-", href: null };
  return {
    text: businessNumber(feature, "feature_number", feature.name || `#${feature.id}`),
    href: featureListHref(projectId, feature.id),
  };
}

function renderProjectFlowNav(projectId, activeKey) {
  if (!projectId) return;
  qsa(".project-flow-nav").forEach((nav) => {
    const projectLink = nav.querySelector('[data-flow-link="project"]');
    const inputsLink = nav.querySelector('[data-flow-link="inputs"]');
    const constitutionLink = nav.querySelector('[data-flow-link="constitution"]');
    const glossaryLink = nav.querySelector('[data-flow-link="glossary"]');
    const featuresLink = nav.querySelector('[data-flow-link="features"]');
    const designsLink = nav.querySelector('[data-flow-link="designs"]');
    const tasksLink = nav.querySelector('[data-flow-link="tasks"]');
    const integrationLink = nav.querySelector('[data-flow-link="integration"]');
    const systemLink = nav.querySelector('[data-flow-link="system"]');
    const changeRequestsLink = nav.querySelector('[data-flow-link="change-requests"]');
    if (projectLink) projectLink.href = `/ui/projects/${projectId}`;
    if (inputsLink) inputsLink.href = `/ui/projects/${projectId}/source-requirements`;
    if (constitutionLink) constitutionLink.href = `/ui/projects/${projectId}/constitution`;
    if (glossaryLink) glossaryLink.href = `/ui/projects/${projectId}/glossary`;
    if (featuresLink) featuresLink.href = `/ui/projects/${projectId}/features`;
    if (designsLink) designsLink.href = `/ui/projects/${projectId}/design-cases`;
    if (tasksLink) tasksLink.href = `/ui/projects/${projectId}/tasks`;
    if (integrationLink) integrationLink.href = `/ui/projects/${projectId}/integration`;
    if (systemLink) systemLink.href = `/ui/projects/${projectId}/system`;
    if (changeRequestsLink) {
      changeRequestsLink.href = `/ui/projects/${projectId}/change-requests`;
    }
    nav.querySelectorAll("[data-flow-link]").forEach((link) => {
      link.classList.toggle("active", link.dataset.flowLink === activeKey);
    });
    show(nav);
  });
}

function markdownList(values) {
  if (!Array.isArray(values) || !values.length) return "";
  return values.map((value) => `- ${value}`).join("\n");
}

function rejectedSolutionsMarkdown(values) {
  if (!Array.isArray(values) || !values.length) return "";
  return values.map((value) => {
    if (value && typeof value === "object") {
      const name = value.name || uiText("未命名方案");
      const description = value.description ? `：${value.description}` : "";
      const reason = value.reason ? uiText`（拒绝原因：${value.reason}）` : "";
      return `- ${name}${description}${reason}`;
    }
    return `- ${value}`;
  }).join("\n");
}

async function apiJson(path, options = {}) {
  const auth = document.querySelector('meta[name="csrf-token"]');
  const headers = { ...(options.headers || {}) };
  if (auth && !["GET", "HEAD", "OPTIONS"].includes((options.method || "GET").toUpperCase())) {
    headers["X-CSRF-Token"] = auth.content;
  }
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const message = data && data.error ? data.error : `HTTP ${response.status}`;
    if (response.status === 401 && window.location.pathname !== "/ui/login") window.location.href = "/ui/login";
    throw new Error(message);
  }
  return data;
}

function jsonOptions(method, body = {}) {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

// --- generic modal manager factory -----------------------------------------
// Every entity CRUD modal follows the same lifecycle: init caches DOM elements,
// openCreate / openEdit populate the form, the form submit handler validates,
// calls an async submit fn, and on success invokes a reload callback.
// The factory encapsulates that lifecycle so each modal is just a config object.
function createModalManager(config) {
  // config keys:
  //   overlayId       – CSS selector for the .modal-overlay (required)
  //   createTitle     – <h2> text in create mode (default "创建")
  //   editTitle       – <h2> text in edit mode (default "编辑")
  //   focusSelector   – optional selector for the input to focus on open
  //   titleSelector   – selector for the <h2> (default "h2")
  //   errorSelector   – selector for the error <span> (default ".form-error")
  //   submitSelector  – selector for the submit button (default 'button[type="submit"]')
  //   cancelSelector  – selector for the cancel button (default 'button[type="button"]')
  //   getPayload(form) → Object | null  – read & validate; return null + set error on fail
  //   setFormData(form, item) – populate form fields for edit mode
  //   submit(mode, payload, editId) → Promise<result> – the API call
  //   onSuccess(result, mode) – called after submit succeeds (modal already closed)

  const state = { el: null, form: null, titleEl: null, errorEl: null, submitBtn: null, mode: null, editId: null };

  function cacheElements(overlay) {
    state.el = overlay;
    state.form = overlay.querySelector("form");
    state.titleEl = overlay.querySelector(config.titleSelector || "h2");
    state.errorEl = overlay.querySelector(config.errorSelector || ".form-error");
    state.submitBtn = overlay.querySelector(config.submitSelector || 'button[type="submit"]');
  }

  function _close() {
    if (!state.el) return;
    hide(state.el);
    state.editId = null;
    state.mode = null;
    if (state.errorEl) state.errorEl.textContent = "";
  }

  function init() {
    const overlay = qs(config.overlayId);
    if (!overlay) return;
    cacheElements(overlay);

    const cancelBtn = overlay.querySelector(config.cancelSelector || 'button[type="button"]');
    if (cancelBtn) cancelBtn.addEventListener("click", _close);

    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) _close();
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !overlay.classList.contains("hidden")) _close();
    });

    if (state.form) {
      state.form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!state.form) return;
        if (state.errorEl) state.errorEl.textContent = "";

        const payload = config.getPayload(state.form);
        if (payload === null) return; // validation failed – getPayload already set error

        if (state.submitBtn) state.submitBtn.disabled = true;
        try {
          const result = await config.submit(state.mode, payload, state.editId);
          _close();
          config.onSuccess(result, state.mode);
        } catch (err) {
          if (state.errorEl) state.errorEl.textContent = err.message || uiText("保存失败");
        } finally {
          if (state.submitBtn) state.submitBtn.disabled = false;
        }
      });
    }
  }

  function openCreate() {
    if (!state.el) return;
    state.mode = "create";
    state.editId = null;
    if (state.titleEl) state.titleEl.textContent = config.createTitle || uiText("创建");
    if (state.form) state.form.reset();
    if (state.errorEl) state.errorEl.textContent = "";
    show(state.el);
    if (config.focusSelector) {
      const focusEl = state.el.querySelector(config.focusSelector);
      if (focusEl) focusEl.focus();
    }
  }

  function openEdit(item) {
    if (!state.el) return;
    state.mode = "edit";
    state.editId = item ? item.id : null;
    if (state.titleEl) state.titleEl.textContent = config.editTitle || uiText("编辑");
    if (state.form) state.form.reset();
    if (item && config.setFormData) config.setFormData(state.form, item);
    if (state.errorEl) state.errorEl.textContent = "";
    show(state.el);
    if (config.focusSelector) {
      const focusEl = state.el.querySelector(config.focusSelector);
      if (focusEl) focusEl.focus();
    }
  }

  function isOpen() {
    return state.el && !state.el.classList.contains("hidden");
  }

  return { init, openCreate, openEdit, close: _close, isOpen };
}

// --- project modal (refactored to use createModalManager) -------------------
const _pm = createModalManager({
  overlayId: "#project-modal",
  createTitle: uiText("创建项目"),
  editTitle: uiText("编辑项目"),
  focusSelector: "#project-name",
  errorSelector: "#create-project-error",
  submitSelector: "#project-modal-submit",
  cancelSelector: "#project-modal-cancel",
  getPayload(form) {
    const name = form.elements.name.value.trim();
    if (!name) {
      form.querySelector("#create-project-error").textContent = uiText("项目名称不能为空");
      return null;
    }
    return {
      name,
      description: form.elements.description.value.trim(),
      repo_url: form.elements.repo_url.value.trim(),
    };
  },
  setFormData(form, item) {
    form.elements.name.value = item.name || "";
    form.elements.description.value = item.description || "";
    form.elements.repo_url.value = item.repo_url || "";
  },
  submit(mode, payload, editId) {
    if (mode === "edit") {
      return apiJson(`/projects/${editId}`, jsonOptions("PATCH", payload));
    }
    return apiJson("/projects", jsonOptions("POST", payload));
  },
  onSuccess(result, mode) {
    if (mode === "create") {
      window.location.href = `/ui/projects/${result.id}`;
    } else {
      refreshCurrentProjectView();
    }
  },
});

function initProjectModal()   { _pm.init(); }
function openCreateProjectModal() { _pm.openCreate(); }
function openEditProjectModal(project) { _pm.openEdit(project); }
function closeProjectModal() { _pm.close(); }

function refreshCurrentProjectView() {
  if (qs("#project-detail-page")) return reloadProjectDetailData();
  if (qs("#projects-page"))       return renderProjectsPage();
  return Promise.resolve();
}

function showDetailError(message) {
  showToast(message);
  const errorEl = qs("#detail-error");
  if (!errorEl) return;
  errorEl.textContent = message;
  show(errorEl);
}

function clearDetailError() {
  const errorEl = qs("#detail-error");
  if (!errorEl) return;
  errorEl.textContent = "";
  hide(errorEl);
}

function readRequired(form, name) {
  const field = form.elements[name];
  const value = field ? field.value.trim() : "";
  if (!value) {
    throw new Error(uiText`${name} 不能为空`);
  }
  return value;
}

// --- delete / recycle-bin actions ------------------------------------------
// The dashboard uses native dialogs throughout, so deletion confirmation does
// too: a confirm() for the reversible soft delete, and a type-the-name prompt()
// as the second confirmation for the irreversible purge.
function confirmSoftDelete(label) {
  return window.confirm(uiText`删除「${label}」？\n会移入回收站，之后可以恢复。`);
}

function confirmHardDelete(label) {
  return window.confirm(uiText`删除「${label}」？\n此操作会直接永久删除，无法恢复。`);
}

function confirmPurge(label) {
  const typed = window.prompt(
    uiText`永久删除不可恢复，且会一并删除其下所有关联数据。\n请输入名称「${label}」以确认：`
  );
  return typed !== null && typed.trim() === label;
}

// Button that runs an async op then refreshes via `reload`. A handler may
// return false to abort (e.g. the user cancelled a dialog) without reloading.
function opButton(label, className, handler, reload, onError) {
  const button = el("button", className, label);
  button.type = "button";
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const ran = await handler();
      if (ran !== false) await reload();
    } catch (err) {
      onError(`API error: ${err.message}`);
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

function deleteButton(label, basePath, reload, onError) {
  return opButton(
    uiText("删除"),
    "action-button danger",
    async () => {
      if (!confirmSoftDelete(label)) return false;
      await apiJson(basePath, { method: "DELETE" });
    },
    reload,
    onError
  );
}

function restoreButton(basePath, reload, onError) {
  return opButton(
    uiText("恢复"),
    "action-button",
    () => apiJson(`${basePath}/restore`, jsonOptions("POST")),
    reload,
    onError
  );
}

function purgeButton(label, basePath, reload, onError) {
  return opButton(
    uiText("永久删除"),
    "action-button danger",
    async () => {
      if (!confirmPurge(label)) return false;
      await apiJson(`${basePath}/purge`, { method: "DELETE" });
    },
    reload,
    onError
  );
}

function taskProgress(tasks) {
  const total = tasks.length;
  const completed = tasks.filter((task) => task.status === "completed").length;
  const percent = total ? Math.round((completed * 100) / total) : 0;
  return { total, completed, percent };
}

function setStatus(statusEl, message, className = "loading") {
  statusEl.className = className;
  statusEl.textContent = message;
}

function makeTable(headers, rows) {
  const table = el("table", "grid");
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  headers.forEach((header) => headRow.appendChild(el("th", "", header)));
  thead.appendChild(headRow);

  const tbody = document.createElement("tbody");
  rows.forEach((cells) => {
    const row = document.createElement("tr");
    cells.forEach((cell) => {
      const td = document.createElement("td");
      if (cell instanceof Node) {
        td.appendChild(cell);
      } else {
        td.textContent = textOr(cell);
      }
      row.appendChild(td);
    });
    tbody.appendChild(row);
  });

  table.append(thead, tbody);
  const scroll = el("div", "table-scroll");
  scroll.appendChild(table);
  return scroll;
}

function appendEmpty(container, message) {
  clear(container);
  container.appendChild(el("p", "empty", message));
}

function renderProgressCell(progress) {
  const cell = el("div", "progress-wrap");
  const bar = el("div", "progress");
  const fill = el("div", "progress-bar");
  fill.style.width = `${progress.percent}%`;
  bar.appendChild(fill);
  cell.append(bar, el("span", "progress-pct", `${progress.percent}%`));
  return cell;
}

async function renderProjectsPage() {
  const page = qs("#projects-page");
  if (!page) return;

  const statusEl = qs("#projects-status", page);
  const table = qs("#projects-table", page);
  const tbody = qs("#projects-body", page);

  try {
    setStatus(statusEl, uiText("正在加载项目..."));
    const projects = await apiJson("/projects");
    if (!projects.length) {
      hide(table);
      setStatus(statusEl, uiText("还没有项目。"), "empty");
      return;
    }

    const rows = await Promise.all(
      projects.map(async (project) => ({
        project,
        tasks: await apiJson(`/projects/${project.id}/tasks`),
      }))
    );

    clear(tbody);
    rows.forEach(({ project, tasks }) => {
      const progress = taskProgress(tasks);
      const row = document.createElement("tr");

      const numberCell = el("td", "");
      numberCell.append(numberLink(project.project_number, `/ui/projects/${project.id}`));

      const nameCell = el("td", "name");
      const link = el("a", "", project.name);
      link.href = `/ui/projects/${project.id}`;
      nameCell.appendChild(link);

      const statusCell = document.createElement("td");
      statusCell.appendChild(statusBadge(project.status));

      const tasksCell = el("td", "num", `${progress.completed} / ${progress.total}`);

      const progressCell = el("td", "progress-cell");
      progressCell.appendChild(renderProgressCell(progress));

      const actionsCell = el("td", "actions-cell");
      const cellActions = el("div", "actions");
      const editBtn = el("button", "action-button", uiText("编辑"));
      editBtn.type = "button";
      editBtn.addEventListener("click", () => openEditProjectModal(project));
      cellActions.append(
        editBtn,
        deleteButton(project.name, `/projects/${project.id}`, renderProjectsPage, showToast)
      );
      actionsCell.appendChild(cellActions);

      row.append(numberCell, nameCell, statusCell, tasksCell, progressCell, actionsCell);
      tbody.appendChild(row);
    });

    hide(statusEl);
    show(table);
    await renderProjectsTrash();
  } catch (err) {
    hide(table);
    setStatus(statusEl, uiText`加载项目失败：${err.message}`, "error");
  }
}

async function renderProjectsTrash() {
  const section = qs("#projects-trash");
  if (!section) return;
  const deleted = await apiJson("/projects/trash");
  if (!deleted.length) {
    clear(section);
    hide(section);
    return;
  }
  clear(section);
  section.append(
    el("h2", null, uiText("回收站")),
    el("p", "trash-hint", uiText("已删除的项目保留在此，可恢复或永久删除（永久删除会一并清除项目下所有数据）。")),
    makeTable(
      [uiText("项目"), uiText("删除时间"), uiText("操作")],
      deleted.map((project) => {
        const base = `/projects/${project.id}`;
        const actions = el("div", "actions");
        actions.append(
          restoreButton(base, renderProjectsPage, showToast),
          purgeButton(project.name, base, renderProjectsPage, showToast)
        );
        return [project.name, shortTime(project.deleted_at), actions];
      })
    )
  );
  show(section);
}

function initCreateProjectForm() {
  const btn = qs("#create-project-btn");
  if (!btn) return;
  btn.addEventListener("click", openCreateProjectModal);
}

function renderProjectHeader(project, tasks) {
  const progress = taskProgress(tasks);
  qs("#project-number").textContent = project.project_number;
  qs("#project-name").textContent = project.name;
  qs("#project-status").replaceChildren(statusBadge(project.status));

  const description = qs("#project-description");
  if (project.description) {
    renderMarkdown(description, project.description);
    show(description);
  } else {
    hide(description);
  }

  qs("#project-summary").textContent =
    uiText`开发任务 ${progress.completed} / ${progress.total} 完成（${progress.percent}%）`;
  qs("#tasks-total").textContent = tasks.length;
}

// --- design confirm prompt chain (shared by project detail + design cases list)
function collectDesignConfirmPayload(design) {
  // Only prompt for the confirm-required fields that are still empty; fields
  // already filled (e.g. by the AI) are left untouched, so a complete design
  // confirms without any dialog. Returns null if the user cancels a prompt.
  const payload = {};
  for (const [field, label] of DESIGN_CONFIRM_FIELDS) {
    if (textOr(design[field], "").trim()) continue;
    const value = window.prompt(uiText`${label}（确认必填，当前为空）`, "");
    if (value === null) return null;
    payload[field] = value.trim();
  }
  return payload;
}

// --- task creation prompt chain (shared by the tasks list "新建任务" entry)
function collectTaskPayload() {
  const title = window.prompt(uiText("任务标题"));
  if (title === null) return null;
  const taskType = window.prompt(uiText("任务类型"), "feature");
  if (taskType === null) return null;
  const goal = window.prompt(uiText("任务目标"));
  if (goal === null) return null;
  return {
    title: title.trim(),
    task_type: taskType.trim(),
    goal: goal.trim(),
  };
}

function shortTime(value) {
  return value ? String(value).replace("T", " ").slice(0, 16) : "-";
}

// Per-entity recycle bin. Each list page renders its *own* deleted rows from
// that entity's `/trash` endpoint — trash is no longer aggregated on the project
// page. `base(id)` is the entity's REST base (for restore/purge); `label(item)`
// is the display name; `reload` re-fetches the page (list + this trash) after an
// action. The section stays hidden when the bin is empty.
async function renderEntityTrash(section, { url, base, label, reload, view }) {
  if (!section) return;
  let items;
  try {
    items = await apiJson(url);
  } catch (err) {
    clear(section);
    show(section);
    section.append(el("h2", null, uiText("回收站")), el("p", "error", uiText`回收站加载失败：${err.message}`));
    return;
  }

  clear(section);
  if (!items.length) {
    hide(section);
    return;
  }
  show(section);
  section.append(
    el("h2", null, uiText`回收站 · ${items.length}`),
    el("p", "trash-hint", uiText("已删除的条目保留在此，点击标题可查看内容，可恢复或永久删除（永久删除会一并清除其下关联数据）。")),
    makeTable(
      [uiText("条目"), uiText("删除时间"), uiText("操作")],
      items.map((item) => {
        const itemBase = base(item.id);
        // The deleted row's GET endpoint 404s, so view it read-only from the
        // data we already have in the trash list.
        const titleCell = el("button", "linklike", textOr(label(item)));
        titleCell.type = "button";
        if (view) {
          titleCell.addEventListener("click", () =>
            openTrashViewModal(textOr(label(item)), view(item)));
        }
        const actions = el("div", "actions");
        actions.append(
          restoreButton(itemBase, reload, showToast),
          purgeButton(label(item), itemBase, reload, showToast)
        );
        return [titleCell, shortTime(item.deleted_at), actions];
      })
    )
  );
}

// Read-only modal for a soft-deleted item: render the fields we already have
// (the deleted row's own GET endpoint returns 404). `fields` is [[label, value]].
function openTrashViewModal(title, fields) {
  const modal = qs("#trash-view-modal");
  if (!modal) return;
  qs("#trash-view-title").textContent = title;
  const body = qs("#trash-view-body");
  clear(body);
  const shown = fields.filter(([, value]) => value != null && String(value).trim() !== "");
  if (!shown.length) {
    body.appendChild(el("p", "empty", uiText("（无可显示内容）")));
  } else {
    shown.forEach(([fieldLabel, value]) => {
      const field = el("div", "trash-view-field");
      field.appendChild(el("h4", null, fieldLabel));
      const content = el("div");
      renderMarkdown(content, value);
      field.appendChild(content);
      body.appendChild(field);
    });
  }
  show(modal);
}

function closeTrashViewModal() {
  const modal = qs("#trash-view-modal");
  if (modal) hide(modal);
}

function initTrashViewModal() {
  const modal = qs("#trash-view-modal");
  if (!modal) return;
  const closeBtn = qs("#trash-view-close");
  if (closeBtn) closeBtn.addEventListener("click", closeTrashViewModal);
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeTrashViewModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeTrashViewModal();
  });
}

function renderProjectDetailState() {
  const pid = detailState.projectId;
  renderProjectFlowNav(pid, "project");
  renderProjectHeader(detailState.project, detailState.tasks);

  // Set links and counts for each sub-feature
  qs("#inputs-list-link").href = `/ui/projects/${pid}/source-requirements`;
  qs("#constitution-link").href = `/ui/projects/${pid}/constitution`;
  qs("#features-list-link").href = `/ui/projects/${pid}/features`;
  qs("#designs-list-link").href = `/ui/projects/${pid}/design-cases`;
  qs("#tasks-list-link").href = `/ui/projects/${pid}/tasks`;
  qs("#glossary-link").href = `/ui/projects/${pid}/glossary`;
  qs("#integration-list-link").href = `/ui/projects/${pid}/integration`;
  qs("#system-list-link").href = `/ui/projects/${pid}/system`;

  qs("#inputs-total").textContent = detailState.inputs.length;
  qs("#features-total").textContent = detailState.features.length;
  qs("#designs-total").textContent = detailState.designs.length;
  qs("#glossary-total").textContent = detailState.glossary.length;

  // The constitution is a real gate — design cannot start until it is
  // confirmed — so show its lifecycle state, not a count. The node only marks
  // presence: a project keeps iterating and the constitution can be reopened at
  // any time, so no stage is ever "done" and the badge is the honest signal.
  const constitution = detailState.constitution;
  const status = constitution ? constitution.status : null;
  const summaryEl = qs("#constitution-summary");
  if (status) {
    summaryEl.replaceChildren(statusBadge(status));
  } else {
    summaryEl.textContent = uiText("尚未汇总");
  }
  setStageState("constitution-stage", status ? "filled" : "");

  setStageState("features-stage", detailState.features.length ? "filled" : "");
  setStageState("designs-stage", detailState.designs.length ? "filled" : "");
  setStageState("kanban-stage", detailState.tasks.length ? "filled" : "");
  renderDeliveryGates();
}

// The two gates have no entity and no page, so this band is the only place a
// human can see that a project owes them. "尚未到达" is not the same answer as
// "不欠" and must not render the same: the first means capability work is still
// in flight, the second means the gate was met — collapsing them is how a
// product ships that nobody noticed had never been assembled.
//
// The gate itself only compares timestamps; it says nothing about whether the
// DeliveryItem checklist underneath it is actually settled (see
// docs/cn/design-evolution.md#delivery — folding the checklist into
// the gate was deliberately rejected). So this band also surfaces each
// phase's open-item count, sourced from delivery_item_service.counts_by_kind,
// as its own line — a project can read "gate closed" and "3 items nobody
// resolved" at the same time, and both are true.
const DELIVERY_STAGE_KINDS = {
  integration: ["assembly", "seam", "duplicate"],
  system: ["journey", "nfr"],
};

function renderDeliveryGates() {
  const gate = detailState.deliveryGate;
  const counts = detailState.deliveryItemCounts || {};
  const rows = [
    ["integration", "integration-stage", "integration-summary", "integration_owed"],
    ["system", "system-stage", "system-summary", "system_owed"],
  ];
  rows.forEach(([phase, stageId, summaryId, owedKey]) => {
    const summaryEl = qs(`#${summaryId}`);
    const openCountEl = qs(`#${phase}-delivery-open`);
    if (openCountEl) {
      const open = DELIVERY_STAGE_KINDS[phase].reduce(
        (sum, kind) => sum + ((counts[kind] || {}).open || 0), 0
      );
      openCountEl.textContent = String(open);
    }
    if (!summaryEl) return;
    if (!gate) {
      summaryEl.textContent = uiText("尚未到达");
      setStageState(stageId, "");
      return;
    }
    if (gate[owedKey]) {
      summaryEl.replaceChildren(statusBadge(uiText("欠着")));
      setStageState(stageId, "attention");
      return;
    }
    const everRan = owedKey === "integration_owed"
      ? gate.last_integration_completion
      : gate.last_system_completion;
    summaryEl.textContent = everRan ? uiText("已通过") : uiText("尚未到达");
    setStageState(stageId, everRan ? "filled" : "");
  });
}

async function reloadProjectDetailData() {
  if (!detailState.projectId) return;
  clearDetailError();

  const [project, inputs, features, tasks] = await Promise.all([
    apiJson(`/projects/${detailState.projectId}`),
    apiJson(`/projects/${detailState.projectId}/source-requirements`),
    apiJson(`/projects/${detailState.projectId}/features`).catch(() => []),
    apiJson(`/projects/${detailState.projectId}/tasks`),
  ]);

  let designs = [];
  try { designs = await apiJson(`/projects/${detailState.projectId}/design-cases`); } catch (_) {}

  // Rail extras: absent/empty is a normal state for each of these (a project
  // with no constitution yet is not an error), so a failure degrades to the
  // empty rendering rather than breaking the whole page.
  const [constitution, glossary, deliveryGate, deliveryItemCounts] = await Promise.all([
    apiJson(`/projects/${detailState.projectId}/constitution`).catch(() => null),
    apiJson(`/projects/${detailState.projectId}/glossary-terms`).catch(() => []),
    apiJson(`/projects/${detailState.projectId}/delivery-gate`).catch(() => null),
    apiJson(`/projects/${detailState.projectId}/delivery-items/counts`).catch(() => ({})),
  ]);

  detailState.project = project;
  detailState.inputs = inputs;
  detailState.features = features;
  detailState.designs = designs;
  detailState.tasks = tasks;
  detailState.constitution = constitution;
  detailState.glossary = glossary;
  detailState.deliveryGate = deliveryGate;
  detailState.deliveryItemCounts = deliveryItemCounts;
  renderProjectDetailState();
}

function initProjectDetailForms() {
  const page = qs("#project-detail-page");
  if (!page) return;

  const editBtn = qs("#edit-project-btn", page);
  if (editBtn) {
    editBtn.addEventListener("click", () => {
      openEditProjectModal(detailState.project);
    });
  }
  initAgentConfigDownload();
}

function initAgentConfigDownload() {
  const button = qs("#download-agent-config-btn");
  const modal = qs("#agent-config-modal");
  const form = qs("#agent-config-form");
  if (!button || !modal || !form || form.dataset.initialized) return;
  form.dataset.initialized = "true";
  const close = () => {
    hide(modal);
    qs("#agent-config-error").textContent = "";
  };
  button.addEventListener("click", () => show(modal));
  qs("#agent-config-cancel").addEventListener("click", close);
  modal.addEventListener("click", (event) => {
    if (event.target === modal) close();
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const error = qs("#agent-config-error");
    const submit = qs("#agent-config-submit");
    error.textContent = "";
    submit.disabled = true;
    try {
      const flavor = form.elements.flavor.value;
      let url = `/projects/${detailState.projectId}/agent-template?flavor=${encodeURIComponent(flavor)}`;
      const model = (form.elements.model.value || "").trim();
      if (model) url += `&model=${encodeURIComponent(model)}`;
      const response = await fetch(url);
      if (!response.ok) {
        let message = uiText("下载失败");
        try {
          message = (await response.json()).error || message;
        } catch (_) {
          /* non-JSON error body */
        }
        throw new Error(message);
      }
      const blob = await response.blob();
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `harnoq-${detailState.project.project_number || "project"}-${flavor}-agent-config.zip`;
      link.click();
      URL.revokeObjectURL(link.href);
      close();
    } catch (err) {
      error.textContent = err.message || uiText("下载失败");
    } finally {
      submit.disabled = false;
    }
  });
}

async function renderProjectDetailPage() {
  const page = qs("#project-detail-page");
  if (!page) return;

  detailState.projectId = page.dataset.projectId;
  const statusEl = qs("#detail-status", page);
  const content = qs("#detail-content", page);
  initProjectDetailForms();

  try {
    setStatus(statusEl, uiText("正在加载项目..."));
    await reloadProjectDetailData();

    hide(statusEl);
    show(content);
  } catch (err) {
    hide(content);
    setStatus(statusEl, uiText`加载项目失败：${err.message}`, "error");
  }
}

const requirementInputsListState = {
  projectId: null,
  inputs: [],
};

function showRilError(message) {
  showToast(message);
  const errorEl = qs("#ril-error");
  if (!errorEl) return;
  errorEl.textContent = message;
  show(errorEl);
}

function clearRilError() {
  const errorEl = qs("#ril-error");
  if (!errorEl) return;
  errorEl.textContent = "";
  hide(errorEl);
}

// --- source requirement modal -----------------------------------------------
const _rilModal = createModalManager({
  overlayId: "#ril-modal",
  createTitle: uiText("添加原始需求"),
  editTitle: uiText("编辑原始需求"),
  focusSelector: "#ril-title",
  errorSelector: "#ril-modal-error",
  submitSelector: "#ril-modal-submit",
  cancelSelector: "#ril-modal-cancel",
  getPayload(form) {
    const title = form.elements.title.value.trim();
    if (!title) {
      form.querySelector("#ril-modal-error").textContent = uiText("标题不能为空");
      return null;
    }
    return {
      title,
      source_type: form.elements.source_type.value,
      source: form.elements.source.value.trim(),
      raw_content: form.elements.raw_content.value,
      assignee_number: form.elements.assignee_number.value || null,
    };
  },
  setFormData(form, item) {
    form.elements.title.value = item.title || "";
    form.elements.source_type.value = item.source_type || "initial_document";
    form.elements.source.value = item.source || "";
    form.elements.raw_content.value = item.raw_content || "";
    populateUserAssigneeSelect(qs("#ril-assignee"), item, workAssigneeUsers);
  },
  submit(mode, payload, editId) {
    if (mode === "edit") {
      return apiJson(`/source-requirements/${editId}`, jsonOptions("PATCH", payload));
    }
    return apiJson(`/projects/${requirementInputsListState.projectId}/source-requirements`, jsonOptions("POST", payload));
  },
  onSuccess() {
    reloadSourceRequirementsListData();
  },
});

const _rilOpenCreate = _rilModal.openCreate.bind(_rilModal);
_rilModal.openCreate = function () {
  _rilOpenCreate();
  populateUserAssigneeSelect(qs("#ril-assignee"), { assignee_number: window.harnoqUser?.user_number }, workAssigneeUsers);
};

function renderSourceRequirementsListState() {
  const inputs = requirementInputsListState.inputs;
  qs("#ril-count").textContent = inputs.length;
  const section = qs("#ril-table-section");
  if (!inputs.length) {
    appendEmpty(section, uiText("暂无原始需求。"));
    return;
  }

  clear(section);
  section.appendChild(
    makeTable(
      [uiText("编号"), uiText("标题"), uiText("类型"), uiText("负责人"), uiText("状态"), uiText("操作")],
      inputs.map((item) => {
        const link = el("a", "", item.title);
        link.href = `/ui/source-requirements/${item.id}`;
        const actions = el("div", "actions");
        const basePath = `/source-requirements/${item.id}`;
        if (window.harnoqUser?.role === "admin") {
          actions.appendChild(opButton(uiText("分配"), "action-button", () => window.harnoqAssignWork(item, `${basePath}/assignee`, reloadSourceRequirementsListData), () => Promise.resolve(), showRilError));
        }
        if (isEditableGateStatus(item.status)) {
          const editBtn = el("button", "action-button", uiText("编辑"));
          editBtn.type = "button";
          editBtn.addEventListener("click", () => _rilModal.openEdit(item));
          actions.appendChild(editBtn);
          if (item.status === "draft") {
            actions.appendChild(
              opButton(
                uiText("需澄清"),
                "action-button",
                () => apiJson(basePath, jsonOptions("PATCH", { status: "needs_clarification" })),
                reloadSourceRequirementsListData,
                showRilError
              )
            );
          } else {
            actions.appendChild(
              opButton(
                uiText("回到草稿"),
                "action-button",
                () => apiJson(basePath, jsonOptions("PATCH", { status: "draft" })),
                reloadSourceRequirementsListData,
                showRilError
              )
            );
          }
          actions.appendChild(
            opButton(
              uiText("确认"),
              "action-button",
              () => apiJson(`${basePath}/confirm`, jsonOptions("POST")),
              reloadSourceRequirementsListData,
              showRilError
            )
          );
        } else if (item.status === "confirmed") {
          actions.appendChild(
            opButton(
              uiText("重新打开"),
              "action-button",
              () => apiJson(`${basePath}/reopen`, jsonOptions("POST")),
              reloadSourceRequirementsListData,
              showRilError
            )
          );
        }
        actions.appendChild(
          deleteButton(item.title, basePath, reloadSourceRequirementsListData, showRilError)
        );
        return [
          numberLink(item.source_requirement_number, `/ui/source-requirements/${item.id}`),
          link,
          chip(item.source_type),
          userAssigneeLabel(item),
          statusBadge(item.status),
          actions,
        ];
      })
    )
  );
}

async function reloadSourceRequirementsListData() {
  if (!requirementInputsListState.projectId) return;
  clearRilError();
  requirementInputsListState.inputs = await apiJson(
    `/projects/${requirementInputsListState.projectId}/source-requirements`
  );
  renderSourceRequirementsListState();
  await renderEntityTrash(qs("#ril-trash"), {
    url: `/projects/${requirementInputsListState.projectId}/source-requirements/trash`,
    base: (id) => `/source-requirements/${id}`,
    label: (x) => x.title,
    reload: reloadSourceRequirementsListData,
    view: (x) => [[uiText("类型"), x.source_type], [uiText("来源"), x.source], [uiText("需求原文"), x.raw_content]],
  });
}

async function renderSourceRequirementsListPage() {
  const page = qs("#source-requirements-list-page");
  if (!page) return;

  requirementInputsListState.projectId = page.dataset.projectId;
  renderProjectFlowNav(requirementInputsListState.projectId, "inputs");
  const statusEl = qs("#ril-status", page);
  const content = qs("#ril-content", page);
  _rilModal.init();

  const createBtn = qs("#ril-create-btn", page);
  if (createBtn) createBtn.addEventListener("click", () => _rilModal.openCreate());
  if (createBtn) createBtn.disabled = true;

  try {
    setStatus(statusEl, uiText("正在加载原始需求..."));
    workAssigneeUsers = await apiJson("/users");
    await reloadSourceRequirementsListData();

    hide(statusEl);
    show(content);
    if (createBtn) createBtn.disabled = false;
  } catch (err) {
    hide(content);
    setStatus(statusEl, uiText`加载原始需求失败：${err.message}`, "error");
  }
}

// --- glossary list page ----------------------------------------------------

const glossaryListState = { projectId: null, terms: [] };

function showGlError(message) {
  showToast(message);
  const errorEl = qs("#gl-error");
  if (!errorEl) return;
  errorEl.textContent = message;
  show(errorEl);
}

function clearGlError() {
  const errorEl = qs("#gl-error");
  if (!errorEl) return;
  errorEl.textContent = "";
  hide(errorEl);
}

const _glModal = createModalManager({
  overlayId: "#gl-modal",
  createTitle: uiText("添加术语"),
  editTitle: uiText("编辑术语"),
  focusSelector: "#gl-term-native",
  errorSelector: "#gl-modal-error",
  submitSelector: "#gl-modal-submit",
  cancelSelector: "#gl-modal-cancel",
  getPayload(form) {
    const term_native = form.elements.term_native.value.trim();
    const term_en = form.elements.term_en.value.trim();
    if (!term_native || !term_en) {
      form.querySelector("#gl-modal-error").textContent = uiText("母语词和英文不能为空");
      return null;
    }
    return {
      term_native,
      term_en,
      definition_en: form.elements.definition_en.value,
      avoid: form.elements.avoid.value,
    };
  },
  setFormData(form, item) {
    form.elements.term_native.value = item.term_native || "";
    form.elements.term_en.value = item.term_en || "";
    form.elements.definition_en.value = item.definition_en || "";
    form.elements.avoid.value = item.avoid || "";
  },
  submit(mode, payload, editId) {
    if (mode === "edit") {
      return apiJson(`/glossary-terms/${editId}`, jsonOptions("PATCH", payload));
    }
    return apiJson(
      `/projects/${glossaryListState.projectId}/glossary-terms`,
      jsonOptions("POST", payload)
    );
  },
  onSuccess() {
    reloadGlossaryListData();
  },
});

function renderGlossaryListState() {
  const section = qs("#gl-table-section");
  if (!section) return;
  qs("#gl-count").textContent = glossaryListState.terms.length;
  if (!glossaryListState.terms.length) {
    clear(section);
    appendEmpty(
      section,
      uiText("暂无术语。这里只拦会被写错的词：先写出下游真会写出来的那句错话，错词填进「会写错的词」，通常是被一词两用的泛称。两个各有各名字的相邻概念不算混淆，不必入表。")
    );
    return;
  }
  clear(section);
  section.appendChild(makeTable(
    [uiText("母语词"), uiText("英文"), uiText("解释"), uiText("会写错的词"), uiText("操作")],
    glossaryListState.terms.map((term) => {
      const actions = el("div", "actions");
      const editBtn = el("button", "action-button", uiText("编辑"));
      editBtn.type = "button";
      editBtn.addEventListener("click", () => _glModal.openEdit(term));
      actions.appendChild(editBtn);
      actions.appendChild(opButton(
        uiText("删除"), "action-button danger",
        async () => {
          if (!window.confirm(uiText`删除术语「${term.term_native}」？`)) return false;
          await apiJson(`/glossary-terms/${term.id}`, { method: "DELETE" });
        },
        reloadGlossaryListData, showGlError
      ));
      return [
        term.term_native,
        term.term_en,
        previewText(term.definition_en, 220),
        previewText(term.avoid, 160),
        actions,
      ];
    })
  ));
}

async function reloadGlossaryListData() {
  if (!glossaryListState.projectId) return;
  clearGlError();
  glossaryListState.terms = await apiJson(
    `/projects/${glossaryListState.projectId}/glossary-terms`
  );
  renderGlossaryListState();
}

async function renderGlossaryPage() {
  const page = qs("#glossary-page");
  if (!page) return;
  glossaryListState.projectId = page.dataset.projectId;
  renderProjectFlowNav(glossaryListState.projectId, "glossary");
  _glModal.init();
  const createBtn = qs("#gl-create-btn", page);
  if (createBtn) createBtn.addEventListener("click", () => _glModal.openCreate());
  const statusEl = qs("#gl-status", page);
  const content = qs("#gl-content", page);
  try {
    setStatus(statusEl, uiText("正在加载术语表..."));
    await reloadGlossaryListData();
    hide(statusEl);
    show(content);
  } catch (err) {
    hide(content);
    setStatus(statusEl, uiText`加载术语表失败：${err.message}`, "error");
  }
}

// --- requirement digest page -----------------------------------------------
// Read-only. The requirement lives in each design's mined_requirements; this
// page derives the project-wide view from those and stores nothing, so it can
// never drift from the designs the way a second copy would.

const REQUIREMENT_MARK_CLASS = {
  "用户确认": "mark-confirmed",
  "我补的(已认可)": "mark-supplied",
  "待确认": "mark-pending",
  "未标记": "mark-unmarked",
};

function requirementMarkBadge(mark) {
  return el("span", `req-mark ${REQUIREMENT_MARK_CLASS[mark] || ""}`, uiText(mark));
}

// A confirmed design with nothing recorded is why a zero on this page is
// ambiguous — it reads the same as "nobody asked for anything here". Naming
// those designs is what tells the two apart.
function renderRequirementUnrecorded(target, digest) {
  const numbers = digest.unrecorded_design_numbers || [];
  target.replaceChildren();
  if (!numbers.length) {
    hide(target);
    return;
  }
  const lead = el("strong", "", uiText`${numbers.length} 份已确认的设计没有记录需求`);
  target.appendChild(lead);
  target.appendChild(el(
    "span", "",
    uiText("：它们在下面显示为 0 条,但那个 0 的意思是没写,不是没有。")
  ));
  const list = el("div", "rd-unrecorded-list");
  numbers.forEach((number) => {
    const design = findDigestDesign(digest, number);
    const link = el("a", "", number);
    if (design) link.href = `/ui/design-cases/${design.design_id}`;
    list.appendChild(link);
  });
  target.appendChild(list);
  show(target);
}

function findDigestDesign(digest, designNumber) {
  for (const feature of digest.features || []) {
    for (const design of feature.designs || []) {
      if (design.design_number === designNumber) return design;
    }
  }
  return null;
}

function renderRequirementSummary(target, digest) {
  target.replaceChildren();
  const order = ["用户确认", "我补的(已认可)", "待确认", "未标记"];
  order.forEach((mark) => {
    const count = (digest.counts && digest.counts[mark]) || 0;
    const chip = el("span", `req-summary-chip ${REQUIREMENT_MARK_CLASS[mark]}`);
    chip.appendChild(el("strong", "", String(count)));
    chip.appendChild(el("span", "", ` ${uiText(mark)}`));
    target.appendChild(chip);
  });
}

function renderRequirementFeatures(target, digest) {
  target.replaceChildren();
  (digest.features || []).forEach((feature) => {
    const section = el("section", "req-feature");
    const heading = el("h2", "req-feature-title");
    heading.appendChild(el("span", "req-feature-number", feature.feature_number));
    heading.appendChild(el("span", "req-feature-name", feature.feature_id == null ? uiText(feature.feature_name) : feature.feature_name));
    heading.appendChild(el("span", "count", String(feature.total)));
    section.appendChild(heading);

    if (!feature.designs.length) {
      section.appendChild(el("p", "empty", uiText("这个功能还没有设计用例。")));
      target.appendChild(section);
      return;
    }

    feature.designs.forEach((design) => {
      const block = el("div", "req-design");
      const label = el("div", "req-design-head");
      const link = el("a", "", design.design_number);
      link.href = `/ui/design-cases/${design.design_id}`;
      label.appendChild(link);
      label.appendChild(statusBadge(design.status));
      block.appendChild(label);

      if (design.empty) {
        block.appendChild(el(
          "p",
          design.unrecorded ? "rd-unrecorded-note" : "empty",
          design.unrecorded
            ? uiText("已确认,但没有记录需求 —— 这个 0 是没写,不是没有。")
            : uiText("这份设计还没有记录需求。")
        ));
      } else {
        const list = el("ul", "req-list");
        design.requirements.forEach((item) => {
          const row = el("li", "req-item");
          row.appendChild(requirementMarkBadge(item.mark));
          row.appendChild(el("span", "req-text", item.text));
          list.appendChild(row);
        });
        block.appendChild(list);
      }
      section.appendChild(block);
    });
    target.appendChild(section);
  });
}

async function renderRequirementDigestPage() {
  const page = qs("#requirement-digest-page");
  if (!page) return;
  const projectId = page.dataset.projectId;
  // Reached from the design list, not from the flow itself: this is a readout
  // of the designs, not a stage of its own, so the nav stays on 设计用例.
  renderProjectFlowNav(projectId, "designs");
  const statusEl = qs("#rd-status", page);
  const content = qs("#rd-content", page);
  try {
    setStatus(statusEl, uiText("正在加载需求汇总..."));
    const digest = await apiJson(`/projects/${projectId}/requirement-digest`);
    qs("#rd-count", page).textContent = String(digest.total || 0);
    renderRequirementUnrecorded(qs("#rd-unrecorded", page), digest);
    renderRequirementSummary(qs("#rd-summary", page), digest);
    renderRequirementFeatures(qs("#rd-features", page), digest);
    hide(statusEl);
    show(content);
  } catch (err) {
    hide(content);
    setStatus(statusEl, uiText`加载需求汇总失败：${err.message}`, "error");
  }
}

// --- NFR coverage page -----------------------------------------------------

// The constitution's nfr_baseline binds the whole project and had no
// verification surface: a row nobody claims is a promise with no owner, and
// that is invisible until someone reads every design in turn. This page asks
// once. Derived from the designs' verifies_nfr — nothing here is stored.
function renderNfrRow(row) {
  const direction = row.strength === "direction";
  const card = el("div", `nfr-row ${row.unverified ? "nfr-row--unverified" : ""}`);
  const head = el("div", "nfr-row__head");
  head.appendChild(el("strong", "", row.metric));
  head.appendChild(el(
    "span", "status-badge", direction ? uiText("方向") : uiText("承诺")
  ));
  head.appendChild(el("span", "nfr-row__target", row.target || ""));
  card.appendChild(head);
  if (row.applies_to) {
    card.appendChild(el("div", "nfr-row__applies", uiText`适用于：${row.applies_to}`));
  }
  // A direction asserts no number, so nothing can be measured against it and an
  // unclaimed one is the expected state — flagging it would train people to
  // claim rows they cannot prove.
  if (direction && !(row.verified_by || []).length) {
    card.appendChild(el(
      "div", "nfr-row__applies", uiText("方向：约束设计取向，不要求认领，也不产生判据")
    ));
    return card;
  }
  if (row.unverified) {
    card.appendChild(el("div", "nfr-row__gap", uiText("没有任何设计认领验证这一条")));
    return card;
  }
  const list = el("div", "nfr-row__by");
  (row.verified_by || []).forEach((entry) => {
    const line = el("div", "nfr-row__design");
    const link = el("a", "", entry.design_number);
    link.href = `/ui/design-cases/${entry.design_id}`;
    line.appendChild(link);
    const tasks = (entry.tasks || [])
      .map((t) => `${t.task_number}:${t.status}`)
      .join("、");
    line.appendChild(el("span", "nfr-row__how", (entry.how || []).join(" / ")));
    if (tasks) line.appendChild(el("span", "nfr-row__tasks", tasks));
    list.appendChild(line);
  });
  card.appendChild(list);
  return card;
}

function renderNfrUnverified(target, data) {
  const metrics = data.unverified_metrics || [];
  target.replaceChildren();
  if (!metrics.length) {
    hide(target);
    return;
  }
  target.appendChild(el("strong", "", uiText`${metrics.length} 条承诺没有任何设计认领`));
  target.appendChild(el(
    "span", "",
    uiText("：项目规约承诺了,但没有设计负责证明它 —— 这不会在任何一次 qa 里被发现。")
  ));
  show(target);
}

// The mirror hole: a design claiming a commitment the constitution does not
// contain. Either the wording drifted or the commitment was never consolidated.
function renderNfrUnmatched(target, data) {
  const claims = data.unmatched_claims || [];
  target.replaceChildren();
  if (!claims.length) {
    hide(target);
    return;
  }
  target.appendChild(el("strong", "", uiText`${claims.length} 条认领对不上规约里的任何一行`));
  const list = el("div", "rd-unrecorded-list");
  claims.forEach((c) => list.appendChild(el("span", "", `${c.design_number}：${c.claim}`)));
  target.appendChild(list);
  show(target);
}

async function renderNfrCoveragePage() {
  const page = qs("#nfr-coverage-page");
  if (!page) return;
  const projectId = page.dataset.projectId;
  renderProjectFlowNav(projectId, "designs");
  const statusEl = qs("#nfr-status", page);
  const content = qs("#nfr-content", page);
  try {
    setStatus(statusEl, uiText("正在加载 NFR 覆盖..."));
    const data = await apiJson(`/projects/${projectId}/nfr-coverage`);
    const split = data.direction_count
      ? uiText`${data.total}（承诺 ${data.commitment_count} · 方向 ${data.direction_count}）`
      : String(data.total || 0);
    qs("#nfr-count", page).textContent = split;
    renderNfrUnverified(qs("#nfr-unverified", page), data);
    renderNfrUnmatched(qs("#nfr-unmatched", page), data);
    const rows = qs("#nfr-rows", page);
    rows.replaceChildren();
    (data.rows || []).forEach((row) => rows.appendChild(renderNfrRow(row)));
    hide(statusEl);
    show(content);
  } catch (err) {
    hide(content);
    setStatus(statusEl, uiText`加载 NFR 覆盖失败：${err.message}`, "error");
  }
}

// --- delivery items page (integration / system) -----------------------------
// One entity (DeliveryItem), two pages that differ only in which `kind`s they
// show — see docs/cn/design-evolution.md#delivery. The kind list for
// a given page is read off the server-rendered <select id="delivery-kind"> options
// rather than hardcoded here a second time, so the page's kind set has exactly
// one source of truth (app/web/views.py).

const DELIVERY_KIND_LABELS = {
  assembly: uiText("装配"),
  seam: uiText("接缝"),
  duplicate: uiText("重复"),
  journey: uiText("用户旅程"),
  nfr: uiText("NFR 承诺"),
};

// Mirrors app/services/delivery_item_service._DRIVER_KINDS: only these two
// kinds carry a driver, so the field is hidden (and never submitted) for the
// other three.
const DELIVERY_DRIVER_KINDS = new Set(["journey", "nfr"]);

const deliveryItemsState = { projectId: null, projectNumber: null, pageKind: null, kinds: [], items: [], features: [] };

function showDeliveryError(message) {
  showToast(message);
  const errorEl = qs("#delivery-error");
  if (!errorEl) return;
  errorEl.textContent = message;
  show(errorEl);
}

function clearDeliveryError() {
  const errorEl = qs("#delivery-error");
  if (!errorEl) return;
  errorEl.textContent = "";
  hide(errorEl);
}

// The one reason this page exists: an `open` item is not a stage waiting its
// turn, it is an account nobody has settled. The open count is the only
// number on this bar that gets bold/coral treatment — everything else here is
// bookkeeping, that one is the point.
function renderDeliveryCounts(target, counts, kinds) {
  target.replaceChildren();
  kinds.forEach((kind) => {
    const bucket = counts[kind] || { total: 0, open: 0, covered: 0, waived: 0 };
    const row = el("div", "delivery-counts-row");
    row.appendChild(el("span", "delivery-counts-label", DELIVERY_KIND_LABELS[kind] || kind));
    row.appendChild(el("span", "", uiText`声明 ${bucket.total} 条`));
    row.appendChild(el("span", "", uiText`· 已覆盖 ${bucket.covered}`));
    row.appendChild(el("span", "", uiText`· 豁免 ${bucket.waived}`));
    const openSpan = el("span", bucket.open ? "delivery-open-count" : "", uiText`· 待处理 ${bucket.open}`);
    row.appendChild(openSpan);
    target.appendChild(row);
  });
}

function updateDeliveryDriverFieldVisibility(form) {
  const field = form.querySelector("#delivery-driver-field");
  if (!field) return;
  const kind = form.elements.kind.value;
  if (DELIVERY_DRIVER_KINDS.has(kind)) {
    show(field);
  } else {
    hide(field);
    if (form.elements.driver) form.elements.driver.value = "";
  }
}

const _deliveryModal = createModalManager({
  overlayId: "#delivery-modal",
  createTitle: uiText("添加"),
  editTitle: uiText("编辑"),
  focusSelector: "#delivery-subject",
  errorSelector: "#delivery-modal-error",
  submitSelector: "#delivery-modal-submit",
  cancelSelector: "#delivery-modal-cancel",
  getPayload(form) {
    const subject = form.elements.subject.value.trim();
    if (!subject) {
      form.querySelector("#delivery-modal-error").textContent = uiText("内容（subject）不能为空");
      return null;
    }
    const kind = form.elements.kind.value;
    const status = form.elements.status.value;
    const coveredBy = form.elements.covered_by.value.trim();
    const waiveReason = form.elements.waive_reason.value.trim();
    if (status === "covered" && !coveredBy) {
      form.querySelector("#delivery-modal-error").textContent =
        uiText("status 为 covered 时请填写本项验证内容");
      return null;
    }
    if (status === "waived" && !waiveReason) {
      form.querySelector("#delivery-modal-error").textContent =
        uiText("status 为 waived 时 waive_reason 不能为空：写下谁决定不做、为什么");
      return null;
    }
    return {
      kind,
      subject,
      source: form.elements.source.value.trim(),
      driver: DELIVERY_DRIVER_KINDS.has(kind) ? form.elements.driver.value.trim() : "",
      status,
      covered_by: coveredBy,
      evidence_command: form.elements.evidence_command.value.trim(),
      evidence_output: form.elements.evidence_output.value.trim(),
      evidence_exit_code: form.elements.evidence_exit_code.value === "" ? null : Number(form.elements.evidence_exit_code.value),
      verified_revision: form.elements.verified_revision.value.trim(),
      repair_record: form.elements.repair_record.value.trim(),
      blocked_reason: form.elements.blocked_reason.value.trim(),
      waive_reason: waiveReason,
      found_in_round: parseInt(form.elements.found_in_round.value, 10) || 1,
      assignee_number: form.elements.assignee_number.value || null,
    };
  },
  setFormData(form, item) {
    form.elements.kind.value = item.kind || deliveryItemsState.kinds[0];
    form.elements.subject.value = item.subject || "";
    form.elements.source.value = item.source || "";
    form.elements.driver.value = item.driver || "";
    form.elements.status.value = item.status || "open";
    form.elements.covered_by.value = item.covered_by || "";
    form.elements.evidence_command.value = item.evidence_command || "";
    form.elements.evidence_output.value = item.evidence_output || "";
    form.elements.evidence_exit_code.value = item.evidence_exit_code ?? "";
    form.elements.verified_revision.value = item.verified_revision || "";
    form.elements.repair_record.value = item.repair_record || "";
    form.elements.blocked_reason.value = item.blocked_reason || "";
    form.elements.waive_reason.value = item.waive_reason || "";
    form.elements.found_in_round.value = item.found_in_round || 1;
    populateUserAssigneeSelect(qs("#delivery-assignee"), item, workAssigneeUsers);
    updateDeliveryDriverFieldVisibility(form);
  },
  submit(mode, payload, editId) {
    if (mode === "edit") {
      return apiJson(`/delivery-items/${editId}`, jsonOptions("PATCH", payload));
    }
    return apiJson(
      `/projects/${deliveryItemsState.projectId}/delivery-items`,
      jsonOptions("POST", payload)
    );
  },
  onSuccess() {
    reloadDeliveryItemsData();
  },
});

function deliveryStatusLine(item) {
  if (item.status === "covered") return item.covered_by || "-";
  if (item.status === "waived") return item.waive_reason || "-";
  return "-";
}

function renderDeliveryItemsTable(section, items) {
  if (!items.length) {
    appendEmpty(section, uiText("暂无记录。"));
    return;
  }
  clear(section);
  section.appendChild(makeTable(
    [uiText("编号"), uiText("类型"), uiText("内容"), uiText("负责人"), uiText("状态"), "covered_by / waive_reason", uiText("操作")],
    items.map((item) => {
      const actions = el("div", "actions");
      if (window.harnoqUser?.role === "admin") {
        actions.appendChild(opButton(uiText("分配"), "action-button", () => window.harnoqAssignWork(item, `/delivery-items/${item.id}/assignee`, reloadDeliveryItemsData), () => Promise.resolve(), showDeliveryError));
      }
      const editBtn = el("button", "action-button", uiText("编辑"));
      editBtn.type = "button";
      editBtn.addEventListener("click", () => _deliveryModal.openEdit(item));
      actions.appendChild(editBtn);
      actions.appendChild(opButton(
        uiText("删除"), "action-button danger",
        async () => {
          if (!window.confirm(uiText`删除「${item.item_number || item.subject}」？`)) return false;
          await apiJson(`/delivery-items/${item.id}`, { method: "DELETE" });
        },
        reloadDeliveryItemsData, showDeliveryError
      ));
      return [
        item.item_number || "-",
        DELIVERY_KIND_LABELS[item.kind] || item.kind,
        previewText(item.subject, 160),
        userAssigneeLabel(item),
        statusBadge(item.status),
        deliveryStatusLine(item),
        actions,
      ];
    })
  ));
}

async function reloadDeliveryItemsData() {
  if (!deliveryItemsState.projectId) return;
  clearDeliveryError();
  const kindQuery = deliveryItemsState.kinds.map((k) => `kind=${encodeURIComponent(k)}`).join("&");
  const [items, counts, runs] = await Promise.all([
    apiJson(`/projects/${deliveryItemsState.projectId}/delivery-items?${kindQuery}`),
    apiJson(`/projects/${deliveryItemsState.projectId}/delivery-items/counts`),
    apiJson(`/projects/${deliveryItemsState.projectNumber}/delivery-stage-runs?stage=${deliveryItemsState.pageKind}`),
  ]);
  deliveryItemsState.items = items;
  renderDeliveryCounts(qs("#delivery-counts"), counts, deliveryItemsState.kinds);
  renderDeliveryItemsTable(qs("#delivery-table-section"), items);
  const run = runs.findLast((row) => ["active", "blocked"].includes(row.status)) || runs.at(-1);
  const runInfo = qs("#delivery-run-info");
  if (run) {
    const states = { active: uiText("进行中"), blocked: uiText("已阻塞"), closed: uiText("已完成"), abandoned: uiText("已放弃") };
    runInfo.textContent = uiText`${run.run_number} · ${states[run.status] || run.status} · 负责人：${userAssigneeLabel(run)}`;
    show(runInfo);
  } else hide(runInfo);
}

async function renderDeliveryItemsPage() {
  const page = qs("#delivery-page");
  if (!page) return;
  deliveryItemsState.projectId = page.dataset.projectId;
  deliveryItemsState.projectNumber = page.dataset.projectNumber;
  deliveryItemsState.pageKind = page.dataset.pageKind;
  deliveryItemsState.kinds = qsa("#delivery-kind option", page).map((opt) => opt.value);
  renderProjectFlowNav(deliveryItemsState.projectId, deliveryItemsState.pageKind);
  _deliveryModal.init();
  const kindSelect = qs("#delivery-kind", page);
  if (kindSelect) {
    kindSelect.addEventListener("change", () => updateDeliveryDriverFieldVisibility(kindSelect.form));
  }
  const createBtn = qs("#delivery-create-btn", page);
  if (createBtn) {
    createBtn.addEventListener("click", () => {
      _deliveryModal.openCreate();
      populateUserAssigneeSelect(qs("#delivery-assignee"), { assignee_number: window.harnoqUser?.user_number }, workAssigneeUsers);
      if (kindSelect && kindSelect.form) updateDeliveryDriverFieldVisibility(kindSelect.form);
    });
  }
  const statusEl = qs("#delivery-load-status", page);
  const content = qs("#delivery-content", page);
  if (createBtn) createBtn.disabled = true;
  try {
    setStatus(statusEl, uiText("正在加载..."));
    workAssigneeUsers = await apiJson("/users");
    await reloadDeliveryItemsData();
    hide(statusEl);
    show(content);
    if (createBtn) createBtn.disabled = false;
  } catch (err) {
    hide(content);
    setStatus(statusEl, uiText`加载失败：${err.message}`, "error");
  }
}

// --- features list page ----------------------------------------------------

const featuresListState = {
  projectId: null,
  features: [],
  units: [],
};

function showFlError(message) {
  showToast(message);
  const errorEl = qs("#fl-error");
  if (!errorEl) return;
  errorEl.textContent = message;
  show(errorEl);
}

function clearFlError() {
  const errorEl = qs("#fl-error");
  if (!errorEl) return;
  errorEl.textContent = "";
  hide(errorEl);
}

function findFeatureById(featureId, features = featuresListState.features) {
  return (features || []).find((feature) => Number(feature.id) === Number(featureId)) || null;
}

function populateFeatureDependencySelect(selectedIds = [], editingFeatureId = null) {
  const select = qs("#fl-depends-on-feature-ids");
  if (!select) return;
  const selected = new Set((selectedIds || []).map((id) => Number(id)));
  select.innerHTML = "";
  (featuresListState.features || []).forEach((feature) => {
    if (Number(feature.id) === Number(editingFeatureId)) return; // no self-dependency
    const opt = document.createElement("option");
    opt.value = feature.id;
    opt.selected = selected.has(Number(feature.id));
    opt.textContent = `${feature.feature_number || `#${feature.id}`} ${feature.name}`;
    select.appendChild(opt);
  });
}

const _flModal = createModalManager({
  overlayId: "#fl-modal",
  createTitle: uiText("添加功能"),
  editTitle: uiText("编辑功能"),
  focusSelector: "#fl-name",
  errorSelector: "#fl-modal-error",
  submitSelector: "#fl-modal-submit",
  cancelSelector: "#fl-modal-cancel",
  getPayload(form) {
    const name = form.elements.name.value.trim();
    const orientation = form.elements.orientation.value;
    if (!name) {
      form.querySelector("#fl-modal-error").textContent = uiText("名称不能为空");
      return null;
    }
    return {
      name,
      description: form.elements.description.value,
      assignee_number: form.elements.assignee_number.value || null,
      orientation,
      foundation_kind: form.elements.foundation_kind.value || null,
      subdomain_type: form.elements.subdomain_type.value,
      is_spike: form.elements.is_spike.checked,
      depends_on_feature_ids: selectedValues(form.elements.depends_on_feature_ids),
    };
  },
  setFormData(form, item) {
    form.elements.name.value = item.name || "";
    form.elements.description.value = item.description || "";
    populateFeatureAssigneeSelect(item);
    form.elements.orientation.value = item.orientation || "vertical";
    form.elements.foundation_kind.value = item.foundation_kind || "";
    form.elements.subdomain_type.value = item.subdomain_type || "supporting";
    form.elements.is_spike.checked = Boolean(item.is_spike);
    populateFeatureDependencySelect(item.depends_on_feature_ids || [], item.id);
  },
  submit(mode, payload, editId) {
    if (mode === "edit") {
      return apiJson(`/features/${editId}`, jsonOptions("PATCH", payload));
    }
    return apiJson(`/projects/${featuresListState.projectId}/features`, jsonOptions("POST", payload));
  },
  async onSuccess() {
    await reloadFeaturesListData();
  },
});

const _flOpenCreateOrig = _flModal.openCreate.bind(_flModal);
const _flOpenEditOrig = _flModal.openEdit.bind(_flModal);
let featureAssigneeUsers = [];
let workAssigneeUsers = [];

function userAssigneeLabel(entity) {
  return entity.assignee_user?.display_name || entity.assignee || "—";
}

function featureAssigneeLabel(feature) {
  return userAssigneeLabel(feature);
}

function populateFeatureAssigneeSelect(feature = {}) {
  populateUserAssigneeSelect(qs("#fl-assignee"), feature, featureAssigneeUsers);
}

function populateUserAssigneeSelect(select, feature = {}, availableUsers = []) {
  select.replaceChildren();
  const unassigned = el("option", "", uiText("未分配"));
  unassigned.value = "";
  select.appendChild(unassigned);
  const users = [...availableUsers];
  if (feature.assignee_user && !users.some((user) => user.user_number === feature.assignee_number)) {
    users.push(feature.assignee_user);
  }
  users.filter((user) => user.is_active || user.user_number === feature.assignee_number).forEach((user) => {
    const option = el("option", "", user.display_name);
    option.value = user.user_number;
    option.disabled = !user.is_active || user.deleted;
    select.appendChild(option);
  });
  select.value = feature.assignee_number || "";
  select.disabled = window.harnoqUser?.role !== "admin";
}

_flModal.openCreate = function () {
  _flOpenCreateOrig();
  populateFeatureAssigneeSelect();
  populateFeatureDependencySelect();
};

_flModal.openEdit = function (item) {
  _flOpenEditOrig(item);
  populateFeatureDependencySelect(item.depends_on_feature_ids || [], item.id);
};

function renderFeaturesListState() {
  const section = qs("#fl-table-section");
  const features = featuresListState.features || [];
  qs("#fl-count").textContent = features.length;
  renderFeaturesGraph(features);
  if (!features.length) {
    appendEmpty(section, uiText("暂无功能。"));
    return;
  }

  clear(section);
  section.appendChild(
    makeTable(
      ["#", uiText("名称"), uiText("类型"), uiText("依赖"), uiText("负责人"), uiText("设计 / 实现"), uiText("旗下设计"), uiText("操作")],
      features.map((feature) => {
        const actions = el("div", "actions");
        const label = businessNumber(feature, "feature_number", feature.name);
        const href = featureListHref(featuresListState.projectId, feature.id);
        const numberNode = numberLink(label, href);
        const nameLink = el("a", "", feature.name);
        nameLink.href = href;
        actions.appendChild(opButton(uiText("编辑"), "action-button", () => {
          _flModal.openEdit(feature);
          return Promise.resolve(false);
        }, () => Promise.resolve(), showFlError));
        actions.appendChild(
          deleteButton(label, `/features/${feature.id}`, reloadFeaturesListData, showFlError)
        );
        const designRefs = (feature.design_numbers || []).map((designNumber, index) => ({
          text: designNumber,
          href: feature.design_case_ids?.[index]
            ? `/ui/design-cases/${feature.design_case_ids[index]}`
            : null,
        }));
        const dependencyRefs = (feature.depends_on_feature_numbers || []).map(
          (dependencyNumber, index) => ({
            text: dependencyNumber || `#${feature.depends_on_feature_ids?.[index]}`,
            href: featureListHref(
              featuresListState.projectId,
              feature.depends_on_feature_ids?.[index]
            ),
          })
        );
        return [
          numberNode,
          nameLink,
          featureTagsNode(feature),
          dependencyRefs.length ? numberLinkList(dependencyRefs) : textOr(null),
          featureAssigneeLabel(feature),
          featureStatusBadges(feature),
          numberLinkList(designRefs),
          actions,
        ];
      })
    )
  );
}

// --- diagrams (mermaid) -----------------------------------------------------
// Graph layout is mermaid's job. This used to be ~240 lines of hand-rolled
// Sugiyama layering + a crossing-reduction sweep, which is a graph-drawing
// library nobody asked us to maintain: it only ever produced left-to-right
// boxes, and every new diagram (ownership chain, change impact) would have
// needed its own layout code. mermaid is loaded only by the templates that
// draw something (see base.html's vendor_scripts block) because it is ~2.5MB.

let _mermaidReady = false;

function mermaidAvailable() {
  return typeof window !== "undefined" && typeof window.mermaid !== "undefined";
}

function initMermaid() {
  if (_mermaidReady || !mermaidAvailable()) return _mermaidReady;
  window.mermaid.initialize({
    startOnLoad: false,
    theme: "base",
    // Follows the app's palette rather than mermaid's default blues, so a
    // diagram does not look pasted in from another product.
    themeVariables: {
      fontFamily: '"Pretendard", system-ui, sans-serif',
      fontSize: "13px",
      primaryColor: "#f2f4f7",
      primaryTextColor: "#2e323b",
      primaryBorderColor: "#e9ecf1",
      lineColor: "#afb6c2",
      secondaryColor: "#ffe9e5",
      tertiaryColor: "#e2f5ee",
    },
    // htmlLabels MUST stay false: with it on (mermaid's default) every label is
    // drawn inside a <foreignObject>, and renderMermaidInto sanitizes the SVG
    // with DOMPurify's svg profile, which lists foreignobject as disallowed —
    // the boxes and edges survive, all the text silently disappears. Native SVG
    // <text> labels pass that filter, so the strict profile can stay strict.
    htmlLabels: false,
    // useMaxWidth:false is deliberate. With it on, mermaid stamps width:100% on
    // the SVG and a wide dependency graph is SHRUNK to the container instead of
    // scrolling — the labels get illegible exactly when the graph is big enough
    // to need reading. The wrapper already has overflow-x:auto for this.
    // curve:linear keeps edges straight between ranks; basis bows them into
    // each other and makes a dense graph look tangled.
    flowchart: {
      curve: "linear",
      padding: 12,
      useMaxWidth: false,
      htmlLabels: false,
      nodeSpacing: 36,
      rankSpacing: 64,
    },
    securityLevel: "strict",
  });
  _mermaidReady = true;
  return true;
}

// Escapes a label for mermaid: quotes make it a literal, but a quote or a
// newline inside would still break the parse, and an unparseable diagram takes
// the whole render down rather than showing one odd node.
function mermaidLabel(text, limit = 22) {
  const value = (text || "").replace(/["\n\r|]/g, " ").replace(/\s+/g, " ").trim();
  const clipped = value.length > limit ? `${value.slice(0, limit)}…` : value;
  return clipped || " ";
}

// One long line per node makes every box wide and flat, which is what turns a
// left-to-right graph into a sprawl. Break the text over at most two lines so
// boxes stay near-square and ranks pack tighter. Breaks on a space when the
// text has them (latin) and mid-word otherwise (CJK has no spaces to use).
function wrapLabelText(value, per = 15, maxLines = 2) {
  if (value.length <= per) return value;
  const lines = [];
  let rest = value;
  while (rest.length > per && lines.length < maxLines - 1) {
    const space = rest.slice(0, per + 1).lastIndexOf(" ");
    const cut = space > per / 2 ? space : per;
    lines.push(rest.slice(0, cut).trim());
    rest = rest.slice(cut).trim();
  }
  lines.push(rest.length > per ? `${rest.slice(0, per - 1)}…` : rest);
  return lines.join("<br/>");
}

// Every node in every diagram reads "business number, then what it is".
function mermaidNodeLabel(number, text) {
  return `${number}<br/>${wrapLabelText(mermaidLabel(text, 30))}`;
}

async function renderMermaidInto(container, definition, { onNodeClick } = {}) {
  if (!container) return;
  clear(container);
  if (!initMermaid()) {
    // No diagram is better than a broken page: every diagram in this app is a
    // second view of data that is also shown as a table right next to it.
    container.appendChild(el("p", "empty", uiText("图形未加载（缺少 mermaid）。")));
    return;
  }
  const id = `mmd-${Math.random().toString(36).slice(2)}`;
  try {
    const { svg } = await window.mermaid.render(id, definition);
    container.innerHTML = DOMPurify.sanitize(svg, { USE_PROFILES: { svg: true, svgFilters: true } });
  } catch (err) {
    container.appendChild(el("p", "error", uiText`图形渲染失败：${err.message}`));
    return;
  }
  if (!onNodeClick) return;
  container.querySelectorAll("[data-node-key]").forEach((node) => {
    node.style.cursor = "pointer";
    node.addEventListener("click", () => onNodeClick(node.dataset.nodeKey));
  });
  // mermaid keeps the node id on the group element; map it back to our key so
  // clicking a box can navigate.
  container.querySelectorAll("g.node").forEach((node) => {
    const key = (node.id || "").split("-")[1];
    if (!key) return;
    node.style.cursor = "pointer";
    node.addEventListener("click", () => onNodeClick(key));
  });
}

//: mermaid node ids must be identifier-safe; business numbers are not (DESIGN-3).
function mermaidNodeId(prefix, id) {
  return `${prefix}${id}`;
}

async function renderFeaturesGraph(features) {
  const container = qs("#fl-graph-section");
  if (!container) return;
  if (!features.length) {
    clear(container);
    appendEmpty(container, uiText("暂无功能。"));
    return;
  }
  const byId = new Map(features.map((feature) => [Number(feature.id), feature]));
  const lines = ["flowchart LR"];
  features.forEach((feature) => {
    const nodeId = mermaidNodeId("F", feature.id);
    const label = mermaidNodeLabel(businessNumber(feature, "feature_number", "?"), feature.name);
    lines.push(`  ${nodeId}["${label}"]`);
    lines.push(`  class ${nodeId} sub-${feature.subdomain_type || "supporting"}`);
  });
  features.forEach((feature) => {
    (feature.depends_on_feature_ids || []).forEach((depId) => {
      if (!byId.has(Number(depId))) return;
      // Edge direction reads as "must exist first" -> "depends on it".
      lines.push(`  ${mermaidNodeId("F", depId)} --> ${mermaidNodeId("F", feature.id)}`);
    });
  });
  lines.push("  classDef sub-core fill:#ffe9e5,stroke:#e15a4c;");
  lines.push("  classDef sub-supporting fill:#f2f4f7,stroke:#afb6c2;");
  lines.push("  classDef sub-generic fill:#e2f5ee,stroke:#2e8e6c;");
  await renderMermaidInto(container, lines.join("\n"), {
    onNodeClick: (key) => {
      const featureId = Number((key || "").replace(/^F/, ""));
      const href = featureListHref(featuresListState.projectId, featureId);
      if (href) window.location.href = href;
    },
  });
}

// Two block tags plus optional foundation identity and spike marker.
function featureTagsNode(feature) {
  const wrap = el("div", "feature-tags");
  wrap.appendChild(el("span", "tag-badge tag-orientation", feature.orientation || "vertical"));
  wrap.appendChild(
    el("span", "tag-badge tag-subdomain", feature.subdomain_type || "supporting")
  );
  if (feature.foundation_kind) {
    wrap.appendChild(el("span", "tag-badge tag-foundation", feature.foundation_kind));
  }
  if (feature.is_spike) wrap.appendChild(el("span", "tag-badge tag-spike", "spike"));
  return wrap;
}

async function reloadFeaturesListData() {
  if (!featuresListState.projectId) return;
  clearFlError();
  featuresListState.features = await apiJson(`/projects/${featuresListState.projectId}/features`);
  renderFeaturesListState();
  await renderEntityTrash(qs("#fl-trash"), {
    url: `/projects/${featuresListState.projectId}/features/trash`,
    base: (id) => `/features/${id}`,
    label: (x) => x.feature_number,
    reload: reloadFeaturesListData,
    view: (x) => [
      [uiText("名称"), x.name],
      [uiText("描述"), x.description],
      [uiText("负责人"), x.assignee],
      [uiText("进度"), x.progress],
    ],
  });
}

async function renderFeaturesListPage() {
  const page = qs("#features-list-page");
  if (!page) return;

  featuresListState.projectId = page.dataset.projectId;
  renderProjectFlowNav(featuresListState.projectId, "features");
  const statusEl = qs("#fl-status", page);
  const content = qs("#fl-content", page);
  _flModal.init();

  const createBtn = qs("#fl-create-btn", page);
  if (createBtn) createBtn.addEventListener("click", () => _flModal.openCreate());
  if (createBtn) createBtn.disabled = true;

  try {
    setStatus(statusEl, uiText("正在加载功能..."));
    featureAssigneeUsers = await apiJson("/users");
    await reloadFeaturesListData();

    hide(statusEl);
    show(content);
    if (createBtn) createBtn.disabled = false;
  } catch (err) {
    hide(content);
    setStatus(statusEl, uiText`加载功能失败：${err.message}`, "error");
  }
}

// --- feature detail page ----------------------------------------------------

const featureDetailState = {
  featureId: null,
  feature: null,
  units: [],
  designCases: [],
  tasks: [],
  versions: [],
  selectedVersionNumbers: new Set(),
};

function showFdiError(message) {
  showToast(message);
  const errorEl = qs("#fdi-error");
  if (!errorEl) return;
  errorEl.textContent = message;
  show(errorEl);
}

function clearFdiError() {
  const errorEl = qs("#fdi-error");
  if (!errorEl) return;
  errorEl.textContent = "";
  hide(errorEl);
}

function renderFeatureDetailState() {
  const feature = featureDetailState.feature;
  renderProjectFlowNav(feature.project_id, "features");
  const label = businessNumber(feature, "feature_number", feature.name || uiText("未编号功能"));
  qs("#fdi-title").textContent = `${label} ${feature.name || ""}`.trim();
  qs("#fdi-progress-badge").replaceChildren(featureStatusBadges(feature));
  qs("#fdi-back-link").href = `/ui/projects/${feature.project_id}/features`;

  qs("#fdi-display-number").textContent = label;
  qs("#fdi-display-version").textContent = feature.current_version_number || "-";
  qs("#fdi-display-assignee").textContent = featureAssigneeLabel(feature);
  qs("#fdi-display-tags").replaceChildren(featureTagsNode(feature));
  const dependencyRefs = (feature.depends_on_feature_numbers || []).map(
    (dependencyNumber, index) => ({
      text: dependencyNumber || `#${feature.depends_on_feature_ids?.[index]}`,
      href: featureListHref(feature.project_id, feature.depends_on_feature_ids?.[index]),
    })
  );
  qs("#fdi-display-dependencies").replaceChildren(
    dependencyRefs.length ? numberLinkList(dependencyRefs) : document.createTextNode("-")
  );
  const designRefs = (feature.design_numbers || []).map((designNumber, index) => ({
    text: designNumber,
    href: feature.design_case_ids?.[index] ? `/ui/design-cases/${feature.design_case_ids[index]}` : null,
  }));
  qs("#fdi-display-designs").replaceChildren(numberLinkList(designRefs));
  renderMarkdown(qs("#fdi-display-description"), feature.description);

  renderFeatureChainGraph();
  renderFeatureDetailActions();
  renderFeatureDetailVersions();
}

// The ownership chain under ONE feature: FEATURE -> DESIGN -> TASK.
// Scoped to a feature on purpose — a whole-project chain on a real project is
// a wall of boxes nobody can read, and the question people actually have is
// "what hangs off this block".
async function renderFeatureChainGraph() {
  const container = qs("#fdi-chain-graph");
  if (!container) return;
  const feature = featureDetailState.feature;
  const designs = featureDetailState.designCases || [];
  const tasks = featureDetailState.tasks || [];
  if (!designs.length) {
    clear(container);
    appendEmpty(container, uiText("这个功能下面还没有设计用例。"));
    return;
  }

  const featureNode = mermaidNodeId("FEAT", feature.id);
  const lines = ["flowchart LR"];
  lines.push(
    `  ${featureNode}["${mermaidNodeLabel(businessNumber(feature, "feature_number", "?"), feature.name)}"]`
  );
  lines.push(`  class ${featureNode} chainFeature`);

  const designIds = new Set(designs.map((design) => Number(design.id)));
  designs.forEach((design) => {
    const nodeId = mermaidNodeId("D", design.id);
    lines.push(
      `  ${nodeId}["${mermaidNodeLabel(businessNumber(design, "design_number", "?"), design.problem_statement)}"]`
    );
    lines.push(`  class ${nodeId} chainDesign`);
    lines.push(`  ${featureNode} --> ${nodeId}`);
  });
  tasks.forEach((task) => {
    if (!designIds.has(Number(task.design_case_id))) return;
    const nodeId = mermaidNodeId("T", task.id);
    lines.push(
      `  ${nodeId}["${mermaidNodeLabel(businessNumber(task, "task_number", "?"), task.title)}"]`
    );
    lines.push(`  class ${nodeId} chainTask`);
    lines.push(`  ${mermaidNodeId("D", task.design_case_id)} --> ${nodeId}`);
  });
  lines.push("  classDef chainFeature fill:#f2f4f7,stroke:#2e323b;");
  lines.push("  classDef chainDesign fill:#ffe9e5,stroke:#e15a4c;");
  lines.push("  classDef chainTask fill:#e2f5ee,stroke:#2e8e6c;");

  await renderMermaidInto(container, lines.join("\n"), {
    onNodeClick: (key) => {
      const match = /^(FEAT|D|T)(\d+)$/.exec(key || "");
      if (!match) return;
      const [, kind, rawId] = match;
      const href = {
        D: `/ui/design-cases/${rawId}`,
        T: `/ui/tasks/${rawId}`,
      }[kind];
      if (href) window.location.href = href;
    },
  });
}

function renderFeatureDetailActions() {
  const feature = featureDetailState.feature;
  const actions = qs("#fdi-actions");
  const hint = qs("#fdi-hint");
  if (!actions || !hint) return;

  clear(actions);
  const basePath = `/features/${featureDetailState.featureId}`;

  if (feature.deleted_at) {
    actions.appendChild(
      opButton(
        uiText("恢复"),
        "action-button",
        async () => {
          featureDetailState.feature = await apiJson(`${basePath}/restore`, jsonOptions("POST"));
          await reloadFeatureDetail();
        },
        () => Promise.resolve(),
        showFdiError
      )
    );
    hint.textContent = uiText("该功能已移入回收站，可在这里恢复。");
    return;
  }

  actions.appendChild(
    opButton(
      uiText("删除"),
      "action-button danger",
      async () => {
        if (!confirmSoftDelete(businessNumber(feature, "feature_number", feature.name || uiText("未编号功能")))) return false;
        featureDetailState.feature = await apiJson(basePath, { method: "DELETE" });
        renderFeatureDetailState();
      },
      () => Promise.resolve(),
      showFdiError
    )
  );
  hint.textContent = uiText("名称、描述、负责人等内容请回功能列表页编辑。");
}

const FEATURE_VERSION_DIFF_FIELDS = [
  ["name", uiText("名称")],
  ["description", uiText("描述")],
  ["assignee", uiText("负责人")],
  ["orientation", uiText("方向")],
  ["foundation_kind", uiText("基础设计类型")],
  ["subdomain_type", uiText("子域类型")],
  ["is_spike", "Walking skeleton"],
  ["depends_on_feature_numbers", uiText("依赖的功能块")],
];

function featureVersionDiffText(field, value) {
  if (field === "is_spike") return value ? uiText("是") : uiText("否");
  if (Array.isArray(value)) return value.length ? value.join("\n") : uiText("(无)");
  if (value === null || value === undefined || value === "") return uiText("(未设置)");
  return String(value);
}

// A small front-end-only LCS implementation. It renders familiar git-style
// context / removed / added lines without sending either snapshot back to the
// server for comparison.
function gitLineDiff(beforeText, afterText) {
  const before = beforeText ? beforeText.split("\n") : [];
  const after = afterText ? afterText.split("\n") : [];
  if (before.length * after.length > 250000) {
    return [
      ...before.map((text) => ["remove", text]),
      ...after.map((text) => ["add", text]),
    ];
  }
  const table = Array.from(
    { length: before.length + 1 },
    () => Array(after.length + 1).fill(0)
  );
  for (let i = before.length - 1; i >= 0; i--) {
    for (let j = after.length - 1; j >= 0; j--) {
      table[i][j] = before[i] === after[j]
        ? table[i + 1][j + 1] + 1
        : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const rows = [];
  let i = 0;
  let j = 0;
  while (i < before.length || j < after.length) {
    if (i < before.length && j < after.length && before[i] === after[j]) {
      rows.push(["context", before[i]]);
      i++;
      j++;
    } else if (j < after.length && (i === before.length || table[i][j + 1] >= table[i + 1][j])) {
      rows.push(["add", after[j]]);
      j++;
    } else {
      rows.push(["remove", before[i]]);
      i++;
    }
  }
  return rows;
}

function hideFeatureVersionDiff() {
  closeVersionDiffModal();
}

function syncFeatureVersionCompareControl() {
  const button = qs("#fdi-compare-versions");
  if (!button) return;
  button.disabled = featureDetailState.selectedVersionNumbers.size !== 2;
}

function renderFeatureVersionDiff() {
  const selected = featureDetailState.versions
    .filter((version) => featureDetailState.selectedVersionNumbers.has(version.version_number))
    .sort((left, right) => left.version_number - right.version_number);
  if (selected.length !== 2) return;

  const [before, after] = selected;
  const content = openVersionDiffModal(
    uiText`版本差异：v${before.version_number} → v${after.version_number}`,
  );
  if (!content) return;
  let changed = 0;
  FEATURE_VERSION_DIFF_FIELDS.forEach(([field, label]) => {
    const beforeText = featureVersionDiffText(field, before[field]);
    const afterText = featureVersionDiffText(field, after[field]);
    if (beforeText === afterText) return;
    changed++;
    const section = el("section", "version-diff-field");
    section.appendChild(el("h3", "", label));
    const code = el("pre", "version-diff-code");
    gitLineDiff(beforeText, afterText).forEach(([kind, text]) => {
      const marker = kind === "add" ? "+" : kind === "remove" ? "-" : " ";
      const line = el("span", `version-diff-line version-diff-${kind}`);
      line.textContent = `${marker} ${text}`;
      code.appendChild(line);
    });
    section.appendChild(code);
    content.appendChild(section);
  });
  if (!changed) appendEmpty(content, uiText("所选版本没有内容差异。"));
}

function renderFeatureDetailVersions() {
  const versions = featureDetailState.versions || [];
  const tableWrap = qs("#fdi-versions-table");
  const emptyEl = qs("#fdi-versions-empty");
  if (!tableWrap || !emptyEl) return;

  clear(tableWrap);
  if (!versions.length) {
    hide(tableWrap);
    show(emptyEl);
    return;
  }
  hide(emptyEl);
  show(tableWrap);
  const rows = versions.map((version) => {
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = featureDetailState.selectedVersionNumbers.has(version.version_number);
    checkbox.setAttribute("aria-label", uiText`选择版本 v${version.version_number}`);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        if (featureDetailState.selectedVersionNumbers.size >= 2) {
          checkbox.checked = false;
          showFdiError(uiText("一次只能比较两个版本"));
          return;
        }
        featureDetailState.selectedVersionNumbers.add(version.version_number);
      } else {
        featureDetailState.selectedVersionNumbers.delete(version.version_number);
      }
      hideFeatureVersionDiff();
      syncFeatureVersionCompareControl();
    });
    const actions = el("div", "actions-cell");
    if (!version.is_current) {
      actions.appendChild(
        opButton(
          uiText("恢复为当前版本"),
          "action-button",
          async () => {
            if (!confirm(uiText`恢复到版本 v${version.version_number}？\n系统会生成一个新的当前版本。`)) return false;
            await apiJson(
              `/features/${featureDetailState.featureId}/versions/${version.version_number}/restore`,
              jsonOptions("POST")
            );
            await reloadFeatureDetail();
          },
          () => Promise.resolve(),
          showFdiError
        )
      );
    }
    const summary = [];
    if (version.is_current) summary.push(uiText("当前"));
    if (version.change_type === "restore" && version.restored_from_version_number) {
      summary.push(uiText`从 v${version.restored_from_version_number} 恢复`);
    } else {
      summary.push(version.change_type);
    }
    return [
      checkbox,
      `v${version.version_number}`,
      summary.join(" / "),
      shortTime(version.created_at),
      previewText(version.name),
      previewText(version.description),
      actions,
    ];
  });
  tableWrap.appendChild(
    makeTable([uiText("选择"), uiText("版本"), uiText("变更"), uiText("创建时间"), uiText("名称快照"), uiText("描述快照"), uiText("操作")], rows)
  );
  syncFeatureVersionCompareControl();
}

async function reloadFeatureDetail() {
  if (!featureDetailState.featureId) return;
  clearFdiError();
  const [feature, versions] = await Promise.all([
    apiJson(`/features/${featureDetailState.featureId}`),
    apiJson(`/features/${featureDetailState.featureId}/versions`),
  ]);
  featureDetailState.feature = feature;
  featureDetailState.versions = versions;
  // The chain graph needs the project's designs/tasks; failures degrade
  // to an empty graph rather than taking the page down with them.
  const projectId = feature.project_id;
  const [designs, tasks] = await Promise.all([
    apiJson(`/projects/${projectId}/design-cases`).catch(() => []),
    apiJson(`/projects/${projectId}/tasks`).catch(() => []),
  ]);
  const featureId = Number(feature.id);
  featureDetailState.designCases = designs.filter(
    (design) => Number(design.feature_id) === featureId
  );
  const designIds = new Set(featureDetailState.designCases.map((d) => Number(d.id)));
  featureDetailState.tasks = tasks.filter((task) => designIds.has(Number(task.design_case_id)));
  featureDetailState.selectedVersionNumbers = new Set();
  hideFeatureVersionDiff();
  renderFeatureDetailState();
}

async function renderFeatureDetailPage() {
  const page = qs("#feature-page");
  if (!page) return;

  featureDetailState.featureId = page.dataset.featureId;
  const statusEl = qs("#fdi-status", page);
  const content = qs("#fdi-content", page);
  const compareButton = qs("#fdi-compare-versions", page);
  if (compareButton) {
    compareButton.addEventListener("click", renderFeatureVersionDiff);
  }

  try {
    setStatus(statusEl, uiText("正在加载功能..."));
    await reloadFeatureDetail();

    hide(statusEl);
    show(content);
  } catch (err) {
    hide(content);
    setStatus(statusEl, uiText`加载功能失败：${err.message}`, "error");
  }
}

// --- project constitution (singleton) --------------------------------------

const constitutionState = {
  projectId: null,
  constitution: null,
  versions: [],
  selectedVersionNumbers: new Set(),
};

function showCstError(message) {
  showToast(message);
  const errorEl = qs("#cst-error");
  if (!errorEl) return;
  errorEl.textContent = message;
  show(errorEl);
}

function clearCstError() {
  const errorEl = qs("#cst-error");
  if (!errorEl) return;
  errorEl.textContent = "";
  hide(errorEl);
}

const _cstModal = createModalManager({
  overlayId: "#cst-modal",
  createTitle: uiText("创建项目规范"),
  editTitle: uiText("编辑项目规范"),
  focusSelector: "#cst-system-nature",
  errorSelector: "#cst-modal-error",
  submitSelector: "#cst-modal-submit",
  cancelSelector: "#cst-modal-cancel",
  getPayload(form) {
    return {
      system_nature: form.elements.system_nature.value,
      fixed_stack: form.elements.fixed_stack.value,
      quality_bar: form.elements.quality_bar.value,
      ui_conventions: form.elements.ui_conventions.value,
      nfr_baseline: form.elements.nfr_baseline.value,
      boundaries: form.elements.boundaries.value,
    };
  },
  setFormData(form, item) {
    form.elements.system_nature.value = item.system_nature || "";
    form.elements.fixed_stack.value = item.fixed_stack || "";
    form.elements.quality_bar.value = item.quality_bar || "";
    form.elements.ui_conventions.value = item.ui_conventions || "";
    form.elements.nfr_baseline.value = item.nfr_baseline || "";
    form.elements.boundaries.value = item.boundaries || "";
  },
  submit(mode, payload) {
    return apiJson(
      `/projects/${constitutionState.projectId}/constitution`,
      jsonOptions("PATCH", payload)
    );
  },
  async onSuccess() {
    await reloadConstitution();
  },
});

function renderConstitutionState() {
  const constitution = constitutionState.constitution;
  renderProjectFlowNav(constitutionState.projectId, "constitution");
  qs("#cst-back-link").href = `/ui/projects/${constitutionState.projectId}`;

  const badge = qs("#cst-status-badge");
  const sections = qs("#cst-sections");
  const empty = qs("#cst-empty");

  if (!constitution) {
    clear(badge);
    hide(sections);
    show(empty);
  } else {
    badge.replaceChildren(gateStatusBadge(constitution.status, constitution.deleted_at));
    show(sections);
    hide(empty);
    renderMarkdown(qs("#cst-display-system-nature"), constitution.system_nature);
    renderMarkdown(qs("#cst-display-fixed-stack"), constitution.fixed_stack);
    renderMarkdown(qs("#cst-display-quality-bar"), constitution.quality_bar);
    renderMarkdown(qs("#cst-display-ui-conventions"), constitution.ui_conventions);
    renderMarkdown(qs("#cst-display-nfr-baseline"), constitution.nfr_baseline);
    renderMarkdown(qs("#cst-display-boundaries"), constitution.boundaries);
  }

  renderConstitutionEditArea();
  renderConstitutionActions();
  renderConstitutionVersions();
}

function renderConstitutionEditArea() {
  const area = qs("#cst-edit-area");
  if (!area) return;
  clear(area);
  const constitution = constitutionState.constitution;
  const editable = !constitution || constitution.status === "draft"
    || constitution.status === "needs_clarification";
  if (!editable) return;

  const button = el("button", "action-button", constitution ? uiText("编辑") : uiText("创建规范"));
  button.type = "button";
  button.addEventListener("click", () => {
    if (constitution) _cstModal.openEdit(constitution);
    else _cstModal.openCreate();
  });
  area.appendChild(button);
}

function renderConstitutionActions() {
  const actions = qs("#cst-lifecycle-actions");
  const hint = qs("#cst-lifecycle-hint");
  if (!actions || !hint) return;
  clear(actions);

  const constitution = constitutionState.constitution;
  if (!constitution) {
    hint.textContent = uiText("先创建并填写规范，再确认，确认后设计阶段才能开始。");
    return;
  }
  const basePath = `/projects/${constitutionState.projectId}/constitution`;

  if (constitution.status === "draft") {
    actions.append(
      opButton(uiText("需澄清"), "action-button",
        () => apiJson(basePath, jsonOptions("PATCH", { status: "needs_clarification" })),
        reloadConstitution, showCstError),
      opButton(uiText("确认"), "action-button",
        () => apiJson(`${basePath}/confirm`, jsonOptions("POST")),
        reloadConstitution, showCstError)
    );
    hint.textContent = uiText("草稿可用『编辑』修改；确认需至少一段非空，确认后设计阶段方可开始。");
  } else if (constitution.status === "needs_clarification") {
    actions.append(
      opButton(uiText("回到草稿"), "action-button",
        () => apiJson(basePath, jsonOptions("PATCH", { status: "draft" })),
        reloadConstitution, showCstError),
      opButton(uiText("确认"), "action-button",
        () => apiJson(`${basePath}/confirm`, jsonOptions("POST")),
        reloadConstitution, showCstError)
    );
    hint.textContent = uiText("当前标记为需澄清；补齐信息后可回到草稿，或直接确认。");
  } else if (constitution.status === "confirmed") {
    actions.appendChild(
      opButton(uiText("重新打开"), "action-button",
        () => apiJson(`${basePath}/reopen`, jsonOptions("POST")),
        reloadConstitution, showCstError)
    );
    hint.textContent = uiText("已确认的规范已锁定；重新打开可修改治理内容，不影响已确认的设计——规范是背景约束，在后续设计/开发时生效。");
  }
}

function renderConstitutionVersions() {
  const versions = constitutionState.versions || [];
  const tableWrap = qs("#cst-versions-table");
  const emptyEl = qs("#cst-versions-empty");
  if (!tableWrap || !emptyEl) return;

  clear(tableWrap);
  if (!versions.length) {
    hide(tableWrap);
    show(emptyEl);
    return;
  }
  hide(emptyEl);
  show(tableWrap);

  const currentNum = versions[0].version_number;
  const constitution = constitutionState.constitution;
  const editable = constitution && (constitution.status === "draft"
    || constitution.status === "needs_clarification");

  const rows = versions.map((version) => {
    const isCurrent = version.version_number === currentNum;
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = constitutionState.selectedVersionNumbers.has(version.version_number);
    checkbox.setAttribute("aria-label", uiText`选择版本 v${version.version_number}`);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        if (constitutionState.selectedVersionNumbers.size >= 2) {
          checkbox.checked = false;
          showCstError(uiText("一次只能比较两个版本"));
          return;
        }
        constitutionState.selectedVersionNumbers.add(version.version_number);
      } else {
        constitutionState.selectedVersionNumbers.delete(version.version_number);
      }
      hideConstitutionVersionDiff();
      syncConstitutionVersionCompareControl();
    });
    const actions = el("div", "actions-cell");
    if (!isCurrent && editable) {
      actions.appendChild(
        opButton(uiText("恢复为当前版本"), "action-button",
          async () => {
            if (!confirm(uiText`确认把规范恢复为版本 v${version.version_number}？`)) return false;
            await apiJson(
              `/projects/${constitutionState.projectId}/constitution/versions/${version.version_number}/restore`,
              jsonOptions("POST")
            );
            await reloadConstitution();
          },
          () => Promise.resolve(), showCstError)
      );
    }
    const summary = [];
    if (isCurrent) summary.push(uiText("当前"));
    if (version.change_type === "restore" && version.restored_from_version_number) {
      summary.push(uiText`从 v${version.restored_from_version_number} 恢复`);
    } else {
      summary.push(version.change_type);
    }
    return [
      checkbox,
      `v${version.version_number}`,
      summary.join(" / "),
      shortTime(version.created_at),
      previewText(version.fixed_stack),
      previewText(version.nfr_baseline),
      actions,
    ];
  });
  tableWrap.appendChild(
    makeTable([uiText("选择"), uiText("版本"), uiText("变更"), uiText("创建时间"), uiText("固定技术栈快照"), uiText("非功能基线快照"), uiText("操作")], rows)
  );
  syncConstitutionVersionCompareControl();
}

const CONSTITUTION_VERSION_DIFF_FIELDS = [
  ["system_nature", uiText("系统性质与目标")],
  ["fixed_stack", uiText("固定技术栈")],
  ["quality_bar", uiText("质量线与测试标准")],
  ["ui_conventions", uiText("界面与交互规范")],
  ["nfr_baseline", uiText("非功能基线")],
  ["boundaries", uiText("边界")],
];

function hideConstitutionVersionDiff() {
  closeVersionDiffModal();
}

function syncConstitutionVersionCompareControl() {
  const button = qs("#cst-compare-versions");
  if (!button) return;
  button.disabled = constitutionState.selectedVersionNumbers.size !== 2;
}

function renderConstitutionVersionDiff() {
  const selected = constitutionState.versions
    .filter((version) => constitutionState.selectedVersionNumbers.has(version.version_number))
    .sort((left, right) => left.version_number - right.version_number);
  if (selected.length !== 2) return;

  const [before, after] = selected;
  const content = openVersionDiffModal(
    uiText`版本差异：v${before.version_number} → v${after.version_number}`,
  );
  if (!content) return;
  let changed = 0;
  CONSTITUTION_VERSION_DIFF_FIELDS.forEach(([field, label]) => {
    const beforeText = before[field] || uiText("(未设置)");
    const afterText = after[field] || uiText("(未设置)");
    if (beforeText === afterText) return;
    changed++;
    const section = el("section", "version-diff-field");
    section.appendChild(el("h3", "", label));
    const code = el("pre", "version-diff-code");
    gitLineDiff(beforeText, afterText).forEach(([kind, text]) => {
      const marker = kind === "add" ? "+" : kind === "remove" ? "-" : " ";
      const line = el("span", `version-diff-line version-diff-${kind}`);
      line.textContent = `${marker} ${text}`;
      code.appendChild(line);
    });
    section.appendChild(code);
    content.appendChild(section);
  });
  if (!changed) appendEmpty(content, uiText("所选版本没有内容差异。"));
}

async function reloadConstitution() {
  if (!constitutionState.projectId) return;
  clearCstError();
  // A project with no constitution yet returns 404 — a normal empty state, not
  // an error. Use raw fetch so we can branch on the status code (apiJson only
  // surfaces the error text).
  const resp = await fetch(`/projects/${constitutionState.projectId}/constitution`);
  let constitution = null;
  if (resp.status !== 404) {
    const data = await resp.json().catch(() => null);
    if (!resp.ok) throw new Error(data && data.error ? data.error : `HTTP ${resp.status}`);
    constitution = data;
  }
  let versions = [];
  if (constitution) {
    versions = await apiJson(
      `/projects/${constitutionState.projectId}/constitution/versions`
    ).catch(() => []);
  }
  constitutionState.constitution = constitution;
  constitutionState.versions = versions;
  constitutionState.selectedVersionNumbers = new Set();
  hideConstitutionVersionDiff();
  renderConstitutionState();
}

async function renderConstitutionPage() {
  const page = qs("#constitution-page");
  if (!page) return;

  _cstModal.init();
  constitutionState.projectId = page.dataset.projectId;
  const statusEl = qs("#cst-status", page);
  const content = qs("#cst-content", page);
  const compareButton = qs("#cst-compare-versions", page);
  if (compareButton) {
    compareButton.addEventListener("click", renderConstitutionVersionDiff);
  }

  try {
    setStatus(statusEl, uiText("正在加载项目规范..."));
    await reloadConstitution();
    hide(statusEl);
    show(content);
  } catch (err) {
    hide(content);
    setStatus(statusEl, uiText`加载项目规范失败：${err.message}`, "error");
  }
}

// --- design cases list page ------------------------------------------------

const designCasesListState = {
  projectId: null,
  designs: [],
  features: [],
  versionsByDesignId: {},
};

function selectedValues(select) {
  return Array.from(select?.selectedOptions || []).map((option) => parseInt(option.value, 10));
}

function populateDesignFeatureSelect(selectedFeatureId = null) {
  const select = qs("#dcl-feature-id");
  if (!select) return;
  select.innerHTML = uiText('<option value="">-- 选择所属功能 --</option>');
  (designCasesListState.features || []).forEach((feature) => {
    const opt = document.createElement("option");
    opt.value = feature.id;
    opt.textContent = `${feature.feature_number} ${feature.name}`;
    opt.selected = Number(selectedFeatureId) === Number(feature.id);
    select.appendChild(opt);
  });
}

function showDclError(message) {
  showToast(message);
  const errorEl = qs("#dcl-error");
  if (!errorEl) return;
  errorEl.textContent = message;
  show(errorEl);
}

function clearDclError() {
  const errorEl = qs("#dcl-error");
  if (!errorEl) return;
  errorEl.textContent = "";
  hide(errorEl);
}

const _dclModal = createModalManager({
  overlayId: "#dcl-modal",
  createTitle: uiText("创建设计用例"),
  editTitle: uiText("编辑设计用例"),
  focusSelector: "#dcl-source-requirement-id",
  errorSelector: "#dcl-modal-error",
  submitSelector: "#dcl-modal-submit",
  cancelSelector: "#dcl-modal-cancel",
  getPayload(form) {
    if (form.dataset.dclMode === "create") {
      const featureId = form.elements.feature_id.value;
      if (!featureId) {
        form.querySelector("#dcl-modal-error").textContent = uiText("请选择所属功能");
        return null;
      }
      return { feature_id: parseInt(featureId, 10) };
    }
    // edit mode – collect all text fields
    const fields = [
      "problem_statement", "mined_requirements", "verifies_nfr", "design_scope",
      "non_goals",
      "assumptions",
      "constraints", "selected_solution", "selection_reason", "risks",
      "test_strategy", "acceptance_criteria",
    ];
    const payload = {};
    fields.forEach((f) => { payload[f] = (form.elements[f] && form.elements[f].value) || ""; });
    payload.feature_id = form.elements.feature_id.value ? parseInt(form.elements.feature_id.value, 10) : null;
    return payload;
  },
  setFormData(form, item) {
    populateDesignFeatureSelect(item.feature_id);
    form.elements.problem_statement.value = item.problem_statement || "";
    form.elements.mined_requirements.value = item.mined_requirements || "";
    form.elements.verifies_nfr.value = item.verifies_nfr || "";
    form.elements.design_scope.value = item.design_scope || "";
    form.elements.non_goals.value = item.non_goals || "";
    form.elements.assumptions.value = item.assumptions || "";
    form.elements.constraints.value = item.constraints || "";
    form.elements.selected_solution.value = item.selected_solution || "";
    form.elements.selection_reason.value = item.selection_reason || "";
    form.elements.risks.value = item.risks || "";
    form.elements.test_strategy.value = item.test_strategy || "";
    form.elements.acceptance_criteria.value = item.acceptance_criteria || "";
  },
  submit(mode, payload, editId) {
    if (mode === "edit") {
      return apiJson(`/design-cases/${editId}`, jsonOptions("PATCH", payload));
    }
    return apiJson(
      `/projects/${designCasesListState.projectId}/design-cases`,
      jsonOptions("POST", { feature_id: payload.feature_id })
    );
  },
  async onSuccess() {
    await reloadDesignCasesListData();
  },
});

// Override openCreate/openEdit to toggle field visibility
const _dclOpenCreateOrig = _dclModal.openCreate.bind(_dclModal);
const _dclOpenEditOrig = _dclModal.openEdit.bind(_dclModal);

_dclModal.openCreate = function () {
  _dclOpenCreateOrig();
  const form = qs("#dcl-modal-form");
  if (form) {
    form.dataset.dclMode = "create";
    hide(qs("#dcl-edit-fields"));
    populateDesignFeatureSelect();
  }
  const panel = qs("#dcl-version-panel");
  const versionEl = qs("#dcl-display-version");
  const emptyEl = qs("#dcl-versions-empty");
  const tableEl = qs("#dcl-versions-table");
  if (panel) hide(panel);
  if (versionEl) versionEl.textContent = "-";
  if (emptyEl) hide(emptyEl);
  if (tableEl) clear(tableEl);
};

_dclModal.openEdit = async function (item) {
  _dclOpenEditOrig(item);
  const form = qs("#dcl-modal-form");
  if (form) {
    form.dataset.dclMode = "edit";
    show(qs("#dcl-edit-fields"));
    populateDesignFeatureSelect(item.feature_id);
  }
  await loadDesignCaseVersions(item);
};

function confirmRestoreDesignCaseVersion(versionNumber) {
  return window.confirm(
    uiText`恢复到版本 ${versionNumber}？\n系统会复制该版本内容，并生成一个新的当前版本。`
  );
}

async function loadDesignCaseVersions(design) {
  const panel = qs("#dcl-version-panel");
  const versionEl = qs("#dcl-display-version");
  const errorEl = qs("#dcl-modal-error");
  if (panel) show(panel);
  if (versionEl) versionEl.textContent = design.current_version_number || "-";

  let versions = designCasesListState.versionsByDesignId[design.id];
  if (!versions) {
    versions = await apiJson(`/design-cases/${design.id}/versions`);
    designCasesListState.versionsByDesignId[design.id] = versions;
  }
  renderDesignCaseVersions(design.id, versions, errorEl);
}

function renderDesignCaseVersions(designId, versions, errorEl) {
  const emptyEl = qs("#dcl-versions-empty");
  const tableWrap = qs("#dcl-versions-table");
  if (!emptyEl || !tableWrap) return;

  clear(tableWrap);
  if (!versions.length) {
    hide(tableWrap);
    show(emptyEl);
    return;
  }

  hide(emptyEl);
  show(tableWrap);
  const rows = versions.map((version) => {
    const actions = el("div", "actions-cell");
    if (!version.is_current) {
      const restoreBtn = el("button", "action-button", uiText("恢复为当前版本"));
      restoreBtn.type = "button";
      restoreBtn.addEventListener("click", async () => {
        if (!confirmRestoreDesignCaseVersion(version.version_number)) return;
        restoreBtn.disabled = true;
        if (errorEl) errorEl.textContent = "";
        try {
          const design = await apiJson(
            `/design-cases/${designId}/versions/${version.version_number}/restore`,
            jsonOptions("POST")
          );
          const index = designCasesListState.designs.findIndex((candidate) => candidate.id === designId);
          if (index >= 0) designCasesListState.designs[index] = design;
          const refreshed = await apiJson(`/design-cases/${designId}/versions`);
          designCasesListState.versionsByDesignId[designId] = refreshed;
          renderDesignCasesListState();
          await _dclModal.openEdit(design);
        } catch (err) {
          if (errorEl) errorEl.textContent = err.message || uiText("恢复失败");
        } finally {
          restoreBtn.disabled = false;
        }
      });
      actions.appendChild(restoreBtn);
    }

    const summary = [];
    if (version.is_current) summary.push(uiText("当前"));
    if (version.change_type === "restore" && version.restored_from_version_number) {
      summary.push(uiText`从 v${version.restored_from_version_number} 恢复`);
    } else {
      summary.push(version.change_type);
    }

    return [
      `v${version.version_number}`,
      summary.join(" / "),
      shortTime(version.created_at),
      previewText(version.problem_statement),
      previewText(version.selected_solution),
      actions,
    ];
  });
  tableWrap.appendChild(
    makeTable([uiText("版本"), uiText("变更"), uiText("创建时间"), uiText("问题陈述快照"), uiText("选定方案快照"), uiText("操作")], rows)
  );
}

function renderDesignCasesListState() {
  const designs = designCasesListState.designs;
  qs("#dcl-count").textContent = designs.length;
  const section = qs("#dcl-table-section");
  if (!designs.length) {
    appendEmpty(section, uiText("暂无设计用例。"));
    return;
  }

  clear(section);
  section.appendChild(
    makeTable(
      ["#", uiText("所属功能"), uiText("状态"), uiText("问题陈述"), uiText("操作")],
      designs.map((design) => {
        const actions = el("div", "actions");
        if (isEditableGateStatus(design.status)) {
          const editBtn = el("button", "action-button", uiText("编辑"));
          editBtn.type = "button";
          editBtn.addEventListener("click", () => _dclModal.openEdit(design));
          actions.appendChild(editBtn);
          if (design.status === "draft") {
            actions.appendChild(
              opButton(
                uiText("需澄清"),
                "action-button",
                () => apiJson(`/design-cases/${design.id}`, jsonOptions("PATCH", { status: "needs_clarification" })),
                reloadDesignCasesListData,
                showDclError
              )
            );
          } else {
            actions.appendChild(
              opButton(
                uiText("回到草稿"),
                "action-button",
                () => apiJson(`/design-cases/${design.id}`, jsonOptions("PATCH", { status: "draft" })),
                reloadDesignCasesListData,
                showDclError
              )
            );
          }
          actions.appendChild(
            opButton(
              uiText("确认"),
              "action-button",
              async () => confirmDesignCaseWithPrompts(design),
              reloadDesignCasesListData,
              showDclError
            )
          );
        } else if (design.status === "confirmed") {
          actions.appendChild(
            opButton(
              uiText("重新打开"),
              "action-button",
              () => apiJson(`/design-cases/${design.id}/reopen`, jsonOptions("POST")),
              reloadDesignCasesListData,
              showDclError
            )
          );
          const detailBtn = el("a", "action-button", uiText("查看详情"));
          detailBtn.href = `/ui/design-cases/${design.id}`;
          actions.appendChild(detailBtn);
        }

        actions.appendChild(
          deleteButton(
            design.design_number,
            `/design-cases/${design.id}`,
            reloadDesignCasesListData,
            showDclError
          )
        );
        if (design.suspect) {
          actions.appendChild(
            realignButton(`/design-cases/${design.id}`, reloadDesignCasesListData, showDclError)
          );
        }
        const feature = findFeatureById(design.feature_id, designCasesListState.features);
        const featureRef = featureRefFor(feature, designCasesListState.projectId);
        const designStatusCell = el("div", "status-cell");
        designStatusCell.appendChild(statusBadge(design.status));
        const designSuspect = suspectBadge(design);
        if (designSuspect) designStatusCell.appendChild(designSuspect);
        return [
          numberLink(design.design_number, `/ui/design-cases/${design.id}`),
          numberLink(featureRef.text, featureRef.href),
          designStatusCell,
          previewText(design.problem_statement),
          actions,
        ];
      })
    )
  );
}

async function reloadDesignCasesListData() {
  if (!designCasesListState.projectId) return;
  clearDclError();
  designCasesListState.versionsByDesignId = {};
  const [designs, features] = await Promise.all([
    apiJson(`/projects/${designCasesListState.projectId}/design-cases`),
    apiJson(`/projects/${designCasesListState.projectId}/features`).catch(() => []),
  ]);
  designCasesListState.designs = designs;
  designCasesListState.features = features;
  renderDesignCasesListState();
  await renderEntityTrash(qs("#dcl-trash"), {
    url: `/projects/${designCasesListState.projectId}/design-cases/trash`,
    base: (id) => `/design-cases/${id}`,
    label: (x) => x.design_number,
    reload: reloadDesignCasesListData,
    view: (x) => [
      [uiText("状态"), x.status], [uiText("问题陈述"), x.problem_statement],
      [uiText("选定方案"), x.selected_solution], [uiText("选择理由"), x.selection_reason],
      [uiText("风险"), x.risks], [uiText("测试策略"), x.test_strategy],
      [uiText("验收标准"), x.acceptance_criteria],
    ],
  });
}

async function renderDesignCasesListPage() {
  const page = qs("#design-cases-list-page");
  if (!page) return;

  designCasesListState.projectId = page.dataset.projectId;
  renderProjectFlowNav(designCasesListState.projectId, "designs");
  const statusEl = qs("#dcl-status", page);
  const content = qs("#dcl-content", page);
  _dclModal.init();

  const createBtn = qs("#dcl-create-btn", page);
  if (createBtn) createBtn.addEventListener("click", () => _dclModal.openCreate());

  try {
    setStatus(statusEl, uiText("正在加载设计用例..."));
    await reloadDesignCasesListData();

    hide(statusEl);
    show(content);
  } catch (err) {
    hide(content);
    setStatus(statusEl, uiText`加载设计用例失败：${err.message}`, "error");
  }
}

// --- design case detail page ----------------------------------------------

const designCaseDetailState = {
  designId: null,
  design: null,
  versions: [],
  selectedVersionNumbers: new Set(),
};

function showDciError(message) {
  showToast(message);
  const errorEl = qs("#dci-error");
  if (!errorEl) return;
  errorEl.textContent = message;
  show(errorEl);
}

function clearDciError() {
  const errorEl = qs("#dci-error");
  if (!errorEl) return;
  errorEl.textContent = "";
  hide(errorEl);
}


function bindSummaryAction(button, onClick) {
  button.type = "button";
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    onClick();
  });
}

function renderOptionalMarkdown(target, value, fallback) {
  if (!target) return;
  if ((value || "").trim()) {
    renderMarkdown(target, value);
    target.classList.remove("muted");
    return;
  }
  clear(target);
  target.textContent = fallback;
  target.classList.add("muted");
}

function renderDesignCaseDetailState() {
  const design = designCaseDetailState.design;
  renderProjectFlowNav(design.project_id, "designs");
  qs("#dci-title").textContent = businessNumber(design, "design_number", uiText("未编号设计用例"));
  const dciBadges = [gateStatusBadge(design.status, design.deleted_at)];
  const dciSuspect = suspectBadge(design);
  if (dciSuspect) dciBadges.push(dciSuspect);
  qs("#dci-status-badge").replaceChildren(...dciBadges);
  qs("#dci-back-link").href = `/ui/projects/${design.project_id}/design-cases`;

  qs("#dci-display-number").textContent = businessNumber(design, "design_number", uiText("未编号设计用例"));
  const featureEl = qs("#dci-display-feature");
  clear(featureEl);
  if (design.feature_id && design.feature_number) {
    const link = el("a", "", `${design.feature_number} ${design.feature_name || ""}`.trim());
    link.href = featureListHref(design.project_id, design.feature_id);
    featureEl.appendChild(link);
  } else {
    featureEl.textContent = "-";
  }
  qs("#dci-display-version").textContent = design.current_version_number || "-";
  qs("#dci-display-impact-areas").textContent = Array.isArray(design.impact_areas) && design.impact_areas.length
    ? design.impact_areas.join(", ")
    : "-";

  renderMarkdown(qs("#dci-display-problem-statement"), design.problem_statement);
  renderMarkdown(qs("#dci-display-mined-requirements"), design.mined_requirements);
  renderMarkdown(qs("#dci-display-verifies-nfr"), design.verifies_nfr);
  renderMarkdown(qs("#dci-display-design-scope"), design.design_scope);
  renderMarkdown(qs("#dci-display-non-goals"), design.non_goals);
  renderMarkdown(qs("#dci-display-assumptions"), design.assumptions);
  renderMarkdown(qs("#dci-display-constraints"), design.constraints);
  renderMarkdown(qs("#dci-display-selected-solution"), design.selected_solution);
  renderMarkdown(qs("#dci-display-selection-reason"), design.selection_reason);
  renderMarkdown(qs("#dci-display-rejected-solutions"), rejectedSolutionsMarkdown(design.rejected_solutions));
  renderMarkdown(qs("#dci-display-risks"), design.risks);
  renderMarkdown(qs("#dci-display-test-strategy"), design.test_strategy);
  renderMarkdown(qs("#dci-display-acceptance-criteria"), design.acceptance_criteria);
  renderMarkdown(qs("#dci-display-open-questions"), markdownList(design.open_questions));

  renderDesignCaseDetailActions();
  appendSuspectHint(qs("#dci-lifecycle-hint"), design);
  renderDesignCaseDetailVersions();
  renderRelatedChangeRequests(
    "dci", `/design-cases/${design.id}/change-requests`
  );
}

const DESIGN_VERSION_DIFF_FIELDS = [
  ["problem_statement", uiText("问题陈述")],
  ["mined_requirements", uiText("需求(挖掘所得)")],
  ["verifies_nfr", uiText("本设计验证的 NFR")],
  ["design_scope", uiText("设计范围")],
  ["non_goals", uiText("非目标")],
  ["assumptions", uiText("假设")],
  ["constraints", uiText("约束")],
  ["selected_solution", uiText("选定方案")],
  ["selection_reason", uiText("选择理由")],
  ["rejected_solutions", uiText("拒绝方案")],
  ["impact_areas", uiText("影响范围")],
  ["risks", uiText("风险")],
  ["test_strategy", uiText("测试策略")],
  ["acceptance_criteria", uiText("验收标准")],
  ["open_questions", uiText("未解决问题")],
  ["reviewer", uiText("评审人")],
];

function designVersionDiffText(value) {
  if (Array.isArray(value)) {
    if (!value.length) return uiText("(无)");
    return value.every((item) => typeof item === "string")
      ? value.join("\n")
      : JSON.stringify(value, null, 2);
  }
  if (value === null || value === undefined || value === "") return uiText("(未设置)");
  return String(value);
}

function hideDesignCaseVersionDiff() {
  closeVersionDiffModal();
}

function syncDesignCaseVersionCompareControl() {
  const button = qs("#dci-compare-versions");
  if (!button) return;
  button.disabled = designCaseDetailState.selectedVersionNumbers.size !== 2;
}

function renderDesignCaseVersionDiff() {
  const selected = designCaseDetailState.versions
    .filter((version) => designCaseDetailState.selectedVersionNumbers.has(version.version_number))
    .sort((left, right) => left.version_number - right.version_number);
  if (selected.length !== 2) return;

  const [before, after] = selected;
  const content = openVersionDiffModal(
    uiText`版本差异：v${before.version_number} → v${after.version_number}`,
  );
  if (!content) return;
  let changed = 0;
  DESIGN_VERSION_DIFF_FIELDS.forEach(([field, label]) => {
    const beforeText = designVersionDiffText(before[field]);
    const afterText = designVersionDiffText(after[field]);
    if (beforeText === afterText) return;
    changed++;
    const section = el("section", "version-diff-field");
    section.appendChild(el("h3", "", label));
    const code = el("pre", "version-diff-code");
    gitLineDiff(beforeText, afterText).forEach(([kind, text]) => {
      const marker = kind === "add" ? "+" : kind === "remove" ? "-" : " ";
      const line = el("span", `version-diff-line version-diff-${kind}`);
      line.textContent = `${marker} ${text}`;
      code.appendChild(line);
    });
    section.appendChild(code);
    content.appendChild(section);
  });
  if (!changed) appendEmpty(content, uiText("所选版本没有内容差异。"));
}

async function confirmDesignCaseWithPrompts(design) {
  const payload = collectDesignConfirmPayload(design);
  if (!payload) return false;
  if (Object.keys(payload).length) {
    await apiJson(`/design-cases/${design.id}`, jsonOptions("PATCH", payload));
  }
  await apiJson(`/design-cases/${design.id}/confirm`, jsonOptions("POST"));
  return true;
}

function renderDesignCaseDetailActions() {
  const design = designCaseDetailState.design;
  const actions = qs("#dci-lifecycle-actions");
  const hint = qs("#dci-lifecycle-hint");
  if (!actions || !hint) return;

  clear(actions);
  const basePath = `/design-cases/${designCaseDetailState.designId}`;

  // Independent of the lifecycle branch below: suspect is orthogonal to status
  // (a confirmed design and a draft one can both have an upstream that moved).
  if (design.suspect && !design.deleted_at) {
    actions.appendChild(
      realignButton(basePath, reloadDesignCaseDetail, showDciError)
    );
  }

  if (design.deleted_at) {
    actions.appendChild(
      opButton(
        uiText("恢复"),
        "action-button",
        async () => {
          designCaseDetailState.design = await apiJson(`${basePath}/restore`, jsonOptions("POST"));
          await reloadDesignCaseDetail();
        },
        () => Promise.resolve(),
        showDciError
      )
    );
    hint.textContent = uiText("该设计用例已移入回收站，可在这里恢复；版本恢复请在恢复实体后再操作。");
    return;
  }

  if (design.status === "draft") {
    actions.append(
      opButton(
        uiText("需澄清"),
        "action-button",
        () => apiJson(basePath, jsonOptions("PATCH", { status: "needs_clarification" })),
        reloadDesignCaseDetail,
        showDciError
      ),
      opButton(
        uiText("确认"),
        "action-button",
        async () => confirmDesignCaseWithPrompts(design),
        reloadDesignCaseDetail,
        showDciError
      )
    );
    hint.textContent = uiText("草稿可先回列表页编辑；确认时会补齐必填字段，若信息不足可标为需澄清。");
  } else if (design.status === "needs_clarification") {
    actions.append(
      opButton(
        uiText("回到草稿"),
        "action-button",
        () => apiJson(basePath, jsonOptions("PATCH", { status: "draft" })),
        reloadDesignCaseDetail,
        showDciError
      ),
      opButton(
        uiText("确认"),
        "action-button",
        async () => confirmDesignCaseWithPrompts(design),
        reloadDesignCaseDetail,
        showDciError
      )
    );
    hint.textContent = uiText("当前标记为需澄清；补齐信息后可回到草稿，或直接确认当前设计。");
  } else if (design.status === "confirmed") {
    actions.append(
      opButton(
        uiText("重新打开"),
        "action-button",
        () => apiJson(`${basePath}/reopen`, jsonOptions("POST")),
        reloadDesignCaseDetail,
        showDciError
      ),
      opButton(
        uiText("新建任务"),
        "action-button",
        async () => {
          const payload = collectTaskPayload();
          if (payload === null) return null;
          return apiJson(`${basePath}/tasks`, jsonOptions("POST", payload));
        },
        reloadDesignCaseDetail,
        showDciError
      )
    );
    // A confirmed design creates exactly one design-backed TASK-* directly.
    // New capability scope uses an approved CR source-backed TASK; never create
    // a second task under the same design.
    hint.textContent = design.task_count
      ? uiText("已确认，已创建设计任务；新增能力范围请通过获批 CR 来源任务承接。")
      : uiText("已确认的设计已锁定内容；点击「新建任务」直接创建开发任务。");
  }

  actions.appendChild(
    opButton(
      uiText("删除"),
      "action-button danger",
      async () => {
        if (!confirmSoftDelete(businessNumber(design, "design_number", uiText("未编号设计用例")))) return false;
        designCaseDetailState.design = await apiJson(basePath, { method: "DELETE" });
        renderDesignCaseDetailState();
      },
      () => Promise.resolve(),
      showDciError
    )
  );
}

function renderDesignCaseDetailVersions() {
  const versions = designCaseDetailState.versions || [];
  const tableWrap = qs("#dci-versions-table");
  const emptyEl = qs("#dci-versions-empty");
  if (!tableWrap || !emptyEl) return;

  clear(tableWrap);
  if (!versions.length) {
    hide(tableWrap);
    show(emptyEl);
    return;
  }

  hide(emptyEl);
  show(tableWrap);
  const rows = versions.map((version) => {
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = designCaseDetailState.selectedVersionNumbers.has(
      version.version_number
    );
    checkbox.setAttribute("aria-label", uiText`选择版本 v${version.version_number}`);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        if (designCaseDetailState.selectedVersionNumbers.size >= 2) {
          checkbox.checked = false;
          showDciError(uiText("一次只能比较两个版本"));
          return;
        }
        designCaseDetailState.selectedVersionNumbers.add(version.version_number);
      } else {
        designCaseDetailState.selectedVersionNumbers.delete(version.version_number);
      }
      hideDesignCaseVersionDiff();
      syncDesignCaseVersionCompareControl();
    });
    const actions = el("div", "actions-cell");
    if (!version.is_current && !designCaseDetailState.design?.deleted_at) {
      actions.appendChild(
        opButton(
          uiText("恢复为当前版本"),
          "action-button",
          async () => {
            if (!confirmRestoreDesignCaseVersion(version.version_number)) return false;
            await apiJson(
              `/design-cases/${designCaseDetailState.designId}/versions/${version.version_number}/restore`,
              jsonOptions("POST")
            );
            await reloadDesignCaseDetail();
          },
          () => Promise.resolve(),
          showDciError
        )
      );
    }

    const summary = [];
    if (version.is_current) summary.push(uiText("当前"));
    if (version.change_type === "restore" && version.restored_from_version_number) {
      summary.push(uiText`从 v${version.restored_from_version_number} 恢复`);
    } else {
      summary.push(version.change_type);
    }

    return [
      checkbox,
      `v${version.version_number}`,
      summary.join(" / "),
      shortTime(version.created_at),
      previewText(version.problem_statement),
      previewText(version.selected_solution),
      actions,
    ];
  });
  tableWrap.appendChild(
    makeTable([uiText("选择"), uiText("版本"), uiText("变更"), uiText("创建时间"), uiText("问题陈述快照"), uiText("选定方案快照"), uiText("操作")], rows)
  );
  syncDesignCaseVersionCompareControl();
}

async function reloadDesignCaseDetail() {
  if (!designCaseDetailState.designId) return;
  clearDciError();
  const [design, versions] = await Promise.all([
    apiJson(`/design-cases/${designCaseDetailState.designId}`),
    apiJson(`/design-cases/${designCaseDetailState.designId}/versions`),
  ]);
  designCaseDetailState.design = design;
  designCaseDetailState.versions = versions;
  designCaseDetailState.selectedVersionNumbers = new Set();
  hideDesignCaseVersionDiff();
  renderDesignCaseDetailState();
}

async function renderDesignCasePage() {
  const page = qs("#design-case-page");
  if (!page) return;

  designCaseDetailState.designId = page.dataset.designId;
  const statusEl = qs("#dci-status", page);
  const content = qs("#dci-content", page);
  const compareButton = qs("#dci-compare-versions", page);
  if (compareButton) {
    compareButton.addEventListener("click", renderDesignCaseVersionDiff);
  }

  try {
    setStatus(statusEl, uiText("正在加载设计用例..."));
    await reloadDesignCaseDetail();

    hide(statusEl);
    show(content);
  } catch (err) {
    hide(content);
    setStatus(statusEl, uiText`加载设计用例失败：${err.message}`, "error");
  }
}

// --- development tasks list page --------------------------------------------

const developmentTasksListState = {
  projectId: null,
  tasks: [],
};

function showDtlError(message) {
  showToast(message);
  const errorEl = qs("#dtl-error");
  if (!errorEl) return;
  errorEl.textContent = message;
  show(errorEl);
}

function clearDtlError() {
  const errorEl = qs("#dtl-error");
  if (!errorEl) return;
  errorEl.textContent = "";
  hide(errorEl);
}

// "新建任务" entry point: pick a confirmed design + fill fields, one call =
// one claimable task (no intermediate object, no separate generation step).
const _dtlCreateModal = createModalManager({
  overlayId: "#dtl-create-modal",
  createTitle: uiText("新建开发任务"),
  focusSelector: "#dtlc-design",
  errorSelector: "#dtlc-modal-error",
  submitSelector: "#dtlc-modal-submit",
  cancelSelector: "#dtlc-modal-cancel",
  getPayload(form) {
    const designId = form.elements.design_id.value;
    if (!designId) {
      form.querySelector("#dtlc-modal-error").textContent = uiText("请选择一个已确认的设计");
      return null;
    }
    const title = form.elements.title.value.trim();
    if (!title) {
      form.querySelector("#dtlc-modal-error").textContent = uiText("标题不能为空");
      return null;
    }
    return {
      designId,
      payload: {
        title,
        task_type: form.elements.task_type.value,
        goal: form.elements.goal.value,
        acceptance_criteria: form.elements.acceptance_criteria.value,
        constraints: form.elements.constraints.value,
        non_goals: form.elements.non_goals.value,
        priority: form.elements.priority.value,
        complexity: form.elements.complexity.value,
        risk_level: form.elements.risk_level.value,
      },
    };
  },
  submit(mode, { designId, payload }) {
    return apiJson(`/design-cases/${designId}/tasks`, jsonOptions("POST", payload));
  },
  onSuccess() {
    reloadDevelopmentTasksListData();
  },
});

async function openCreateDevelopmentTask() {
  const select = qs("#dtlc-design");
  if (select) {
    clear(select);
    let confirmedDesigns = [];
    try {
      confirmedDesigns = (
        await apiJson(`/projects/${developmentTasksListState.projectId}/design-cases`)
      ).filter((d) => d.status === "confirmed");
    } catch (_) {
      confirmedDesigns = [];
    }
    if (!confirmedDesigns.length) {
      select.appendChild(el("option", null, uiText("（没有已确认的设计）")));
    } else {
      confirmedDesigns.forEach((design) => {
        const option = el(
          "option", null,
          `${textOr(design.design_number)} ${design.problem_statement ? previewText(design.problem_statement, 30) : ""}`
        );
        option.value = design.id;
        select.appendChild(option);
      });
    }
  }
  _dtlCreateModal.openCreate();
}

const _dtlModal = createModalManager({
  overlayId: "#dtl-modal",
  createTitle: "", // not used – edit only, create goes through _dtlCreateModal
  editTitle: uiText("编辑开发任务"),
  focusSelector: "#dtl-title",
  errorSelector: "#dtl-modal-error",
  submitSelector: "#dtl-modal-submit",
  cancelSelector: "#dtl-modal-cancel",
  getPayload(form) {
    const title = form.elements.title.value.trim();
    if (!title) {
      form.querySelector("#dtl-modal-error").textContent = uiText("标题不能为空");
      return null;
    }
    return {
      title,
      description: form.elements.description.value,
      acceptance_criteria: form.elements.acceptance_criteria.value,
      constraints: form.elements.constraints.value,
      non_goals: form.elements.non_goals.value,
      task_type: form.elements.task_type.value,
      priority: form.elements.priority.value,
      complexity: form.elements.complexity.value,
      risk_level: form.elements.risk_level.value,
    };
  },
  setFormData(form, item) {
    form.elements.title.value = item.title || "";
    form.elements.description.value = item.description || "";
    form.elements.acceptance_criteria.value = item.acceptance_criteria || "";
    form.elements.constraints.value = item.constraints || "";
    form.elements.non_goals.value = item.non_goals || "";
    form.elements.task_type.value = item.task_type || "feature";
    form.elements.priority.value = item.priority || "normal";
    form.elements.complexity.value = item.complexity || "medium";
    form.elements.risk_level.value = item.risk_level || "low";
  },
  submit(mode, payload, editId) {
    return apiJson(`/tasks/${editId}`, jsonOptions("PATCH", payload));
  },
  onSuccess() {
    reloadDevelopmentTasksListData();
  },
});

// Every lifecycle move available to one development task, as a button array.
//
// Shared by the list rows and the detail page's 状态操作 section. It used to
// live inline in the list renderer only, so the detail page — the place you
// land on to actually read a task before deciding anything — could not move it
// at all. One task must offer the same moves wherever it is shown; duplicating
// the chain in two renderers is how they drift apart.
//
// `task` must carry the live gate hints (claim_ready /
// waiting_on_feature_numbers) that /projects/<id>/tasks computes; they are
// display-only, the endpoints re-check and refuse on their own authority.
function canManageTask(task) {
  const user = window.harnoqUser;
  return !user || user.role === "admin" || (task.assignee_number
    ? task.assignee_number === user.user_number : task.status === "claimable");
}

function taskAssigneeLabel(task) {
  return userAssigneeLabel(task);
}

function taskLifecycleButtons(task, reload, onError) {
  const base = `/tasks/${task.id}`;
  const sourceBacked = task.source_kind && task.source_kind !== "design";
  const revisionLabel = sourceBacked ? uiText("退回修订") : uiText("驳回到设计");
  const post = (path, body) => apiJson(`${base}/${path}`, jsonOptions("POST", body || {}));
  const number = businessNumber(task, "task_number", uiText("未编号任务"));
  const btn = (label, path, opts = {}) => {
    const button = opButton(label, `action-button${opts.danger ? " danger" : ""}`,
      () => (opts.handler ? opts.handler() : post(path)), reload, onError);
    if (opts.title) button.title = opts.title;
    return button;
  };
  const buttons = [];

  if (window.harnoqUser?.role === "admin") {
    buttons.push(btn(uiText("分配"), null, { handler: () => window.harnoqAssignTask(task, reload) }));
  }
  if (task.status === "claimable") {
    if (task.claim_ready === false) {
      // Keep the actions row a uniform button row: a DISABLED 认领 instead of a
      // variable-width wait chip that wrapped and shoved 删除 onto a second line.
      const claimBtn = el("button", "action-button", uiText("认领"));
      claimBtn.type = "button";
      claimBtn.disabled = true;
      claimBtn.title =
        uiText`等待上游 Feature 完成：${(task.waiting_on_feature_numbers || []).join("、")}`;
      buttons.push(claimBtn);
    } else {
      buttons.push(btn(uiText("认领"), null, {
        handler: () => {
          if (window.harnoqUser) return post("claim");
          const assignee = window.prompt(uiText("认领人（负责人）"));
          if (assignee === null || !assignee.trim()) return false;
          return post("claim", { assignee: assignee.trim() });
        },
      }));
    }
  } else if (task.status === "claimed") {
    buttons.push(btn(sourceBacked ? uiText("开始技术检查") : uiText("开始 review"), "start-pre-review"));
  } else if (task.status === "pre_reviewing") {
    buttons.push(btn(sourceBacked ? uiText("通过技术检查") : uiText("批准开发"), null, {
      handler: () => {
        if (window.harnoqUser) return post("approve-pre-review");
        const reviewer = window.prompt(sourceBacked ? uiText("检查人") : uiText("评审人"));
        if (reviewer === null) return false;
        return post("approve-pre-review", { reviewer: reviewer.trim() });
      },
    }));
    // The other half of pre-review. Approving was the only expressible verdict
    // in the UI, so a reviewer who found the task wrong had nothing to press —
    // "退回" existed in the API and nowhere on screen. Its single destination is
    // The destination is source-neutral: design tasks return to their design;
    // source-backed tasks return to the editable task contract.
    buttons.push(btn(revisionLabel, null, {
      title: sourceBacked ? uiText("预审不通过：修订任务验收或来源义务后重新打开") : uiText("预审不通过：退回产出该任务的设计，修订并重新确认后可再打开"),
      handler: () => {
        const comment = window.prompt(
          uiText`${revisionLabel}「${number}」的预审？\n` +
            (sourceBacked ? uiText("任务会进入 needs_revision；修订任务验收或来源义务后，再用「重新打开」回到可领取状态。\n") : uiText("任务会进入 needs_revision，等对应设计修订并重新确认后，再用「重新打开」回到可领取状态。\n")) +
            uiText("只是文字表述有问题的，直接编辑任务即可，不必驳回。\n\n驳回理由必填：")
        );
        if (comment === null || !comment.trim()) return false;
        const reviewer = window.prompt(uiText("评审人")) || "";
        return post("reject-pre-review", {
          comment: comment.trim(),
          reviewer: reviewer.trim(),
        });
      },
    }));
  } else if (task.status === "rejected_to_design" || task.status === "needs_revision") {
    // The only way out of rejected_to_design: the server refuses unless the
    // linked design is confirmed again, so a disabled-until-ready hint would
    // just duplicate a check the endpoint already owns — let it 409.
    buttons.push(btn(uiText("重新打开"), "reopen", {
      title: sourceBacked ? uiText("修订任务内容或来源义务后，把任务退回可领取状态") : uiText("设计已修订并重新确认后，把任务退回可领取状态"),
    }));
  } else if (task.status === "ready_for_development") {
    // Opens the build unit, once. Coder and qa rounds inside it record runs
    // without moving the task, so there is nothing to re-enter.
    buttons.push(btn(uiText("开始编码"), "start-coding"));
  } else if (task.status === "in_progress") {
    // A passing qa run does not advance the task — the quality gate is an
    // explicit call. The server re-checks it (latest coder+qa passed, qa newer
    // than coder, no blocking_issues) and 409s with the reason.
    buttons.push(btn(uiText("标记可提 PR"), "mark-ready-for-pr", {
      title: uiText("质量门禁：coder 与 qa 最新一次都通过、qa 晚于 coder、无阻塞问题"),
    }));
    // pre_reviewing's revision action is unreachable once coding has started, so
    // a design defect found mid-build (e.g. qa reports deviates and the
    // design itself is the thing that's wrong) had nowhere to go before this.
    // Same single destination: the design that produced the task.
    buttons.push(btn(revisionLabel, null, {
      title: sourceBacked ? uiText("开发中发现任务验收或来源义务有问题：退回修订") : uiText("开发中发现设计本身有问题：退回产出该任务的设计，修订并重新确认后可再打开"),
      handler: () => {
        const reason = window.prompt(
          `${revisionLabel}「${number}」？\n` +
            (sourceBacked ? uiText("任务会进入 needs_revision，修订任务验收标准或来源义务后重新打开。已有的分支与提交不受影响，原样保留。\n原因必须说明哪项任务验收或来源义务不成立。\n\n原因：") : uiText("任务会进入 needs_revision，等对应设计修订并重新确认后重新打开。已有的分支与提交不受影响，原样保留。\n驳回原因必填，且需说明设计里具体哪里错了或缺了什么。\n\n原因："))
        );
        if (reason === null || !reason.trim()) return false;
        return post("reject-to-design", { reason: reason.trim() });
      },
    }));
  } else if (task.status === "ready_for_pr") {
    // The task stays in ready_for_pr through the whole PR phase, so all the
    // remaining steps live here. Harnoq never calls the git host: PR details
    // are typed in from `gh pr create` output, and 确认已合并 records a human's
    // observation on the host.
    buttons.push(btn(uiText("关联 PR"), null, {
      handler: () => {
        const prNumber = window.prompt(uiText("PR 编号（数字）"));
        if (prNumber === null) return false;
        const prUrl = window.prompt(uiText("PR 地址")) || "";
        const branchName = window.prompt(uiText("分支名")) || "";
        return post("link-pr", {
          pr_number: Number(prNumber.trim()),
          pr_url: prUrl.trim(),
          branch_name: branchName.trim(),
        });
      },
    }));
    buttons.push(btn(uiText("确认已合并"), "mark-pr-merged", {
      title: uiText("在 Git 托管上确认 PR 已合并后，记录这个事实"),
    }));
    buttons.push(btn(uiText("完成"), "mark-completed", {
      title: uiText("需要已关联并确认合并的 PR"),
    }));
    // ready_for_pr used to be a one-way door: a problem seen after the quality
    // gate closed could only be answered by merging work believed wrong and
    // then reopening it. This is the way back out, until the PR merges.
    buttons.push(btn(uiText("撤回重做"), null, {
      title: uiText("PR 合并前发现问题时，重新打开开发单元，继续编码与 QA"),
      handler: () => {
        const reason = window.prompt(
          uiText`撤回「${number}」的待 PR 交付？\n` +
            uiText("会重新打开开发单元，直接继续派 coder 与 qa（无需再点「开始编码」）；\n") +
            uiText("质量门与 qa 判定一并清空，新的 QA 必须重新逐条判定全部验收标准。\n") +
            uiText("已关联的 PR 记录保留。PR 若已合并则不能撤回——那种情况应先标记完成，再用「打回重做」。\n\n") +
            uiText("撤回原因必填,说明交付需要改什么：\n")
        );
        if (reason === null || !reason.trim()) return false;
        return post("withdraw-from-pr", { reason: reason.trim() });
      },
    }));
  } else if (task.status === "completed") {
    // Completion is retractable: integration testing routinely exposes that a
    // merged task did not actually satisfy its design, and without this the
    // only recorded options were to leave a known-wrong task marked done or
    // invent an unrelated one.
    buttons.push(btn(uiText("打回重做"), null, {
      title: sourceBacked ? uiText("发现任务验收或来源义务未满足时，退回修订") : uiText("集成测试等环节发现该任务并未真正满足其设计时，退回可领取状态重做"),
      handler: () => {
        const reason = window.prompt(
          uiText`打回「${number}」重做？\n` +
            uiText("会退回可领取状态，重新经过领取与预审；已合并的 PR 记录保留，重做会产生新的 PR。\n") +
            uiText("其所属 Feature 将不再满足，尚未领取的下游任务会被重新阻塞。\n\n") +
            (sourceBacked ? uiText("打回原因必填，且需引用未满足的 TASK 验收标准或来源义务。\n\n原因：") : uiText("打回原因必填，且需引用未满足的设计条款。\n若指不出条款，说明该需求从未被设计过：那属于设计变更，应改设计后新增来源任务，而不是在此打回。\n\n原因："))
        );
        if (reason === null || !reason.trim()) return false;
        return post("reopen-completed", { reason: reason.trim() });
      },
    }));
  }

  // Available anywhere before coding starts. Appended after the forward moves
  // so the row reads "advance it, or hand it back".
  if (["claimed", "pre_reviewing", "ready_for_development"].includes(task.status)) {
    const approved = task.status === "ready_for_development";
    buttons.push(btn(uiText("放弃认领"), null, {
      title: approved
        ? uiText("把任务交回可领取状态，换人来做；预审结论会一并作废，下一个人需重新预审")
        : uiText("把任务交回可领取状态，换人来做；此时尚无任何产出，不涉及设计对错"),
      handler: () => {
        if (!window.confirm(
          uiText`放弃「${number}」的认领？\n` +
            uiText`任务回到可领取状态，清空认领人${task.assignee ? uiText`（当前：${task.assignee}）` : ""}，` +
            uiText("谁都可以重新领取。\n") +
            (approved
              ? uiText("预审结论会一并作废，下一个领取人需要重新走预审——") +
                uiText("批准是某个人对某个任务的判断，不该被下一个人默认继承。\n")
              : "") +
            uiText("尚未产出任何代码，所以这不涉及任务契约是否正确——") +
            uiText`认为任务本身有问题应当用「${revisionLabel}」。`
        )) return false;
        return post("release");
      },
    }));
  }

  if (!canManageTask(task)) {
    buttons.forEach((button) => {
      button.disabled = true;
      button.title = uiText("仅任务负责人或管理员可以操作");
    });
  }
  return buttons;
}

// What the current status means and what the buttons above will do. Shown on
// the detail page only — the list already carries this context in its columns.
const TASK_STATUS_HINTS = {
  claimable: uiText("可领取：认领后进入适用的预审或技术检查。上游 Feature 未完成时不能领取。"),
  claimed: uiText("已领取：设计任务开始人工预审；来源任务由管理者检查 TASK 契约和直接来源；此时尚无产出，可直接放弃认领换人。"),
  pre_reviewing: uiText("预审/技术检查中：通过则进入待开发；发现任务契约、设计或来源义务有问题则退回修订；只是想换人做则放弃认领。"),
  rejected_to_design: uiText("已退回修订（旧状态）：修订后重新打开回到可领取。"),
  needs_revision: uiText("待修订：设计任务修订并重新确认；来源任务直接修订任务验收/来源义务后重新打开。"),
  ready_for_development: uiText("待开发：开始编码后进入开发单元，coder 与 qa 的轮次都在其中记录，不再改变状态。此时仍可放弃认领，但预审结论会作废。"),
  in_progress: uiText("开发中：coder 与 qa 反复运行；两者最新一次都通过且 qa 晚于 coder 时，才能标记可提 PR。发现任务验收或来源义务有问题则退回修订。"),
  ready_for_pr: uiText("待 PR：关联 PR、确认已合并、再标记完成；合并前发现问题可撤回重做。"),
  completed: uiText("已完成：PR 已被接受。事后发现未满足设计条款或来源义务时可打回重做。"),
};

function renderDevelopmentTasksListState() {
  const tasks = developmentTasksListState.tasks;
  qs("#dtl-count").textContent = tasks.length;
  const section = qs("#dtl-table-section");
  if (!tasks.length) {
    appendEmpty(section, uiText("暂无开发任务。"));
    return;
  }

  clear(section);
  section.appendChild(
    makeTable(
      // Feature sits next to 编号: the blocked note in the 状态 cell names the
      // upstream FEATURE-* it waits on, which reads as noise unless the row
      // also says which Feature the task itself belongs to.
      [uiText("编号"), uiText("来源"), "Feature", uiText("标题"), uiText("类型"), uiText("优先级"), uiText("复杂度"), uiText("状态"), uiText("经办人"), uiText("操作")],
      tasks.map((task) => {
        const actions = el("div", "actions");
        const editBtn = el("button", "action-button", uiText("编辑"));
        editBtn.type = "button";
        const editable = canManageTask(task) && (task.status === "claimable" ||
          (task.status === "needs_revision" && task.source_kind !== "design"));
        editBtn.disabled = !editable;
        editBtn.title = editable ? uiText("编辑任务内容") : uiText("任务已领取或需先修订设计，当前不可编辑");
        if (editable) editBtn.addEventListener("click", () => _dtlModal.openEdit(task));
        actions.appendChild(editBtn);

        taskLifecycleButtons(task, reloadDevelopmentTasksListData, showDtlError).forEach(
          (button) => actions.appendChild(button)
        );

        const deleteTask = deleteButton(task.title, `/tasks/${task.id}`, reloadDevelopmentTasksListData, showDtlError);
        deleteTask.disabled = !canManageTask(task);
        actions.appendChild(deleteTask);
        // Blocked-by-upstream reason lives in the status cell (wraps cleanly)
        // rather than crammed among the action buttons.
        let statusCell = statusBadge(task.status);
        if (task.suspect) {
          const wrap = el("div", "status-cell");
          wrap.appendChild(statusCell);
          wrap.appendChild(suspectBadge(task));
          statusCell = wrap;
          actions.appendChild(
            realignButton(
              `/tasks/${task.id}`,
              reloadDevelopmentTasksListData,
              showDtlError,
              task.status === "completed"
            )
          );
        }
        if (task.claim_ready === false) {
          // Shown for in-flight tasks too, not just claimable ones. An
          // upstream Feature can stop being satisfied AFTER this task was
          // claimed (its upstream task got reopened), and then the completion
          // gate refuses — so the reason has to be visible before someone
          // walks into that 409 with a finished branch in hand.
          const claimable = task.status === "claimable";
          statusCell = el("div", "status-cell");
          statusCell.appendChild(statusBadge(task.status));
          const listSuspect = suspectBadge(task);
          if (listSuspect) statusCell.appendChild(listSuspect);
          statusCell.appendChild(
            el("span", "blocked-note",
              `${claimable ? uiText("等待上游") : uiText("上游已撤回")}：` +
              `${(task.waiting_on_feature_numbers || []).join("、")}`)
          );
        }
        const titleLink = el("a", "", task.title);
        titleLink.href = `/ui/tasks/${task.id}`;
        const sourceParts = [];
        if (task.source_kind) sourceParts.push(task.source_kind);
        sourceParts.push(...(task.change_requests || []));
        if (task.dependencies?.length) sourceParts.push(uiText`依赖：${task.dependencies.map((item) => item.task_number).join("、")}`);
        return [
          numberLink(task.task_number, `/ui/tasks/${task.id}`),
          textOr(sourceParts.join("、") || (task.design_number ? `design · ${task.design_number}` : "-")),
          textOr(task.feature_number),
          titleLink,
          chip(task.task_type),
          chip(task.priority, `chip-${cssToken(task.priority)}`),
          chip(task.complexity),
          statusCell,
          taskAssigneeLabel(task),
          actions,
        ];
      })
    )
  );
}

async function reloadDevelopmentTasksListData() {
  if (!developmentTasksListState.projectId) return;
  clearDtlError();
  developmentTasksListState.tasks = await apiJson(
    `/projects/${developmentTasksListState.projectId}/tasks`
  );
  renderDevelopmentTasksListState();
  await renderEntityTrash(qs("#dtl-trash"), {
    url: `/projects/${developmentTasksListState.projectId}/tasks/trash`,
    base: (id) => `/tasks/${id}`,
    label: (x) => x.title,
    reload: reloadDevelopmentTasksListData,
    view: (x) => [
      [uiText("状态"), x.status], [uiText("类型"), x.task_type],
      [uiText("描述"), x.description], [uiText("验收标准"), x.acceptance_criteria],
      [uiText("约束"), x.constraints], [uiText("非目标"), x.non_goals],
    ],
  });
}

async function renderDevelopmentTasksListPage() {
  const page = qs("#development-tasks-list-page");
  if (!page) return;

  developmentTasksListState.projectId = page.dataset.projectId;
  renderProjectFlowNav(developmentTasksListState.projectId, "tasks");
  const statusEl = qs("#dtl-status", page);
  const content = qs("#dtl-content", page);
  _dtlModal.init();
  _dtlCreateModal.init();
  const createBtn = qs("#dtl-create-btn", page);
  if (createBtn) createBtn.addEventListener("click", openCreateDevelopmentTask);

  try {
    setStatus(statusEl, uiText("正在加载开发任务..."));
    await reloadDevelopmentTasksListData();

    hide(statusEl);
    show(content);
  } catch (err) {
    hide(content);
    setStatus(statusEl, uiText`加载开发任务失败：${err.message}`, "error");
  }
}

const requirementInputState = {
  inputId: null,
  item: null,
  versions: [],
  selectedVersionNumbers: new Set(),
};

function showRiError(message) {
  showToast(message);
  const errorEl = qs("#ri-error");
  if (!errorEl) return;
  errorEl.textContent = message;
  show(errorEl);
}

function clearRiError() {
  const errorEl = qs("#ri-error");
  if (!errorEl) return;
  errorEl.textContent = "";
  hide(errorEl);
}

function renderSourceRequirementState() {
  const item = requirementInputState.item;
  renderProjectFlowNav(item.project_id, "inputs");
  const label = businessNumber(item, "source_requirement_number", uiText("未编号原始需求"));
  qs("#ri-title").textContent = `${label} ${item.title || ""}`.trim();
  qs("#ri-status-badge").replaceChildren(gateStatusBadge(item.status, item.deleted_at));
  qs("#ri-back-link").href = `/ui/projects/${item.project_id}/source-requirements`;

  // Read-only display fields
  qs("#ri-display-title").textContent = item.title;
  qs("#ri-display-type").textContent = item.source_type;
  qs("#ri-display-assignee").textContent = userAssigneeLabel(item);
  qs("#ri-display-source").textContent = item.source || "-";
  qs("#ri-display-version").textContent = item.current_version_number || "-";
  renderMarkdown(qs("#ri-display-content"), item.raw_content);

  renderSourceRequirementActions();
  renderSourceRequirementVersions();
}

function renderSourceRequirementActions() {
  const item = requirementInputState.item;
  const actions = qs("#ri-lifecycle-actions");
  const hint = qs("#ri-lifecycle-hint");
  if (!actions || !hint) return;

  clear(actions);
  const basePath = `/source-requirements/${requirementInputState.inputId}`;

  if (item.deleted_at) {
    actions.appendChild(
      opButton(
        uiText("恢复"),
        "action-button",
        async () => {
          requirementInputState.item = await apiJson(`${basePath}/restore`, jsonOptions("POST"));
          await reloadSourceRequirementDetail();
        },
        () => Promise.resolve(),
        showRiError
      )
    );
    hint.textContent = uiText("当前条目已在回收站，可在这里恢复。");
    return;
  }

  if (item.status === "draft") {
    actions.append(
      opButton(
        uiText("需澄清"),
        "action-button",
        () => apiJson(basePath, jsonOptions("PATCH", { status: "needs_clarification" })),
        reloadSourceRequirementDetail,
        showRiError
      ),
      opButton(
        uiText("确认"),
        "action-button",
        () => apiJson(`${basePath}/confirm`, jsonOptions("POST")),
        reloadSourceRequirementDetail,
        showRiError
      )
    );
    hint.textContent = uiText("草稿可先回列表页编辑；聊透彻后再确认——确认没有必填字段检查，只是状态翻转。");
  } else if (item.status === "needs_clarification") {
    actions.append(
      opButton(
        uiText("回到草稿"),
        "action-button",
        () => apiJson(basePath, jsonOptions("PATCH", { status: "draft" })),
        reloadSourceRequirementDetail,
        showRiError
      ),
      opButton(
        uiText("确认"),
        "action-button",
        () => apiJson(`${basePath}/confirm`, jsonOptions("POST")),
        reloadSourceRequirementDetail,
        showRiError
      )
    );
    hint.textContent = uiText("当前标记为需澄清；补齐信息后可回到草稿，或直接确认当前内容。");
  } else if (item.status === "confirmed") {
    actions.appendChild(
      opButton(
        uiText("重新打开"),
        "action-button",
        () => apiJson(`${basePath}/reopen`, jsonOptions("POST")),
        reloadSourceRequirementDetail,
        showRiError
      )
    );
    hint.textContent = uiText("已确认并锁定；如需修改内容，先重新打开。项目需所有 INPUT 都确认后才能进入项目规范阶段。");
  }

  actions.appendChild(
    opButton(
      uiText("删除"),
      "action-button danger",
      async () => {
        if (!confirmSoftDelete(item.title)) return false;
        requirementInputState.item = await apiJson(basePath, { method: "DELETE" });
        renderSourceRequirementState();
      },
      () => Promise.resolve(),
      showRiError
    )
  );
}

function confirmRestoreSourceRequirementVersion(versionNumber) {
  return window.confirm(
    uiText`恢复到版本 ${versionNumber}？\n系统会复制该版本内容，并生成一个新的当前版本。`
  );
}

function renderSourceRequirementVersions() {
  const versions = requirementInputState.versions || [];
  const tableWrap = qs("#ri-versions-table");
  const emptyEl = qs("#ri-versions-empty");
  if (!tableWrap || !emptyEl) return;

  clear(tableWrap);
  if (!versions.length) {
    hide(tableWrap);
    show(emptyEl);
    return;
  }

  hide(emptyEl);
  show(tableWrap);
  const rows = versions.map((version) => {
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = requirementInputState.selectedVersionNumbers.has(
      version.version_number
    );
    checkbox.setAttribute("aria-label", uiText`选择版本 v${version.version_number}`);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        if (requirementInputState.selectedVersionNumbers.size >= 2) {
          checkbox.checked = false;
          showRiError(uiText("一次只能比较两个版本"));
          return;
        }
        requirementInputState.selectedVersionNumbers.add(version.version_number);
      } else {
        requirementInputState.selectedVersionNumbers.delete(version.version_number);
      }
      hideSourceRequirementVersionDiff();
      syncSourceRequirementVersionCompareControl();
    });
    const actions = el("div", "actions-cell");
    if (!version.is_current && !requirementInputState.item?.deleted_at) {
      actions.appendChild(
        opButton(
          uiText("恢复为当前版本"),
          "action-button",
          async () => {
            if (!confirmRestoreSourceRequirementVersion(version.version_number)) return false;
            await apiJson(
              `/source-requirements/${requirementInputState.inputId}/versions/${version.version_number}/restore`,
              jsonOptions("POST")
            );
            await reloadSourceRequirementDetail();
          },
          () => Promise.resolve(),
          showRiError
        )
      );
    }

    const summary = [];
    if (version.is_current) summary.push(uiText("当前"));
    if (version.change_type === "restore" && version.restored_from_version_number) {
      summary.push(uiText`从 v${version.restored_from_version_number} 恢复`);
    } else {
      summary.push(version.change_type);
    }

    return [
      checkbox,
      `v${version.version_number}`,
      summary.join(" / "),
      shortTime(version.created_at),
      version.title,
      previewText(version.raw_content),
      actions,
    ];
  });
  tableWrap.appendChild(
    makeTable([uiText("选择"), uiText("版本"), uiText("变更"), uiText("创建时间"), uiText("标题"), uiText("内容快照"), uiText("操作")], rows)
  );
  syncSourceRequirementVersionCompareControl();
}

const SOURCE_REQUIREMENT_VERSION_DIFF_FIELDS = [
  ["title", uiText("标题")],
  ["source_type", uiText("类型")],
  ["raw_content", uiText("需求原文")],
  ["attachments", uiText("附件")],
  ["source", uiText("来源")],
  ["submitted_by", uiText("提交人")],
];

function sourceRequirementVersionDiffText(value) {
  if (Array.isArray(value)) return value.length ? value.join("\n") : uiText("(无)");
  if (value === null || value === undefined || value === "") return uiText("(未设置)");
  return String(value);
}

function hideSourceRequirementVersionDiff() {
  closeVersionDiffModal();
}

function syncSourceRequirementVersionCompareControl() {
  const button = qs("#ri-compare-versions");
  if (!button) return;
  button.disabled = requirementInputState.selectedVersionNumbers.size !== 2;
}

function renderSourceRequirementVersionDiff() {
  const selected = requirementInputState.versions
    .filter((version) => requirementInputState.selectedVersionNumbers.has(version.version_number))
    .sort((left, right) => left.version_number - right.version_number);
  if (selected.length !== 2) return;

  const [before, after] = selected;
  const content = openVersionDiffModal(
    uiText`版本差异：v${before.version_number} → v${after.version_number}`,
  );
  if (!content) return;
  let changed = 0;
  SOURCE_REQUIREMENT_VERSION_DIFF_FIELDS.forEach(([field, label]) => {
    const beforeText = sourceRequirementVersionDiffText(before[field]);
    const afterText = sourceRequirementVersionDiffText(after[field]);
    if (beforeText === afterText) return;
    changed++;
    const section = el("section", "version-diff-field");
    section.appendChild(el("h3", "", label));
    const code = el("pre", "version-diff-code");
    gitLineDiff(beforeText, afterText).forEach(([kind, text]) => {
      const marker = kind === "add" ? "+" : kind === "remove" ? "-" : " ";
      const line = el("span", `version-diff-line version-diff-${kind}`);
      line.textContent = `${marker} ${text}`;
      code.appendChild(line);
    });
    section.appendChild(code);
    content.appendChild(section);
  });
  if (!changed) appendEmpty(content, uiText("所选版本没有内容差异。"));
}

async function reloadSourceRequirementDetail() {
  if (!requirementInputState.inputId) return;
  clearRiError();
  const [item, versions] = await Promise.all([
    apiJson(`/source-requirements/${requirementInputState.inputId}`),
    apiJson(`/source-requirements/${requirementInputState.inputId}/versions`),
  ]);
  requirementInputState.item = item;
  requirementInputState.versions = versions;
  requirementInputState.selectedVersionNumbers = new Set();
  hideSourceRequirementVersionDiff();
  renderSourceRequirementState();
}

async function renderSourceRequirementPage() {
  const page = qs("#source-requirement-page");
  if (!page) return;

  requirementInputState.inputId = page.dataset.inputId;
  const statusEl = qs("#ri-status", page);
  const content = qs("#ri-content", page);
  const compareButton = qs("#ri-compare-versions", page);
  if (compareButton) {
    compareButton.addEventListener("click", renderSourceRequirementVersionDiff);
  }

  try {
    setStatus(statusEl, uiText("正在加载原始需求..."));
    await reloadSourceRequirementDetail();

    hide(statusEl);
    show(content);
  } catch (err) {
    hide(content);
    setStatus(statusEl, uiText`加载原始需求失败：${err.message}`, "error");
  }
}

// --- development task detail page -------------------------------------------

const developmentTaskDetailState = {
  taskId: null,
  task: null,
  // Whether this task's upstream Features are all satisfied — computed live,
  // never stored (docs/cn/design-evolution.md#dependencies).
  claimReady: true,
  waitingOnFeatureNumbers: [],
};

function showDtiError(message) {
  showToast(message);
  const errorEl = qs("#dti-error");
  if (!errorEl) return;
  errorEl.textContent = message;
  show(errorEl);
}

function clearDtiError() {
  const errorEl = qs("#dti-error");
  if (!errorEl) return;
  errorEl.textContent = "";
  hide(errorEl);
}

function renderDevelopmentTaskDetailState() {
  const task = developmentTaskDetailState.task;
  renderProjectFlowNav(task.project_id, "tasks");
  qs("#dti-title").textContent = businessNumber(task, "task_number", uiText("未编号任务"));
  qs("#dti-status-badge").replaceChildren(gateStatusBadge(task.status, task.deleted_at));
  qs("#dti-back-link").href = `/ui/projects/${task.project_id}/tasks`;
  renderDevelopmentTaskStatusProgress(task.status);

  qs("#dti-display-number").textContent = businessNumber(task, "task_number", uiText("未编号任务"));
  qs("#dti-display-title").textContent = textOr(task.title);
  qs("#dti-display-type").textContent = textOr(task.task_type);
  qs("#dti-display-priority").textContent = textOr(task.priority);
  qs("#dti-display-complexity").textContent = textOr(task.complexity);
  qs("#dti-display-risk").textContent = textOr(task.risk_level);
  qs("#dti-display-assignee").textContent = taskAssigneeLabel(task);
  qs("#dti-display-source-kind").textContent = textOr(task.source_kind);
  const sources = qs("#dti-display-sources");
  clear(sources);
  const sourceGroups = [
    ["CR", task.change_requests || []],
    [uiText("依赖 TASK"), (task.dependencies || []).map((dependency) => dependency.task_number)],
  ];
  const shown = sourceGroups.filter(([, values]) => values.length);
  if (!shown.length) sources.textContent = task.design_number ? `DESIGN · ${task.design_number}` : uiText("无直接来源");
  shown.forEach(([label, values]) => sources.appendChild(el("div", "source-group", `${label}：${values.join("、")}`)));

  const requirementEl = qs("#dti-display-requirement");
  if (task.design_case_id && task.design_number) {
    requirementEl.replaceChildren(
      numberLink(task.design_number, `/ui/design-cases/${task.design_case_id}`)
    );
    const viewBtn = el("button", "action-button", uiText("查看设计"));
    viewBtn.type = "button";
    viewBtn.addEventListener("click", () => openDesignViewModal(task.design_case_id));
    requirementEl.append(" ", viewBtn);
  } else {
    requirementEl.textContent = "-";
  }

  renderMarkdown(qs("#dti-display-description"), task.description);
  renderMarkdown(qs("#dti-display-acceptance"), task.acceptance_criteria);
  renderMarkdown(qs("#dti-display-constraints"), task.constraints);
  renderMarkdown(qs("#dti-display-non-goals"), task.non_goals);

  renderDevelopmentTaskActions();
  renderDevelopmentTaskGate();
  renderDevelopmentTaskTimeline();
  renderRelatedChangeRequests(
    "dti", `/tasks/${developmentTaskDetailState.taskId}/change-requests`
  );
}

// The detail page offers the same lifecycle moves as the list row (shared
// taskLifecycleButtons), plus the recycle-bin pair. Reading the task and acting
// on it were split across two pages before: you came here to see the design,
// the timeline and the gate, then had to go back to the list to press anything.
function renderDevelopmentTaskActions() {
  const task = developmentTaskDetailState.task;
  const actions = qs("#dti-lifecycle-actions");
  const hint = qs("#dti-lifecycle-hint");
  if (!actions || !hint) return;

  clear(actions);
  const basePath = `/tasks/${developmentTaskDetailState.taskId}`;

  taskLifecycleButtons(task, reloadDevelopmentTaskDetail, showDtiError).forEach(
    (button) => actions.appendChild(button)
  );
  if (task.suspect) {
    actions.appendChild(
      realignButton(
        basePath,
        reloadDevelopmentTaskDetail,
        showDtiError,
        task.status === "completed"
      )
    );
  }
  // Deleting leaves this page with nothing to show — GET /tasks/<id> filters
  // soft-deleted rows — so it navigates to the list, whose recycle bin is
  // where a restore is done.
  actions.appendChild(
    opButton(
      uiText("删除"),
      "action-button danger",
      async () => {
        if (!confirmSoftDelete(task.title)) return false;
        await apiJson(basePath, { method: "DELETE" });
        window.location.href = `/ui/projects/${task.project_id}/tasks`;
      },
      () => Promise.resolve(),
      showDtiError
    )
  );

  let text = TASK_STATUS_HINTS[task.status] || "";
  if (task.source_kind && task.source_kind !== "design" && task.status === "pre_reviewing") {
    text = uiText("来源任务技术检查中：由管理者核对 TASK 验收标准与直接来源，不需要用户重复批准。");
  }
  if (task.suspect) {
    text += `${text ? " " : ""}${suspectHintText(task)}`;
  }
  if (task.claim_ready === false) {
    const waiting = (developmentTaskDetailState.waitingOnFeatureNumbers || []).join("、");
    text += uiText`${text ? " " : ""}上游 Feature 未满足（${waiting}）：` +
      uiText("领取与完成都会被拒绝。");
  }
  hint.textContent = text;
}

// Renders every task status from TASK_STATUS_ORDER as a horizontal progress
// track, highlighting whichever one matches ``task.status``. The two
// branch/rejection statuses aren't part of the linear happy-path sequence
// (a task rejected mid-review jumps back to design/task-planning rather than
// advancing), so they're only shown — appended as a separate "branch" chip —
// when the task is actually in one of them; otherwise they're omitted to
// keep the main track a clean straight line.
function statusProgressStep(step, stateClass) {
  const stepEl = el("span", `status-progress-step ${stateClass}`);
  stepEl.append(el("span", "status-progress-dot"), el("span", "status-progress-label", textOr(step)));
  return stepEl;
}

function renderDevelopmentTaskStatusProgress(status) {
  const wrap = qs("#dti-status-progress");
  if (!wrap) return;
  const BRANCH_STATUSES = new Set(["rejected_to_design", "needs_revision"]);
  const mainSteps = TASK_STATUS_ORDER.filter((s) => !BRANCH_STATUSES.has(s));
  const isBranch = BRANCH_STATUSES.has(status);
  // How far the pipeline has advanced along the linear track. A branch/reject
  // status left the track at pre_reviewing, so that's where "done" reaches.
  let currentIndex = mainSteps.indexOf(status);
  if (currentIndex === -1) currentIndex = mainSteps.indexOf("pre_reviewing");

  const track = el("div", "status-progress-track");
  mainSteps.forEach((step, i) => {
    if (i > 0) {
      const conn = el("span", "status-progress-connector");
      if (i <= currentIndex) conn.classList.add("filled");
      track.appendChild(conn);
    }
    let state = "upcoming";
    if (i < currentIndex) state = "done";
    else if (i === currentIndex) state = isBranch ? "done" : "current";
    track.appendChild(statusProgressStep(step, state));
  });
  wrap.replaceChildren(track);

  if (isBranch) {
    const branch = el("div", "status-progress-branch");
    branch.append(
      el("span", "status-progress-branch-arrow", "↳"),
      statusProgressStep(status, "branch")
    );
    wrap.appendChild(branch);
  }
}

// Feature dependency order is never stored on the task — computed live (see
// docs/cn/design-evolution.md#dependencies). This just displays
// the batched result already fetched in reloadDevelopmentTaskDetail.
function renderDevelopmentTaskGate() {
  const wrap = qs("#dti-feature-gate");
  if (!wrap) return;
  const waitingOn = developmentTaskDetailState.waitingOnFeatureNumbers || [];
  // Name the task's OWN Feature first: an upstream list on its own leaves the
  // reader guessing which Feature is doing the waiting.
  const own = developmentTaskDetailState.featureNumber;
  const ownPrefix = own ? uiText`本任务属于 ${own}。` : "";
  if (developmentTaskDetailState.claimReady || !waitingOn.length) {
    wrap.textContent = uiText`${ownPrefix}无未满足的上游 Feature 依赖。`;
    return;
  }
  wrap.textContent = uiText`${ownPrefix}等待上游 Feature 完成：${waitingOn.join("、")}。`;
}

// The task row cannot answer "when did this become X": status holds only the
// current value and updated_at only the last write. This renders the merged
// timeline (status transitions + agent runs + PR records) the server assembles.
// It lives on the detail page, not the list — reference material for judging a
// delivery, not something every row needs to carry.

// Which change requests rewrote this object. Read WHY, never reconcile WHAT:
// a request carries no content, so after it is applied the page above IS the
// outcome. Fetched per detail page rather than folded into the object payload
// — the scan would otherwise run once per row on the list pages.
async function renderRelatedChangeRequests(prefix, url) {
  const wrap = qs(`#${prefix}-change-requests`);
  const empty = qs(`#${prefix}-change-requests-empty`);
  if (!wrap) return;
  wrap.replaceChildren();
  let rows = [];
  try {
    rows = await apiJson(url);
  } catch (err) {
    wrap.appendChild(el("p", "error", uiText`加载相关变更单失败：${err.message}`));
    if (empty) hide(empty);
    return;
  }
  if (!rows.length) {
    if (empty) show(empty);
    return;
  }
  if (empty) hide(empty);
  rows.forEach((cr) => {
    const card = el("div", "cr-related");
    const head = el("div", "cr-related__head");
    const link = el("a", "", cr.change_request_number);
    link.href = `/ui/projects/${cr.project_id}/change-requests`;
    head.appendChild(link);
    head.appendChild(el("span", "cr-related__status", cr.status));
    if (cr.target_number) {
      head.appendChild(el("span", "cr-related__target", uiText`主目标 ${cr.target_number}`));
    }
    card.appendChild(head);
    card.appendChild(el("div", "cr-related__reason", cr.reason || ""));
    if (cr.resolution) {
      card.appendChild(el("div", "cr-related__resolution", cr.resolution));
    }
    wrap.appendChild(card);
  });
}

function renderDevelopmentTaskTimeline() {
  const wrap = qs("#dti-timeline");
  if (!wrap) return;
  const entries = developmentTaskDetailState.timeline || [];
  if (!entries.length) {
    wrap.replaceChildren(
      el("p", "empty", uiText("暂无记录。"))
    );
    return;
  }
  const kindLabels = {
    status: uiText("状态"),
    agent_run: "agent",
    pull_request: "PR",
  };
  wrap.replaceChildren(
    makeTable(
      [uiText("时间"), uiText("类型"), uiText("事件"), uiText("说明")],
      entries.map((entry) => [
        shortTime(entry.at),
        kindLabels[entry.kind] || entry.kind,
        entry.title,
        entry.detail || "-",
      ])
    )
  );
}

async function reloadDevelopmentTaskDetail() {
  const id = developmentTaskDetailState.taskId;
  if (!id) return;
  clearDtiError();
  const task = await apiJson(`/tasks/${id}`);
  // projectTasks (for the gate hints) and timeline don't depend on each
  // other, so fetch them together rather than serially.
  const [projectTasks, timeline] = await Promise.all([
    apiJson(`/projects/${task.project_id}/tasks`).catch(() => []),
    apiJson(`/tasks/${id}/timeline`).catch(() => []),
  ]);
  const gateInfo = (projectTasks || []).find(
    (candidate) => Number(candidate.id) === Number(task.id)
  );
  // taskLifecycleButtons reads the gate hints off the task itself (that is how
  // the list hands them over), so copy the batched result onto it.
  task.claim_ready = gateInfo ? gateInfo.claim_ready : true;
  task.waiting_on_feature_numbers = gateInfo ? gateInfo.waiting_on_feature_numbers : [];
  developmentTaskDetailState.task = task;
  developmentTaskDetailState.claimReady = gateInfo ? gateInfo.claim_ready : true;
  developmentTaskDetailState.waitingOnFeatureNumbers = gateInfo
    ? gateInfo.waiting_on_feature_numbers
    : [];
  developmentTaskDetailState.featureNumber = gateInfo ? gateInfo.feature_number : null;
  developmentTaskDetailState.timeline = timeline;
  renderDevelopmentTaskDetailState();
}

async function renderDevelopmentTaskPage() {
  const page = qs("#development-task-page");
  if (!page) return;

  developmentTaskDetailState.taskId = page.dataset.taskId;
  const statusEl = qs("#dti-status", page);
  const content = qs("#dti-content", page);

  try {
    setStatus(statusEl, uiText("正在加载开发任务..."));
    await reloadDevelopmentTaskDetail();

    hide(statusEl);
    show(content);
  } catch (err) {
    hide(content);
    setStatus(statusEl, uiText`加载开发任务失败：${err.message}`, "error");
  }
}

// Config page: copy any server-rendered snippet (system prompt, MCP config
// JSON/TOML) to the clipboard. Every `.copy-button` carries a `data-target`
// selector. Falls back to selecting the text when the Clipboard API is
// unavailable (e.g. plain-HTTP contexts).
function initConfigPage() {
  const buttons = document.querySelectorAll(".copy-button[data-target]");
  if (!buttons.length) return;

  buttons.forEach((button) => {
    const target = qs(button.dataset.target);
    if (!target) return;

    const flash = (label) => {
      button.textContent = label;
      setTimeout(() => { button.textContent = uiText("复制"); }, 1500);
    };

    button.addEventListener("click", async () => {
      const text = target.textContent;
      try {
        await navigator.clipboard.writeText(text);
        flash(uiText("已复制"));
      } catch (err) {
        const range = document.createRange();
        range.selectNodeContents(target);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        flash(uiText("请手动复制"));
      }
    });
  });
}

// --- change requests: list + modal ------------------------------------------
// A change request exists for the case where something is already built on what
// you want to change. Editing a draft needs none of this, and the page says so.
// Applying one records that the edit was made; it never edits anything itself.

const changeRequestsListState = {
  projectId: null,
  projectNumber: null,
  changeRequests: [],
  designCases: [],
  features: [],
};

const CHANGE_REQUEST_STAGE_HINTS = {
  proposed: uiText("已提出：下一步是分析影响。"),
  analyzing: uiText("已分析：把影响清单给用户看，由他决定要不要批准。"),
  approved: uiText("已批准：去做那处上游修改，做完标记为已应用。"),
  applied: uiText("已应用：下游会自己亮起“上游已变”，逐个消化。"),
  closed: uiText("已关闭。"),
};

function showCrlError(message) {
  showToast(message);
  const errorEl = qs("#crl-error");
  if (!errorEl) return;
  errorEl.textContent = message;
  show(errorEl);
}

function clearCrlError() {
  const errorEl = qs("#crl-error");
  if (!errorEl) return;
  errorEl.textContent = "";
  hide(errorEl);
}

function populateChangeTargetSelect() {
  const select = qs("#crl-target");
  if (!select) return;
  select.innerHTML = uiText('<option value="new">新增需求（不动已交付的东西）</option>');
  (changeRequestsListState.designCases || []).forEach((design) => {
    const opt = document.createElement("option");
    opt.value = `design_case:${design.id}`;
    opt.textContent = `${design.design_number} ${previewLabel(design.problem_statement)}`;
    select.appendChild(opt);
  });
}

function previewLabel(text) {
  const value = (text || "").trim().replace(/\s+/g, " ");
  return value.length > 40 ? `${value.slice(0, 40)}…` : value;
}

// Impact is shown BEFORE anything is proposed: asking "what does this break"
// must not require first creating a record, or nobody asks.
async function refreshChangeImpactPreview() {
  const select = qs("#crl-target");
  const panel = qs("#crl-impact-panel");
  const emptyEl = qs("#crl-impact-empty");
  const tableEl = qs("#crl-impact-table");
  if (!select || !panel || !tableEl) return;
  const [targetType, targetId] = (select.value || "new").split(":");
  if (targetType === "new") {
    hide(panel);
    return;
  }
  show(panel);
  clear(tableEl);
  let impact = [];
  try {
    impact = await apiJson(
      `/projects/${changeRequestsListState.projectId}/change-impact` +
        `?target_type=${targetType}&target_id=${targetId}`
    );
  } catch (err) {
    showCrlError(uiText`影响分析失败：${err.message}`);
    return;
  }
  const graphEl = qs("#crl-impact-graph");
  if (graphEl) clear(graphEl);
  if (!impact.length) {
    if (emptyEl) show(emptyEl);
    return;
  }
  if (emptyEl) hide(emptyEl);
  tableEl.appendChild(renderImpactTable(impact));
  renderChangeImpactGraph(graphEl, select.selectedOptions[0]?.textContent || "", impact);
}

// The same impact list as a picture: the target highlighted, everything the
// change reaches hanging off it. The table right below carries the per-object
// response text — the graph answers "how far does this reach" at a glance,
// which is the question a reader has before reading any of the responses.
//: settled downstream states — the ones where a change costs the most.
const _IMPACT_DONE_STATUSES = new Set(["completed", "confirmed", "merged"]);

async function renderChangeImpactGraph(container, targetLabel, impact) {
  if (!container) return;
  const lines = ["flowchart LR", `  TGT["${mermaidLabel(targetLabel, 26)}"]`, "  class TGT impactTarget"];
  let previousDesign = null;
  impact.forEach((entry, index) => {
    const nodeId = `I${index}`;
    lines.push(
      `  ${nodeId}["${mermaidLabel(entry.object_number, 14)}<br/>${mermaidLabel(entry.status, 14)}"]`
    );
    // Only tag statuses we actually define a class for: a `class X undefined`
    // line parses but styles nothing, which reads as a rendering bug later.
    if (_IMPACT_DONE_STATUSES.has(entry.status)) {
      lines.push(`  class ${nodeId} impactDone`);
    }
    // Tasks hang off the design they came from, so the chain reads
    // target → design → task instead of a fan of siblings.
    const parent =
      entry.object_type === "development_task" && previousDesign ? previousDesign : "TGT";
    lines.push(`  ${parent} --> ${nodeId}`);
    if (entry.object_type === "design_case") previousDesign = nodeId;
  });
  lines.push("  classDef impactTarget fill:#ffe9e5,stroke:#e15a4c,stroke-width:2px;");
  lines.push("  classDef impactDone fill:#e2f5ee,stroke:#2e8e6c;");
  await renderMermaidInto(container, lines.join("\n"));
}

function renderImpactTable(impact) {
  return makeTable(
    [uiText("下游"), uiText("当前状态"), uiText("该怎么办")],
    impact.map((entry) => [
      textOr(entry.object_number),
      statusBadge(entry.status),
      textOr(uiText(entry.response)),
    ])
  );
}

function changeRequestFormFields(form) {
  const reason = (form.elements.reason.value || "").trim();
  if (!reason) {
    form.querySelector("#crl-modal-error").textContent = uiText("请写明变更原因");
    return null;
  }
  const [targetType, targetId] = (form.elements.target.value || "new").split(":");
  return {
    target_type: targetType,
    target_id: targetType === "new" ? null : parseInt(targetId, 10),
    reason,
  };
}

const _crlModal = createModalManager({
  overlayId: "#crl-modal",
  createTitle: uiText("提出变更"),
  editTitle: uiText("编辑变更原因"),
  focusSelector: "#crl-target",
  errorSelector: "#crl-modal-error",
  submitSelector: "#crl-modal-submit",
  cancelSelector: "#crl-modal-cancel",
  getPayload(form) {
    return changeRequestFormFields(form);
  },
  setFormData(form, item) {
    populateChangeTargetSelect();
    form.elements.target.value =
      item.target_type === "new" ? "new" : `${item.target_type}:${item.target_id}`;
    form.elements.target.disabled = true;  // the target is what was analysed
    form.elements.reason.value = item.reason || "";
    refreshChangeImpactPreview();
  },
  submit(mode, payload, editId) {
    if (mode === "edit") {
      // Only the reason is editable, and only before approval — the service
      // enforces both; the form just doesn't offer the rest.
      return apiJson(
        `/change-requests/${editId}`,
        jsonOptions("PATCH", { reason: payload.reason })
      );
    }
    return apiJson(
      `/projects/${changeRequestsListState.projectId}/change-requests`,
      jsonOptions("POST", payload)
    );
  },
  async onSuccess() {
    await reloadChangeRequestsListData();
  },
});

const _crlOpenCreateOrig = _crlModal.openCreate.bind(_crlModal);

_crlModal.openCreate = function () {
  _crlOpenCreateOrig();
  populateChangeTargetSelect();
  const select = qs("#crl-target");
  if (select) select.disabled = false;
  hide(qs("#crl-impact-panel"));
};

function changeRequestActions(changeRequest) {
  const actions = el("div", "actions");
  const basePath = `/change-requests/${changeRequest.id}`;
  const reload = reloadChangeRequestsListData;
  const step = (label, path, handler) =>
    opButton(label, "action-button", handler || (() => apiJson(`${basePath}/${path}`, jsonOptions("POST"))), reload, showCrlError);

  if (changeRequest.status === "proposed") {
    actions.appendChild(step(uiText("分析影响"), "analyze"));
    actions.appendChild(
      opButton(uiText("编辑原因"), "action-button", () => {
        _crlModal.openEdit(changeRequest);
        return false;  // the modal drives its own reload
      }, reload, showCrlError)
    );
  } else if (changeRequest.status === "analyzing") {
    actions.appendChild(step(uiText("批准"), "approve"));
  } else if (changeRequest.status === "approved") {
    actions.appendChild(step(uiText("标记已应用"), "apply"));
  }
  if (changeRequest.status !== "closed") {
    actions.appendChild(
      opButton(
        uiText("关闭"),
        "action-button",
        () => {
          const resolution = window.prompt(
            uiText("这条变更怎么收场？（已实施 / 放弃 / 否决 …）一句话说明：")
          );
          if (!resolution || !resolution.trim()) return false;
          return apiJson(
            `${basePath}/close`,
            jsonOptions("POST", { resolution: resolution.trim() })
          );
        },
        reload,
        showCrlError
      )
    );
  }
  actions.appendChild(
    deleteButton(
      changeRequest.change_request_number,
      basePath,
      reload,
      showCrlError
    )
  );
  return actions;
}

function renderChangeRequestsTable() {
  const changeRequests = changeRequestsListState.changeRequests;
  qs("#crl-count").textContent = changeRequests.length;
  const section = qs("#crl-table-section");
  if (!changeRequests.length) {
    appendEmpty(section, uiText("暂无变更请求。"));
    return;
  }
  clear(section);
  section.appendChild(
    makeTable(
      [uiText("选择"), "#", uiText("改什么"), uiText("状态"), uiText("关联任务"), uiText("原因"), uiText("影响"), uiText("操作")],
      changeRequests.map((changeRequest) => {
        const select = el("input");
        select.type = "checkbox";
        select.className = "cr-task-select";
        select.dataset.changeRequestNumber = changeRequest.change_request_number || "";
        select.disabled = !["approved", "applied"].includes(changeRequest.status) || Boolean(changeRequest.task_number);
        const impact = changeRequest.impact || [];
        const impactCell =
          changeRequest.status === "proposed"
            ? el("span", "muted", uiText("未分析"))
            : textOr(
                impact.length
                  ? impact.map((entry) => entry.object_number).join("、")
                  : uiText("无下游")
              );
        const statusCell = el("div", "status-cell");
        statusCell.appendChild(statusBadge(changeRequest.status));
        // What this state expects NEXT — the flow is five steps and nobody
        // remembers which one hands off to whom.
        const stageHint = changeRequest.resolution
          ? changeRequest.resolution
          : CHANGE_REQUEST_STAGE_HINTS[changeRequest.status];
        if (stageHint) {
          statusCell.appendChild(el("span", "blocked-note", stageHint));
        }
        return [
          select,
          textOr(changeRequest.change_request_number),
          textOr(changeRequest.target_number || (changeRequest.target_type === "new" ? uiText("新增需求") : "-")),
          statusCell,
          textOr(changeRequest.task_number || uiText("未归入任务")),
          previewText(changeRequest.reason),
          impactCell,
          changeRequestActions(changeRequest),
        ];
      })
    )
  );
}

function closeChangeRequestTaskModal() {
  hide(qs("#crl-task-modal"));
  const form = qs("#crl-task-form");
  if (form) form.reset();
  const error = qs("#crl-task-error");
  if (error) error.textContent = "";
}

async function openChangeRequestTaskModal() {
  const selected = qsa(".cr-task-select").filter((box) => box.checked).map((box) => box.dataset.changeRequestNumber);
  if (!selected.length) {
    showCrlError(uiText("请选择至少一个未归入任务的已批准或已应用变更"));
    return;
  }
  const featureSelect = qs("#crl-task-feature");
  clear(featureSelect);
  try {
    const features = await apiJson(`/projects/${changeRequestsListState.projectId}/features`);
    changeRequestsListState.features = features;
    features.forEach((feature) => {
      const option = el("option", null, `${feature.feature_number} ${previewLabel(feature.title || feature.name || "")}`);
      option.value = feature.feature_number;
      featureSelect.appendChild(option);
    });
  } catch (err) {
    showCrlError(uiText`加载 Feature 失败：${err.message}`);
    return;
  }
  if (!changeRequestsListState.features.length) {
    showCrlError(uiText("没有可用 Feature"));
    return;
  }
  qs("#crl-task-modal").dataset.changeRequestNumbers = JSON.stringify(selected);
  show(qs("#crl-task-modal"));
  qs("#crl-task-title").focus();
}

async function submitChangeRequestSourceTask(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const error = qs("#crl-task-error");
  error.textContent = "";
  if (!form.elements.title.value.trim() || !form.elements.goal.value.trim() || !form.elements.acceptance_criteria.value.trim()) {
    error.textContent = uiText("标题、目标和验收标准不能为空");
    return;
  }
  const payload = {
    source_kind: "change_request",
    feature_number: form.elements.feature_number.value,
    change_request_numbers: JSON.parse(qs("#crl-task-modal").dataset.changeRequestNumbers || "[]"),
    title: form.elements.title.value.trim(),
    task_type: form.elements.task_type.value,
    goal: form.elements.goal.value.trim(),
    acceptance_criteria: form.elements.acceptance_criteria.value.trim(),
    constraints: form.elements.constraints.value.trim(),
    non_goals: form.elements.non_goals.value.trim(),
    priority: form.elements.priority.value,
    complexity: form.elements.complexity.value,
    risk_level: form.elements.risk_level.value,
  };
  try {
    const task = await apiJson(`/projects/${changeRequestsListState.projectNumber}/tasks/from-sources`, jsonOptions("POST", payload));
    closeChangeRequestTaskModal();
    showToast(uiText`已创建 ${task.task_number || "TASK-*"}`);
    await reloadChangeRequestsListData();
  } catch (err) {
    error.textContent = err.message;
  }
}

async function reloadChangeRequestsListData() {
  if (!changeRequestsListState.projectId) return;
  clearCrlError();
  const projectId = changeRequestsListState.projectId;
  const [changeRequests, designCases] = await Promise.all([
    apiJson(`/projects/${projectId}/change-requests`),
    apiJson(`/projects/${projectId}/design-cases`).catch(() => []),
  ]);
  changeRequestsListState.changeRequests = changeRequests;
  changeRequestsListState.designCases = designCases;
  renderChangeRequestsTable();
  await renderEntityTrash(qs("#crl-trash"), {
    url: `/projects/${projectId}/change-requests/trash`,
    base: (id) => `/change-requests/${id}`,
    label: (item) => `${item.change_request_number} ${previewLabel(item.reason)}`,
    reload: reloadChangeRequestsListData,
  });
}

async function renderChangeRequestsListPage() {
  const page = qs("#change-requests-list-page");
  if (!page) return;

  changeRequestsListState.projectId = page.dataset.projectId;
  changeRequestsListState.projectNumber = page.dataset.projectNumber;
  renderProjectFlowNav(changeRequestsListState.projectId, "change-requests");
  const statusEl = qs("#crl-status", page);
  const content = qs("#crl-content", page);
  _crlModal.init();

  const createBtn = qs("#crl-create-btn", page);
  if (createBtn) createBtn.addEventListener("click", () => _crlModal.openCreate());
  const taskButton = qs("#crl-create-task-btn", page);
  if (taskButton) taskButton.addEventListener("click", openChangeRequestTaskModal);
  const taskForm = qs("#crl-task-form", page);
  if (taskForm) taskForm.addEventListener("submit", submitChangeRequestSourceTask);
  const taskCancel = qs("#crl-task-cancel", page);
  if (taskCancel) taskCancel.addEventListener("click", closeChangeRequestTaskModal);
  const targetSelect = qs("#crl-target", page);
  if (targetSelect) {
    targetSelect.addEventListener("change", () => refreshChangeImpactPreview());
  }

  try {
    setStatus(statusEl, uiText("正在加载变更请求..."));
    await reloadChangeRequestsListData();
    hide(statusEl);
    show(content);
  } catch (err) {
    hide(content);
    setStatus(statusEl, uiText`加载变更请求失败：${err.message}`, "error");
  }
}

// Read-only "view the linked design" modal, opened from the development-task
// detail page. Lets the reader see the full DESIGN-* without leaving the page —
// the design is what the coder actually receives, so it is the real spec.
// Markup lives in base.html so every page can open it. (Deleted by accident in
// c937b63 along with the task-drafts UI it used to be filed under, while both
// its call sites and its markup stayed — restored here.)
const _DESIGN_VIEW_SECTIONS = [
  [uiText("问题陈述"), "problem_statement", renderMarkdown],
  [uiText("需求(挖掘所得)"), "mined_requirements", renderMarkdown],
  [uiText("本设计验证的 NFR"), "verifies_nfr", renderMarkdown],
  [uiText("设计范围"), "design_scope", renderMarkdown],
  [uiText("假设"), "assumptions", renderMarkdown],
  [uiText("约束"), "constraints", renderMarkdown],
  [uiText("选定方案"), "selected_solution", renderMarkdown],
  [uiText("选择理由"), "selection_reason", renderMarkdown],
  [uiText("被否方案"), "rejected_solutions",
    (t, v) => renderMarkdown(t, rejectedSolutionsMarkdown(v))],
  [uiText("风险"), "risks", renderMarkdown],
  [uiText("测试策略"), "test_strategy", renderMarkdown],
  [uiText("验收标准"), "acceptance_criteria", renderMarkdown],
  [uiText("非目标"), "non_goals", renderMarkdown],
  [uiText("开放问题"), "open_questions", (t, v) => renderMarkdown(t, markdownList(v))],
];

function renderDesignViewModalBody(design) {
  const body = qs("#design-view-modal-body");
  clear(body);
  const meta = el("p", "summary",
    `${businessNumber(design, "design_number", uiText("未编号设计"))}`
    + (design.feature_number ? ` · ${design.feature_number}` : "")
    + (Array.isArray(design.impact_areas) && design.impact_areas.length
      ? uiText` · 影响面：${design.impact_areas.join(", ")}` : ""));
  body.appendChild(meta);
  for (const [label, key, render] of _DESIGN_VIEW_SECTIONS) {
    const field = el("div", "detail-field detail-field-block");
    field.appendChild(el("span", "detail-label", label));
    const md = el("div", "markdown-body");
    render(md, design[key]);
    field.appendChild(md);
    body.appendChild(field);
  }
}

async function openDesignViewModal(designCaseId) {
  const modal = qs("#design-view-modal");
  if (!modal) return;
  const statusEl = qs("#design-view-modal-status");
  const body = qs("#design-view-modal-body");
  const openLink = qs("#design-view-modal-open");
  if (openLink) openLink.href = `/ui/design-cases/${designCaseId}`;
  clear(body);
  show(statusEl);
  setStatus(statusEl, uiText("正在加载设计..."));
  show(modal);
  try {
    const design = await apiJson(`/design-cases/${designCaseId}`);
    hide(statusEl);
    renderDesignViewModalBody(design);
  } catch (err) {
    setStatus(statusEl, uiText`加载设计失败：${err.message}`, "error");
  }
}

function closeDesignViewModal() {
  const modal = qs("#design-view-modal");
  if (modal) hide(modal);
}

function initDesignViewModal() {
  const modal = qs("#design-view-modal");
  if (!modal) return;
  const closeBtn = qs("#design-view-modal-close");
  if (closeBtn) closeBtn.addEventListener("click", closeDesignViewModal);
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeDesignViewModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.classList.contains("hidden")) {
      closeDesignViewModal();
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  initProjectModal();
  initConfigPage();
  initTrashViewModal();
  initDesignViewModal();
  initVersionDiffModal();
  initCreateProjectForm();
  renderProjectsPage();
  renderProjectDetailPage();
  renderSourceRequirementsListPage();
  renderGlossaryPage();
  renderConstitutionPage();
  renderFeaturesListPage();
  renderFeatureDetailPage();
  renderDesignCasesListPage();
  renderDesignCasePage();
  renderRequirementDigestPage();
  renderNfrCoveragePage();
  renderDeliveryItemsPage();
  renderDevelopmentTasksListPage();
  renderChangeRequestsListPage();
  renderDevelopmentTaskPage();
  renderSourceRequirementPage();
});
