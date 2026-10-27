"""Soft-delete / recycle-bin behaviour across the spine entities.

Covers the shared lifecycle (soft delete hides from live reads, restore brings
it back, purge is permanent), the per-project recycle-bin aggregation, and that
purging a parent cascades a real delete to its subtree.
"""

CONFIRM_FIELDS = {
    "problem_statement": "Users need to log in.",
    "selected_solution": "Email + password with rate limiting.",
    "selection_reason": "Lowest risk, satisfies all acceptance criteria.",
    "risks": "Brute force; mitigated by lockout.",
    "test_strategy": "Unit tests for auth logic; integration test for login.",
    "acceptance_criteria": "User can log in; wrong password returns 401.",
}


def _project(client, name="P"):
    return client.post("/projects", json={"name": name}).get_json()


def _input(client, project_id, title="i"):
    return client.post(
        f"/projects/{project_id}/source-requirements",
        json={"title": title, "source_type": "new_feature"},
    ).get_json()


def _feature(client, project_id, name="F"):
    return client.post(
        f"/projects/{project_id}/features", json={"name": name}
    ).get_json()


def _design(client, project_id, feature_id):
    return client.post(
        f"/projects/{project_id}/design-cases", json={"feature_id": feature_id}
    ).get_json()


def _confirm_design(client, design_id):
    client.patch(f"/design-cases/{design_id}", json=CONFIRM_FIELDS)
    client.post(f"/design-cases/{design_id}/confirm")
    return client.get(f"/design-cases/{design_id}").get_json()


def _task(client, design_id):
    return client.post(
        f"/design-cases/{design_id}/tasks", json={"title": "a", "task_type": "feature"}
    ).get_json()


# ---- Project lifecycle ----------------------------------------------------


def test_soft_delete_hides_project_from_list_and_get(client):
    project = _project(client)
    pid = project["id"]

    deleted = client.delete(f"/projects/{pid}")
    assert deleted.status_code == 200
    assert deleted.get_json()["deleted_at"] is not None

    assert client.get(f"/projects/{pid}").status_code == 404
    assert all(p["id"] != pid for p in client.get("/projects").get_json())


def test_deleted_project_appears_in_trash(client):
    pid = _project(client)["id"]
    client.delete(f"/projects/{pid}")

    trash = client.get("/projects/trash").get_json()
    assert [p["id"] for p in trash] == [pid]


def test_restore_brings_project_back(client):
    pid = _project(client)["id"]
    client.delete(f"/projects/{pid}")

    restored = client.post(f"/projects/{pid}/restore")
    assert restored.status_code == 200
    assert restored.get_json()["deleted_at"] is None

    assert client.get(f"/projects/{pid}").status_code == 200
    assert client.get("/projects/trash").get_json() == []


def test_restore_requires_a_deleted_project(client):
    pid = _project(client)["id"]
    # never deleted -> not in the recycle bin
    assert client.post(f"/projects/{pid}/restore").status_code == 404


def test_deleting_a_missing_project_is_404(client):
    assert client.delete("/projects/9999").status_code == 404


def test_double_soft_delete_is_404_second_time(client):
    pid = _project(client)["id"]
    assert client.delete(f"/projects/{pid}").status_code == 200
    # already in the bin, no longer a live row
    assert client.delete(f"/projects/{pid}").status_code == 404


def test_purge_removes_project_permanently(client):
    pid = _project(client)["id"]
    client.delete(f"/projects/{pid}")

    assert client.delete(f"/projects/{pid}/purge").status_code == 204
    assert client.get("/projects/trash").get_json() == []
    # nothing left to restore
    assert client.post(f"/projects/{pid}/restore").status_code == 404


def test_purge_requires_a_deleted_project(client):
    pid = _project(client)["id"]
    # cannot purge a live project; it must go through the bin first
    assert client.delete(f"/projects/{pid}/purge").status_code == 404


# ---- Children + recycle-bin aggregation -----------------------------------


def test_source_requirement_soft_delete_and_restore(client):
    pid = _project(client)["id"]
    iid = _input(client, pid)["id"]

    client.delete(f"/source-requirements/{iid}")
    listed = client.get(f"/projects/{pid}/source-requirements").get_json()
    assert all(i["id"] != iid for i in listed)

    client.post(f"/source-requirements/{iid}/restore")
    listed = client.get(f"/projects/{pid}/source-requirements").get_json()
    assert any(i["id"] == iid for i in listed)


def test_each_entity_trash_endpoint_lists_only_its_own_deleted(client):
    """Each entity has its own recycle bin; trash is not aggregated/mixed."""
    pid = _project(client)["id"]
    fid = _feature(client, pid)["id"]
    did = _design(client, pid, fid)["id"]

    client.delete(f"/design-cases/{did}")

    dc_trash = client.get(f"/projects/{pid}/design-cases/trash").get_json()
    sr_trash = client.get(f"/projects/{pid}/source-requirements/trash").get_json()

    assert [d["id"] for d in dc_trash] == [did]
    # a different entity's bin does not pick up this deletion
    assert sr_trash == []


def test_entity_trash_excludes_live_rows(client):
    pid = _project(client)["id"]
    iid = _input(client, pid)["id"]  # never deleted

    assert client.get(f"/projects/{pid}/source-requirements/trash").get_json() == []

    client.delete(f"/source-requirements/{iid}")
    assert [i["id"] for i in
            client.get(f"/projects/{pid}/source-requirements/trash").get_json()] == [iid]

    client.post(f"/source-requirements/{iid}/restore")
    assert client.get(f"/projects/{pid}/source-requirements/trash").get_json() == []


def test_aggregated_project_trash_endpoint_is_gone(client):
    pid = _project(client)["id"]
    # the old mixed bin at /projects/<id>/trash was removed in favor of
    # per-entity bins; it should no longer resolve
    assert client.get(f"/projects/{pid}/trash").status_code == 404


def test_design_case_soft_delete(client):
    pid = _project(client)["id"]
    fid = _feature(client, pid)["id"]
    did = _design(client, pid, fid)["id"]

    assert client.delete(f"/design-cases/{did}").status_code == 200
    assert client.get(f"/design-cases/{did}").status_code == 404
    assert client.post(f"/design-cases/{did}/restore").status_code == 200
    assert client.get(f"/design-cases/{did}").status_code == 200


def test_development_task_soft_delete_restore_purge(client):
    pid = _project(client)["id"]
    fid = _feature(client, pid)["id"]
    did = _design(client, pid, fid)["id"]
    _confirm_design(client, did)
    task = _task(client, did)
    tid = task["id"]

    assert client.delete(f"/tasks/{tid}").status_code == 200
    assert client.get(f"/tasks/{tid}").status_code == 404
    assert all(t["id"] != tid for t in client.get(f"/projects/{pid}/tasks").get_json())

    assert client.post(f"/tasks/{tid}/restore").status_code == 200
    assert client.get(f"/tasks/{tid}").status_code == 200

    client.delete(f"/tasks/{tid}")
    assert client.delete(f"/tasks/{tid}/purge").status_code == 204
    assert client.post(f"/tasks/{tid}/restore").status_code == 404


# ---- Cascade purge --------------------------------------------------------


def test_purge_project_cascades_to_children(client, app):
    pid = _project(client)["id"]
    _input(client, pid)

    client.delete(f"/projects/{pid}")
    assert client.delete(f"/projects/{pid}/purge").status_code == 204

    db = app.config["DATABASE"]
    db.connect(reuse_if_open=True)
    try:
        count = db.execute_sql(
            "SELECT COUNT(*) FROM source_requirement WHERE project_id = ?", (pid,)
        ).fetchone()[0]
        assert count == 0, "source_requirement rows survived project purge"
    finally:
        db.close()
