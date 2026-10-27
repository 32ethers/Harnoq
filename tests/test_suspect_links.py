"""Suspect links (see docs/cn/design-evolution.md §6): the replacement for needs_review.

The point of the replacement is that nothing is stored on the downstream object
and nothing cascades — the fact lives on the EDGE and is compared at read time.
So these tests check three things: the basis gets recorded at the right moments,
staleness appears (and disappears) purely as a function of version numbers, and
a suspect edge gates nothing.

A design's only upstream is its FEATURE (the REQ-* layer was removed
that used to sit between them), so every design-suspect scenario here is
triggered by revising or removing the feature.
"""
from tests.test_design_cases import CONFIRM_FIELDS


def _project(client, name="P"):
    return client.post("/projects", json={"name": name}).get_json()


def _feature(client, project_id, name="f"):
    return client.post(
        f"/projects/{project_id}/features", json={"name": name}
    ).get_json()


def _design(client, project_id, feature_id):
    return client.post(
        f"/projects/{project_id}/design-cases", json={"feature_id": feature_id}
    ).get_json()


def _confirm_design(client, design_id):
    client.patch(f"/design-cases/{design_id}", json=CONFIRM_FIELDS)
    return client.post(f"/design-cases/{design_id}/confirm").get_json()


def _task(client, design_id, title="T"):
    return client.post(
        f"/design-cases/{design_id}/tasks",
        json={"title": title, "task_type": "feature"},
    ).get_json()


def _get_design(client, design_id):
    return client.get(f"/design-cases/{design_id}").get_json()


def _get_task(client, task_id):
    return client.get(f"/tasks/{task_id}").get_json()


def test_a_freshly_confirmed_design_is_not_suspect(client):
    project = _project(client)
    feature = _feature(client, project["id"])
    design = _design(client, project["id"], feature["id"])
    _confirm_design(client, design["id"])

    fetched = _get_design(client, design["id"])
    assert fetched["suspect"] is False
    assert fetched["suspect_upstream"] == []


def test_reshaping_the_feature_makes_its_designs_suspect(client):
    # This edge is what replaces the deleted feature -> design needs_review
    # cascade: the same signal, on the edge, without moving the design's status.
    project = _project(client)
    feature = _feature(client, project["id"])
    design = _design(client, project["id"], feature["id"])
    _confirm_design(client, design["id"])

    client.patch(f"/features/{feature['id']}", json={"name": "reshaped"})

    fetched = _get_design(client, design["id"])
    assert fetched["suspect"] is True
    assert [e["upstream_type"] for e in fetched["suspect_upstream"]] == ["feature"]
    assert fetched["status"] == "confirmed"


def test_suspect_blocks_nothing(client):
    project = _project(client)
    feature = _feature(client, project["id"])
    design = _design(client, project["id"], feature["id"])
    _confirm_design(client, design["id"])
    client.patch(f"/features/{feature['id']}", json={"name": "改了"})
    assert _get_design(client, design["id"])["suspect"] is True

    created = client.post(
        f"/design-cases/{design['id']}/tasks",
        json={"title": "still allowed", "task_type": "feature"},
    )
    assert created.status_code == 201


def test_realigning_clears_the_suspicion_without_touching_content(client):
    project = _project(client)
    feature = _feature(client, project["id"])
    design = _design(client, project["id"], feature["id"])
    _confirm_design(client, design["id"])
    client.patch(f"/features/{feature['id']}", json={"name": "改了"})

    realigned = client.post(f"/design-cases/{design['id']}/realign-basis")
    assert realigned.status_code == 200
    assert realigned.get_json()["suspect"] is False
    after = _get_design(client, design["id"])
    assert after["status"] == "confirmed"
    assert after["problem_statement"] == CONFIRM_FIELDS["problem_statement"]

    # And it is not a one-time silence: a FURTHER change is suspect again.
    client.patch(f"/features/{feature['id']}", json={"name": "又改了"})
    assert _get_design(client, design["id"])["suspect"] is True


def test_deleting_the_feature_makes_its_design_suspect(client):
    # A withdrawn upstream (soft-deleted, or purged/gone) must read as suspect
    # too — silence here would be the false "still holds" signal that suspect
    # links exist to prevent (see docs/cn/design-evolution.md §6).
    project = _project(client)
    feature = _feature(client, project["id"])
    design = _design(client, project["id"], feature["id"])
    _confirm_design(client, design["id"])

    client.delete(f"/features/{feature['id']}")

    fetched = _get_design(client, design["id"])
    assert fetched["suspect"] is True
    edge = next(
        e for e in fetched["suspect_upstream"]
        if e["upstream_type"] == "feature" and e["upstream_id"] == feature["id"]
    )
    assert edge["reason"] == "upstream_deleted"

    # Same signal shows up through the bulk/list path, not just the detail one.
    designs = client.get(f"/projects/{project['id']}/design-cases").get_json()
    assert [d["suspect"] for d in designs] == [True]


def test_reconfirming_records_a_fresh_basis(client):
    project = _project(client)
    feature = _feature(client, project["id"])
    design = _design(client, project["id"], feature["id"])
    _confirm_design(client, design["id"])
    client.patch(f"/features/{feature['id']}", json={"name": "改了"})
    assert _get_design(client, design["id"])["suspect"] is True

    client.post(f"/design-cases/{design['id']}/reopen")
    # Reopened: nothing is settled, so nothing can be stale.
    assert _get_design(client, design["id"])["suspect"] is False
    client.post(f"/design-cases/{design['id']}/confirm")
    assert _get_design(client, design["id"])["suspect"] is False


def test_revising_the_design_makes_its_tasks_suspect(client):
    project = _project(client)
    feature = _feature(client, project["id"])
    design = _design(client, project["id"], feature["id"])
    _confirm_design(client, design["id"])
    task = _task(client, design["id"])
    assert _get_task(client, task["id"])["suspect"] is False

    client.post(f"/design-cases/{design['id']}/reopen")
    client.patch(f"/design-cases/{design['id']}", json={"risks": "新的风险"})
    client.post(f"/design-cases/{design['id']}/confirm")

    fetched = _get_task(client, task["id"])
    assert fetched["suspect"] is True
    assert fetched["suspect_upstream"][0]["upstream_type"] == "design_case"

    realigned = client.post(f"/tasks/{task['id']}/realign-basis")
    assert realigned.status_code == 200
    assert realigned.get_json()["suspect"] is False


def test_task_list_and_design_list_carry_the_flag(client):
    project = _project(client)
    feature = _feature(client, project["id"])
    design = _design(client, project["id"], feature["id"])
    _confirm_design(client, design["id"])
    _task(client, design["id"])
    client.patch(f"/features/{feature['id']}", json={"name": "改了"})

    designs = client.get(f"/projects/{project['id']}/design-cases").get_json()
    assert [d["suspect"] for d in designs] == [True]
    tasks = client.get(f"/projects/{project['id']}/tasks").get_json()
    assert [t["suspect"] for t in tasks] == [False]


def test_project_context_reports_suspect_to_agents(client):
    project = _project(client)
    feature = _feature(client, project["id"])
    design = _design(client, project["id"], feature["id"])
    _confirm_design(client, design["id"])
    client.patch(f"/features/{feature['id']}", json={"name": "改了"})

    context = client.post(
        "/skills/project-context",
        json={"project_id": project["id"], "project_number": project["project_number"]},
    ).get_json()
    assert context["design_cases"][0]["suspect"] is True
    assert "suspect_guidance" in context
    assert "1 of them suspect" in context["change_summary"]


def test_realign_is_reachable_from_the_skill_surface(client):
    project = _project(client)
    feature = _feature(client, project["id"])
    design = _design(client, project["id"], feature["id"])
    _confirm_design(client, design["id"])
    client.patch(f"/features/{feature['id']}", json={"name": "改了"})

    response = client.post(
        "/skills/design-case-realign-basis",
        json={
            "project_number": project["project_number"],
            "payload": {"design_number": design["design_number"]},
        },
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["status"] == "succeeded"
    assert _get_design(client, design["id"])["suspect"] is False
