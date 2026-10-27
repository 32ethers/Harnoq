"""Forward gates across the design lifecycle, and the absence of propagation.

Nothing flags a downstream object's STATUS any more (the change model retired
needs_review): editing a feature, reopening the constitution or touching an
INPUT leaves every confirmed design confirmed. "Upstream moved under me" is a
property of an edge, derived on read from version snapshots, not a state
written onto the design. What is tested here:
  - a feature change (of any kind) does NOT touch the designs under it, and
  - the design's own forward gate: a design that is not confirmed cannot
    produce a task until it is re-confirmed.
"""

_DESIGN_FIELDS = {
    "problem_statement": "Users need login.",
    "selected_solution": "Email login.",
    "selection_reason": "Smallest viable scope.",
    "risks": "Credential stuffing.",
    "test_strategy": "Auth unit tests and login integration test.",
    "acceptance_criteria": "User can log in with valid credentials.",
}


def _make_project(client, name="P"):
    return client.post("/projects", json={"name": name}).get_json()


def _make_feature(client, project_id, name="F"):
    return client.post(
        f"/projects/{project_id}/features", json={"name": name}
    ).get_json()


def _make_confirmed_design(client, project_id, feature_id):
    design = client.post(
        f"/projects/{project_id}/design-cases", json={"feature_id": feature_id}
    ).get_json()
    client.patch(f"/design-cases/{design['id']}", json=_DESIGN_FIELDS)
    client.post(f"/design-cases/{design['id']}/confirm")
    return client.get(f"/design-cases/{design['id']}").get_json()


def _add_task(client, design_id, title="d0"):
    return client.post(
        f"/design-cases/{design_id}/tasks",
        json={"title": title, "task_type": "feature"},
    ).get_json()


def _change_feature_shape(client, feature_id):
    """A design-relevant feature edit (its description / boundary)."""
    return client.patch(
        f"/features/{feature_id}", json={"description": "boundary reshaped"}
    )


def test_reopen_design_blocks_task_creation_until_reconfirmed(client):
    project = _make_project(client)
    feature = _make_feature(client, project["id"])
    design = _make_confirmed_design(client, project["id"], feature["id"])
    _add_task(client, design["id"])

    resp = client.post(f"/design-cases/{design['id']}/reopen")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "draft"
    blocked = client.post(
        f"/design-cases/{design['id']}/tasks",
        json={"title": "later", "task_type": "feature"},
    )
    assert blocked.status_code == 409


def test_delete_design_removes_design_access(client):
    project = _make_project(client)
    feature = _make_feature(client, project["id"])
    design = _make_confirmed_design(client, project["id"], feature["id"])

    resp = client.delete(f"/design-cases/{design['id']}")
    assert resp.status_code == 200
    assert client.get(f"/design-cases/{design['id']}").status_code == 404


def test_feature_shape_change_leaves_its_designs_confirmed(client):
    # Regression against the old feature -> design needs_review cascade. The
    # edit may well invalidate the design, but that is a fact about the edge:
    # the design's own status keeps describing the design, and work under it is
    # not blocked by a flag nobody reviewed.
    project = _make_project(client)
    feature = _make_feature(client, project["id"])
    design = _make_confirmed_design(client, project["id"], feature["id"])
    _add_task(client, design["id"])

    resp = _change_feature_shape(client, feature["id"])
    assert resp.status_code == 200
    design_after = client.get(f"/design-cases/{design['id']}").get_json()
    assert design_after["status"] == "confirmed"
    still_allowed = client.post(
        f"/design-cases/{design['id']}/tasks",
        json={"title": "another", "task_type": "feature"},
    )
    assert still_allowed.status_code == 409


def test_feature_assignee_change_leaves_its_designs_confirmed(client):
    # assignee is ownership, not shape. Kept as a second angle on "no cascade":
    # this edit was already exempt when the cascade existed.
    project = _make_project(client)
    feature = _make_feature(client, project["id"])
    design = _make_confirmed_design(client, project["id"], feature["id"])

    resp = client.post(
        f"/features/{feature['id']}/assignee", json={"assignee": "alice"}
    )
    assert resp.status_code == 200
    assert client.get(f"/design-cases/{design['id']}").get_json()["status"] == "confirmed"


def test_decision_b_create_task_blocked_when_design_not_confirmed(client):
    project = _make_project(client)
    feature = _make_feature(client, project["id"])
    design = _make_confirmed_design(client, project["id"], feature["id"])
    _add_task(client, design["id"])
    client.post(f"/design-cases/{design['id']}/reopen")

    design_after = client.get(f"/design-cases/{design['id']}").get_json()
    assert design_after["status"] == "draft"

    resp = client.post(
        f"/design-cases/{design['id']}/tasks",
        json={"title": "another", "task_type": "feature"},
    )
    assert resp.status_code == 409


def test_needs_review_is_not_a_status_anywhere(client):
    """The state is gone from the vocabulary, not just unreachable.

    A PATCH aiming at it must be refused as an invalid status rather than
    silently accepted, on every entity that shares the lifecycle.
    """
    from app.services import lifecycle

    assert "needs_review" not in lifecycle.LIFECYCLE_STATUSES
    assert not hasattr(lifecycle, "NEEDS_REVIEW")
    assert not hasattr(lifecycle, "flag_needs_review")

    project = _make_project(client)
    feature = _make_feature(client, project["id"])
    design = client.post(
        f"/projects/{project['id']}/design-cases", json={"feature_id": feature["id"]}
    ).get_json()
    response = client.patch(
        f"/design-cases/{design['id']}", json={"status": "needs_review"}
    )
    assert response.status_code == 400
