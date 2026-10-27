"""Business-number keyed task execution endpoints for downstream agents."""
from tests.test_agent_runs import (
    _agent_run,
    _in_progress_with_coder_pass,
    _qa_evidence,
    _ready_for_development_task,
)
from tests.test_development_task_lifecycle import _claimable_task


def _numbers(client, task):
    project = client.get(f"/projects/{task['project_id']}").get_json()
    return project["project_number"], task["task_number"]


def _set_integration_kind(client, task):
    design = client.get(f"/design-cases/{task['design_case_id']}").get_json()
    response = client.patch(
        f"/features/{design['feature_id']}",
        json={"orientation": "foundation", "foundation_kind": "integration"},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return design


def _assign_delivery_item(client, task, item_number):
    design = _set_integration_kind(client, task)
    assert client.post(f"/design-cases/{design['id']}/reopen").status_code == 200
    assert client.patch(
        f"/design-cases/{design['id']}",
        json={"design_scope": f"This design owns {item_number}."},
    ).status_code == 200
    assert client.post(f"/design-cases/{design['id']}/confirm").status_code == 200


def _contains_internal_id(value):
    if isinstance(value, dict):
        return any(
            key == "id" or key.endswith("_id") or _contains_internal_id(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_internal_id(item) for item in value)
    return False


def test_agent_claim_is_business_number_keyed_and_hides_internal_ids(client):
    task = _claimable_task(client)
    project_number, task_number = _numbers(client, task)
    response = client.post(
        f"/projects/{project_number}/tasks/{task_number}/claim",
        json={"assignee": "manager"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["task_number"] == task_number
    assert data["status"] == "claimed"
    assert not _contains_internal_id(data)


def test_agent_release_returns_the_task_to_the_pool(client):
    """The manager can hand a task back before coding starts.

    Its only other exit from a claim was reject_pre_review, which records that
    the DESIGN-* is wrong — a false record when the real reason is "the wrong
    holder has it".
    """
    task = _claimable_task(client)
    project_number, task_number = _numbers(client, task)
    base = f"/projects/{project_number}/tasks/{task_number}"
    client.post(f"{base}/claim", json={"assignee": "manager"})
    response = client.post(f"{base}/release")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "claimable"
    assert data["assignee"] in (None, "")
    assert not _contains_internal_id(data)
    # released for real: claimable again through the same agent surface
    assert (
        client.post(f"{base}/claim", json={"assignee": "other"}).status_code == 200
    )


def test_agent_release_refused_once_coding_started(client):
    task = _ready_for_development_task(client)
    project_number, task_number = _numbers(client, task)
    base = f"/projects/{project_number}/tasks/{task_number}"
    client.post(f"{base}/start-coding")
    assert client.post(f"{base}/release").status_code == 409


def test_agent_task_get_is_business_number_keyed_and_hides_internal_ids(client):
    task = _ready_for_development_task(client)
    project_number, task_number = _numbers(client, task)
    response = client.get(f"/projects/{project_number}/tasks/{task_number}")
    assert response.status_code == 200
    data = response.get_json()
    assert data["task_number"] == task_number
    assert data["design_number"].startswith("P1-DESIGN-")
    assert not _contains_internal_id(data)


def test_agent_task_get_joins_the_full_design_text(client):
    """A coder must receive the design body with the task, not a pointer to it.

    A 1:1 task's own acceptance_criteria is deliberately "以 DESIGN-X 为准", so
    if the design body does not ride along the coder is left implementing the
    title.  Assert the substantive spec fields, not just the key's presence.
    """
    task = _ready_for_development_task(client)
    project_number, task_number = _numbers(client, task)
    response = client.get(f"/projects/{project_number}/tasks/{task_number}")
    assert response.status_code == 200
    design = response.get_json()["design"]
    assert design["design_number"].startswith("P1-DESIGN-")
    for field in (
        "design_scope",
        "selected_solution",
        "constraints",
        "non_goals",
        "acceptance_criteria",
        "test_strategy",
        "risks",
    ):
        assert field in design, f"coder cannot see the design's {field}"
    assert not _contains_internal_id(response.get_json())


def test_agent_task_get_includes_confirmed_project_constitution(client):
    """The project's governance rides along with the task, unabridged.

    A coder only ever receives one TASK-*, so project-wide norms (fixed
    stack, quality bar, ...) have to be pushed to it rather than left for it
    to go fetch on its own; the context delivery change pushes them onto this same response,
    verbatim off ProjectConstitution.to_dict() -- no summarizing, no picking
    which governance fields matter.
    """
    task = _claimable_task(client)
    project_number, task_number = _numbers(client, task)
    client.patch(
        f"/projects/{task['project_id']}/constitution",
        json={
            "fixed_stack": "Flask + SQLite",
            "quality_bar": "pytest -q fully green before merge",
        },
    )
    client.post(f"/projects/{task['project_id']}/constitution/confirm")

    response = client.get(f"/projects/{project_number}/tasks/{task_number}")
    assert response.status_code == 200
    data = response.get_json()
    constitution = data["project_constitution"]
    assert constitution is not None
    assert constitution["status"] == "confirmed"
    assert constitution["fixed_stack"] == "Flask + SQLite"
    assert constitution["quality_bar"] == "pytest -q fully green before merge"
    assert not _contains_internal_id(data)


def test_agent_task_get_hides_an_unconfirmed_project_constitution(client):
    """A draft constitution is not yet governance, so it is not handed over.

    Exposing it would let a coder treat someone's half-written notes as a
    binding rule nobody agreed to. The key stays present (as null), never
    omitted: "unreadable" must be visible, not silently absent, same
    reasoning as `design` being null.
    """
    task = _claimable_task(client)
    project_number, task_number = _numbers(client, task)
    client.patch(
        f"/projects/{task['project_id']}/constitution",
        json={"fixed_stack": "still being drafted"},
    )

    response = client.get(f"/projects/{project_number}/tasks/{task_number}")
    data = response.get_json()
    assert "project_constitution" in data
    assert data["project_constitution"] is None


def test_agent_task_get_project_constitution_is_null_when_project_has_none(client):
    """A project that never touched its constitution still gets the key, null."""
    task = _claimable_task(client)
    project_number, task_number = _numbers(client, task)

    response = client.get(f"/projects/{project_number}/tasks/{task_number}")
    data = response.get_json()
    assert "project_constitution" in data
    assert data["project_constitution"] is None


def test_agent_pre_review_actions_are_business_number_keyed(client):
    task = _claimable_task(client)
    project_number, task_number = _numbers(client, task)
    client.post(
        f"/projects/{project_number}/tasks/{task_number}/claim",
        json={"assignee": "manager"},
    )

    reviewing = client.post(
        f"/projects/{project_number}/tasks/{task_number}/start-pre-review"
    )
    assert reviewing.status_code == 200
    assert reviewing.get_json()["status"] == "pre_reviewing"

    approved = client.post(
        f"/projects/{project_number}/tasks/{task_number}/approve-pre-review",
        json={"reviewer": "human"},
    )
    assert approved.status_code == 200
    assert approved.get_json()["status"] == "ready_for_development"
    assert not _contains_internal_id(approved.get_json())


def test_agent_pre_review_rejection_requires_a_comment(client):
    task = _claimable_task(client)
    project_number, task_number = _numbers(client, task)
    client.post(
        f"/projects/{project_number}/tasks/{task_number}/claim",
        json={"assignee": "manager"},
    )
    client.post(f"/projects/{project_number}/tasks/{task_number}/start-pre-review")
    path = f"/projects/{project_number}/tasks/{task_number}/reject-pre-review"

    assert client.post(path, json={}).status_code == 400
    assert client.post(path, json={"comment": "   "}).status_code == 400
    rejected = client.post(path, json={
        "comment": "failure modes need design work",
        "reviewer": "human",
    })
    assert rejected.status_code == 200
    assert rejected.get_json()["status"] == "needs_revision"


def test_agent_start_coding_is_business_number_keyed(client):
    task = _ready_for_development_task(client)
    project_number, task_number = _numbers(client, task)

    response = client.post(
        f"/projects/{project_number}/tasks/{task_number}/start-coding"
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "in_progress"
    assert not _contains_internal_id(data)


def test_agent_run_delegates_and_hides_internal_ids(client):
    task = _ready_for_development_task(client)
    project_number, task_number = _numbers(client, task)
    client.post(f"/projects/{project_number}/tasks/{task_number}/start-coding")

    response = client.post(
        f"/projects/{project_number}/tasks/{task_number}/agent-runs",
        json={"role": "coder", "status": "passed", "summary": "implemented"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["agent_run"]["role"] == "coder"
    assert data["task"]["status"] == "in_progress"
    assert not _contains_internal_id(data)


def test_agent_link_pr_is_business_number_keyed(client):
    """The number-keyed surface accepts a manually-reported branch/pr_number/
    pr_url — the manual PR model removed all repository-integration resolution;
    Harnoq never calls a git-hosting-provider API."""
    from tests.test_pr_lifecycle import _ready_for_pr_task

    task = _ready_for_pr_task(client)
    project_number, task_number = _numbers(client, task)

    response = client.post(
        f"/projects/{project_number}/tasks/{task_number}/link-pr",
        json={"pr_number": 7, "pr_url": "https://example/pr/7"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["pull_request_record"]["pr_number"] == 7
    assert data["task"]["status"] == "ready_for_pr"
    assert not _contains_internal_id(data)


def test_agent_mark_pr_merged_is_business_number_keyed(client):
    """The manual PR model: sync-pr was renamed mark-pr-merged and dropped all GitHub
    polling — it only records a human's manual merge confirmation now."""
    from tests.test_pr_lifecycle import _ready_for_pr_task

    task = _ready_for_pr_task(client)
    project_number, task_number = _numbers(client, task)

    client.post(
        f"/projects/{project_number}/tasks/{task_number}/link-pr",
        json={"pr_number": 7, "branch_name": "feature"},
    )
    response = client.post(
        f"/projects/{project_number}/tasks/{task_number}/mark-pr-merged"
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["task"]["status"] == "ready_for_pr"
    assert data["pull_request_record"]["status"] == "merged"
    assert not _contains_internal_id(data)


def test_agent_surface_exposes_every_mcp_task_action(client):
    """Every action the MCP tool offers must have a route actually served here.

    These two lists drifted once: mark_ready_for_pr existed in the service and
    on the internal /tasks/{id} surface but on neither the MCP tool nor this
    blueprint, so a manager whose qa run had passed had no call that could reach
    ready_for_pr and the task was stranded in agent_qa. Nothing caught it —
    the agent tests below reach ready_for_pr by borrowing the INTERNAL route,
    hopping straight over the missing one.
    """
    from harnoq_mcp.server import _TASK_ACTION_PATHS

    served = {
        rule.rule.split("/tasks/<task_number>")[1]
        for rule in client.application.url_map.iter_rules()
        if "/tasks/<task_number>" in rule.rule
    }
    missing = {
        action: path
        for action, path in _TASK_ACTION_PATHS.items()
        if path not in served
    }
    assert not missing, f"MCP actions with no agent route: {missing}"


def test_agent_can_drive_qa_to_ready_for_pr_without_internal_routes(client):
    """The manager's own path from a passing qa run to a linkable PR.

    Uses only /projects/{n}/tasks/{n}/* — borrowing an internal /tasks/{id}
    route here would hide exactly the gap this covers.
    """
    task = _ready_for_development_task(client)
    project_number, task_number = _numbers(client, task)
    base = f"/projects/{project_number}/tasks/{task_number}"
    client.post(f"{base}/start-coding")
    client.post(f"{base}/agent-runs", json={"role": "coder", "status": "passed"})
    client.post(
        f"{base}/agent-runs",
        json={"role": "qa", "status": "passed"} | _qa_evidence(task),
    )

    # a passing qa run parks the task in agent_qa; it does not advance itself
    assert client.get(base).get_json()["status"] == "in_progress"

    response = client.post(f"{base}/mark-ready-for-pr")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ready_for_pr"
    assert not _contains_internal_id(data)

    # and the PR can now be linked — the step that was unreachable before
    assert client.post(f"{base}/link-pr", json={"pr_number": 1}).status_code == 200


def test_agent_mark_ready_for_pr_enforces_quality_gate(client):
    task = _ready_for_development_task(client)
    project_number, task_number = _numbers(client, task)
    base = f"/projects/{project_number}/tasks/{task_number}"
    client.post(f"{base}/start-coding")
    # no runs recorded at all -> still coding, gate cannot be reached
    assert client.post(f"{base}/mark-ready-for-pr").status_code == 409


def test_agent_task_routes_return_404_for_unknown_business_numbers(client):
    assert client.post("/projects/P-404/tasks/TASK-1/claim", json={}).status_code == 404
    project = client.post("/projects", json={"name": "P"}).get_json()
    assert client.get(
        f"/projects/{project['project_number']}/tasks/TASK-404"
    ).status_code == 404


def test_agent_task_package_route_is_removed(client):
    task = _ready_for_development_task(client)
    project_number, task_number = _numbers(client, task)
    assert client.get(
        f"/projects/{project_number}/tasks/{task_number}/package"
    ).status_code == 404


def test_agent_can_reopen_a_completed_task_without_internal_routes(client):
    """Rework entry, reachable from the manager's own surface.

    Uses only /projects/{n}/tasks/{n}/* — borrowing an internal /tasks/{id}
    route here would hide the gap this covers.
    """
    task = _ready_for_development_task(client)
    project_number, task_number = _numbers(client, task)
    base = f"/projects/{project_number}/tasks/{task_number}"
    client.post(f"{base}/start-coding")
    client.post(f"{base}/agent-runs", json={"role": "coder", "status": "passed"})
    client.post(
        f"{base}/agent-runs",
        json={"role": "qa", "status": "passed"} | _qa_evidence(task),
    )
    client.post(f"{base}/mark-ready-for-pr")
    client.post(f"{base}/link-pr", json={"pr_number": 1, "branch_name": "b"})
    client.post(f"{base}/mark-pr-merged")
    assert client.post(f"{base}/mark-completed").get_json()["status"] == "completed"

    assert client.post(f"{base}/reopen-completed", json={}).status_code == 400
    resp = client.post(f"{base}/reopen-completed", json={"reason": "integration"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "claimable"

    # A manager picking up the retry must see that PR #1 already merged, or it
    # reports the rework as if it were the first delivery.
    history = client.get(base).get_json()["pull_requests"]
    assert [r["pr_number"] for r in history] == [1]
    assert history[0]["status"] == "merged"


def test_agent_task_get_reports_no_pull_requests_before_any_link(client):
    task = _ready_for_development_task(client)
    project_number, task_number = _numbers(client, task)
    data = client.get(f"/projects/{project_number}/tasks/{task_number}").get_json()
    assert data["pull_requests"] == []
    assert not _contains_internal_id(data)


def test_openapi_documents_every_mcp_task_action():
    """The published contract must not lag the tool surface.

    openapi.yaml is what the OpenWebUI / skill channel reads, so an action
    present in the MCP tool but absent here is invisible on that channel even
    though the route exists — the same drift class as the agent-route gap
    above, one layer further out.
    """
    import yaml

    from harnoq_mcp.server import _TASK_ACTION_PATHS

    # Pre-existing gap, found when this test was written, deliberately NOT
    # fixed here: the whole PR/completion tail is undocumented, so a manager
    # driven through the openapi channel can pre-review and start coding but
    # cannot claim, record a run, link a PR or complete. Listing them keeps
    # the test honest about today's state while still failing on NEW drift.
    # Close the gap and shrink this set; never grow it.
    known_undocumented = {
        "claim", "record_agent_run", "link_pr", "mark_pr_merged",
        "mark_completed",
    }

    spec = yaml.safe_load(open("skills/openapi.yaml", encoding="utf-8"))
    prefix = "/projects/{project_number}/tasks/{task_number}"
    documented = {
        path[len(prefix):]
        for path in spec["paths"]
        if path.startswith(prefix)
    }
    missing = {
        action: path
        for action, path in _TASK_ACTION_PATHS.items()
        if path not in documented and action not in known_undocumented
    }
    assert not missing, f"MCP actions absent from openapi.yaml: {missing}"
    # And the listed gap must not silently widen or go stale.
    still_missing = {
        action for action in known_undocumented
        if _TASK_ACTION_PATHS[action] not in documented
    }
    assert still_missing == known_undocumented, (
        "known_undocumented is stale; documented now: "
        f"{known_undocumented - still_missing}"
    )
