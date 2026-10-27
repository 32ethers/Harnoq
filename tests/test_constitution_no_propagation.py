"""The constitution's edges propagate nothing (see docs/cn/design-evolution.md §6).

INPUT (SourceRequirement) shares the 3-state lifecycle, and a fresh INPUT
starts editable (draft), so these tests edit it directly without confirming or
reopening first.

Neither direction moves a status:
  - editing an INPUT the constitution was harvested from leaves the confirmed
    constitution confirmed (it used to flip to needs_review), and
  - reopening the constitution leaves confirmed designs alone — it is a
    cross-cutting background constraint, not an artifact designs derive from.
Staleness is derived from version snapshots instead of stamped onto objects.
"""

CONFIRM_FIELDS = {
    "problem_statement": "P.",
    "selected_solution": "S.",
    "selection_reason": "R.",
    "risks": "K.",
    "test_strategy": "T.",
    "acceptance_criteria": "A.",
}


def _project(client):
    return client.post("/projects", json={"name": "P"}).get_json()


def _input(client, pid, title="u"):
    return client.post(
        f"/projects/{pid}/source-requirements",
        json={"title": title, "source_type": "new_feature", "raw_content": "d"},
    ).get_json()


def _feature(client, pid, name="F"):
    return client.post(f"/projects/{pid}/features", json={"name": name}).get_json()


def _confirmed_constitution(client, pid):
    client.patch(f"/projects/{pid}/constitution", json={"system_nature": "x"})
    return client.post(f"/projects/{pid}/constitution/confirm").get_json()


def _status(client, pid):
    return client.get(f"/projects/{pid}/constitution").get_json()["status"]


def test_editing_input_leaves_confirmed_constitution_confirmed(client):
    pid = _project(client)["id"]
    inp = _input(client, pid)
    _confirmed_constitution(client, pid)
    assert _status(client, pid) == "confirmed"

    # An INPUT's content changes — the constitution keeps its own status.
    client.patch(f"/source-requirements/{inp['id']}", json={"title": "changed"})
    assert _status(client, pid) == "confirmed"


def test_draft_constitution_stays_draft_on_input_change(client):
    pid = _project(client)["id"]
    inp = _input(client, pid)
    client.patch(f"/projects/{pid}/constitution", json={"system_nature": "x"})
    client.patch(f"/source-requirements/{inp['id']}", json={"title": "changed"})
    assert _status(client, pid) == "draft"


def test_reopening_constitution_does_not_flag_designs(client):
    # The constitution is a background constraint, not a design's upstream, so
    # revising it must NOT re-review settled designs (regression against the
    # old constitution->design cascade, now removed).
    pid = _project(client)["id"]
    feature = _feature(client, pid)
    _confirmed_constitution(client, pid)
    design = client.post(
        f"/projects/{pid}/design-cases", json={"feature_id": feature["id"]}
    ).get_json()
    client.patch(f"/design-cases/{design['id']}", json=CONFIRM_FIELDS)
    client.post(f"/design-cases/{design['id']}/confirm")
    assert client.get(f"/design-cases/{design['id']}").get_json()["status"] == "confirmed"

    reopened = client.post(f"/projects/{pid}/constitution/reopen")
    assert reopened.status_code == 200, reopened.get_json()
    # Design is untouched — still confirmed.
    assert client.get(f"/design-cases/{design['id']}").get_json()["status"] == "confirmed"
