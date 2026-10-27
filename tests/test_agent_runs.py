"""AgentRun state-machine coverage after task-package removal."""
from app.repositories import agent_run_repo, design_case_repo, development_task_repo
from app.services import acceptance_criteria
from tests.test_development_task_lifecycle import _claimable_task


def _test_run(failed=0, exit_code=0, collected=10, passed=10, skipped=0):
    """A green-by-default structured test-suite record (see FIX 3)."""
    return {
        "command": "run the project's test suite",
        "exit_code": exit_code,
        "collected": collected,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
    }


def _qa_evidence(task, verdict="pass", evidence="ran the suite; green"):
    """The per-criterion evidence a qa verdict has to carry.

    Derived from the task's real design so the shape stays honest: if the
    fixture design gains criteria, the evidence grows with it rather than
    silently under-covering. ``test_run`` defaults to a clean run; a caller
    exercising the fail/deviates path passes its own verdict but this stays
    green, matching what a `needs_fix` on criteria-only grounds looks like.
    """
    row = development_task_repo.get(task["id"])
    design = design_case_repo.get(row.design_case_id)
    return {
        "design_number": design.design_number,
        "criteria_verdicts": [
            {"criterion": criterion, "verdict": verdict, "evidence": evidence}
            for criterion in acceptance_criteria.split(design.acceptance_criteria)
        ],
        "test_run": _test_run(),
    }


def _ready_for_development_task(client):
    task = _claimable_task(client)
    client.post(f"/tasks/{task['id']}/claim", json={"assignee": "alice"})
    client.post(f"/tasks/{task['id']}/start-pre-review")
    client.post(f"/tasks/{task['id']}/approve-pre-review", json={"reviewer": "alice"})
    return task


def _start_coding(client, task):
    return client.post(f"/tasks/{task['id']}/start-coding").get_json()


def _agent_run(client, task, role, status, summary="", **extra):
    """Post an agent run, auto-supplying qa evidence when the caller has no
    opinion about it.

    Most callers exercise the loop, not the evidence gate, and would otherwise
    have to restate the design in every test. Passing ``design_number`` or
    ``criteria_verdicts`` explicitly overrides this — that is how the gate's own
    tests drive it.
    """
    payload = {"role": role, "status": status, "summary": summary}
    if role == "qa" and status in {"passed", "needs_fix"} and not extra:
        payload |= _qa_evidence(task, verdict="pass" if status == "passed" else "fail")
    return client.post(f"/tasks/{task['id']}/agent-runs", json=payload | extra)


def test_coder_and_qa_runs_close_the_quality_gate(client):
    task = _ready_for_development_task(client)
    _start_coding(client, task)
    coder = _agent_run(client, task, "coder", "passed", "implemented").get_json()
    # A passing run does not move the task: rounds happen inside the unit.
    assert coder["task"]["status"] == "in_progress"
    qa = _agent_run(client, task, "qa", "passed", "green and reviewed").get_json()
    ready = client.post(f"/tasks/{task['id']}/mark-ready-for-pr").get_json()
    assert ready["status"] == "ready_for_pr"


def test_qa_needs_fix_records_the_blocker_without_moving_the_task(client):
    """Rework is another round inside the same unit, not a state change."""
    task = _ready_for_development_task(client)
    _start_coding(client, task)
    _agent_run(client, task, "coder", "passed")
    result = _agent_run(client, task, "qa", "needs_fix", "missing edge case").get_json()
    assert result["task"]["status"] == "in_progress"
    assert result["task"]["blocking_issues"] == "missing edge case"
    # The gate is shut and coder simply runs again — no re-entry action.
    assert client.post(f"/tasks/{task['id']}/mark-ready-for-pr").status_code == 409
    assert _agent_run(client, task, "coder", "passed", "fixed").status_code == 200
    # start_coding opens the unit once; it is not a per-round action.
    assert client.post(f"/tasks/{task['id']}/start-coding").status_code == 409


def test_run_outside_the_build_unit_does_not_persist(client):
    """Runs are only meaningful once the unit is open.

    Role ORDER inside the unit is no longer gated here — the quality gate
    compares the runs themselves — but a run recorded before the human
    approved the task would be evidence about work nobody authorised.
    """
    task = _ready_for_development_task(client)
    response = _agent_run(client, task, "coder", "passed")
    assert response.status_code == 409
    assert agent_run_repo.list_by_task(task["id"]) == []


def test_either_role_may_run_first_inside_the_unit(client):
    """Sequencing is the manager's business; the gate still refuses a qa run
    that predates the code it judged."""
    task = _ready_for_development_task(client)
    _start_coding(client, task)
    assert _agent_run(client, task, "qa", "passed", "early").status_code == 200
    assert _agent_run(client, task, "coder", "passed").status_code == 200
    refused = client.post(f"/tasks/{task['id']}/mark-ready-for-pr")
    assert refused.status_code == 409
    assert "predates" in refused.get_json()["error"]


# --- blocked: the run never reached a verdict ------------------------------
#
# A qa subagent whose session started while the Harnoq MCP surface was down
# holds no design and can judge nothing. Recording that as needs_fix (TASK-1,
# 2026-07-21) sent coder back to fix an opinion nobody had formed and spent a
# qa round on verification that never ran.


def _in_progress_with_coder_pass(client):
    task = _ready_for_development_task(client)
    _start_coding(client, task)
    _agent_run(client, task, "coder", "passed")
    return task


def test_blocked_qa_run_leaves_the_task_where_it_was(client):
    task = _in_progress_with_coder_pass(client)
    result = _agent_run(
        client, task, "qa", "blocked", "no harnoq_* tools in session"
    ).get_json()
    assert result["agent_run"]["status"] == "blocked"
    assert result["task"]["status"] == "in_progress"


def test_blocked_qa_run_keeps_the_quality_gate_shut(client):
    task = _in_progress_with_coder_pass(client)
    _agent_run(client, task, "qa", "blocked", "design unreachable")
    refused = client.post(f"/tasks/{task['id']}/mark-ready-for-pr")
    assert refused.status_code == 409
    assert "qa" in refused.get_json()["error"]


def test_blocked_qa_run_can_be_retried_in_place_and_then_pass(client):
    task = _in_progress_with_coder_pass(client)
    _agent_run(client, task, "qa", "blocked", "harnoq server was down")
    # The manager fixes the cause and dispatches qa again; no start_coding
    # round-trip, because the code was never the problem.
    assert _agent_run(client, task, "qa", "passed", "verified").status_code == 200
    assert client.post(
        f"/tasks/{task['id']}/mark-ready-for-pr"
    ).get_json()["status"] == "ready_for_pr"


def test_blocked_requires_a_reason(client):
    task = _in_progress_with_coder_pass(client)
    response = _agent_run(client, task, "qa", "blocked", "")
    assert response.status_code == 400
    # Only the coder run from the fixture; a reasonless blocked run is not
    # stored, because its whole content would have been the reason.
    assert [(run.role, run.status) for run in agent_run_repo.list_by_task(task["id"])] == [
        ("coder", "passed")
    ]


def test_coder_can_report_blocked_too(client):
    task = _ready_for_development_task(client)
    _start_coding(client, task)
    result = _agent_run(
        client, task, "coder", "blocked", "no harnoq_* tools in session"
    ).get_json()
    assert result["task"]["status"] == "in_progress"


# --- qa evidence: proof the run held its judgement standard ----------------
#
# Honesty about missing context was a property of the model that happened to be
# running. These tests pin the structural version: a verdict that cannot quote
# the design's criteria is not recorded at all.


def _criteria(task):
    return _qa_evidence(task)["criteria_verdicts"]


def test_qa_passed_without_evidence_is_refused_and_not_recorded(client):
    task = _in_progress_with_coder_pass(client)
    response = client.post(
        f"/tasks/{task['id']}/agent-runs",
        json={"role": "qa", "status": "passed", "summary": "looks good to me"},
    )
    assert response.status_code == 400
    # No qa row at all: a rejected verdict must not leave a record suggesting
    # verification happened.
    assert [(run.role, run.status) for run in agent_run_repo.list_by_task(task["id"])] == [
        ("coder", "passed")
    ]


def test_qa_passed_stores_the_evidence_it_was_given(client):
    task = _in_progress_with_coder_pass(client)
    body = _agent_run(client, task, "qa", "passed", "verified").get_json()
    run = body["agent_run"]
    assert run["design_number"] == _qa_evidence(task)["design_number"]
    assert len(run["criteria_verdicts"]) == len(_criteria(task))
    assert {entry["verdict"] for entry in run["criteria_verdicts"]} == {"pass"}
    assert all(entry["evidence"] for entry in run["criteria_verdicts"])


def test_qa_verdict_must_name_the_right_design(client):
    task = _in_progress_with_coder_pass(client)
    response = _agent_run(
        client, task, "qa", "passed", "verified",
        design_number="DESIGN-999",
        criteria_verdicts=_criteria(task),
        test_run=_test_run(),
    )
    assert response.status_code == 400


def test_qa_verdict_must_cover_every_criterion(client):
    task = _in_progress_with_coder_pass(client)
    verdicts = _criteria(task)
    verdicts.append({
        "criterion": "an extra criterion nobody wrote",
        "verdict": "pass",
        "evidence": "made up",
    })
    response = _agent_run(
        client, task, "qa", "passed", "verified",
        design_number=_qa_evidence(task)["design_number"],
        criteria_verdicts=verdicts,
        test_run=_test_run(),
    )
    assert response.status_code == 400
    error = response.get_json()["error"]
    assert "is not one of" in error
    # Two different agents read the old wording ("does not match any UNCLAIMED
    # acceptance criterion") as "an earlier run already claimed these", and
    # both concluded the workflow was blocking them. It is not: every call is
    # matched against the design as it reads right now.
    assert "claimed" in error and "Nothing here is 'claimed'" in error


def test_qa_verdict_criteria_must_be_quoted_not_paraphrased(client):
    task = _in_progress_with_coder_pass(client)
    response = _agent_run(
        client, task, "qa", "passed", "verified",
        design_number=_qa_evidence(task)["design_number"],
        criteria_verdicts=[
            {
                "criterion": "the login thing works",
                "verdict": "pass",
                "evidence": "tried it",
            }
        ],
        test_run=_test_run(),
    )
    assert response.status_code == 400


def test_qa_verdict_requires_evidence_per_criterion(client):
    task = _in_progress_with_coder_pass(client)
    verdicts = [dict(entry, evidence="  ") for entry in _criteria(task)]
    response = _agent_run(
        client, task, "qa", "passed", "verified",
        design_number=_qa_evidence(task)["design_number"],
        criteria_verdicts=verdicts,
        test_run=_test_run(),
    )
    assert response.status_code == 400
    assert "evidence" in response.get_json()["error"]


def test_qa_passed_cannot_contain_a_failing_criterion(client):
    task = _in_progress_with_coder_pass(client)
    response = _agent_run(
        client, task, "qa", "passed", "verified",
        design_number=_qa_evidence(task)["design_number"],
        criteria_verdicts=_qa_evidence(task, verdict="fail")["criteria_verdicts"],
        test_run=_test_run(),
    )
    assert response.status_code == 400
    assert "needs_fix" in response.get_json()["error"]


def test_qa_needs_fix_requires_a_failing_criterion(client):
    task = _in_progress_with_coder_pass(client)
    response = _agent_run(
        client, task, "qa", "needs_fix", "something is wrong",
        design_number=_qa_evidence(task)["design_number"],
        criteria_verdicts=_criteria(task),
        test_run=_test_run(),
    )
    assert response.status_code == 400


def test_qa_can_no_longer_report_the_failed_alias(client):
    """``failed`` was a byte-identical alias of ``needs_fix`` for qa, and it is
    the word the run that judged nothing actually reached for.

    Leaving a spare synonym for "bad" around is what gives an outcome with no
    home somewhere to hide.
    """
    task = _in_progress_with_coder_pass(client)
    response = _agent_run(client, task, "qa", "failed", "could not reach the design")
    assert response.status_code == 400
    assert client.get(f"/tasks/{task['id']}").get_json()["status"] == "in_progress"


def test_coder_keeps_failed(client):
    task = _ready_for_development_task(client)
    _start_coding(client, task)
    result = _agent_run(client, task, "coder", "failed", "compile error").get_json()
    assert result["task"]["status"] == "in_progress"


def test_qa_blocked_needs_no_evidence(client):
    task = _in_progress_with_coder_pass(client)
    response = _agent_run(client, task, "qa", "blocked", "harnoq unreachable")
    assert response.status_code == 200
    assert response.get_json()["agent_run"]["criteria_verdicts"] == []


def test_timeline_shows_a_qa_verdict_s_coverage(client):
    """The human's one view of the task must distinguish "qa passed" from
    "qa passed having judged every criterion"."""
    task = _in_progress_with_coder_pass(client)
    _agent_run(client, task, "qa", "passed", "verified")
    timeline = client.get(f"/tasks/{task['id']}/timeline").get_json()
    qa_entry = next(
        entry for entry in timeline
        if entry["kind"] == "agent_run" and entry["title"] == "qa passed"
    )
    assert "acceptance criteria assessed" in qa_entry["detail"]
    assert _qa_evidence(task)["design_number"] in qa_entry["detail"]


def test_timeline_shows_a_blocked_run_as_itself(client):
    task = _in_progress_with_coder_pass(client)
    _agent_run(client, task, "qa", "blocked", "no harnoq_* tools in session")
    timeline = client.get(f"/tasks/{task['id']}/timeline").get_json()
    assert any(entry["title"] == "qa blocked" for entry in timeline)
    # No status transition at all: the run IS the record, and the unit has
    # not moved.
    assert [e["title"] for e in timeline if e["kind"] == "status"][-1] == (
        "ready_for_development → in_progress"
    )


def test_blocked_after_a_pass_shuts_the_gate_again(client):
    """A later run that could not check must not leave the earlier pass
    standing. This used to be enforced by blanking a cached qa_passed flag;
    the gate now reads the runs themselves, so the newer blocked run is simply
    the latest qa verdict and there is nothing to blank."""
    task = _in_progress_with_coder_pass(client)
    _agent_run(client, task, "qa", "passed", "verified")
    assert client.post(f"/tasks/{task['id']}/mark-ready-for-pr").status_code == 200
    # Assert each step landed: if the withdrawal or the blocked run silently
    # failed, the task would still be sitting in ready_for_pr and the final 409
    # would pass for the wrong reason.
    withdrawn = client.post(
        f"/tasks/{task['id']}/withdraw-from-pr", json={"reason": "recheck"}
    )
    assert withdrawn.status_code == 200
    assert withdrawn.get_json()["status"] == "in_progress"
    blocked = _agent_run(client, task, "qa", "blocked", "harnoq went down")
    assert blocked.status_code == 200
    assert client.post(f"/tasks/{task['id']}/mark-ready-for-pr").status_code == 409


# --- deviates: code works but does not match the design --------------------
#
# A criterion whose behaviour is present but diverges from what the design
# states had nowhere to go before this verdict existed: `fail` overstates a
# working feature and `pass` erases the divergence, so it got written into
# evidence prose and filed `pass` anyway (audited run, TASK-1's qa round).


def test_qa_passed_cannot_contain_a_deviating_criterion(client):
    task = _in_progress_with_coder_pass(client)
    response = _agent_run(
        client, task, "qa", "passed", "verified",
        design_number=_qa_evidence(task)["design_number"],
        criteria_verdicts=_qa_evidence(task, verdict="deviates")["criteria_verdicts"],
        test_run=_test_run(),
    )
    assert response.status_code == 400
    assert "deviates" in response.get_json()["error"]
    assert "needs_fix" in response.get_json()["error"]


def test_qa_needs_fix_accepts_deviates_alone_with_no_failing_criterion(client):
    """`needs_fix` used to require at least one `fail`; a run whose only
    problem is a design/code divergence (no criterion outright fails) must
    still be able to file `needs_fix`, not be forced to lie and say `passed`
    or invent a `fail` that did not happen."""
    task = _in_progress_with_coder_pass(client)
    response = _agent_run(
        client, task, "qa", "needs_fix", "one criterion diverges from the design",
        design_number=_qa_evidence(task)["design_number"],
        criteria_verdicts=_qa_evidence(task, verdict="deviates")["criteria_verdicts"],
        test_run=_test_run(),
    )
    assert response.status_code == 200
    verdicts = response.get_json()["agent_run"]["criteria_verdicts"]
    assert {entry["verdict"] for entry in verdicts} == {"deviates"}


def test_timeline_counts_deviates_separately_from_failed(client):
    """A deviating run must not read as a clean pass in the one human-facing
    view of the task: the coverage line has to name the divergence."""
    task = _in_progress_with_coder_pass(client)
    _agent_run(
        client, task, "qa", "needs_fix", "diverges",
        design_number=_qa_evidence(task)["design_number"],
        criteria_verdicts=_qa_evidence(task, verdict="deviates")["criteria_verdicts"],
        test_run=_test_run(),
    )
    timeline = client.get(f"/tasks/{task['id']}/timeline").get_json()
    qa_entry = next(
        entry for entry in timeline
        if entry["kind"] == "agent_run" and entry["title"] == "qa needs_fix"
    )
    assert "design/implementation deviations" in qa_entry["detail"]


# --- test_run: a structured, self-inconsistent-when-false suite claim ------
#
# The audited qa run claimed "58 passed" — actually the COLLECTED count, off
# pytest's own collection line; the suite ran 2 failed / 56 passed on that
# exact commit. Free-text absorbed the lie; Harnoq stored the sentence
# verbatim and gated nothing on it.


def test_qa_passed_requires_a_test_run_record(client):
    task = _in_progress_with_coder_pass(client)
    response = _agent_run(
        client, task, "qa", "passed", "verified",
        design_number=_qa_evidence(task)["design_number"],
        criteria_verdicts=_criteria(task),
    )
    assert response.status_code == 400
    assert "test_run" in response.get_json()["error"]


def test_qa_passed_with_failed_count_above_zero_is_rejected(client):
    """The exact rule that would have caught the incident: a suite that
    reports any failures cannot back a `passed` verdict, regardless of what
    the summary sentence around it claims."""
    task = _in_progress_with_coder_pass(client)
    response = _agent_run(
        client, task, "qa", "passed", "verified",
        design_number=_qa_evidence(task)["design_number"],
        criteria_verdicts=_criteria(task),
        test_run=_test_run(failed=2, passed=56, collected=58),
    )
    assert response.status_code == 400
    assert "green" in response.get_json()["error"]


def test_qa_passed_with_nonzero_exit_code_is_rejected(client):
    task = _in_progress_with_coder_pass(client)
    response = _agent_run(
        client, task, "qa", "passed", "verified",
        design_number=_qa_evidence(task)["design_number"],
        criteria_verdicts=_criteria(task),
        test_run=_test_run(exit_code=1),
    )
    assert response.status_code == 400


def test_qa_needs_fix_requires_a_test_run_record_too(client):
    task = _in_progress_with_coder_pass(client)
    response = _agent_run(
        client, task, "qa", "needs_fix", "something is wrong",
        design_number=_qa_evidence(task)["design_number"],
        criteria_verdicts=_qa_evidence(task, verdict="fail")["criteria_verdicts"],
    )
    assert response.status_code == 400
    assert "test_run" in response.get_json()["error"]


def test_qa_blocked_requires_no_test_run(client):
    """A run that could not run has no suite result to report; requiring one
    here would refuse the exact outcome that has nothing to report."""
    task = _in_progress_with_coder_pass(client)
    response = _agent_run(client, task, "qa", "blocked", "harnoq unreachable")
    assert response.status_code == 200
    assert response.get_json()["agent_run"]["test_run"] is None


def test_qa_passed_stores_the_test_run_it_was_given(client):
    task = _in_progress_with_coder_pass(client)
    body = _agent_run(client, task, "qa", "passed", "verified").get_json()
    assert body["agent_run"]["test_run"] == _test_run()


def test_test_run_field_rejects_non_integer_counts(client):
    task = _in_progress_with_coder_pass(client)
    bad = _test_run()
    bad["passed"] = "ten"
    response = _agent_run(
        client, task, "qa", "passed", "verified",
        design_number=_qa_evidence(task)["design_number"],
        criteria_verdicts=_criteria(task),
        test_run=bad,
    )
    assert response.status_code == 400
    assert "test_run.passed" in response.get_json()["error"]
