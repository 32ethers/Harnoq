"""DesignCase API tests. A design attaches only to a FEATURE-* block — it has
no INPUT-* link of its own (see app/models/design_case.py)."""

CONFIRM_FIELDS = {
    "problem_statement": "Users need to log in.",
    "selected_solution": "Email + password with rate limiting.",
    "selection_reason": "Lowest risk, satisfies all acceptance criteria.",
    "risks": "Brute force; mitigated by lockout.",
    "test_strategy": "Unit tests for auth logic; integration test for login flow.",
    "acceptance_criteria": "User can log in; wrong password returns 401.",
}


def _make_project(client, name="P"):
    return client.post("/projects", json={"name": name}).get_json()


def _make_feature(client, project_id, name="功能A"):
    return client.post(
        f"/projects/{project_id}/features", json={"name": name}
    ).get_json()


def _make_design(client, project_id, feature_id):
    return client.post(
        f"/projects/{project_id}/design-cases", json={"feature_id": feature_id}
    ).get_json()


def _fill_confirm_fields(client, design_case_id, **overrides):
    payload = dict(CONFIRM_FIELDS)
    payload.update(overrides)
    return client.patch(f"/design-cases/{design_case_id}", json=payload)


def test_create_design_defaults(client):
    project = _make_project(client)
    feature = _make_feature(client, project["id"])
    response = client.post(
        f"/projects/{project['id']}/design-cases", json={"feature_id": feature["id"]}
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data["status"] == "draft"
    assert data["feature_id"] == feature["id"]
    assert data["project_id"] == project["id"]


def test_create_design_rejects_status(client):
    project = _make_project(client)
    feature = _make_feature(client, project["id"])
    response = client.post(
        f"/projects/{project['id']}/design-cases",
        json={"feature_id": feature["id"], "status": "confirmed"},
    )
    assert response.status_code == 400


def test_create_design_requires_feature_id(client):
    project = _make_project(client)
    response = client.post(f"/projects/{project['id']}/design-cases", json={})
    assert response.status_code == 400


def test_list_designs_by_feature(client):
    project = _make_project(client)
    feature = _make_feature(client, project["id"])
    _make_design(client, project["id"], feature["id"])
    _make_design(client, project["id"], feature["id"])
    response = client.get(f"/projects/{project['id']}/design-cases")
    assert response.status_code == 200
    assert len(response.get_json()) == 2


def test_update_content_and_needs_clarification_status(client):
    project = _make_project(client)
    feature = _make_feature(client, project["id"])
    design = _make_design(client, project["id"], feature["id"])
    response = client.patch(
        f"/design-cases/{design['id']}",
        json={"selected_solution": "X", "status": "needs_clarification", "impact_areas": ["backend", "api"]},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["selected_solution"] == "X"
    assert data["status"] == "needs_clarification"
    assert data["impact_areas"] == ["backend", "api"]


def test_update_rejects_gate_status_via_patch(client):
    project = _make_project(client)
    feature = _make_feature(client, project["id"])
    design = _make_design(client, project["id"], feature["id"])
    response = client.patch(f"/design-cases/{design['id']}", json={"status": "confirmed"})
    assert response.status_code == 409


def test_confirm_requires_all_six_fields(client):
    project = _make_project(client)
    feature = _make_feature(client, project["id"])
    design = _make_design(client, project["id"], feature["id"])
    for missing in CONFIRM_FIELDS:
        _fill_confirm_fields(client, design["id"], **{missing: ""})
        response = client.post(f"/design-cases/{design['id']}/confirm")
        assert response.status_code == 400, missing
        assert missing in response.get_json()["error"]


def test_confirm_success_sets_status(client):
    project = _make_project(client)
    feature = _make_feature(client, project["id"])
    design = _make_design(client, project["id"], feature["id"])
    _fill_confirm_fields(client, design["id"])

    response = client.post(f"/design-cases/{design['id']}/confirm")
    assert response.status_code == 200
    confirmed = response.get_json()
    assert confirmed["status"] == "confirmed"
    assert confirmed["confirmed_at"] is not None


def test_design_feature_relation_is_persisted(client):
    project = _make_project(client)
    feature = _make_feature(client, project["id"])
    design = _make_design(client, project["id"], feature["id"])

    assert design["feature_id"] == feature["id"]
    assert design["feature_number"] == feature["feature_number"]


def test_move_design_to_different_feature(client):
    project = _make_project(client)
    feature_a = _make_feature(client, project["id"], "A")
    feature_b = _make_feature(client, project["id"], "B")
    design = _make_design(client, project["id"], feature_a["id"])

    response = client.patch(
        f"/design-cases/{design['id']}", json={"feature_id": feature_b["id"]}
    )
    assert response.status_code == 200
    assert response.get_json()["feature_id"] == feature_b["id"]


def test_reopen_requires_locked_state(client):
    """reopen (and its back-compat alias reject) only applies to confirmed
    designs — a fresh draft is already editable, so there is nothing to
    reopen."""
    project = _make_project(client)
    feature = _make_feature(client, project["id"])
    design = _make_design(client, project["id"], feature["id"])
    response = client.post(f"/design-cases/{design['id']}/reject")
    assert response.status_code == 409


def test_reopen_confirmed_design_succeeds(client):
    project = _make_project(client)
    feature = _make_feature(client, project["id"])
    design = _make_design(client, project["id"], feature["id"])
    _fill_confirm_fields(client, design["id"])
    client.post(f"/design-cases/{design['id']}/confirm")
    response = client.post(f"/design-cases/{design['id']}/reopen")
    assert response.status_code == 200
    reopened = response.get_json()
    assert reopened["status"] == "draft"
    assert reopened["confirmed_at"] is None


def test_confirmed_design_is_immutable_to_content_patch(client):
    project = _make_project(client)
    feature = _make_feature(client, project["id"])
    design = _make_design(client, project["id"], feature["id"])
    _fill_confirm_fields(client, design["id"])
    client.post(f"/design-cases/{design['id']}/confirm")

    response = client.patch(
        f"/design-cases/{design['id']}", json={"selected_solution": "CHANGED"}
    )
    assert response.status_code == 409
    # content must be unchanged
    unchanged = client.get(f"/design-cases/{design['id']}").get_json()
    assert unchanged["selected_solution"] == CONFIRM_FIELDS["selected_solution"]


def test_confirmed_design_can_be_reopened_for_editing(client):
    """PATCH can no longer reopen a locked design directly (that shortcut is
    gone) — reopen() is the dedicated action, then content edits via PATCH."""
    project = _make_project(client)
    feature = _make_feature(client, project["id"])
    design = _make_design(client, project["id"], feature["id"])
    _fill_confirm_fields(client, design["id"])
    client.post(f"/design-cases/{design['id']}/confirm")

    reopen_response = client.post(f"/design-cases/{design['id']}/reopen")
    assert reopen_response.status_code == 200
    assert reopen_response.get_json()["status"] == "draft"
    assert reopen_response.get_json()["confirmed_at"] is None

    response = client.patch(
        f"/design-cases/{design['id']}",
        json={"selected_solution": "Reworked design"},
    )
    assert response.status_code == 200
    reopened = response.get_json()
    assert reopened["status"] == "draft"
    assert reopened["selected_solution"] == "Reworked design"


def test_design_versions_track_create_update_and_restore(client):
    project = _make_project(client)
    feature = _make_feature(client, project["id"])
    created = _make_design(client, project["id"], feature["id"])
    design_id = created["id"]
    assert created["current_version_number"] == 1

    # v1 snapshot from create (empty content)
    versions = client.get(f"/design-cases/{design_id}/versions").get_json()
    assert [row["version_number"] for row in versions] == [1]
    assert versions[0]["change_type"] == "create"
    assert versions[0]["is_current"] is True

    # an update produces v2
    updated = client.patch(
        f"/design-cases/{design_id}",
        json={"problem_statement": "v2 problem", "impact_areas": ["backend"]},
    ).get_json()
    assert updated["current_version_number"] == 2

    versions = client.get(f"/design-cases/{design_id}/versions").get_json()
    assert [row["version_number"] for row in versions] == [2, 1]
    assert versions[0]["change_type"] == "update"
    assert versions[0]["problem_statement"] == "v2 problem"
    assert versions[0]["impact_areas"] == ["backend"]
    assert versions[0]["is_current"] is True
    assert versions[1]["is_current"] is False

    # restoring v1 copies its (empty) content into a new v3
    restored = client.post(f"/design-cases/{design_id}/versions/1/restore")
    assert restored.status_code == 200
    restored_data = restored.get_json()
    assert restored_data["problem_statement"] == ""
    assert restored_data["current_version_number"] == 3

    versions = client.get(f"/design-cases/{design_id}/versions").get_json()
    assert [row["version_number"] for row in versions] == [3, 2, 1]
    assert versions[0]["change_type"] == "restore"
    assert versions[0]["restored_from_version_number"] == 1
    assert versions[0]["is_current"] is True


def test_design_status_only_patch_does_not_create_version(client):
    project = _make_project(client)
    feature = _make_feature(client, project["id"])
    design = _make_design(client, project["id"], feature["id"])
    # status change with no content change must not bump the version
    client.patch(f"/design-cases/{design['id']}", json={"status": "needs_clarification"})
    versions = client.get(f"/design-cases/{design['id']}/versions").get_json()
    assert [row["version_number"] for row in versions] == [1]


def test_restore_design_version_404_when_missing(client):
    project = _make_project(client)
    feature = _make_feature(client, project["id"])
    design = _make_design(client, project["id"], feature["id"])
    response = client.post(f"/design-cases/{design['id']}/versions/999/restore")
    assert response.status_code == 404


def test_restore_design_version_409_when_confirmed(client):
    project = _make_project(client)
    feature = _make_feature(client, project["id"])
    design = _make_design(client, project["id"], feature["id"])
    _fill_confirm_fields(client, design["id"])
    client.post(f"/design-cases/{design['id']}/confirm")
    # confirmed designs are immutable, so restoring an old version is forbidden
    response = client.post(f"/design-cases/{design['id']}/versions/1/restore")
    assert response.status_code == 409


def test_create_design_on_foundation_feature(client):
    project = _make_project(client)
    feature = client.post(
        f"/projects/{project['id']}/features",
        json={"name": "Platform", "orientation": "foundation", "subdomain_type": "generic"},
    ).get_json()

    response = client.post(
        f"/projects/{project['id']}/design-cases",
        json={
            "feature_id": feature["id"],
            "design_scope": "Thumbnail storage layout + contract to every slice.",
        },
    )

    assert response.status_code == 201
    data = response.get_json()
    assert data["feature_number"] == feature["feature_number"]


def test_design_can_be_confirmed_with_depth_fields(client):
    project = _make_project(client)
    feature = _make_feature(client, project["id"])
    design = _make_design(client, project["id"], feature["id"])

    _fill_confirm_fields(client, design["id"])
    confirmed = client.post(f"/design-cases/{design['id']}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.get_json()["status"] == "confirmed"


def test_design_missing_depth_field_is_rejected_at_confirm(client):
    project = _make_project(client)
    feature = _make_feature(client, project["id"])
    design = _make_design(client, project["id"], feature["id"])

    # Depth gate still applies: confirm without the six required fields fails.
    response = client.post(f"/design-cases/{design['id']}/confirm")
    assert response.status_code == 400
