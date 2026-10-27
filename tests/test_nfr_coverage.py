"""Coverage of the constitution's nfr_baseline by the designs that claim it
(see app/services/nfr_coverage_service.py)."""
from app.services import nfr_coverage_service as nfr

BASELINE = """\
| Metric | Target | Applies to |
|---|---|---|
| Collection capacity | 500,000 records | INPUT-1 |
| First screen | within 3 seconds | INPUT-1 |
| Scan throughput | no fixed target | INPUT-1 |
"""

CONFIRM_FIELDS = {
    "problem_statement": "要解决 X。",
    "selected_solution": "用 A 方案。",
    "selection_reason": "比 B 可回退。",
    "risks": "失败时降级。",
    "test_strategy": "写组件测试。",
    "acceptance_criteria": "- Given …, When …, Then …。",
}


def _project_with_baseline(client, baseline=BASELINE):
    project = client.post("/projects", json={"name": "P"}).get_json()
    client.patch(
        f"/projects/{project['id']}/constitution",
        json={"nfr_baseline": baseline, "system_nature": "一个系统。"},
    )
    client.post(f"/projects/{project['id']}/constitution/confirm")
    feature = client.post(
        f"/projects/{project['id']}/features", json={"name": "功能A"}
    ).get_json()
    return project, feature


def _design(client, project, feature, verifies_nfr=""):
    design = client.post(
        f"/projects/{project['id']}/design-cases", json={"feature_id": feature["id"]}
    ).get_json()
    if verifies_nfr:
        client.patch(f"/design-cases/{design['id']}", json={"verifies_nfr": verifies_nfr})
    return design


def test_extracts_table_rows_and_drops_the_header():
    rows = nfr.extract_nfr_rows(BASELINE)
    assert [r["metric"] for r in rows] == [
        "Collection capacity", "First screen", "Scan throughput"
    ]
    assert rows[0]["target"] == "500,000 records"
    assert rows[0]["applies_to"] == "INPUT-1"


def test_a_baseline_with_no_table_is_one_row_per_line():
    """Same choice acceptance_criteria makes: guessing at an unanticipated
    shape drops rows silently, and a coverage view that omits rows is worse
    than none."""
    rows = nfr.extract_nfr_rows("能撑住 500,000 条\n首屏 3 秒内\n")
    assert [r["metric"] for r in rows] == ["能撑住 500,000 条", "首屏 3 秒内"]


def test_empty_baseline_has_no_rows():
    assert nfr.extract_nfr_rows("") == []
    assert nfr.extract_nfr_rows("   \n ") == []


def test_verifies_nfr_round_trips_and_is_versioned(client):
    """It records who is responsible for proving a project-wide commitment, so
    an edit that left no history would lose that assignment silently."""
    project, feature = _project_with_baseline(client)
    design = _design(client, project, feature, "- Collection capacity：插 50 万行后测分页。")
    stored = client.get(f"/design-cases/{design['id']}").get_json()
    assert "Collection capacity" in stored["verifies_nfr"]

    client.patch(
        f"/design-cases/{design['id']}",
        json={"verifies_nfr": "- First screen：测首屏。"},
    )
    versions = client.get(f"/design-cases/{design['id']}/versions").get_json()
    texts = [v["verifies_nfr"] for v in versions]
    assert any("Collection capacity" in t for t in texts)
    assert any("First screen" in t for t in texts)


def test_a_claimed_row_reports_its_verifying_design(client):
    project, feature = _project_with_baseline(client)
    design = _design(client, project, feature, "- Collection capacity：插 50 万行后测分页。")
    data = client.get(f"/projects/{project['id']}/nfr-coverage").get_json()

    row = next(r for r in data["rows"] if r["metric"] == "Collection capacity")
    assert row["unverified"] is False
    (entry,) = row["verified_by"]
    assert entry["design_number"] == design["design_number"]
    assert entry["how"] == ["Collection capacity：插 50 万行后测分页。"]


def test_a_row_no_design_claims_is_named(client):
    """The whole point: a commitment with no owner is invisible until someone
    reads every design in turn."""
    project, feature = _project_with_baseline(client)
    _design(client, project, feature, "- Collection capacity：插 50 万行后测分页。")
    data = client.get(f"/projects/{project['id']}/nfr-coverage").get_json()

    assert data["total"] == 3
    assert data["unverified_count"] == 2
    assert set(data["unverified_metrics"]) == {"First screen", "Scan throughput"}


def test_nothing_claimed_leaves_every_row_unverified(client):
    project, feature = _project_with_baseline(client)
    _design(client, project, feature)
    data = client.get(f"/projects/{project['id']}/nfr-coverage").get_json()
    assert data["unverified_count"] == 3
    assert all(r["unverified"] for r in data["rows"])


def test_a_claim_matching_no_row_is_reported(client):
    """The mirror hole: a design believes it is proving a project commitment
    the constitution does not contain."""
    project, feature = _project_with_baseline(client)
    design = _design(client, project, feature, "- Cold start time：测冷启动。")
    data = client.get(f"/projects/{project['id']}/nfr-coverage").get_json()

    (unmatched,) = data["unmatched_claims"]
    assert unmatched["design_number"] == design["design_number"]
    assert "Cold start time" in unmatched["claim"]
    assert data["unverified_count"] == 3


def test_two_designs_can_share_one_row(client):
    project, feature = _project_with_baseline(client)
    _design(client, project, feature, "- First screen：前端测。")
    _design(client, project, feature, "- First screen：后端测。")
    data = client.get(f"/projects/{project['id']}/nfr-coverage").get_json()
    row = next(r for r in data["rows"] if r["metric"] == "First screen")
    assert len(row["verified_by"]) == 2


def test_the_verifying_designs_tasks_are_reported(client):
    """"Claimed" and "shipped" are different answers, and the reader needs the
    second one to know whether the commitment has actually been proved yet."""
    project, feature = _project_with_baseline(client)
    design = _design(client, project, feature, "- First screen：测首屏。")
    client.patch(f"/design-cases/{design['id']}", json=CONFIRM_FIELDS)
    client.post(f"/design-cases/{design['id']}/confirm")
    task = client.post(
        f"/design-cases/{design['id']}/tasks",
        json={"title": "实现", "task_type": "feature"},
    ).get_json()

    data = client.get(f"/projects/{project['id']}/nfr-coverage").get_json()
    row = next(r for r in data["rows"] if r["metric"] == "First screen")
    (entry,) = row["verified_by"]
    assert entry["tasks"] == [{"task_number": task["task_number"], "status": "claimable"}]


def test_the_view_is_derived_not_stored(client):
    """Editing the design must move the view — a stored second copy would not."""
    project, feature = _project_with_baseline(client)
    design = _design(client, project, feature, "- Collection capacity：测容量。")
    assert client.get(
        f"/projects/{project['id']}/nfr-coverage"
    ).get_json()["unverified_count"] == 2

    client.patch(f"/design-cases/{design['id']}", json={"verifies_nfr": ""})
    assert client.get(
        f"/projects/{project['id']}/nfr-coverage"
    ).get_json()["unverified_count"] == 3
