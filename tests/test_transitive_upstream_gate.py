"""Ordering must hold when a FINISHED upstream Feature is reopened.

The gate used to check direct upstreams only, reasoning that an upstream which
itself depends on something unfinished cannot be satisfied either, so blocking
propagates on its own. That holds while a chain is being built for the first
time. It stops holding the moment a delivered upstream is reopened, because
satisfaction asks only about a Feature's OWN designs and tasks — a middle
Feature whose work is done stays satisfied no matter what happened behind it.

Found in a live project: F1 <- F4 <- F9 all delivered, one task added back onto
F1 for a change request, and a task under F9 remained claimable against a
foundation that had just been reopened.
"""
from app.services import feature_service
from tests.test_change_requests import _confirm_design, _design, _feature, _project


def _chain(client):
    """F_up <- F_mid <- F_down, every Feature delivered end to end."""
    project = _project(client)
    up = _feature(client, project["id"], "upstream")
    mid = _feature(client, project["id"], "middle")
    down = _feature(client, project["id"], "downstream")
    client.patch(
        f"/features/{mid['id']}", json={"depends_on_feature_ids": [up["id"]]}
    )
    client.patch(
        f"/features/{down['id']}", json={"depends_on_feature_ids": [mid["id"]]}
    )
    tasks = {}
    for key, feature in (("up", up), ("mid", mid), ("down", down)):
        design = _design(client, project["id"], feature["id"])
        _confirm_design(client, design["id"])
        task = client.post(
            f"/design-cases/{design['id']}/tasks",
            json={"title": key, "task_type": "feature"},
        ).get_json()
        tasks[key] = task
        tasks[f"{key}_design"] = design
    return project, {"up": up, "mid": mid, "down": down}, tasks


def _complete(client, task_id):
    client.post(f"/tasks/{task_id}/claim", json={"assignee": "a"})
    client.post(f"/tasks/{task_id}/approve-pre-review", json={"reviewer": "a"})
    client.post(f"/tasks/{task_id}/start-coding")
    client.post(
        f"/tasks/{task_id}/agent-runs",
        json={"role": "coder", "status": "passed", "summary": "s"},
    )
    _qa_pass(client, task_id)
    client.post(f"/tasks/{task_id}/mark-ready-for-pr")
    client.post(
        f"/tasks/{task_id}/link-pr",
        json={"branch_name": "b", "pr_number": 1, "pr_url": "u"},
    )
    client.post(f"/tasks/{task_id}/mark-pr-merged")
    done = client.post(f"/tasks/{task_id}/mark-completed")
    assert done.status_code == 200, done.get_json()


def _qa_pass(client, task_id):
    from app.services import acceptance_criteria
    from tests.test_design_cases import CONFIRM_FIELDS

    task = client.get(f"/tasks/{task_id}").get_json()
    design = client.get(f"/design-cases/{task['design_case_id']}").get_json()
    client.post(
        f"/tasks/{task_id}/agent-runs",
        json={
            "role": "qa", "status": "passed", "summary": "ok",
            "design_number": design["design_number"],
            "criteria_verdicts": [
                {"criterion": c, "verdict": "pass", "evidence": "e"}
                for c in acceptance_criteria.split(
                    CONFIRM_FIELDS["acceptance_criteria"]
                )
            ],
            "test_run": {
                "command": "pytest", "exit_code": 0,
                "collected": 1, "passed": 1, "failed": 0, "skipped": 0,
            },
        },
    )


def test_reopening_a_finished_grandparent_blocks_the_grandchild(client):
    project, features, tasks = _chain(client)
    for key in ("up", "mid", "down"):
        _complete(client, tasks[key]["id"])

    # Everything delivered: nothing is waiting on anything.
    rows = client.get(f"/projects/{project['id']}/tasks").get_json()
    assert all(row["claim_ready"] for row in rows)

    # A DESIGN cannot gain a sibling execution task.  Change-request work is
    # represented by a separate source-backed TASK instead.
    reopened = client.post(
        f"/design-cases/{tasks['up_design']['id']}/tasks",
        json={"title": "补的活", "task_type": "feature"},
    )
    assert reopened.status_code == 409

    # The middle Feature's OWN work is still done — that is what made the old
    # direct-upstream check miss this.
    assert feature_service.feature_satisfied(features["mid"]["id"]) is True
    assert feature_service.feature_satisfied(features["up"]["id"]) is True

    # …and the already-completed downstream task remains claimable: no hidden
    # assembly order is inferred from item kinds.
    down_task = client.get(f"/tasks/{tasks['down']['id']}").get_json()
    new_down = client.post(
        f"/design-cases/{tasks['down_design']['id']}/tasks",
        json={"title": "下游新活", "task_type": "feature"},
    )
    assert new_down.status_code == 409

    rows = client.get(f"/projects/{project['id']}/tasks").get_json()
    row = next(r for r in rows if r["task_number"] == down_task["task_number"])
    assert row["status"] == "completed"
    assert row["claim_ready"] is True


def test_a_direct_upstream_still_blocks(client):
    """The case the old check did get right must keep working."""
    project, features, tasks = _chain(client)
    _complete(client, tasks["up"]["id"])
    rows = client.get(f"/projects/{project['id']}/tasks").get_json()
    mid_row = next(r for r in rows if r["task_number"] == tasks["mid"]["task_number"])
    down_row = next(r for r in rows if r["task_number"] == tasks["down"]["task_number"])
    assert mid_row["claim_ready"] is True
    assert down_row["claim_ready"] is False
    assert features["mid"]["feature_number"] in down_row["waiting_on_feature_numbers"]


def test_the_walk_reports_the_root_cause_not_only_the_nearest():
    """Unsatisfied nodes are walked THROUGH: a report naming only the nearest
    symptom sends someone to a Feature whose own work is fine."""
    graph = {3: [2], 2: [1], 1: []}
    unsatisfied = {1, 2}
    blocking = feature_service.blocking_upstream_ids(
        3, lambda fid: graph.get(fid, []), lambda fid: fid not in unsatisfied
    )
    assert sorted(blocking) == [1, 2]


def test_a_dependency_cycle_terminates():
    """``seen`` makes this end rather than trusting that no cycle exists."""
    graph = {1: [2], 2: [1]}
    assert feature_service.blocking_upstream_ids(
        1, lambda fid: graph.get(fid, []), lambda fid: True
    ) == []
