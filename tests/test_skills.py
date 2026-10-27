"""Skill infrastructure + contract layer + read-only skill tests."""

import yaml

from app.core.errors import ValidationError
from app.models.skill_invocation import SkillInvocation
from app.repositories import skill_invocation_repo


# ---------------------------------------------------------------------------
# Model + repository
# ---------------------------------------------------------------------------


def _make_project(client, name="P"):
    return client.post("/projects", json={"name": name}).get_json()


def test_skill_invocation_to_dict(app):
    with app.app_context():
        project = _create_project_row()
        invocation = skill_invocation_repo.create(
            project=project.id,
            skill_name="harnoq_source_requirement",
            actor="alice",
            session_id="s1",
            idempotency_key="k1",
            target_type="source_requirement",
            target_id=42,
            request_summary="req",
            response_summary="resp",
            status="succeeded",
        )
        data = invocation.to_dict()
        assert set(data) == {
            "id", "project_id", "skill_name", "actor", "session_id",
            "idempotency_key", "target_type", "target_id", "request_summary",
            "response_summary", "status", "created_at",
        }
        assert data["target_id"] == 42
        assert data["status"] == "succeeded"
        assert data["created_at"] is not None


def test_repo_get_and_list_by_project(app):
    with app.app_context():
        project = _create_project_row()
        a = skill_invocation_repo.create(
            project=project.id, skill_name="s1", actor="a", session_id="x",
            idempotency_key="k1", status="succeeded",
        )
        b = skill_invocation_repo.create(
            project=project.id, skill_name="s2", actor="a", session_id="x",
            idempotency_key="k2", status="failed",
        )

        assert skill_invocation_repo.get(a.id).id == a.id
        assert skill_invocation_repo.get(99999) is None

        listed = skill_invocation_repo.list_by_project(project.id)
        assert [row.id for row in listed] == [a.id, b.id]




# ---------------------------------------------------------------------------
# Migration 005
# ---------------------------------------------------------------------------


def test_005_migration_creates_skill_invocation_table(app):
    db = app.config["DATABASE"]
    assert db.table_exists("skill_invocation") is True


# ---------------------------------------------------------------------------
# Contract layer: run_write_skill
# ---------------------------------------------------------------------------


def _create_project_row():
    from app.repositories import project_repo
    return project_repo.create(name="P")


def test_run_write_skill_runs_even_with_missing_identity(app):
    """actor/session_id/idempotency_key are optional audit-trail fields, not a
    gate — a missing one must NOT block the write (there is no rejection path
    anymore: see skill_service module docstring)."""
    from app.services import skill_service

    calls = []

    def write_action():
        calls.append(1)
        return ("design_case", 1, "created", [], None)

    with app.app_context():
        project = _create_project_row()
        envelope = skill_service.run_write_skill(
            project_id=project.id, skill_name="harnoq_design_case",
            actor="", session_id="", idempotency_key="",
            action="create", payload={"x": 1}, write_action=write_action,
        )
        assert envelope["status"] == "succeeded"
        assert calls == [1]
        recorded = skill_invocation_repo.list_by_project(project.id)
        assert len(recorded) == 1
        assert recorded[0].status == "succeeded"
        # missing actor/session recorded as a conservative fallback, not blank
        assert recorded[0].actor
        assert recorded[0].session_id


def test_run_write_skill_always_reruns_with_identical_params(app):
    """No idempotency dedup: two calls with the SAME (project, actor, skill,
    idempotency_key, payload) both execute write_action and both succeed.

    This is the fix for three separate production incidents where a
    payload-hash 'auto-' key silently replayed a stale success instead of
    re-running: a 2nd DESIGN-* create under one FEATURE replayed the 1st
    design instead of minting a new one; a re-confirm after a reopen replayed
    a stale success without running confirm(); a delete after a restore
    replayed the first delete and left the row live. The root cause was that
    a payload hash cannot tell "the same logical operation retried" from "the
    same shape of operation done again on purpose" — patching it action by
    action fixed each incident but not the class, so dedup is gone entirely."""
    from app.services import skill_service

    calls = []

    def make_write_action(object_id):
        def write_action():
            calls.append(object_id)
            return ("design_case", object_id, "created", ["q?"], "review it")

        return write_action

    with app.app_context():
        project = _create_project_row()
        common = dict(
            project_id=project.id, skill_name="harnoq_design_case",
            actor="alice", session_id="s1", idempotency_key="k1",
            action="create", payload={"x": 1},
        )

        first = skill_service.run_write_skill(write_action=make_write_action(99), **common)
        second = skill_service.run_write_skill(write_action=make_write_action(100), **common)

        assert first["status"] == "succeeded" and first["object_id"] == 99
        assert second["status"] == "succeeded" and second["object_id"] == 100
        assert calls == [99, 100]  # both ran, neither was a replay

        invocations = skill_invocation_repo.list_by_project(project.id)
        assert len(invocations) == 2
        assert [row.status for row in invocations] == ["succeeded", "succeeded"]


def test_run_write_skill_records_failed_and_reraises(app):
    from app.services import skill_service

    def write_action():
        raise ValidationError("bad payload")

    with app.app_context():
        project = _create_project_row()
        try:
            skill_service.run_write_skill(
                project_id=project.id, skill_name="harnoq_design_case",
                actor="alice", session_id="s1", idempotency_key="k1",
                action="create", payload={}, write_action=write_action,
            )
            assert False, "expected ValidationError"
        except ValidationError:
            pass

        invocations = skill_invocation_repo.list_by_project(project.id)
        assert len(invocations) == 1
        assert invocations[0].status == "failed"


def test_run_write_skill_retries_after_a_failure(app):
    """No cache at all means a failed call leaves nothing to poison a later
    retry with the same params: it runs write_action fresh again."""
    from app.services import skill_service

    calls = []

    def flaky_write_action():
        calls.append(1)
        if len(calls) == 1:
            raise ValidationError("bad payload")
        return ("design_case", 5, "created", [], None)

    with app.app_context():
        project = _create_project_row()
        common = dict(
            project_id=project.id, skill_name="harnoq_design_case",
            actor="alice", session_id="s1", idempotency_key="k1",
            action="create", payload={},
        )
        try:
            skill_service.run_write_skill(write_action=flaky_write_action, **common)
            assert False, "expected ValidationError"
        except ValidationError:
            pass

        success = skill_service.run_write_skill(write_action=flaky_write_action, **common)
        assert success["status"] == "succeeded"
        assert len(calls) == 2


# ---------------------------------------------------------------------------
# Contract layer: run_read_skill
# ---------------------------------------------------------------------------


def test_run_read_skill_falls_back_when_missing_identity(app):
    from app.services import skill_service

    ran = []

    def read_action():
        ran.append(1)
        return skill_service.build_envelope("context", 1, "ready", "ok")

    with app.app_context():
        project = _create_project_row()
        envelope = skill_service.run_read_skill(
            project_id=project.id, skill_name="harnoq_project_context",
            actor="", session_id="", action="read", payload={}, read_action=read_action,
        )
        assert envelope["status"] == "ready"
        assert ran == [1]
        invocations = skill_invocation_repo.list_by_project(project.id)
        assert len(invocations) == 1
        assert invocations[0].status == "succeeded"
        assert invocations[0].actor == "openwebui"
        assert invocations[0].session_id == "openwebui-read"


def test_run_read_skill_success_records_invocation(app):
    from app.services import skill_service

    def read_action():
        return skill_service.build_envelope(
            "context", 1, "ready", "2 units", open_questions=["q?"], next_step="design",
        )

    with app.app_context():
        project = _create_project_row()
        envelope = skill_service.run_read_skill(
            project_id=project.id, skill_name="harnoq_project_context",
            actor="alice", session_id="s1", action="read", payload={},
            read_action=read_action,
        )
        assert envelope["object_type"] == "context"
        assert envelope["open_questions"] == ["q?"]
        invocations = skill_invocation_repo.list_by_project(project.id)
        assert len(invocations) == 1
        assert invocations[0].status == "succeeded"
        assert invocations[0].skill_name == "harnoq_project_context"


# ---------------------------------------------------------------------------
# Read-only endpoints
# ---------------------------------------------------------------------------


def test_project_context_endpoint_aggregates_and_records(app, client):
    project = _make_project(client)
    pid = project["id"]
    pnum = project["project_number"]
    client.post(f"/projects/{pid}/source-requirements", json={
        "title": "login request", "source_type": "new_feature", "raw_content": "# login",
    })

    response = client.post("/skills/project-context", json={
        "project_number": pnum, "actor": "alice", "session_id": "s1",
    })
    assert response.status_code == 200
    data = response.get_json()
    assert data["object_type"] == "context"
    assert data["object_id"] == pnum
    assert data["status"] == "ready"
    assert "1 source requirements" in data["change_summary"]
    assert len(data["source_requirements"]) == 1
    assert data["source_requirements"][0]["raw_content"] == "# login"
    input_actions = data["source_requirements"][0]["ai_available_actions"]
    assert (
        input_actions["append_text_to_this_input"]["tool_name"]
        == "harnoq_source_requirement_append_content"
    )
    assert input_actions["append_text_to_this_input"]["must_call_tool"] is True
    assert "requirement_items" not in data
    assert isinstance(data["features"], list)
    assert isinstance(data["design_cases"], list)
    assert "feature_map" in data
    assert "task_plans" not in data
    assert isinstance(data["development_tasks"], list)
    assert data["project"]["id"] == pid
    # The project constitution rides along in project-context (None until one
    # exists) so every stage can read the governance it must obey.
    assert "constitution" in data
    assert data["available_tools"]["write_tools"]["source_requirement"]["update"] == (
        "harnoq_source_requirement_update"
    )
    assert data["available_tools"]["write_tools"]["source_requirement"]["append_content"] == (
        "harnoq_source_requirement_append_content"
    )
    assert data["business_id_rules"]["source_requirement"] == "P*-INPUT-*"
    assert data["business_id_rules"]["design_case"] == "P*-DESIGN-*"
    assert data["business_id_rules"]["integration_item"] == "P*-INTEG-*"
    assert data["business_id_rules"]["system_item"] == "P*-SYS-*"
    assert any(
        "copy its fully qualified P*-... number exactly" in note
        for note in data["business_id_rules"]["notes"]
    )
    assert any(
        "Requirement mining and feature composition are two "
        "separate steps" in item for item in data["workflow_guidance"]
    )
    # The full chain must reach the agent on every read: a stale guidance that
    # stopped short of FEATURE->DESIGN is what made agents skip stages.
    assert any("THE PIPELINE, in order" in item for item in data["workflow_guidance"])
    # Stage-specific methods are delivered dynamically (option B) so the system
    # prompt can stay lean — and ONLY the current stage's, hoisted to the top.
    # The full map used to ride along as reference material: five methods,
    # ~75k characters, half the whole response, of which the current one was
    # already hoisted. Nothing consumed it, and every character of it competed
    # for attention with the project's own content (a fifth of the size).
    assert "stage_methods" not in data
    assert data["active_stage_method"] is not None
    from app.api.skills import _stage_methods

    stage_methods = _stage_methods()
    # P2 (docs/cn/design-evolution.md#delivery) added two delivery
    # gates -- "integration" and "system" -- so this closed set legitimately
    # grew; it is not a routing bug, the two are meant to exist.
    assert set(stage_methods) == {
        "requirement_decomposition", "constitution", "design",
        "task_planning", "change", "integration", "system",
    }
    # Constitution consolidation: a thin governance step between confirmed INPUT-* and
    # design — aggregate scattered governance, elevate the mis-modelled NFR / UI
    # items, confirm, then govern downstream.
    constitution = stage_methods["constitution"]
    assert len(constitution["rounds"]) == 2
    assert set(constitution["source_map"]) == {
        "system_nature", "fixed_stack", "quality_bar", "ui_conventions",
        "nfr_baseline", "boundaries",
    }
    assert any("Consolidate, do not re-invent" in item for item in constitution["principles"])
    # Performance is governance harvested into nfr_baseline, never a requirement.
    assert any(
        "nfr_baseline is governance, never a requirement" in item
        for item in constitution["principles"]
    )
    assert "harnoq_constitution_confirm" in constitution["write"]
    decomposition = stage_methods["requirement_decomposition"]
    # INPUT-* has no structured fields/enums anymore (free text); confirmation
    # is a lifecycle gate, not a required-field checklist. The old REQ table-output shape (enum_constraints, output_table_
    # columns, layer_guidance, feature_area_checklist) does not apply.
    assert "enum_constraints" not in decomposition
    assert "output_table_columns" not in decomposition
    assert len(decomposition["rounds"]) == 3
    # The deleted REQUIRED_FOR_CONFIRM checklist forced the LLM to cover goal /
    # acceptance / constraints; the prompt must now actively interrogate for
    # the same depth (see docs/cn/design-evolution.md §2).
    assert "充分澄清需求" in decomposition["principles"][0]
    assert "未明确表达的业务目标" in decomposition["principles"][0]
    assert any("System-shape first" in item for item in decomposition["principles"])
    assert any("never start from UI screens" in item for item in decomposition["principles"])
    assert "pressure surface" in decomposition["rounds"][0]
    assert "is genuinely thorough" in decomposition["rounds"][0]
    # Dependencies / build order do NOT live on the requirement — that lives at
    # the Feature level (docs/cn/design-evolution.md §5).
    assert "do NOT live on the requirement" in decomposition["principles"][-1]
    assert "carries no dependency graph" in decomposition["write"]
    # Performance is surfaced as a register but NOT persisted as a requirement:
    # it is governance that lands in the constitution's nfr_baseline.
    assert "performance_register" in decomposition
    assert "NOT a requirement" in decomposition["performance_register"]
    assert "nfr_baseline" in decomposition["performance_register"]
    # design and task_planning are fleshed out (not TBD placeholders): each has
    # the same three-round, challenge-first shape as decomposition.
    design = stage_methods["design"]
    assert len(design["rounds"]) == 5
    # Confirm is gated inside the final round: fill leaves a DRAFT, the written
    # design is presented back, and a fresh approval of the text is required
    # before confirm — never fill-and-confirm in one turn.
    assert "leaving each as a DRAFT" in design["rounds"][4]
    assert "FRESH explicit approval" in design["rounds"][4]
    assert "fill-and-confirm" in design["rounds"][4]
    assert design["feature_map_confirmation_required"] is True
    # Design is Feature-by-Feature over a Feature map defined by information
    # hiding, not one-REQ-at-a-time. FEATURE (block / ownership unit) and
    # DESIGN-* (Parnas module inside it) stay two granularities.
    assert any(
        "FEATURE-BY-FEATURE" in item and "information hiding" in item
        for item in design["principles"]
    )
    assert any("Feature map" in item for item in design["principles"])
    assert "FEATURE MAP" in design["rounds"][0]
    assert any(
        "two DIFFERENT granularities" in item and "information hiding" in item
        for item in design["principles"]
    )
    # Every block carries two orthogonal tags (orientation + subdomain_type),
    # and core blocks are spiked (is_spike).
    assert any(
        "orientation" in item and "subdomain_type" in item and "is_spike" in item
        for item in design["principles"]
    )
    assert any(
        "foundation" in item and "vertical" in item for item in design["principles"]
    )
    assert any(
        "core" in item and "supporting" in item and "generic" in item
        for item in design["principles"]
    )
    # No FEATURE (nor DESIGN-*) carries a formal INPUT-* link at all — not
    # even foundation/generic blocks get a fabricated platform INPUT-*.
    assert any(
        "Neither carries a formal INPUT-* link" in item
        for item in design["principles"]
    )
    # Round 1 draws + tags the blocks; what waits for what is a SEPARATE round,
    # so the cut is not made while chasing a tidy build sequence.
    assert "DRAW THE FEATURE MAP" in design["rounds"][0]
    assert "depends_on_feature_numbers" not in design["rounds"][0]
    assert "depends_on_feature_numbers" in design["rounds"][1]
    assert "MUST BE FINISHED BEFORE WORK ON A CAN START" in design["rounds"][1]
    # Persistence is designed only for what the block actually owns (a pure-UI
    # block gets no fabricated table).
    assert any("ACTUALLY OWNS" in item for item in design["principles"])
    # Design is gated on a confirmed constitution and must read it as given
    # (fixed stack / NFR / UI conventions come from it, not re-derived).
    assert "constitution is confirmed" in design["rounds"][0]
    # UI gets a dedicated foundation DESIGN-* plus a separate page DESIGN-* per
    # page-bearing feature (capability vs page are two DESIGN-*).
    assert any("UI Foundation" in item for item in design["principles"])
    assert any("UI Foundation DESIGN-*" in item for item in design["principles"])
    assert any("页面 DESIGN-*" in item for item in design["principles"])
    # Page design unit is the screen: several capabilities on one screen share
    # ONE page DESIGN-* that covers only page-specific layout/interaction and
    # references the functional DESIGN-*(s) it renders.
    assert any(
        "SAME screen" in item and "covers only the" in item
        for item in design["principles"]
    )
    # A capability several modules need is referenced, not buried in one of them
    # (guards buried shared logic) — but by whether it changes for its own
    # reasons, not by counting callers, which manufactures strata.
    assert any(
        "REFERENCED, not buried" in item and "do NOT turn this into consumer-counting" in item
        for item in design["principles"]
    )
    assert any("no direct page" in item for item in design["principles"])
    assert any("page type" in item for item in design["principles"])
    # Designs must land: the buildable shape is the parts plus each part's
    # interface and the uses relation, with a depth bar per confirm-required
    # field — so neither a task planner nor a coder invents architecture.
    assert any("buildable shape" in item for item in design["principles"])
    assert "field_standards" in design
    assert set(design["required_for_confirm"]) <= set(design["field_standards"])
    assert "ITS INTERFACE" in design["field_standards"]["selected_solution"]
    assert design["required_for_confirm"] == [
        "problem_statement", "selected_solution", "selection_reason",
        "risks", "test_strategy", "acceptance_criteria",
    ]
    task_planning = stage_methods["task_planning"]
    assert len(task_planning["rounds"]) == 3
    assert task_planning["task_required_fields"] == [
        "title", "task_type", "goal", "acceptance_criteria", "constraints",
        "non_goals",
    ]
    # Sizing is by agent CONTEXT, not human PR size: default one DESIGN-* = ONE
    # task, split only on context overflow, and over-splitting relocates
    # validation to regression.
    assert "orient_first" in task_planning
    assert "task_field_standards" in task_planning
    assert task_planning["anti_patterns"]
    assert any("one DESIGN-* = ONE task" in item for item in task_planning["principles"])
    assert any(
        "context" in item and "overflow" in item.lower()
        for item in task_planning["principles"]
    )
    assert any("regression" in item.lower() for item in task_planning["principles"])
    assert any("qa_passed" in item for item in task_planning["principles"])
    # A task is a PR-sized unit bound to exactly one DESIGN-* (the six marks).
    assert any(
        "PR-sized" in item and "ONE DESIGN-*" in item
        for item in task_planning["principles"]
    )
    # Tasks are PRESENTED in the order the Features wait, read from context
    # rather than guessed — presentation only; the gate lives at claim (see
    # test_task_planning_does_not_forbid_creating_ahead_of_upstream).
    assert any(
        "the order the Features wait" in item and "not guess the order" in item
        for item in task_planning["principles"]
    )
    # Greenfield bootstrap folds into the first task (no separate "set up
    # project" task); is_spike does NOT drive task splitting.
    assert any(
        "Bootstrap folds into the first task" in item
        for item in task_planning["principles"]
    )
    for method in stage_methods.values():
        assert "TBD" not in repr(method)
    # The guidance points at the one method that IS sent, and says the others
    # are not — otherwise "apply the entry matching your stage" reads as an
    # instruction to go find something that is no longer in the response.
    assert any(
        "active_stage_method IS the method for the stage you are in" in item
        for item in data["workflow_guidance"]
    )
    assert any(
        "Other stages' methods are not sent" in item
        for item in data["workflow_guidance"]
    )
    assert any(
        "Requirement mining and feature composition are two "
        "separate steps" in item for item in data["workflow_guidance"]
    )
    assert set(data["feature_map"]) == {
        "features",
        "foundation_features",
        "pending_design_feature_numbers",
        "all_features_designed",
    }
    assert data["feature_map"]["foundation_features"] == {
        "architecture": None,
        "ui": None,
    }
    # Cross-cutting: every stage must orient to the project first.
    assert any(
        "Orient before any stage" in item for item in data["workflow_guidance"]
    )
    # Cross-cutting: revising a confirmed upstream obliges the agent to walk the
    # downstream TASK-* and put keep/edit/delete/leave-unstarted to the
    # user — nothing flags a task, and the stage indicator will not switch to
    # "design", so this must ride in the always-on workflow guidance, not a
    # stage method the reshaping agent never reads.
    assert any(
        "carry the change DOWN" in item for item in data["workflow_guidance"]
    )
    # "不做了" is deletion now (no cancelled/rejected status), so delete/restore
    # are exposed for the gate entities, with mandatory user confirmation.
    assert data["write_policy"]["deletion"]["delete_tools_exposed"] is True
    assert data["write_policy"]["deletion"]["user_confirmation_required"] is True
    assert any(
        "Use the returned envelope status to decide whether a write succeeded"
        in item for item in data["write_policy"]["execution_truth"]
    )

    with app.app_context():
        rows = skill_invocation_repo.list_by_project(pid)
        assert len(rows) == 1
        assert rows[0].status == "succeeded"
        assert rows[0].skill_name == "harnoq_project_context"


def test_openapi_json_rewrites_servers_url_to_request_host(client):
    response = client.get("/openapi.json", headers={"Host": "10.0.0.18:5000"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["servers"] == [{
        "url": "http://10.0.0.18:5000",
        "description": "Harnoq runtime server for this request origin",
    }]
    assert (
        "/skills/source-requirement-append-content" in payload["paths"]
    )
    append_op = payload["paths"]["/skills/source-requirement-append-content"]["post"]
    assert append_op["operationId"] == "harnoq_source_requirement_append_content"
    info_description = payload["info"]["description"]
    assert "AI operating notes" in info_description
    assert "returned envelope status" in info_description
    assert "source of" in info_description


def test_openapi_yaml_rewrites_servers_url_to_request_host(client):
    response = client.get("/openapi.yaml", headers={"Host": "10.0.0.18:5000"})

    assert response.status_code == 200
    payload = yaml.safe_load(response.get_data(as_text=True))
    assert payload["servers"] == [{
        "url": "http://10.0.0.18:5000",
        "description": "Harnoq runtime server for this request origin",
    }]


def test_openapi_documents_manager_only_conversational_pre_review(client):
    payload = client.get("/openapi.json").get_json()
    paths = payload["paths"]
    approve = paths[
        "/projects/{project_number}/tasks/{task_number}/approve-pre-review"
    ]["post"]
    reject = paths[
        "/projects/{project_number}/tasks/{task_number}/reject-pre-review"
    ]["post"]

    assert approve["operationId"] == "harnoq_task_approve_pre_review"
    assert "explicitly approved" in approve["description"]
    schema = reject["requestBody"]["content"]["application/json"]["schema"]
    assert schema["required"] == ["comment"]
    # One destination, so no target to choose (migration 054).
    assert "rejection_target" not in schema["properties"]


def test_openapi_request_schemas_declare_markdown_vs_plain_fields(client):
    """Every Markdown-classified content field on a *Request schema must carry
    the standard Markdown clause so a generic OpenAPI-driven caller (not just
    the MCP tools) knows to write real line breaks instead of literal \\n."""
    response = client.get("/openapi.yaml", headers={"Host": "10.0.0.18:5000"})
    assert response.status_code == 200
    payload = yaml.safe_load(response.get_data(as_text=True))
    schemas = payload["components"]["schemas"]

    md_snippet = "use real line breaks, never the literal characters backslash-n"
    markdown_fields_by_schema = {
        "SourceRequirementCreateRequest": ["raw_content"],
        "SourceRequirementUpdateRequest": ["raw_content"],
        "SourceRequirementAppendContentRequest": ["raw_content_to_append"],
        "FeatureCreateRequest": ["description"],
        "FeatureUpdateRequest": ["description"],
        "DeliveryItemCreateRequest": ["subject"],
        "DeliveryItemUpdateRequest": ["subject"],
        "ConstitutionUpdateRequest": [
            "system_nature", "fixed_stack", "quality_bar", "ui_conventions",
            "nfr_baseline", "boundaries",
        ],
        "DesignCaseUpdateRequest": [
            "problem_statement", "design_scope", "non_goals", "assumptions",
            "constraints", "selected_solution", "selection_reason", "risks",
            "test_strategy", "acceptance_criteria",
        ],
        "DesignCaseCreateTaskRequest": [
            "goal", "acceptance_criteria", "constraints", "non_goals",
        ],
        "TaskUpdateRequest": [
            "goal", "acceptance_criteria", "constraints", "non_goals",
        ],
    }
    for schema_name, fields in markdown_fields_by_schema.items():
        properties = schemas[schema_name]["properties"]
        for field in fields:
            description = properties[field].get("description", "")
            assert md_snippet in description, (
                f"{schema_name}.{field} openapi schema missing the Markdown clause"
            )

    # The RequirementItem layer has been removed entirely.
    assert "RequirementItemCreateRequest" not in schemas
    assert "RequirementItemUpdateRequest" not in schemas

    # The constitution-update path used to fall back to the untyped generic
    # SkillRequest; it must now reference its own described schema.
    constitution_update_schema_ref = payload["paths"]["/skills/constitution-update"][
        "post"
    ]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert constitution_update_schema_ref.endswith("ConstitutionUpdateRequest")


def test_llm_facing_guidance_uses_canonical_terminology_and_current_gates():
    """Prompt-facing surfaces must not reintroduce retired metaphors or the
    stale claim that INPUT confirmation is optional. Exact examples belong in
    docs/cn/design-evolution.md#terminology; this test protects the high-risk wording that
    previously led clients to skip lifecycle gates or invent architecture
    categories."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    surfaces = [
        root / "app/api/stage_methods.py",
        root / "app/api/skills.py",
        root / "harnoq_mcp/server.py",
        root / "skills/openapi.yaml",
        root / "skills/openwebui-system-prompt.md",
    ]
    rendered = "\n".join(path.read_text() for path in surfaces).lower()
    retired_terms = (
        "横向地基", "纵向切片", "收割", "聊深聊透", "dependency spine",
        "required-blocks", "forcing step", "block map", "feature partition", "ddd-lite",
    )
    assert not [term for term in retired_terms if term in rendered]
    assert "every live input-* must be confirmed" in rendered
    assert "there is no taskplan entity" in rendered


def test_project_context_maps_each_design_to_its_created_tasks(client):
    """An agent must be able to see which TASK-* a DESIGN-* already has,
    without inferring it from any other object."""
    project_id, _, design_id = _make_confirmed_design(client)
    project = client.get(f"/projects/{project_id}").get_json()
    design_number = client.get(
        f"/design-cases/{design_id}"
    ).get_json()["design_number"]
    task_a = client.post(
        f"/design-cases/{design_id}/tasks",
        json={"title": "A", "task_type": "feature"},
    ).get_json()
    duplicate = client.post(
        f"/design-cases/{design_id}/tasks",
        json={"title": "B", "task_type": "feature"},
    )
    assert duplicate.status_code == 409

    context = client.post(
        "/skills/project-context",
        json={"project_number": project["project_number"]},
    ).get_json()
    design = next(
        item
        for item in context["design_cases"]
        if item["design_number"] == design_number
    )
    assert design["task_count"] == 1
    assert design["task_numbers"] == [task_a["task_number"]]


def test_project_context_endpoint_falls_back_when_missing_identity(app, client):
    project = _make_project(client)
    response = client.post("/skills/project-context", json={"project_number": project["project_number"]})
    assert response.status_code == 200
    assert response.get_json()["status"] == "ready"
    with app.app_context():
        rows = skill_invocation_repo.list_by_project(project["id"])
        assert len(rows) == 1
        assert rows[0].status == "succeeded"
        assert rows[0].actor == "openwebui"
        assert rows[0].session_id == "openwebui-read"


def test_project_context_endpoint_404_for_unknown_project(client):
    response = client.post("/skills/project-context", json={
        "project_number": "P-99999", "actor": "alice", "session_id": "s1",
    })
    assert response.status_code == 404


def test_task_progress_endpoint_aggregates_and_records(app, client):
    from app.models.task_enums import TASK_TYPES
    from app.repositories import development_task_repo

    project = _make_project(client)
    pid = project["id"]
    pnum = project["project_number"]
    unit = client.post(f"/projects/{pid}/source-requirements", json={
        "title": "u", "source_type": "new_feature",
    }).get_json()

    with app.app_context():
        development_task_repo.create(
            project=pid, title="T1", task_type=TASK_TYPES[0],
        )
        development_task_repo.create(
            project=pid, title="T2", task_type=TASK_TYPES[0],
        )

    response = client.post("/skills/task-progress", json={
        "project_number": pnum, "actor": "alice", "session_id": "s1",
    })
    assert response.status_code == 200
    data = response.get_json()
    assert data["object_type"] == "progress"
    assert len(data["tasks"]) == 2
    # Each task carries the live Feature-graph gate for display (see
    # docs/cn/design-evolution.md §5) — no stored dependencies.
    assert all("claim_ready" in task for task in data["tasks"])
    assert all("waiting_on_feature_numbers" in task for task in data["tasks"])
    gate = data["quality_gate_summary"]
    assert gate["total"] == 2
    assert data["available_tools"]["write_tools"]["development_task"]["create"] == (
        "harnoq_task_create"
    )
    assert data["write_policy"]["deletion"]["user_confirmation_required"] is True

    with app.app_context():
        rows = skill_invocation_repo.list_by_project(pid)
        assert len(rows) == 1
        assert rows[0].status == "succeeded"
        assert rows[0].skill_name == "harnoq_task_progress"


def test_task_progress_endpoint_404_for_unknown_project(client):
    response = client.post("/skills/task-progress", json={
        "project_number": "P-99999", "actor": "alice", "session_id": "s1",
    })
    assert response.status_code == 404


# ===========================================================================
# Write skills: source-requirement / design-case. Covers each action,
# SkillInvocation, the skill-layer guards, and that service-enforced limits
# still hold. Writes are NOT idempotent — see skill_service module docstring.
# ===========================================================================

_DESIGN_CONFIRM_FIELDS = {
    "problem_statement": "Users need login.",
    "selected_solution": "Email login.",
    "selection_reason": "Smallest viable scope.",
    "risks": "Credential stuffing.",
    "test_strategy": "Auth unit tests and login integration test.",
    "acceptance_criteria": "User can log in with valid credentials.",
}


def _write_body(project_number, action, payload, *, actor="alice", session="s1",
                key="k1"):
    return {
        "project_number": project_number,
        "actor": actor,
        "session_id": session,
        "idempotency_key": key,
        "action": action,
        "payload": payload,
    }


def _pnum(client, project_id):
    return client.get(f"/projects/{project_id}").get_json()["project_number"]


def _make_confirmed_design(client, project_id=None):
    """Walk the full pipeline to a confirmed design case under a fresh
    FEATURE. Returns (project_id, feature_id, design_id)."""
    if project_id is None:
        project_id = client.post("/projects", json={"name": "P"}).get_json()["id"]
    feature = client.post(
        f"/projects/{project_id}/features",
        json={"name": "F"},
    ).get_json()
    design = client.post(
        f"/projects/{project_id}/design-cases", json={"feature_id": feature["id"]}
    ).get_json()
    client.patch(f"/design-cases/{design['id']}", json=_DESIGN_CONFIRM_FIELDS)
    client.post(f"/design-cases/{design['id']}/confirm")
    return project_id, feature["id"], design["id"]


# --- harnoq_source_requirement ---------------------------------------------


def test_source_requirement_skill_create_always_creates(client, app):
    project = client.post("/projects", json={"name": "P"}).get_json()
    pid = project["id"]
    pnum = project["project_number"]
    payload = {"title": "Login feature request", "source_type": "new_feature"}

    first = client.post(
        "/skills/source-requirement",
        json=_write_body(pnum, "create", payload, key="k1"),
    )
    assert first.status_code == 200
    env = first.get_json()
    assert env["object_type"] == "source_requirement"
    assert env["status"] == "succeeded"
    target_id = env["object_id"]
    assert target_id is not None
    assert env["next_step"] is not None

    # SAME (actor, skill, key, payload) -> re-runs and creates a NEW object;
    # writes are not deduped (see skill_service module docstring).
    second = client.post(
        "/skills/source-requirement",
        json=_write_body(pnum, "create", payload, key="k1"),
    ).get_json()
    assert second["object_id"] != target_id
    assert second["status"] == "succeeded"

    # Different key -> also a new creation, different target_id.
    third = client.post(
        "/skills/source-requirement",
        json=_write_body(pnum, "create", payload, key="k2"),
    ).get_json()
    assert third["object_id"] not in (target_id, second["object_id"])

    with app.app_context():
        rows = skill_invocation_repo.list_by_project(pid)
        # Three calls -> three succeeded rows, none served from a cache.
        assert len(rows) == 3
        assert all(r.skill_name == "harnoq_source_requirement" for r in rows)
        assert all(r.status == "succeeded" for r in rows)


def test_source_requirement_skill_get_and_list(client):
    project = client.post("/projects", json={"name": "P"}).get_json()
    pid = project["id"]
    pnum = project["project_number"]
    created = client.post(
        "/skills/source-requirement",
        json=_write_body(pnum, "create",
                         {"title": "RI1", "source_type": "new_feature"}, key="k1"),
    ).get_json()

    got = client.post(
        "/skills/source-requirement",
        json={"project_number": pnum, "actor": "alice", "session_id": "s1",
              "action": "get", "payload": {"source_requirement_number": created["object_id"]}},
    ).get_json()
    assert got["source_requirement"]["title"] == "RI1"

    listed = client.post(
        "/skills/source-requirement",
        json={"project_number": pnum, "actor": "alice", "session_id": "s1",
              "action": "list", "payload": {}},
    ).get_json()
    assert len(listed["source_requirements"]) == 1


def test_source_requirement_skill_update(client):
    project = client.post("/projects", json={"name": "P"}).get_json()
    pid = project["id"]
    pnum = project["project_number"]
    created = client.post(
        "/skills/source-requirement",
        json=_write_body(pnum, "create",
                         {"title": "RI1", "source_type": "new_feature"}, key="k1"),
    ).get_json()
    updated = client.post(
        "/skills/source-requirement",
        json=_write_body(pnum, "update",
                         {"source_requirement_number": created["object_id"], "title": "Renamed"},
                         key="k2"),
    ).get_json()
    assert updated["status"] == "succeeded"
    assert updated["change_summary"].startswith("updated source requirement")


def test_source_requirement_explicit_update_skill(client):
    project = client.post("/projects", json={"name": "P"}).get_json()
    pnum = project["project_number"]
    created = client.post(
        "/skills/source-requirement-create",
        json={
            "project_number": pnum,
            "actor": "alice",
            "session_id": "s1",
            "idempotency_key": "k1",
            "payload": {"title": "RI1", "source_type": "new_feature"},
        },
    ).get_json()
    updated = client.post(
        "/skills/source-requirement-update",
        json={
            "project_number": pnum,
            "actor": "alice",
            "session_id": "s1",
            "idempotency_key": "k2",
            "payload": {"source_requirement_number": created["object_id"], "title": "Renamed"},
        },
    ).get_json()
    assert updated["status"] == "succeeded"
    assert updated["change_summary"].startswith("updated source requirement")


def test_source_requirement_create_accepts_description_and_default_source_type(client):
    project = client.post("/projects", json={"name": "P"}).get_json()
    pnum = project["project_number"]
    created = client.post(
        "/skills/source-requirement-create",
        json={
            "project_number": pnum,
            "actor": "assistant",
            "session_id": "current-session",
            "idempotency_key": "k-desc",
            "payload": {
                "title": "Additional Requirements from User",
                "description": "Supports jpg, jpeg, heic, mp4, mov.",
            },
        },
    ).get_json()
    assert created["status"] == "succeeded"
    fetched = client.post(
        "/skills/source-requirement",
        json={
            "project_number": pnum,
            "action": "get",
            "payload": {"source_requirement_number": created["object_id"]},
        },
    ).get_json()["source_requirement"]
    assert fetched["source_type"] == "manual_note"
    assert fetched["raw_content"] == "Supports jpg, jpeg, heic, mp4, mov."


def test_source_requirement_explicit_update_accepts_flat_args_without_project_or_identity(client):
    project = client.post("/projects", json={"name": "P"}).get_json()
    pnum = project["project_number"]
    created = client.post(
        "/skills/source-requirement-create",
        json={
            "project_number": pnum,
            "title": "Additional Requirements from User",
            "description": "Original body",
        },
    ).get_json()
    updated = client.post(
        "/skills/source-requirement-update",
        json={
            "source_requirement_number": created["object_id"],
            "raw_content": "Updated body",
        },
    ).get_json()
    assert updated["status"] == "succeeded"
    fetched = client.post(
        "/skills/source-requirement",
        json={
            "project_number": pnum,
            "action": "get",
            "payload": {"source_requirement_number": created["object_id"]},
        },
    ).get_json()["source_requirement"]
    assert fetched["raw_content"] == "Updated body"


def test_source_requirement_explicit_update_accepts_source_requirement_alias(client):
    project = client.post("/projects", json={"name": "P"}).get_json()
    pnum = project["project_number"]
    created = client.post(
        "/skills/source-requirement-create",
        json={"project_number": pnum, "title": "RI1", "description": "Original"},
    ).get_json()
    updated = client.post(
        "/skills/source-requirement-update",
        json={
            "source_requirement_number": created["object_id"],
            "raw_content": "Updated through alias",
        },
    ).get_json()
    assert updated["status"] == "succeeded"


def test_source_requirement_explicit_append_content_always_reruns(client):
    """Writes are not deduped (see skill_service module docstring): a repeated
    append_content call with the SAME payload appends again rather than being
    silently absorbed as a replay. This is a deliberate trade — an LLM/tool
    caller that double-sends an append will duplicate text — accepted because
    the alternative (a payload-hash key) previously caused several writes to
    be silently dropped in production."""
    project = client.post("/projects", json={"name": "P"}).get_json()
    pnum = project["project_number"]
    created = client.post(
        "/skills/source-requirement-create",
        json={
            "project_number": pnum,
            "title": "Initial requirements",
            "source_type": "initial_document",
            "raw_content": "1. Existing requirement",
        },
    ).get_json()

    body = {
        "source_requirement_number": created["object_id"],
        "raw_content_to_append": "2. Avoid infinite scrolling; use pagination.",
    }
    first = client.post("/skills/source-requirement-append-content", json=body).get_json()
    second = client.post("/skills/source-requirement-append-content", json=body).get_json()

    assert first["status"] == "succeeded"
    assert second["status"] == "succeeded"
    fetched = client.post(
        "/skills/source-requirement",
        json={
            "project_number": pnum,
            "action": "get",
            "payload": {"source_requirement_number": created["object_id"]},
        },
    ).get_json()["source_requirement"]
    assert fetched["raw_content"] == (
        "1. Existing requirement\n"
        "2. Avoid infinite scrolling; use pagination.\n"
        "2. Avoid infinite scrolling; use pagination."
    )


def test_source_requirement_skill_runs_with_missing_actor_session(client, app):
    """actor/session_id are optional audit-trail fields; omitting them must
    NOT block the write (no rejection path — see skill_service docstring)."""
    project = client.post("/projects", json={"name": "P"}).get_json()
    pid = project["id"]
    pnum = project["project_number"]
    env = client.post(
        "/skills/source-requirement",
        json={"project_number": pnum, "action": "create",
              "idempotency_key": "k1", "payload": {"title": "x", "source_type": "new_feature"}},
    ).get_json()
    assert env["status"] == "succeeded"
    with app.app_context():
        rows = skill_invocation_repo.list_by_project(pid)
        assert len(rows) == 1 and rows[0].status == "succeeded"


def test_source_requirement_skill_unsupported_action(client):
    project = client.post("/projects", json={"name": "P"}).get_json()
    pid = project["id"]
    pnum = project["project_number"]
    resp = client.post(
        "/skills/source-requirement",
        json=_write_body(pnum, "bogus", {}, key="k1"),
    )
    assert resp.status_code == 400


# --- harnoq_design_case ---------------------------------------------------


def test_design_case_skill_lifecycle(client, app):
    pid, _feature_id, _ = _make_confirmed_design(client)
    pnum = _pnum(client, pid)
    # Create a fresh FEATURE + draft design to drive the skill.
    fresh_feature = client.post(
        f"/projects/{pid}/features",
        json={"name": "F2"},
    ).get_json()

    created = client.post(
        "/skills/design-case",
        json=_write_body(pnum, "create", {"feature_number": fresh_feature["feature_number"]}, key="k1"),
    ).get_json()
    assert created["object_type"] == "design_case"
    design_number = created["object_id"]

    # Not deduped: same key + payload creates a SECOND, distinct design under
    # the same FEATURE (the "several DESIGN-* per FEATURE" model this fixes).
    again = client.post(
        "/skills/design-case",
        json=_write_body(pnum, "create", {"feature_number": fresh_feature["feature_number"]}, key="k1"),
    ).get_json()
    assert again["object_id"] != design_number

    # Update incl. rejected_solutions.
    updated = client.post(
        "/skills/design-case",
        json=_write_body(pnum, "update",
                         {"design_number": design_number,
                          "selected_solution": "SAML SSO",
                          "selection_reason": "enterprise req",
                          "rejected_solutions": [
                              {"name": "Email", "reason": "too weak"}],
                          },
                         key="k2"),
    ).get_json()
    assert updated["status"] == "succeeded"

    # Confirm: service enforces REQUIRED_FOR_CONFIRM. Fill the rest first.
    client.post(
        "/skills/design-case",
        json=_write_body(pnum, "update",
                         {"design_number": design_number,
                          "problem_statement": "p",
                          "risks": "r",
                          "test_strategy": "ts",
                          "acceptance_criteria": "ac"},
                         key="k3"),
    )
    confirmed = client.post(
        "/skills/design-case",
        json=_write_body(pnum, "confirm", {"design_number": design_number}, key="k4"),
    ).get_json()
    assert confirmed["status"] == "succeeded"
    assert "confirmed design case" in confirmed["change_summary"]

    with app.app_context():
        rows = skill_invocation_repo.list_by_project(pid)
        assert all(r.skill_name == "harnoq_design_case" for r in rows)
        # k1 (create, ran twice — no dedup) + k2 + k3 + k4 = 5 succeeded
        assert len(rows) == 5


def test_design_case_skill_create_via_feature_number(client):
    project = client.post("/projects", json={"name": "P"}).get_json()
    pid = project["id"]
    pnum = project["project_number"]
    feature = client.post(
        f"/projects/{pid}/features",
        json={"name": "Platform", "orientation": "foundation", "subdomain_type": "generic"},
    ).get_json()

    created = client.post(
        "/skills/design-case",
        json=_write_body(
            pnum,
            "create",
            {
                "feature_number": feature["feature_number"],
                "design_scope": "Storage layout + contract to slices.",
            },
            key="k1",
        ),
    ).get_json()

    assert created["object_type"] == "design_case"
    assert created["status"] == "succeeded"
    design_number = created["object_id"]

    fetched = client.post(
        "/skills/design-case",
        json={
            "project_number": pnum,
            "actor": "alice",
            "session_id": "s1",
            "action": "get",
            "payload": {"design_number": design_number},
        },
    ).get_json()
    assert fetched["design_case"]["feature_number"] == feature["feature_number"]


def test_design_case_skill_create_without_req_or_feature_is_rejected(client):
    project = client.post("/projects", json={"name": "P"}).get_json()
    pnum = project["project_number"]

    response = client.post(
        "/skills/design-case",
        json=_write_body(pnum, "create", {"design_scope": "x"}, key="k1"),
    )
    assert response.status_code == 400


def test_constitution_skill_update_get_confirm_reopen(client):
    project = client.post("/projects", json={"name": "P"}).get_json()
    pnum = project["project_number"]

    updated = client.post(
        "/skills/constitution-update",
        json={
            "project_number": pnum,
            "actor": "alice",
            "session_id": "s1",
            "idempotency_key": "c1",
            "payload": {"fixed_stack": "Flask + SQLite", "system_nature": "离线相册"},
        },
    ).get_json()
    assert updated["status"] == "succeeded"
    assert updated["object_id"] == pnum

    got = client.post(
        "/skills/constitution",
        json={
            "project_number": pnum,
            "actor": "alice",
            "session_id": "s1",
            "action": "get",
            "payload": {},
        },
    ).get_json()["constitution"]
    assert got["fixed_stack"] == "Flask + SQLite"
    assert got["status"] == "draft"

    confirmed = client.post(
        "/skills/constitution-confirm",
        json={
            "project_number": pnum,
            "actor": "alice",
            "session_id": "s1",
            "idempotency_key": "c2",
        },
    ).get_json()
    assert confirmed["status"] == "succeeded"

    # Locked now: an update is rejected (409) until reopened.
    blocked = client.post(
        "/skills/constitution-update",
        json={
            "project_number": pnum,
            "actor": "alice",
            "session_id": "s1",
            "idempotency_key": "c3",
            "payload": {"fixed_stack": "changed"},
        },
    )
    assert blocked.status_code == 409

    reopened = client.post(
        "/skills/constitution-reopen",
        json={
            "project_number": pnum,
            "actor": "alice",
            "session_id": "s1",
            "idempotency_key": "c4",
        },
    ).get_json()
    assert reopened["status"] == "succeeded"


def test_feature_skill_create_get_list_update_assign_delete(client):
    project = client.post("/projects", json={"name": "P"}).get_json()
    pnum = project["project_number"]

    created = client.post(
        "/skills/feature-create",
        json={
            "project_number": pnum,
            "actor": "alice",
            "session_id": "s1",
            "idempotency_key": "k1",
            "payload": {
                "name": "功能A",
                "assignee": "alice",
            },
        },
    ).get_json()
    assert created["status"] == "succeeded"

    detail = client.post(
        "/skills/feature",
        json={
            "project_number": pnum,
            "actor": "alice",
            "session_id": "s1",
            "action": "get",
            "payload": {"feature_number": created["object_id"]},
        },
    ).get_json()["feature"]
    assert detail["feature_number"] == created["object_id"]

    listed = client.post(
        "/skills/feature",
        json={
            "project_number": pnum,
            "actor": "alice",
            "session_id": "s1",
            "action": "list",
            "payload": {},
        },
    ).get_json()
    assert [item["feature_number"] for item in listed["features"]] == [created["object_id"]]

    updated = client.post(
        "/skills/feature-update",
        json={
            "project_number": pnum,
            "actor": "alice",
            "session_id": "s1",
            "idempotency_key": "k2",
            "payload": {"feature_number": created["object_id"], "description": "updated"},
        },
    ).get_json()
    assert updated["status"] == "succeeded"

    assigned = client.post(
        "/skills/feature-assign",
        json={
            "project_number": pnum,
            "actor": "alice",
            "session_id": "s1",
            "idempotency_key": "k3",
            "payload": {"feature_number": created["object_id"], "assignee": "bob"},
        },
    ).get_json()
    assert assigned["status"] == "succeeded"

    deleted = client.post(
        "/skills/feature-delete",
        json=_write_body(
            pnum,
            "delete",
            {"feature_number": created["object_id"]},
            key="k4",
        ),
    ).get_json()
    assert deleted["status"] == "succeeded"


def test_design_case_skill_confirm_missing_required_fields(client):
    """Service-enforced REQUIRED_FOR_CONFIRM still holds via the skill."""
    project = client.post("/projects", json={"name": "P"}).get_json()
    pid = project["id"]
    pnum = project["project_number"]
    feature = client.post(
        f"/projects/{pid}/features", json={"name": "F"}
    ).get_json()
    case = client.post(
        f"/projects/{pid}/design-cases", json={"feature_id": feature["id"]}
    ).get_json()
    resp = client.post(
        "/skills/design-case",
        json=_write_body(pnum, "confirm", {"design_number": case["design_number"]}, key="k1"),
    )
    assert resp.status_code == 400


def test_design_case_skill_get_and_list(client):
    pid, feature_id, _ = _make_confirmed_design(client)
    pnum = _pnum(client, pid)
    feature_number = client.get(
        f"/features/{feature_id}"
    ).get_json()["feature_number"]
    got = client.post(
        "/skills/design-case",
        json={"project_number": pnum, "actor": "alice", "session_id": "s1",
              "action": "list", "payload": {"feature_number": feature_number}},
    ).get_json()
    assert len(got["design_cases"]) == 1

    project_wide = client.post(
        "/skills/design-case",
        json={"project_number": pnum, "actor": "alice", "session_id": "s1",
              "action": "list", "payload": {}},
    ).get_json()
    assert len(project_wide["design_cases"]) == 1

    design_number = got["design_cases"][0]["design_number"]
    one = client.post(
        "/skills/design-case",
        json={"project_number": pnum, "actor": "alice", "session_id": "s1",
              "action": "get", "payload": {"design_number": design_number}},
    ).get_json()
    assert one["design_case"]["design_number"] == design_number


def test_design_case_explicit_update_confirm_reopen_skills(client):
    project = client.post("/projects", json={"name": "P"}).get_json()
    pid = project["id"]
    pnum = project["project_number"]
    feature = client.post(
        f"/projects/{pid}/features",
        json={"name": "F"},
    ).get_json()
    feature2 = client.post(
        f"/projects/{pid}/features",
        json={"name": "F2"},
    ).get_json()
    created = client.post(
        "/skills/design-case-create",
        json={
            "project_number": pnum,
            "actor": "alice",
            "session_id": "s1",
            "idempotency_key": "k1",
            "payload": {"feature_number": feature["feature_number"]},
        },
    ).get_json()
    design_number = created["object_id"]
    updated = client.post(
        "/skills/design-case-update",
        json={
            "project_number": pnum,
            "actor": "alice",
            "session_id": "s1",
            "idempotency_key": "k2",
            "payload": {"design_number": design_number, **_DESIGN_CONFIRM_FIELDS},
        },
    ).get_json()
    assert updated["status"] == "succeeded"
    confirmed = client.post(
        "/skills/design-case-confirm",
        json={
            "project_number": pnum,
            "actor": "alice",
            "session_id": "s1",
            "idempotency_key": "k3",
            "payload": {"design_number": design_number},
        },
    ).get_json()
    assert confirmed["status"] == "succeeded"
    # reopen only applies to a confirmed design, so
    # confirm one first before reopening it back to draft.
    created2 = client.post(
        "/skills/design-case-create",
        json={
            "project_number": pnum,
            "actor": "alice",
            "session_id": "s1",
            "idempotency_key": "k4",
            "payload": {"feature_number": feature2["feature_number"]},
        },
    ).get_json()
    client.post(
        "/skills/design-case-update",
        json={
            "project_number": pnum,
            "actor": "alice",
            "session_id": "s1",
            "idempotency_key": "k4b",
            "payload": {"design_number": created2["object_id"], **_DESIGN_CONFIRM_FIELDS},
        },
    )
    client.post(
        "/skills/design-case-confirm",
        json={
            "project_number": pnum,
            "actor": "alice",
            "session_id": "s1",
            "idempotency_key": "k4c",
            "payload": {"design_number": created2["object_id"]},
        },
    )
    reopened = client.post(
        "/skills/design-case-reopen",
        json={
            "project_number": pnum,
            "actor": "alice",
            "session_id": "s1",
            "idempotency_key": "k5",
            "payload": {"design_number": created2["object_id"]},
        },
    ).get_json()
    assert reopened["status"] == "succeeded"


# --- harnoq_design_case create_task ----------------------------------------


_FULL_TASK = {
    "title": "Implement login",
    "task_type": "feature",
    "goal": "Users can log in",
    "acceptance_criteria": "Login succeeds with valid creds",
    "constraints": "Must not store plaintext passwords",
    "non_goals": "No social login",
}


def test_design_case_create_task_skill_action(client, app):
    pid, item_id, design_id = _make_confirmed_design(client)
    pnum = _pnum(client, pid)
    design_number = client.get(f"/design-cases/{design_id}").get_json()["design_number"]

    task = client.post(
        "/skills/design-case",
        json=_write_body(pnum, "create_task", {"design_number": design_number, **_FULL_TASK}, key="k1"),
    ).get_json()
    assert task["object_type"] == "development_task"
    assert task["status"] == "succeeded"
    task_number = task["object_id"]

    # A live DESIGN has one execution TASK.  Repeating the request is a
    # conflict, even when the idempotency key differs.
    again = client.post(
        "/skills/design-case",
        json=_write_body(pnum, "create_task", {"design_number": design_number, **_FULL_TASK}, key="k1"),
    )
    assert again.status_code == 409

    with app.app_context():
        rows = skill_invocation_repo.list_by_project(pid)
        assert all(r.skill_name == "harnoq_design_case" for r in rows)
        assert len(rows) == 2


def test_design_case_create_task_skill_missing_fields_rejected(client, app):
    """Skill guard: a task create missing any of the four spec fields is rejected."""
    pid, _, design_id = _make_confirmed_design(client)
    pnum = _pnum(client, pid)
    design_number = client.get(f"/design-cases/{design_id}").get_json()["design_number"]

    # Missing goal.
    bad = dict(_FULL_TASK)
    bad.pop("goal")
    resp = client.post(
        "/skills/design-case",
        json=_write_body(pnum, "create_task", {"design_number": design_number, **bad}, key="k2"),
    )
    assert resp.status_code == 400
    # Empty-string constraints also rejected.
    bad2 = dict(_FULL_TASK, constraints="   ")
    assert client.post(
        "/skills/design-case",
        json=_write_body(pnum, "create_task", {"design_number": design_number, **bad2}, key="k3"),
    ).status_code == 400

    ok = client.post(
        "/skills/design-case",
        json=_write_body(pnum, "create_task", {"design_number": design_number, **_FULL_TASK}, key="k4"),
    ).get_json()
    assert ok["status"] == "succeeded"


def test_design_case_create_task_skill_read_reflects_created_tasks(client):
    pid, _, design_id = _make_confirmed_design(client)
    pnum = _pnum(client, pid)
    design_number = client.get(f"/design-cases/{design_id}").get_json()["design_number"]

    task = client.post(
        "/skills/design-case",
        json=_write_body(pnum, "create_task", {"design_number": design_number, **_FULL_TASK}, key="k1"),
    ).get_json()
    duplicate = client.post(
        "/skills/design-case",
        json=_write_body(
            pnum, "create_task", {"design_number": design_number, **dict(_FULL_TASK, title="T2")}, key="k2"
        ),
    )
    assert duplicate.status_code == 409

    got = client.post(
        "/skills/design-case",
        json={"project_number": pnum, "actor": "alice", "session_id": "s1",
              "action": "get", "payload": {"design_number": design_number}},
    ).get_json()
    assert got["design_case"]["design_number"] == design_number
    assert len(got["design_case"]["task_numbers"]) == 1
    assert task["object_id"] in got["design_case"]["task_numbers"]

    listed = client.post(
        "/skills/design-case",
        json={"project_number": pnum, "actor": "alice", "session_id": "s1",
              "action": "list", "payload": {}},
    ).get_json()
    assert [item["design_number"] for item in listed["design_cases"]] == [design_number]
    assert len(listed["design_cases"][0]["task_numbers"]) == 1


def test_design_case_create_task_skill_requires_confirmed_design(client):
    """Service-enforced limit: a task requires a confirmed design."""
    project = client.post("/projects", json={"name": "P"}).get_json()
    pid = project["id"]
    pnum = project["project_number"]
    feature = client.post(
        f"/projects/{pid}/features", json={"name": "F"}
    ).get_json()
    design = client.post(
        f"/projects/{pid}/design-cases", json={"feature_id": feature["id"]}
    ).get_json()
    resp = client.post(
        "/skills/design-case",
        json=_write_body(pnum, "create_task", {"design_number": design["design_number"], **_FULL_TASK}, key="k1"),
    )
    assert resp.status_code == 409


def test_design_case_explicit_create_task_write_skill(client):
    pid, _, design_id = _make_confirmed_design(client)
    pnum = _pnum(client, pid)
    design_number = client.get(f"/design-cases/{design_id}").get_json()["design_number"]
    task = client.post(
        "/skills/design-case-create-task",
        json={
            "project_number": pnum,
            "actor": "alice",
            "session_id": "s1",
            "idempotency_key": "k1",
            "payload": {"design_number": design_number, **_FULL_TASK},
        },
    ).get_json()
    assert task["status"] == "succeeded"
    task2 = client.post(
        "/skills/design-case-create-task",
        json={
            "project_number": pnum,
            "actor": "alice",
            "session_id": "s1",
            "idempotency_key": "k2",
            "payload": {"design_number": design_number, **dict(_FULL_TASK, title="T2")},
        },
    )
    assert task2.status_code == 409


# ---------------------------------------------------------------------------
# Gate 3: mechanical coverage report (domain-agnostic feature map)
# ---------------------------------------------------------------------------


def test_build_feature_map_partitions_by_design_coverage():
    """Coverage is decided purely by current_design_case_id + feature
    partition — mechanically, not by the INPUT's own lifecycle status (see
    _build_feature_map, which deliberately does not filter by status)."""
    from app.api.skills import _build_feature_map

    features = [
        {"feature_number": "FEATURE-1", "name": "功能A",
         "orientation": "vertical", "subdomain_type": "supporting",
         "assignee": "alice", "progress": "done", "design_numbers": ["DESIGN-1"]},
        {"feature_number": "FEATURE-2", "name": "功能B",
         "orientation": "vertical", "subdomain_type": "supporting",
         "assignee": None, "progress": "in_design", "design_numbers": ["DESIGN-2"]},
    ]

    result = _build_feature_map(features)

    assert [d["feature_number"] for d in result["features"]] == ["FEATURE-1", "FEATURE-2"]
    assert result["pending_design_feature_numbers"] == ["FEATURE-2"]
    assert result["all_features_designed"] is False


def test_build_feature_map_all_covered_when_no_gap():
    from app.api.skills import _build_feature_map

    features = [
        {"feature_number": "FEATURE-1", "name": "功能A",
         "orientation": "vertical", "subdomain_type": "supporting",
         "assignee": None, "progress": "done", "design_numbers": ["DESIGN-1"]},
    ]

    result = _build_feature_map(features)

    assert result["pending_design_feature_numbers"] == []
    assert result["all_features_designed"] is True


def test_build_feature_map_surfaces_project_foundation_features():
    from app.api.skills import _build_feature_map

    features = [
        {
            "feature_number": "FEATURE-1", "name": "Architecture Foundation",
            "orientation": "foundation", "foundation_kind": "architecture",
            "subdomain_type": "supporting", "assignee": None,
            "progress": "done", "design_numbers": ["DESIGN-1"],
        },
        {
            "feature_number": "FEATURE-2", "name": "UI Foundation",
            "orientation": "foundation", "foundation_kind": "ui",
            "subdomain_type": "supporting", "assignee": None,
            "progress": "in_design", "design_numbers": ["DESIGN-2"],
        },
    ]

    result = _build_feature_map(features)

    assert result["foundation_features"]["architecture"]["feature_number"] == "FEATURE-1"
    assert result["foundation_features"]["ui"]["feature_number"] == "FEATURE-2"


def test_current_stage_is_planning_complete_when_all_confirmed_designs_have_tasks():
    from app.api.skills import _current_stage

    constitution = {"status": "confirmed"}
    units = [{"title": "i", "status": "confirmed"}]
    feature_map = {"pending_design_feature_numbers": []}

    # Every confirmed design already has a task -> planning is done.
    all_tasked = [{"status": "confirmed", "task_count": 1}]
    assert _current_stage(constitution, units, all_tasked, feature_map) == "planning_complete"

    # A confirmed design still missing its task -> real planning work remains.
    mixed = [
        {"status": "confirmed", "task_count": 1},
        {"status": "confirmed", "task_count": 0},
    ]
    assert _current_stage(constitution, units, mixed, feature_map) == "task_planning"


def test_planning_complete_directive_tells_agent_not_to_recreate_tasks():
    from app.api.skills import _stage_directive

    directive = _stage_directive("planning_complete")
    assert "COMPLETE" in directive
    assert "Do NOT create" in directive
    assert "task_count" in directive


def test_task_planning_does_not_forbid_creating_ahead_of_upstream():
    """The method used to say "do not create a task for a design whose Feature
    is still waiting", which fights both the machinery and its own principle.

    development_task_service.claim() is the single authority on dependency
    order, computed live from the Feature graph and impossible to bypass
    (tests/test_feature_dependency_gating.py pins that creation is NOT gated).
    Restating that gate here bought nothing and cost a refusal: an agent read
    'claimable' as "can be claimed now", concluded batch creation was unsafe,
    and told the user it was forbidden to create the plan they asked for.
    """
    from app.api.skills import _stage_methods

    task_planning = _stage_methods()["task_planning"]
    body = " ".join(task_planning["principles"]) + " ".join(task_planning["rounds"])

    assert "do not create a task for a design whose feature" not in body.lower()
    # The order survives as presentation guidance...
    assert "PRESENT the tasks in the order the Features wait" in body
    # ...and the reason it is not a creation gate is stated where the refusal
    # would otherwise be reinvented.
    assert "creation is never blocked by an unfinished upstream" in body
    assert "enforced at CLAIM" in body
    assert "STATE NAME" in body


def test_task_planning_method_checks_existing_tasks_first():
    from app.api.skills import _stage_methods

    orient = _stage_methods()["task_planning"]["orient_first"]
    assert "task_count" in orient
    assert "already" in orient.lower()


def test_current_stage_requirement_before_constitution():
    from app.api.skills import _current_stage

    empty_fm = {"pending_design_feature_numbers": []}
    # Fresh project: no INPUT-* at all -> mine requirements first, NOT author a
    # constitution (which is harvested from mined INPUT-*).
    assert _current_stage(None, [], [], empty_fm) == "requirement"
    # A still-draft INPUT must NOT bounce straight to constitution — the
    # requirement stage isn't done until every INPUT is confirmed.
    assert (
        _current_stage(None, [{"title": "i", "status": "draft"}], [], empty_fm)
        == "requirement"
    )
    # Once every INPUT is confirmed, the constitution stage is reachable.
    assert (
        _current_stage(None, [{"title": "i", "status": "confirmed"}], [], empty_fm)
        == "constitution"
    )


def test_every_current_stage_maps_to_a_real_method():
    from app.api.skills import _stage_methods, _STAGE_METHOD_KEY

    methods = _stage_methods()
    for stage in (
        "requirement", "constitution", "design", "task_planning",
        "planning_complete", "change", "integration", "system",
    ):
        key = _STAGE_METHOD_KEY.get(stage, stage)
        assert methods.get(key) is not None, f"stage {stage} has no active_stage_method"


def test_integration_and_system_methods_have_the_shared_shape():
    """docs/cn/design-evolution.md#delivery: same skeleton
    as design/task_planning (when/principles-or-equivalent/rounds/write)."""
    from app.api.skills import _stage_methods

    methods = _stage_methods()
    for key in ("integration", "system"):
        method = methods[key]
        for field in ("when", "rounds", "write"):
            assert method.get(field), f"{key} missing {field}"
        assert method.get("principles"), f"{key} missing principles"


def test_integration_directive_demands_code_judged_criteria():
    from app.api.skills import _stage_directive

    directive = _stage_directive("integration")
    assert "EXIT ASSERTION" in directive
    assert "runs and exits" in directive


def test_system_directive_demands_black_box_script_verdict():
    from app.api.skills import _stage_directive

    directive = _stage_directive("system")
    assert "EXIT ASSERTION" in directive
    assert "black-box" in directive
    assert "exit code" in directive
    assert "assertions of the promised user result" in directive
    assert "explicitly waived by the user" in directive
    assert "system_owed=false" in directive


def test_integration_round1_workflow_uses_existing_design_materials():
    """The linear workflow derives inventory from existing design fields and
    repository evidence rather than inventing a second source of truth."""
    from app.api.skills import _stage_methods

    method = _stage_methods()["integration"]
    steps = " ".join(method["round_1_workflow"]["steps"])
    assert "selected_solution" in steps
    assert "design_scope" in steps
    assert "Build/bundle/compile/package/codegen/test/dev/preview" in steps
    assert "NEVER entries by themselves" in steps
    assert "verified served/deployed load path" in steps
    assert "zero verified entries means zero reachable parts" in steps
    assert "targeted skeleton" in steps
    assert "materials" not in method


def test_integration_round1_recovers_from_helper_and_tool_interruptions():
    from app.api.skills import _stage_directive, _stage_methods

    directive = _stage_directive("integration")
    workflow = _stage_methods()["integration"]["round_1_workflow"]
    assert "take over missing work yourself" in directive
    assert "inspect what was persisted" in directive
    assert "do the remaining work yourself" in workflow["rule"]
    assert "read back saved state before retrying" in workflow["rule"]
    assert "A progress report is not a handoff or stop condition" in workflow["user_handoff"]
    assert "manager owns the cross-design inventory and final manifest" in directive
    principles = " ".join(_stage_methods()["integration"]["principles"])
    assert "Delegate only bounded mechanical evidence slices" in principles
    assert "Coder and qa handle stage-run repair and verification" in principles
    assert "harnoq_integration_item_get" in principles
    assert "groups all open findings by shared root cause" in principles
    assert "Do not default to one coder call or one full QA cycle per item" in principles
    assert "run.evidence" in _stage_methods()["integration"]["repair_policy"]


def test_integration_round1_command_evidence_has_fixed_block_shape():
    from app.api.skills import _stage_methods

    step_b = _stage_methods()["integration"]["round_1_workflow"]["steps"][1]
    for field in ("command", "exit_code", "expected_exit_codes", "stdout", "stderr"):
        assert field in step_b
    assert "INLINE raw" in step_b
    assert "external output file" in step_b


def test_integration_round1_convergence_defaults_to_retain():
    from app.api.skills import _stage_methods

    step_f = _stage_methods()["integration"]["round_1_workflow"]["steps"][5]
    assert "DEFAULT RETAIN" in step_f
    assert "same contract" in step_f
    assert "identical required caller semantics" in step_f
    assert "one legitimate canonical owner" in step_f
    assert "divergence would be a defect" in step_f
    assert "Same name/body/fields" in step_f
    assert "Every E collision key maps to exactly one responsibility candidate" in step_f


def test_open_change_request_routes_to_the_change_stage():
    """The change method and its tools both existed while nothing ever selected
    them, because _current_stage never looked at change_requests — so the stage
    was unreachable and the table stayed empty."""
    from app.api.skills import _current_stage

    constitution = {"status": "confirmed"}
    units = [{"title": "i", "status": "confirmed"}]
    feature_map = {"pending_design_feature_numbers": []}
    designs = [{"status": "confirmed", "task_count": 1}]

    # Nothing open -> the pipeline position stands.
    assert _current_stage(constitution, units, designs, feature_map, []) == (
        "planning_complete"
    )
    # Closed requests are finished work and must not pin the project there.
    closed = [{"status": "closed"}]
    assert _current_stage(constitution, units, designs, feature_map, closed) == (
        "planning_complete"
    )
    # Anything short of closed still owes a step, including 'applied' (its
    # downstream still has to be digested and a resolution written).
    for status in ("proposed", "analyzing", "approved", "applied"):
        assert _current_stage(
            constitution, units, designs, feature_map, [{"status": status}]
        ) == "change", status


def test_change_directive_names_the_cheap_exit():
    """The method's own orient_first says a change with nothing downstream is
    just an edit; the directive must not read as ceremony-by-default."""
    from app.api.skills import _stage_directive

    directive = _stage_directive("change")
    assert "CR-*" in directive
    assert "change_requests" in directive
    assert "just make the edit" in directive


def test_project_context_ships_change_requests(client):
    project = client.post("/projects", json={"name": "P"}).get_json()
    client.post(
        f"/projects/{project['id']}/change-requests",
        json={"target_type": "new", "reason": "a new requirement arrived"},
    )
    context = client.post(
        "/skills/project-context", json={"project_number": project["project_number"]}
    ).get_json()

    assert len(context["change_requests"]) == 1
    request = context["change_requests"][0]
    assert request["status"] == "proposed"
    # Without the actions an agent can see the request but not act on it.
    assert "analyze_this_change_request" in request["ai_available_actions"]


def test_dependencies_are_ordering_constraints_stated_per_block():
    """Two separate defects, one wording fix.

    First, deciding the order while deciding the blocks biases the cut: strata
    stack into a tidy sequence, so anything chasing a tidy sequence drifts into
    strata. Hence separate rounds.

    Second, the round was written in graph vocabulary (edges, acyclic, transitive
    closure) and the agent answered in kind — it drew ASCII trees that
    contradicted its own list and expanded the closure into 27 "edges" for 9
    blocks, including pure-backend blocks waiting on the UI foundation. The graph
    is not what the system asks: the claim gate only asks "can work on this start
    yet". Saying that in plain dependency terms, per block, with the stuck thing
    named, is the fix — the redundant waits cost nothing, but a wait that isn't
    real wrongly blocks work from being claimed.
    """
    from app.api.skills import _stage_methods

    rounds = _stage_methods()["design"]["rounds"]
    blocks, order = rounds[0], rounds[1]
    assert "BLOCKS ONLY" in blocks
    assert "DO NOT WORK OUT WHAT WAITS FOR WHAT IN THIS ROUND" in blocks
    # One meaning only, and explicitly not "A uses B".
    assert "MUST BE FINISHED BEFORE WORK ON A CAN START" in order
    assert "IT IS NOT 'A USES B'" in order
    # Per block, with the stuck thing named — no diagram to contradict it.
    assert "GO BLOCK BY BLOCK" in order
    assert "WHAT OF IT IS STUCK" in order
    assert "DO NOT DRAW A TREE OR A DIAGRAM" in order
    # The two mistakes are not symmetric: this only ever holds work back, so a
    # spare wait costs a later start while a missing one lets work begin against
    # something that does not exist. Attention goes to the missing ones, and the
    # richest source of those is a block whose own description says it reuses
    # another block while not waiting for it.
    assert "THE TWO MISTAKES DO NOT COST THE SAME" in order
    assert "DO NOT PRUNE FOR TIDINESS" in order
    assert "SWEEP FOR WHAT IS MISSING" in order
    assert "REUSES rather than" in order
    # Mutual waiting sends the cut back; the spike/late-block observations are
    # reported, never worked around by quietly redrawing.
    assert "NEVER WAIT FOR EACH OTHER" in order
    assert "go back to Round 1" in order
    assert "neither is a defect on its" in order.lower()
    assert "is_spike" in order
    # Redundant waits on the backbone are explicitly fine, so the agent stops
    # chasing them: the gate only reads direct waits.
    assert "only ever looks at what a block waits for" in order
    # And the graph vocabulary that produced trees and closures is gone.
    for jargon in ("ACYCLIC", "transitive", "STRUCTURED EDGES"):
        assert jargon not in order, jargon


def test_design_stage_absorbs_requirement_mining():
    """REQ is gone (see docs/cn/design-evolution.md §2), so the detail gets mined here — by drafting.

    "Ask thoroughly" is not actionable: you cannot ask about what you did not
    think of. What IS actionable is a draft the user can strike, plus four
    sweeps that are searches with fixed inputs — the four that actually produced
    the good questions when this was done by hand. And because one conversation
    now settles both the requirement and the solution, they have to be shown
    apart, or approving the solution silently approves every assumption in it.
    """
    from app.api.skills import _stage_methods

    design = _stage_methods()["design"]
    assert "THIS STAGE ALSO MINES THE REQUIREMENTS" in design["when"]
    principles = " ".join(design["principles"])
    # The four sweeps, each a search rather than an exhortation to be thorough.
    assert "FOUR SWEEPS" in principles
    for sweep in (
        "UNCLAIMED GROUND",
        "SENTENCES THAT FIGHT",
        "THE SECOND ORDER OF EVERY NUMBER",
        "UNQUANTIFIED WORDS",
    ):
        assert sweep in principles, sweep
    # Who decides follows orientation: a user cannot choose between mechanisms
    # they will never see, and product decisions must not be taken for them.
    assert "WHO DECIDES DEPENDS ON WHICH KIND OF FEATURE" in principles
    assert "ask for a veto rather than a choice" in principles
    # Two columns, with supplied-but-unconfirmed lines left marked.
    assert "PRESENT IT IN TWO COLUMNS" in principles
    assert "what I understand you want" in principles
    assert "what I intend to build" in principles
    # The depth_bar audits the draft; it is not the question generator.
    assert set(design["depth_bar"]) == {
        "trigger",
        "inputs_outputs",
        "figures",
        "failure_and_rejection",
        "boundaries",
    }
    # The frame never stops for its own approval, and the draft is the DESIGN
    # itself — requirement and solution in one turn, two columns, right column
    # at record depth (the combined requirement-and-solution format; pushing the solution out of the
    # draft left a structure-owning Feature with nothing to say).
    draft = design["rounds"][2]
    assert "never stops for approval" in draft
    assert "THE SHAPE THE THIRD COLUMN ADDS UP TO" in draft
    # And only the solution shape: criteria/risks/tests are Round-5 fields, so
    # drafting them here doubles a document the user is asked to strike through
    # and produces criteria that never went through the Round-4 audit.
    assert "WRITES THE SOLUTION SHAPE AND NOTHING ELSE" in draft
    assert "say each boundary ONCE" in draft
    # What the draft decides follows what the feature owns — structure drafts
    # structure, behaviour drafts behaviour; no per-orientation checklists.
    assert "READ OFF THE FEATURE'S OWN DESCRIPTION" in draft
    assert "归属/契约/关键数据/不做" in draft
    assert "owns structure" in draft
    # A gap is invisible and nobody corrects it; a wrong specific gets struck.
    assert "GUESS, DO NOT LEAVE GAPS" in draft
    assert "THEN STOP" in draft
    # The sweeps and the questions are explicitly held back to the next round.
    assert "do NOT raise challenges" in draft
    # And the fabrication ban carves the draft out, or the model stays silent.
    assert "THE BAN IS ON WRITING, NOT ON DRAFTING" in principles

    # The cycle is per FEATURE. Read per DESIGN-*, the same ladder multiplies by
    # the number of modules, which is the cost complaint that produced the tiers.
    assert "COVERS ONE WHOLE FEATURE" in draft
    assert "NOT run once per DESIGN-*" in draft

    # The audit round works on the corrected draft, not the original.
    audit = design["rounds"][3]
    assert "AS THE USER MARKED IT UP" in audit
    assert "audits the draft" in audit
    assert "MULTIPLE-CHOICE" in audit

    # The corrected draft has a home in the record, not just in the transcript
    # — its OWN field, so the project-level view reads a column instead of
    # parsing a heading out of prose, and design_scope means only the boundary.
    mined = design["field_standards"]["mined_requirements"]
    assert "需求(我理解你要的是)" in mined
    assert "ONLY place the mined requirement is stored" in mined
    assert "mined_requirements" in design["writable_fields"]
    # Whichever stage turns the detail up, it comes back to the same field.
    assert "single home no matter which stage discovered it" in mined
    # And the boundary field must not invite the requirement back in.
    assert "需求(我理解你要的是)" not in design["field_standards"]["design_scope"]


def test_round5_runs_three_checks_before_presenting():
    """The depth rules exist; what failed in a live run was their timing.

    A foundation design reached "please confirm" with zero table/column/state
    names — the persistence self-check was keyed to "before confirming" and
    never fired. And its criteria all collected on the easy runtime metrics
    (a latency figure, a bind address) while logging, error strategy and the
    module-seam contract carried none, so none of them got designed past a
    one-liner: the deliverables⊆criteria check sat in task planning, where
    catching it costs a reopen. Both now run in Round 5, before presenting,
    where fixing them is free. No new rules — relocated ones.
    """
    from app.api.skills import _stage_methods

    design = _stage_methods()["design"]
    final = design["rounds"][4]
    assert "THREE CHECKS" in final
    assert "SCOPE-CLOSED" in final
    # Coverage drives depth: a capability with no criterion stays a one-liner.
    assert "COVERAGE" in final
    assert "pinned by at least" in final
    assert "same back-check task planning runs" in final
    # And the persistence depth check fires before the user sees the text.
    assert "DEPTH" in final
    assert "SELF-CHECK before PRESENTING" in " ".join(design["principles"])


def test_no_always_applies_rule_is_filed_in_a_skippable_round():
    """Rounds 1-2 are skipped by design, so nothing universal may live there.

    Round 1 tells an agent that already has a FEATURE map to "skip Rounds 1 and
    2 entirely and go straight to Round 3" — which is every project after the
    mapping session. Two rules that apply to every design pass had been filed in
    Round 1 anyway, so they shipped in the payload and were never read; the one
    run where they fired looked like flaky compliance and was actually luck.
    A rule that governs each pass belongs in `principles` (always delivered) or
    in the round that acts on it. This guard catches the next such filing.
    """
    from app.api.skills import _stage_methods

    rounds = _stage_methods()["design"]["rounds"]
    skippable = rounds[0] + rounds[1]
    assert "skip Rounds 1 and 2 entirely" in rounds[0]
    # Distinctive phrases of rules that apply to EVERY design pass. (A bare
    # mention of fixed_stack is fine here — Round 1 legitimately tells the agent
    # to justify a foundation Feature from it; what must not live here is the
    # rule about carrying a new choice UP into it.)
    for stranded in (
        "carry it up into fixed_stack",   # write-back to governance
        "harnoq_constitution_reopen",     # the write-back call
        "Given is a FLOOR",               # a choice the constitution never made
        "前置",                            # interface contracts
        "翻过来要重建什么",                 # the cost column
    ):
        assert stranded not in skippable, stranded


def test_design_owes_parnas_documents_not_just_his_criterion():
    """A design a coder cannot be bound by is not finished.

    We had taken Parnas's decomposition criterion (information hiding) and none
    of the documents that go with it, so selected_solution asked for "one-line
    responsibilities" — exactly the altitude that constrains nobody. A live
    foundation design duly produced "supervises background process lifecycle"
    and left every interface, and the call directions between parts, to be
    invented at build time. The altitude bar was actively causing it: by
    banning "function names" it read as banning interfaces, when Parnas's line
    puts the interface on the design side and only what is BEHIND it on the
    coder's. Also folded in: the forcing table (requirement -> what it forces
    structurally), which a model invented under pressure and which produced
    every good question in that run, and a significance test for what deserves
    a question at all — the run before it spent the user's attention on a port
    number.
    """
    from app.api.skills import _stage_methods

    design = _stage_methods()["design"]
    principles = " ".join(design["principles"])
    draft = design["rounds"][2]
    final = design["rounds"][4]

    # A one-line responsibility is named as the failure, not the bar.
    assert "IS NOT ONE" in principles
    assert "we owe the DOCUMENTS" in principles
    # The three artifacts: the secret, the interface, the uses relation.
    assert "WHAT EACH PART HIDES" in principles
    assert "one row per operation" in principles
    assert "preconditions" in design["field_standards"]["selected_solution"]
    assert "THE USES RELATION" in principles
    assert "no cycles" in principles
    # The altitude line is redrawn where Parnas puts it.
    assert "the INTERFACE is design and belongs here" in principles
    assert "that is implementation detail" in principles
    # Depth check enforces it at presentation time, not at confirm.
    assert "uses relation is present and free of cycles" in final

    # The forcing table drives the round; a row that restates is not a row. It
    # is ONE table with the two presentation columns, not a separate artifact —
    # split apart, every requirement got written twice.
    assert "THE DRAFT IS ONE TABLE OF THREE COLUMNS" in draft
    assert "write each requirement twice" in draft
    assert "what that row FORCES" in draft
    assert "REQUIREMENT CONFLICT" in draft

    # Significance decides what is worth asking; trivia is decided and stated.
    assert "WHICH OPEN POINTS ARE WORTH RAISING AT ALL" in principles
    assert "one line of " in principles
    assert "SIGNIFICANT IS NOT THE SAME AS ASKABLE" in principles


def test_project_wide_choices_are_settled_and_kept_in_the_constitution():
    """Re-running a stage from scratch is the intended workflow, which makes the
    persisted upstream — not the transcript — the whole input to a design.

    Two clean runs of the same Architecture Foundation picked two incompatible
    delivery forms. Neither run misbehaved: no INPUT-* stated one, consolidation
    only collects what was stated, so fixed_stack was silent, and design is told
    to treat the constitution as given — an empty slot is not inherited, it is
    re-invented by whoever reaches it first. Worse, the winner was recorded only
    in that design's own text, where the next Feature's design never looks and
    the constitution shipped to the coder never mentions it. So the constitution
    stage must settle the project-wide form choices, and a design that makes one
    anyway must carry it back up.
    """
    from app.api.skills import _stage_methods

    methods = _stage_methods()
    constitution = " ".join(methods["constitution"]["principles"])
    source_map = methods["constitution"]["source_map"]
    design_round_one = methods["design"]["rounds"][0]

    # Consolidation must not leave a load-bearing slot empty.
    assert "CONSOLIDATING IS NOT THE SAME AS LEAVING BLANKS" in constitution
    assert "BIND EVERY FEATURE" in constitution
    assert "the design will decide" in constitution
    # Per-feature choices still stay out of governance.
    assert "Choices scoped to" in constitution
    assert "form choices no INPUT-* happened to state" in source_map["fixed_stack"]

    # And what design settles anyway goes back up, or it governs nothing. This
    # rule lived in Round 1 and was therefore never read: Round 1 tells an agent
    # with an existing FEATURE map to skip to Round 3, which is every project
    # after the mapping session. It belongs in the principles, which always ship.
    principles = " ".join(methods["design"]["principles"])
    assert "PUT IT WHERE IT WILL BE FOUND" in principles
    assert "reads the constitution, not your DESIGN-*" in principles
    assert "harnoq_constitution_reopen" in principles
    assert "skip Rounds 1 and 2 entirely" in design_round_one
    for stranded in ("PUT IT WHERE IT WILL BE FOUND", "Given is a FLOOR"):
        assert stranded not in design_round_one, stranded


def test_the_three_checks_a_live_run_skipped():
    """Each rule was present and each was quietly not applied.

    Interface depth decayed down the list — the first part carried full
    pre/post/failure, the most-called part further down was left as
    `read(operation) -> result`. The conflict pass produced nothing and said
    nothing, which reads the same as not having run it. And the significance
    test ("if flipping it costs one line of config it was never a question")
    was a rule to remember, so a log-rotation size still went to the user.
    Rules that are remembered get skipped; slots that must be filled get filled.

    A 5th live run then defeated the fix inside the very run that shipped it:
    the interface table landed correctly, but the conflict-check instruction
    sitting next to that table's spec was skipped again — an obligation
    beside the artifact's spec is not part of the artifact and gets dropped
    exactly like a rule "to remember" does. The lesson escalates: the
    conflict check, one-material-per-row and the interface's secret-leak
    check are now grown into the table shapes themselves, not left as prose
    beside them.
    """
    from app.api.skills import _stage_methods

    design = _stage_methods()["design"]
    principles = " ".join(design["principles"])
    draft = design["rounds"][2]

    # Depth is held by a fixed-column table, not by a bar to live up to: prose
    # decays down a list (first part full, later ones bare signatures), a cell
    # cannot. The same form closes both dodges — module-wide rules fill no row,
    # and a placeholder type cannot fill 入参/返回.
    assert "THE INTERFACE IS A TABLE, NOT PROSE" in draft
    assert "操作 | 入参 | 返回 | 前置 | 后置 | 失败" in draft
    assert "an empty cell is written 无" in draft
    assert "A cell cannot thin out" in draft
    assert "policy note that fills no row" in draft
    assert "cannot fill 入参 or 返回" in draft
    # Work with the decay gradient instead of exhorting against it.
    assert "ORDER THE PARTS BY HOW MANY ARROWS POINT AT THEM" in draft
    # Each part's declared secret is checked row by row: an 入参/返回 that
    # exposes it is a leak, not a reminder to be careful.
    assert "CLAIMS TO HIDE" in draft
    assert "LEAKED" in draft

    # The conflict check is now the table's own required final row — living
    # in the artifact's shape — rather than an instruction next to the table's
    # spec, which is exactly the placement the 5th live run skipped.
    assert "REQUIRED FINAL ROW IS 冲突检查" in draft
    assert "无冲突" in draft
    assert "part of the artifact's own shape" in draft

    # One material per row: the forcing column is the detector for a row that
    # smuggled in a second, unanswered material.
    assert "ONE MATERIAL PER ROW" in draft
    assert "answers only PART" in draft

    # The cost is a cell in a REQUIRED table, not an annotation on an optional
    # list — a rule attached to raising points is satisfied by raising none.
    assert "CLOSE WITH THE DECISION TABLE — REQUIRED" in draft
    assert "决定 | 翻过来要重建什么 | 归属" in draft
    assert "is this pass NOT RUN" in draft
    assert "no row carries a cost" in draft
    assert "satisfied by raising none" in principles

    # The secret-leak self-check also lands in Round 5's DEPTH pass, not just
    # the draft round.
    assert "exposing what its part claims to hide" in _stage_methods()["design"]["rounds"][4]


def test_load_bearing_choices_cannot_be_deferred_out_of_design():
    """A live AF design shipped with its web framework and database unnamed.

    Three rules pushed the decision out the door and none caught it: the
    constitution's ask-first boundary was read as "ask at implementation time",
    Round 1's "treat the stack as given" was read as "stack decisions are not
    mine", and the coder-altitude line was read as "picking a library is an
    implementation detail". So the most load-bearing choice in the backbone was
    routed to nobody, and "not yet selected" passed review inside
    open_questions. Given is now a floor, options must be NAMED to be
    comparable, and an unnamed load-bearing dependency fails the depth check.
    """
    from app.api.skills import _stage_methods

    design = _stage_methods()["design"]
    rounds = design["rounds"]

    # Given is a floor: a choice the constitution never made is still this
    # design's to make, and ask-first means ask here, not at build time.
    principles = " ".join(_stage_methods()["design"]["principles"])
    assert "Given is a FLOOR, not a ceiling" in principles
    assert "if this design stands on it, THIS design names it" in principles
    assert "never means defer to implementation" in principles

    # An architecture option with a blank where its technology goes cannot be
    # compared or vetoed, so the AF comparison must name what it stands on.
    assert "THE OPTIONS ARE NAMED, NOT SHAPED" in rounds[2]
    assert "IS how the constitution's ask-first boundary gets" in rounds[2]

    # And open_questions stops being the exit hatch for a deferred decision.
    assert "DEPTH ALSO CLOSES THE UNNAMED DEPENDENCY" in rounds[4]
    assert "does not belong in" in rounds[4]


def test_draft_marks_sources_and_carries_no_tier_tables():
    """Two lessons from live runs, folded into one shape.

    (1) A foundation draft came out as behavioural minutiae with no design in
    it: the draft device mandated behaviour while the round banned solutions,
    so on a Feature whose content IS structure there was nothing left to write.
    The fix is the combined requirement-and-solution format — requirement and solution in one turn,
    two columns — with the right column anchored to field_standards instead of
    a parallel quota system, and the altitude read off the feature's own
    description. (2) The same run dropped all source marks because the draft
    banned 待确认 while the two-column principle demanded it on supplied lines;
    draft marks (用户说的/我补的) and the landing mark (待确认) are now separate.
    The depth-tier table is gone with the quota blocks: pre-named boxes get
    filled (the feature-map lesson), so cost is held down by one fold judgment
    and the per-feature cycle, not by a tier taxonomy.
    """
    from app.api.skills import _stage_methods

    design = _stage_methods()["design"]
    principles = " ".join(design["principles"])
    draft = design["rounds"][2]

    # Source marks exist for the draft, and cannot be confused with the marker
    # used once the column lands in the record.
    assert "用户说的" in principles and "我补的" in principles
    assert "banned as a VALUE" in principles
    assert "Only when the column lands in the written design" in principles

    # No tier taxonomy; one fold judgment, and the floors that never fold.
    assert "depth_tiers" not in design
    assert "One fold is allowed" in draft
    assert "never fold" in draft
    assert "nothing ever runs per DESIGN-*" in draft

    # The stop is phrased per WHO DECIDES: veto for foundations, correction
    # for verticals — not a menu of mechanisms the user cannot judge.
    assert "ask for a VETO" in draft
    assert "strike and correct both columns" in draft


def test_feature_map_round_is_judgment_not_a_box_filling_procedure():
    """The mandated artifacts were producing the failure they were meant to stop.

    Round 1 used to require a consumer-analysis table plus a four-question
    checklist that MANDATED a generic platform Feature, an isolated engine and a
    Feature per cross-cutting policy. That hands the model pre-named boxes before
    it has looked at the project, and it fills them: successive maps went from
    four strata out of nine to eight out of thirteen, with "one requirement, one
    home" satisfied on paper by giving every capability its own box. The
    machinery is gone; what is left is the judgment and one check run against
    real requirements.
    """
    from app.api.skills import _stage_methods

    round_one = _stage_methods()["design"]["rounds"][0]
    # The box-filling procedures are gone, in this round and in the always-on
    # instructions that used to restate them.
    for removed in (
        "MANDATORY CONSUMER ANALYSIS",
        "REQUIRED DESIGN CHECKLIST",
        "TWO OR MORE Features",
    ):
        assert removed not in round_one, removed
    # What replaces them is a size judgment plus a check run against requirements.
    assert "KEEP THE MAP AS SMALL AS THE PROJECT IS" in round_one
    assert "CHECK THE CUT WITH REAL REQUIREMENTS" in round_one
    assert "which SINGLE Feature would deliver it" in round_one
    # Fragmenting must fail the check too, or "one home" is satisfied by boxes.
    assert "passes this test on paper and fails it in practice" in round_one
    # Cross-cutting conventions belong to the backbone, not to Features of their
    # own — the rule that manufactured a "recovery policy" Feature.
    assert "Architecture Foundation's, not blocks of their own" in round_one


def test_design_stage_names_the_architecture_foundations_scope():
    """The Feature stage said an Architecture Foundation must EXIST without
    ever saying what its scope consists of, so it was described as a runtime
    capability ("starts the application") — a scope with nothing in it to
    mine or to design, which sends every later Feature back to inventing its
    own conventions.
    """
    from app.api.skills import _stage_methods

    methods = _stage_methods()
    # The Architecture Foundation's scope is the code backbone, named at the
    # Feature stage — the same list the design stage is told to answer.
    feature_stage = methods["design"]["principles"]
    joined = " ".join(feature_stage)
    assert "ARCHITECTURE FOUNDATION'S SCOPE IS THE PROJECT'S CODE BACKBONE" in joined
    for part in ("module seams", "persistence", "error strategy", "scaffolding"):
        assert part in joined, part
    # Fixing the scope is not the same as making the choices inside it.
    assert "CHOICES inside that scope are made in its" in joined


def test_change_method_defaults_to_adding_after_delivery():
    from app.api.skills import _stage_methods

    method = _stage_methods()["change"]
    principles = " ".join(method["principles"])
    assert "SHIPPED" in principles
    assert "ADD, not to" in principles
    # An ordinary repair can use existing authorization; new scope cannot hide in it.
    assert "without another user confirmation" in principles
    assert "never soften the impact or disguise new scope as a repair" in principles
    policy = method["approval_policy"]
    assert "explicit user ask-first rule" in policy["autonomous_repair"]
    assert "lowering acceptance" in policy["ask_user"]
    assert "waivers" in policy["ask_user"]
    assert "Do not fabricate" in policy["evidence"]
    # Asking what breaks must not require creating a record first.
    assert "harnoq_change_request_impact" in method["orient_first"]


def test_current_stage_is_planning_complete_when_the_only_feature_is_done():
    from app.api.skills import _build_feature_map, _current_stage

    constitution = {"status": "confirmed"}
    inputs = [{"title": "i", "status": "confirmed"}]
    done = {
        "id": 1,
        "feature_number": "FEATURE-1",
        "progress": "done",
        "design_numbers": ["DESIGN-1"],
    }
    feature_map = _build_feature_map([done])

    designs = [{"status": "confirmed", "task_count": 1}]
    assert (
        _current_stage(constitution, inputs, designs, feature_map)
        == "planning_complete"
    )


def test_map_rounds_are_not_shipped_once_the_map_exists():
    """Rounds the agent is told to skip are pure payload cost.

    The design method opens with two rounds that draw and order the Feature map,
    then tells anyone holding a map to "skip Rounds 1 and 2 entirely and go
    straight to Round 3" — true for every design call after the mapping session.
    They were still being sent: ~18% of the payload, on every call, in text
    instructed to be ignored. Each round labels itself, so dropping the leading
    two renumbers nothing.
    """
    from app.api.skills import _drop_map_rounds_once_mapped, _stage_methods

    design = _stage_methods()["design"]
    assert design["map_rounds"] == 2
    assert "skip Rounds 1 and 2 entirely" in design["rounds"][0]

    # No features yet: the agent still has a map to draw, so nothing is dropped.
    assert _drop_map_rounds_once_mapped(design, [])["rounds"] == design["rounds"]

    mapped = _drop_map_rounds_once_mapped(design, [{"feature_number": "P1-FEATURE-1"}])
    assert mapped["rounds"] == design["rounds"][2:]
    assert mapped["rounds"][0].startswith("Round 3")
    # Trimming must not mutate the source method or lose its other keys.
    assert len(design["rounds"]) == 5
    assert set(mapped) == set(design)

    # A method without the marker is passed through untouched.
    plain = _stage_methods()["task_planning"]
    assert _drop_map_rounds_once_mapped(plain, [{"x": 1}]) is plain


def test_a_project_without_a_map_still_receives_the_map_rounds(client):
    """The trim must never strand a new project with no way to draw its map.

    Dropping rounds 1-2 is safe only while FEATURE-* exist. A project that has
    confirmed its inputs and constitution but drawn nothing yet is exactly the
    case where those rounds are the whole job — if the trim fired there, the
    agent would open the method at "Round 3 — the DESIGN DRAFT" and design
    against a map that does not exist.
    """
    project = _make_project(client, "unmapped")
    body = client.post(
        "/skills/project-context",
        json={"project_number": project["project_number"]},
    ).get_json()

    assert body["features"] == []
    method = body["active_stage_method"]
    if method and method.get("map_rounds"):
        assert len(method["rounds"]) == 5
        assert method["rounds"][0].startswith("Round 1")


def test_every_structurally_validated_field_documents_its_shape():
    """A field the service rejects on shape must say what shape it wants.

    design_case_service refuses a non-list for impact_areas, rejected_solutions
    and open_questions. Only impact_areas documented that, so a live run wrote
    prose into rejected_solutions, got refused, and had to discover the format
    by failing. (The spec had existed inside the old standalone decision round
    and was lost when that round was merged — which is why it belongs in
    field_standards, where a writer looks for field specs and no round
    restructuring can strand it.)
    """
    import inspect

    from app.api.skills import _stage_methods
    from app.services import design_case_service

    source = inspect.getsource(design_case_service)
    list_fields = {
        name for name in _stage_methods()["design"]["writable_fields"]
        if f'not isinstance(data["{name}"], list)' in source
    }
    assert list_fields == {"impact_areas", "rejected_solutions", "open_questions"}

    standards = _stage_methods()["design"]["field_standards"]
    for name in list_fields:
        assert name in standards, name
        assert "LIST" in standards[name] or "list" in standards[name], name
