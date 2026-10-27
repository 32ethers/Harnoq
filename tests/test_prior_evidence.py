"""Handing the previous round's locations forward, without its verdict.

Structured evidence made "which tests cover this, and where is it satisfied"
queryable, but nothing read it back — it was written and never returned, so
every re-verifying qa run still rebuilt the index from scratch. Four
supplementary tasks argued 83 criteria between them, 59 untouched by the
change, and no two rounds wrote the same evidence text twice.
"""
from tests.test_agent_runs import _test_run
from tests.test_development_task_lifecycle import _claimable_task
from app.repositories import design_case_repo, development_task_repo
from app.services import acceptance_criteria, development_task_service


def _structured(tests, code, note="checked"):
    return {"tests": tests, "code": code, "note": note}


def _in_progress(client, task):
    client.post(f"/tasks/{task['id']}/claim", json={"assignee": "m"})
    client.post(f"/tasks/{task['id']}/approve-pre-review", json={"reviewer": "r"})
    client.post(f"/tasks/{task['id']}/start-coding")


def _setup(client):
    task = _claimable_task(client)
    _in_progress(client, task)
    with client.application.app_context():
        design = design_case_repo.get(task["design_case_id"])
        criteria = acceptance_criteria.split(design.acceptance_criteria)
    return task, design.design_number, criteria


def _qa(client, task, number, criteria, evidence, status="passed"):
    return client.post(
        f"/tasks/{task['id']}/agent-runs",
        json={
            "role": "qa", "status": status, "summary": "qa",
            "design_number": number,
            "criteria_verdicts": [
                {"criterion": c, "verdict": "pass", "evidence": evidence}
                for c in criteria
            ],
            "test_run": _test_run(),
        },
    )


def _prior(client, task):
    with client.application.app_context():
        return development_task_service.prior_evidence(task["id"])


def test_the_locations_carry_forward(client):
    task, number, criteria = _setup(client)
    _qa(client, task, number, criteria,
        _structured(["test_login_ok"], ["auth.py:40-58"]))

    found = _prior(client, task)
    assert [f["criterion"] for f in found] == criteria
    assert found[0]["tests"] == ["test_login_ok"]
    assert found[0]["code"] == ["auth.py:40-58"]
    assert found[0]["last_checked_by"] == task["task_number"]


def test_the_verdict_and_the_note_are_deliberately_absent(client):
    """"It passed last time" is the most anchoring fact available and does
    nothing for relocation, which is the only purpose here."""
    task, number, criteria = _setup(client)
    _qa(client, task, number, criteria,
        _structured(["t"], ["m.py:1"], note="全部通过，无问题"))

    entry = _prior(client, task)[0]
    assert set(entry) == {"criterion", "tests", "code", "last_checked_by"}


def test_a_second_task_on_the_same_design_is_rejected(client):
    """A live DESIGN has exactly one execution TASK; work from another
    source uses a source-backed task instead of creating a sibling here."""
    first, number, criteria = _setup(client)
    _qa(client, first, number, criteria,
        _structured(["test_from_first"], ["mod.py:10"]))

    second = client.post(
        f"/design-cases/{first['design_case_id']}/tasks",
        json={"title": "补充", "task_type": "feature"},
    )
    assert second.status_code == 409


def test_the_newest_location_wins(client):
    task, number, criteria = _setup(client)
    _qa(client, task, number, criteria, _structured(["old"], ["a.py:1"]),
        status="passed")
    _qa(client, task, number, criteria, _structured(["new"], ["b.py:2"]),
        status="passed")

    assert _prior(client, task)[0]["tests"] == ["new"]


def test_legacy_string_evidence_is_not_an_index(client):
    """Prose may name a location or may just restate the verdict, and nothing
    here can tell which — so it is not offered as one."""
    task, number, criteria = _setup(client)
    _qa(client, task, number, criteria, "跑了套件，全绿")

    assert _prior(client, task) == []


def test_a_reworded_criterion_has_no_prior_entry(client):
    """Its old locations were found against wording that no longer stands."""
    task, number, criteria = _setup(client)
    _qa(client, task, number, criteria, _structured(["t"], ["m.py:1"]))

    client.post(f"/design-cases/{task['design_case_id']}/reopen")
    client.patch(f"/design-cases/{task['design_case_id']}",
                 json={"acceptance_criteria": "- 完全不同的一条判据。"})
    client.post(f"/design-cases/{task['design_case_id']}/confirm")

    stale = [
        e for e in _prior(client, task)
        if acceptance_criteria.normalize(e["criterion"]) == acceptance_criteria.normalize(criteria[0])
    ]
    assert stale, "上一轮的记录仍在,只是新判据匹配不上它"
    with client.application.app_context():
        now = {
            acceptance_criteria.normalize(c)
            for c in acceptance_criteria.split(
                design_case_repo.get(task["design_case_id"]).acceptance_criteria
            )
        }
    assert acceptance_criteria.normalize(stale[0]["criterion"]) not in now


def test_a_task_with_no_prior_round_gets_nothing(client):
    task, _, _ = _setup(client)
    assert _prior(client, task) == []


def test_it_reaches_the_agent_route(client):
    """A working aid nobody is handed is not a working aid."""
    task, number, criteria = _setup(client)
    _qa(client, task, number, criteria,
        _structured(["test_login_ok"], ["auth.py:40-58"]))

    project = client.get(f"/projects/{task['project_id']}").get_json()
    payload = client.get(
        f"/projects/{project['project_number']}/tasks/{task['task_number']}"
    ).get_json()
    assert payload["prior_evidence"][0]["tests"] == ["test_login_ok"]


def test_it_is_not_on_the_browser_route(client):
    """It is an aid for the run in flight, not a fact about the task worth
    rendering on a page — and the task list would pay for it per row."""
    task, number, criteria = _setup(client)
    _qa(client, task, number, criteria, _structured(["t"], ["m.py:1"]))

    assert "prior_evidence" not in client.get(f"/tasks/{task['id']}").get_json()
