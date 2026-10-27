"""Tests for the Harnoq MCP server (harnoq_mcp).

The MCP SDK lives in the optional ``mcp`` dependency group, so these are skipped
on a default install and run under ``uv run --group mcp pytest``. They route the
server's single network boundary (``post_skill``) through the in-process Flask
test client, exercising the real tool -> payload -> skill endpoint -> service
chain without a socket.
"""
import asyncio
import hashlib
import io
import json
import urllib.error

import pytest

pytest.importorskip("mcp")  # optional `mcp` dependency group

from harnoq_mcp import server  # noqa: E402
from tests.test_delivery_gate import _capability_task  # noqa: E402


@pytest.fixture
def harnoq(client, monkeypatch):
    """Point the MCP tools at the in-process app instead of a real HTTP host."""
    def fake_post(path, payload):
        body = {key: value for key, value in payload.items() if value is not None}
        response = client.post(path, json=body)
        data = response.get_json()
        if response.status_code >= 400:
            raise server.HarnoqError(
                f"Harnoq {path} -> HTTP {response.status_code}: "
                f"{(data or {}).get('error')}"
            )
        return data

    monkeypatch.setattr(server, "post_skill", fake_post)
    def fake_get(path):
        response = client.get(path)
        data = response.get_json()
        if response.status_code >= 400:
            raise server.HarnoqError(
                f"Harnoq {path} -> HTTP {response.status_code}: "
                f"{(data or {}).get('error')}"
            )
        return data

    monkeypatch.setattr(server, "get_harnoq", fake_get)
    return server


def _project(client, name="家用图片搜索系统"):
    return client.post("/projects", json={"name": name}).get_json()


# --- transport boundary (real post_skill, no socket) -----------------------

def test_post_skill_drops_none_and_posts_json(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"status": "ready"}).encode("utf-8")

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["content_type"] = request.headers.get("Content-type")
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr(server.urllib.request, "urlopen", fake_urlopen)

    out = server.post_skill("/skills/project-context", {"project_number": "P-1", "x": None})

    assert out == {"status": "ready"}
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/skills/project-context")
    assert "json" in captured["content_type"]
    assert captured["body"] == {"project_number": "P-1"}  # None dropped


def test_post_skill_surfaces_harnoq_error_message(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 400, "Bad Request", {},
            io.BytesIO(json.dumps({"error": "project_number is required"}).encode("utf-8")),
        )

    monkeypatch.setattr(server.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(server.HarnoqError) as excinfo:
        server.post_skill("/skills/project-context", {})
    assert "project_number is required" in str(excinfo.value)
    assert "HTTP 400" in str(excinfo.value)


# --- tool registration -----------------------------------------------------

def test_stage_item_tools_expose_direct_evidence_fields():
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    fields = {
        "evidence_command", "evidence_output", "evidence_exit_code",
        "verified_revision", "repair_record", "blocked_reason",
    }
    for name in (
        "harnoq_integration_item_update", "harnoq_system_item_update",
    ):
        assert fields <= set(tools[name].inputSchema["properties"])


def test_stage_run_update_tool_uses_unambiguous_post_route(client, harnoq):
    from tests.test_integration_inventory_validator import _integration_ready_project
    from tests.test_integration_completion_acceptance import _submit, _zero_gap_manifest

    project, design = _integration_ready_project(client)
    assert _submit(client, project, _zero_gap_manifest(design))["valid"]
    started = harnoq.harnoq_delivery_stage_run_start(
        project["project_number"], "integration", "before-merge", "confirmed-basis"
    )
    updated = harnoq.harnoq_delivery_stage_run_update(
        project["project_number"], started["run_number"], code_revision="merged-main"
    )
    assert updated["run_number"] == started["run_number"]
    assert updated["stage"] == "integration"
    assert updated["code_revision"] == "merged-main"


def test_integration_item_mcp_forwards_direct_stage_evidence(client, harnoq):
    from tests.test_integration_inventory_validator import _integration_ready_project, _assembly_manifest

    project, design = _integration_ready_project(client)
    manifest = _assembly_manifest(design)
    receipt = client.post("/skills/integration-inventory-validate", json={
        "project_number": project["project_number"], "manifest": manifest,
    }).get_json()
    assert receipt["valid"], receipt["errors"]
    proposed = manifest["proposed_items"][0]
    created = client.post("/skills/delivery-item-create", json={
        "project_number": project["project_number"],
        **{key: proposed[key] for key in ("kind", "subject", "source", "found_in_round")},
        "validation_id": receipt["validation_id"], "source_row_key": proposed["key"],
    })
    assert created.status_code == 200, created.get_json()
    started = client.post(f"/projects/{project['project_number']}/delivery-stage-runs/integration", json={
        "code_revision": "rev-1", "design_revision": "confirmed-basis",
    })
    assert started.status_code == 201, started.get_json()

    result = harnoq.harnoq_integration_item_update(
        project_number=project["project_number"],
        item_number=created.get_json()["object_id"], status="covered",
        covered_by="pytest -q tests/test_entry.py::test_user_path",
        evidence_command="pytest -q tests/test_entry.py::test_user_path",
        evidence_output="1 passed", evidence_exit_code=0,
        verified_revision="rev-1", repair_record="Connected entry; test passed.",
    )
    assert result["status"] == "succeeded"
    row = client.get(f"/projects/{project['id']}/delivery-items").get_json()[0]
    assert row["status"] == "covered"
    assert row["evidence_command"] == "pytest -q tests/test_entry.py::test_user_path"
    assert row["stage_run_number"] == started.get_json()["run_number"]


def test_all_workflow_tools_are_registered():
    names = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    expected = {
        "harnoq_project_context", "harnoq_task_progress", "harnoq_task",
        "harnoq_task_get", "harnoq_task_update",
        "harnoq_source_requirement_list", "harnoq_source_requirement_get",
        "harnoq_source_requirement_create",
        "harnoq_source_requirement_append_content",
        "harnoq_source_requirement_update",
        "harnoq_source_requirement_confirm", "harnoq_source_requirement_reopen",
        "harnoq_feature_list", "harnoq_feature_get",
        "harnoq_feature_create", "harnoq_feature_update",
        "harnoq_feature_delete", "harnoq_feature_assign",
        "harnoq_constitution_get", "harnoq_constitution_update",
        "harnoq_constitution_confirm", "harnoq_constitution_reopen",
        "harnoq_glossary_get", "harnoq_glossary_create",
        "harnoq_glossary_update", "harnoq_glossary_delete",
        "harnoq_integration_item_get", "harnoq_integration_item_list", "harnoq_integration_item_create",
        "harnoq_integration_item_update", "harnoq_system_item_list",
        "harnoq_system_item_create", "harnoq_system_item_update",
        "harnoq_integration_inventory_validate",
        "harnoq_integration_inventory_get", "harnoq_integration_inventory_revise",
        "harnoq_design_case_list", "harnoq_design_case_get",
        "harnoq_design_case_create", "harnoq_design_case_update",
        "harnoq_design_case_confirm", "harnoq_design_case_reopen",
        "harnoq_design_case_delete", "harnoq_design_case_restore",
        "harnoq_task_create",
    }
    assert expected <= names
    # No dedicated transition_status tool for INPUT-* — same as DesignCase, the
    # draft <-> needs_clarification PATCH-only move goes through update()'s
    # status field, not a separate tool.
    assert "harnoq_source_requirement_transition_status" not in names
    # The DRAFT layer is gone entirely (see docs/cn/design-evolution.md §2) — no add/update/remove
    # draft tools of any kind.
    assert "harnoq_design_case_add_draft" not in names
    assert "harnoq_design_case_update_draft" not in names
    assert "harnoq_design_case_remove_draft" not in names
    assert "harnoq_design_case_add_draft_dependency" not in names
    assert "harnoq_design_case_remove_draft_dependency" not in names
    # The RequirementItem layer has been removed entirely.
    assert "harnoq_requirement_item_list" not in names
    assert "harnoq_requirement_item_create" not in names


def test_no_entity_has_a_mark_reviewed_mcp_tool():
    # 3-state lifecycle everywhere now (see docs/cn/design-evolution.md §6): no entity has a
    # mark_reviewed tool, because no entity carries needs_review.
    names = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    for entity in ("source_requirement", "constitution", "design_case"):
        assert f"harnoq_{entity}_mark_reviewed" not in names


def test_project_context_tool_requires_project_number():
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    schema = tools["harnoq_project_context"].inputSchema
    assert schema["required"] == ["project_number"]


def test_harnoq_task_get_uses_business_number_route(client, harnoq):
    from tests.test_agent_runs import _ready_for_development_task

    task = _ready_for_development_task(client)
    project = client.get(f"/projects/{task['project_id']}").get_json()
    task_data = harnoq.harnoq_task_get(
        project_number=project["project_number"],
        task_number=task["task_number"],
    )
    assert task_data["task_number"] == task["task_number"]
    assert task_data["design_number"].startswith("P1-DESIGN-")
    assert "id" not in task_data
    # The context delivery change: project_constitution rides along with the same call; this
    # project never touched its constitution, so it must be present as null,
    # not silently absent.
    assert "project_constitution" in task_data
    assert task_data["project_constitution"] is None


def test_harnoq_task_drives_conversational_pre_review(client, harnoq):
    from tests.test_development_task_lifecycle import _claimable_task

    task = _claimable_task(client)
    project = client.get(f"/projects/{task['project_id']}").get_json()
    base = {
        "project_number": project["project_number"],
        "task_number": task["task_number"],
    }
    harnoq.harnoq_task(action="claim", assignee="manager", **base)
    reviewing = harnoq.harnoq_task(action="start_pre_review", **base)
    assert reviewing["status"] == "pre_reviewing"
    approved = harnoq.harnoq_task(
        action="approve_pre_review", reviewer="human", **base
    )
    assert approved["status"] == "ready_for_development"
    coding = harnoq.harnoq_task(action="start_coding", **base)
    assert coding["status"] == "in_progress"


def test_harnoq_task_add_dependencies_uses_business_number_batch(harnoq, monkeypatch):
    captured = {}

    def fake(path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {"task": {"task_number": "P1-TASK-2"}, "dependencies": []}

    monkeypatch.setattr(server, "post_skill", fake)
    server.harnoq_task(
        action="add_dependencies", project_number="P1", task_number="P1-TASK-2",
        depends_on_task_numbers=["P1-TASK-1", "P1-TASK-3"],
    )
    assert captured["path"].endswith("/projects/P1/tasks/P1-TASK-2/dependencies")
    assert captured["payload"]["depends_on_task_numbers"] == ["P1-TASK-1", "P1-TASK-3"]


def test_stage_item_tools_default_and_validate_kind_scope(monkeypatch):
    calls = []
    monkeypatch.setattr(
        server, "harnoq_delivery_item_list",
        lambda project_number, kind=None: calls.append((project_number, kind)) or {},
    )
    server.harnoq_integration_item_list("P1")
    server.harnoq_system_item_list("P1")
    assert calls == [("P1", ["assembly", "seam", "duplicate"]), ("P1", ["journey", "nfr"])]
    with pytest.raises(server.HarnoqError):
        server.harnoq_integration_item_list("P1", kind=["journey"])


def test_integration_item_get_uses_project_business_number_route(monkeypatch):
    paths = []
    monkeypatch.setattr(server, "get_harnoq", lambda path: paths.append(path) or {"item_number": "P1-INTEG-7"})
    result = server.harnoq_integration_item_get("P1", "P1-INTEG-7")
    assert result["item_number"] == "P1-INTEG-7"
    assert paths == ["/projects/P1/integration-items/P1-INTEG-7"]


def test_task_update_openapi_and_mcp_expose_the_same_contract(client):
    spec = client.get("/openapi.json").get_json()
    path = "/projects/{project_number}/tasks/{task_number}/update"
    assert spec["paths"][path]["post"]["operationId"] == "harnoq_task_update"
    schema = spec["components"]["schemas"]["TaskUpdateRequest"]
    tool = next(tool for tool in asyncio.run(server.mcp.list_tools())
                if tool.name == "harnoq_task_update")
    assert set(tool.inputSchema["properties"]) - {"project_number", "task_number"} == set(schema["properties"])
    assert schema["additionalProperties"] is False
    assert schema["minProperties"] == 1


def test_harnoq_task_reject_pre_review_passes_reason_fields(client, harnoq):
    from tests.test_development_task_lifecycle import _claimable_task

    task = _claimable_task(client)
    project = client.get(f"/projects/{task['project_id']}").get_json()
    base = {
        "project_number": project["project_number"],
        "task_number": task["task_number"],
    }
    harnoq.harnoq_task(action="claim", assignee="manager", **base)
    harnoq.harnoq_task(action="start_pre_review", **base)
    rejected = harnoq.harnoq_task(
        action="reject_pre_review",
        reviewer="human",
        comment="criteria are not testable",
        **base,
    )
    assert rejected["status"] == "needs_revision"


def test_harnoq_task_reject_to_design_reaches_the_service(client, harnoq):
    """A service action nobody can call through the MCP surface is a
    deadlock: `in_progress` -> `rejected_to_design` exists in the service
    layer, but a manager only ever acts through `harnoq_task`."""
    from tests.test_development_task_lifecycle import _claimable_task

    task = _claimable_task(client)
    project = client.get(f"/projects/{task['project_id']}").get_json()
    base = {
        "project_number": project["project_number"],
        "task_number": task["task_number"],
    }
    harnoq.harnoq_task(action="claim", assignee="manager", **base)
    harnoq.harnoq_task(action="start_pre_review", **base)
    harnoq.harnoq_task(action="approve_pre_review", reviewer="human", **base)
    harnoq.harnoq_task(action="start_coding", **base)
    rejected = harnoq.harnoq_task(
        action="reject_to_design",
        reason="the design's chosen mechanism cannot satisfy the stated criterion",
        **base,
    )
    assert rejected["status"] == "needs_revision"


def test_harnoq_task_carries_qa_evidence_and_blocked_through_to_the_service(
    client, harnoq
):
    """The manager's only path to recording a qa outcome.

    A gate the service enforces but the MCP tool cannot express is a gate no
    manager can satisfy: it would strand every task in `agent_qa`. Drives both
    new outcomes through the real tool signature.
    """
    from tests.test_agent_runs import _qa_evidence, _ready_for_development_task

    task = _ready_for_development_task(client)
    project = client.get(f"/projects/{task['project_id']}").get_json()
    base = {
        "project_number": project["project_number"],
        "task_number": task["task_number"],
    }
    harnoq.harnoq_task(action="start_coding", **base)
    harnoq.harnoq_task(
        action="record_agent_run", role="coder", status="passed", **base
    )

    blocked = harnoq.harnoq_task(
        action="record_agent_run",
        role="qa",
        status="blocked",
        summary="no harnoq_* tools in session",
        **base,
    )
    assert blocked["task"]["status"] == "in_progress"

    evidence = _qa_evidence(task)
    passed = harnoq.harnoq_task(
        action="record_agent_run",
        role="qa",
        status="passed",
        summary="verified",
        design_number=evidence["design_number"],
        criteria_verdicts=evidence["criteria_verdicts"],
        test_run=evidence["test_run"],
        **base,
    )
    assert passed["agent_run"]["design_number"] == evidence["design_number"]
    assert passed["agent_run"]["criteria_verdicts"]
    assert passed["agent_run"]["test_run"] == evidence["test_run"]
    assert harnoq.harnoq_task(action="mark_ready_for_pr", **base)["status"] == (
        "ready_for_pr"
    )


def test_harnoq_task_carries_structured_evidence_and_uncovered_criteria_through(
    client, harnoq
):
    """The structured ``{tests, code, note}`` evidence shape, and the
    ``uncovered_criteria`` view derived from it, must survive the full
    tool -> HTTP -> service -> HTTP -> tool round trip a real manager uses —
    not just the direct service call ``test_qa_evidence_shape.py`` exercises.
    """
    from tests.test_agent_runs import _ready_for_development_task

    task = _ready_for_development_task(client)
    project = client.get(f"/projects/{task['project_id']}").get_json()
    base = {
        "project_number": project["project_number"],
        "task_number": task["task_number"],
    }
    design_number = harnoq.harnoq_task_get(**base)["design_number"]

    harnoq.harnoq_task(action="start_coding", **base)
    harnoq.harnoq_task(action="record_agent_run", role="coder", status="passed", **base)

    passed = harnoq.harnoq_task(
        action="record_agent_run",
        role="qa",
        status="passed",
        summary="verified",
        design_number=design_number,
        criteria_verdicts=[{
            "criterion": "User can log in with valid credentials.",
            "verdict": "pass",
            "evidence": {"tests": [], "code": ["auth.py:1-2"], "note": "read by hand"},
        }],
        test_run={
            "command": "run the project's test suite", "exit_code": 0,
            "collected": 1, "passed": 1, "failed": 0, "skipped": 0,
        },
        **base,
    )
    stored = passed["agent_run"]["criteria_verdicts"][0]["evidence"]
    assert stored == {"tests": [], "code": ["auth.py:1-2"], "note": "read by hand"}

    # An empty tests list is what makes this criterion uncovered — the
    # agent-facing read (what manager actually calls) must surface it.
    uncovered = harnoq.harnoq_task_get(**base)["uncovered_criteria"]
    assert [c["criterion"] for c in uncovered] == [
        "User can log in with valid credentials."
    ]


def test_harnoq_task_carries_a_deviates_verdict_through_to_needs_fix(client, harnoq):
    """`deviates` (code works, does not match the design) must reach the
    service through the real tool signature the same way `fail` does — a
    manager can only pass through what qa reported, on whichever word qa used.
    """
    from tests.test_agent_runs import _qa_evidence, _ready_for_development_task

    task = _ready_for_development_task(client)
    project = client.get(f"/projects/{task['project_id']}").get_json()
    base = {
        "project_number": project["project_number"],
        "task_number": task["task_number"],
    }
    harnoq.harnoq_task(action="start_coding", **base)
    harnoq.harnoq_task(action="record_agent_run", role="coder", status="passed", **base)

    evidence = _qa_evidence(task, verdict="deviates")
    needs_fix = harnoq.harnoq_task(
        action="record_agent_run",
        role="qa",
        status="needs_fix",
        summary="a criterion diverges from the design",
        design_number=evidence["design_number"],
        criteria_verdicts=evidence["criteria_verdicts"],
        test_run=evidence["test_run"],
        **base,
    )
    assert {
        entry["verdict"] for entry in needs_fix["agent_run"]["criteria_verdicts"]
    } == {"deviates"}


def test_harnoq_task_test_run_param_is_exposed_in_the_tool_schema():
    """A field the service requires but the MCP tool cannot send would
    deadlock every qa run (see the test above, which sends it through the
    real tool signature): the schema must advertise it too."""
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    properties = tools["harnoq_task"].inputSchema["properties"]
    assert "test_run" in properties
    assert properties["test_run"].get("type") != "string"


def test_harnoq_task_qa_evidence_params_are_exposed_in_the_tool_schema():
    """Present in the generated inputSchema, not merely accepted by the Python
    function — a manager can only send what the schema advertises."""
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    properties = tools["harnoq_task"].inputSchema["properties"]
    assert "design_number" in properties
    assert "criteria_verdicts" in properties
    assert properties["criteria_verdicts"].get("type") != "string"


def test_markdown_fields_are_declared_in_docstrings_and_param_descriptions():
    """Every Markdown-classified write-tool parameter must carry the standard
    Markdown clause both in the tool docstring (guaranteed-delivered) and in
    its per-parameter Annotated Field description (verified to actually reach
    FastMCP's generated inputSchema)."""
    md_clause = "Markdown — use real line breaks, never the literal characters backslash-n."
    md_snippet = "use real line breaks, never the literal characters backslash-n"
    assert "All prose/content fields in Harnoq are Markdown" in server.HARNOQ_INSTRUCTIONS
    assert "backslash-n" in server.HARNOQ_INSTRUCTIONS

    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    markdown_fields_by_tool = {
        "harnoq_source_requirement_create": ["raw_content"],
        "harnoq_source_requirement_append_content": ["raw_content_to_append"],
        "harnoq_source_requirement_update": ["raw_content"],
        "harnoq_feature_create": ["description"],
        "harnoq_feature_update": ["description"],
        "harnoq_constitution_update": [
            "system_nature", "fixed_stack", "quality_bar", "ui_conventions",
            "nfr_baseline", "boundaries",
        ],
        "harnoq_design_case_update": [
            "problem_statement", "design_scope", "non_goals", "assumptions",
            "constraints", "selected_solution", "selection_reason", "risks",
            "test_strategy", "acceptance_criteria",
        ],
        "harnoq_task_create": [
            "goal", "acceptance_criteria", "constraints", "non_goals",
        ],
    }
    for tool_name, fields in markdown_fields_by_tool.items():
        tool = tools[tool_name]
        normalized_docstring = " ".join(tool.description.split())
        assert md_snippet in normalized_docstring, (
            f"{tool_name} docstring missing the Markdown clause"
        )
        schema = tool.inputSchema
        for field in fields:
            prop = schema["properties"][field]
            assert md_clause in prop.get("description", ""), (
                f"{tool_name}.{field} inputSchema missing the Markdown clause"
            )


# --- read path -------------------------------------------------------------

def test_project_context_reads_source_requirements(client, harnoq):
    _project(client)
    harnoq.harnoq_source_requirement_create(
        project_number="P1", title="初始需求",
        source_type="initial_document", raw_content="Users need photo search.",
    )

    env = harnoq.harnoq_project_context("P1")

    assert env["status"] == "ready"
    assert env["object_id"] == "P1"
    assert len(env["source_requirements"]) == 1
    assert env["source_requirements"][0]["source_requirement_number"] == "P1-INPUT-1"
    assert "requirement_items" not in env
    # Only the current stage's method is sent. The full map used to ride along
    # as reference — five methods, half the response — competing for attention
    # with the project's own content, which is a fifth of the size.
    assert "stage_methods" not in env
    assert env["active_stage_method"] is not None


def test_glossary_get_is_available_through_mcp(client, harnoq):
    _project(client)
    created = harnoq.harnoq_glossary_create(
        project_number="P1",
        term_native="照片",
        term_en="photo",
        definition_en="A captured image.",
        avoid="ledger",
    )
    assert created["status"] == "succeeded"
    glossary = harnoq.harnoq_glossary_get("P1")["glossary"]
    assert glossary == [
        {
            "id": glossary[0]["id"],
            "project_id": glossary[0]["project_id"],
            "term_native": "照片",
            "term_en": "photo",
            "definition_en": "A captured image.",
            "avoid": "ledger",
        }
    ]


def test_integration_inventory_validator_through_mcp(client, harnoq):
    project = _project(client, name="Reconciliation tool")
    _capability_task(client, project["id"])
    context = harnoq.harnoq_project_context(project["project_number"])
    design = context["design_cases"][0]
    design_number = design["design_number"]

    result = harnoq.harnoq_integration_inventory_validate(
        project_number=project["project_number"],
        external_resources=server.IntegrationExternalResources(
            scan_evidence_keys=["E1"], resources=[],
            design_audits=[server.IntegrationResourceDesignAudit(
                design_number=design_number, resource_keys=[],
                no_resources_reason="Only local records are used by this batch job.",
            )],
        ),
        found_in_round=1,
        evidence=[server.IntegrationEvidenceRecord(
            key="E1", role="source-scan", command="rg -n BatchJob src", exit_code=0,
            stdout="src/job.py:12:class BatchJob", stderr="",
        )],
        production_entries=[],
        design_coverage=[server.IntegrationDesignCoverageRecord(
            key=design_number,
            design_number=design_number,
            selected_solution_sha256=hashlib.sha256(
                design["selected_solution"].encode()
            ).hexdigest(),
            design_scope_sha256=hashlib.sha256(
                design["design_scope"].encode()
            ).hexdigest(),
            declared_part_keys=["PART-1"],
            seam_keys=[],
        )],
        source_scans=[server.IntegrationSourceScanRecord(
            key="SCAN-1", language="python", roots=["src"],
            scan_evidence_key="E1", collision_keys=[],
            different_name_candidate_keys=[],
        )],
        declared_parts=[server.IntegrationDeclaredPartRecord(
            key="PART-1", design_number=design_number, name="BatchJob",
            declaration=design["selected_solution"],
        )],
        assembly_rows=[server.IntegrationAssemblyRow(
            key="PART-1", reachable=False, entry_keys=[], path=[],
            gap_key="GAP-1", missing_edge="production scheduler -> BatchJob",
            evidence_keys=["E1"],
        )],
        seams=[],
        identifier_collisions=[],
        duplicate_candidates=[],
        proposed_items=[server.IntegrationProposedItemRecord(
            key="GAP-1", kind="assembly",
            subject="BatchJob has no operational production path.",
            source=(
                f"{design_number} selected_solution; missing edge: "
                "production scheduler -> BatchJob"
            ),
            found_in_round=1,
        )],
    )

    assert result["valid"] is True
    assert result["counts"]["expected_items"] == 1
    assert result["audit"]["all_checks_pass"] is True


def test_source_requirement_update_and_append(client, harnoq):
    _project(client)
    harnoq.harnoq_source_requirement_create(
        project_number="P1", title="初始需求",
        source_type="initial_document", raw_content="raw",
    )
    updated = harnoq.harnoq_source_requirement_update(
        project_number="P1", source_requirement_number="INPUT-1",
        raw_content="深化后的需求理解",
    )
    assert updated["status"] == "succeeded"
    appended = harnoq.harnoq_source_requirement_append_content(
        project_number="P1", source_requirement_number="INPUT-1",
        raw_content_to_append="补充一条",
    )
    assert appended["status"] == "succeeded"
    view = harnoq.harnoq_source_requirement_get("P1", "INPUT-1")[
        "source_requirement"
    ]
    assert "深化后的需求理解" in view["raw_content"]
    assert "补充一条" in view["raw_content"]


def test_mcp_read_tools_list_and_get_workflow_objects(client, harnoq):
    _project(client)
    harnoq.harnoq_source_requirement_create(
        project_number="P1", title="初始需求",
        source_type="initial_document", raw_content="raw",
    )
    harnoq.harnoq_feature_create(
        project_number="P1",
        name="登录功能",
    )
    harnoq.harnoq_design_case_create(
        project_number="P1", feature_number="FEATURE-1",
    )
    harnoq.harnoq_design_case_update(
        project_number="P1", design_number="DESIGN-1",
        problem_statement="Users need login.", selected_solution="Email login.",
        selection_reason="Smallest viable scope.", risks="Credential stuffing.",
        test_strategy="Auth unit tests.", acceptance_criteria="User can log in.",
    )
    harnoq.harnoq_design_case_confirm(
        project_number="P1", design_number="DESIGN-1",
    )
    harnoq.harnoq_task_create(
        project_number="P1", design_number="DESIGN-1",
        title="Build login", task_type="feature",
        goal="do it", acceptance_criteria="works",
        constraints="none", non_goals="no SSO",
    )

    inputs = harnoq.harnoq_source_requirement_list("P1")
    assert [x["source_requirement_number"] for x in inputs["source_requirements"]] == ["P1-INPUT-1"]
    assert harnoq.harnoq_source_requirement_get("P1", "INPUT-1")[
        "source_requirement"
    ]["title"] == "初始需求"

    features = harnoq.harnoq_feature_list("P1")
    assert [x["feature_number"] for x in features["features"]] == ["P1-FEATURE-1"]
    assert harnoq.harnoq_feature_get("P1", "FEATURE-1")[
        "feature"
    ]["name"] == "登录功能"

    designs = harnoq.harnoq_design_case_list("P1")
    assert [x["design_number"] for x in designs["design_cases"]] == ["P1-DESIGN-1"]
    designs_for_feature = harnoq.harnoq_design_case_list("P1", "FEATURE-1")
    assert [x["design_number"] for x in designs_for_feature["design_cases"]] == ["P1-DESIGN-1"]
    assert harnoq.harnoq_design_case_get("P1", "DESIGN-1")[
        "design_case"
    ]["design_number"] == "P1-DESIGN-1"

    design_detail = harnoq.harnoq_design_case_get("P1", "DESIGN-1")["design_case"]
    assert design_detail["task_numbers"] == ["P1-TASK-1"]


def test_feature_block_tags_and_dependency_edges_through_mcp(client, harnoq):
    """The feature create/update tools must carry orientation/foundation_kind/
    subdomain_type/is_spike and depends_on_feature_numbers through to Harnoq. A feature
    carries no INPUT-* link at all (see app/models/feature.py)."""
    _project(client)

    engine = harnoq.harnoq_feature_create(
        project_number="P1", name="核心引擎",
        orientation="foundation", foundation_kind="architecture",
        subdomain_type="core", is_spike=True,
    )
    assert engine["status"] == "succeeded"
    engine_number = engine["object_id"]
    engine_view = harnoq.harnoq_feature_get("P1", engine_number)["feature"]
    assert engine_view["orientation"] == "foundation"
    assert engine_view["foundation_kind"] == "architecture"
    assert engine_view["subdomain_type"] == "core"
    assert engine_view["is_spike"] is True

    platform = harnoq.harnoq_feature_create(
        project_number="P1", name="平台底座",
        orientation="foundation", subdomain_type="generic",
        depends_on_feature_numbers=[engine_number],
    )
    platform_number = platform["object_id"]
    platform_view = harnoq.harnoq_feature_get("P1", platform_number)["feature"]
    assert platform_view["depends_on_feature_numbers"] == [engine_number]

    # A cycle is rejected by the server.
    with pytest.raises(server.HarnoqError):
        harnoq.harnoq_feature_update(
            project_number="P1", feature_number=engine_number,
            depends_on_feature_numbers=[platform_number],
        )


def test_foundation_design_through_mcp(client, harnoq):
    """harnoq_design_case_create needs only a feature_number — a foundation
    block's design is created the same way as any other."""
    _project(client)
    feature = harnoq.harnoq_feature_create(
        project_number="P1", name="缩略图存储",
        orientation="foundation", subdomain_type="generic",
    )
    feature_number = feature["object_id"]

    design = harnoq.harnoq_design_case_create(
        project_number="P1", feature_number=feature_number,
    )
    assert design["status"] == "succeeded"
    design_view = harnoq.harnoq_design_case_get("P1", design["object_id"])[
        "design_case"
    ]
    assert design_view["feature_number"] == feature_number


# --- full chain: INPUT -> FEATURE -> DESIGN -> TASK -------------------------

def test_full_pipeline_through_mcp_tools(client, harnoq):
    _project(client)
    harnoq.harnoq_source_requirement_create(
        project_number="P1", title="初始需求",
        source_type="initial_document", raw_content="raw",
    )
    harnoq.harnoq_feature_create(
        project_number="P1", name="登录功能",
    )
    harnoq.harnoq_design_case_create(
        project_number="P1", feature_number="FEATURE-1",
    )
    harnoq.harnoq_design_case_update(
        project_number="P1", design_number="DESIGN-1",
        problem_statement="Users need login.", selected_solution="Email login.",
        selection_reason="Smallest viable scope.", risks="Credential stuffing.",
        test_strategy="Auth unit tests.", acceptance_criteria="User can log in.",
    )
    confirmed = harnoq.harnoq_design_case_confirm(
        project_number="P1", design_number="DESIGN-1",
    )
    assert confirmed["status"] == "succeeded"

    task = harnoq.harnoq_task_create(
        project_number="P1", design_number="DESIGN-1",
        title="Build login", task_type="feature",
        goal="do it", acceptance_criteria="works",
        constraints="none", non_goals="no SSO",
    )
    assert task["status"] == "succeeded"


@pytest.mark.parametrize('serialized', [False, True])
def test_mcp_runtime_accepts_qa_object_without_double_encoding(client, harnoq, serialized):
    from tests.test_integration_inventory_validator import _integration_ready_project
    from app.repositories import delivery_stage_run_repo
    project, _ = _integration_ready_project(client)
    run = harnoq.harnoq_delivery_stage_run_start(project['project_number'], 'integration', 'merged', 'design')
    report = {'role': 'qa', 'item_numbers': [], 'command': 'pytest -q',
              'output': '1 passed', 'exit_code': 0, 'code_revision': 'merged'}
    checks = [{'command': 'pytest -q', 'output': '1 passed',
               'exit_code': 0, 'code_revision': 'merged'}]
    asyncio.run(server.mcp.call_tool('harnoq_delivery_stage_run_update', {
        'project_number': project['project_number'], 'run_number': run['run_number'],
        'evidence': json.dumps(report) if serialized else report,
        'integration_rerun_evidence': json.dumps(checks) if serialized else checks,
    }))
    saved = delivery_stage_run_repo.get_by_number(project['id'], run['run_number'])
    assert json.loads(saved.evidence) == report
    assert json.loads(saved.integration_rerun_evidence) == checks
