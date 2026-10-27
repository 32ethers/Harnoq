"""Structural validation for Integration Round-1 inventory reports.

The repository analysis is necessarily performed by the external planning
agent, but cardinality and payload reconciliation are deterministic.  Keeping
those checks in prose let an agent mark its own contradictory report PASS.
This module validates the structured manifest before item creation. It
deliberately does not pretend to prove repository facts such
as whether a claimed call edge really exists; those remain command evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter

from app.core.errors import ConflictError, ValidationError
from app.core.time import utcnow
from app.db.base import database_proxy
from app.repositories import (
    delivery_item_repo, design_case_repo, development_task_repo,
    integration_completion_audit_repo, integration_inventory_validation_repo,
    task_event_repo,
)
from app.services import delivery_gate, delivery_item_service, design_case_service
from app.services import integration_resource_audit


_DESIGN_NUMBER_RE = re.compile(r"^P\d+-DESIGN-\d+$")
_ELLIPSIS_RE = re.compile(r"\.\.\.|…")
_COMPRESSED_REFERENCE_RE = re.compile(
    r"\b(?:same as above|see (?:section|row|table)|template applies)\b"
    r"|同上|原样入册|公共尾注|参见[表章节]|见[表章节]|关键行|关键输出"
    r"|完整输出(?:位于|见|存于)|输出位置",
    re.IGNORECASE,
)
_BUILD_ONLY_COMMAND_RE = re.compile(
    r"(?:npm\s+run\s+build|vite\s+build|(?:^|\s)tsc(?:\s|$)|webpack"
    r"|pytest|vitest|cargo\s+build|go\s+build|mvn\s+package"
    r"|gradle\s+build|docker\s+build)",
    re.IGNORECASE,
)


def completion_basis_fingerprint(project_id):
    """Low-level current capability basis; deliberately never calls the gate."""
    tasks = development_task_repo.list_by_project(project_id)
    completed = [row for row in tasks if row.status == "completed"]
    events = task_event_repo.latest_completed_at_by_task([row.id for row in completed])
    rows = []
    for task in tasks:
        rows.append((task.task_number, task.status, (events.get(task.id) or task.updated_at).isoformat() if task.status == "completed" else None))
    design_rows = [
        (row.design_number, row.status, row.selected_solution, row.design_scope)
        for row in design_case_repo.list_by_project(project_id)
        if row.status == "confirmed"
    ]
    return hashlib.sha256(json.dumps([sorted(rows), sorted(design_rows)], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def rerun_command(item):
    """The executable command is independent of the human coverage reference."""
    command = (item.evidence_command or "").strip()
    if command:
        return command
    # Older coverage references could prefix a test node with a TASK number.
    return re.sub(r"^P\d+-TASK-\d+\s*:\s*", "", (item.covered_by or "").strip())


def matches_rerun(command, evidence):
    actual = evidence.get("command")
    return bool(command and isinstance(actual, str) and
                re.search(rf"(?<![A-Za-z0-9_]){re.escape(command)}(?![A-Za-z0-9_])", actual))


def completion_status(project_id):
    audit = integration_completion_audit_repo.get_active(project_id)
    if audit is None:
        return {"complete": False, "has_audit": False, "errors": ["no current Integration completion audit"]}
    if audit.basis_fingerprint != completion_basis_fingerprint(project_id):
        return {"complete": False, "has_audit": True, "errors": ["Integration completion audit basis changed"]}
    try:
        proposals, created = json.loads(audit.proposed_items_json), json.loads(audit.created_items_json)
        manifest = json.loads(audit.manifest_json)
    except (TypeError, ValueError):
        return {"complete": False, "has_audit": True, "errors": ["Integration completion audit is corrupt"]}
    floor_errors = _minimum_entry_and_seam_errors(manifest, _capability_designs(project_id))
    if floor_errors:
        return {"complete": False, "has_audit": True, "errors": floor_errors}
    items = {row.item_number: row for row in delivery_item_repo.list_by_project(project_id, kinds=("assembly", "seam", "duplicate"))}
    resource_errors, resource_blockers, _ = integration_resource_audit.check(
        manifest, set(_capability_designs(project_id)), list(items.values()),
    )
    if resource_errors or resource_blockers:
        return {"complete": False, "has_audit": True, "errors": resource_errors + resource_blockers}
    if audit.completed_at is None:
        return {"complete": False, "has_audit": True, "errors": ["Integration audit is not a final settled audit"]}
    errors = []
    # Completion covers every live INTEG row at audit time, not only gaps
    # proposed during this pass.  A covered historic row needs fresh evidence.
    snapshot = manifest.get("_live_items", [])
    snapshot_by_number = {row.get("item_number"): row for row in snapshot}
    if set(items) != set(snapshot_by_number):
        errors.append("live Integration item set changed since audit")
    rows = list(proposals) + [
        {"key": f"live:{row.get('item_number')}", "existing_item_number": row.get("item_number")}
        for row in snapshot
        if row.get("item_number") not in {
            p.get("existing_item_number") for p in proposals
        }
    ]
    for row in rows:
        number = row.get("existing_item_number") or created.get(row.get("key"))
        item = items.get(number)
        if item is None or item.status == "open":
            errors.append(f"unsettled Integration item: {row.get('key')}")
        elif row.get("existing_item_number"):
            # A prior cover is not this pass's evidence.  The submitted
            # manifest must rerun the executable check stored on the item.
            saved = next((x for x in snapshot if x.get("item_number") == number), {})
            current = item.to_dict()
            identity_fields = (
                "kind", "subject", "source", "found_in_round", "verified_revision",
                "status", "covered_by", "evidence_command", "evidence_exit_code", "evidence_output", "waive_reason",
            )
            if any(saved.get(field) != current.get(field) for field in identity_fields):
                errors.append(f"Integration item identity changed: {row.get('key')}")
                continue
            if item.status != "covered":
                continue
            named_test = rerun_command(item)
            rerun = next((e for e in manifest.get("evidence", []) if isinstance(e, dict) and e.get("role") == "test" and e.get("exit_code") == 0 and matches_rerun(named_test, e)), None)
            if rerun is None:
                errors.append(f"covered Integration item lacks successful current rerun: {row.get('key')}")
    return {"complete": not errors, "has_audit": True, "errors": errors, "completed_at": audit.completed_at}
_KINDS = {"assembly", "seam", "duplicate"}
_VERDICTS = {"converge", "retain"}
_DISCOVERY_KINDS = {"exact-name", "different-name"}
_ENTRY_TYPES = {"service", "cli", "browser", "scheduled"}
_EVIDENCE_ROLES = {
    "entry-launch", "entry-observation", "entry-launch-observation",
    "source-scan", "skeleton", "contract", "caller", "owner", "test", "other",
    "resource-check", "resource-call",
}
_BROWSER_OBSERVATION_RE = re.compile(
    r"(?:https?://|file://|playwright|cypress|selenium|curl|wget"
    r"|invoke-webrequest|browser)",
    re.IGNORECASE,
)


def _minimum_entry_and_seam_errors(manifest, capability_designs):
    """Recheck the minimum entry/seam proof even for an older saved receipt."""
    parts = {row.get("key"): row for row in _list(manifest.get("declared_parts")) if isinstance(row, dict)}
    evidence = {row.get("key"): row for row in _list(manifest.get("evidence")) if isinstance(row, dict)}
    assembly = _list(manifest.get("assembly_rows"))
    seams = [row for row in _list(manifest.get("seams")) if isinstance(row, dict)]
    errors = []
    for row in assembly:
        if not isinstance(row, dict) or row.get("reachable") is not True:
            continue
        key = row.get("key")
        part = parts.get(key, {})
        path = row.get("path")
        name = part.get("name")
        if isinstance(name, str) and isinstance(path, list) and not any(
            isinstance(edge, str) and name in edge for edge in path
        ):
            errors.append(f"assembly_rows[{key}] path must identify the declared part, not only its inventory key")
        evidence_keys = row.get("evidence_keys")
        if not isinstance(evidence_keys, list) or not any(
            evidence.get(evidence_key, {}).get("role") == "test"
            and evidence.get(evidence_key, {}).get("exit_code") == 0
            for evidence_key in evidence_keys if isinstance(evidence_key, str)
        ):
            errors.append(
                f"assembly_rows[{key}] reachable=true requires a passing "
                "executable test evidence key; entry health alone is insufficient"
            )
    relation_words = re.compile(
        r"消费|使用|调用|提供|依赖|接入|复用|交给|送入|向|由"
        r"|\b(?:consume|use|call|provide|feed|depend|integrate)s?\b",
        re.IGNORECASE,
    )
    allowed_designs = set(capability_designs)
    for declaring_number, design in capability_designs.items():
        clauses = re.split(r"[。；;\n]", design.design_scope or "")
        referenced = {
            number for clause in clauses
            if relation_words.search(clause)
            and not re.search(r"^\s*(?:它)?不拥有|^\s*does not own\b", clause, re.IGNORECASE)
            for number in re.findall(r"P\d+-DESIGN-\d+", clause)
        }
        missing_pairs = sorted(
            number for number in referenced & allowed_designs
            if number != declaring_number and not any(
                row.get("declaring_design_number") == declaring_number
                and number in (row.get("producer_design_number"), row.get("consumer_design_number"))
                for row in seams
            )
        )
        if missing_pairs:
            errors.append(
                f"seams missing explicit design_scope adjacencies from {declaring_number}: "
                + ", ".join(missing_pairs)
            )
    return errors


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _list(value):
    return value if isinstance(value, list) else []


def _finding_key(kind, subject, source):
    def normalize(value):
        return " ".join((value or "").split()).casefold()

    return kind, normalize(subject), normalize(source)


def _record_index(rows, name, errors):
    if not isinstance(rows, list):
        errors.append(f"{name} must be a list")
        return {}
    indexed = {}
    for position, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            errors.append(f"{name}[{position}] must be an object")
            continue
        key = row.get("key")
        if not _text(key):
            errors.append(f"{name}[{position}].key is required")
            continue
        key = key.strip()
        if key in indexed:
            errors.append(f"{name} has duplicate key {key}")
            continue
        indexed[key] = row
    return indexed


def _check_fields(owner, row, allowed, errors):
    extras = sorted(set(row) - set(allowed))
    if extras:
        errors.append(f"{owner} has unsupported fields: {extras}")


def _check_evidence_keys(owner, keys, evidence, errors):
    if not isinstance(keys, list) or not keys:
        errors.append(f"{owner}.evidence_keys must be a non-empty list")
        return
    for key in keys:
        if key not in evidence:
            errors.append(f"{owner} references unknown evidence key {key!r}")
        else:
            row = evidence[key]
            expected = row.get("expected_exit_codes", [0])
            if row.get("exit_code") not in expected:
                errors.append(
                    f"{owner} references evidence {key} whose exit code was unexpected"
                )


def _check_evidence_role(owner, key, evidence, allowed_roles, errors):
    row = evidence.get(key)
    if row is None:
        errors.append(f"{owner} references unknown evidence key {key!r}")
    elif row.get("role") not in allowed_roles:
        errors.append(
            f"{owner} requires evidence role {sorted(allowed_roles)}, got "
            f"{row.get('role')!r}"
        )


def _capability_designs(project_id):
    designs = {}
    for design in design_case_service.list_by_project(project_id):
        feature = design.feature
        if design.status == "confirmed" and feature is not None:
            designs[design.design_number] = design
    return designs


def _check_design_number(owner, number, allowed, errors):
    if not _text(number) or not _DESIGN_NUMBER_RE.fullmatch(number.strip()):
        errors.append(f"{owner} must use a fully qualified P*-DESIGN-* number")
    elif number.strip() not in allowed:
        errors.append(
            f"{owner} references {number.strip()}, which is not a confirmed "
            "capability DESIGN-* in this project"
        )


def _check_standalone_payload(owner, value, field, errors):
    if not _text(value):
        errors.append(f"{owner}.{field} is required and must be standalone")
        return
    if _ELLIPSIS_RE.search(value) or _COMPRESSED_REFERENCE_RE.search(value):
        errors.append(
            f"{owner}.{field} is compressed or refers elsewhere; repeat the full value"
        )


def _check_exact_test(owner, value, errors):
    if not _text(value):
        errors.append(f"{owner} must be an exact test node or executable command")
    elif (
        _ELLIPSIS_RE.search(value)
        or _COMPRESSED_REFERENCE_RE.search(value)
        or (" " not in value and "::" not in value)
    ):
        errors.append(
            f"{owner} is abbreviated or only a filename; use an exact node/command"
        )


def validate(project_id, manifest):
    """Return a deterministic validation result for one Round-1 manifest."""
    errors = []
    if not isinstance(manifest, dict):
        manifest = {}
        errors.append("manifest must be an object")
    canonical_retry = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    current_audit = integration_completion_audit_repo.get_active(project_id)
    same_final_retry = (
        current_audit is not None and current_audit.completed_at is not None
        and current_audit.basis_fingerprint == completion_basis_fingerprint(project_id)
        and json.dumps((lambda value: (value.pop("_live_items", None), value)[1])(json.loads(current_audit.manifest_json)), ensure_ascii=False, sort_keys=True, separators=(",", ":")) == canonical_retry
    )

    gate = delivery_gate.gate_status(project_id)
    if not gate["integration_owed"] and not same_final_retry:
        errors.append("the project is not currently at the Integration delivery gate")

    existing_items = delivery_item_service.list_by_project(
        project_id, kinds=sorted(_KINDS)
    )
    expected_round = (
        max((item.found_in_round for item in existing_items), default=0) + 1
    )
    found_in_round = manifest.get("found_in_round")
    if (
        not isinstance(found_in_round, int)
        or isinstance(found_in_round, bool)
        or found_in_round != expected_round
    ):
        errors.append(
            f"found_in_round must be {expected_round} for the current inventory"
        )

    evidence = _record_index(manifest.get("evidence"), "evidence", errors)
    for key, row in evidence.items():
        _check_fields(
            f"evidence[{key}]",
            row,
            {
                "key", "role", "command", "exit_code", "expected_exit_codes",
                "stdout", "stderr",
            },
            errors,
        )
        if row.get("role") not in _EVIDENCE_ROLES:
            errors.append(
                f"evidence[{key}].role must be one of {sorted(_EVIDENCE_ROLES)}"
            )
        command = row.get("command")
        if not _text(command):
            errors.append(f"evidence[{key}].command is required")
        elif _ELLIPSIS_RE.search(command) or "<literal" in command.lower():
            errors.append(f"evidence[{key}].command is abbreviated or placeholder text")
        exit_code = row.get("exit_code")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            errors.append(f"evidence[{key}].exit_code must be an integer")
        expected_exit_codes = row.get("expected_exit_codes", [0])
        if (
            not isinstance(expected_exit_codes, list)
            or not expected_exit_codes
            or not all(
                isinstance(code, int) and not isinstance(code, bool)
                for code in expected_exit_codes
            )
        ):
            errors.append(
                f"evidence[{key}].expected_exit_codes must be a non-empty integer list"
            )
        for field in ("stdout", "stderr"):
            if not isinstance(row.get(field), str):
                errors.append(f"evidence[{key}].{field} must be a string; use empty string")
            elif _COMPRESSED_REFERENCE_RE.search(row[field]):
                errors.append(
                    f"evidence[{key}].{field} refers to a summary or external output"
                )

    entries = _record_index(
        manifest.get("production_entries"), "production_entries", errors
    )
    for key, row in entries.items():
        _check_fields(
            f"production_entries[{key}]",
            row,
            {
                "key", "name", "entry_type", "operational_invocation",
                "observable_boundary", "executes_delivered_behavior",
                "launch_evidence_key", "observation_evidence_key",
            },
            errors,
        )
        for field in ("name", "operational_invocation", "observable_boundary"):
            if not _text(row.get(field)):
                errors.append(f"production_entries[{key}].{field} is required")
        if row.get("entry_type") not in _ENTRY_TYPES:
            errors.append(
                f"production_entries[{key}].entry_type must be one of "
                f"{sorted(_ENTRY_TYPES)}"
            )
        invocation = row.get("operational_invocation") or ""
        if _BUILD_ONLY_COMMAND_RE.search(invocation):
            errors.append(
                f"production_entries[{key}].operational_invocation is a build/test "
                "command, not an operational product entry"
            )
        if row.get("executes_delivered_behavior") is not True:
            errors.append(
                f"production_entries[{key}] must execute delivered behavior; "
                "build/test/dev/preview discovery alone is not an entry"
            )
        launch_key = row.get("launch_evidence_key")
        observation_key = row.get("observation_evidence_key")
        _check_evidence_keys(
            f"production_entries[{key}]",
            [launch_key, observation_key],
            evidence,
            errors,
        )
        _check_evidence_role(
            f"production_entries[{key}].launch_evidence_key",
            launch_key,
            evidence,
            {"entry-launch", "entry-launch-observation"},
            errors,
        )
        _check_evidence_role(
            f"production_entries[{key}].observation_evidence_key",
            observation_key,
            evidence,
            {"entry-observation", "entry-launch-observation"},
            errors,
        )
        launch = evidence.get(launch_key, {})
        observation = evidence.get(observation_key, {})
        if _text(invocation) and invocation.strip() != (launch.get("command") or "").strip():
            errors.append(
                f"production_entries[{key}].operational_invocation must exactly "
                "match its launch evidence command"
            )
        if not (observation.get("stdout") or observation.get("stderr")):
            errors.append(
                f"production_entries[{key}] observation evidence has no observable output"
            )
        if row.get("entry_type") == "browser" and not _BROWSER_OBSERVATION_RE.search(
            observation.get("command") or ""
        ):
            errors.append(
                f"production_entries[{key}] browser observation must load a served/"
                "deployed URL or use a browser driver"
            )

    capability_designs = _capability_designs(project_id)
    allowed_designs = set(capability_designs)
    design_coverage = _record_index(
        manifest.get("design_coverage"), "design_coverage", errors
    )
    covered_design_numbers = set()
    for key, row in design_coverage.items():
        _check_fields(
            f"design_coverage[{key}]",
            row,
            {
                "key", "design_number", "selected_solution_sha256",
                "design_scope_sha256", "declared_part_keys", "seam_keys",
            },
            errors,
        )
        number = row.get("design_number")
        _check_design_number(
            f"design_coverage[{key}].design_number", number, allowed_designs, errors
        )
        if key != number:
            errors.append(
                f"design_coverage[{key}].key must equal its full design_number"
            )
        if number in covered_design_numbers:
            errors.append(f"design_coverage repeats {number}")
        covered_design_numbers.add(number)
        design = capability_designs.get(number)
        if design is not None:
            expected_solution_hash = hashlib.sha256(
                (design.selected_solution or "").encode("utf-8")
            ).hexdigest()
            expected_scope_hash = hashlib.sha256(
                (design.design_scope or "").encode("utf-8")
            ).hexdigest()
            if row.get("selected_solution_sha256") != expected_solution_hash:
                errors.append(
                    f"design_coverage[{key}] selected_solution hash is stale or missing"
                )
            if row.get("design_scope_sha256") != expected_scope_hash:
                errors.append(
                    f"design_coverage[{key}] design_scope hash is stale or missing"
                )
        for field in ("declared_part_keys", "seam_keys"):
            if not isinstance(row.get(field), list):
                errors.append(f"design_coverage[{key}].{field} must be a list")
    if covered_design_numbers != allowed_designs:
        missing = sorted(allowed_designs - covered_design_numbers)
        extra = sorted(covered_design_numbers - allowed_designs)
        if missing:
            errors.append(f"design_coverage missing confirmed capability designs: {missing}")
        if extra:
            errors.append(f"design_coverage contains non-capability designs: {extra}")

    source_scans = _record_index(manifest.get("source_scans"), "source_scans", errors)
    if not source_scans:
        errors.append("source_scans must contain at least one repository source scan")
    for key, row in source_scans.items():
        _check_fields(
            f"source_scans[{key}]",
            row,
            {
                "key", "language", "roots", "scan_evidence_key",
                "collision_keys", "different_name_candidate_keys",
            },
            errors,
        )
        if not _text(row.get("language")):
            errors.append(f"source_scans[{key}].language is required")
        if (
            not isinstance(row.get("roots"), list)
            or not row.get("roots")
            or not all(_text(root) for root in row.get("roots"))
        ):
            errors.append(f"source_scans[{key}].roots must be a non-empty list")
        for field in ("collision_keys", "different_name_candidate_keys"):
            if not isinstance(row.get(field), list):
                errors.append(f"source_scans[{key}].{field} must be a list")
        scan_key = row.get("scan_evidence_key")
        _check_evidence_keys(
            f"source_scans[{key}]", [scan_key], evidence, errors
        )
        _check_evidence_role(
            f"source_scans[{key}].scan_evidence_key",
            scan_key,
            evidence,
            {"source-scan"},
            errors,
        )

    parts = _record_index(manifest.get("declared_parts"), "declared_parts", errors)
    seen_parts = set()
    for key, row in parts.items():
        _check_fields(
            f"declared_parts[{key}]",
            row,
            {"key", "design_number", "name", "declaration"},
            errors,
        )
        design_number = row.get("design_number")
        _check_design_number(
            f"declared_parts[{key}].design_number", design_number, allowed_designs, errors
        )
        for field in ("name", "declaration"):
            if not _text(row.get(field)):
                errors.append(f"declared_parts[{key}].{field} is required")
        design = capability_designs.get(design_number)
        declaration = row.get("declaration")
        if (
            design is not None
            and _text(declaration)
            and declaration.strip() not in (design.selected_solution or "")
        ):
            errors.append(
                f"declared_parts[{key}].declaration must be copied verbatim from "
                f"{design_number} selected_solution"
            )
        identity = (design_number, row.get("name"))
        if identity in seen_parts:
            errors.append(
                f"declared_parts[{key}] duplicates another DESIGN/part pair; "
                "one declared part must have one ledger row"
            )
        seen_parts.add(identity)

    assembly = _record_index(
        manifest.get("assembly_rows"), "assembly_rows", errors
    )
    part_keys = set(parts)
    assembly_keys = set(assembly)
    if assembly_keys != part_keys:
        missing = sorted(part_keys - assembly_keys)
        extra = sorted(assembly_keys - part_keys)
        if missing:
            errors.append(f"assembly_rows missing declared part keys: {missing}")
        if extra:
            errors.append(f"assembly_rows contain undeclared part keys: {extra}")
    for key, row in assembly.items():
        _check_fields(
            f"assembly_rows[{key}]",
            row,
            {
                "key", "reachable", "entry_keys", "path", "gap_key",
                "missing_edge", "evidence_keys",
            },
            errors,
        )
        reachable = row.get("reachable")
        if not isinstance(reachable, bool):
            errors.append(f"assembly_rows[{key}].reachable must be boolean")
            continue
        entry_keys = row.get("entry_keys")
        path = row.get("path")
        if reachable:
            if not isinstance(entry_keys, list) or not entry_keys:
                errors.append(f"assembly_rows[{key}] reachable=true needs entry_keys")
            else:
                for entry_key in entry_keys:
                    if entry_key not in entries:
                        errors.append(
                            f"assembly_rows[{key}] references unknown entry {entry_key!r}"
                        )
            if not isinstance(path, list) or not path or not all(_text(edge) for edge in path):
                errors.append(
                    f"assembly_rows[{key}] reachable=true needs a non-empty wired path"
                )
            if row.get("gap_key") is not None or row.get("missing_edge") is not None:
                errors.append(
                    f"assembly_rows[{key}] reachable=true cannot name an assembly gap"
                )
        elif entry_keys not in ([], None):
            errors.append(f"assembly_rows[{key}] reachable=false must have no entry_keys")
        if reachable is False:
            if path not in ([], None):
                errors.append(f"assembly_rows[{key}] reachable=false must have no path")
            for field in ("gap_key", "missing_edge"):
                if not _text(row.get(field)):
                    errors.append(
                        f"assembly_rows[{key}] reachable=false requires {field}"
                    )
        _check_evidence_keys(
            f"assembly_rows[{key}]", row.get("evidence_keys"), evidence, errors
        )
    if not entries and any(row.get("reachable") is True for row in assembly.values()):
        errors.append("zero verified production entries requires zero reachable parts")

    assembly_gap_edges = {}
    assembly_gap_parts = {}
    gap_key_by_edge = {}
    for part_key, row in assembly.items():
        if row.get("reachable") is not False or not _text(row.get("gap_key")):
            continue
        gap_key = row["gap_key"].strip()
        missing_edge = (row.get("missing_edge") or "").strip()
        if gap_key in assembly_gap_edges and assembly_gap_edges[gap_key] != missing_edge:
            errors.append(
                f"assembly gap {gap_key} is reused for different missing edges"
            )
        prior_gap_key = gap_key_by_edge.get(missing_edge)
        if prior_gap_key is not None and prior_gap_key != gap_key:
            errors.append(
                f"missing edge {missing_edge!r} is split across assembly gaps "
                f"{prior_gap_key} and {gap_key}"
            )
        assembly_gap_edges[gap_key] = missing_edge
        gap_key_by_edge[missing_edge] = gap_key
        assembly_gap_parts.setdefault(gap_key, []).append(part_key)

    seams = _record_index(manifest.get("seams"), "seams", errors)
    errors.extend(_minimum_entry_and_seam_errors(manifest, capability_designs))
    for key, row in seams.items():
        _check_fields(
            f"seams[{key}]",
            row,
            {
                "key", "declaring_design_number", "producer_design_number",
                "consumer_design_number",
                "producer", "consumer", "declaration", "wiring", "test",
                "wired", "integration_verified", "test_evidence_key",
                "evidence_keys",
            },
            errors,
        )
        for side in ("declaring", "producer", "consumer"):
            _check_design_number(
                f"seams[{key}].{side}_design_number",
                row.get(f"{side}_design_number"),
                allowed_designs,
                errors,
            )
        for field in ("producer", "consumer", "declaration", "wiring"):
            if not _text(row.get(field)):
                errors.append(f"seams[{key}].{field} is required")
        declaring_design = capability_designs.get(row.get("declaring_design_number"))
        declaration = row.get("declaration")
        if (
            declaring_design is not None
            and _text(declaration)
            and declaration.strip() not in (declaring_design.design_scope or "")
        ):
            errors.append(
                f"seams[{key}].declaration must be copied verbatim from "
                f"{row.get('declaring_design_number')} design_scope"
            )
        test = row.get("test")
        if test is not None:
            _check_exact_test(f"seams[{key}].test", test, errors)
        wired = row.get("wired")
        verified = row.get("integration_verified")
        if not isinstance(wired, bool):
            errors.append(f"seams[{key}].wired must be boolean")
        if not isinstance(verified, bool):
            errors.append(f"seams[{key}].integration_verified must be boolean")
        if verified is True:
            if wired is not True:
                errors.append(
                    f"seams[{key}] cannot be integration_verified while unwired"
                )
            _check_exact_test(f"seams[{key}].test", test, errors)
            test_key = row.get("test_evidence_key")
            _check_evidence_keys(
                f"seams[{key}].test_evidence_key", [test_key], evidence, errors
            )
            _check_evidence_role(
                f"seams[{key}].test_evidence_key",
                test_key,
                evidence,
                {"test"},
                errors,
            )
            test_evidence = evidence.get(test_key, {})
            if _text(test) and test.strip() != (test_evidence.get("command") or "").strip():
                errors.append(
                    f"seams[{key}].test must exactly match its test evidence command"
                )
            if not (test_evidence.get("stdout") or test_evidence.get("stderr")):
                errors.append(f"seams[{key}] test evidence has no result output")
        elif row.get("test_evidence_key") is not None:
            errors.append(
                f"seams[{key}].test_evidence_key is only valid when "
                "integration_verified=true"
            )
        _check_evidence_keys(f"seams[{key}]", row.get("evidence_keys"), evidence, errors)

    part_coverage_count = {key: 0 for key in parts}
    seam_coverage_count = {key: 0 for key in seams}
    for coverage_key, coverage in design_coverage.items():
        number = coverage.get("design_number")
        for part_key in coverage.get("declared_part_keys") or []:
            part = parts.get(part_key)
            if part is None:
                errors.append(
                    f"design_coverage[{coverage_key}] references unknown part {part_key!r}"
                )
            else:
                part_coverage_count[part_key] += 1
                if part.get("design_number") != number:
                    errors.append(
                        f"design_coverage[{coverage_key}] claims part {part_key} from "
                        "a different DESIGN-*"
                    )
        for seam_key in coverage.get("seam_keys") or []:
            seam = seams.get(seam_key)
            if seam is None:
                errors.append(
                    f"design_coverage[{coverage_key}] references unknown seam {seam_key!r}"
                )
            else:
                seam_coverage_count[seam_key] += 1
                if seam.get("declaring_design_number") != number:
                    errors.append(
                        f"design_coverage[{coverage_key}] claims seam {seam_key} declared "
                        "by a different DESIGN-*"
                    )
    for part_key, count in part_coverage_count.items():
        if count != 1:
            errors.append(
                f"declared part {part_key} must appear in exactly one design_coverage row; "
                f"actual={count}"
            )
    for seam_key, count in seam_coverage_count.items():
        if count != 1:
            errors.append(
                f"seam {seam_key} must appear in exactly one design_coverage row; "
                f"actual={count}"
            )

    collisions = _record_index(
        manifest.get("identifier_collisions"), "identifier_collisions", errors
    )
    for key, row in collisions.items():
        _check_fields(
            f"identifier_collisions[{key}]",
            row,
            {"key", "identifier", "implementations", "evidence_keys"},
            errors,
        )
        identifier = row.get("identifier")
        implementations = row.get("implementations")
        if not _text(identifier):
            errors.append(f"identifier_collisions[{key}].identifier is required")
        if (
            not isinstance(implementations, list)
            or len(implementations) < 2
            or not all(
                isinstance(item, dict)
                and set(item) == {"module", "identifier"}
                and _text(item.get("module"))
                and item.get("identifier") == identifier
                for item in implementations
            )
        ):
            errors.append(
                f"identifier_collisions[{key}] needs at least two modules with the "
                "same complete identifier"
            )
        elif len({item["module"] for item in implementations}) != len(implementations):
            errors.append(
                f"identifier_collisions[{key}] implementations must use distinct modules"
            )
        _check_evidence_keys(
            f"identifier_collisions[{key}]", row.get("evidence_keys"), evidence, errors
        )

    candidates = _record_index(
        manifest.get("duplicate_candidates"), "duplicate_candidates", errors
    )
    collision_use_count = {key: 0 for key in collisions}
    for key, row in candidates.items():
        _check_fields(
            f"duplicate_candidates[{key}]",
            row,
            {
                "key", "discovery", "collision_keys", "implementations",
                "responsibility", "verdict",
                "canonical_owner", "caller_semantics", "divergence_is_defect",
                "boundary_fit", "baseline_evidence_key", "rationale", "evidence_keys",
                "contract_evidence_keys", "caller_evidence_keys", "owner_evidence_keys",
            },
            errors,
        )
        verdict = row.get("verdict")
        if verdict not in _VERDICTS:
            errors.append(f"duplicate_candidates[{key}].verdict must be converge or retain")
        discovery = row.get("discovery")
        if discovery not in _DISCOVERY_KINDS:
            errors.append(
                f"duplicate_candidates[{key}].discovery must be exact-name or different-name"
            )
        collision_keys = row.get("collision_keys")
        if not isinstance(collision_keys, list):
            errors.append(f"duplicate_candidates[{key}].collision_keys must be a list")
            collision_keys = []
        if discovery == "exact-name" and not collision_keys:
            errors.append(
                f"duplicate_candidates[{key}] exact-name requires collision_keys"
            )
        if discovery == "different-name" and collision_keys:
            errors.append(
                f"duplicate_candidates[{key}] different-name cannot claim collision_keys"
            )
        expected_implementations = set()
        for collision_key in collision_keys:
            collision = collisions.get(collision_key)
            if collision is None:
                errors.append(
                    f"duplicate_candidates[{key}] references unknown collision "
                    f"{collision_key!r}"
                )
                continue
            collision_use_count[collision_key] += 1
            expected_implementations.update(
                (item.get("module"), item.get("identifier"))
                for item in collision.get("implementations") or []
                if isinstance(item, dict)
            )
        implementations = row.get("implementations")
        if (
            not isinstance(implementations, list)
            or len(implementations) < 2
            or not all(
                isinstance(item, dict)
                and set(item) == {"module", "identifier"}
                and _text(item.get("module"))
                and _text(item.get("identifier"))
                for item in implementations
            )
        ):
            errors.append(
                f"duplicate_candidates[{key}].implementations needs at least two "
                "module-qualified implementations"
            )
        elif expected_implementations:
            actual_implementations = {
                (item.get("module"), item.get("identifier"))
                for item in implementations
            }
            if actual_implementations != expected_implementations:
                errors.append(
                    f"duplicate_candidates[{key}].implementations must exactly cover "
                    "its collision_keys"
                )
        for field in ("responsibility", "caller_semantics", "rationale"):
            if not _text(row.get(field)):
                errors.append(f"duplicate_candidates[{key}].{field} is required")
        if verdict == "converge":
            for field in ("canonical_owner", "boundary_fit", "baseline_evidence_key"):
                if not _text(row.get(field)):
                    errors.append(
                        f"duplicate_candidates[{key}] converge requires {field}"
                    )
            baseline_key = row.get("baseline_evidence_key")
            _check_evidence_keys(
                f"duplicate_candidates[{key}].baseline_evidence_key",
                [baseline_key],
                evidence,
                errors,
            )
            _check_evidence_role(
                f"duplicate_candidates[{key}].baseline_evidence_key",
                baseline_key,
                evidence,
                {"test"},
                errors,
            )
            baseline = evidence.get(baseline_key, {})
            if _text(baseline.get("command")):
                _check_exact_test(
                    f"duplicate_candidates[{key}].baseline evidence command",
                    baseline.get("command"),
                    errors,
                )
            if row.get("divergence_is_defect") is not True:
                errors.append(
                    f"duplicate_candidates[{key}] converge must prove divergence_is_defect"
                )
            for proof in (
                "contract_evidence_keys", "caller_evidence_keys", "owner_evidence_keys"
            ):
                _check_evidence_keys(
                    f"duplicate_candidates[{key}].{proof}",
                    row.get(proof),
                    evidence,
                    errors,
                )
            proof_roles = {
                "contract_evidence_keys": "contract",
                "caller_evidence_keys": "caller",
                "owner_evidence_keys": "owner",
            }
            proof_sets = []
            for proof, role in proof_roles.items():
                keys = row.get(proof) or []
                proof_sets.append(set(keys))
                for evidence_key in keys:
                    _check_evidence_role(
                        f"duplicate_candidates[{key}].{proof}",
                        evidence_key,
                        evidence,
                        {role},
                        errors,
                    )
            if any(left & right for index, left in enumerate(proof_sets) for right in proof_sets[index + 1:]):
                errors.append(
                    f"duplicate_candidates[{key}] contract/caller/owner proof evidence "
                    "must be role-distinct"
                )
        _check_evidence_keys(
            f"duplicate_candidates[{key}]", row.get("evidence_keys"), evidence, errors
        )
    for collision_key, count in collision_use_count.items():
        if count != 1:
            errors.append(
                f"identifier collision {collision_key} must map to exactly one "
                f"responsibility candidate; actual={count}"
            )
    scanned_collision_counts = Counter(
        key
        for row in source_scans.values()
        for key in row.get("collision_keys") or []
    )
    scanned_collision_keys = set(scanned_collision_counts)
    if scanned_collision_keys != set(collisions):
        errors.append(
            "source_scans collision_keys must exactly equal identifier_collisions keys"
        )
    for key, count in scanned_collision_counts.items():
        if count != 1:
            errors.append(
                f"source scan collision key {key} must be owned by exactly one scan; "
                f"actual={count}"
            )
    scanned_different_counts = Counter(
        key
        for row in source_scans.values()
        for key in row.get("different_name_candidate_keys") or []
    )
    scanned_different_keys = set(scanned_different_counts)
    actual_different_keys = {
        key for key, row in candidates.items() if row.get("discovery") == "different-name"
    }
    if scanned_different_keys != actual_different_keys:
        errors.append(
            "source_scans different_name_candidate_keys must exactly equal "
            "different-name responsibility candidate keys"
        )
    for key, count in scanned_different_counts.items():
        if count != 1:
            errors.append(
                f"different-name candidate key {key} must be owned by exactly one "
                f"source scan; actual={count}"
            )

    expected_mappings = {}
    for key in assembly_gap_edges:
        expected_mappings[key] = "assembly"
    for key, row in seams.items():
        if row.get("wired") is True and row.get("integration_verified") is True:
            continue
        if key in expected_mappings:
            errors.append(f"mapping key {key} is reused across inventory kinds")
        expected_mappings[key] = "seam"
    for key, row in candidates.items():
        if row.get("verdict") == "converge":
            if key in expected_mappings:
                errors.append(f"mapping key {key} is reused across inventory kinds")
            expected_mappings[key] = "duplicate"

    proposals = _record_index(
        manifest.get("proposed_items"), "proposed_items", errors
    )
    proposal_keys = set(proposals)
    expected_keys = set(expected_mappings)
    if proposal_keys != expected_keys:
        missing = sorted(expected_keys - proposal_keys)
        extra = sorted(proposal_keys - expected_keys)
        if missing:
            errors.append(f"proposed_items missing mapping keys: {missing}")
        if extra:
            errors.append(f"proposed_items contain unmapped keys: {extra}")

    existing_by_number = {item.item_number: item for item in existing_items}
    existing_by_finding = {
        _finding_key(item.kind, item.subject, item.source): item
        for item in existing_items
    }
    used_existing = set()
    for key, row in proposals.items():
        _check_fields(
            f"proposed_items[{key}]",
            row,
            {
                "key", "kind", "subject", "source", "found_in_round",
                "existing_item_number",
            },
            errors,
        )
        kind = row.get("kind")
        if kind not in _KINDS:
            errors.append(f"proposed_items[{key}].kind is invalid")
        if key in expected_mappings and kind != expected_mappings[key]:
            errors.append(
                f"proposed_items[{key}].kind={kind!r}, expected {expected_mappings[key]!r}"
            )
        _check_standalone_payload(key, row.get("subject"), "subject", errors)
        _check_standalone_payload(key, row.get("source"), "source", errors)

        existing_number = row.get("existing_item_number")
        if existing_number is not None:
            item = existing_by_number.get(existing_number)
            if item is None:
                errors.append(
                    f"proposed_items[{key}] references unknown existing_item_number"
                )
            else:
                if item.kind != kind:
                    errors.append(
                        f"proposed_items[{key}] existing item has a different kind"
                    )
                if existing_number in used_existing:
                    errors.append(
                        f"existing item {existing_number} maps more than one source row"
                    )
                used_existing.add(existing_number)
                if row.get("found_in_round") != item.found_in_round:
                    errors.append(
                        f"proposed_items[{key}].found_in_round must match the existing item"
                    )
                if (row.get("subject") or "").strip() != item.subject.strip():
                    errors.append(
                        f"proposed_items[{key}].subject does not match the existing item"
                    )
                if (row.get("source") or "").strip() != item.source.strip():
                    errors.append(
                        f"proposed_items[{key}].source does not match the existing item"
                    )
        else:
            matching = existing_by_finding.get(
                _finding_key(kind, row.get("subject"), row.get("source"))
            )
            if matching is not None:
                errors.append(
                    f"proposed_items[{key}] matches existing {matching.item_number}; "
                    "set existing_item_number instead of proposing a new create"
                )
            if row.get("found_in_round") != expected_round:
                errors.append(
                    f"proposed_items[{key}].found_in_round must be {expected_round}"
                )

        source = row.get("source") or ""
        if kind == "assembly" and key in assembly_gap_parts:
            missing_edge = assembly_gap_edges.get(key, "")
            if missing_edge and missing_edge not in f"{row.get('subject') or ''}\n{source}":
                errors.append(
                    f"proposed_items[{key}] must repeat its exact missing_edge"
                )
            numbers = {
                parts[part_key].get("design_number")
                for part_key in assembly_gap_parts[key]
                if part_key in parts
            }
            for number in numbers:
                if _text(number) and number not in source:
                    errors.append(
                        f"proposed_items[{key}].source must cite {number} in full"
                    )
        if kind == "seam" and key in seams:
            standalone = f"{row.get('subject') or ''}\n{source}"
            for endpoint in (seams[key].get("producer"), seams[key].get("consumer")):
                if _text(endpoint) and endpoint not in standalone:
                    errors.append(
                        f"proposed_items[{key}] must repeat seam endpoint {endpoint!r}"
                    )
            for number in (
                seams[key].get("producer_design_number"),
                seams[key].get("consumer_design_number"),
            ):
                if _text(number) and number not in source:
                    errors.append(
                        f"proposed_items[{key}].source must cite {number} in full"
                    )

    resource_errors, resource_blockers, resource_requests = integration_resource_audit.check(
        manifest, set(allowed_designs), existing_items,
    )
    errors.extend(resource_errors)
    counts = {
        "evidence": len(evidence),
        "production_entries": len(entries),
        "capability_designs": len(allowed_designs),
        "design_coverage": len(design_coverage),
        "source_scans": len(source_scans),
        "declared_parts": len(parts),
        "assembly_rows": len(assembly),
        "unreachable_parts": sum(
            row.get("reachable") is False for row in assembly.values()
        ),
        "assembly_gaps": len(assembly_gap_edges),
        "seams": len(seams),
        "seam_gaps": sum(
            row.get("wired") is not True
            or row.get("integration_verified") is not True
            for row in seams.values()
        ),
        "identifier_collisions": len(collisions),
        "duplicate_candidates": len(candidates),
        "converge_candidates": sum(
            row.get("verdict") == "converge" for row in candidates.values()
        ),
        "proposed_items": len(proposals),
        "expected_items": len(expected_mappings),
    }
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    basis = completion_basis_fingerprint(project_id)
    live_identity = [
        {field: row.to_dict().get(field) for field in (
            "item_number", "kind", "subject", "source", "driver", "status",
            "covered_by", "verified_revision", "repair_record", "blocked_reason", "waive_reason", "found_in_round",
        )}
        for row in delivery_item_service.list_by_project(project_id, kinds=sorted(_KINDS))
    ]
    audit_material = json.dumps(
        {"manifest": manifest, "live_items": live_identity},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    validation_id = hashlib.sha256((basis + audit_material).encode("utf-8")).hexdigest()
    audit = {
        "evidence_blocks_valid": not any(
            error.startswith("evidence[") for error in errors
        ),
        "production_entries_valid": not any(
            error.startswith("production_entries[") for error in errors
        ),
        "assembly_is_exact_partition": assembly_keys == part_keys,
        "capability_designs_are_covered": covered_design_numbers == allowed_designs,
        "parts_and_seams_are_owned_once": (
            all(count == 1 for count in part_coverage_count.values())
            and all(count == 1 for count in seam_coverage_count.values())
        ),
        "source_scans_reconcile": (
            scanned_collision_keys == set(collisions)
            and scanned_different_keys == actual_different_keys
            and all(count == 1 for count in scanned_collision_counts.values())
            and all(count == 1 for count in scanned_different_counts.values())
        ),
        "mapping_is_exact_partition": proposal_keys == expected_keys,
        "collisions_are_exact_partition": all(
            count == 1 for count in collision_use_count.values()
        ),
        "item_formula": (
            counts["expected_items"]
            == counts["assembly_gaps"]
            + counts["seam_gaps"]
            + counts["converge_candidates"]
        ),
        "all_checks_pass": not errors,
    }
    if not errors:
        create_items = [
            row for row in proposals.values()
            if row.get("existing_item_number") is None
        ]
        # Separate audit table keeps legacy receipts schema-compatible while
        # recording the full current-baseline completion contract.
        audit_manifest = dict(manifest)
        audit_manifest["_live_items"] = [row.to_dict() for row in delivery_item_service.list_by_project(project_id, kinds=sorted(_KINDS))]
        live_items = delivery_item_service.list_by_project(project_id, kinds=sorted(_KINDS))
        # Only the Round-4 resubmission after creation/coverage is a completion.
        # Initial inventories still persist their basis for receipt binding but
        # cannot move a gate while they contain new or open obligations.
        live_open = any(row.status == "open" for row in live_items)
        final = not create_items and not live_open and not resource_blockers
        with database_proxy.atomic():
            integration_inventory_validation_repo.replace_active(
                project_id,
                validation_id,
                expected_round,
                json.dumps(create_items, ensure_ascii=False, sort_keys=True),
            )
            integration_completion_audit_repo.replace_active(
                project_id, validation_id, expected_round,
                basis, json.dumps(audit_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                json.dumps(list(proposals.values()), ensure_ascii=False, sort_keys=True),
                utcnow() if final else None,
            )
    return {
        "valid": not errors,
        "validation_id": validation_id,
        "expected_found_in_round": expected_round,
        "counts": counts,
        "audit": audit,
        "errors": errors,
        "resource_requests": resource_requests,
        "pending_creation_keys": [
            key for key, row in proposals.items() if not row.get("existing_item_number")
        ],
    }


def authorize_create(project_id, validation_id, source_row_key, payload):
    """Return the active receipt only when one proposed row exactly matches."""
    if not _text(validation_id) or not _text(source_row_key):
        raise ValidationError(
            "Integration INTEG creation requires validation_id and source_row_key "
            "from the latest valid harnoq_integration_inventory_validate result"
        )
    receipt = integration_inventory_validation_repo.get_active(
        project_id, validation_id.strip()
    )
    audit = integration_completion_audit_repo.get_active(project_id)
    if receipt is None or audit is None or audit.validation_id != validation_id.strip() or audit.basis_fingerprint != completion_basis_fingerprint(project_id):
        raise ConflictError(
            "validation_id is not the active valid Integration inventory receipt; "
            "validate the complete current manifest again"
        )
    proposals = json.loads(receipt.proposed_items_json or "[]")
    proposed = next(
        (row for row in proposals if row.get("key") == source_row_key.strip()), None
    )
    if proposed is None:
        raise ConflictError(
            "source_row_key is not an unpersisted item in the validated manifest"
        )
    expected = {
        "kind": proposed.get("kind"),
        "subject": proposed.get("subject"),
        "source": proposed.get("source"),
        "found_in_round": proposed.get("found_in_round"),
    }
    actual = {key: payload.get(key) for key in expected}
    if actual != expected:
        raise ConflictError(
            "INTEG payload differs from the server-validated proposed item; "
            "use the exact kind, subject, source and found_in_round"
        )
    created = json.loads(receipt.created_items_json or "{}")
    if source_row_key.strip() in created:
        raise ConflictError("this validated source_row_key was already created")
    return receipt


def record_created(receipt, source_row_key, item_number):
    created = json.loads(receipt.created_items_json or "{}")
    created[source_row_key.strip()] = item_number
    receipt.created_items_json = json.dumps(created, ensure_ascii=False, sort_keys=True)
    proposals = json.loads(receipt.proposed_items_json or "[]")
    if len(created) >= len(proposals):
        receipt.active = False
    integration_inventory_validation_repo.save(receipt)
    audit = integration_completion_audit_repo.get_active(receipt.project_id)
    if audit is not None and audit.validation_id == receipt.validation_id:
        created = json.loads(audit.created_items_json or "{}")
        created[source_row_key.strip()] = item_number
        audit.created_items_json = json.dumps(created, ensure_ascii=False, sort_keys=True)
        integration_completion_audit_repo.save(audit)
