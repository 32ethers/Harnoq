"""Web dashboard shell/API-contract tests."""
from pathlib import Path


CONFIRM_FIELDS = {
    "problem_statement": "Users need login.",
    "selected_solution": "Email login.",
    "selection_reason": "Smallest viable scope.",
    "risks": "Credential stuffing.",
    "test_strategy": "Auth unit tests.",
    "acceptance_criteria": "User can log in.",
}


def _project_with_pipeline(client, name="P"):
    """Build project -> FEATURE -> design -> task (created directly)."""
    project = client.post("/projects", json={"name": name}).get_json()
    feature = client.post(
        f"/projects/{project['id']}/features", json={"name": "F"}
    ).get_json()
    design = client.post(
        f"/projects/{project['id']}/design-cases", json={"feature_id": feature["id"]}
    ).get_json()
    client.patch(f"/design-cases/{design['id']}", json=CONFIRM_FIELDS)
    client.post(f"/design-cases/{design['id']}/confirm")
    client.post(
        f"/design-cases/{design['id']}/tasks",
        json={"title": "Build login", "task_type": "feature", "goal": "do it"},
    )
    return project


def _app_js(client):
    response = client.get("/static/app.js")
    assert response.status_code == 200
    assert "javascript" in response.content_type
    return response.get_data(as_text=True)


def test_home_redirects_to_projects(client):
    response = client.get("/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/ui/projects")


def test_web_views_do_not_import_domain_services():
    source = Path("app/web/views.py").read_text()
    assert "app.services" not in source
    assert "_service" not in source


def test_projects_list_returns_shell_not_server_rendered_data(client):
    client.post("/projects", json={"name": "Acme"}).get_json()

    response = client.get("/ui/projects")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert 'id="projects-page"' in body
    assert 'id="projects-body"' in body
    assert 'id="projects-status"' in body
    assert 'id="create-project-form"' in body
    assert 'id="project-name"' in body
    assert "Acme" not in body
    assert 'action=' not in body.split('id="create-project-form"')[1].split(">")[0]


def test_app_js_renders_project_list_via_existing_apis(client):
    body = _app_js(client)
    assert 'apiJson("/projects")' in body
    assert "`/projects/${project.id}/tasks`" in body
    assert 'jsonOptions("POST", payload)' in body
    assert 'window.location.href = `/ui/projects/${result.id}`' in body


def test_projects_list_has_create_modal_trigger(client):
    response = client.get("/ui/projects")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert 'id="create-project-btn"' in body
    assert 'id="project-modal"' in body
    assert 'id="project-description"' in body


def test_project_detail_has_edit_trigger(client):
    project = client.post("/projects", json={"name": "EditMe"}).get_json()

    response = client.get(f"/ui/projects/{project['id']}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert 'id="edit-project-btn"' in body
    assert 'class="detail-header"' in body


def test_project_detail_rail_groups_both_phases_and_every_stage(client):
    project = client.post("/projects", json={"name": "Rail"}).get_json()

    body = client.get(f"/ui/projects/{project['id']}").get_data(as_text=True)

    # The rail must not silently omit a stage — an incomplete pipeline diagram
    # lies about the flow (the constitution in particular is a real gate:
    # design cannot start until it is confirmed).
    assert 'data-phase="planning"' in body and 'data-phase="build"' in body
    assert "Planning" in body and "Build" in body
    for stage in ("inputs-total", "constitution-stage", "features-stage",
                  "designs-stage", "kanban-stage"):
        assert stage in body, f"rail is missing {stage}"
    # The glossary spans both phases, so it is a band, never a rail step.
    assert 'id="glossary-band"' in body
    assert 'class="phase-band"' in body


def test_app_js_has_project_modal_and_edit_flow(client):
    body = _app_js(client)
    assert "function openCreateProjectModal" in body
    assert "function openEditProjectModal" in body
    assert 'jsonOptions("PATCH", payload)' in body
    assert "function initProjectModal" in body
    assert "function closeProjectModal" in body


def test_project_detail_returns_shell_not_server_rendered_pipeline_data(client):
    project = _project_with_pipeline(client, name="Project content must be loaded by the client")

    response = client.get(f"/ui/projects/{project['id']}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert 'id="project-detail-page"' in body
    assert f'data-project-id="{project["id"]}"' in body
    assert "Source requirements" in body
    assert "Feature" in body
    assert "Design cases" in body
    assert "任务计划" not in body
    assert "Development tasks" in body
    assert 'id="detail-error"' in body
    assert 'id="project-description" class="desc markdown-body hidden"' in body
    assert 'class="project-flow-nav hidden"' in body
    assert 'id="inputs-list-link"' in body
    assert 'id="load-plan-form"' not in body
    assert "按计划 ID 加载" not in body
    assert "Project content must be loaded by the client" not in body
    assert "Login request" not in body
    assert "Build login" not in body


def test_app_js_renders_project_detail_via_existing_apis(client):
    body = _app_js(client)
    assert "`/projects/${detailState.projectId}`" in body
    assert "`/projects/${detailState.projectId}/source-requirements`" in body
    assert "`/projects/${detailState.projectId}/features`" in body
    assert "`/projects/${detailState.projectId}/tasks`" in body
    assert "`/projects/${detailState.projectId}/design-cases`" in body
    # The DRAFT layer is gone (see docs/cn/design-evolution.md §2): a confirmed design creates its
    # TASK-* directly, one call, no generate-tasks step anywhere.
    assert "`${basePath}/generate-tasks`" not in body
    assert "generateTasksFromDraft" not in body
    # Task lifecycle calls all go through taskLifecycleButtons' `post` helper
    # (one `${base}/${path}` template shared by the list rows and the detail
    # page), so the action names are asserted rather than whole URLs.
    assert "const base = `/tasks/${task.id}`;" in body
    assert "apiJson(`${base}/${path}`, jsonOptions(\"POST\", body || {}))" in body
    for action in (
        "claim",
        "start-pre-review",
        "approve-pre-review",
        "reject-pre-review",
        "start-coding",
        "mark-ready-for-pr",
        "link-pr",
        "mark-pr-merged",
        "mark-completed",
        "withdraw-from-pr",
        "reopen-completed",
    ):
        assert f'"{action}"' in body
    assert "detailState = {" in body
    assert "function showDetailError" in body
    # project.description is Markdown (docs/cn/design-evolution.md §10: Markdown fields must
    # render as Markdown wherever shown, not as raw textContent).
    assert "renderMarkdown(description, project.description);" in body
    # Dashboard now links to sub-feature list pages instead of rendering inline tables
    assert "`/ui/projects/${pid}/source-requirements`" in body
    assert "`/ui/projects/${pid}/features`" in body
    assert "`/ui/projects/${pid}/design-cases`" in body
    assert "`/ui/projects/${pid}/tasks`" in body


def test_project_detail_missing_project_uses_shell_and_api_error_path(client):
    response = client.get("/ui/projects/9999")
    assert response.status_code == 200
    assert 'data-project-id="9999"' in response.get_data(as_text=True)
    assert client.get("/projects/9999").status_code == 404
    assert "加载项目失败" in _app_js(client)


def test_source_requirement_detail_returns_shell_not_server_rendered_data(client):
    project = client.post("/projects", json={"name": "Detail"}).get_json()
    source_requirement = client.post(
        f"/projects/{project['id']}/source-requirements",
        json={
            "title": "Secret login request",
            "source_type": "new_feature",
            "raw_content": "# Need login\nMarkdown body.",
        },
    ).get_json()

    response = client.get(f"/ui/source-requirements/{source_requirement['id']}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert 'id="source-requirement-page"' in body
    assert f'data-input-id="{source_requirement["id"]}"' in body
    assert 'class="project-flow-nav hidden"' in body
    assert 'id="ri-display-content"' in body
    assert 'class="detail-field detail-field-block"' in body
    assert 'id="ri-display-title"' in body
    assert 'id="ri-display-version"' in body
    assert 'id="ri-lifecycle-actions"' in body
    assert 'id="ri-versions-table"' in body
    assert 'id="ri-compare-versions"' in body
    assert 'id="version-diff-modal"' in body
    assert "Secret login request" not in body
    assert "Markdown body." not in body


def test_source_requirements_list_links_to_detail_page(client):
    project = client.post("/projects", json={"name": "Linker"}).get_json()
    client.post(
        f"/projects/{project['id']}/source-requirements",
        json={"title": "Login request", "source_type": "new_feature", "raw_content": "x"},
    )

    body = _app_js(client)
    assert "/ui/source-requirements/${item.id}" in body


def test_source_requirements_list_returns_shell_not_server_rendered_data(client):
    project = client.post("/projects", json={"name": "ListMe"}).get_json()
    client.post(
        f"/projects/{project['id']}/source-requirements",
        json={"title": "Secret login request", "source_type": "new_feature", "raw_content": "x"},
    )

    response = client.get(f"/ui/projects/{project['id']}/source-requirements")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert 'id="source-requirements-list-page"' in body
    assert f'data-project-id="{project["id"]}"' in body
    assert 'id="ril-create-btn"' in body
    assert 'id="ril-modal"' in body
    assert 'id="ril-table-section"' in body
    assert "Secret login request" not in body


def test_features_list_returns_shell_not_server_rendered_data(client):
    project = client.post("/projects", json={"name": "FeatureList"}).get_json()
    response = client.get(f"/ui/projects/{project['id']}/features")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert 'id="features-list-page"' in body
    assert f'data-project-id="{project["id"]}"' in body
    assert 'id="fl-create-btn"' in body
    assert 'id="fl-modal"' in body
    assert 'id="fl-orientation"' in body
    assert 'id="fl-subdomain-type"' in body
    assert 'id="fl-is-spike"' in body
    assert 'id="fl-depends-on-feature-ids"' in body


def test_feature_detail_returns_shell_not_server_rendered_data(client):
    project = client.post("/projects", json={"name": "FeatureDetail"}).get_json()
    unit = client.post(
        f"/projects/{project['id']}/source-requirements",
        json={"title": "Secret unit", "source_type": "new_feature"},
    ).get_json()
    feature = client.post(
        f"/projects/{project['id']}/features",
        json={"name": "Secret feature", "source_requirement_ids": [unit["id"]]},
    ).get_json()

    response = client.get(f"/ui/features/{feature['id']}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert 'id="feature-page"' in body
    assert f'data-feature-id="{feature["id"]}"' in body
    assert 'class="project-flow-nav hidden"' in body
    assert 'id="fdi-display-tags"' in body
    assert 'id="fdi-display-dependencies"' in body
    assert 'id="fdi-versions-table"' in body
    assert 'id="fdi-compare-versions"' in body
    assert 'id="version-diff-modal"' in body
    assert "Secret feature" not in body


def test_app_js_compares_feature_versions_in_the_browser(client):
    body = _app_js(client)
    assert "function gitLineDiff" in body
    assert "function renderFeatureVersionDiff" in body
    assert "`/features/${featureDetailState.featureId}/versions`" in body
    assert "`/features/${featureDetailState.featureId}/versions/${version.version_number}/restore`" in body
    assert "function renderDesignCaseVersionDiff" in body
    assert "function renderConstitutionVersionDiff" in body
    assert "function renderSourceRequirementVersionDiff" in body
    assert "`/design-cases/${designCaseDetailState.designId}/versions`" in body


def test_constitution_page_returns_shell_with_modal(client):
    project = client.post("/projects", json={"name": "ConstProj"}).get_json()

    response = client.get(f"/ui/projects/{project['id']}/constitution")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert 'id="constitution-page"' in body
    assert f'data-project-id="{project["id"]}"' in body
    assert 'class="project-flow-nav hidden"' in body
    # Singleton detail page: edit modal on the page, six governance sections.
    assert 'id="cst-modal"' in body
    assert 'id="cst-display-fixed-stack"' in body
    assert 'id="cst-display-nfr-baseline"' in body
    assert 'id="cst-compare-versions"' in body
    assert 'id="version-diff-modal"' in body
    assert 'data-flow-link="constitution"' in body


def test_app_js_renders_constitution_via_existing_apis(client):
    body = _app_js(client)
    assert "function renderConstitutionPage" in body
    assert "`/projects/${constitutionState.projectId}/constitution`" in body
    assert "renderProjectFlowNav(constitutionState.projectId" in body


def test_project_detail_links_to_source_requirements_list(client):
    project = client.post("/projects", json={"name": "Linker2"}).get_json()

    response = client.get(f"/ui/projects/{project['id']}")
    body = response.get_data(as_text=True)
    assert 'id="inputs-list-link"' in body

    body_js = _app_js(client)
    assert "`/ui/projects/${pid}/source-requirements`" in body_js


def test_app_js_renders_source_requirements_list_via_existing_apis(client):
    body = _app_js(client)
    assert "`/projects/${requirementInputsListState.projectId}/source-requirements`" in body
    assert "function renderSourceRequirementsListPage" in body
    assert "function reloadSourceRequirementsListData" in body


def test_app_js_renders_source_requirement_detail_via_existing_apis(client):
    body = _app_js(client)
    assert "`/source-requirements/${requirementInputState.inputId}`" in body
    assert "`/source-requirements/${requirementInputState.inputId}/versions`" in body
    assert "function renderSourceRequirementActions" in body
    assert "`/source-requirements/${requirementInputState.inputId}/status`" not in body
    assert "confirmRestoreSourceRequirementVersion" in body
    assert "function renderSourceRequirementPage" in body
    assert "function reloadSourceRequirementDetail" in body


def test_app_js_markdown_renderer_preserves_soft_line_breaks(client):
    body = _app_js(client)
    assert "function renderMarkdown(target, value)" in body
    assert "breaks: true" in body
    assert "target.innerHTML = DOMPurify.sanitize(html);" in body


def test_source_requirement_detail_missing_input_uses_shell_and_api_error_path(client):
    response = client.get("/ui/source-requirements/9999")
    assert response.status_code == 200
    assert 'data-input-id="9999"' in response.get_data(as_text=True)
    assert client.get("/source-requirements/9999").status_code == 404
    assert "加载原始需求失败" in _app_js(client)


# --- new list pages (design cases, task plans, dev tasks) ---

def test_design_cases_list_returns_shell(client):
    project = client.post("/projects", json={"name": "DCList"}).get_json()

    response = client.get(f"/ui/projects/{project['id']}/design-cases")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert 'id="design-cases-list-page"' in body
    assert 'id="dcl-create-btn"' in body
    assert 'id="dcl-modal"' in body
    assert 'id="dcl-feature-id"' in body
    assert 'id="dcl-table-section"' in body
    assert 'class="modal modal-wide"' in body
    assert "DCList" not in body


def test_design_cases_list_table_previews_problem_statement_not_raw(client):
    """The design-cases list table's 问题陈述 column must show a truncated
    preview (previewText), never the raw untruncated Markdown value — a full
    multi-line problem_statement dumped straight into a table cell is exactly
    the kind of full-value plain render this display sweep fixes."""
    body = _app_js(client)
    assert "previewText(design.problem_statement)" in body
    assert "textOr(design.problem_statement)" not in body


def test_design_case_detail_shell_hides_unconfirmed_data(client):
    project = client.post("/projects", json={"name": "DCDetail"}).get_json()
    feature = client.post(
        f"/projects/{project['id']}/features",
        json={"name": "F"},
    ).get_json()
    design = client.post(
        f"/projects/{project['id']}/design-cases", json={"feature_id": feature["id"]}
    ).get_json()
    client.patch(
        f"/design-cases/{design['id']}",
        json={"problem_statement": "Hidden design problem."},
    )

    response = client.get(f"/ui/design-cases/{design['id']}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert 'id="design-case-page"' in body
    assert f'data-design-id="{design["id"]}"' in body
    assert 'class="project-flow-nav hidden"' in body
    assert 'id="dci-display-problem-statement"' in body
    assert 'id="dci-display-selected-solution"' in body
    assert 'class="detail-field detail-field-block"' in body
    assert 'id="dci-lifecycle-actions"' in body
    assert 'id="dci-versions-table"' in body
    assert 'id="dci-compare-versions"' in body
    assert 'id="version-diff-modal"' in body
    assert "Secret unit" not in body
    assert "Hidden design problem." not in body


def test_app_js_has_design_cases_crud(client):
    body = _app_js(client)
    assert "function renderDesignCasesListPage" in body
    assert "function reloadDesignCasesListData" in body
    assert "function populateDesignFeatureSelect" in body
    assert "function createModalManager" in body  # factory function is used
    assert "/ui/design-cases/${design.id}" in body
    assert 'el("a", "action-button", "查看")' not in body
    assert "function confirmDesignCaseWithPrompts" in body
    assert '`/projects/${designCasesListState.projectId}/design-cases`' in body
    assert "`/design-cases/${design.id}/reopen`" in body
    # needs_review retired (see docs/cn/design-evolution.md §6) — no "标为已复审" affordance anywhere.
    assert "mark-reviewed" not in body
    assert "`/design-cases/${design.id}/reject`" not in body
    assert "function renderDesignCasePage" in body
    assert "`/design-cases/${designCaseDetailState.designId}`" in body
    assert "`/design-cases/${designCaseDetailState.designId}/versions`" in body


def test_app_js_renders_project_flow_navigation(client):
    body = _app_js(client)
    assert "function renderProjectFlowNav" in body
    assert "`/ui/projects/${projectId}/source-requirements`" in body
    assert "`/ui/projects/${projectId}/design-cases`" in body
    assert 'renderProjectFlowNav(item.project_id, "inputs")' in body
    assert 'renderProjectFlowNav(design.project_id, "designs")' in body


def test_design_case_modal_actions_stay_in_one_row():
    css = Path("app/static/style.css").read_text()
    assert ".modal-form > div:not(.version-history):not(.modal-actions)" in css
    assert ".modal-form > .modal-actions" in css


def test_design_case_detail_returns_shell_not_server_rendered_data(client):
    project = client.post("/projects", json={"name": "DCDetail"}).get_json()
    feature = client.post(
        f"/projects/{project['id']}/features",
        json={"name": "F"},
    ).get_json()
    design = client.post(
        f"/projects/{project['id']}/design-cases", json={"feature_id": feature["id"]}
    ).get_json()
    client.patch(f"/design-cases/{design['id']}", json=CONFIRM_FIELDS)
    client.post(f"/design-cases/{design['id']}/confirm")
    client.post(
        f"/design-cases/{design['id']}/tasks",
        json={"title": "Secret task", "task_type": "feature", "goal": "Hidden goal."},
    )

    response = client.get(f"/ui/design-cases/{design['id']}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert 'id="design-case-page"' in body
    assert f'data-design-id="{design["id"]}"' in body
    assert 'class="project-flow-nav hidden"' in body
    assert 'id="dci-display-number"' in body
    # The DRAFT layer is gone (see docs/cn/design-evolution.md §2) — no draft table/modal ids remain.
    assert 'id="dci-drafts-table"' not in body
    assert 'id="dcd-modal"' not in body
    assert 'class="detail-field detail-field-block"' in body
    assert 'id="dci-lifecycle-actions"' in body
    assert 'id="dci-versions-table"' in body
    assert "Secret unit" not in body
    assert "Secret task" not in body


def test_app_js_design_page_creates_tasks_directly(client):
    body = _app_js(client)
    # A confirmed design creates its TASK-* directly — no draft list, no
    # separate generate-tasks step anywhere in the design page.
    assert "function renderDesignCaseDrafts" not in body
    assert "`${basePath}/generate-tasks`" not in body
    assert "_dcdModal" not in body
    assert "collectTaskPayload" in body
    assert "`${basePath}/tasks`" in body
    # The dedicated 任务草案 pages are gone entirely.
    assert "function renderTaskDraftsListPage" not in body
    assert "function renderTaskDraftDetailPage" not in body
    assert "/ui/task-drafts/" not in body
    # Retired PLAN pages must not resurface.
    assert "function renderTaskPlansListPage" not in body
    assert "function renderTaskPlanPage" not in body


def test_flow_nav_has_no_task_drafts_step(client):
    body = _app_js(client)
    assert "/task-drafts" not in body
    assert 'data-flow-link="drafts"' not in Path("app/templates/_project_flow_nav.html").read_text()


def test_development_tasks_list_returns_shell(client):
    project = client.post("/projects", json={"name": "DTList"}).get_json()

    response = client.get(f"/ui/projects/{project['id']}/tasks")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert 'id="development-tasks-list-page"' in body
    assert 'id="dtl-modal"' in body
    assert 'id="dtl-table-section"' in body
    # New-task entry point: tasks are created directly under a confirmed design.
    assert 'id="dtl-create-btn"' in body
    assert 'id="dtl-create-modal"' in body
    assert "DTList" not in body


def test_app_js_has_development_tasks_edit(client):
    body = _app_js(client)
    assert "function renderDevelopmentTasksListPage" in body
    assert "function reloadDevelopmentTasksListData" in body
    # Task numbers in the list link to the task detail page.
    assert "`/ui/tasks/${task.id}`" in body


def test_development_task_detail_returns_shell(client):
    project = _project_with_pipeline(client, name="DTDetail")
    task = client.get(f"/projects/{project['id']}/tasks").get_json()[0]

    response = client.get(f"/ui/tasks/{task['id']}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert 'id="development-task-page"' in body
    assert f'data-task-id="{task["id"]}"' in body
    # Dependency is Feature-only now (docs/cn/design-evolution.md §5);
    # the detail page shows the live-computed gate, not stored task edges.
    assert 'id="dti-feature-gate"' in body
    # Shell only — no server-rendered task data.
    assert task["title"] not in body


def test_app_js_has_development_task_detail(client):
    body = _app_js(client)
    assert "function renderDevelopmentTaskPage" in body
    assert "function reloadDevelopmentTaskDetail" in body
    assert "function renderDevelopmentTaskGate" in body


def test_config_page_renders_system_prompt(client):
    response = client.get("/ui/config")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert 'id="config-page"' in body
    assert 'id="system-prompt-text"' in body
    # Copy button is class-based now (one per snippet), no fixed id.
    assert 'class="copy-button" data-target="#system-prompt-text"' in body
    # The prompt is read from the skill doc (single source of truth) and shown
    # verbatim, not a placeholder.
    prompt = Path("skills/openwebui-system-prompt.en.md").read_text(encoding="utf-8")
    assert "You are the Harnoq project collaboration assistant." in prompt
    assert "You are the Harnoq project collaboration assistant." in body


def test_config_page_covers_both_phases_and_clients(client):
    response = client.get("/ui/config")
    body = response.get_data(as_text=True)

    # Two phases, each with its client configs. Umbrella names are Planning
    # (REQ→FEATURE→DESIGN→TASK) and Build — "设计阶段" would collide with the
    # DESIGN entity that lives inside Planning.
    assert "Planning stage" in body
    assert "Build stage" in body
    assert "OpenWebUI" in body
    assert "Claude Code" in body and "Codex" in body
    # Remote MCP url derived from request host, same port as the web server.
    assert "/mcp" in body
    assert ":5001/mcp" not in body
    assert '"type": "http"' in body           # Claude remote MCP config
    assert 'url = "http://' in body            # Codex remote MCP config (toml)
    assert "harnoq-init.py" in body           # coding-phase install


def test_config_page_linked_from_topbar(client):
    body = client.get("/ui/projects").get_data(as_text=True)
    assert "/ui/config" in body
    assert "Connection setup" in body


def test_app_js_has_config_copy_handler(client):
    body = _app_js(client)
    assert "function initConfigPage" in body
    assert "initConfigPage();" in body


def test_each_list_page_has_its_own_trash_section(client):
    project = client.post("/projects", json={"name": "TrashUI"}).get_json()
    pid = project["id"]
    cases = {
        "source-requirements": "ril-trash",
        "design-cases": "dcl-trash",
        "tasks": "dtl-trash",
    }
    for path, section_id in cases.items():
        body = client.get(f"/ui/projects/{pid}/{path}").get_data(as_text=True)
        assert f'id="{section_id}"' in body, path


def test_project_detail_no_longer_has_aggregated_trash(client):
    project = client.post("/projects", json={"name": "NoTrash"}).get_json()
    body = client.get(f"/ui/projects/{project['id']}").get_data(as_text=True)
    assert 'id="trash-section"' not in body


def test_app_js_renders_per_entity_trash_not_aggregated(client):
    body = _app_js(client)
    assert "function renderEntityTrash" in body
    assert "/source-requirements/trash" in body
    assert "/design-cases/trash" in body
    assert "/tasks/trash" in body
    # the old aggregated project-detail bin is gone
    assert "TRASH_BUCKETS" not in body
    assert "renderTrashGroup" not in body


def test_deleted_items_are_viewable_via_readonly_modal(client):
    project = client.post("/projects", json={"name": "ViewTrash"}).get_json()
    # the read-only view modal ships in the shared shell (base.html)
    body = client.get(
        f"/ui/projects/{project['id']}/source-requirements"
    ).get_data(as_text=True)
    assert 'id="trash-view-modal"' in body
    assert 'id="trash-view-body"' in body

    js = _app_js(client)
    assert "function openTrashViewModal" in js
    assert "initTrashViewModal" in js
    assert "linklike" in js  # clickable deleted-item title


def test_new_list_pages_linked_from_project_detail(client):
    """The project detail dashboard links to each sub-feature list page."""
    project = client.post("/projects", json={"name": "Linked"}).get_json()

    response = client.get(f"/ui/projects/{project['id']}")
    body = response.get_data(as_text=True)

    assert 'id="inputs-list-link"' in body
    assert 'id="designs-list-link"' in body
    assert 'id="plans-list-link"' not in body
    assert 'id="tasks-list-link"' in body


def test_only_diagram_pages_load_mermaid(client):
    """mermaid is ~2.5MB — 70x marked+purify combined.

    Loading it from base.html would put that on every page in the app, so the
    templates that draw something opt in. This asserts both halves: the opt-in
    exists where a diagram lives, and does NOT leak into pages without one.
    """
    project = client.post("/projects", json={"name": "P"}).get_json()
    feature = client.post(
        f"/projects/{project['id']}/features", json={"name": "f"}
    ).get_json()

    with_diagram = [
        f"/ui/projects/{project['id']}/features",
        f"/ui/features/{feature['id']}",
        f"/ui/projects/{project['id']}/change-requests",
    ]
    without_diagram = [
        f"/ui/projects/{project['id']}",
        f"/ui/projects/{project['id']}/source-requirements",
        f"/ui/projects/{project['id']}/tasks",
    ]
    for path in with_diagram:
        body = client.get(path).get_data(as_text=True)
        assert "vendor/mermaid.min.js" in body, path
    for path in without_diagram:
        body = client.get(path).get_data(as_text=True)
        assert "vendor/mermaid.min.js" not in body, path


def test_mermaid_is_vendored_not_fetched_from_a_cdn(client):
    """The app must keep working with no network: assets are served locally."""
    response = client.get("/static/vendor/mermaid.min.js")
    assert response.status_code == 200
    assert len(response.get_data()) > 100_000
    for path in ("/static/app.js",):
        body = client.get(path).get_data(as_text=True)
        assert "cdn.jsdelivr.net" not in body
        assert "unpkg.com" not in body
