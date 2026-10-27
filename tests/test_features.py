"""Feature API tests."""
from app.models.feature import Feature, FeatureDependency
from app.models.project import Project

CONFIRM_FIELDS = {
    "problem_statement": "Users need a feature.",
    "selected_solution": "Build it.",
    "selection_reason": "Lowest risk.",
    "risks": "Regression.",
    "test_strategy": "Unit + integration tests.",
    "acceptance_criteria": "Given/when/then.",
}


def _make_project(client, name="P"):
    return client.post("/projects", json={"name": name}).get_json()


def _make_feature(client, project_id, **overrides):
    payload = {
        "name": "功能A",
        "description": "desc",
        "assignee": "alice",
    }
    payload.update(overrides)
    return client.post(f"/projects/{project_id}/features", json=payload)


def _make_design(client, project_id, feature_id):
    return client.post(
        f"/projects/{project_id}/design-cases", json={"feature_id": feature_id}
    ).get_json()


def _confirm_design(client, design_id):
    client.patch(f"/design-cases/{design_id}", json=CONFIRM_FIELDS)
    return client.post(f"/design-cases/{design_id}/confirm")


def test_create_feature_assigns_number_and_metadata(client):
    project = _make_project(client)

    response = _make_feature(client, project["id"])

    assert response.status_code == 201
    data = response.get_json()
    assert data["feature_number"] == "P1-FEATURE-1"
    assert data["name"] == "功能A"
    assert data["assignee"] == "alice"
    assert data["progress"] == "planned"
    assert data["current_version_number"] == 1
    # No INPUT-* link at all — Feature carries no such field.
    assert "covered_source_requirement_ids" not in data
    assert "covered_source_requirement_numbers" not in data


def test_feature_versions_capture_dependencies_and_restore(client):
    project = _make_project(client)
    foundation = _make_feature(client, project["id"], name="Foundation").get_json()
    feature = _make_feature(
        client,
        project["id"],
        name="Original feature",
        description="first line\nsecond line",
        depends_on_feature_ids=[foundation["id"]],
    ).get_json()

    created_versions = client.get(f"/features/{feature['id']}/versions")
    assert created_versions.status_code == 200
    assert created_versions.get_json()[0]["version_number"] == 1
    assert created_versions.get_json()[0]["depends_on_feature_numbers"] == [
        foundation["feature_number"]
    ]
    assert "depends_on_feature_ids" not in created_versions.get_json()[0]

    updated = client.patch(
        f"/features/{feature['id']}",
        json={
            "name": "Renamed feature",
            "description": "first line\nchanged line",
            "depends_on_feature_ids": [],
        },
    )
    assert updated.status_code == 200
    assert updated.get_json()["current_version_number"] == 2

    versions = client.get(f"/features/{feature['id']}/versions").get_json()
    assert [version["version_number"] for version in versions] == [2, 1]
    assert versions[0]["is_current"] is True
    assert versions[0]["depends_on_feature_numbers"] == []

    restored = client.post(f"/features/{feature['id']}/versions/1/restore")
    assert restored.status_code == 200
    assert restored.get_json()["name"] == "Original feature"
    assert restored.get_json()["current_version_number"] == 3
    assert restored.get_json()["depends_on_feature_numbers"] == [
        foundation["feature_number"]
    ]

    versions = client.get(f"/features/{feature['id']}/versions").get_json()
    assert [version["version_number"] for version in versions] == [3, 2, 1]
    assert versions[0]["change_type"] == "restore"
    assert versions[0]["restored_from_version_number"] == 1


def test_two_features_can_be_created_freely_no_exclusivity(client):
    # A holistic INPUT-* document can inform many features; there is no
    # "each requirement belongs to exactly one feature" constraint anymore.
    project = _make_project(client)
    first = _make_feature(client, project["id"], name="功能A")
    assert first.status_code == 201
    second = _make_feature(client, project["id"], name="功能B")
    assert second.status_code == 201


def test_confirmed_design_does_not_imply_implemented_feature(client):
    project = _make_project(client)
    feature = _make_feature(client, project["id"]).get_json()

    design = _make_design(client, project["id"], feature["id"])
    assert _confirm_design(client, design["id"]).status_code == 200

    designed = client.get(f"/features/{feature['id']}").get_json()
    assert designed["progress"] == "confirmed"
    assert designed["design_status"] == "confirmed"
    assert designed["implementation_status"] == "not_started"

    solo_feature = _make_feature(client, project["id"], name="功能C").get_json()
    solo_design = _make_design(client, project["id"], solo_feature["id"])
    # Not confirmed yet -> in_design.
    in_design = client.get(f"/features/{solo_feature['id']}").get_json()
    assert in_design["progress"] == "in_design"


def test_feature_soft_delete_and_restore(client):
    project = _make_project(client)
    feature = _make_feature(client, project["id"]).get_json()

    deleted = client.delete(f"/features/{feature['id']}")
    assert deleted.status_code == 200
    assert deleted.get_json()["deleted_at"] is not None
    assert client.get(f"/projects/{project['id']}/features").get_json() == []

    trash = client.get(f"/projects/{project['id']}/features/trash").get_json()
    assert [item["feature_number"] for item in trash] == [feature["feature_number"]]

    restored = client.post(f"/features/{feature['id']}/restore")
    assert restored.status_code == 200
    assert restored.get_json()["deleted_at"] is None


def test_feature_model_defaults_orientation_supporting_and_not_spike(app):
    project = Project.create(name="P")
    feature = Feature.create(project=project, name="F")

    data = feature.to_dict()
    assert data["orientation"] == "vertical"
    assert data["subdomain_type"] == "supporting"
    assert data["is_spike"] is False
    assert data["depends_on_feature_ids"] == []
    assert data["depends_on_feature_numbers"] == []


def test_feature_model_persists_tags_and_is_spike(app):
    project = Project.create(name="P")
    feature = Feature.create(
        project=project,
        name="Core engine",
        orientation="foundation",
        subdomain_type="core",
        is_spike=True,
    )
    reloaded = Feature.get(Feature.id == feature.id)

    data = reloaded.to_dict()
    assert data["orientation"] == "foundation"
    assert data["subdomain_type"] == "core"
    assert data["is_spike"] is True


def test_architecture_foundation_is_explicit_and_unique_per_project(client):
    project = _make_project(client)
    created = client.post(
        f"/projects/{project['id']}/features",
        json={
            "name": "Architecture Foundation",
            "orientation": "foundation",
            "foundation_kind": "architecture",
            "subdomain_type": "supporting",
        },
    )
    assert created.status_code == 201
    assert created.get_json()["foundation_kind"] == "architecture"

    duplicate = client.post(
        f"/projects/{project['id']}/features",
        json={
            "name": "A second architecture",
            "orientation": "foundation",
            "foundation_kind": "architecture",
        },
    )
    assert duplicate.status_code == 400
    assert "already has architecture foundation" in duplicate.get_json()["error"]


def test_delivery_stages_are_not_features(client):
    project = _make_project(client)
    for kind in ("integration", "system"):
        created = client.post(
            f"/projects/{project['id']}/features",
            json={
                "name": f"{kind} gate",
                "orientation": "foundation",
                "foundation_kind": kind,
                "subdomain_type": "supporting",
            },
        )
        assert created.status_code == 400
        assert "invalid foundation_kind" in created.get_json()["error"]


def test_foundation_kind_requires_foundation_orientation(client):
    project = _make_project(client)
    response = client.post(
        f"/projects/{project['id']}/features",
        json={"name": "Wrong", "foundation_kind": "ui"},
    )
    assert response.status_code == 400
    assert "requires orientation='foundation'" in response.get_json()["error"]


def test_foundation_kind_is_clearable_and_cannot_survive_vertical_update(client):
    project = _make_project(client)
    feature = _make_feature(
        client,
        project["id"],
        name="UI Foundation",
        orientation="foundation",
        foundation_kind="ui",
    ).get_json()

    rejected = client.patch(f"/features/{feature['id']}", json={"orientation": "vertical"})
    assert rejected.status_code == 400

    cleared = client.patch(
        f"/features/{feature['id']}",
        json={"foundation_kind": None, "orientation": "vertical"},
    )
    assert cleared.status_code == 200
    assert cleared.get_json()["foundation_kind"] is None
    assert cleared.get_json()["orientation"] == "vertical"


def test_restore_rejects_foundation_kind_replaced_while_deleted(client):
    project = _make_project(client)
    original = _make_feature(
        client,
        project["id"],
        name="Original Architecture Foundation",
        orientation="foundation",
        foundation_kind="architecture",
    ).get_json()
    assert client.delete(f"/features/{original['id']}").status_code == 200

    replacement = _make_feature(
        client,
        project["id"],
        name="Replacement Architecture Foundation",
        orientation="foundation",
        foundation_kind="architecture",
    ).get_json()

    restored = client.post(f"/features/{original['id']}/restore")
    assert restored.status_code == 400
    assert "cannot restore: project already has architecture foundation" in (
        restored.get_json()["error"]
    )
    live = client.get(f"/projects/{project['id']}/features").get_json()
    assert [feature["id"] for feature in live] == [replacement["id"]]


def test_feature_dependency_edge_shows_up_in_to_dict(app):
    project = Project.create(name="P")
    engine = Feature.create(
        project=project, name="Engine", orientation="foundation", subdomain_type="core"
    )
    platform = Feature.create(
        project=project,
        name="Platform",
        orientation="foundation",
        subdomain_type="generic",
    )
    vertical = Feature.create(project=project, name="Browse")
    FeatureDependency.create(feature=vertical, depends_on_feature=platform)
    FeatureDependency.create(feature=platform, depends_on_feature=engine)

    data = vertical.to_dict()
    assert data["depends_on_feature_ids"] == [platform.id]
    assert data["depends_on_feature_numbers"] == [platform.feature_number]

    # A soft-deleted target feature drops out of the live dependency list.
    platform.deleted_at = "2026-01-01T00:00:00+00:00"
    platform.save()
    assert Feature.get(Feature.id == vertical.id).to_dict()["depends_on_feature_ids"] == []


def test_feature_dependency_pair_is_unique(app):
    project = Project.create(name="P")
    a = Feature.create(project=project, name="A")
    b = Feature.create(project=project, name="B")
    FeatureDependency.create(feature=a, depends_on_feature=b)

    import pytest
    from peewee import IntegrityError

    with pytest.raises(IntegrityError):
        FeatureDependency.create(feature=a, depends_on_feature=b)


def test_design_case_requires_a_feature(app):
    project = Project.create(name="P")
    feature = Feature.create(
        project=project, name="Storage", orientation="foundation", subdomain_type="generic"
    )
    from app.models.design_case import DesignCase

    design = DesignCase.create(
        project=project,
        feature=feature,
        design_scope="Provides thumbnail storage to every vertical slice.",
    )

    data = design.to_dict()
    assert data["feature_id"] == feature.id
    assert data["feature_number"] == feature.feature_number
    assert "source_requirement_id" not in data


def test_create_foundation_feature(client):
    project = _make_project(client)

    response = client.post(
        f"/projects/{project['id']}/features",
        json={
            "name": "Core engine",
            "orientation": "foundation",
            "subdomain_type": "core",
            "is_spike": True,
        },
    )

    assert response.status_code == 201
    data = response.get_json()
    assert data["orientation"] == "foundation"
    assert data["subdomain_type"] == "core"
    assert data["is_spike"] is True


def test_create_vertical_feature_needs_no_source_requirement(client):
    project = _make_project(client)

    response = client.post(
        f"/projects/{project['id']}/features",
        json={"name": "Browse", "orientation": "vertical"},
    )

    assert response.status_code == 201


def test_create_feature_rejects_invalid_orientation_and_subdomain_type(client):
    project = _make_project(client)

    bad_orientation = client.post(
        f"/projects/{project['id']}/features",
        json={"name": "F", "orientation": "sideways"},
    )
    assert bad_orientation.status_code == 400

    bad_subdomain = client.post(
        f"/projects/{project['id']}/features",
        json={
            "name": "F",
            "orientation": "foundation",
            "subdomain_type": "bespoke",
        },
    )
    assert bad_subdomain.status_code == 400


def test_update_feature_tags_round_trip(client):
    project = _make_project(client)
    feature = _make_feature(client, project["id"]).get_json()

    response = client.patch(
        f"/features/{feature['id']}",
        json={"orientation": "foundation", "subdomain_type": "generic", "is_spike": False},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["orientation"] == "foundation"
    assert data["subdomain_type"] == "generic"
    assert data["is_spike"] is False


def test_feature_dependency_edges_create_update_and_cycle_rejection(client):
    project = _make_project(client)
    engine = client.post(
        f"/projects/{project['id']}/features",
        json={"name": "Engine", "orientation": "foundation", "subdomain_type": "core"},
    ).get_json()
    platform = client.post(
        f"/projects/{project['id']}/features",
        json={
            "name": "Platform",
            "orientation": "foundation",
            "subdomain_type": "generic",
            "depends_on_feature_ids": [engine["id"]],
        },
    ).get_json()
    vertical = _make_feature(
        client, project["id"], name="Browse",
        depends_on_feature_ids=[platform["id"]],
    ).get_json()

    assert vertical["depends_on_feature_ids"] == [platform["id"]]
    assert vertical["depends_on_feature_numbers"] == [platform["feature_number"]]

    # Replacing edges via update swaps them out cleanly.
    updated = client.patch(
        f"/features/{vertical['id']}",
        json={"depends_on_feature_ids": [engine["id"]]},
    ).get_json()
    assert updated["depends_on_feature_ids"] == [engine["id"]]

    # A self-dependency is rejected.
    self_dep = client.patch(
        f"/features/{engine['id']}",
        json={"depends_on_feature_ids": [engine["id"]]},
    )
    assert self_dep.status_code == 400

    # A cycle (engine -> platform -> engine) is rejected and leaves the graph
    # unchanged.
    cycle = client.patch(
        f"/features/{engine['id']}",
        json={"depends_on_feature_ids": [platform["id"]]},
    )
    assert cycle.status_code == 400
    assert "acyclic" in cycle.get_json()["error"]
    unchanged = client.get(f"/features/{engine['id']}").get_json()
    assert unchanged["depends_on_feature_ids"] == []


def test_feature_dependency_target_must_be_same_project(client):
    project_a = _make_project(client, "A")
    project_b = _make_project(client, "B")
    feature_a = client.post(
        f"/projects/{project_a['id']}/features",
        json={"name": "F", "orientation": "foundation"},
    ).get_json()
    feature_b = client.post(
        f"/projects/{project_b['id']}/features",
        json={"name": "G", "orientation": "foundation"},
    ).get_json()

    response = client.patch(
        f"/features/{feature_a['id']}",
        json={"depends_on_feature_ids": [feature_b["id"]]},
    )
    assert response.status_code == 400


def test_feature_dependency_topological_order_matches_dependency_spine(app):
    from app.services import feature_dependency_service

    project = Project.create(name="P")
    engine = Feature.create(project=project, name="Engine", orientation="foundation", subdomain_type="core")
    platform = Feature.create(project=project, name="Platform", orientation="foundation", subdomain_type="generic")
    vertical = Feature.create(project=project, name="Browse")
    feature_dependency_service.replace_dependencies(platform, [engine.id])
    feature_dependency_service.replace_dependencies(vertical, [platform.id])

    order = feature_dependency_service.topological_order(project.id)
    assert order.index(engine.id) < order.index(platform.id) < order.index(vertical.id)
