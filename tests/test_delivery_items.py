"""DeliveryItem: model/service/API/UI — see app/models/delivery_item.py and
docs/cn/design-evolution.md#delivery for what this entity is."""
from app.models.delivery_item import DeliveryItem
from tests.test_delivery_gate import _capability_task


def _project(client, name="P"):
    return client.post("/projects", json={"name": name}).get_json()


def _create(client, project_id, kind="seam", **extra):
    payload = {"kind": kind, "subject": "A feeds B", "source": "DESIGN-1 design_scope"}
    payload.update(extra)
    return client.post(f"/projects/{project_id}/delivery-items", json=payload)


# --- service-layer rules (waived/covered evidence, numbering, kind filter) --


def test_create_assigns_monotonic_business_number(client):
    project = _project(client)
    first = _create(client, project["id"]).get_json()
    second = _create(client, project["id"], kind="duplicate").get_json()
    assert first["item_number"] == f"{project['project_number']}-INTEG-1"
    assert second["item_number"] == f"{project['project_number']}-INTEG-2"


def test_reentry_reconciles_an_identical_finding_instead_of_recreating_it(client):
    project = _project(client)
    first = _create(client, project["id"], kind="duplicate").get_json()
    assert client.patch(
        f"/delivery-items/{first['id']}",
        json={"status": "waived", "waive_reason": "human accepted both boundaries"},
    ).status_code == 200

    duplicate = _create(
        client,
        project["id"],
        kind="duplicate",
        subject="  A   FEEDS B ",
        source="design-1   design_scope",
        found_in_round=2,
    )
    assert duplicate.status_code == 409
    assert first["item_number"] in duplicate.get_json()["error"]
    assert "update" in duplicate.get_json()["error"]


def test_found_in_round_must_be_positive(client):
    project = _project(client)
    assert _create(client, project["id"], found_in_round=0).status_code == 400


def test_create_requires_kind_and_subject(client):
    project = _project(client)
    missing_kind = client.post(
        f"/projects/{project['id']}/delivery-items", json={"subject": "x"}
    )
    assert missing_kind.status_code == 400
    missing_subject = client.post(
        f"/projects/{project['id']}/delivery-items", json={"kind": "seam"}
    )
    assert missing_subject.status_code == 400


def test_waived_without_reason_is_rejected(client):
    """The evidence gate: a status change with no reason is indistinguishable
    from having forgotten about the item, so the service refuses it — this is
    not a prompt-level suggestion, it is enforced in delivery_item_service."""
    project = _project(client)
    rejected = _create(client, project["id"], status="waived")
    assert rejected.status_code == 400
    assert "waive_reason" in rejected.get_json()["error"]

    created = _create(client, project["id"]).get_json()
    updated = client.patch(
        f"/delivery-items/{created['id']}", json={"status": "waived"}
    )
    assert updated.status_code == 400
    assert "waive_reason" in updated.get_json()["error"]

    ok = client.patch(
        f"/delivery-items/{created['id']}",
        json={"status": "waived", "waive_reason": "not worth doing this pass"},
    )
    assert ok.status_code == 200
    assert ok.get_json()["status"] == "waived"


def test_driver_only_applies_to_journey_and_nfr(client):
    project = _project(client)
    rejected = _create(client, project["id"], kind="seam", driver="browser automation")
    assert rejected.status_code == 400
    assert "driver" in rejected.get_json()["error"]

    accepted = _create(
        client, project["id"], kind="journey", driver="browser automation"
    )
    assert accepted.status_code == 201
    assert accepted.get_json()["driver"] == "browser automation"


def test_kind_change_drops_stale_driver(client):
    project = _project(client)
    created = _create(client, project["id"], kind="journey", driver="browser automation").get_json()
    assert created["driver"] == "browser automation"
    updated = client.patch(
        f"/delivery-items/{created['id']}", json={"kind": "assembly"}
    ).get_json()
    assert updated["driver"] is None


def test_sys_driver_cannot_be_cleared_or_omitted_by_kind_change(client):
    project = _project(client)
    journey = _create(
        client, project["id"], kind="journey", driver="browser automation"
    ).get_json()
    cleared = client.patch(f"/delivery-items/{journey['id']}", json={"driver": ""})
    assert cleared.status_code == 400
    assembly = _create(client, project["id"], kind="assembly").get_json()
    changed = client.patch(f"/delivery-items/{assembly['id']}", json={"kind": "nfr"})
    assert changed.status_code == 400
    assert "driver is required" in changed.get_json()["error"]


def test_invalid_kind_and_status_are_rejected(client):
    project = _project(client)
    assert _create(client, project["id"], kind="not-a-kind").status_code == 400
    assert _create(client, project["id"], status="not-a-status").status_code == 400


# --- API: list filtering, counts, get/patch/delete error paths -------------


def test_list_filters_by_kind_query_param(client):
    project = _project(client)
    _create(client, project["id"], kind="assembly")
    _create(client, project["id"], kind="seam")
    _create(client, project["id"], kind="journey", driver="browser automation")

    all_items = client.get(f"/projects/{project['id']}/delivery-items").get_json()
    assert len(all_items) == 3

    only_seams = client.get(
        f"/projects/{project['id']}/delivery-items?kind=seam"
    ).get_json()
    assert [item["kind"] for item in only_seams] == ["seam"]

    two_kinds = client.get(
        f"/projects/{project['id']}/delivery-items?kind=seam&kind=journey"
    ).get_json()
    assert {item["kind"] for item in two_kinds} == {"seam", "journey"}


def test_get_patch_delete_unknown_item_404(client):
    project = _project(client)
    assert client.get("/delivery-items/9999").status_code == 404
    assert client.patch("/delivery-items/9999", json={"subject": "x"}).status_code == 404
    assert client.delete("/delivery-items/9999").status_code == 404
    # A project id that doesn't exist 404s too (mirrors glossary/feature).
    assert client.post(
        "/projects/9999/delivery-items", json={"kind": "seam", "subject": "x"}
    ).status_code == 404
    assert client.get("/projects/9999/delivery-items").status_code == 404
    assert client.get("/projects/9999/delivery-items/counts").status_code == 404


def test_delete_is_soft_and_excludes_from_list(client):
    project = _project(client)
    created = _create(client, project["id"]).get_json()
    deleted = client.delete(f"/delivery-items/{created['id']}")
    assert deleted.status_code == 200
    assert client.get(f"/projects/{project['id']}/delivery-items").get_json() == []
    assert DeliveryItem.get_by_id(created["id"]).deleted_at is not None


def test_delivery_items_list_and_counts_have_no_n_plus_one_queries(client, app, monkeypatch):
    project = _project(client)
    _create(client, project["id"], kind="seam")
    database = app.config["DATABASE"]
    original_execute_sql = database.execute_sql
    statements = []

    def count_queries(sql, *args, **kwargs):
        if sql.lstrip().upper().startswith("SELECT"):
            statements.append(sql)
        return original_execute_sql(sql, *args, **kwargs)

    monkeypatch.setattr(database, "execute_sql", count_queries)
    assert client.get(f"/projects/{project['id']}/delivery-items").status_code == 200
    one_item_list_queries = len(statements)
    statements.clear()
    assert client.get(f"/projects/{project['id']}/delivery-items/counts").status_code == 200
    one_item_counts_queries = len(statements)
    statements.clear()

    _create(client, project["id"], kind="assembly")
    _create(client, project["id"], kind="duplicate")
    statements.clear()  # ignore the two setup writes above

    assert client.get(f"/projects/{project['id']}/delivery-items").status_code == 200
    assert len(statements) == one_item_list_queries
    statements.clear()
    assert client.get(f"/projects/{project['id']}/delivery-items/counts").status_code == 200
    assert len(statements) == one_item_counts_queries


def test_delivery_items_cascade_on_physical_project_delete(client, app):
    project = _project(client)
    _create(client, project["id"])
    database = app.config["DATABASE"]
    database.execute_sql("DELETE FROM project WHERE id = ?", (project["id"],))
    assert DeliveryItem.select().where(DeliveryItem.project_id == project["id"]).count() == 0


# --- UI: both pages, and the project-detail nav/gate wiring -----------------


def test_integration_and_system_pages_render_kind_filtered_form(client):
    project = _project(client)
    integration = client.get(f"/ui/projects/{project['id']}/integration")
    assert integration.status_code == 200
    body = integration.get_data(as_text=True)
    assert 'data-page-kind="integration"' in body
    assert 'value="assembly"' in body and 'value="seam"' in body and 'value="duplicate"' in body
    assert 'value="journey"' not in body and 'value="nfr"' not in body
    assert 'id="delivery-create-btn"' in body
    assert 'id="delivery-modal"' in body
    assert body.count('id="delivery-load-status"') == 1
    assert body.count('id="delivery-status"') == 1
    assert "deliv-" not in body

    system = client.get(f"/ui/projects/{project['id']}/system")
    assert system.status_code == 200
    body = system.get_data(as_text=True)
    assert 'data-page-kind="system"' in body
    assert 'value="journey"' in body and 'value="nfr"' in body
    assert 'value="assembly"' not in body


def test_project_detail_links_to_integration_and_system_pages(client):
    project = _project(client)
    body = client.get(f"/ui/projects/{project['id']}").get_data(as_text=True)
    assert 'id="integration-list-link"' in body
    assert 'id="system-list-link"' in body
    assert 'id="integration-delivery-open"' in body
    assert 'id="system-delivery-open"' in body


def test_project_flow_nav_includes_integration_and_system(client):
    project = _project(client)
    body = client.get(f"/ui/projects/{project['id']}/glossary").get_data(as_text=True)
    assert 'data-flow-link="integration"' in body
    assert 'data-flow-link="system"' in body


# --- skill layer (harnoq_delivery_item) -------------------------------------


def _validated_integration_create_payload(client, project):
    from tests.test_integration_inventory_validator import _assembly_manifest

    context = client.post(
        "/skills/project-context",
        json={"project_number": project["project_number"]},
    ).get_json()
    manifest = _assembly_manifest(context["design_cases"][0])
    validation = client.post(
        "/skills/integration-inventory-validate",
        json={"project_number": project["project_number"], "manifest": manifest},
    ).get_json()
    assert validation["valid"] is True, validation["errors"]
    proposed = manifest["proposed_items"][0]
    return {
        "project_number": project["project_number"],
        **{key: proposed[key] for key in ("kind", "subject", "source", "found_in_round")},
        "validation_id": validation["validation_id"],
        "source_row_key": proposed["key"],
    }


def test_skill_list_filters_by_kind(client):
    project = _project(client)
    project_number = project["project_number"]
    for kind in ("assembly", "seam", "journey"):
        # This test is for the read filter, so use the human CRUD surface to
        # arrange mixed historical rows. Agent writes are intentionally gated
        # to one live delivery stage at a time.
        _create(client, project["id"], kind=kind, subject="x")
    filtered = client.post("/skills/delivery-item", json={
        "project_number": project_number,
        "action": "list",
        "payload": {"kind": ["assembly", "seam"]},
    }).get_json()
    assert {item["kind"] for item in filtered["delivery_items"]} == {"assembly", "seam"}


def test_skill_delivery_write_requires_an_open_delivery_gate(client):
    project = _project(client)
    response = client.post("/skills/delivery-item-create", json={
        "project_number": project["project_number"],
        "kind": "seam",
        "subject": "A feeds B",
    })
    assert response.status_code == 409
    assert "harnoq_project_context" in response.get_json()["error"]


def test_skill_delivery_create_keeps_kind_validation_distinct_from_stage_gate(client):
    project = _project(client)
    for payload in (
        {"subject": "A feeds B"},
        {"kind": "not-a-kind", "subject": "A feeds B"},
    ):
        response = client.post("/skills/delivery-item-create", json={
            "project_number": project["project_number"],
            **payload,
        })
        assert response.status_code == 400
        assert "kind" in response.get_json()["error"]


def test_skill_delivery_create_requires_explicit_provenance_and_round(client):
    project = _project(client)
    _capability_task(client, project["id"])
    base = {
        "project_number": project["project_number"],
        "kind": "seam",
        "subject": "Producer feeds consumer",
    }

    missing_source = client.post("/skills/delivery-item-create", json={
        **base,
        "found_in_round": 1,
    })
    assert missing_source.status_code == 400
    assert "source is required" in missing_source.get_json()["error"]

    missing_round = client.post("/skills/delivery-item-create", json={
        **base,
        "source": "P1-DESIGN-1 design_scope",
    })
    assert missing_round.status_code == 400
    assert "found_in_round is required" in missing_round.get_json()["error"]

    invalid_round = client.post("/skills/delivery-item-create", json={
        **base,
        "source": "P1-DESIGN-1 design_scope",
        "found_in_round": 0,
    })
    assert invalid_round.status_code == 400
    assert "positive integer" in invalid_round.get_json()["error"]

    valid = client.post(
        "/skills/delivery-item-create",
        json=_validated_integration_create_payload(client, project),
    )
    assert valid.status_code == 200
    cleared = client.post("/skills/delivery-item-update", json={
        "project_number": project["project_number"],
        "item_number": valid.get_json()["object_id"],
        "source": "   ",
    })
    assert cleared.status_code == 400
    assert "source is required" in cleared.get_json()["error"]


def test_openapi_documents_stage_item_tools(client):
    payload = client.get("/openapi.json").get_json()
    for path, op_id in (
        ("/skills/integration-item", "harnoq_integration_item"),
        ("/skills/integration-item-create", "harnoq_integration_item_create"),
        ("/skills/integration-item-update", "harnoq_integration_item_update"),
        ("/skills/system-item", "harnoq_system_item"),
        ("/skills/system-item-create", "harnoq_system_item_create"),
        ("/skills/system-item-update", "harnoq_system_item_update"),
    ):
        assert payload["paths"][path]["post"]["operationId"] == op_id
    assert "/skills/delivery-item" not in payload["paths"]
    required = payload["components"]["schemas"]["DeliveryItemCreateRequest"]["required"]
    assert set(required) == {
        "project_number", "kind", "subject", "source", "found_in_round",
    }


# --- a waived decision is not deletable -------------------------------------

def _waived(client, project_id):
    item = client.post(
        f"/projects/{project_id}/delivery-items",
        json={"kind": "duplicate", "subject": "同一个契约有三处实现"},
    ).get_json()
    client.patch(
        f"/delivery-items/{item['id']}",
        json={"status": "waived", "waive_reason": "三处各自的调用方约束不同,合并代价大于收益"},
    )
    return item


def test_a_waived_item_cannot_be_deleted(client):
    """Its reason is the answer to a question the next scan WILL ask again.
    Delete it and someone re-decides from scratch — the rediscovery loop this
    whole stage exists to end."""
    project = client.post("/projects", json={"name": "P"}).get_json()
    item = _waived(client, project["id"])

    refused = client.delete(f"/delivery-items/{item['id']}")
    assert refused.status_code == 409
    assert "waived" in refused.get_json()["error"]

    still_there = client.get(f"/projects/{project['id']}/delivery-items").get_json()
    assert [i["id"] for i in still_there] == [item["id"]]


def test_reopening_a_waived_item_makes_it_deletable_again(client):
    """Changing your mind stays possible — as two steps, and the two steps are
    the point: 'open' is itself the statement that the question is live again."""
    project = client.post("/projects", json={"name": "P"}).get_json()
    item = _waived(client, project["id"])

    assert client.patch(
        f"/delivery-items/{item['id']}", json={"status": "open"}
    ).status_code == 200
    assert client.delete(f"/delivery-items/{item['id']}").status_code == 200
