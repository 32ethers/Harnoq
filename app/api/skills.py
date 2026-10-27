"""OpenWebUI skill HTTP endpoints.

These endpoints face the skill adapter, not the regular page API. This module
exposes the two read-only context skills (project-context, task-progress) and
the design-time write skills (source-requirement, feature, design-case,
constitution). Write skills route through :func:`run_write_skill` (always
re-runs — see its docstring for why writes are not deduped); read skills
through :func:`run_read_skill`.
"""
import json
import hashlib
from pathlib import Path

import yaml
from flask import Blueprint, Response, jsonify, request

from app.core.agent_text import normalize_agent_text
from app.core.errors import ConflictError, ValidationError
from app.services import skill_service

from .stage_methods import (  # re-exported: prose lives in stage_methods.py
    _STAGE_METHOD_KEY,
    _available_tool_guidance,
    _business_id_rules,
    _stage_directive,
    _stage_methods,
    _workflow_guidance,
    _write_policy_guidance,
)

bp = Blueprint("skills", __name__)


@bp.after_request
def _normalize_outgoing_text(response):
    """Repair literal \\n (and friends) in every skill response.

    This is the agent-facing surface, so prose leaves here for a model to read;
    stored data is untouched (see app.core.agent_text). JSON only — the raw
    openapi document served from this blueprint is JSON too and carries no
    literal escapes to change, so it rides through harmlessly.
    """
    if response.is_json:
        response.set_data(
            json.dumps(normalize_agent_text(response.get_json()), ensure_ascii=False)
        )
    return response

# skills/openapi.yaml at the repo root is the single source of truth for the
# spec; this route just serves it as JSON so an OpenAPI-aware tool server
# (OpenWebUI's "OpenAPI Tool Server", Chatbox, etc.) can point a URL at this
# app and auto-discover the 6 skill endpoints as tools.
_OPENAPI_SPEC_PATH = Path(__file__).resolve().parents[2] / "skills" / "openapi.yaml"


@bp.get("/openapi.json")
def openapi_spec():
    return jsonify(_runtime_openapi_spec())


@bp.get("/openapi.yaml")
def openapi_spec_yaml():
    return Response(
        yaml.safe_dump(_runtime_openapi_spec(), sort_keys=False),
        mimetype="application/yaml",
    )


def _runtime_openapi_spec():
    with open(_OPENAPI_SPEC_PATH, encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    # Keep the checked-in spec as the contract source of truth, but publish the
    # current request origin so OpenWebUI imports and subsequent tool calls hit
    # the actual running Harnoq host instead of a stale LAN IP.
    spec["servers"] = [{
        "url": request.host_url.rstrip("/"),
        "description": "Harnoq runtime server for this request origin",
    }]
    return spec


_COMMON_SKILL_KEYS = {
    "project_number",
    "actor",
    "session_id",
    "idempotency_key",
    "action",
    "payload",
}

# Legacy pre-rename payload key still accepted as an alias for the canonical
# business-number key (input_number -> source_requirement_number). Kept so
# older callers keep working.
_PAYLOAD_ALIASES = {
    "input_number": "source_requirement_number",
}


def _normalize_skill_payload_aliases(payload):
    if not isinstance(payload, dict):
        return payload
    normalized = dict(payload)
    for alias, canonical in _PAYLOAD_ALIASES.items():
        if canonical not in normalized and alias in normalized:
            normalized[canonical] = normalized[alias]
    return normalized


def _body():
    """Parse the request body and resolve project_number -> internal project_id.

    Skill callers (the design-time AI /
    OpenWebUI) only ever see the project's business number (P1), never
    the raw database id. Every skill route reads ``body["project_id"]``
    downstream, so resolving it once here keeps that code unchanged while the
    wire contract uses the number.
    """
    data = request.get_json(silent=True) or {}
    data["payload"] = _normalize_skill_payload_aliases(data.get("payload") or {})
    project_number = data.get("project_number")
    if project_number is None:
        raise ValidationError("project_number is required")
    from app.services import project_service

    data["project_id"] = project_service.get_by_number(project_number).id
    return data


def _body_for_explicit_write(skill_name, forced_action):
    data = request.get_json(silent=True) or {}
    if "payload" not in data:
        data["payload"] = {
            key: value for key, value in data.items() if key not in _COMMON_SKILL_KEYS
        }
    data["payload"] = _normalize_skill_payload_aliases(data.get("payload") or {})
    data["action"] = forced_action
    if data.get("project_number") is None:
        data["project_number"] = _infer_project_number_from_payload(data["payload"])
    body = _body()
    body["actor"] = body.get("actor") or "openwebui"
    body["session_id"] = (
        body.get("session_id")
        or request.headers.get("x-session-id")
        or "openwebui-write"
    )
    return body


def _infer_project_number_from_payload(payload):
    from app.models.design_case import DesignCase
    from app.models.feature import Feature
    from app.models.project import Project
    from app.models.source_requirement import SourceRequirement
    lookups = (
        ("source_requirement_number", SourceRequirement, "source_requirement_number"),
        ("feature_number", Feature, "feature_number"),
        ("design_number", DesignCase, "design_number"),
    )
    for payload_key, model, field_name in lookups:
        business_number = payload.get(payload_key)
        if not business_number:
            continue
        row = model.get_or_none(
            getattr(model, field_name) == business_number,
            model.deleted_at.is_null(),
        )
        if row is None:
            continue
        project = Project.get_or_none(Project.id == row.project_id)
        if project is not None:
            return project.project_number
    raise ValidationError(
        "project_number is required unless the payload contains an existing "
        "business number such as source_requirement_number, feature_number, or "
        "design_number"
    )


def _drop_map_rounds_once_mapped(method, features):
    """Ship only the rounds that can still run.

    A stage method may open with rounds that exist solely to produce something
    the project already has — the design stage's first two draw and order the
    Feature map, and the method itself then tells an agent holding a map to
    "skip Rounds 1 and 2 entirely and go straight to Round 3". Sending them
    anyway costs ~18% of a design-stage payload in text that is instructed to be
    ignored, on every call for the rest of the project's life.

    The count comes from the method (``map_rounds``), not from a stage name
    hardcoded here. Each round carries its own label in its text, so dropping
    the leading ones renumbers nothing.
    """
    count = (method or {}).get("map_rounds")
    if not count or not features:
        return method
    trimmed = dict(method)
    trimmed["rounds"] = method["rounds"][count:]
    return trimmed


def _delivery_context_summary(row):
    """Bound an index row; fetch full prose by its business number when needed."""
    summary = {}
    body_fields = {}
    for key, value in row.items():
        if key in {"id", "project_id"} or key.endswith("_id") or key.endswith("_ids"):
            continue
        if isinstance(value, str) and len(value.encode("utf-8")) > 1200:
            body_fields[key] = {
                "bytes": len(value.encode("utf-8")),
                "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
            }
        else:
            summary[key] = value
    if body_fields:
        summary["body_fields_to_fetch"] = body_fields
    return summary


def _project_context_read_action(project_id, page=1, page_size=20, snapshot_token=None):
    from app.core.workflow_vocabulary import DELIVERY_WORKFLOW_VOCABULARY
    from app.services import (
        change_request_service,
        delivery_gate,
        delivery_item_service,
        delivery_stage_service,
        design_case_service,
        development_task_service,
        feature_service,
        glossary_term_service,
        project_constitution_service,
        project_service,
        source_requirement_service,
        suspect_service,
        task_source_service,
    )

    project = project_service.get(project_id).to_dict()
    # Keep glossary near the hoisted stage method below: its avoid list only
    # works when every planning client sees it before composing prose.
    glossary = [term.to_dict() for term in glossary_term_service.list_by_project(project_id)]
    constitution = project_constitution_service.get_for_project(project_id)
    constitution = constitution.to_dict() if constitution is not None else None
    inputs = [
        _with_source_requirement_actions(ri.to_dict())
        for ri in source_requirement_service.list_by_project(project_id)
    ]
    feature_ctx = feature_service.dict_ctx(project_id)
    features = [
        _with_feature_actions(
            feature.to_dict(
                dependencies=feature_ctx["dependencies_by_feature_id"].get(feature.id, []),
                design_cases=feature_ctx["design_cases_by_feature_id"].get(feature.id, []),
            )
        )
        for feature in feature_service.list_by_project(project_id)
    ]

    # Suspect edges for the whole project in one bulk read, then stamped onto
    # both designs and tasks below (never a query per object).
    stale_by_downstream = suspect_service.stale_edges_by_downstream(project_id)

    tasks = []
    for task in development_task_service.list_by_project(project_id):
        payload = task.to_dict()
        sources = task_source_service.list_sources(task)
        payload["change_requests"] = sources["change_requests"]
        payload["dependencies"] = [
            row.to_dict() for row in development_task_service.list_dependencies(task.id)
        ]
        tasks.append(
            suspect_service.annotate(
                payload, suspect_service.DEVELOPMENT_TASK, task.id, stale_by_downstream
            )
        )

    # Which designs already have a TASK-* — so an agent can SEE that a design
    # is already decomposed and does not re-propose creating a task that
    # exists (task creation is one call = one task; no batch step to re-run).
    task_numbers_by_design = {}
    for task in tasks:
        design_id = task.get("design_case_id")
        if design_id is not None:
            task_numbers_by_design.setdefault(design_id, []).append(task.get("task_number"))
    design_ctx = design_case_service.dict_ctx(project_id)
    design_cases = [
        suspect_service.annotate(
            case.to_dict(ctx=design_ctx)
            | {
                "task_numbers": task_numbers_by_design.get(case.id, []),
                "task_count": len(task_numbers_by_design.get(case.id, [])),
            },
            suspect_service.DESIGN_CASE,
            case.id,
            stale_by_downstream,
        )
        for case in design_case_service.list_by_project(project_id)
    ]
    design_cases = [_with_design_case_actions(item) for item in design_cases]

    # CR-* rides along for the same reason design_cases do: the change stage
    # has a full method and a full tool set, but an agent that cannot SEE an
    # open request has no way to reach either. Shipping the tools without the
    # objects is why this table stayed empty.
    change_requests = [
        _with_change_request_actions(item.to_dict())
        for item in change_request_service.list_by_project(project_id)
    ]

    # Compatibility aggregate for the two item stages. Keep this
    # agent-facing view business-number-only: service/repository code keeps
    # raw ids internally, while a planning agent names the same objects a
    # human sees in the UI.
    from app.repositories import delivery_stage_run_repo
    delivery_stage_runs = [run.to_dict() for run in delivery_stage_run_repo.list_by_project(project_id)]
    delivery_items = [
        {
            "item_number": item.item_number,
            "kind": item.kind,
            "subject": item.subject,
            "source": item.source,
            "driver": item.driver,
            "status": item.status,
            "covered_by": item.covered_by,
            "verified_revision": item.verified_revision,
            "repair_record": item.repair_record,
            "blocked_reason": item.blocked_reason,
            "waive_reason": item.waive_reason,
            "found_in_round": item.found_in_round,
        }
        for item in delivery_item_service.list_by_project(project_id)
    ]
    integration_items = [
        item for item in delivery_items if item["kind"] not in ("journey", "nfr")
    ]
    system_items = [
        item for item in delivery_items if item["kind"] in ("journey", "nfr")
    ]

    constitution_state = (
        constitution["status"] if constitution is not None else "none"
    )
    change_summary = (
        f"project context: constitution {constitution_state}, "
        f"{len(inputs)} source requirements, "
        f"{len(features)} features, "
        f"{len(design_cases)} design cases, "
        f"{sum(1 for c in design_cases if c['suspect'])} of them suspect, "
        f"{len(tasks)} development tasks"
    )

    feature_map = _build_feature_map(features)
    stage_methods = _stage_methods()
    gate = delivery_gate.gate_status(project_id)
    current_stage = _current_stage(
        constitution, inputs, design_cases, feature_map, change_requests, gate
    )

    # Keep the project globally visible without letting a large project be
    # silently truncated by the transport.  The index states exactly which
    # bounded page was returned and how much remains to read.
    all_sections = {
        "source_requirements": inputs, "features": features, "design_cases": design_cases,
        "development_tasks": tasks, "delivery_items": delivery_items, "change_requests": change_requests,
        "delivery_stage_runs": delivery_stage_runs,
    }
    snapshot = hashlib.sha256(json.dumps(all_sections, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:24]
    if snapshot_token is not None and snapshot_token != snapshot:
        raise ConflictError("project context changed since the previous page; restart at page 1")
    try:
        page = int(page)
        page_size = int(page_size)
    except (TypeError, ValueError):
        raise ValidationError("page and page_size must be positive integers")
    if page < 1 or page_size < 1:
        raise ValidationError("page and page_size must be positive integers")
    if page > 1 and not snapshot_token:
        raise ValidationError("snapshot_token from page 1 is required for later pages")
    page_size = min(20, page_size)
    start = (page - 1) * page_size
    flat = [(name, row) for name, rows in all_sections.items() for row in rows]
    selected = flat[start:start + page_size]
    returned_by_section = {name: [] for name in all_sections}
    for name, row in selected:
        returned_by_section[name].append(row)
    seen_by_section = {name: 0 for name in all_sections}
    for name, _ in flat[:start + page_size]:
        seen_by_section[name] += 1
    context_index = {
        name: {"total": len(rows), "returned": len(returned_by_section[name]),
               "unread": len(rows) - seen_by_section[name]}
        for name, rows in all_sections.items()
    }
    inputs, features, design_cases, tasks, delivery_items, change_requests, delivery_stage_runs = (
        returned_by_section[name] for name in all_sections
    )
    if current_stage in {"integration", "system"}:
        inputs, features, design_cases, tasks, delivery_items, change_requests, delivery_stage_runs = (
            [_delivery_context_summary(row) for row in rows]
            for rows in (inputs, features, design_cases, tasks, delivery_items, change_requests, delivery_stage_runs)
        )
        feature_map = dict(feature_map)
        # Feature rows are already part of the paged index; repeating the
        # entire map here would restore the oversized response we just bounded.
        feature_map["features"] = []
        feature_map["foundation_features"] = {
            name: _delivery_context_summary(row) if row else None
            for name, row in feature_map["foundation_features"].items()
        }
        if constitution is not None:
            constitution = _delivery_context_summary(constitution)
        project = _delivery_context_summary(project)
        glossary = [_delivery_context_summary(row) for row in glossary]
    return skill_service.build_envelope(
        object_type="context",
        object_id=project["project_number"],
        status="ready",
        change_summary=change_summary,
        open_questions=[],
        next_step=_stage_directive(current_stage),
        # extra context carried alongside the fixed envelope keys
    ) | {
        # The method that governs the CURRENT stage is hoisted to the very top so
        # an agent (or a truncating MCP client) cannot miss it the way it misses a
        # method buried at char ~79k of a 100k+ blob. It is BINDING, not reference
        # material — the loud next_step above points at it with a self-test.
        "current_stage": current_stage,
        # current_stage labels don't all match stage_methods keys 1:1 — map them
        # so the hoisted method is never None (a mismatch stranded the agent with
        # no binding method).
        "active_stage_method": _drop_map_rounds_once_mapped(
            stage_methods.get(_STAGE_METHOD_KEY.get(current_stage, current_stage)),
            features,
        ),
        # Harnoq's own delivery vocabulary is global, not editable project
        # terminology. Send it only where INTEG-*/SYS-* kinds are active so other
        # stages do not pay the context cost.
        "active_workflow_vocabulary": (
            DELIVERY_WORKFLOW_VOCABULARY
            if current_stage in {"integration", "system"}
            else None
        ),
        "glossary": glossary,
        "glossary_guidance": (
            "Before writing any requirement, design, or task document, read glossary. "
            "For a concept already listed, copy term_native and term_en exactly; do not "
            "replace them with synonyms. A word matching any avoid entry is incorrect: "
            "stop and correct it. For a new concept, first add it to the glossary and "
            "confirm it with the user before using it in a document. "
            "WHAT BELONGS HERE — ONE TEST: SUBSTITUTION. A row exists to stop a wrong "
            "WORD from being written, so justify it by writing the sentence a later "
            "reader would actually produce with the wrong word in it, then check three "
            "things. (1) The wrong word is one somebody would really type — a generic "
            "catch-all, or the natural English name of a neighbouring concept — and NOT "
            "the name of another glossary term: filling avoid with a sibling term is "
            "ADJACENCY POSING AS CONFUSION. (2) The wrong sentence reads naturally and "
            "does not give itself away; a slip that looks absurd on sight needs no "
            "guard. (3) You can state what breaks — which identifier ends up wrong in "
            "code, which criterion ends up wrong in a design, which operation hits the "
            "wrong object. No such sentence, no row. "
            "ADJACENCY IS NOT CONFUSION: two concepts that sit next to each other in "
            "the architecture, each already carrying its own distinct name, earn "
            "nothing here. Their relationship — one excludes the other, one is derived "
            "from the other — is a CONSTRAINT and belongs to the constitution or the "
            "design. "
            "The strongest rows BAN A GENERIC WORD the project overloads: an ordinary "
            "catch-all that silently denotes two things here ('order' used for both the "
            "customer's request and the stored record; 'user' used for both the login "
            "account and the human being). Such a row's job is to forbid the bare word "
            "and force the writer to pick a side — put the generic word in avoid and "
            "say in definition_en which of the two this row is. A row that only supplies "
            "an English translation of an unambiguous noun does no work. "
            "Explicitly NOT qualifications: that the "
            "project coined the word, that it is domain vocabulary, that the user said "
            "it, or that it appears in the source material — if the source already names "
            "something unambiguously, downstream will name it the same way, and a row "
            "adds nothing. A literal compound noun whose English is a word-for-word "
            "rendering is a description, not a term. definition_en identifies the "
            "concept and draws the line against what it is confused with; it never "
            "carries numbers, technology choices or behaviour rules — those belong to "
            "the constitution and the design, which are gated and versioned, while this "
            "table is neither. Expect FEW rows: on material that is already precise, "
            "zero is a correct answer. If most nouns in the document got a row, you ran "
            "a translation pass, not a substitution audit. "
            "This whole table ships on every context read: padding it gets "
            "it skimmed instead of obeyed, at the expense of the rows that matter. "
            "The glossary is ungated and always editable, so it never gates anything "
            "else either: reviewing terms is a separate ask from confirming an object, "
            "and an unsettled term is never a reason to withhold that confirmation."
        ),
        "project": project,
        "constitution": constitution,
        "source_requirements": inputs,
        "features": features,
        "design_cases": design_cases,
        "feature_map": feature_map,
        "development_tasks": tasks,
        "delivery_gate": gate,
        "delivery_items": delivery_items,
        "delivery_stage_runs": delivery_stage_runs,
        "integration_items": integration_items,
        "system_items": system_items,
        "delivery_progress": {
            "resume_at": (
                "integration"
                if gate["integration_owed"]
                else "system"
                if gate["system_owed"]
                else None
            ),
            "open_item_numbers": (
                gate["open_integration_item_numbers"]
                if gate["integration_owed"]
                else gate["open_system_item_numbers"]
                if gate["system_owed"]
                else []
            ),
        },
        "context_page": {"page": page, "page_size": page_size,
                         "snapshot_token": snapshot,
                         "has_more": any(v["unread"] for v in context_index.values()),
                         "next_page": page + 1 if any(v["unread"] for v in context_index.values()) else None,
                         "read_notice": "Read pages 1 through has_more=false with snapshot_token. Long prose is indexed by body_fields_to_fetch (bytes and sha256); fetch each needed object by business number before auditing it."},
        "context_index": context_index,
        "change_requests": change_requests,
        "suspect_guidance": (
            "suspect=true on a design or task means something it was settled "
            "against has changed since — its suspect_upstream lists which, with "
            "a reason per edge: 'upstream_revised' (version moved; basis_"
            "version_number vs current_version_number show by how much) or "
            "'upstream_deleted' (the upstream is gone — soft-deleted or purged; "
            "there is no current version to compare, only the fact that the "
            "coverage no longer has anything to stand on). It is a prompt to "
            "look, NOT a gate: nothing is blocked by it. Read the upstream "
            "change, then either realign the object (it still holds) or reopen "
            "and revise it. A realign cannot clear an 'upstream_deleted' edge "
            "since there is nothing left to re-record against — reopen and drop "
            "the coverage instead. Never realign to clear the flag without "
            "reading the change."
        ),
        "available_tools": _available_tool_guidance(),
        "business_id_rules": _business_id_rules(),
        "workflow_guidance": _workflow_guidance(),
        # The full stage_methods map used to ride along here as reference
        # material — all five methods, ~75k characters, of which the current
        # one was already hoisted into active_stage_method above. That is half
        # this response, and every character of it competes for attention with
        # the project's own content (which is a fifth of the size). Nothing
        # consumed it: it existed so a model could look ahead at a stage it is
        # not in. Only the binding method for the current stage is sent now.
        "write_policy": _write_policy_guidance(),
    }


@bp.post("/skills/project-context")
def project_context():
    data = _body()
    project_id = data.get("project_id")
    envelope = skill_service.run_read_skill(
        project_id=project_id,
        skill_name="harnoq_project_context",
        actor=data.get("actor"),
        session_id=data.get("session_id"),
        action="project-context",
        payload=data,
        read_action=lambda: _project_context_read_action(
            project_id, data.get("page", 1), data.get("page_size", 20), data.get("snapshot_token")
        ),
    )
    return jsonify(envelope)


def _task_progress_read_action(project_id):
    from app.services import development_task_service, project_service

    project_number = project_service.get(project_id).project_number
    tasks = development_task_service.list_by_project_with_gate(project_id)

    gate_summary = _quality_gate_summary(tasks)

    change_summary = f"task progress: {len(tasks)} tasks"

    return skill_service.build_envelope(
        object_type="progress",
        object_id=project_number,
        status="ready",
        change_summary=change_summary,
        open_questions=[],
        next_step=(
            "Use project context if you need business numbers or want to edit "
            "requirement/design/task objects."
        ),
    ) | {
        "tasks": tasks,
        "quality_gate_summary": gate_summary,
        "available_tools": _available_tool_guidance(),
        "business_id_rules": _business_id_rules(),
        "workflow_guidance": _workflow_guidance(),
        "write_policy": _write_policy_guidance(),
    }


def _quality_gate_summary(tasks):
    """Aggregate task progress.

    Used to carry a ``qa_passed`` count and a ``by_quality_gate_status``
    breakdown as well; both were cached copies of what the AgentRun rows say, and both are
    already implied here — a task at ``ready_for_pr``/``completed`` is exactly a
    task whose gate opened (see migration 053).
    """
    total = len(tasks)
    if total == 0:
        return {"total": 0}
    return {
        "total": total,
        "by_status": _count_by(tasks, "status"),
    }


def _count_by(tasks, key):
    counts = {}
    for task in tasks:
        value = task.get(key) or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return counts


def _current_stage(
    constitution, source_requirements, design_cases, feature_map,
    change_requests=None, gate=None,
):
    """Best-effort pipeline position, used to hoist the binding stage method.

    The selected method governs subsequent writes, so delivery-only Features
    must never be mistaken for missing capability designs during repair work.

    ``gate`` is the pre-computed result of
    ``delivery_gate.gate_status(project_id)`` (same pattern as
    ``feature_map``: the caller queries, this function stays a pure,
    unit-testable dict-in/label-out helper with no DB access of its own).
    Optional/None is treated as "nothing owed" so old callers keep working.
    """
    # An unclosed CR-* outranks the pipeline position: it is outstanding work
    # somebody already accepted the cost of, and it has its own method. Until
    # this looked at change_requests the "change" stage was unreachable — the
    # method existed, the tools existed, and nothing ever routed to either.
    # Anything short of closed still has a step owed (an applied request still
    # needs its downstream digested and a resolution written).
    open_changes = [
        item for item in (change_requests or []) if item.get("status") != "closed"
    ]
    if open_changes:
        return "change"
    # Requirement mining comes FIRST. The constitution is harvested from mined
    # INPUT-*, so on a fresh project there is nothing to harvest yet — the agent
    # must create / mine INPUT-*, not author a constitution. INPUT-* shares the
    # 4-state lifecycle (see app/services/lifecycle.py): the requirement stage
    # isn't done until every live INPUT-* is confirmed — a single freshly-
    # created (still-draft) INPUT must NOT bounce the agent straight to
    # constitution, or the "interrogate deeply" stage method never gets more
    # than one turn. (Checking constitution first stranded every early project
    # in the constitution stage and made the requirement stage unreachable.)
    unconfirmed = [item for item in source_requirements if item.get("status") != "confirmed"]
    if not source_requirements or unconfirmed:
        return "requirement"
    if not constitution or constitution.get("status") != "confirmed":
        return "constitution"

    # Capability planning precedes each project-wide delivery run. A newly
    # confirmed design without its task must be planned before integration.
    gate = gate or {}
    pending_design = feature_map.get("pending_design_feature_numbers") or []
    confirmed_designs = [d for d in design_cases if d.get("status") == "confirmed"]
    untasked = [d for d in confirmed_designs if not d.get("task_count")]

    if not pending_design and not untasked:
        if gate.get("integration_owed"):
            return "integration"
        if gate.get("system_owed"):
            return "system"

    if not design_cases or pending_design:
        return "design"
    # Past design: if every confirmed DESIGN already has a TASK-*, the task-
    # creation work is done — stop serving the "create a task" directive so
    # the agent doesn't re-propose one that already exists. (task_count comes
    # from the project-context decoration above.)
    if confirmed_designs and not untasked:
        return "planning_complete"
    return "task_planning"


def _with_source_requirement_actions(item):
    source_requirement_number = item.get("source_requirement_number")
    return item | {
        "ai_available_actions": {
            "append_text_to_this_input": {
                "tool_name": "harnoq_source_requirement_append_content",
                "required_arguments": ["source_requirement_number", "raw_content_to_append"],
                "example_arguments": {
                    "source_requirement_number": source_requirement_number,
                    "raw_content_to_append": "New requirement text to append.",
                },
                "use_when": (
                    "User asks to add, append, supplement, or add item N to "
                    f"{source_requirement_number}."
                ),
                "must_call_tool": True,
            },
            "replace_or_edit_this_input": {
                "tool_name": "harnoq_source_requirement_update",
                "required_arguments": ["source_requirement_number"],
                "example_arguments": {
                    "source_requirement_number": source_requirement_number,
                    "raw_content": "Replacement full requirement text.",
                },
                "use_when": (
                    "User asks to revise existing fields on this Source Requirement."
                ),
                "must_call_tool": True,
            },
            "confirm_this_input": {
                "tool_name": "harnoq_source_requirement_confirm",
                "required_arguments": ["source_requirement_number"],
                "example_arguments": {"source_requirement_number": source_requirement_number},
                "use_when": "The requirement discussion has genuinely gone deep (goal / acceptance / constraints / failure modes) and the user agrees it captures the requirement — never confirm just to advance the pipeline.",
                "must_call_tool": True,
            },
            "reopen_this_input": {
                "tool_name": "harnoq_source_requirement_reopen",
                "required_arguments": ["source_requirement_number"],
                "example_arguments": {"source_requirement_number": source_requirement_number},
                "use_when": "status is confirmed and the user wants to revise it — this is the only way to edit a locked INPUT-*.",
                "must_call_tool": True,
            },
        }
    }


def _with_design_case_actions(item):
    design_number = item.get("design_number")
    return item | {
        "ai_available_actions": {
            "edit_this_design_case": {
                "tool_name": "harnoq_design_case_update",
                "required_arguments": ["design_number"],
                "example_arguments": {"design_number": design_number},
                "must_call_tool": True,
            },
            "confirm_this_design_case": {
                "tool_name": "harnoq_design_case_confirm",
                "required_arguments": ["design_number"],
                "example_arguments": {"design_number": design_number},
                "must_call_tool": True,
            },
            "reopen_this_design_case": {
                "tool_name": "harnoq_design_case_reopen",
                "required_arguments": ["design_number"],
                "example_arguments": {"design_number": design_number},
                "use_when": "status is confirmed and the user wants to revise it, or reject it back for rework — this is the only way to edit a locked design.",
                "must_call_tool": True,
            },
            "realign_this_design_case_to_its_upstream": {
                "tool_name": "harnoq_design_case_realign_basis",
                "required_arguments": ["design_number"],
                "example_arguments": {"design_number": design_number},
                "use_when": "suspect is true and you have READ the upstream change and judged this design still holds as written — it records that judgement. If the design does not still hold, reopen and revise it instead.",
                "must_call_tool": True,
            },
            "create_task_under_this_design_case": {
                "tool_name": "harnoq_task_create",
                "required_arguments": [
                    "design_number",
                    "title",
                    "task_type",
                    "goal",
                    "acceptance_criteria",
                    "constraints",
                    "non_goals",
                ],
                "example_arguments": {"design_number": design_number},
                "use_when": "status is confirmed and this design needs a (or one more) TASK-*.",
                "must_call_tool": True,
            },
        }
    }


def _with_feature_actions(item):
    feature_number = item.get("feature_number")
    return item | {
        "ai_available_actions": {
            "edit_this_feature": {
                "tool_name": "harnoq_feature_update",
                "required_arguments": ["feature_number"],
                "example_arguments": {"feature_number": feature_number},
                "must_call_tool": True,
            },
            "assign_this_feature": {
                "tool_name": "harnoq_feature_assign",
                "required_arguments": ["feature_number", "assignee_number"],
                "example_arguments": {"feature_number": feature_number},
                "must_call_tool": True,
            },
            "delete_this_feature": {
                "tool_name": "harnoq_feature_delete",
                "required_arguments": ["feature_number"],
                "example_arguments": {"feature_number": feature_number},
                "must_call_tool": True,
            },
        }
    }


def _build_feature_map(features):
    """Mechanical feature-completeness report: which FEATURE-* blocks still
    need a confirmed DESIGN-*. FEATURE/DESIGN carry no formal INPUT-* link
    (see app/models/feature.py / app/models/design_case.py), so completeness
    is tracked purely at the feature/design level — not by INPUT coverage."""
    summarized_features = [
        {
            "feature_number": feature.get("feature_number"),
            "name": feature.get("name"),
            # The block's design-entry card (see the design stage_method's
            # description guidance) — kept in the summary too, not just the
            # full features list, so an agent working from feature_map alone
            # doesn't lose the block's boundary/contract/ownership.
            "description": feature.get("description"),
            "orientation": feature.get("orientation"),
            "foundation_kind": feature.get("foundation_kind"),
            "subdomain_type": feature.get("subdomain_type"),
            "assignee": feature.get("assignee"),
            "assignee_number": feature.get("assignee_number"),
            "assignee_user": feature.get("assignee_user"),
            "progress": feature.get("progress"),
            "design_status": feature.get("design_status") or ("confirmed" if feature.get("progress") == "done" else feature.get("progress")),
            "implementation_status": feature.get("implementation_status"),
            "design_numbers": feature.get("design_numbers") or [],
        }
        for feature in features
    ]
    pending_design = [
        feature["feature_number"]
        for feature in summarized_features
        if feature["design_status"] != "confirmed"
    ]
    return {
        "features": summarized_features,
        "foundation_features": {
            kind: next(
                (feature for feature in summarized_features if feature["foundation_kind"] == kind),
                None,
            )
            for kind in ("architecture", "ui")
        },
        "pending_design_feature_numbers": pending_design,
        "all_features_designed": not pending_design,
    }

@bp.post("/skills/task-progress")
def task_progress():
    data = _body()
    project_id = data.get("project_id")
    envelope = skill_service.run_read_skill(
        project_id=project_id,
        skill_name="harnoq_task_progress",
        actor=data.get("actor"),
        session_id=data.get("session_id"),
        action="task-progress",
        payload=data,
        read_action=lambda: _task_progress_read_action(project_id),
    )
    return jsonify(envelope)


# ---------------------------------------------------------------------------
# Design-time write skills
#
# Each write endpoint picks a handler by ``action`` from a {action: factory}
# table and routes through run_write_skill, which supplies the 6-key envelope
# and true idempotency. ``payload`` is the data the skill passes to the
# underlying service; the handler returns the fixed 5-tuple
# (target_type, target_id, change_summary, open_questions, next_step).
#
# The direct-task model: the old human-only gate on creating DevelopmentTasks is gone —
# create_task IS exposed at the skill layer, so an agent can create a
# claimable TASK-* directly from a confirmed DESIGN-*.
# ---------------------------------------------------------------------------


def _dispatch_write(skill_name, body, handlers):
    """Dispatch a write skill by action through run_write_skill.

    ``handlers`` maps action name -> write_action factory. A factory takes the
    request ``payload`` and returns a write_action callable (no args) returning
    the 5-tuple the contract layer expects. This split lets factory-time
    payload validation (which raises ValidationError, recorded as ``failed`` by
    the contract layer) run before the action is handed off.
    """
    action = body.get("action")
    if not action:
        raise ValidationError("action is required")
    factory = handlers.get(action)
    if factory is None:
        raise ValidationError(
            f"unsupported action '{action}' for skill '{skill_name}'"
        )
    write_action = factory(body.get("payload") or {})
    return skill_service.run_write_skill(
        project_id=body.get("project_id"),
        skill_name=skill_name,
        actor=body.get("actor"),
        session_id=body.get("session_id"),
        idempotency_key=body.get("idempotency_key"),
        action=action,
        payload=body.get("payload") or {},
        write_action=write_action,
    )


def _dispatch_read(skill_name, body, read_action):
    """Dispatch a read-only skill action through run_read_skill."""
    return skill_service.run_read_skill(
        project_id=body.get("project_id"),
        skill_name=skill_name,
        actor=body.get("actor"),
        session_id=body.get("session_id"),
        action=body.get("action"),
        payload=body.get("payload") or {},
        read_action=read_action,
    )


# --- harnoq_source_requirement ---------------------------------------------


def _normalize_source_requirement_payload(payload):
    normalized = dict(payload)
    if "raw_content" not in normalized:
        if "description" in normalized:
            normalized["raw_content"] = normalized["description"]
        elif "content" in normalized:
            normalized["raw_content"] = normalized["content"]
    return normalized


def _source_requirement_skill_response(*, forced_action=None, explicit_body=None):
    from app.services import source_requirement_service

    skill_name = "harnoq_source_requirement"
    body = explicit_body if explicit_body is not None else _body()
    action = forced_action or body.get("action")
    if forced_action:
        body["action"] = forced_action

    # Read actions: get / list (no idempotency_key required).
    if action == "get":
        payload = body.get("payload") or {}
        source_requirement_number = _require_id(payload, "source_requirement_number")

        def read_action():
            item = _with_source_requirement_actions(
                source_requirement_service.get_by_number(
                    body.get("project_id"), source_requirement_number
                ).to_dict()
            )
            return skill_service.build_envelope(
                object_type="source_requirement",
                object_id=source_requirement_number,
                status="ready",
                change_summary=f"source requirement {source_requirement_number} fetched",
            ) | {"source_requirement": item}

        return jsonify(_dispatch_read(skill_name, body, read_action))

    if action == "list":
        project_id = body.get("project_id")

        def read_action():
            items = [
                _with_source_requirement_actions(ri.to_dict())
                for ri in source_requirement_service.list_by_project(project_id)
            ]
            return skill_service.build_envelope(
                object_type="source_requirement",
                object_id=project_id,
                status="ready",
                change_summary=f"listed {len(items)} source requirements",
            ) | {"source_requirements": items}

        return jsonify(_dispatch_read(skill_name, body, read_action))

    # Write actions: create / update / append_content / confirm / reopen.
    # Shares the 3-state lifecycle (see app/services/lifecycle.py) — confirm
    # has no required-field checklist.
    def create_factory(payload):
        payload = _normalize_source_requirement_payload(payload)
        if not payload.get("source_type"):
            payload["source_type"] = "manual_note"

        def write_action():
            project_id = body["project_id"]
            item = source_requirement_service.create(project_id, payload)
            return (
                "source_requirement",
                item.source_requirement_number,
                f"created source requirement '{item.title}'",
                [],
                "Review the input, then mine it by conversation before composing a feature.",
            )

        return write_action

    def update_factory(payload):
        payload = _normalize_source_requirement_payload(payload)
        source_requirement_id = _resolve_id(
            body, payload, "source_requirement_number", source_requirement_service.get_by_number
        )

        def write_action():
            item = source_requirement_service.update(source_requirement_id, payload)
            return (
                "source_requirement",
                item.source_requirement_number,
                f"updated source requirement {item.source_requirement_number}",
                [],
                None,
            )

        return write_action

    def append_content_factory(payload):
        payload = _normalize_source_requirement_payload(payload)
        source_requirement_id = _resolve_id(
            body, payload, "source_requirement_number", source_requirement_service.get_by_number
        )
        append_text = (
            payload.get("raw_content_to_append")
            or payload.get("additional_content")
            or payload.get("text")
            or payload.get("raw_content")
        )
        if append_text is None or (
            isinstance(append_text, str) and not append_text.strip()
        ):
            raise ValidationError("raw_content_to_append is required")
        separator = payload.get("separator")
        if separator is None:
            separator = "\n"

        def write_action():
            current = source_requirement_service.get(source_requirement_id)
            existing = current.raw_content or ""
            addition = str(append_text)
            if existing and separator and not existing.endswith(separator):
                raw_content = f"{existing}{separator}{addition}"
            else:
                raw_content = f"{existing}{addition}"
            item = source_requirement_service.update(
                source_requirement_id, {"raw_content": raw_content}
            )
            return (
                "source_requirement",
                item.source_requirement_number,
                f"appended content to source requirement {item.source_requirement_number}",
                [],
                None,
            )

        return write_action

    def confirm_factory(payload):
        source_requirement_id = _resolve_id(
            body, payload, "source_requirement_number", source_requirement_service.get_by_number
        )

        def write_action():
            item = source_requirement_service.confirm(source_requirement_id)
            return (
                "source_requirement",
                item.source_requirement_number,
                f"confirmed source requirement {item.source_requirement_number}",
                [],
                "Once every INPUT-* in the project is confirmed, the constitution "
                "stage can begin.",
            )

        return write_action

    def reopen_factory(payload):
        source_requirement_id = _resolve_id(
            body, payload, "source_requirement_number", source_requirement_service.get_by_number
        )

        def write_action():
            item = source_requirement_service.reopen(source_requirement_id)
            return (
                "source_requirement",
                item.source_requirement_number,
                f"reopened source requirement {item.source_requirement_number} to draft",
                [],
                "Revise the requirement, then confirm it again when ready.",
            )

        return write_action

    handlers = {
        "create": create_factory,
        "update": update_factory,
        "append_content": append_content_factory,
        "confirm": confirm_factory,
        "reopen": reopen_factory,
    }
    return jsonify(_dispatch_write(skill_name, body, handlers))


@bp.post("/skills/source-requirement")
def source_requirement_skill():
    return _source_requirement_skill_response()


@bp.post("/skills/source-requirement-create")
def source_requirement_create_skill():
    return _source_requirement_skill_response(
        forced_action="create",
        explicit_body=_body_for_explicit_write(
            "harnoq_source_requirement", "create"
        ),
    )


@bp.post("/skills/source-requirement-update")
def source_requirement_update_skill():
    return _source_requirement_skill_response(
        forced_action="update",
        explicit_body=_body_for_explicit_write(
            "harnoq_source_requirement", "update"
        ),
    )


@bp.post("/skills/source-requirement-append-content")
def source_requirement_append_content_skill():
    return _source_requirement_skill_response(
        forced_action="append_content",
        explicit_body=_body_for_explicit_write(
            "harnoq_source_requirement", "append_content"
        ),
    )


@bp.post("/skills/source-requirement-confirm")
def source_requirement_confirm_skill():
    return _source_requirement_skill_response(
        forced_action="confirm",
        explicit_body=_body_for_explicit_write(
            "harnoq_source_requirement", "confirm"
        ),
    )


@bp.post("/skills/source-requirement-reopen")
def source_requirement_reopen_skill():
    return _source_requirement_skill_response(
        forced_action="reopen",
        explicit_body=_body_for_explicit_write(
            "harnoq_source_requirement", "reopen"
        ),
    )


# --- harnoq_feature -------------------------------------------------------


def _normalize_feature_payload(project_id, payload):
    from app.services import feature_service

    normalized = dict(payload)
    # Dependency edges are addressed by FEATURE-* numbers at the skill boundary
    # (编号/id 原则); resolve them to internal ids for the service.
    depends_on_feature_numbers = normalized.pop("depends_on_feature_numbers", None)
    if (
        depends_on_feature_numbers is not None
        and "depends_on_feature_ids" not in normalized
    ):
        if not isinstance(depends_on_feature_numbers, list):
            raise ValidationError("depends_on_feature_numbers must be a list")
        normalized["depends_on_feature_ids"] = [
            feature_service.get_by_number(project_id, feature_number).id
            for feature_number in depends_on_feature_numbers
        ]
    return normalized


def _feature_skill_response(*, forced_action=None, explicit_body=None):
    from app.services import feature_service

    skill_name = "harnoq_feature"
    body = explicit_body if explicit_body is not None else _body()
    action = forced_action or body.get("action")
    if forced_action:
        body["action"] = forced_action

    if action == "get":
        payload = body.get("payload") or {}
        feature_number = _require_id(payload, "feature_number")

        def read_action():
            item = _with_feature_actions(
                feature_service.get_by_number(
                    body.get("project_id"), feature_number
                ).to_dict()
            )
            return skill_service.build_envelope(
                object_type="feature",
                object_id=feature_number,
                status="ready",
                change_summary=f"feature {feature_number} fetched",
            ) | {"feature": item}

        return jsonify(_dispatch_read(skill_name, body, read_action))

    if action == "list":
        def read_action():
            project_id = body.get("project_id")
            feature_ctx = feature_service.dict_ctx(project_id)
            items = [
                _with_feature_actions(
                    feature.to_dict(
                        dependencies=feature_ctx["dependencies_by_feature_id"].get(feature.id, []),
                        design_cases=feature_ctx["design_cases_by_feature_id"].get(feature.id, []),
                    )
                )
                for feature in feature_service.list_by_project(project_id)
            ]
            return skill_service.build_envelope(
                object_type="feature",
                object_id=body.get("project_id"),
                status="ready",
                change_summary=f"listed {len(items)} feature(s)",
            ) | {"features": items}

        return jsonify(_dispatch_read(skill_name, body, read_action))

    def create_factory(payload):
        payload = _normalize_feature_payload(body.get("project_id"), payload)

        def write_action():
            feature = feature_service.create(body.get("project_id"), payload)
            return (
                "feature",
                feature.feature_number,
                f"created feature {feature.feature_number}",
                [],
                "Create DESIGN-* records under this feature when the user selects one to design.",
            )

        return write_action

    def update_factory(payload):
        payload = _normalize_feature_payload(body.get("project_id"), payload)
        feature_id = _resolve_id(
            body, payload, "feature_number", feature_service.get_by_number
        )

        def write_action():
            feature = feature_service.update(feature_id, payload)
            return (
                "feature",
                feature.feature_number,
                f"updated feature {feature.feature_number}",
                [],
                None,
            )

        return write_action

    def assign_factory(payload):
        feature_id = _resolve_id(
            body, payload, "feature_number", feature_service.get_by_number
        )
        assignment = {"assignee_number": payload["assignee_number"]} if "assignee_number" in payload else {"assignee": payload.get("assignee")}

        def write_action():
            feature = feature_service.update(feature_id, assignment)
            return (
                "feature",
                feature.feature_number,
                f"assigned feature {feature.feature_number} to {feature.assignee or '-'}",
                [],
                None,
            )

        return write_action

    def delete_factory(payload):
        feature_id = _resolve_id(
            body, payload, "feature_number", feature_service.get_by_number
        )

        def write_action():
            feature = feature_service.delete(feature_id)
            return (
                "feature",
                feature.feature_number,
                f"deleted feature {feature.feature_number} (recoverable via restore)",
                [],
                None,
            )

        return write_action

    handlers = {
        "create": create_factory,
        "update": update_factory,
        "assign": assign_factory,
        "delete": delete_factory,
    }
    return jsonify(_dispatch_write(skill_name, body, handlers))


@bp.post("/skills/feature")
def feature_skill():
    return _feature_skill_response()


@bp.post("/skills/feature-create")
def feature_create_skill():
    return _feature_skill_response(
        forced_action="create",
        explicit_body=_body_for_explicit_write("harnoq_feature", "create"),
    )


@bp.post("/skills/feature-update")
def feature_update_skill():
    return _feature_skill_response(
        forced_action="update",
        explicit_body=_body_for_explicit_write("harnoq_feature", "update"),
    )


@bp.post("/skills/feature-delete")
def feature_delete_skill():
    return _feature_skill_response(
        forced_action="delete",
        explicit_body=_body_for_explicit_write("harnoq_feature", "delete"),
    )


@bp.post("/skills/feature-assign")
def feature_assign_skill():
    return _feature_skill_response(
        forced_action="assign",
        explicit_body=_body_for_explicit_write("harnoq_feature", "assign"),
    )


# --- harnoq_constitution ---------------------------------------------------


def _project_constitution_skill_response(*, forced_action=None, explicit_body=None):
    """Skill layer for the singleton project constitution. Keyed by
    project_number (no per-entity business number), so actions read project_id
    straight from the resolved body."""
    from app.services import project_constitution_service

    skill_name = "harnoq_constitution"
    body = explicit_body if explicit_body is not None else _body()
    action = forced_action or body.get("action")
    if forced_action:
        body["action"] = forced_action
    project_id = body.get("project_id")
    target_id = body.get("project_number") or project_id

    if action == "get":
        def read_action():
            constitution = project_constitution_service.get_for_project(project_id)
            item = constitution.to_dict() if constitution is not None else None
            return skill_service.build_envelope(
                object_type="constitution",
                object_id=target_id,
                status="ready",
                change_summary=(
                    f"constitution {item['status']}"
                    if item is not None
                    else "no constitution yet"
                ),
            ) | {"constitution": item}

        return jsonify(_dispatch_read(skill_name, body, read_action))

    def update_factory(payload):
        def write_action():
            constitution = project_constitution_service.update(project_id, payload)
            return (
                "constitution",
                target_id,
                f"updated constitution (status {constitution.status})",
                [],
                None,
            )

        return write_action

    def confirm_factory(payload):
        def write_action():
            project_constitution_service.confirm(project_id)
            return (
                "constitution",
                target_id,
                "confirmed constitution",
                [],
                "Design may now begin under this constitution.",
            )

        return write_action

    def reopen_factory(payload):
        def write_action():
            project_constitution_service.reopen(project_id)
            return (
                "constitution",
                target_id,
                "reopened constitution to draft",
                [],
                "Revise the constitution, then confirm it again when ready.",
            )

        return write_action

    handlers = {
        "update": update_factory,
        "confirm": confirm_factory,
        "reopen": reopen_factory,
    }
    return jsonify(_dispatch_write(skill_name, body, handlers))


@bp.post("/skills/constitution")
def constitution_skill():
    return _project_constitution_skill_response()


@bp.post("/skills/constitution-update")
def constitution_update_skill():
    return _project_constitution_skill_response(
        forced_action="update",
        explicit_body=_body_for_explicit_write("harnoq_constitution", "update"),
    )


@bp.post("/skills/constitution-confirm")
def constitution_confirm_skill():
    return _project_constitution_skill_response(
        forced_action="confirm",
        explicit_body=_body_for_explicit_write("harnoq_constitution", "confirm"),
    )


@bp.post("/skills/constitution-reopen")
def constitution_reopen_skill():
    return _project_constitution_skill_response(
        forced_action="reopen",
        explicit_body=_body_for_explicit_write("harnoq_constitution", "reopen"),
    )


# --- harnoq_glossary -------------------------------------------------------


def _glossary_term_for_native(project_id, term_native):
    """Resolve the glossary's user-facing key without exposing raw ids."""
    from app.repositories import glossary_term_repo

    term = glossary_term_repo.get_by_native(project_id, term_native)
    if term is None:
        raise ValidationError(f"glossary term {term_native!r} not found")
    return term


def _glossary_skill_response(*, forced_action=None, explicit_body=None):
    from app.services import glossary_term_service

    skill_name = "harnoq_glossary"
    body = explicit_body if explicit_body is not None else _body()
    action = forced_action or body.get("action")
    if forced_action:
        body["action"] = forced_action
    project_id = body["project_id"]
    project_number = body["project_number"]

    if action in ("get", "list"):
        def read_action():
            glossary = [term.to_dict() for term in glossary_term_service.list_by_project(project_id)]
            return skill_service.build_envelope(
                object_type="glossary",
                object_id=project_number,
                status="ready",
                change_summary=f"{len(glossary)} glossary terms",
            ) | {"glossary": glossary}

        return jsonify(_dispatch_read(skill_name, body, read_action))

    def create_factory(payload):
        def write_action():
            term = glossary_term_service.create(project_id, payload)
            return (
                "glossary_term", term.term_native,
                f"added glossary term {term.term_native}", [], None,
            )
        return write_action

    def update_factory(payload):
        existing_native = payload.pop("existing_term_native", None)
        if not isinstance(existing_native, str) or not existing_native.strip():
            raise ValidationError("existing_term_native is required for glossary update")
        term_id = _glossary_term_for_native(project_id, existing_native.strip()).id

        def write_action():
            term = glossary_term_service.update(term_id, payload)
            return (
                "glossary_term", term.term_native,
                f"updated glossary term {term.term_native}", [], None,
            )
        return write_action

    def delete_factory(payload):
        term_native = payload.get("term_native")
        if not isinstance(term_native, str) or not term_native.strip():
            raise ValidationError("term_native is required for glossary delete")
        term_id = _glossary_term_for_native(project_id, term_native.strip()).id

        def write_action():
            term = glossary_term_service.delete(term_id)
            return (
                "glossary_term", term.term_native,
                f"deleted glossary term {term.term_native}", [], None,
            )
        return write_action

    return jsonify(_dispatch_write(skill_name, body, {
        "create": create_factory,
        "update": update_factory,
        "delete": delete_factory,
    }))


@bp.post("/skills/glossary")
def glossary_skill():
    return _glossary_skill_response()


@bp.post("/skills/glossary-create")
def glossary_create_skill():
    return _glossary_skill_response(
        forced_action="create",
        explicit_body=_body_for_explicit_write("harnoq_glossary", "create"),
    )


@bp.post("/skills/glossary-update")
def glossary_update_skill():
    return _glossary_skill_response(
        forced_action="update",
        explicit_body=_body_for_explicit_write("harnoq_glossary", "update"),
    )


@bp.post("/skills/glossary-delete")
def glossary_delete_skill():
    return _glossary_skill_response(
        forced_action="delete",
        explicit_body=_body_for_explicit_write("harnoq_glossary", "delete"),
    )


# --- harnoq_delivery_item ---------------------------------------------------
#
# DeliveryItem (INTEG-*/SYS-*) — see app/models/delivery_item.py. Round 1 of the
# integration/system stage methods asks the agent to batch-create these, so
# create/update must be reachable the same way every other write is: through
# a skill endpoint, not just the plain REST API in app/api/delivery_items.py.


_INTEGRATION_DELIVERY_KINDS = frozenset({"assembly", "seam", "duplicate"})
_SYSTEM_DELIVERY_KINDS = frozenset({"journey", "nfr"})
_DELIVERY_KINDS = _INTEGRATION_DELIVERY_KINDS | _SYSTEM_DELIVERY_KINDS


def _ensure_delivery_kind_matches_active_gate(project_id, kind):
    """Reject AI writes that cross the live Integration/System boundary.

    The human-facing CRUD pages remain useful for correcting records manually,
    but an agent write goes through this skill boundary and must agree with the
    derived delivery gate. Prompt discipline alone is not enough: a Build-only
    manager once lacked project_context yet still had the wildcard write tools,
    and created System scenarios during Integration. This is the backstop that
    makes the same mistake impossible even when routing instructions are missed.
    """
    # Missing/unknown values belong to the ordinary service validator, which
    # returns a precise 400. This guard is only the cross-stage 409 backstop.
    if kind not in _DELIVERY_KINDS:
        return

    from app.services import delivery_gate

    gate = delivery_gate.gate_status(project_id)
    if gate["integration_owed"]:
        stage = "integration"
        allowed = _INTEGRATION_DELIVERY_KINDS
    elif gate["system_owed"]:
        stage = "system"
        allowed = _SYSTEM_DELIVERY_KINDS
    else:
        raise ConflictError(
            "item writes are not open for this project: call "
            "harnoq_project_context and follow its current_stage before "
            "creating or changing an INTEG-* or SYS-* item"
        )
    if kind not in allowed:
        raise ConflictError(
            f"kind={kind!r} belongs to the other delivery stage; current_stage "
            f"is {stage} and only {', '.join(sorted(allowed))} are writable. "
            "Call harnoq_project_context and follow active_stage_method."
        )


def _ensure_delivery_write_provenance(payload, current=None):
    """Preserve the two reentry/provenance facts Agent-written rows depend on.

    Human CRUD remains permissive enough for manual correction. Planning agents,
    however, create INTEG-*/SYS-* only from a stage inventory: an empty source loses
    where the finding came from, while an implicit round silently mislabels every
    reentry after the first one. Keep those mistakes out at the skill boundary.
    """
    source = payload.get("source", current.source if current is not None else None)
    if not isinstance(source, str) or not source.strip():
        raise ValidationError(
            "source is required for Agent item writes: cite the exact "
            "fully qualified business number and field that declared the finding, "
            "or the exact repository command/code location that discovered it"
        )
    found_in_round = payload.get(
        "found_in_round", current.found_in_round if current is not None else None
    )
    if (
        not isinstance(found_in_round, int)
        or isinstance(found_in_round, bool)
        or found_in_round < 1
    ):
        raise ValidationError(
            "found_in_round is required for Agent item writes and must "
            "be a positive integer computed from the current inventory"
        )
    payload["source"] = source.strip()


def _delivery_item_create_next_step(kind):
    if kind in _INTEGRATION_DELIVERY_KINDS:
        return (
            "Stay in the current Integration active_stage_method round and "
            "finish the full assembly/seam/duplicate inventory and "
            "reconciliation. This per-item result does not authorize a TASK-* "
            "or waiver; Round 2 clusters items by explicit executable driver "
            "rather than creating one task per item."
        )
    return (
        "Stay in the current System active_stage_method round: collect both "
        "mandatory scenario sources through the complete "
        "INPUT/NFR audit. Give every journey/NFR an explicit executable "
        "driver and consume every key in the current validated receipt. This "
        "per-item result does not authorize a TASK-* or waiver; cluster work only "
        "after the server reports no pending inventory keys."
    )


def _delivery_item_skill_response(*, forced_action=None, explicit_body=None):
    from app.services import delivery_item_service

    skill_name = "harnoq_delivery_item"
    body = explicit_body if explicit_body is not None else _body()
    action = forced_action or body.get("action")
    if forced_action:
        body["action"] = forced_action
    project_id = body.get("project_id")

    if action == "list":
        payload = body.get("payload") or {}
        kinds = payload.get("kind") or payload.get("kinds")
        if isinstance(kinds, str):
            kinds = [kinds]

        def read_action():
            items = [
                {
                    "item_number": item.item_number,
                    "kind": item.kind,
                    "subject": item.subject,
                    "source": item.source,
                    "driver": item.driver,
                    "status": item.status,
                    "covered_by": item.covered_by,
                    "waive_reason": item.waive_reason,
                    "found_in_round": item.found_in_round,
                    **{key: value for key, value in item.to_dict().items() if key.startswith("assignee")},
                }
                for item in delivery_item_service.list_by_project(project_id, kinds=kinds)
            ]
            return skill_service.build_envelope(
                object_type="delivery_item",
                object_id=body["project_number"],
                status="ready",
                change_summary=f"listed {len(items)} delivery item(s)",
            ) | {"delivery_items": items}

        return jsonify(_dispatch_read(skill_name, body, read_action))

    def create_factory(payload):
        def write_action():
            kind = payload.get("kind")
            _ensure_delivery_kind_matches_active_gate(project_id, kind)
            if kind in _DELIVERY_KINDS:
                _ensure_delivery_write_provenance(payload)
            source_row_key = payload.pop("source_row_key", None)
            validation_id = payload.pop("validation_id", None)
            if kind in _INTEGRATION_DELIVERY_KINDS:
                from app.db.base import database_proxy
                from app.services import integration_inventory_validator

                # Consuming the receipt and creating its INTEG row are one
                # transaction: neither half may survive on its own.
                with database_proxy.atomic():
                    receipt = integration_inventory_validator.authorize_create(
                        project_id,
                        validation_id,
                        source_row_key,
                        payload,
                    )
                    item = delivery_item_service.create(project_id, payload)
                    integration_inventory_validator.record_created(
                        receipt, source_row_key, item.item_number
                    )
            elif kind in _SYSTEM_DELIVERY_KINDS:
                from app.db.base import database_proxy
                from app.services import system_inventory_validator

                with database_proxy.atomic():
                    receipt = system_inventory_validator.authorize_create(
                        project_id, validation_id, source_row_key, payload
                    )
                    item = delivery_item_service.create(project_id, payload)
                    system_inventory_validator.record_created(
                        receipt, source_row_key, item.item_number
                    )
            else:
                item = delivery_item_service.create(project_id, payload)
            return (
                "delivery_item",
                item.item_number,
                f"created delivery item {item.item_number} ({item.kind})",
                [],
                _delivery_item_create_next_step(item.kind),
            )
        return write_action

    def update_factory(payload):
        item_id = _resolve_id(
            body, payload, "item_number", delivery_item_service.get_by_number
        )

        def write_action():
            current = delivery_item_service.get(item_id)
            # A scoped user waiver records a decision about an existing
            # obligation; it does not execute a later stage. Requiring that
            # stage to be open can deadlock a repair whose contract includes
            # recording the waiver. Creation, coverage and scope edits remain
            # gated, and the normal service still requires a waiver reason.
            waiver_only = (
                payload.get("status") == "waived"
                and set(payload) <= {"item_number", "status", "waive_reason"}
            )
            if not waiver_only:
                _ensure_delivery_kind_matches_active_gate(
                    project_id, payload.get("kind", current.kind)
                )
            _ensure_delivery_write_provenance(payload, current)
            item = delivery_item_service.update(item_id, payload)
            return (
                "delivery_item",
                item.item_number,
                f"updated delivery item {item.item_number} (status={item.status})",
                [],
                None,
            )
        return write_action

    return jsonify(_dispatch_write(skill_name, body, {
        "create": create_factory,
        "update": update_factory,
    }))


@bp.post("/skills/delivery-item")
def delivery_item_skill():
    return _delivery_item_skill_response()


@bp.post("/skills/delivery-item-create")
def delivery_item_create_skill():
    return _delivery_item_skill_response(
        forced_action="create",
        explicit_body=_body_for_explicit_write("harnoq_delivery_item", "create"),
    )


@bp.post("/skills/delivery-item-update")
def delivery_item_update_skill():
    return _delivery_item_skill_response(
        forced_action="update",
        explicit_body=_body_for_explicit_write("harnoq_delivery_item", "update"),
    )


# Canonical public aliases.  The generic routes remain for old clients and
# migration tooling; new prompt/tool contracts use the stage-specific names.
@bp.post("/skills/integration-item")
def integration_item_skill():
    return _delivery_item_skill_response()


@bp.post("/skills/integration-item-create")
def integration_item_create_skill():
    return _delivery_item_skill_response(
        forced_action="create",
        explicit_body=_body_for_explicit_write("harnoq_integration_item", "create"),
    )


@bp.post("/skills/integration-item-update")
def integration_item_update_skill():
    return _delivery_item_skill_response(
        forced_action="update",
        explicit_body=_body_for_explicit_write("harnoq_integration_item", "update"),
    )


@bp.post("/skills/system-item")
def system_item_skill():
    return _delivery_item_skill_response()


@bp.post("/skills/system-item-create")
def system_item_create_skill():
    return _delivery_item_skill_response(
        forced_action="create",
        explicit_body=_body_for_explicit_write("harnoq_system_item", "create"),
    )


@bp.post("/skills/system-item-update")
def system_item_update_skill():
    return _delivery_item_skill_response(
        forced_action="update",
        explicit_body=_body_for_explicit_write("harnoq_system_item", "update"),
    )


def _integration_inventory_result(project_id, project_number, result):
    """Shared close-out result for full submissions and recovered revisions."""
    from app.services import integration_inventory_validator

    completion = integration_inventory_validator.completion_status(project_id)
    from app.services import delivery_gate
    gate = delivery_gate.gate_status(project_id)
    integration_complete = result["valid"] and completion["complete"] and not gate["integration_owed"] and not gate["capability_outstanding"]
    if not result["valid"]:
        next_step = (
            "Correct every returned error and call this validator again; "
            "do not ask for approval or create INTEG/SYS items."
        )
    elif result.get("resource_requests"):
        next_step = (
            "External resources still need preparation or a real production call. "
            "Present resource_requests to the user with the supported source/version, "
            "configured location, preparation steps and verification command. Obtain "
            "needed files, access or approval, run the real path, and resubmit the "
            "inventory. Missing-resource handling and test doubles do not prove "
            "successful real invocation. No completion until resolved or explicitly waived."
        )
    elif integration_complete:
        next_step = (
            "Integration final audit complete on the current basis. Read "
            "harnoq_project_context for the next stage; no additional task is needed."
        )
    elif not result["pending_creation_keys"]:
        next_step = (
            "No new Integration gaps were found. If open INTEG items require "
            "code repair, use the active Integration run and group related "
            "findings into bounded coder assignments. Otherwise "
            "resolve completion_errors and "
            "call harnoq_integration_inventory_get and harnoq_integration_inventory_revise "
            "with successful exact rerun evidence for every covered INTEG item. "
            "Reuse saved sections; do not ask the user to reconstruct the payload."
        )
    else:
        next_step = (
            "Receipt valid: create every bound INTEG item and start one Integration "
            "run. Group related findings, repair them on one branch, run independent "
            "QA and merge one PR. Record a direct rerun for each item. Then call "
            "harnoq_integration_inventory_get and harnoq_integration_inventory_revise "
            "to merge current rerun evidence and changed sections into the saved inventory."
        )
    result.update(
        integration_complete=integration_complete,
        completion_errors=completion["errors"],
        completed_at=(
            completion["completed_at"].isoformat()
            if integration_complete and completion.get("completed_at") else None
        ),
    )
    return skill_service.build_envelope(
        object_type="integration_inventory",
        object_id=project_number,
        status="ready",
        change_summary=(
            "integration inventory valid"
            if result["valid"]
            else f"integration inventory invalid: {len(result['errors'])} error(s)"
        ),
        next_step=next_step,
    ) | result


@bp.post("/skills/integration-inventory-validate")
def integration_inventory_validate_skill():
    """Validate Integration Round-1 cardinality before item creation.

    It creates no INTEG/SYS item. A valid inventory binds exact creation
    payloads; a settled final audit also records this Integration completion.
    The audit is computed from the submitted structure rather than from an
    Agent-authored PASS table.
    """
    from app.services import integration_inventory_validator

    body = _body()
    manifest = body.get("manifest")
    body["action"] = "validate-integration-inventory"
    body["payload"] = {"manifest": manifest}

    def read_action():
        result = integration_inventory_validator.validate(
            body["project_id"], manifest
        )
        return _integration_inventory_result(body["project_id"], body["project_number"], result)

    return jsonify(
        _dispatch_read(
            "harnoq_integration_inventory_validate", body, read_action
        )
    )


@bp.post("/skills/integration-inventory-get")
def integration_inventory_get_skill():
    from app.services import integration_inventory_recovery

    body = _body()

    def read_action():
        state = integration_inventory_recovery.get_inventory(body["project_id"])
        return skill_service.build_envelope(
            object_type="integration_inventory", object_id=body["project_number"],
            status="ready", change_summary="saved Integration inventory and current obligations",
            next_step=state["next_step"],
        ) | state

    return jsonify(_dispatch_read("harnoq_integration_inventory_get", body, read_action))


@bp.post("/skills/integration-inventory-revise")
def integration_inventory_revise_skill():
    from app.services import integration_inventory_recovery

    body = _body()
    body["action"] = "revise-integration-inventory"

    def read_action():
        result = integration_inventory_recovery.revise(
            body["project_id"], body.get("base_validation_id"),
            body.get("reviewed_revision_token"), body.get("evidence_updates"),
            body.get("section_updates"),
        )
        return _integration_inventory_result(body["project_id"], body["project_number"], result)

    return jsonify(_dispatch_read("harnoq_integration_inventory_revise", body, read_action))


@bp.post("/skills/system-inventory-validate")
def system_inventory_validate_skill():
    """Issue a durable, source-bound receipt for the complete SYS inventory."""
    from app.services import system_inventory_validator

    body = _body()
    manifest = body.get("manifest")
    body["action"] = "validate-system-inventory"
    body["payload"] = {"manifest": manifest}

    def read_action():
        result = system_inventory_validator.validate(body["project_id"], manifest)
        return skill_service.build_envelope(
            object_type="system_inventory", object_id=body["project_number"],
            status="ready",
            change_summary=("system inventory valid" if result["valid"] else f"system inventory invalid: {len(result['errors'])} error(s)"),
        ) | result
    return jsonify(_dispatch_read("harnoq_system_inventory_validate", body, read_action))


# --- harnoq_design_case ---------------------------------------------------


def _normalize_design_case_payload(project_id, payload):
    from app.services import feature_service

    normalized = dict(payload)
    feature_number = normalized.pop("feature_number", None)
    if feature_number is not None and "feature_id" not in normalized:
        normalized["feature_id"] = feature_service.get_by_number(
            project_id, feature_number
        ).id
    return normalized


def _tasks_by_design(project_id):
    """One query for a project's tasks, grouped by design_case_id — so a
    design-case LIST decorates every design without a per-design task query."""
    from app.repositories import development_task_repo

    tasks = {}
    for task in development_task_repo.list_by_project(project_id):
        tasks.setdefault(task.design_case_id, []).append(task)
    return tasks


def _decorate_design_case_skill_item(
    design_case, tasks_by_design=None, design_ctx=None
):
    from app.repositories import development_task_repo

    item = design_case.to_dict(ctx=design_ctx)
    # In a list, pass the pre-grouped dict (one query total); for a single
    # design, fall back to a direct query.
    if tasks_by_design is not None:
        tasks = tasks_by_design.get(design_case.id, [])
    else:
        tasks = development_task_repo.list_by_design_case(design_case.id)
    item["task_numbers"] = [task.task_number for task in tasks]
    return _with_design_case_actions(item)


def _design_case_skill_response(*, forced_action=None, explicit_body=None):
    from app.services import design_case_service

    skill_name = "harnoq_design_case"
    body = explicit_body if explicit_body is not None else _body()
    action = forced_action or body.get("action")
    if forced_action:
        body["action"] = forced_action

    if action == "get":
        payload = body.get("payload") or {}
        design_number = _require_id(payload, "design_number")

        def read_action():
            item = _decorate_design_case_skill_item(
                design_case_service.get_by_number(
                    body.get("project_id"), design_number
                ),
            )
            return skill_service.build_envelope(
                object_type="design_case",
                object_id=design_number,
                status="ready",
                change_summary=f"design case {design_number} fetched",
            ) | {"design_case": item}

        return jsonify(_dispatch_read(skill_name, body, read_action))

    if action == "list":
        payload = body.get("payload") or {}
        feature_number = payload.get("feature_number")

        def read_action():
            from app.services import feature_service

            if feature_number:
                feature_id = feature_service.get_by_number(
                    body.get("project_id"), feature_number
                ).id
                tasks_by_design = _tasks_by_design(body.get("project_id"))
                design_ctx = design_case_service.dict_ctx(body.get("project_id"))
                items = [
                    _decorate_design_case_skill_item(c, tasks_by_design, design_ctx)
                    for c in design_case_service.list_by_feature(feature_id)
                ]
                object_id = feature_number
                summary = f"listed {len(items)} design case(s) for feature {feature_number}"
            else:
                tasks_by_design = _tasks_by_design(body.get("project_id"))
                design_ctx = design_case_service.dict_ctx(body.get("project_id"))
                items = [
                    _decorate_design_case_skill_item(c, tasks_by_design, design_ctx)
                    for c in design_case_service.list_by_project(body.get("project_id"))
                ]
                object_id = body.get("project_id")
                summary = f"listed {len(items)} design case(s)"
            return skill_service.build_envelope(
                object_type="design_case",
                object_id=object_id,
                status="ready",
                change_summary=summary,
            ) | {"design_cases": items}

        return jsonify(_dispatch_read(skill_name, body, read_action))

    def create_factory(payload):
        project_id = body.get("project_id")
        payload = _normalize_design_case_payload(project_id, payload)

        def write_action():
            case = design_case_service.create(project_id, payload)
            summary = f"created design case {case.design_number}"
            return (
                "design_case",
                case.design_number,
                summary,
                [],
                "Fill in the design fields, then submit for review.",
            )

        return write_action

    def update_factory(payload):
        payload = _normalize_design_case_payload(body.get("project_id"), payload)
        case_id = _resolve_id(
            body, payload, "design_number", design_case_service.get_by_number
        )

        def write_action():
            # update supports payload.rejected_solutions (service accepts it).
            case = design_case_service.update(case_id, payload)
            return (
                "design_case",
                case.design_number,
                f"updated design case {case.design_number}",
                [],
                None,
            )

        return write_action

    def confirm_factory(payload):
        case_id = _resolve_id(
            body, payload, "design_number", design_case_service.get_by_number
        )

        def write_action():
            # Service enforces REQUIRED_FOR_CONFIRM (incl. test_strategy).
            case = design_case_service.confirm(case_id)
            return (
                "design_case",
                case.design_number,
                f"confirmed design case {case.design_number}",
                [],
                "Create a TASK-* under this design when it needs to become buildable work.",
            )

        return write_action

    def reopen_factory(payload):
        case_id = _resolve_id(
            body, payload, "design_number", design_case_service.get_by_number
        )

        def write_action():
            case = design_case_service.reopen(case_id)
            return (
                "design_case",
                case.design_number,
                f"reopened design case {case.design_number} to draft",
                [],
                "Revise the design, then confirm it again when ready.",
            )

        return write_action

    def realign_basis_factory(payload):
        from app.services import suspect_service

        case_id = _resolve_id(
            body, payload, "design_number", design_case_service.get_by_number
        )

        def write_action():
            case = design_case_service.get(case_id)
            suspect_service.realign(suspect_service.DESIGN_CASE, case.id)
            return (
                "design_case",
                case.design_number,
                f"realigned design case {case.design_number} to its upstream's "
                "current versions (reviewed, still holds)",
                [],
                None,
            )

        return write_action

    def delete_factory(payload):
        case_id = _resolve_id(
            body, payload, "design_number", design_case_service.get_by_number
        )

        def write_action():
            case = design_case_service.delete(case_id)
            return (
                "design_case",
                case.design_number,
                f"deleted design case {case.design_number} (recoverable via restore)",
                [],
                None,
            )

        return write_action

    def restore_factory(payload):
        case_id = _resolve_id(
            body, payload, "design_number", design_case_service.get_deleted_by_number
        )

        def write_action():
            case = design_case_service.restore(case_id)
            return (
                "design_case",
                case.design_number,
                f"restored design case {case.design_number} from the recycle bin",
                [],
                None,
            )

        return write_action

    def create_task_factory(payload):
        _require_task_fields(payload)
        case_id = _resolve_id(
            body, payload, "design_number", design_case_service.get_by_number
        )

        def write_action():
            task = design_case_service.create_task(case_id, payload)
            return (
                "development_task",
                task.task_number,
                f"created task '{task.title}' under design {payload['design_number']}",
                [],
                None,
            )

        return write_action

    handlers = {
        "create": create_factory,
        "update": update_factory,
        "create_task": create_task_factory,
        "confirm": confirm_factory,
        "reopen": reopen_factory,
        "realign_basis": realign_basis_factory,
        "delete": delete_factory,
        "restore": restore_factory,
    }
    return jsonify(_dispatch_write(skill_name, body, handlers))


@bp.post("/skills/design-case")
def design_case_skill():
    return _design_case_skill_response()


@bp.post("/skills/design-case-create")
def design_case_create_skill():
    return _design_case_skill_response(
        forced_action="create",
        explicit_body=_body_for_explicit_write("harnoq_design_case", "create"),
    )


@bp.post("/skills/design-case-update")
def design_case_update_skill():
    return _design_case_skill_response(
        forced_action="update",
        explicit_body=_body_for_explicit_write("harnoq_design_case", "update"),
    )


@bp.post("/skills/design-case-create-task")
def design_case_create_task_skill():
    return _design_case_skill_response(
        forced_action="create_task",
        explicit_body=_body_for_explicit_write("harnoq_design_case", "create_task"),
    )


@bp.post("/skills/task-create-from-sources")
def task_create_from_sources_skill():
    """Create a source-backed task in one transaction.

    Approved change requests can source one execution task. Integration
    findings are repaired directly within their project-wide stage run.
    """
    from app.services import task_source_service

    body = _body_for_explicit_write("harnoq_task_create_from_sources", "create")
    project_id = body["project_id"]

    def create_factory(payload):
        # Explicit-write envelopes keep user fields under ``payload``. Pass
        # that payload through the same factory contract as every other
        # _dispatch_write handler: factory(payload) -> write_action().
        payload = dict(payload)

        def write_action():
            task, sources = task_source_service.create_and_assign(project_id, payload)
            return (
                "development_task",
                task.task_number,
                f"created source task {task.task_number} with {len(sources)} source row(s)",
                [],
                None,
            )

        return write_action

    return jsonify(
        _dispatch_write(
            "harnoq_task_create_from_sources", body, {"create": create_factory}
        )
    )


@bp.post("/skills/design-case-confirm")
def design_case_confirm_skill():
    return _design_case_skill_response(
        forced_action="confirm",
        explicit_body=_body_for_explicit_write("harnoq_design_case", "confirm"),
    )


@bp.post("/skills/design-case-reopen")
def design_case_reopen_skill():
    return _design_case_skill_response(
        forced_action="reopen",
        explicit_body=_body_for_explicit_write("harnoq_design_case", "reopen"),
    )


@bp.post("/skills/design-case-realign-basis")
def design_case_realign_basis_skill():
    return _design_case_skill_response(
        forced_action="realign_basis",
        explicit_body=_body_for_explicit_write("harnoq_design_case", "realign_basis"),
    )


@bp.post("/skills/design-case-delete")
def design_case_delete_skill():
    return _design_case_skill_response(
        forced_action="delete",
        explicit_body=_body_for_explicit_write("harnoq_design_case", "delete"),
    )


@bp.post("/skills/design-case-restore")
def design_case_restore_skill():
    return _design_case_skill_response(
        forced_action="restore",
        explicit_body=_body_for_explicit_write("harnoq_design_case", "restore"),
    )


# --- harnoq_change_request -------------------------------------------------


def _with_change_request_actions(item):
    change_request_number = item.get("change_request_number")
    example = {"change_request_number": change_request_number}
    return item | {
        "ai_available_actions": {
            "analyze_this_change_request": {
                "tool_name": "harnoq_change_request_analyze",
                "required_arguments": ["change_request_number"],
                "example_arguments": example,
                "use_when": "status is proposed — walks the chain under the target and records what is downstream, each with the staged response for how far it got.",
                "must_call_tool": True,
            },
            "approve_this_change_request": {
                "tool_name": "harnoq_change_request_approve",
                "required_arguments": ["change_request_number"],
                "example_arguments": example,
                "use_when": "after impact analysis: the manager may approve an ordinary defect repair restoring the confirmed contract within existing authorization, without another user confirmation. Product decisions, waivers, lower acceptance standards, significant extra cost or destructive actions require the applicable user authorization. Follow change.approval_policy; record the basis and never invent a fresh user approval.",
                "must_call_tool": True,
            },
            "apply_this_change_request": {
                "tool_name": "harnoq_change_request_apply",
                "required_arguments": ["change_request_number"],
                "example_arguments": example,
                "use_when": "the upstream edit has actually been made. This records that it happened; it does NOT edit anything itself.",
                "must_call_tool": True,
            },
            "close_this_change_request": {
                "tool_name": "harnoq_change_request_close",
                "required_arguments": ["change_request_number", "resolution"],
                "example_arguments": example | {"resolution": "applied and downstream digested"},
                "use_when": "the request is finished or dropped; resolution must say which.",
                "must_call_tool": True,
            },
        }
    }


def _resolve_change_target(project_id, number):
    """A DESIGN-*/TASK-* business number to a {target_type, target_id} pair."""
    from app.core.errors import ValidationError
    from app.services import design_case_service, development_task_service

    text = (number or "").upper()
    if "DESIGN" in text:
        return {
            "target_type": "design_case",
            "target_id": design_case_service.get_by_number(project_id, number).id,
        }
    if "TASK" in text:
        return {
            "target_type": "development_task",
            "target_id": development_task_service.get_by_number(project_id, number).id,
        }
    raise ValidationError(
        f"cannot tell what {number!r} refers to; pass a DESIGN-* or TASK-* number"
    )


def _normalize_change_request_payload(project_id, payload):
    """Business numbers in, internal ids out (编号/id 原则)."""
    normalized = dict(payload)
    design_number = normalized.pop("design_number", None)
    task_number = normalized.pop("task_number", None)
    if design_number is not None:
        normalized |= _resolve_change_target(project_id, design_number)
    elif task_number is not None:
        normalized |= _resolve_change_target(project_id, task_number)
    # Every OTHER object the same change rewrites. Given as numbers for the
    # same reason the primary target is: agents never see raw ids.
    also_numbers = normalized.pop("also_changes_numbers", None)
    if also_numbers is not None:
        from app.core.errors import ValidationError

        if not isinstance(also_numbers, list):
            raise ValidationError(
                "also_changes_numbers must be a list of DESIGN-*/TASK-* numbers"
            )
        normalized["also_changes"] = [
            _resolve_change_target(project_id, number) for number in also_numbers
        ]
    return normalized


def _change_request_skill_response(*, forced_action=None, explicit_body=None):
    from app.api.change_requests import _list_ctx, _serialize
    from app.services import change_request_service

    skill_name = "harnoq_change_request"
    body = explicit_body if explicit_body is not None else _body()
    action = forced_action or body.get("action")
    if forced_action:
        body["action"] = forced_action
    project_id = body.get("project_id")

    if action == "get":
        payload = body.get("payload") or {}
        change_request_number = _require_id(payload, "change_request_number")

        def read_action():
            item = _with_change_request_actions(
                _serialize(
                    change_request_service.get_by_number(project_id, change_request_number)
                )
            )
            return skill_service.build_envelope(
                object_type="change_request",
                object_id=change_request_number,
                status="ready",
                change_summary=f"change request {change_request_number} fetched",
            ) | {"change_request": item}

        return jsonify(_dispatch_read(skill_name, body, read_action))

    if action == "list":
        def read_action():
            rows = change_request_service.list_by_project(project_id)
            ctx = _list_ctx(project_id)
            items = [_with_change_request_actions(_serialize(cr, ctx)) for cr in rows]
            return skill_service.build_envelope(
                object_type="change_request",
                object_id=project_id,
                status="ready",
                change_summary=f"listed {len(items)} change request(s)",
            ) | {"change_requests": items}

        return jsonify(_dispatch_read(skill_name, body, read_action))

    if action == "impact":
        payload = _normalize_change_request_payload(project_id, body.get("payload") or {})

        def read_action():
            impact = change_request_service.impact_of(
                project_id, payload.get("target_type"), payload.get("target_id")
            )
            return skill_service.build_envelope(
                object_type="change_request",
                object_id=project_id,
                status="ready",
                change_summary=f"{len(impact)} downstream object(s) would be affected",
            ) | {"impact": impact}

        return jsonify(_dispatch_read(skill_name, body, read_action))

    def create_factory(payload):
        payload = _normalize_change_request_payload(project_id, payload)

        def write_action():
            item = change_request_service.create(project_id, payload)
            return (
                "change_request",
                item.change_request_number,
                f"proposed change request {item.change_request_number}",
                [],
                "Analyze it next: the downstream chain decides what the change costs.",
            )

        return write_action

    def _lifecycle_factory(service_call, summary, next_step=None, pass_payload=False):
        def factory(payload):
            change_request_id = _resolve_id(
                body, payload, "change_request_number",
                change_request_service.get_by_number,
            )

            def write_action():
                item = (
                    service_call(change_request_id, payload)
                    if pass_payload
                    else service_call(change_request_id)
                )
                return (
                    "change_request",
                    item.change_request_number,
                    summary.format(number=item.change_request_number),
                    [],
                    next_step,
                )

            return write_action

        return factory

    handlers = {
        "create": create_factory,
        "analyze": _lifecycle_factory(
            change_request_service.analyze,
            "analyzed change request {number}",
            "Classify the analysed impact using change.approval_policy. For an ordinary "
            "defect repair restoring the confirmed contract within existing authorization, "
            "the manager approves and continues without another user confirmation. "
            "Ask only for an actual product decision, waiver, lower acceptance standard, "
            "significant extra cost or other action outside that authorization.",
        ),
        "approve": _lifecycle_factory(
            change_request_service.approve,
            "approved change request {number}",
            "Create or reuse the minimum source TASK batch, complete technical pre-review "
            "and Build/QA, then apply and close after the real change is verified. "
            "No additional user confirmation for ordinary repairs within existing authorization.",
        ),
        "apply": _lifecycle_factory(
            change_request_service.apply,
            "marked change request {number} applied",
            "Downstream objects now read as suspect; digest them one by one.",
        ),
        "close": _lifecycle_factory(
            change_request_service.close,
            "closed change request {number}",
            pass_payload=True,
        ),
    }
    return jsonify(_dispatch_write(skill_name, body, handlers))


@bp.post("/skills/change-request")
def change_request_skill():
    return _change_request_skill_response()


@bp.post("/skills/change-request-create")
def change_request_create_skill():
    return _change_request_skill_response(
        forced_action="create",
        explicit_body=_body_for_explicit_write("harnoq_change_request", "create"),
    )


@bp.post("/skills/change-request-analyze")
def change_request_analyze_skill():
    return _change_request_skill_response(
        forced_action="analyze",
        explicit_body=_body_for_explicit_write("harnoq_change_request", "analyze"),
    )


@bp.post("/skills/change-request-approve")
def change_request_approve_skill():
    return _change_request_skill_response(
        forced_action="approve",
        explicit_body=_body_for_explicit_write("harnoq_change_request", "approve"),
    )


@bp.post("/skills/change-request-apply")
def change_request_apply_skill():
    return _change_request_skill_response(
        forced_action="apply",
        explicit_body=_body_for_explicit_write("harnoq_change_request", "apply"),
    )


@bp.post("/skills/change-request-close")
def change_request_close_skill():
    return _change_request_skill_response(
        forced_action="close",
        explicit_body=_body_for_explicit_write("harnoq_change_request", "close"),
    )


# --- shared payload helpers -----------------------------------------------


def _require_id(payload, key):
    value = payload.get(key)
    if value is None:
        raise ValidationError(f"{key} is required")
    return value


def _resolve_id(body, payload, key, get_by_number):
    """Resolve a skill-facing business number to its internal database id.

    Skill callers (the design-time AI / OpenWebUI) only ever see business
    numbers (P1-INPUT-12, P1-DESIGN-3, ...), never
    the raw id. ``get_by_number`` does the (project_id, number) lookup and
    raises NotFoundError if it doesn't resolve.
    """
    number = _require_id(payload, key)
    return get_by_number(body.get("project_id"), number).id


_TASK_CREATE_REQUIRED_FIELDS = ("goal", "acceptance_criteria", "constraints", "non_goals")


def _require_task_fields(payload):
    """Skill guard: a task create must carry all four spec fields non-empty."""
    missing = []
    for field in _TASK_CREATE_REQUIRED_FIELDS:
        value = payload.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field)
    if missing:
        raise ValidationError(
            "task create missing required field(s): " + ", ".join(missing)
        )
