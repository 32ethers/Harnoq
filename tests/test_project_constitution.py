"""Project constitution API + lifecycle tests."""


def _make_project(client, name="P"):
    return client.post("/projects", json={"name": name}).get_json()


def _put(client, project_id, **fields):
    return client.patch(f"/projects/{project_id}/constitution", json=fields)


def test_get_before_any_edit_is_404(client):
    project = _make_project(client)
    response = client.get(f"/projects/{project['id']}/constitution")
    assert response.status_code == 404


def test_put_creates_draft_and_roundtrips(client):
    project = _make_project(client)
    response = _put(
        client,
        project["id"],
        system_nature="离线单机相册",
        fixed_stack="Flask + SQLite",
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "draft"
    assert data["system_nature"] == "离线单机相册"
    assert data["fixed_stack"] == "Flask + SQLite"
    # Now GET returns it.
    got = client.get(f"/projects/{project['id']}/constitution").get_json()
    assert got["fixed_stack"] == "Flask + SQLite"


def test_singleton_put_updates_same_record(client):
    project = _make_project(client)
    _put(client, project["id"], fixed_stack="v1")
    _put(client, project["id"], fixed_stack="v2", boundaries="never: 删库")
    got = client.get(f"/projects/{project['id']}/constitution").get_json()
    assert got["fixed_stack"] == "v2"
    assert got["boundaries"] == "never: 删库"


def test_confirm_requires_nonempty(client):
    project = _make_project(client)
    _put(client, project["id"])  # creates an empty draft
    response = client.post(f"/projects/{project['id']}/constitution/confirm")
    assert response.status_code == 400


def test_confirm_then_locked_then_reopen(client):
    project = _make_project(client)
    _put(client, project["id"], system_nature="x")
    confirmed = client.post(
        f"/projects/{project['id']}/constitution/confirm"
    ).get_json()
    assert confirmed["status"] == "confirmed"
    assert confirmed["confirmed_at"] is not None
    # Locked: content edits are rejected until reopen.
    locked = _put(client, project["id"], system_nature="y")
    assert locked.status_code == 409
    reopened = client.post(
        f"/projects/{project['id']}/constitution/reopen"
    ).get_json()
    assert reopened["status"] == "draft"
    assert reopened["confirmed_at"] is None


def test_versions_snapshot_and_restore(client):
    project = _make_project(client)
    _put(client, project["id"], fixed_stack="v1")
    _put(client, project["id"], fixed_stack="v2")
    versions = client.get(
        f"/projects/{project['id']}/constitution/versions"
    ).get_json()
    # create (empty) + two content updates = 3 snapshots, newest first.
    assert [v["fixed_stack"] for v in versions] == ["v2", "v1", ""]
    target = next(v for v in versions if v["fixed_stack"] == "v1")
    restored = client.post(
        f"/projects/{project['id']}/constitution/versions/"
        f"{target['version_number']}/restore"
    ).get_json()
    assert restored["fixed_stack"] == "v1"


def test_patch_status_only_allows_clarification_moves(client):
    project = _make_project(client)
    _put(client, project["id"], system_nature="x")
    ok = client.patch(
        f"/projects/{project['id']}/constitution",
        json={"status": "needs_clarification"},
    )
    assert ok.status_code == 200
    assert ok.get_json()["status"] == "needs_clarification"
    # Cannot jump to confirmed via PATCH.
    bad = client.patch(
        f"/projects/{project['id']}/constitution",
        json={"status": "confirmed"},
    )
    assert bad.status_code == 409
