"""Reading a design should show WHY it says what it says.

Traceability ran one way: a request recorded which designs it changed, a
design had no idea it had ever been changed. And ``apply`` — which asserts the
change was made upstream — checked nothing, so a request could be applied and
closed against a design whose version never moved once.
"""
from tests.test_change_requests import (
    _confirm_design,
    _cr,
    _design,
    _feature,
    _project,
    _task,
)


def _design_with_task(client):
    project = _project(client)
    feature = _feature(client, project["id"])
    design = _design(client, project["id"], feature["id"])
    _confirm_design(client, design["id"])
    return project, design, _task(client, design["id"])


def _revise(client, design_id, text="改后的说法。"):
    """A real edit: reopen, change, re-confirm — the lifecycle gate is separate
    from the change request and still applies."""
    client.post(f"/design-cases/{design_id}/reopen")
    client.patch(f"/design-cases/{design_id}", json={"problem_statement": text})
    client.post(f"/design-cases/{design_id}/confirm")


# --- reverse traceability --------------------------------------------------

def test_a_design_lists_the_requests_that_rewrote_it(client):
    project, design, _ = _design_with_task(client)
    cr = _cr(
        client, project["id"], target_type="design_case", target_id=design["id"],
        reason="需求方改了主意",
    ).get_json()

    rows = client.get(f"/design-cases/{design['id']}/change-requests").get_json()
    assert [r["change_request_number"] for r in rows] == [cr["change_request_number"]]
    assert rows[0]["reason"] == "需求方改了主意"


def test_a_secondary_target_is_listed_too(client):
    """The one that was invisible: a design changed as part of someone else's
    request. Matching only the primary target would leave it looking untouched."""
    project, primary, _ = _design_with_task(client)
    secondary = _design(client, project["id"], primary["feature_id"])
    _confirm_design(client, secondary["id"])

    cr = _cr(
        client, project["id"], target_type="design_case", target_id=primary["id"],
        reason="一个决定改两个设计",
        also_changes=[{"target_type": "design_case", "target_id": secondary["id"]}],
    ).get_json()

    rows = client.get(f"/design-cases/{secondary['id']}/change-requests").get_json()
    assert [r["change_request_number"] for r in rows] == [cr["change_request_number"]]


def test_an_untouched_design_lists_nothing(client):
    project, design, _ = _design_with_task(client)
    other = _design(client, project["id"], design["feature_id"])
    _cr(
        client, project["id"], target_type="design_case", target_id=design["id"],
        reason="r",
    )
    assert client.get(f"/design-cases/{other['id']}/change-requests").get_json() == []


def test_a_task_lists_both_its_own_and_its_designs_requests(client):
    """A request against the design is what changed this task's specification,
    so a list without those answers the wrong question."""
    project, design, task = _design_with_task(client)
    on_design = _cr(
        client, project["id"], target_type="design_case", target_id=design["id"],
        reason="规格改了",
    ).get_json()
    on_task = _cr(
        client, project["id"], target_type="development_task", target_id=task["id"],
        reason="已交付代码要修",
    ).get_json()

    rows = client.get(f"/tasks/{task['id']}/change-requests").get_json()
    assert sorted(r["change_request_number"] for r in rows) == sorted(
        [on_design["change_request_number"], on_task["change_request_number"]]
    )


# --- apply now checks that something moved ---------------------------------

def test_apply_is_refused_when_no_target_ever_moved(client):
    """``applied`` asserts the change was made upstream. A live request said so
    while its target design sat at the version it started at."""
    project, design, _ = _design_with_task(client)
    cr = _cr(
        client, project["id"], target_type="design_case", target_id=design["id"],
        reason="说要改",
    ).get_json()
    client.post(f"/change-requests/{cr['id']}/analyze")
    client.post(f"/change-requests/{cr['id']}/approve")

    refused = client.post(f"/change-requests/{cr['id']}/apply")
    assert refused.status_code == 409
    assert "version" in refused.get_json()["error"]


def test_apply_succeeds_once_the_target_is_actually_revised(client):
    project, design, _ = _design_with_task(client)
    cr = _cr(
        client, project["id"], target_type="design_case", target_id=design["id"],
        reason="说要改",
    ).get_json()
    client.post(f"/change-requests/{cr['id']}/analyze")
    client.post(f"/change-requests/{cr['id']}/approve")
    _revise(client, design["id"])

    applied = client.post(f"/change-requests/{cr['id']}/apply")
    assert applied.status_code == 200
    assert applied.get_json()["status"] == "applied"


def test_a_partly_applied_change_is_allowed_and_reported(client):
    """A request may legitimately conclude one target needed no edit after all.
    Blocking that would push people to file a second request for work they
    correctly did not do — so it is reported, not refused."""
    project, primary, _ = _design_with_task(client)
    secondary = _design(client, project["id"], primary["feature_id"])
    _confirm_design(client, secondary["id"])
    cr = _cr(
        client, project["id"], target_type="design_case", target_id=primary["id"],
        reason="两个目标，只有一个真的需要改",
        also_changes=[{"target_type": "design_case", "target_id": secondary["id"]}],
    ).get_json()
    client.post(f"/change-requests/{cr['id']}/analyze")
    client.post(f"/change-requests/{cr['id']}/approve")
    _revise(client, primary["id"])

    applied = client.post(f"/change-requests/{cr['id']}/apply")
    assert applied.status_code == 200

    stored = client.get(f"/change-requests/{cr['id']}").get_json()
    unchanged = [
        entry["target_id"] for entry in stored["target_versions"]
    ]
    assert secondary["id"] in unchanged  # it was recorded as a target
    from app.services import change_request_service

    with client.application.app_context():
        left = change_request_service.unchanged_targets(
            change_request_service.get(cr["id"])
        )
    assert [entry["target_id"] for entry in left] == [secondary["id"]]


def test_a_delivered_task_target_does_not_block_apply(client):
    """A task carries no version, so it can never be shown to have moved.
    Counting it as unchanged would block exactly the code-only change the task
    target type exists for."""
    project, design, task = _design_with_task(client)
    cr = _cr(
        client, project["id"], target_type="development_task", target_id=task["id"],
        reason="已交付代码要修，规格不动",
    ).get_json()
    client.post(f"/change-requests/{cr['id']}/analyze")
    client.post(f"/change-requests/{cr['id']}/approve")

    applied = client.post(f"/change-requests/{cr['id']}/apply")
    assert applied.status_code == 200
