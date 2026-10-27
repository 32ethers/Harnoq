"""A criterion that broke while the suite was green, and was fixed with nothing
added to the suite.

Seen in a live task: qa judged 2 fail + 2 deviates on a green suite (391
collected / 389 passed / 0 failed), coder reworked, and the second qa run
reported all 29 pass with the identical 391 collected. The suite that had not
noticed the four problems still could not notice them — the repair rested
entirely on one qa agent's reading, and nothing in the record said so.
"""
from tests.test_agent_runs import _test_run
from tests.test_development_task_lifecycle import _claimable_task
from app.repositories import design_case_repo
from app.services import acceptance_criteria, development_task_service


def _in_progress(client):
    task = _claimable_task(client)
    client.post(f"/tasks/{task['id']}/claim", json={"assignee": "m"})
    client.post(f"/tasks/{task['id']}/approve-pre-review", json={"reviewer": "r"})
    client.post(f"/tasks/{task['id']}/start-coding")
    with client.application.app_context():
        design = design_case_repo.get(task["design_case_id"])
        criteria = acceptance_criteria.split(design.acceptance_criteria)
    return task, design.design_number, criteria


def _qa(client, task, design_number, criteria, verdicts, **run):
    """One qa run: ``verdicts`` maps criterion index -> verdict, rest pass."""
    return client.post(
        f"/tasks/{task['id']}/agent-runs",
        json={
            "role": "qa",
            "status": "needs_fix" if set(verdicts.values()) - {"pass"} else "passed",
            "summary": "qa",
            "design_number": design_number,
            "criteria_verdicts": [
                {"criterion": c, "verdict": verdicts.get(i, "pass"), "evidence": "e"}
                for i, c in enumerate(criteria)
            ],
            "test_run": _test_run(**run),
        },
    )


def _repairs(client, task):
    with client.application.app_context():
        return development_task_service.cleared_failures(task["id"])


def test_a_repair_with_no_new_test_is_named(client):
    task, number, criteria = _in_progress(client)
    _qa(client, task, number, criteria, {0: "fail"}, collected=10, passed=9)
    _qa(client, task, number, criteria, {}, collected=10, passed=10)

    found = _repairs(client, task)
    assert [f["criterion"] for f in found] == [criteria[0]]
    assert found[0]["was"] == "fail"
    assert found[0]["collected_when_broken"] == 10
    assert found[0]["collected_when_fixed"] == 10


def test_deviates_counts_the_same_way(client):
    """It is a divergence the suite also failed to notice."""
    task, number, criteria = _in_progress(client)
    _qa(client, task, number, criteria, {0: "deviates"}, collected=10, passed=9)
    _qa(client, task, number, criteria, {}, collected=10, passed=10)

    assert [f["was"] for f in _repairs(client, task)] == ["deviates"]


def test_a_repair_that_added_a_test_is_not_named(client):
    task, number, criteria = _in_progress(client)
    _qa(client, task, number, criteria, {0: "fail"}, collected=10, passed=9)
    _qa(client, task, number, criteria, {}, collected=11, passed=11)

    assert _repairs(client, task) == []


def test_a_failure_on_a_RED_suite_is_left_alone(client):
    """The red test might have been that criterion's own. Nothing here can tell
    which test failed, so it is not asserted either way."""
    task, number, criteria = _in_progress(client)
    _qa(client, task, number, criteria, {0: "fail"},
        collected=10, passed=8, failed=2, exit_code=1)
    _qa(client, task, number, criteria, {}, collected=10, passed=10)

    assert _repairs(client, task) == []


def test_a_criterion_that_never_passed_is_not_a_repair(client):
    task, number, criteria = _in_progress(client)
    _qa(client, task, number, criteria, {0: "fail"}, collected=10, passed=9)
    _qa(client, task, number, criteria, {0: "fail"}, collected=10, passed=9)

    assert _repairs(client, task) == []


def test_a_clean_task_names_nothing(client):
    task, number, criteria = _in_progress(client)
    _qa(client, task, number, criteria, {}, collected=10, passed=10)

    assert _repairs(client, task) == []


def test_runs_from_before_the_test_run_column_are_not_guessed_at(client):
    """Older runs carry no counts. An absent number is not a small number."""
    task, number, criteria = _in_progress(client)
    _qa(client, task, number, criteria, {0: "fail"}, collected=10, passed=9)
    with client.application.app_context():
        from app.repositories import agent_run_repo

        run = agent_run_repo.latest_for_role(task["id"], "qa")
        run.test_run = ""
        run.save()
    _qa(client, task, number, criteria, {}, collected=10, passed=10)

    assert _repairs(client, task) == []


# --- the other way a failure clears: the criterion leaves ------------------

def _rewrite_criteria(client, design_id, text):
    """Reopen, replace the acceptance criteria, re-confirm — what a design
    revision mid-task actually looks like."""
    client.post(f"/design-cases/{design_id}/reopen")
    client.patch(f"/design-cases/{design_id}", json={"acceptance_criteria": text})
    client.post(f"/design-cases/{design_id}/confirm")


def test_a_criterion_deleted_after_it_failed_is_named(client):
    """What a live task did: qa failed four criteria, the design was rewritten
    to drop the capability they described, and the next qa run passed a set
    that no longer contained them. Nothing in the task said the failures went
    away because the question was withdrawn."""
    task, number, criteria = _in_progress(client)
    _qa(client, task, number, criteria, {0: "fail"}, collected=10, passed=9)
    _rewrite_criteria(client, task["design_case_id"], "- 完全不同的一条判据。")

    with client.application.app_context():
        new = acceptance_criteria.split(
            design_case_repo.get(task["design_case_id"]).acceptance_criteria
        )
    _qa(client, task, number, new, {}, collected=10, passed=10)

    found = _repairs(client, task)
    assert [f["how"] for f in found] == ["criterion_removed"]
    assert found[0]["criterion"] == criteria[0]
    assert found[0]["was"] == "fail"
    assert found[0]["collected_when_fixed"] is None


def test_a_criterion_still_in_the_design_is_not_reported_as_removed(client):
    """It was simply not re-judged. Mid-round, that answer is not in yet — and
    the qa gate already refuses a run that skips a live criterion."""
    task, number, criteria = _in_progress(client)
    _qa(client, task, number, criteria, {0: "fail"}, collected=10, passed=9)

    assert _repairs(client, task) == []


def test_the_timeline_says_the_criterion_was_removed(client):
    task, number, criteria = _in_progress(client)
    _qa(client, task, number, criteria, {0: "fail"}, collected=10, passed=9)
    _rewrite_criteria(client, task["design_case_id"], "- 完全不同的一条判据。")
    with client.application.app_context():
        new = acceptance_criteria.split(
            design_case_repo.get(task["design_case_id"]).acceptance_criteria
        )
    _qa(client, task, number, new, {}, collected=10, passed=10)

    runs = [
        e for e in client.get(f"/tasks/{task['id']}/timeline").get_json()
        if e["kind"] == "agent_run"
    ]
    assert "removed, not resolved" in runs[-1]["detail"]


# --- it has to reach somebody -----------------------------------------------

def test_the_task_detail_carries_it(client):
    task, number, criteria = _in_progress(client)
    _qa(client, task, number, criteria, {0: "fail"}, collected=10, passed=9)
    _qa(client, task, number, criteria, {}, collected=10, passed=10)

    detail = client.get(f"/tasks/{task['id']}").get_json()
    assert [f["criterion"] for f in detail["cleared_failures"]] == [criteria[0]]


def test_the_agent_facing_read_carries_it(client):
    """Manager decides whether a delivery is believable, and it reads the agent
    route — a derivation only the browser sees reaches nobody who acts on it."""
    task, number, criteria = _in_progress(client)
    _qa(client, task, number, criteria, {0: "fail"}, collected=10, passed=9)
    _qa(client, task, number, criteria, {}, collected=10, passed=10)

    project = client.get(f"/projects/{task['project_id']}").get_json()
    payload = client.get(
        f"/projects/{project['project_number']}/tasks/{task['task_number']}"
    ).get_json()
    assert [f["criterion"] for f in payload["cleared_failures"]] == [criteria[0]]


def test_the_timeline_says_it_on_the_run_that_declared_the_pass(client):
    task, number, criteria = _in_progress(client)
    _qa(client, task, number, criteria, {0: "fail"}, collected=10, passed=9)
    _qa(client, task, number, criteria, {}, collected=10, passed=10)

    runs = [
        e for e in client.get(f"/tasks/{task['id']}/timeline").get_json()
        if e["kind"] == "agent_run"
    ]
    assert "without added tests" in runs[-1]["detail"]
    assert "without added tests" not in runs[0]["detail"]


def test_the_gate_still_opens(client):
    """Named, not blocked: the cheapest way to satisfy a hard gate here is a
    test written to match whatever the code now does."""
    task, number, criteria = _in_progress(client)
    _qa(client, task, number, criteria, {0: "fail"}, collected=10, passed=9)
    client.post(
        f"/tasks/{task['id']}/agent-runs",
        json={"role": "coder", "status": "passed", "summary": "fixed"},
    )
    _qa(client, task, number, criteria, {}, collected=10, passed=10)

    assert client.post(f"/tasks/{task['id']}/mark-ready-for-pr").status_code == 200
