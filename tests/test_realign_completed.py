"""Realigning a suspect edge on work that has already shipped.

For an unclaimed or in-flight task, realigning judges a DESCRIPTION. For a
``completed`` one it asserts that MERGED code still satisfies a design that has
since changed — nothing re-runs, no verdict is recorded, and the act is one
call. A live project cleared such an edge 38 seconds after the design changed,
leaving a record in which "we checked" and "we clicked" are the same thing.
"""
from tests.test_change_requests import (
    _confirm_design,
    _design,
    _feature,
    _project,
    _task,
)
from tests.test_design_cases import CONFIRM_FIELDS


def _claimable_task(client):
    project = _project(client)
    feature = _feature(client, project["id"])
    design = _design(client, project["id"], feature["id"])
    _confirm_design(client, design["id"])
    return project, design, _task(client, design["id"])


def test_an_unshipped_task_realigns_with_no_reason(client):
    """The cheap case stays cheap: nothing has been built, so the judgement is
    about a description and demanding a written reason would be ceremony."""
    _, design, task = _claimable_task(client)
    client.post(f"/design-cases/{design['id']}/reopen")
    client.patch(f"/design-cases/{design['id']}", json={"risks": "改了风险描述。"})
    client.post(f"/design-cases/{design['id']}/confirm")

    response = client.post(f"/tasks/{task['id']}/realign-basis", json={})
    assert response.status_code == 200


def test_a_completed_task_refuses_to_realign_without_a_reason(client):
    _, design, task = _claimable_task(client)
    _complete(client, design, task)

    response = client.post(f"/tasks/{task['id']}/realign-basis", json={})
    assert response.status_code == 400
    assert "reason" in response.get_json()["error"].lower()


def test_a_completed_task_realigns_with_a_reason_and_records_it(client):
    """Stating it does not make the claim true — it makes it attributable, and
    puts the cost of asserting it above zero."""
    _, design, task = _claimable_task(client)
    _complete(client, design, task)

    reason = "改动只是措辞澄清，交付的行为逐条对过没有变化。"
    response = client.post(
        f"/tasks/{task['id']}/realign-basis", json={"reason": reason}
    )
    assert response.status_code == 200

    timeline = client.get(f"/tasks/{task['id']}/timeline").get_json()
    assert reason in str(timeline)


def test_the_reason_requirement_is_not_route_only(client):
    """The rule lives in the service, so the business-number route an agent
    uses enforces it too — a gate only one caller passes through is not one."""
    project, design, task = _claimable_task(client)
    _complete(client, design, task)

    response = client.post(
        f"/projects/{project['project_number']}/tasks/{task['task_number']}/realign-basis",
        json={},
    )
    assert response.status_code == 400


def _complete(client, design, task):
    """Walk a task all the way to completed, then revise its design."""
    client.post(f"/tasks/{task['id']}/claim", json={"assignee": "alice"})
    client.post(f"/tasks/{task['id']}/approve-pre-review", json={"reviewer": "alice"})
    client.post(f"/tasks/{task['id']}/start-coding")
    run = {
        "design_number": design["design_number"],
        "criteria_verdicts": [
            {"criterion": c, "verdict": "pass", "evidence": "e"}
            for c in _criteria(client, design)
        ],
        "test_run": {
            "command": "pytest", "exit_code": 0,
            "collected": 1, "passed": 1, "failed": 0, "skipped": 0,
        },
    }
    client.post(
        f"/tasks/{task['id']}/agent-runs",
        json={"role": "coder", "status": "passed", "summary": "done"},
    )
    client.post(
        f"/tasks/{task['id']}/agent-runs",
        json={"role": "qa", "status": "passed", "summary": "ok"} | run,
    )
    client.post(f"/tasks/{task['id']}/mark-ready-for-pr")
    client.post(
        f"/tasks/{task['id']}/link-pr",
        json={"branch_name": "b", "pr_number": 1, "pr_url": "http://x/1"},
    )
    client.post(f"/tasks/{task['id']}/mark-pr-merged")
    completed = client.post(f"/tasks/{task['id']}/mark-completed")
    assert completed.status_code == 200, completed.get_json()

    # Now move the design under it — this is what makes the edge suspect.
    client.post(f"/design-cases/{design['id']}/reopen")
    client.patch(f"/design-cases/{design['id']}", json={"risks": "改了风险描述。"})
    client.post(f"/design-cases/{design['id']}/confirm")


def _criteria(client, design):
    from app.services import acceptance_criteria

    return acceptance_criteria.split(CONFIRM_FIELDS["acceptance_criteria"])
