"""Feature-only dependency gate (docs/cn/design-evolution.md §5).

Dependency lives only on the Feature graph now — REQ/Draft/Task dependency
tables are gone. Generation never stores a gate (a generated task's status is
always "claimable" = "generated, awaiting claim"); whether it can actually be
claimed right now is computed LIVE from the Feature graph, at claim time
(the sole authority) and at list-render time (display only, via
claim_ready/waiting_on_feature_numbers)."""

CONFIRM_FIELDS = {
    "problem_statement": "P.",
    "selected_solution": "S.",
    "selection_reason": "R.",
    "risks": "K.",
    "test_strategy": "T.",
    "acceptance_criteria": "A.",
}


def _project(client):
    return client.post("/projects", json={"name": "P"}).get_json()


def _foundation_feature(client, project_id, name, depends_on_feature_ids=None):
    payload = {"name": name, "orientation": "foundation", "subdomain_type": "generic"}
    if depends_on_feature_ids is not None:
        payload["depends_on_feature_ids"] = depends_on_feature_ids
    return client.post(f"/projects/{project_id}/features", json=payload).get_json()


def _confirmed_design_for_feature(client, project_id, feature_id):
    design = client.post(
        f"/projects/{project_id}/design-cases", json={"feature_id": feature_id}
    ).get_json()
    client.patch(f"/design-cases/{design['id']}", json=CONFIRM_FIELDS)
    client.post(f"/design-cases/{design['id']}/confirm")
    return design


def _generate_one_task(client, design_id, title="T"):
    return client.post(
        f"/design-cases/{design_id}/tasks", json={"title": title, "task_type": "feature"}
    ).get_json()


def _force_completed(app, task_id):
    """Directly move a task to ``completed`` bypassing the PR/CI state machine
    — this file tests the Feature-satisfied READ path (feature_satisfied only
    looks at task.status), not the mark_completed transition itself (covered
    in test_github_pr_ci.py)."""
    from app.repositories import development_task_repo

    with app.app_context():
        task = development_task_repo.get(task_id)
        task.status = "completed"
        development_task_repo.save(task)


def test_upstream_not_designed_still_blocks_claim(client):
    # Scenario 1: A has no design at all — B's task is still refused.
    project = _project(client)
    a = _foundation_feature(client, project["id"], "A")
    b = _foundation_feature(client, project["id"], "B", depends_on_feature_ids=[a["id"]])
    b_design = _confirmed_design_for_feature(client, project["id"], b["id"])
    task_b = _generate_one_task(client, b_design["id"])

    assert task_b["status"] == "claimable"  # no stored gate
    response = client.post(f"/tasks/{task_b['id']}/claim", json={"assignee": "x"})
    assert response.status_code == 409


def test_upstream_completed_unblocks_claim(client, app):
    # Scenario 2: once A's only task is completed, B's claim succeeds.
    project = _project(client)
    a = _foundation_feature(client, project["id"], "A")
    b = _foundation_feature(client, project["id"], "B", depends_on_feature_ids=[a["id"]])
    a_design = _confirmed_design_for_feature(client, project["id"], a["id"])
    task_a = _generate_one_task(client, a_design["id"])
    b_design = _confirmed_design_for_feature(client, project["id"], b["id"])
    task_b = _generate_one_task(client, b_design["id"])

    assert client.post(f"/tasks/{task_b['id']}/claim", json={"assignee": "x"}).status_code == 409

    _force_completed(app, task_a["id"])

    assert client.post(f"/tasks/{task_b['id']}/claim", json={"assignee": "x"}).status_code == 200


def test_upstream_partially_completed_still_blocks(client, app):
    # Scenario 3: A has two designs; only one's task is completed, the other
    # is still in draft — B stays blocked.
    project = _project(client)
    a = _foundation_feature(client, project["id"], "A")
    b = _foundation_feature(client, project["id"], "B", depends_on_feature_ids=[a["id"]])
    a_design_1 = _confirmed_design_for_feature(client, project["id"], a["id"])
    task_a1 = _generate_one_task(client, a_design_1["id"], title="A1")
    # A second design under A that is never confirmed/generated.
    client.post(f"/projects/{project['id']}/design-cases", json={"feature_id": a["id"]})

    b_design = _confirmed_design_for_feature(client, project["id"], b["id"])
    task_b = _generate_one_task(client, b_design["id"])

    _force_completed(app, task_a1["id"])

    response = client.post(f"/tasks/{task_b['id']}/claim", json={"assignee": "x"})
    assert response.status_code == 409


def test_create_task_not_blocked_by_unready_upstream(client):
    # Scenario 4: task creation itself is never gated — only claim is. B can
    # create a task even while A has no design at all.
    project = _project(client)
    a = _foundation_feature(client, project["id"], "A")
    b = _foundation_feature(client, project["id"], "B", depends_on_feature_ids=[a["id"]])
    b_design = _confirmed_design_for_feature(client, project["id"], b["id"])

    response = client.post(
        f"/design-cases/{b_design['id']}/tasks", json={"title": "T", "task_type": "feature"}
    )

    assert response.status_code == 201
    assert response.get_json()["status"] == "claimable"


def test_design_without_feature_is_unconstrained(client):
    # Scenario 5: a design whose feature was purged (feature_id nulled via
    # ON DELETE SET NULL) is not subject to the Feature graph at all — claim
    # succeeds immediately.
    project = _project(client)
    feature = client.post(
        f"/projects/{project['id']}/features", json={"name": "F"}
    ).get_json()
    design = client.post(
        f"/projects/{project['id']}/design-cases", json={"feature_id": feature["id"]}
    ).get_json()
    client.patch(f"/design-cases/{design['id']}", json=CONFIRM_FIELDS)
    client.post(f"/design-cases/{design['id']}/confirm")
    task = _generate_one_task(client, design["id"])

    client.delete(f"/features/{feature['id']}")
    assert client.delete(f"/features/{feature['id']}/purge").status_code == 204

    response = client.post(f"/tasks/{task['id']}/claim", json={"assignee": "x"})
    assert response.status_code == 200


def test_no_stored_gate_status(client, app):
    # Scenario 6: "blocked" no longer exists as a stored status — a generated
    # task is always "claimable", even when its upstream Feature is nowhere
    # near satisfied. See app/models/development_task.py DEVELOPMENT_TASK_STATUSES.
    from app.models.development_task import DEVELOPMENT_TASK_STATUSES

    assert "blocked" not in DEVELOPMENT_TASK_STATUSES

    project = _project(client)
    a = _foundation_feature(client, project["id"], "A")
    b = _foundation_feature(client, project["id"], "B", depends_on_feature_ids=[a["id"]])
    b_design = _confirmed_design_for_feature(client, project["id"], b["id"])
    task_b = _generate_one_task(client, b_design["id"])

    assert task_b["status"] == "claimable"


def test_batch_list_reflects_live_gate(client, app):
    # Scenario 7: the project task list (list_by_project_with_gate) computes
    # claim_ready / waiting_on_feature_numbers in a batch, matching the
    # per-task claim outcome, both before and after the upstream completes.
    project = _project(client)
    a = _foundation_feature(client, project["id"], "A")
    b = _foundation_feature(client, project["id"], "B", depends_on_feature_ids=[a["id"]])
    a_design = _confirmed_design_for_feature(client, project["id"], a["id"])
    task_a = _generate_one_task(client, a_design["id"])
    b_design = _confirmed_design_for_feature(client, project["id"], b["id"])
    task_b = _generate_one_task(client, b_design["id"])

    def _by_id(task_id):
        listed = client.get(f"/projects/{project['id']}/tasks").get_json()
        return next(t for t in listed if t["id"] == task_id)

    before = _by_id(task_b["id"])
    assert before["claim_ready"] is False
    assert before["waiting_on_feature_numbers"] == [a["feature_number"]]

    _force_completed(app, task_a["id"])

    after = _by_id(task_b["id"])
    assert after["claim_ready"] is True
    assert after["waiting_on_feature_numbers"] == []


def test_soft_deleted_own_design_does_not_bypass_gate(client):
    # Regression: a task's Feature affiliation must survive its DesignCase
    # being soft-deleted — deleting the design must NOT let the task slip
    # past the claim-time gate (see feature_service._feature_id_of_task /
    # design_case_repo.get_including_deleted).
    project = _project(client)
    a = _foundation_feature(client, project["id"], "A")
    b = _foundation_feature(client, project["id"], "B", depends_on_feature_ids=[a["id"]])
    b_design = _confirmed_design_for_feature(client, project["id"], b["id"])
    task_b = _generate_one_task(client, b_design["id"])

    assert client.post(f"/tasks/{task_b['id']}/claim", json={"assignee": "x"}).status_code == 409

    assert client.delete(f"/design-cases/{b_design['id']}").status_code == 200

    response = client.post(f"/tasks/{task_b['id']}/claim", json={"assignee": "x"})
    assert response.status_code == 409

    listed = client.get(f"/projects/{project['id']}/tasks").get_json()
    still_gated = next(t for t in listed if t["id"] == task_b["id"])
    assert still_gated["claim_ready"] is False


def test_deleting_upstream_feature_unblocks_instead_of_deadlocking(client):
    # Regression: a dependency edge whose target Feature was soft-deleted
    # must be treated as inert (matching Feature.to_dict()'s own live-target
    # filter for the displayed dependency list), not as a permanently
    # unsatisfiable upstream — and never leak a raw None into
    # waiting_on_feature_numbers (see feature_dependency_repo.depends_on_
    # feature_ids / list_live_edges_by_project).
    project = _project(client)
    a = _foundation_feature(client, project["id"], "A")
    b = _foundation_feature(client, project["id"], "B", depends_on_feature_ids=[a["id"]])
    b_design = _confirmed_design_for_feature(client, project["id"], b["id"])
    task_b = _generate_one_task(client, b_design["id"])

    assert client.post(f"/tasks/{task_b['id']}/claim", json={"assignee": "x"}).status_code == 409

    assert client.delete(f"/features/{a['id']}").status_code == 200

    listed = client.get(f"/projects/{project['id']}/tasks").get_json()
    unblocked = next(t for t in listed if t["id"] == task_b["id"])
    assert unblocked["claim_ready"] is True
    assert unblocked["waiting_on_feature_numbers"] == []

    response = client.post(f"/tasks/{task_b['id']}/claim", json={"assignee": "y"})
    assert response.status_code == 200
