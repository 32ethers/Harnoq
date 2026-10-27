"""delivery_gate.gate_status and the _current_stage routing it feeds.

See docs/cn/design-evolution.md#delivery. Every task in these tests
is walked through the REAL HTTP lifecycle (claim -> ... -> mark-completed),
not poked directly into the DB, so a test failure here means the gate
actually misreads production data, not a fixture shortcut.
"""
from app.api.skills import _current_stage
from app.services import acceptance_criteria as ac_module
from app.services import delivery_gate

_DESIGN_CONFIRM_FIELDS = {
    "problem_statement": "Users need a way to reconcile two ledgers.",
    "selected_solution": "Batch reconciliation job.",
    "selection_reason": "Smallest viable scope.",
    "risks": "Double-counting on retry.",
    "test_strategy": "Reconciliation unit test plus an idempotency test.",
    "acceptance_criteria": "The job reconciles two ledgers without double-counting.",
}


def _make_project(client, name="P"):
    return client.post("/projects", json={"name": name}).get_json()


def _make_project_number(client, project_id):
    return client.get(f"/projects/{project_id}").get_json()["project_number"]


def _make_feature(client, project_id, name, foundation_kind=None):
    payload = {"name": name}
    if foundation_kind is not None:
        payload["foundation_kind"] = foundation_kind
        payload["orientation"] = "foundation"
    resp = client.post(f"/projects/{project_id}/features", json=payload)
    assert resp.status_code in (200, 201), resp.get_data(as_text=True)
    return resp.get_json()


def _make_confirmed_design(client, project_id, feature_id):
    design = client.post(
        f"/projects/{project_id}/design-cases", json={"feature_id": feature_id}
    ).get_json()
    patched = client.patch(f"/design-cases/{design['id']}", json=_DESIGN_CONFIRM_FIELDS)
    assert patched.status_code == 200, patched.get_data(as_text=True)
    confirmed = client.post(f"/design-cases/{design['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.get_data(as_text=True)
    return confirmed.get_json()


def _complete_task(client, design, title="T"):
    """Walk one TASK-* under ``design`` all the way to ``completed`` through
    the real HTTP lifecycle, and return the completed task dict."""
    task = client.post(
        f"/design-cases/{design['id']}/tasks",
        json={"title": title, "task_type": "feature"},
    ).get_json()
    return _drive_to_completed(client, design, task)


def _drive_to_completed(client, design, task):
    """Same walk, but for a task that already exists — so a test can create an
    unfinished task, observe the gate, then finish that same one."""
    # Agent-facing task responses intentionally hide database ids.  Use the
    # business-number routes for source-backed tasks; retain the legacy route
    # only for dashboard-shaped fixtures that still include an id.
    tid = task.get("id")
    if tid is not None:
        base = f"/tasks/{tid}"
    else:
        project_id = task.get("project_id") or design.get("project_id")
        project_number = _make_project_number(client, project_id)
        base = f"/projects/{project_number}/tasks/{task['task_number']}"
    assert client.post(f"{base}/claim", json={"assignee": "alice"}).status_code == 200
    assert client.post(f"{base}/start-pre-review").status_code == 200
    assert (
        client.post(f"{base}/approve-pre-review", json={"reviewer": "alice"}).status_code
        == 200
    )
    assert client.post(f"{base}/start-coding").status_code == 200
    coder = client.post(
        f"{base}/agent-runs",
        json={"role": "coder", "status": "passed", "summary": "implemented"},
    )
    assert coder.status_code == 200, coder.get_data(as_text=True)

    criteria = ac_module.split(task.get("acceptance_criteria") or _DESIGN_CONFIRM_FIELDS["acceptance_criteria"])
    qa = client.post(
        f"{base}/agent-runs",
        json={
            "role": "qa",
            "status": "passed",
            "summary": "green and reviewed",
                "design_number": design.get("design_number"),
            "criteria_verdicts": [
                {
                    "criterion": criterion,
                    "verdict": "pass",
                    "evidence": {
                        "tests": ["tests/test_reconciliation.py::test_path"],
                        "code": [],
                        "note": "covered by the reconciliation test",
                    },
                }
                for criterion in criteria
            ],
            "test_run": {
                "command": "run the project's test suite",
                "exit_code": 0,
                "collected": 3,
                "passed": 3,
                "failed": 0,
                "skipped": 0,
            },
        },
    )
    assert qa.status_code == 200, qa.get_data(as_text=True)

    assert client.post(f"{base}/mark-ready-for-pr").status_code == 200
    linked = client.post(
        f"{base}/link-pr",
        json={
            "pr_number": task["task_number"],
            "branch_name": f"branch-{task['task_number']}",
            "pr_url": f"https://example.invalid/pull/{task['task_number']}",
        },
    )
    assert linked.status_code == 200, linked.get_data(as_text=True)
    assert client.post(f"{base}/mark-pr-merged").status_code == 200
    completed = client.post(f"{base}/mark-completed")
    assert completed.status_code == 200, completed.get_data(as_text=True)
    return completed.get_json()


def _capability_task(client, project_id, title="T"):
    """A completed task under an ordinary (non-foundation) Feature."""
    feature = _make_feature(client, project_id, f"Capability {title}")
    design = _make_confirmed_design(client, project_id, feature["id"])
    return _complete_task(client, design, title=title)


# --- gate_status ------------------------------------------------------------


def test_gate_status_no_tasks_owes_nothing(client):
    project = _make_project(client)
    gate = delivery_gate.gate_status(project["id"])
    assert gate == {
        "integration_owed": False,
        "system_owed": False,
        "last_capability_completion": None,
        "last_integration_completion": None,
        "last_system_completion": None,
        "open_integration_item_numbers": [],
        "open_system_item_numbers": [],
        "capability_outstanding": False,
        "outstanding_capability_task_numbers": [],
        "system_inventory_outstanding": False,
        "system_inventory_errors": [],
        "system_inventory_pending_keys": [],
        "integration_inventory_outstanding": False,
        "integration_inventory_errors": [],
    }


def test_gate_status_incomplete_capability_task_owes_nothing(client):
    project = _make_project(client)
    feature = _make_feature(client, project["id"], "Cap")
    design = _make_confirmed_design(client, project["id"], feature["id"])
    # Create the task but never walk it to completed.
    client.post(
        f"/design-cases/{design['id']}/tasks", json={"title": "T", "task_type": "feature"}
    )
    gate = delivery_gate.gate_status(project["id"])
    assert gate["integration_owed"] is False
    assert gate["system_owed"] is False
    assert gate["last_capability_completion"] is None


def test_gate_status_capability_completed_owes_integration(client):
    project = _make_project(client)
    _capability_task(client, project["id"])
    gate = delivery_gate.gate_status(project["id"])
    assert gate["integration_owed"] is True
    assert gate["system_owed"] is False
    assert gate["last_capability_completion"] is not None
    assert gate["last_integration_completion"] is None


def test_gate_status_one_of_several_capability_tasks_owes_nothing(client):
    """The gate is EVERY capability task, not ANY. An earlier draft said "any",
    which would tell a project to start integrating a product whose other parts
    are still being written — integration is what you do to finished parts."""
    project = _make_project(client)
    _capability_task(client, project["id"], title="Done")

    feature = _make_feature(client, project["id"], "Still building")
    design = _make_confirmed_design(client, project["id"], feature["id"])
    client.post(
        f"/design-cases/{design['id']}/tasks",
        json={"title": "In flight", "task_type": "feature"},
    )

    gate = delivery_gate.gate_status(project["id"])
    assert gate["integration_owed"] is False
    assert gate["last_capability_completion"] is not None


def test_gate_status_opens_once_the_last_capability_task_lands(client):
    """Same project, one task later — the outstanding one is what held it."""
    project = _make_project(client)
    _capability_task(client, project["id"], title="Done")
    feature = _make_feature(client, project["id"], "Second")
    design = _make_confirmed_design(client, project["id"], feature["id"])
    pending = client.post(
        f"/design-cases/{design['id']}/tasks",
        json={"title": "In flight", "task_type": "feature"},
    ).get_json()
    assert delivery_gate.gate_status(project["id"])["integration_owed"] is False

    _drive_to_completed(client, design, pending)
    assert delivery_gate.gate_status(project["id"])["integration_owed"] is True


def test_gate_status_falls_back_to_updated_at_without_task_event(app, client):
    """A task completed before task_event started being recorded (an old
    project, pre-migration) carries no completed-transition row for it. The
    gate must still read task.updated_at for that one task rather than treat
    it as never-completed — this is the ONE case the design doc says the
    updated_at fallback is acceptable for."""
    from app.models.development_task import DevelopmentTask
    from app.models.task_event import TaskEvent

    project = _make_project(client)
    task = _capability_task(client, project["id"])

    with app.app_context():
        # Simulate "no task_event row for this completion" by deleting the
        # real completed-transition row the HTTP lifecycle above just wrote.
        deleted = (
            TaskEvent.delete()
            .where((TaskEvent.task_id == task["id"]) & (TaskEvent.to_status == "completed"))
            .execute()
        )
        assert deleted >= 1
        stored_updated_at = DevelopmentTask.get_by_id(task["id"]).updated_at

    gate = delivery_gate.gate_status(project["id"])
    assert gate["integration_owed"] is True
    assert gate["last_capability_completion"] == stored_updated_at.isoformat()


# --- _current_stage routing --------------------------------------------------
#
# Pure-function tests: no client/DB, matching the existing style in
# test_skills.py (see test_current_stage_is_planning_complete_when_all_
# confirmed_designs_have_tasks, which this file must not break — it calls
# _current_stage WITHOUT a gate argument at all).


def _base_args():
    constitution = {"status": "confirmed"}
    source_requirements = [{"status": "confirmed"}]
    design_cases = [{"status": "confirmed", "task_count": 1}]
    feature_map = {"pending_design_feature_numbers": []}
    return constitution, source_requirements, design_cases, feature_map


def test_current_stage_planning_complete_while_development_in_progress():
    # Every confirmed design already has a task, but nothing has completed
    # yet (development in progress) -- delivery_gate reports nothing owed,
    # and this must still read planning_complete, not integration/system.
    # This is the behavior the construction order explicitly says to keep.
    constitution, source_requirements, design_cases, feature_map = _base_args()
    gate = {"integration_owed": False, "system_owed": False}
    stage = _current_stage(
        constitution, source_requirements, design_cases, feature_map, gate=gate
    )
    assert stage == "planning_complete"


def test_current_stage_routes_to_integration_when_owed():
    constitution, source_requirements, design_cases, feature_map = _base_args()
    gate = {"integration_owed": True, "system_owed": False}
    stage = _current_stage(
        constitution, source_requirements, design_cases, feature_map, gate=gate
    )
    assert stage == "integration"


def test_current_stage_routes_to_system_when_owed():
    constitution, source_requirements, design_cases, feature_map = _base_args()
    gate = {"integration_owed": False, "system_owed": True}
    stage = _current_stage(
        constitution, source_requirements, design_cases, feature_map, gate=gate
    )
    assert stage == "system"


def test_current_stage_integration_outranks_system_if_gate_ever_reports_both():
    # Should never happen from a real gate_status() (system_owed's own
    # formula requires NOT integration_owed), but _current_stage's branch
    # order is the last line of defense -- prove it explicitly rather than
    # trust the caller never sends a contradictory dict.
    constitution, source_requirements, design_cases, feature_map = _base_args()
    gate = {"integration_owed": True, "system_owed": True}
    stage = _current_stage(
        constitution, source_requirements, design_cases, feature_map, gate=gate
    )
    assert stage == "integration"


def test_current_stage_ignores_gate_while_designs_are_still_untasked():
    # The task_planning gate outranks the delivery gate: an unfinished
    # planning pass must not be skipped just because some earlier batch of
    # capability work made integration_owed true.
    constitution, source_requirements, _, feature_map = _base_args()
    design_cases = [
        {"status": "confirmed", "task_count": 1},
        {"status": "confirmed", "task_count": 0},
    ]
    gate = {"integration_owed": True, "system_owed": False}
    stage = _current_stage(
        constitution, source_requirements, design_cases, feature_map, gate=gate
    )
    assert stage == "task_planning"


def test_current_stage_finishes_capability_task_planning_before_integration():
    constitution, source_requirements, _, _ = _base_args()
    design_cases = [
        {"design_number": "DESIGN-1", "status": "confirmed", "task_count": 1},
        {"design_number": "DESIGN-2", "status": "confirmed", "task_count": 0},
    ]
    feature_map = {
        "features": [
            {
                "feature_number": "FEATURE-1",
                "foundation_kind": None,
                "design_numbers": ["DESIGN-1"],
            },
            {
                "feature_number": "FEATURE-2",
                "foundation_kind": None,
                "design_numbers": ["DESIGN-2"],
            },
        ],
        "pending_design_feature_numbers": [],
    }
    stage = _current_stage(
        constitution,
        source_requirements,
        design_cases,
        feature_map,
        gate={"integration_owed": True, "system_owed": False},
    )
    assert stage == "task_planning"


def test_current_stage_finishes_capability_design_before_integration():
    constitution, source_requirements, design_cases, _ = _base_args()
    feature_map = {
        "features": [
            {
                "feature_number": "FEATURE-2",
                "foundation_kind": None,
                "design_numbers": [],
            }
        ],
        "pending_design_feature_numbers": ["FEATURE-2"],
    }
    stage = _current_stage(
        constitution,
        source_requirements,
        design_cases,
        feature_map,
        gate={"integration_owed": True, "system_owed": False},
    )
    assert stage == "design"


# --- end-to-end wiring through /skills/project-context -----------------------


def _confirm_source_requirement_and_constitution(client, project_id):
    sr = client.post(
        f"/projects/{project_id}/source-requirements",
        json={
            "title": "Reconciliation",
            "source_type": "new_feature",
            "raw_content": "Users need two ledgers reconciled nightly.",
        },
    ).get_json()
    confirmed_sr = client.post(f"/source-requirements/{sr['id']}/confirm")
    assert confirmed_sr.status_code == 200, confirmed_sr.get_data(as_text=True)

    client.patch(
        f"/projects/{project_id}/constitution",
        json={"system_nature": "A nightly ledger-reconciliation batch job."},
    )
    confirmed_constitution = client.post(f"/projects/{project_id}/constitution/confirm")
    assert confirmed_constitution.status_code == 200, confirmed_constitution.get_data(
        as_text=True
    )


def test_project_context_reports_integration_stage_after_capability_completion(client):
    """End-to-end: the delivery_gate computed in
    _project_context_read_action actually reaches _current_stage and gets
    hoisted as active_stage_method — not just correct in isolation."""
    project = _make_project(client)
    _confirm_source_requirement_and_constitution(client, project["id"])
    _capability_task(client, project["id"])
    item = client.post(
        f"/projects/{project['id']}/delivery-items",
        json={"kind": "assembly", "subject": "Production entry point wiring"},
    ).get_json()

    context = client.post(
        "/skills/project-context",
        json={"project_number": project["project_number"]},
    ).get_json()

    assert context["current_stage"] == "integration"
    assert context["active_stage_method"] is not None
    vocabulary = context["active_workflow_vocabulary"]
    assert "assembly GAP" in vocabulary["terms"]["assembly"]
    assert "one exact assembly edge" in vocabulary["terms"]["INTEG-*"]
    assert "unique missing edges" in vocabulary["terms"]["assembly"]
    assert "owned directly by the active system stage run" in vocabulary["terms"]["SYS-*"]
    assert "Round-1 validation gate" in vocabulary["terms"]["integration inventory manifest"]
    assert "creation-binding receipt" in vocabulary["terms"]["integration inventory manifest"]
    assert "never INTEG-*" in vocabulary["terms"]["integration inventory manifest"]
    assert "actually operates delivered product behavior" in vocabulary["terms"]["production entry point"]
    assert "Build, bundle, compile" in vocabulary["terms"]["production entry point"]
    assert "fully wired transitive path" in vocabulary["terms"]["production-reachable"]
    assert "producer -> consumer" in vocabulary["terms"]["seam"]
    assert "only an unwired seam" in vocabulary["terms"]["seam"]
    assert "Same name, body, fields or data shape alone" in vocabulary["terms"]["duplicate"]
    assert "one legitimate canonical owner" in vocabulary["terms"]["duplicate"]
    assert "unreachable rows reconcile" in vocabulary["terms"]["assembly matrix"]
    assert "mechanically extracted" in vocabulary["terms"]["seam graph"]
    assert "complete identifier is exactly equal" in vocabulary["terms"]["exact-name collision"]
    assert "implementation bodies omitted" in vocabulary["terms"]["source skeleton"]
    assert "structural fitness test" in vocabulary["terms"]["convergence"]
    assert "rounds" in context["active_stage_method"]
    assert "EXIT ASSERTION" in context["next_step"]
    assert context["delivery_gate"]["open_integration_item_numbers"] == [
        item["item_number"]
    ]
    assert context["delivery_progress"] == {
        "resume_at": "integration",
        "open_item_numbers": [item["item_number"]],
    }
    assert context["delivery_items"][0]["item_number"] == item["item_number"]
    assert "id" not in context["delivery_items"][0]
    assert "project_id" not in context["delivery_items"][0]
