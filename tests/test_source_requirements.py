"""SourceRequirement API tests."""


def _make_project(client, name="P"):
    return client.post("/projects", json={"name": name}).get_json()


def _make_input(client, project_id, **overrides):
    payload = {"title": "t", "source_type": "new_feature"}
    payload.update(overrides)
    return client.post(f"/projects/{project_id}/source-requirements", json=payload)


def test_create_input_preserves_raw_content(client):
    project = _make_project(client)
    response = _make_input(
        client,
        project["id"],
        title="login feature",
        source_type="new_feature",
        raw_content="用户可以用邮箱登录\n还要支持 SSO",
        attachments=[{"url": "https://x/a.png"}],
        source="user input",
        submitted_by="alice",
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data["raw_content"] == "用户可以用邮箱登录\n还要支持 SSO"
    assert data["source_type"] == "new_feature"
    assert data["attachments"] == [{"url": "https://x/a.png"}]
    assert data["source"] == "user input"
    assert data["submitted_by"] == "alice"
    assert data["project_id"] == project["id"]


def test_create_input_defaults(client):
    project = _make_project(client)
    response = _make_input(client, project["id"], source_type="bug_report")
    assert response.status_code == 201
    data = response.get_json()
    assert data["raw_content"] == ""
    assert data["attachments"] == []
    assert data["source"] == ""
    assert data["submitted_by"] is None


def test_create_input_rejects_status(client):
    project = _make_project(client)
    response = _make_input(
        client, project["id"], title="t", source_type="bug_report", status="archived"
    )
    assert response.status_code == 400


def test_create_input_invalid_type(client):
    project = _make_project(client)
    response = _make_input(client, project["id"], source_type="not_a_type")
    assert response.status_code == 400


def test_create_input_missing_required(client):
    project = _make_project(client)
    response = client.post(
        f"/projects/{project['id']}/source-requirements", json={"source_type": "new_feature"}
    )
    assert response.status_code == 400


def test_create_input_project_not_found(client):
    response = client.post(
        "/projects/9999/source-requirements", json={"title": "t", "source_type": "bug_report"}
    )
    assert response.status_code == 404


def test_list_inputs_by_project(client):
    project = _make_project(client)
    _make_input(client, project["id"], title="a", source_type="new_feature")
    _make_input(client, project["id"], title="b", source_type="bug_report")
    response = client.get(f"/projects/{project['id']}/source-requirements")
    assert response.status_code == 200
    titles = [item["title"] for item in response.get_json()]
    assert titles == ["a", "b"]


def test_get_input(client):
    project = _make_project(client)
    created = _make_input(client, project["id"]).get_json()
    response = client.get(f"/source-requirements/{created['id']}")
    assert response.status_code == 200
    assert response.get_json()["title"] == "t"
    assert response.get_json()["current_version_number"] == 1


def test_get_input_not_found(client):
    response = client.get("/source-requirements/9999")
    assert response.status_code == 404


def test_update_input(client):
    project = _make_project(client)
    created = _make_input(client, project["id"]).get_json()
    response = client.patch(
        f"/source-requirements/{created['id']}", json={"title": "t2", "raw_content": "updated"}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["title"] == "t2"
    assert data["raw_content"] == "updated"


def test_update_input_rejects_status(client):
    project = _make_project(client)
    created = _make_input(client, project["id"]).get_json()
    response = client.patch(
        f"/source-requirements/{created['id']}", json={"status": "analyzing"}
    )
    assert response.status_code == 400


def test_source_requirement_versions_track_create_update_and_restore(client):
    project = _make_project(client)
    created = _make_input(
        client,
        project["id"],
        title="login feature",
        source_type="new_feature",
        raw_content="v1 content",
    ).get_json()
    source_requirement_id = created["id"]

    versions = client.get(f"/source-requirements/{source_requirement_id}/versions")
    assert versions.status_code == 200
    versions_data = versions.get_json()
    assert [row["version_number"] for row in versions_data] == [1]
    assert versions_data[0]["change_type"] == "create"
    assert versions_data[0]["raw_content"] == "v1 content"
    assert versions_data[0]["is_current"] is True

    updated = client.patch(
        f"/source-requirements/{source_requirement_id}",
        json={"raw_content": "v2 content"},
    )
    assert updated.status_code == 200
    assert updated.get_json()["current_version_number"] == 2

    versions_data = client.get(f"/source-requirements/{source_requirement_id}/versions").get_json()
    assert [row["version_number"] for row in versions_data] == [2, 1]
    assert versions_data[0]["change_type"] == "update"
    assert versions_data[0]["raw_content"] == "v2 content"
    assert versions_data[0]["is_current"] is True
    assert versions_data[1]["is_current"] is False

    restored = client.post(f"/source-requirements/{source_requirement_id}/versions/1/restore")
    assert restored.status_code == 200
    restored_data = restored.get_json()
    assert restored_data["raw_content"] == "v1 content"
    assert restored_data["current_version_number"] == 3

    versions_data = client.get(f"/source-requirements/{source_requirement_id}/versions").get_json()
    assert [row["version_number"] for row in versions_data] == [3, 2, 1]
    assert versions_data[0]["change_type"] == "restore"
    assert versions_data[0]["restored_from_version_number"] == 1
    assert versions_data[0]["raw_content"] == "v1 content"
    assert versions_data[0]["is_current"] is True


def test_restore_source_requirement_version_404_when_missing(client):
    project = _make_project(client)
    created = _make_input(client, project["id"]).get_json()
    response = client.post(f"/source-requirements/{created['id']}/versions/999/restore")
    assert response.status_code == 404


# ---- lifecycle (draft/needs_clarification/confirmed/needs_review) --------


def test_new_input_starts_as_draft(client):
    project = _make_project(client)
    created = _make_input(client, project["id"]).get_json()
    assert created["status"] == "draft"


def test_update_input_status_needs_clarification_via_patch(client):
    project = _make_project(client)
    created = _make_input(client, project["id"]).get_json()
    response = client.patch(
        f"/source-requirements/{created['id']}", json={"status": "needs_clarification"}
    )
    assert response.status_code == 200
    assert response.get_json()["status"] == "needs_clarification"


def test_update_input_rejects_gate_status_via_patch(client):
    project = _make_project(client)
    created = _make_input(client, project["id"]).get_json()
    response = client.patch(f"/source-requirements/{created['id']}", json={"status": "confirmed"})
    assert response.status_code == 409


def test_confirm_input_is_bare_status_flip(client):
    project = _make_project(client)
    created = _make_input(client, project["id"]).get_json()
    response = client.post(f"/source-requirements/{created['id']}/confirm")
    assert response.status_code == 200
    assert response.get_json()["status"] == "confirmed"


def test_confirmed_input_is_immutable_to_content_patch(client):
    project = _make_project(client)
    created = _make_input(client, project["id"]).get_json()
    client.post(f"/source-requirements/{created['id']}/confirm")
    response = client.patch(
        f"/source-requirements/{created['id']}", json={"title": "changed"}
    )
    assert response.status_code == 409


def test_reopen_requires_locked_state(client):
    project = _make_project(client)
    created = _make_input(client, project["id"]).get_json()
    response = client.post(f"/source-requirements/{created['id']}/reopen")
    assert response.status_code == 409


def test_reopen_confirmed_input_allows_editing_again(client):
    project = _make_project(client)
    created = _make_input(client, project["id"]).get_json()
    client.post(f"/source-requirements/{created['id']}/confirm")

    reopened = client.post(f"/source-requirements/{created['id']}/reopen")
    assert reopened.status_code == 200
    assert reopened.get_json()["status"] == "draft"

    response = client.patch(
        f"/source-requirements/{created['id']}", json={"title": "changed"}
    )
    assert response.status_code == 200
    assert response.get_json()["title"] == "changed"


def test_mark_reviewed_endpoint_is_gone(client):
    # needs_review retired (see docs/cn/design-evolution.md §6): there is no "still fine" action to
    # undo a flag nobody set. Kept as a route-level guard so it is not revived.
    project = _make_project(client)
    created = _make_input(client, project["id"]).get_json()
    client.post(f"/source-requirements/{created['id']}/confirm")

    response = client.post(f"/source-requirements/{created['id']}/mark-reviewed")
    assert response.status_code == 404
