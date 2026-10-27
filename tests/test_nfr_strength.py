"""A baseline row can be a promise or a stance, and they used to look alike.

A capacity figure the user later called an offhand number sat in the same kind
of table cell as a hard promise. Three designs each copied it into a MUST
acceptance criterion, and each then needed a fixture at that scale to discharge
it. The same table already had a row reading "no fixed target" — the author was
making the distinction in prose because no field carried it.
"""
from app.services import nfr_baseline as nb
from app.services import nfr_coverage_service as nfr
from tests.test_nfr_coverage import CONFIRM_FIELDS, _design, _project_with_baseline

WITH_STRENGTH = """\
| Metric | Target | Strength | Applies to |
|---|---|---|---|
| Collection capacity | 500,000 records | direction | INPUT-1 |
| First screen | within 3 seconds | commitment | INPUT-1 |
"""


# --- parsing ---------------------------------------------------------------

def test_a_table_without_the_column_is_all_commitments():
    """Every constitution written before the column existed. Defaulting the
    other way would silently retire promises nobody had reconsidered."""
    rows = nb.extract_nfr_rows(
        "| Metric | Target | Applies to |\n|---|---|---|\n| Cap | 500k | INPUT-1 |\n"
    )
    assert [r["strength"] for r in rows] == [nb.COMMITMENT]
    assert rows[0]["target"] == "500k" and rows[0]["applies_to"] == "INPUT-1"


def test_the_strength_column_is_read_by_header_not_position():
    """So adding it to an existing table does not depend on where it is put."""
    rows = nb.extract_nfr_rows(WITH_STRENGTH)
    assert [(r["metric"], r["strength"]) for r in rows] == [
        ("Collection capacity", nb.DIRECTION),
        ("First screen", nb.COMMITMENT),
    ]
    assert rows[0]["applies_to"] == "INPUT-1"  # still found, though shifted right


def test_strength_can_sit_anywhere_the_header_says():
    rows = nb.extract_nfr_rows(
        "| 性质 | 指标 | 目标 |\n|---|---|---|\n| 方向 | 扫描吞吐 | 无固定目标 |\n"
    )
    assert rows == [{"metric": "扫描吞吐", "target": "无固定目标",
                     "applies_to": "", "strength": nb.DIRECTION}]


def test_an_unrecognised_strength_stays_a_commitment():
    """A typo must not retire a promise. It stays in the coverage view still
    demanding an owner, which is how it gets noticed."""
    rows = nb.extract_nfr_rows(
        "| Metric | Strength |\n|---|---|\n| Cap | 大概吧 |\n"
    )
    assert rows[0]["strength"] == nb.COMMITMENT


def test_a_baseline_with_no_table_is_still_one_commitment_per_line():
    rows = nb.extract_nfr_rows("能撑住 500,000 条\n首屏 3 秒内\n")
    assert [r["metric"] for r in rows] == ["能撑住 500,000 条", "首屏 3 秒内"]
    assert {r["strength"] for r in rows} == {nb.COMMITMENT}


# --- coverage --------------------------------------------------------------

def test_an_unclaimed_direction_is_not_a_hole(client):
    """Nothing about it is measurable, so an owner would own nothing. Flagging
    it trains people to claim rows they cannot prove."""
    project, feature = _project_with_baseline(client, baseline=WITH_STRENGTH)

    report = client.get(f"/projects/{project['id']}/nfr-coverage").get_json()
    assert report["total"] == 2
    assert report["commitment_count"] == 1
    assert report["direction_count"] == 1
    assert report["unverified_metrics"] == ["First screen"]
    assert [r["unverified"] for r in report["rows"]] == [False, True]


def test_a_design_may_still_claim_a_direction(client):
    """Claiming one is not an error — it just was never required."""
    project, feature = _project_with_baseline(client, baseline=WITH_STRENGTH)
    design = _design(client, project, feature, verifies_nfr="Collection capacity — 建 50 万行实测")

    report = client.get(f"/projects/{project['id']}/nfr-coverage").get_json()
    row = report["rows"][0]
    assert row["strength"] == nb.DIRECTION
    assert row["verified_by"][0]["design_number"] == design["design_number"]
    assert report["unmatched_claims"] == []


# --- the confirm gate follows the distinction ------------------------------

def test_a_baseline_of_only_directions_demands_no_answer(client):
    """Otherwise every design writes a ritual 无, and a required field that is
    always answered the same way stops being read."""
    project, feature = _project_with_baseline(client, baseline="""\
| Metric | Target | Strength |
|---|---|---|
| Scan throughput | no fixed target | direction |
""")
    design = _design(client, project, feature)
    client.patch(f"/design-cases/{design['id']}", json=CONFIRM_FIELDS)

    assert client.post(f"/design-cases/{design['id']}/confirm").status_code == 200


def test_one_commitment_is_enough_to_demand_an_answer(client):
    project, feature = _project_with_baseline(client, baseline=WITH_STRENGTH)
    design = _design(client, project, feature)
    client.patch(f"/design-cases/{design['id']}", json=CONFIRM_FIELDS)

    refused = client.post(f"/design-cases/{design['id']}/confirm")
    assert refused.status_code == 400
    assert "verifies_nfr" in refused.get_json()["error"]


def test_downgrading_a_row_to_direction_closes_the_hole_it_left(client):
    """The live sequence: a figure was recorded as a promise, three designs
    inherited it as a MUST, and it turned out to have been said in passing.
    Changing the constitution is what retires it — not deleting the test."""
    project, feature = _project_with_baseline(client, baseline=WITH_STRENGTH)
    before = client.get(f"/projects/{project['id']}/nfr-coverage").get_json()
    assert "Collection capacity" not in before["unverified_metrics"]

    client.post(f"/projects/{project['id']}/constitution/reopen")
    client.patch(
        f"/projects/{project['id']}/constitution",
        json={"nfr_baseline": WITH_STRENGTH.replace("| direction |", "| commitment |")},
    )
    client.post(f"/projects/{project['id']}/constitution/confirm")

    after = client.get(f"/projects/{project['id']}/nfr-coverage").get_json()
    assert after["unverified_metrics"] == ["Collection capacity", "First screen"]
    assert after["direction_count"] == 0


def test_the_coverage_service_still_re_exports_the_parsers():
    """nfr_coverage_service is what the API and older callers import."""
    assert nfr.extract_nfr_rows is nb.extract_nfr_rows
    assert nfr.extract_claims is nb.extract_claims
