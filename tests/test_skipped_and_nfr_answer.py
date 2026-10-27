"""Two holes found by reading one finished task's record.

A qa run reported ``371 collected / 369 passed / 0 failed`` on a suite whose
two performance tests were skipped, and the record could not say so — the
shape had no field for it, so a suite that skipped every expensive test looked
identical to one that ran them all. Separately, thirteen consecutive designs in
that project had left ``verifies_nfr`` empty, so every row of the constitution's
NFR baseline showed as unclaimed while one of them was in fact being measured:
the field existed and nothing ever required an answer.
"""
from app.services import nfr_coverage_service as nfr
from tests.test_nfr_coverage import (
    BASELINE,
    CONFIRM_FIELDS,
    _design,
    _project_with_baseline,
)


# --- test_run.skipped ------------------------------------------------------

def _run(**over):
    run = {"command": "pytest", "exit_code": 0, "collected": 10,
           "passed": 10, "failed": 0, "skipped": 0}
    run.update(over)
    return run


def _qa(client, task, design, **over):
    from app.services import acceptance_criteria

    return client.post(
        f"/tasks/{task['id']}/agent-runs",
        json={
            "role": "qa", "status": over.pop("status", "passed"),
            "summary": "qa", "design_number": design["design_number"],
            "criteria_verdicts": [
                {"criterion": c, "verdict": "pass", "evidence": "e"}
                for c in acceptance_criteria.split(design["acceptance_criteria"])
            ],
            "test_run": _run(**over),
        },
    )


def _in_progress_task(client):
    """A task sitting at in_progress with a confirmed design behind it."""
    project, feature = _project_with_baseline(client)
    design = _design(client, project, feature, verifies_nfr="Collection capacity")
    client.patch(f"/design-cases/{design['id']}", json=CONFIRM_FIELDS)
    client.post(f"/design-cases/{design['id']}/confirm")
    design = client.get(f"/design-cases/{design['id']}").get_json()
    task = client.post(
        f"/design-cases/{design['id']}/tasks",
        json={"title": "T", "task_type": "feature"},
    ).get_json()
    client.post(f"/tasks/{task['id']}/claim", json={"assignee": "m"})
    client.post(f"/tasks/{task['id']}/approve-pre-review", json={"reviewer": "r"})
    client.post(f"/tasks/{task['id']}/start-coding")
    return task, design


def test_a_test_run_without_skipped_is_refused(client):
    """The count is required, not optional-with-a-default: a default of zero
    would let the same silent record through while looking like an answer."""
    task, design = _in_progress_task(client)
    run = _run()
    del run["skipped"]
    refused = client.post(
        f"/tasks/{task['id']}/agent-runs",
        json={"role": "qa", "status": "passed", "summary": "qa",
              "design_number": design["design_number"],
              "criteria_verdicts": [], "test_run": run},
    )
    assert refused.status_code == 400
    assert "skipped" in refused.get_json()["error"]


def test_skipped_tests_do_not_block_a_passing_verdict(client):
    """Suites skip on platform, on optional dependencies, on opt-in flags.
    Refusing would make the honest number the expensive one to report."""
    task, design = _in_progress_task(client)
    ok = _qa(client, task, design, collected=10, passed=8, failed=0, skipped=2)
    assert ok.status_code == 200
    assert ok.get_json()["agent_run"]["test_run"]["skipped"] == 2


def test_the_counts_have_to_reconcile(client):
    """Reporting the collection count as the pass count is the failure this
    record was built for. With skipped present it stops being merely
    implausible and becomes arithmetically impossible to state."""
    task, design = _in_progress_task(client)
    refused = _qa(client, task, design, collected=10, passed=10, failed=0, skipped=2)
    assert refused.status_code == 400
    assert "reconcile" in refused.get_json()["error"]


def test_a_run_reporting_fewer_than_collected_is_fine(client):
    """xfail, errors and deselection all live in the gap; only exceeding
    collected is a contradiction."""
    task, design = _in_progress_task(client)
    assert _qa(
        client, task, design, collected=10, passed=6, failed=0, skipped=1
    ).status_code == 200


# --- verifies_nfr must be answered -----------------------------------------

def test_confirming_needs_an_nfr_answer_when_the_constitution_states_any(client):
    project, feature = _project_with_baseline(client)
    design = _design(client, project, feature)
    client.patch(f"/design-cases/{design['id']}", json=CONFIRM_FIELDS)

    refused = client.post(f"/design-cases/{design['id']}/confirm")
    assert refused.status_code == 400
    assert "verifies_nfr" in refused.get_json()["error"]


def test_an_explicit_none_is_an_answer(client):
    """Most designs verify no NFR. Blocking them would make the requirement a
    toll rather than a question, and a toll gets paid with a false claim."""
    project, feature = _project_with_baseline(client)
    design = _design(client, project, feature, verifies_nfr="无")
    client.patch(f"/design-cases/{design['id']}", json=CONFIRM_FIELDS)

    assert client.post(f"/design-cases/{design['id']}/confirm").status_code == 200


def test_an_explicit_none_is_not_reported_as_a_bogus_claim(client):
    """It would otherwise land in unmatched_claims and read as a design
    claiming a commitment the constitution does not contain."""
    project, feature = _project_with_baseline(client)
    _design(client, project, feature, verifies_nfr="无")

    report = client.get(f"/projects/{project['id']}/nfr-coverage").get_json()
    assert report["unmatched_claims"] == []
    assert report["unverified_count"] == 3


def test_a_word_containing_none_is_still_a_claim():
    """The sentinel is anchored at the start and needs a separator after it. A
    claim that merely begins with one of these words must not be dropped."""
    assert nfr.extract_claims("none of the scan throughput targets") == [
        "none of the scan throughput targets"
    ]
    assert nfr.extract_claims("无关紧要但 Hardware 由本设计验证") == [
        "无关紧要但 Hardware 由本设计验证"
    ]
    assert nfr.extract_claims("- 无\n- N/A\n") == []


def test_a_denial_that_explains_itself_is_still_a_denial(client):
    """The live regression. Requiring an answer produced prose, and prose
    saying WHICH rows it does not prove names those rows — which substring
    matching then read as claims. One design was recorded as the owner of four
    commitments, taking the coverage view from four honest holes to none, which
    is strictly worse than the empty field it replaced."""
    project, feature = _project_with_baseline(client)
    _design(
        client, project, feature,
        verifies_nfr=(
            "无——本设计不证明 nfr_baseline 中的任何指标（Collection capacity、"
            "First screen、Scan throughput 均由后端设计验证；本页只消费其状态）。"
        ),
    )

    report = client.get(f"/projects/{project['id']}/nfr-coverage").get_json()
    assert report["unverified_count"] == 3
    assert report["unmatched_claims"] == []
    assert all(not row["verified_by"] for row in report["rows"])


def test_a_project_with_no_nfr_baseline_does_not_demand_the_answer(client):
    """A required field that is always answered the same way stops being read."""
    project, feature = _project_with_baseline(client, baseline="")
    design = _design(client, project, feature)
    client.patch(f"/design-cases/{design['id']}", json=CONFIRM_FIELDS)

    assert client.post(f"/design-cases/{design['id']}/confirm").status_code == 200


def test_the_live_shape_it_was_built_for(client):
    """A design that does verify a row still confirms and still shows as the
    owner — the gate asks for an answer, it does not push toward 无."""
    project, feature = _project_with_baseline(client)
    design = _design(
        client, project, feature,
        verifies_nfr="Collection capacity — 建 500,000 行跑首屏查询,断言 < 3s",
    )
    client.patch(f"/design-cases/{design['id']}", json=CONFIRM_FIELDS)
    assert client.post(f"/design-cases/{design['id']}/confirm").status_code == 200

    report = client.get(f"/projects/{project['id']}/nfr-coverage").get_json()
    owned = [r for r in report["rows"] if r["verified_by"]]
    assert [r["metric"] for r in owned] == ["Collection capacity"]
    assert owned[0]["verified_by"][0]["design_number"] == design["design_number"]
    assert BASELINE  # the fixture baseline is the one being covered
