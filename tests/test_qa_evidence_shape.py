"""Structured qa evidence (``{tests, code, note}``) and the criteria it
leaves uncovered.

See docs/cn/design-evolution.md §13: re-verifying an unchanged design
used to cost most of its time relocating, per criterion, "where is this
satisfied and what tests watch it" — an answer already written down the
previous round as prose that could not be read back. Structured evidence
makes that one fact queryable instead of re-derived from scratch; these tests
exercise the shape gate (``_qa_evidence_value``) and the derived view built on
top of it (``uncovered_criteria``), not merely that the prompt asks for the
shape — that is covered separately in ``test_agent_templates.py``.
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


def _rewrite_criteria(client, design_id, text):
    """Reopen, replace the acceptance criteria, re-confirm — the only way a
    task ends up with more than the fixture's single default criterion."""
    client.post(f"/design-cases/{design_id}/reopen")
    client.patch(f"/design-cases/{design_id}", json={"acceptance_criteria": text})
    client.post(f"/design-cases/{design_id}/confirm")


def _two_criteria_task(client):
    task, number, _ = _in_progress(client)
    _rewrite_criteria(client, task["design_case_id"], "- 第一条判据。\n- 第二条判据。")
    with client.application.app_context():
        criteria = acceptance_criteria.split(
            design_case_repo.get(task["design_case_id"]).acceptance_criteria
        )
    return task, number, criteria


def _qa(client, task, design_number, verdicts, **run):
    """One qa run built from explicit ``{criterion, verdict, evidence}`` entries."""
    status = "passed" if all(v["verdict"] == "pass" for v in verdicts) else "needs_fix"
    return client.post(
        f"/tasks/{task['id']}/agent-runs",
        json={
            "role": "qa",
            "status": status,
            "summary": "qa",
            "design_number": design_number,
            "criteria_verdicts": verdicts,
            "test_run": _test_run(**run),
        },
    )


# --- the evidence shape gate: string (legacy) or {tests, code, note} -------


def test_structured_evidence_with_tests_and_code_is_stored(client):
    task, number, criteria = _in_progress(client)
    evidence = {"tests": ["test_login_ok"], "code": ["auth.py:10-20"], "note": ""}
    response = _qa(client, task, number, [
        {"criterion": criteria[0], "verdict": "pass", "evidence": evidence}
    ])
    assert response.status_code == 200
    stored = response.get_json()["agent_run"]["criteria_verdicts"][0]["evidence"]
    assert stored == evidence


def test_structured_evidence_with_both_lists_empty_needs_a_note(client):
    task, number, criteria = _in_progress(client)
    response = _qa(client, task, number, [
        {"criterion": criteria[0], "verdict": "pass",
         "evidence": {"tests": [], "code": [], "note": ""}}
    ])
    assert response.status_code == 400
    assert "evidence" in response.get_json()["error"]


def test_structured_evidence_with_empty_lists_but_a_note_is_accepted(client):
    """An empty ``tests`` list is a real, useful answer on its own — it must
    be rejected only when NOTHING at all was said, not merely for being
    empty."""
    task, number, criteria = _in_progress(client)
    response = _qa(client, task, number, [
        {"criterion": criteria[0], "verdict": "pass",
         "evidence": {
             "tests": [], "code": [],
             "note": "read the module by hand; no automated test exists",
         }}
    ])
    assert response.status_code == 200
    stored = response.get_json()["agent_run"]["criteria_verdicts"][0]["evidence"]
    assert stored["tests"] == [] and stored["code"] == []
    assert stored["note"]


def test_structured_evidence_missing_a_key_is_rejected(client):
    task, number, criteria = _in_progress(client)
    response = _qa(client, task, number, [
        {"criterion": criteria[0], "verdict": "pass",
         "evidence": {"tests": [], "note": "checked"}}  # no "code" key at all
    ])
    assert response.status_code == 400
    assert "code" in response.get_json()["error"]


def test_structured_evidence_tests_must_be_a_list_of_strings(client):
    task, number, criteria = _in_progress(client)
    response = _qa(client, task, number, [
        {"criterion": criteria[0], "verdict": "pass",
         "evidence": {"tests": "test_login_ok", "code": [], "note": "x"}}
    ])
    assert response.status_code == 400
    assert "evidence.tests" in response.get_json()["error"]


def test_evidence_that_is_neither_string_nor_dict_is_rejected(client):
    task, number, criteria = _in_progress(client)
    response = _qa(client, task, number, [
        {"criterion": criteria[0], "verdict": "pass", "evidence": 12345}
    ])
    assert response.status_code == 400
    assert "evidence" in response.get_json()["error"]


def test_legacy_string_evidence_still_stores_verbatim(client):
    """Backward compatibility: 200+ historical rows are plain strings, never
    migrated — the gate must keep accepting the old shape unchanged."""
    task, number, criteria = _in_progress(client)
    response = _qa(client, task, number, [
        {"criterion": criteria[0], "verdict": "pass", "evidence": "ran the suite by hand"}
    ])
    assert response.status_code == 200
    assert response.get_json()["agent_run"]["criteria_verdicts"][0]["evidence"] == (
        "ran the suite by hand"
    )


# --- uncovered_criteria: zero-test-coverage criteria from the latest qa run -


def _uncovered(client, task):
    with client.application.app_context():
        return development_task_service.uncovered_criteria(task["id"])


def test_a_criterion_with_no_tests_is_named(client):
    task, number, criteria = _two_criteria_task(client)
    _qa(client, task, number, [
        {"criterion": criteria[0], "verdict": "pass",
         "evidence": {"tests": ["test_a"], "code": [], "note": ""}},
        {"criterion": criteria[1], "verdict": "pass",
         "evidence": {"tests": [], "code": ["mod.py:1-2"], "note": ""}},
    ])
    found = _uncovered(client, task)
    assert [f["criterion"] for f in found] == [criteria[1]]


def test_a_criterion_with_tests_is_not_named(client):
    task, number, criteria = _two_criteria_task(client)
    _qa(client, task, number, [
        {"criterion": criteria[0], "verdict": "pass",
         "evidence": {"tests": ["test_a"], "code": [], "note": ""}},
        {"criterion": criteria[1], "verdict": "pass",
         "evidence": {"tests": ["test_b"], "code": [], "note": ""}},
    ])
    assert _uncovered(client, task) == []


def test_legacy_string_evidence_is_unknown_not_zero_coverage(client):
    """A criterion whose evidence is prose must not be counted as uncovered —
    prose cannot be split into a tests/code answer, so it is absent from this
    list entirely rather than folded into either side of it."""
    task, number, criteria = _two_criteria_task(client)
    _qa(client, task, number, [
        {"criterion": criteria[0], "verdict": "pass", "evidence": "checked by hand"},
        {"criterion": criteria[1], "verdict": "pass",
         "evidence": {"tests": ["test_b"], "code": [], "note": ""}},
    ])
    assert _uncovered(client, task) == []


def test_only_the_latest_qa_run_is_read(client):
    """A criterion uncovered in an earlier round must not linger once a later
    round reports a test that now covers it."""
    task, number, criteria = _two_criteria_task(client)
    _qa(client, task, number, [
        {"criterion": criteria[0], "verdict": "fail",
         "evidence": {"tests": [], "code": [], "note": "not implemented yet"}},
        {"criterion": criteria[1], "verdict": "pass",
         "evidence": {"tests": ["test_b"], "code": [], "note": ""}},
    ])
    client.post(f"/tasks/{task['id']}/agent-runs", json={
        "role": "coder", "status": "passed", "summary": "fixed",
    })
    _qa(client, task, number, [
        {"criterion": criteria[0], "verdict": "pass",
         "evidence": {"tests": ["test_a"], "code": [], "note": ""}},
        {"criterion": criteria[1], "verdict": "pass",
         "evidence": {"tests": ["test_b"], "code": [], "note": ""}},
    ])
    assert _uncovered(client, task) == []


def test_a_blocked_run_after_a_verdicted_one_is_skipped(client):
    """``blocked`` carries no ``criteria_verdicts``; the derivation must fall
    back to the last run that actually reached a verdict."""
    task, number, criteria = _two_criteria_task(client)
    _qa(client, task, number, [
        {"criterion": criteria[0], "verdict": "pass",
         "evidence": {"tests": [], "code": [], "note": "code review only"}},
        {"criterion": criteria[1], "verdict": "pass",
         "evidence": {"tests": ["test_b"], "code": [], "note": ""}},
    ])
    client.post(f"/tasks/{task['id']}/agent-runs", json={
        "role": "qa", "status": "blocked", "summary": "harnoq unreachable",
    })
    found = _uncovered(client, task)
    assert [f["criterion"] for f in found] == [criteria[0]]


def test_no_qa_run_yet_is_an_empty_list(client):
    task, number, criteria = _two_criteria_task(client)
    assert _uncovered(client, task) == []


# --- it has to reach somebody -----------------------------------------------


def test_the_task_detail_carries_it(client):
    task, number, criteria = _two_criteria_task(client)
    _qa(client, task, number, [
        {"criterion": criteria[0], "verdict": "pass",
         "evidence": {"tests": [], "code": [], "note": "code review only"}},
        {"criterion": criteria[1], "verdict": "pass",
         "evidence": {"tests": ["test_b"], "code": [], "note": ""}},
    ])
    detail = client.get(f"/tasks/{task['id']}").get_json()
    assert [f["criterion"] for f in detail["uncovered_criteria"]] == [criteria[0]]


def test_the_agent_facing_read_carries_it(client):
    """Manager decides whether a delivery is believable from the agent route —
    a derivation only the browser sees reaches nobody who acts on it."""
    task, number, criteria = _two_criteria_task(client)
    _qa(client, task, number, [
        {"criterion": criteria[0], "verdict": "pass",
         "evidence": {"tests": [], "code": [], "note": "code review only"}},
        {"criterion": criteria[1], "verdict": "pass",
         "evidence": {"tests": ["test_b"], "code": [], "note": ""}},
    ])
    project = client.get(f"/projects/{task['project_id']}").get_json()
    payload = client.get(
        f"/projects/{project['project_number']}/tasks/{task['task_number']}"
    ).get_json()
    assert [f["criterion"] for f in payload["uncovered_criteria"]] == [criteria[0]]
