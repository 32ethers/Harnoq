"""The project-wide requirement view, derived from each design's
mined_requirements (see app/services/requirement_digest_service.py)."""
from app.services import requirement_digest_service as digest

BULLETS = """\
- 用户可以导出报表。**用户确认**
- 导出格式固定为 CSV。**我补的（已认可）**
- 单次导出上限尚未定。**待确认**
"""

#: A two-column table putting the mark in its own cell.
TWO_COLUMN = """\
| 需求材料 | 来源 |
|---|---|
| 订阅到期后保留数据 30 天。 | 用户确认 |
| 到期提醒提前 7 天发送。 | 我补的(已认可) |
"""

#: The draft round's three-column forcing table, mark inline in column 1.
FORCING = """\
| 材料 | 强迫出的结构 | 我打算这么做 |
|---|---|---|
| 用户说的：房间容量上限 50 人。**用户确认** | 加入前必须校验容量。 | 满员时拒绝并提示。 |
| 冲突检查 | 无冲突 | — |
"""


def _make_design_with(client, requirements):
    project = client.post("/projects", json={"name": "P"}).get_json()
    feature = client.post(
        f"/projects/{project['id']}/features", json={"name": "功能A"}
    ).get_json()
    design = client.post(
        f"/projects/{project['id']}/design-cases", json={"feature_id": feature["id"]}
    ).get_json()
    client.patch(
        f"/design-cases/{design['id']}", json={"mined_requirements": requirements}
    )
    return project, feature, design


def test_mined_requirements_round_trips(client):
    project, _, design = _make_design_with(client, BULLETS)
    stored = client.get(f"/design-cases/{design['id']}").get_json()
    assert stored["mined_requirements"] == BULLETS


def test_mined_requirements_is_versioned(client):
    """It carries the requirement, so an edit that leaves no history would lose
    what the user asked for with no way to see it changed."""
    project, _, design = _make_design_with(client, BULLETS)
    client.patch(
        f"/design-cases/{design['id']}",
        json={"mined_requirements": BULLETS + "- 另加一条。**用户确认**\n"},
    )
    versions = client.get(f"/design-cases/{design['id']}/versions").get_json()
    texts = [v["mined_requirements"] for v in versions]
    assert BULLETS in texts
    assert any("另加一条" in t for t in texts)


def test_extracts_bullet_marks():
    lines = digest.extract_lines(BULLETS)
    assert [line["mark"] for line in lines] == [
        digest.CONFIRMED, digest.SUPPLIED, digest.PENDING
    ]
    assert lines[0]["text"] == "用户可以导出报表"


def test_extracts_two_column_table():
    """The mark sits in its own cell here — the text must come from the other
    cell, not from the cell the mark was found in."""
    lines = digest.extract_lines(TWO_COLUMN)
    assert [line["mark"] for line in lines] == [digest.CONFIRMED, digest.SUPPLIED]
    assert lines[0]["text"] == "订阅到期后保留数据 30 天"


def test_extracts_forcing_table_and_drops_structural_rows():
    """冲突检查 is a row the method mandates, not something anyone asked for."""
    lines = digest.extract_lines(FORCING)
    assert len(lines) == 1
    assert lines[0]["mark"] == digest.CONFIRMED
    assert "房间容量上限" in lines[0]["text"]


def test_unmarked_lines_are_reported_not_dropped():
    """A design that skipped the marking is a gap worth seeing; silently
    contributing nothing would hide it."""
    lines = digest.extract_lines("- 有人写了一条但没标记。\n")
    assert [line["mark"] for line in lines] == [digest.UNMARKED]


def test_headings_and_empty_input_contribute_nothing():
    assert digest.extract_lines("") == []
    assert digest.extract_lines("## 需求（我理解你要的是）\n") == []


def test_digest_groups_by_feature_and_counts(client):
    project, feature, design = _make_design_with(client, BULLETS)
    response = client.get(f"/projects/{project['id']}/requirement-digest")
    assert response.status_code == 200
    data = response.get_json()

    assert data["total"] == 3
    assert data["counts"][digest.CONFIRMED] == 1
    assert data["counts"][digest.SUPPLIED] == 1
    assert data["counts"][digest.PENDING] == 1

    (row,) = data["features"]
    assert row["feature_number"] == feature["feature_number"]
    assert row["total"] == 3
    (design_row,) = row["designs"]
    assert design_row["design_number"] == design["design_number"]
    assert design_row["design_id"] == design["id"]
    assert design_row["empty"] is False


def test_digest_marks_a_design_with_no_requirements_as_empty(client):
    project = client.post("/projects", json={"name": "P"}).get_json()
    feature = client.post(
        f"/projects/{project['id']}/features", json={"name": "功能A"}
    ).get_json()
    client.post(
        f"/projects/{project['id']}/design-cases", json={"feature_id": feature["id"]}
    )
    data = client.get(f"/projects/{project['id']}/requirement-digest").get_json()
    (row,) = data["features"]
    assert row["designs"][0]["empty"] is True
    assert data["total"] == 0


CONFIRM_FIELDS = {
    "problem_statement": "要解决 X。",
    "selected_solution": "用 A 方案,分成 B、C 两个部件。",
    "selection_reason": "比 D 方案可回退。",
    "risks": "B 失败时降级到 C。",
    "test_strategy": "对 B、C 各写组件测试。",
    "acceptance_criteria": "Given …, When …, Then …。",
}


def test_confirmed_design_with_no_requirement_is_named(client):
    """A design can confirm with this field empty — nothing gates it, on
    purpose. What must not happen is the resulting 0 reading the same as
    "nobody asked for anything here"."""
    project = client.post("/projects", json={"name": "P"}).get_json()
    feature = client.post(
        f"/projects/{project['id']}/features", json={"name": "功能A"}
    ).get_json()
    design = client.post(
        f"/projects/{project['id']}/design-cases", json={"feature_id": feature["id"]}
    ).get_json()
    client.patch(f"/design-cases/{design['id']}", json=CONFIRM_FIELDS)
    confirmed = client.post(f"/design-cases/{design['id']}/confirm")
    assert confirmed.status_code == 200

    data = client.get(f"/projects/{project['id']}/requirement-digest").get_json()
    assert data["total"] == 0
    assert data["unrecorded_design_numbers"] == [design["design_number"]]
    assert data["features"][0]["designs"][0]["unrecorded"] is True


def test_draft_design_with_no_requirement_is_not_named(client):
    """A draft has not claimed to be finished, so an empty one is work in
    progress, not a hole — naming it would train the reader to ignore this."""
    project = client.post("/projects", json={"name": "P"}).get_json()
    feature = client.post(
        f"/projects/{project['id']}/features", json={"name": "功能A"}
    ).get_json()
    client.post(
        f"/projects/{project['id']}/design-cases", json={"feature_id": feature["id"]}
    )
    data = client.get(f"/projects/{project['id']}/requirement-digest").get_json()
    assert data["unrecorded_design_numbers"] == []
    assert data["features"][0]["designs"][0]["empty"] is True
    assert "unrecorded" not in data["features"][0]["designs"][0]


def test_confirmed_design_with_requirements_is_not_named(client):
    project, _, design = _make_design_with(client, BULLETS)
    client.patch(f"/design-cases/{design['id']}", json=CONFIRM_FIELDS)
    client.post(f"/design-cases/{design['id']}/confirm")
    data = client.get(f"/projects/{project['id']}/requirement-digest").get_json()
    assert data["unrecorded_design_numbers"] == []


def test_digest_is_derived_not_stored(client):
    """Editing the design must move the view — a stored second copy would not."""
    project, _, design = _make_design_with(client, BULLETS)
    client.patch(
        f"/design-cases/{design['id']}",
        json={"mined_requirements": "- 只剩这一条。**用户确认**\n"},
    )
    data = client.get(f"/projects/{project['id']}/requirement-digest").get_json()
    assert data["total"] == 1
    assert data["counts"][digest.CONFIRMED] == 1
    assert data["counts"][digest.PENDING] == 0
