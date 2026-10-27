"""Recover and revise stored inventories without weakening the full validator."""
import hashlib
import json

from app.core.errors import ConflictError, ValidationError
from app.db.base import database_proxy
from app.repositories import integration_completion_audit_repo
from app.services import (
    delivery_gate, delivery_item_service, integration_inventory_validator as validator,
    integration_resource_audit,
)


SECTION_FIELDS = frozenset({
    "production_entries", "design_coverage", "source_scans", "declared_parts",
    "assembly_rows", "seams", "identifier_collisions", "duplicate_candidates",
    "proposed_items", "external_resources",
})
_ITEM_FIELDS = (
    "item_number", "kind", "subject", "source", "found_in_round",
    "status", "covered_by", "waive_reason", "evidence_command",
    "evidence_exit_code", "evidence_output", "verified_revision",
)


def _digest(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def _item_identity(row):
    return {key: row.get(key) for key in _ITEM_FIELDS}


def get_inventory(project_id):
    """Return saved input, current obligations and an optimistic revision token."""
    audit = integration_completion_audit_repo.get_active(project_id)
    gate = delivery_gate.gate_status(project_id)
    if audit is None:
        return {
            "has_inventory": False, "manifest": None,
            "completion_errors": ["no saved Integration inventory"],
            "delivery_gate": gate,
            "next_step": "Submit the initial complete manifest with harnoq_integration_inventory_validate.",
        }
    try:
        manifest = json.loads(audit.manifest_json)
        created = json.loads(audit.created_items_json)
        if not isinstance(manifest, dict) or not isinstance(created, dict):
            raise ValueError
    except (TypeError, ValueError):
        raise ConflictError("Saved Integration inventory is corrupt; submit a complete current manifest")
    saved_items = manifest.pop("_live_items", [])
    live_items = delivery_item_service.list_by_project(project_id, kinds=sorted(validator._KINDS))
    current_items = [
        _item_identity(row.to_dict())
        for row in live_items
    ]
    current_items.sort(key=lambda row: row["item_number"])
    basis = validator.completion_basis_fingerprint(project_id)
    coverage = {row["design_number"]: row for row in manifest.get("design_coverage", [])}
    current_designs = {
        number: {
            "selected_solution_sha256": hashlib.sha256((design.selected_solution or "").encode()).hexdigest(),
            "design_scope_sha256": hashlib.sha256((design.design_scope or "").encode()).hexdigest(),
        }
        for number, design in validator._capability_designs(project_id).items()
    }
    design_changes = [
        {"design_number": number, "change": "removed" if number not in current_designs else
         "added" if number not in coverage else "changed",
         "current_hashes": current_designs.get(number)}
        for number in sorted(set(coverage) | set(current_designs))
        if number not in current_designs or number not in coverage
        or any(coverage[number].get(key) != value for key, value in current_designs[number].items())
    ]
    before = {row["item_number"]: _item_identity(row) for row in saved_items}
    after = {row["item_number"]: row for row in current_items}
    item_changes = [
        {"item_number": number, "before": before.get(number), "current": after.get(number)}
        for number in sorted(set(before) | set(after)) if before.get(number) != after.get(number)
    ]
    completion = validator.completion_status(project_id)
    resource_errors, resource_blockers, resource_requests = integration_resource_audit.check(
        manifest, set(current_designs), live_items,
    )
    basis_changed = audit.basis_fingerprint != basis
    review = []
    if basis_changed:
        review.append("Task completion/status or confirmed design basis changed. Read project context, review affected code/paths and rerun affected evidence before revising; unchanged sections may be reused.")
    if design_changes:
        review.append("Review design_changes and update affected design_coverage, declarations, seams and resource audits; do not merely replace hashes.")
    if item_changes:
        review.append("Reconcile item_changes with the saved findings; item closure alone does not prove a repaired assembly/seam.")
    complete = completion["complete"] and not gate["integration_owed"] and not gate["capability_outstanding"]
    return {
        "has_inventory": True,
        "validation_id": audit.validation_id,
        "revision_token": _digest([audit.validation_id, basis, current_items]),
        "basis_changed": basis_changed,
        "design_changes": design_changes,
        "item_changes": item_changes,
        "review_required": review,
        "manifest": manifest,
        "created_items": created,
        "current_items": current_items,
        "required_reruns": [
            {"item_number": item.item_number, "covered_by": item.covered_by,
             "command": validator.rerun_command(item)}
            for item in live_items if item.status == "covered"
        ],
        "expected_found_in_round": max((row["found_in_round"] for row in current_items), default=0) + 1,
        "completion_errors": completion["errors"],
        "resource_errors": resource_errors + resource_blockers,
        "resource_requests": resource_requests,
        "integration_complete": complete,
        "delivery_gate": gate,
        "next_step": "Integration is complete on the current basis; read harnoq_project_context for the next stage." if complete else (
            "Review changes and required_reruns, then call harnoq_integration_inventory_revise "
            "with validation_id as base_validation_id and revision_token as reviewed_revision_token. "
            "Send only new/replacement evidence and changed sections; the server revalidates the full manifest. "
            "Preparing this payload is agent work, not a user approval or external blocker."
        ),
    }


def revise(project_id, base_validation_id, reviewed_revision_token, evidence_updates=None, section_updates=None):
    """Merge a reviewed delta, then use exactly the ordinary full validation path."""
    evidence_updates = [] if evidence_updates is None else evidence_updates
    section_updates = {} if section_updates is None else section_updates
    if not isinstance(section_updates, dict) or set(section_updates) - SECTION_FIELDS:
        raise ValidationError("section_updates accepts only named inventory sections; evidence uses evidence_updates")
    for key, value in section_updates.items():
        if not isinstance(value, dict if key == "external_resources" else list):
            raise ValidationError(f"section_updates.{key} has the wrong type; use a complete section, not null")
    if not isinstance(evidence_updates, list):
        raise ValidationError("evidence_updates must be a list")
    keys = set()
    for row in evidence_updates:
        key = row.get("key") if isinstance(row, dict) else None
        if not isinstance(key, str) or not key.strip() or key in keys:
            raise ValidationError("evidence_updates requires unique non-empty evidence keys")
        keys.add(key)
    with database_proxy.atomic():
        state = get_inventory(project_id)
        if not state["has_inventory"]:
            raise ConflictError("No saved inventory; submit the initial complete manifest")
        if base_validation_id != state["validation_id"] or reviewed_revision_token != state["revision_token"]:
            raise ConflictError("Inventory or project state changed; call harnoq_integration_inventory_get and review the current revision before retrying")
        missing = []
        for item in state["required_reruns"]:
            test = item["command"]
            reruns = [
                row for row in evidence_updates
                if test and row.get("role") == "test" and isinstance(row.get("command"), str)
                and validator.matches_rerun(test, row)
            ]
            # A failed fresh run must not be hidden by an old success under a
            # different evidence key. On a changed basis, old runs alone never
            # establish current coverage, even if their command still matches.
            if (state["basis_changed"] and not reruns) or any(
                type(row.get("exit_code")) is not int or row["exit_code"] != 0
                or not any(isinstance(row.get(k), str) and row[k].strip() for k in ("stdout", "stderr"))
                for row in reruns
            ):
                missing.append(item["item_number"])
        if missing:
            raise ValidationError(
                "Fresh successful evidence_updates naming the exact evidence_command checks are required "
                "(missing on changed basis or failed/empty rerun) for: " + ", ".join(missing)
            )
        manifest = state["manifest"]
        merged = {row["key"]: row for row in manifest.get("evidence", [])}
        merged.update({row["key"]: row for row in evidence_updates})
        manifest["evidence"] = list(merged.values())
        manifest.update(section_updates)
        manifest["found_in_round"] = state["expected_found_in_round"]
        # Reconcile only server-recorded creations, never infer that a finding
        # is fixed or waived. Removed/changed live objects still fail validation.
        for row in manifest.get("proposed_items", []):
            if not isinstance(row, dict):
                continue  # The full validator reports the malformed row.
            if not row.get("existing_item_number"):
                number = state["created_items"].get(row.get("key"))
                if number:
                    row["existing_item_number"] = number
                else:
                    row["found_in_round"] = manifest["found_in_round"]
        result = validator.validate(project_id, manifest)
        result["reused_inventory"] = True
        return result
