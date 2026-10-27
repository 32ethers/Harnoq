"""ChangeRequest governance for designs and new work (docs/cn/design-evolution.md §6).

The record is governance, not an editor: these tests pin the workflow, the
impact analysis walking the real chain, the staged change response, and
soft-delete/restore.
"""
from tests.test_design_cases import CONFIRM_FIELDS


def _project(client, name="P"):
    return client.post("/projects", json={"name": name}).get_json()


def _feature(client, project_id, name="f"):
    return client.post(
        f"/projects/{project_id}/features", json={"name": name}
    ).get_json()


def _design(client, project_id, feature_id):
    return client.post(
        f"/projects/{project_id}/design-cases", json={"feature_id": feature_id}
    ).get_json()


def _confirm_design(client, design_id):
    client.patch(f"/design-cases/{design_id}", json=CONFIRM_FIELDS)
    return client.post(f"/design-cases/{design_id}/confirm").get_json()


def _task(client, design_id, title="T"):
    return client.post(
        f"/design-cases/{design_id}/tasks",
        json={"title": title, "task_type": "feature"},
    ).get_json()


def _cr(client, project_id, **payload):
    return client.post(f"/projects/{project_id}/change-requests", json=payload)


def test_a_change_request_starts_proposed_and_numbered(client):
    project = _project(client)
    feature = _feature(client, project["id"])
    design = _design(client, project["id"], feature["id"])

    response = _cr(
        client,
        project["id"],
        target_type="design_case",
        target_id=design["id"],
        reason="设计需要重新考虑",
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data["status"] == "proposed"
    assert data["change_request_number"] == "P1-CR-1"
    assert data["target_number"] == design["design_number"]
    assert data["impact"] == []


def test_reason_is_required(client):
    project = _project(client)
    assert _cr(client, project["id"], target_type="new").status_code == 400


def test_a_new_target_takes_no_id(client):
    project = _project(client)
    assert _cr(client, project["id"], target_type="new", reason="加个需求").status_code == 201
    bad = _cr(client, project["id"], target_type="new", target_id=1, reason="x")
    assert bad.status_code == 400


def test_analysis_walks_design_to_task(client):
    project = _project(client)
    feature = _feature(client, project["id"])
    design = _design(client, project["id"], feature["id"])
    _confirm_design(client, design["id"])
    task = _task(client, design["id"])

    change_request = _cr(
        client,
        project["id"],
        target_type="design_case",
        target_id=design["id"],
        reason="设计本身错了",
    ).get_json()
    analyzed = client.post(
        f"/change-requests/{change_request['id']}/analyze"
    ).get_json()

    assert analyzed["status"] == "analyzing"
    numbers = [entry["object_number"] for entry in analyzed["impact"]]
    assert numbers == [task["task_number"]]
    assert all(entry["response"] for entry in analyzed["impact"])


def test_a_shipped_task_is_told_to_add_rather_than_rewrite(client, app):
    # The default change policy once something merged: propose an increment, leave the
    # delivery alone. The analysis has to SAY that, or nobody follows it.
    project = _project(client)
    feature = _feature(client, project["id"])
    design = _design(client, project["id"], feature["id"])
    _confirm_design(client, design["id"])
    task = _task(client, design["id"])
    with app.app_context():
        from app.repositories import development_task_repo

        row = development_task_repo.get(task["id"])
        row.status = "completed"
        development_task_repo.save(row)

    change_request = _cr(
        client,
        project["id"],
        target_type="design_case",
        target_id=design["id"],
        reason="需求变了",
    ).get_json()
    analyzed = client.post(
        f"/change-requests/{change_request['id']}/analyze"
    ).get_json()

    task_entry = next(
        e for e in analyzed["impact"] if e["object_type"] == "development_task"
    )
    assert "新增" in task_entry["response"]
    assert "reopen_completed" in task_entry["response"]


def test_impact_preview_needs_no_record(client):
    project = _project(client)
    feature = _feature(client, project["id"])
    design = _design(client, project["id"], feature["id"])
    _confirm_design(client, design["id"])
    _task(client, design["id"])

    preview = client.get(
        f"/projects/{project['id']}/change-impact"
        f"?target_type=design_case&target_id={design['id']}"
    )
    assert preview.status_code == 200
    assert len(preview.get_json()) == 1
    # Nothing was created by asking.
    assert client.get(f"/projects/{project['id']}/change-requests").get_json() == []


def test_the_workflow_runs_forward_only(client):
    project = _project(client)
    change_request = _cr(
        client, project["id"], target_type="new", reason="新增一条需求"
    ).get_json()
    base = f"/change-requests/{change_request['id']}"

    # Cannot skip analysis.
    assert client.post(f"{base}/approve").status_code == 409
    assert client.post(f"{base}/analyze").status_code == 200
    assert client.post(f"{base}/apply").status_code == 409
    assert client.post(f"{base}/approve").status_code == 200
    applied = client.post(f"{base}/apply")
    assert applied.status_code == 200
    assert applied.get_json()["applied_at"] is not None
    # And no going back.
    assert client.post(f"{base}/approve").status_code == 409


def test_closing_requires_saying_how_it_ended(client):
    project = _project(client)
    change_request = _cr(
        client, project["id"], target_type="new", reason="新增一条需求"
    ).get_json()
    base = f"/change-requests/{change_request['id']}"

    assert client.post(f"{base}/close", json={}).status_code == 400
    closed = client.post(f"{base}/close", json={"resolution": "放弃, 需求方撤回了"})
    assert closed.status_code == 200
    assert closed.get_json()["status"] == "closed"
    assert closed.get_json()["resolution"] == "放弃, 需求方撤回了"
    # Terminal.
    assert client.post(f"{base}/analyze").status_code == 409


def test_the_reason_locks_once_approved(client):
    project = _project(client)
    change_request = _cr(
        client, project["id"], target_type="new", reason="原始理由"
    ).get_json()
    base = f"/change-requests/{change_request['id']}"

    assert client.patch(base, json={"reason": "改过的理由"}).status_code == 200
    client.post(f"{base}/analyze")
    client.post(f"{base}/approve")
    # An approval is a signature on a stated reason.
    assert client.patch(base, json={"reason": "偷偷再改"}).status_code == 409


def test_the_whole_flow_is_reachable_from_the_skill_surface(client):
    project = _project(client)
    feature = _feature(client, project["id"])
    design = _design(client, project["id"], feature["id"])
    pnum = project["project_number"]

    created = client.post(
        "/skills/change-request-create",
        json={
            "project_number": pnum,
            "payload": {
                "design_number": design["design_number"],
                "reason": "需求方改了主意",
            },
        },
    )
    assert created.status_code == 200, created.get_json()
    number = created.get_json()["object_id"]
    assert number == "P1-CR-1"

    for action in ("analyze", "approve"):
        response = client.post(
            f"/skills/change-request-{action}",
            json={"project_number": pnum, "payload": {"change_request_number": number}},
        )
        assert response.status_code == 200, (action, response.get_json())
        assert response.get_json()["status"] == "succeeded"

    # The change itself. `apply` asserts it was made upstream and now checks
    # that claim, so a flow test that never edits the target is not walking
    # the real flow — it is walking the one this gate exists to catch.
    client.patch(
        f"/design-cases/{design['id']}",
        json={"problem_statement": "改了：需求方的新说法。"},
    )

    applied = client.post(
        "/skills/change-request-apply",
        json={"project_number": pnum, "payload": {"change_request_number": number}},
    )
    assert applied.status_code == 200, applied.get_json()
    assert applied.get_json()["status"] == "succeeded"

    closed = client.post(
        "/skills/change-request-close",
        json={
            "project_number": pnum,
            "payload": {"change_request_number": number, "resolution": "已实施"},
        },
    )
    assert closed.status_code == 200
    fetched = client.post(
        "/skills/change-request",
        json={
            "project_number": pnum,
            "action": "get",
            "payload": {"change_request_number": number},
        },
    ).get_json()
    assert fetched["change_request"]["status"] == "closed"
    assert fetched["change_request"]["resolution"] == "已实施"


def test_analyzed_repair_guidance_allows_existing_authorization_on_every_surface(client):
    """Both the next-step receipt and later reads must avoid a blanket human stop."""
    import asyncio
    import pytest

    project = _project(client)
    feature = _feature(client, project["id"])
    design = _design(client, project["id"], feature["id"])
    created = client.post("/skills/change-request-create", json={
        "project_number": project["project_number"],
        "payload": {"design_number": design["design_number"],
                    "reason": "Restore the confirmed contract within the user's existing repair authorization."},
    })
    assert created.status_code == 200
    keys = {"project_number": project["project_number"],
            "payload": {"change_request_number": created.get_json()["object_id"]}}
    analyzed = client.post("/skills/change-request-analyze", json=keys)
    assert analyzed.status_code == 200
    fetched = client.post("/skills/change-request", json=keys | {"action": "get"}).get_json()
    action = fetched["change_request"]["ai_available_actions"]["approve_this_change_request"]
    spec = client.get("/openapi.json").get_json()
    surfaces = [
        analyzed.get_json()["next_step"], action["use_when"],
        spec["paths"]["/skills/change-request-analyze"]["post"]["description"],
        spec["paths"]["/skills/change-request-approve"]["post"]["description"],
    ]
    for surface in surfaces:
        assert "existing authorization" in surface
        assert "without another user confirmation" in surface
        assert "product decision" in surface.lower() or "product trade-offs" in surface.lower()
    # Context hoists only the active method, which must include the full policy.
    context = client.post("/skills/project-context", json={
        "project_number": project["project_number"],
    }).get_json()
    assert "approval_policy" in context["active_stage_method"]
    assert client.post("/skills/change-request-approve", json=keys).status_code == 200

    pytest.importorskip("mcp")
    from harnoq_mcp import server
    descriptions = {tool.name: tool.description for tool in asyncio.run(server.mcp.list_tools())}
    for name in ("harnoq_change_request_analyze", "harnoq_change_request_approve"):
        assert "without another user confirmation" in " ".join(descriptions[name].split())


def test_impact_is_readable_from_the_skill_surface_without_proposing(client):
    project = _project(client)
    feature = _feature(client, project["id"])
    design = _design(client, project["id"], feature["id"])
    _confirm_design(client, design["id"])
    _task(client, design["id"])

    response = client.post(
        "/skills/change-request",
        json={
            "project_number": project["project_number"],
            "action": "impact",
            "payload": {"design_number": design["design_number"]},
        },
    )
    assert response.status_code == 200
    assert len(response.get_json()["impact"]) == 1
    assert client.get(f"/projects/{project['id']}/change-requests").get_json() == []


def test_soft_delete_and_restore(client):
    project = _project(client)
    change_request = _cr(
        client, project["id"], target_type="new", reason="x"
    ).get_json()
    base = f"/change-requests/{change_request['id']}"

    assert client.delete(base).status_code == 200
    assert client.get(base).status_code == 404
    trash = client.get(f"/projects/{project['id']}/change-requests/trash").get_json()
    assert [cr["id"] for cr in trash] == [change_request["id"]]
    assert client.post(f"{base}/restore").status_code == 200
    assert client.get(base).status_code == 200


def test_change_request_is_reachable_on_every_surface(client):
    """Action-surface parity: a service action nobody can call is a hole.

    This project has repeatedly shipped an entity wired at one layer and
    invisible at another, so the surfaces get asserted together.
    """
    import asyncio

    import pytest
    import yaml

    # skill routes
    project = _project(client)
    for path in (
        "/skills/change-request",
        "/skills/change-request-create",
        "/skills/change-request-analyze",
        "/skills/change-request-approve",
        "/skills/change-request-apply",
        "/skills/change-request-close",
    ):
        response = client.post(path, json={"project_number": project["project_number"]})
        assert response.status_code != 404, path

    # openapi
    spec = yaml.safe_load(open("skills/openapi.yaml", encoding="utf-8"))
    documented = {
        entry["post"]["operationId"]
        for path, entry in spec["paths"].items()
        if "change-request" in path and "post" in entry
    }
    assert documented == {
        "harnoq_change_request",
        "harnoq_change_request_create",
        "harnoq_change_request_analyze",
        "harnoq_change_request_approve",
        "harnoq_change_request_apply",
        "harnoq_change_request_close",
    }

    # MCP (optional dependency group)
    pytest.importorskip("mcp")
    from harnoq_mcp import server

    names = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    assert {
        "harnoq_change_request_list",
        "harnoq_change_request_get",
        "harnoq_change_request_impact",
        "harnoq_change_request_create",
        "harnoq_change_request_analyze",
        "harnoq_change_request_approve",
        "harnoq_change_request_apply",
        "harnoq_change_request_close",
    } <= names

    # UI page route
    assert client.get(f"/ui/projects/{project['id']}/change-requests").status_code == 200
    body = client.get("/static/app.js").get_data(as_text=True)
    assert "function renderChangeRequestsListPage" in body
    assert "/change-requests" in body
