"""Server-owned completeness and creation binding for System inventories.

The manifest describes the agent's reading of free-form INPUT documents; the
server can therefore verify coverage and identity, rather than pretending it
can infer journeys from arbitrary prose.  A receipt is current only while its
source basis and every reconciled SYS row still agree with that manifest.
"""
import hashlib
import json

from app.core.errors import ConflictError, ValidationError
from app.repositories import (
    delivery_item_repo, project_constitution_repo, source_requirement_repo,
    system_inventory_validation_repo,
    development_task_repo, design_case_repo, feature_repo, task_event_repo,
    integration_completion_audit_repo,
)
from app.services.nfr_baseline import COMMITMENT, extract_nfr_rows

_KINDS = frozenset(("journey", "nfr"))


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _norm(value):
    return " ".join((value or "").split()).casefold()


def _nfr_key(row, occurrence=1):
    """Stable key for one occurrence, including duplicated baseline rows."""
    value = "\x1f".join(_norm(row.get(field, "")) for field in ("metric", "target", "applies_to"))
    return f"nfr:{hashlib.sha256(value.encode()).hexdigest()[:24]}:{occurrence}"


def _nfr_keys(rows):
    occurrences = {}
    keys = []
    for row in rows:
        value = "\x1f".join(
            _norm(row.get(field, "")) for field in ("metric", "target", "applies_to")
        )
        occurrences[value] = occurrences.get(value, 0) + 1
        keys.append(_nfr_key(row, occurrences[value]))
    return keys


def basis_snapshot(project_id):
    """Low-level reader: intentionally does not call delivery_gate."""
    inputs = [row for row in source_requirement_repo.list_by_project(project_id) if row.status == "confirmed"]
    constitution = project_constitution_repo.get_for_project(project_id)
    baseline = constitution.nfr_baseline if constitution else ""
    # Do this directly rather than via delivery_gate: the latter reads our
    # status, whereas this snapshot must remain a one-way lower-level reader.
    from app.repositories import delivery_stage_run_repo
    integration_run = delivery_stage_run_repo.latest_closed(project_id, "integration")
    from app.services import integration_inventory_validator
    completion = integration_inventory_validator.completion_status(project_id)
    payload = {
        "inputs": [{"number": row.source_requirement_number, "content": row.raw_content, "status": row.status} for row in inputs],
        "nfr_baseline": baseline,
        "latest_integration_completion": integration_run.closed_at.isoformat() if integration_run else None,
        "integration_completion_audit": (
            (audit.validation_id, audit.completed_at.isoformat())
            if (audit := integration_completion_audit_repo.get_active(project_id)) and audit.completed_at and completion["complete"] else None
        ),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return payload, hashlib.sha256(canonical.encode()).hexdigest()


def _has_inventory_basis(project_id):
    snapshot, _ = basis_snapshot(project_id)
    return bool(snapshot["inputs"] or extract_nfr_rows(snapshot["nfr_baseline"]))


def _proposal_identity(row):
    return tuple(_norm(row.get(field)) for field in ("kind", "subject", "source", "driver")) + (row.get("found_in_round"),)


def _manifest_errors(project_id, manifest):
    errors = []
    if not isinstance(manifest, dict):
        return ["manifest must be an object"], [], None, None
    snapshot, fingerprint = basis_snapshot(project_id)
    inputs = snapshot["inputs"]
    current_numbers = {row["number"] for row in inputs}
    audits = manifest.get("input_audits")
    if not isinstance(audits, list):
        errors.append("input_audits must be a list")
        audits = []
    audit_by_number = {}
    journey_keys = set()
    for index, audit in enumerate(audits):
        if not isinstance(audit, dict) or not _text(audit.get("input_number")):
            errors.append(f"input_audits[{index}].input_number is required")
            continue
        number = audit["input_number"].strip()
        if number in audit_by_number or number not in current_numbers:
            errors.append(f"input audit {number} is duplicate or outside this project")
        audit_by_number[number] = audit
        keys, reason = audit.get("journey_keys"), audit.get("no_journey_reason")
        if not isinstance(keys, list) or any(not _text(key) for key in keys):
            errors.append(f"input audit {number}.journey_keys must be string list")
            keys = []
        if bool(keys) == _text(reason):
            errors.append(f"input audit {number} must have journey_keys xor no_journey_reason")
        for key in keys:
            key = key.strip()
            if key in journey_keys:
                errors.append(f"journey key {key} is duplicated")
            journey_keys.add(key)
    if set(audit_by_number) != current_numbers:
        errors.append("input_audits must cover every confirmed INPUT exactly once")

    constitution = project_constitution_repo.get_for_project(project_id)
    commitments = [row for row in extract_nfr_rows(constitution.nfr_baseline if constitution else "") if row["strength"] == COMMITMENT]
    nfr_audits = manifest.get("nfr_audits")
    if not isinstance(nfr_audits, list):
        errors.append("nfr_audits must be a list")
        nfr_audits = []
    expected_nfr = _nfr_keys(commitments)
    seen_nfr = set()
    for index, audit in enumerate(nfr_audits):
        if not isinstance(audit, dict):
            errors.append(f"nfr_audits[{index}] must be an object")
            continue
        key, proposal = audit.get("nfr_key"), audit.get("proposed_item_key")
        if not _text(key) or not _text(proposal):
            errors.append(f"nfr_audits[{index}] needs nfr_key and proposed_item_key")
            continue
        if key in seen_nfr:
            errors.append(f"nfr commitment {key} occurs more than once")
        seen_nfr.add(key)
    if seen_nfr != set(expected_nfr) or len(nfr_audits) != len(expected_nfr):
        errors.append("nfr_audits must map every commitment exactly once")

    proposals = manifest.get("proposed_items")
    if not isinstance(proposals, list):
        errors.append("proposed_items must be a list")
        proposals = []
    by_key = {}
    existing = {item.item_number: item for item in delivery_item_repo.list_by_project(project_id, kinds=_KINDS)}
    for index, row in enumerate(proposals):
        if not isinstance(row, dict) or not _text(row.get("key")):
            errors.append(f"proposed_items[{index}].key is required")
            continue
        key = row["key"].strip()
        if key in by_key:
            errors.append(f"proposed item key {key} is duplicated")
        by_key[key] = row
        if row.get("kind") not in _KINDS or not all(_text(row.get(field)) for field in ("subject", "source", "driver")):
            errors.append(f"proposed item {key} needs kind, subject, source and driver")
        if not isinstance(row.get("found_in_round"), int) or isinstance(row.get("found_in_round"), bool) or row["found_in_round"] < 1:
            errors.append(f"proposed item {key}.found_in_round must be positive")
        existing_number = row.get("existing_item_number")
        if existing_number is not None:
            item = existing.get(existing_number)
            if item is None or _proposal_identity(row) != _proposal_identity(item.to_dict()):
                errors.append(f"existing reconciliation for {key} does not match a live SYS item")
    declared_keys = journey_keys | {a.get("proposed_item_key", "").strip() for a in nfr_audits if isinstance(a, dict) and _text(a.get("proposed_item_key"))}
    if set(by_key) != declared_keys:
        errors.append("proposed_items must be the exact union of input journeys and NFR mappings")
    for key in journey_keys:
        if by_key.get(key, {}).get("kind") != "journey":
            errors.append(f"journey key {key} must map to kind=journey")
    for audit in nfr_audits:
        if isinstance(audit, dict) and _text(audit.get("proposed_item_key")):
            key = audit["proposed_item_key"].strip()
            if by_key.get(key, {}).get("kind") != "nfr":
                errors.append(f"NFR mapping {key} must map to kind=nfr")

    rounds = [item.found_in_round for item in existing.values()]
    expected_round = max(rounds, default=0) + 1
    if manifest.get("found_in_round") != expected_round:
        errors.append(f"found_in_round must be {expected_round}")
    for row in by_key.values():
        # Reconciled rows keep the round in which they were found.  Only a
        # newly persisted obligation belongs to this pass; forcing old rows to
        # wear the new number would make exact identity reconciliation
        # impossible on every re-entry.
        if not row.get("existing_item_number") and row.get("found_in_round") != expected_round:
            errors.append("each new proposed item must use the current found_in_round")
    reruns = manifest.get("rerun_evidence", [])
    if not isinstance(reruns, list):
        errors.append("rerun_evidence must be a list")
        reruns = []
    evidence = {row.get("item_number"): row for row in reruns if isinstance(row, dict)}
    for row in by_key.values():
        number = row.get("existing_item_number")
        item = existing.get(number) if number else None
        if item is not None and item.status == "covered":
            proof = evidence.get(number)
            if not proof or not _text(proof.get("command")) or not isinstance(proof.get("exit_code"), int) or not isinstance(proof.get("stdout"), str) or not isinstance(proof.get("stderr"), str):
                errors.append(f"covered {number} requires complete rerun evidence")
            elif proof["exit_code"] != 0:
                # A stale covered row cannot keep the gate closed.
                item.status, item.covered_by = "open", ""
                delivery_item_repo.save(item)
                errors.append(f"covered {number} rerun failed and was reopened")
    return errors, list(by_key.values()), fingerprint, expected_round


def validate(project_id, manifest):
    errors, proposals, fingerprint, expected_round = _manifest_errors(project_id, manifest)
    canonical = json.dumps(manifest if isinstance(manifest, dict) else {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    validation_id = hashlib.sha256(((fingerprint or "") + canonical).encode()).hexdigest()
    if not errors:
        system_inventory_validation_repo.replace_active(project_id, validation_id, expected_round, fingerprint, canonical, json.dumps(proposals, ensure_ascii=False, sort_keys=True))
    constitution = project_constitution_repo.get_for_project(project_id)
    required_nfr_keys = _nfr_keys([
        row for row in extract_nfr_rows(constitution.nfr_baseline if constitution else "")
        if row["strength"] == COMMITMENT
    ])
    return {"valid": not errors, "validation_id": validation_id, "expected_found_in_round": expected_round, "errors": errors, "pending_keys": [row.get("key") for row in proposals if not row.get("existing_item_number")], "required_nfr_keys": required_nfr_keys}


def authorize_create(project_id, validation_id, source_row_key, payload):
    # A project with no confirmed functional source and no NFR baseline has no
    # server-derivable inventory. Preserve the manual correction path for that
    # genuinely empty baseline; as soon as either source exists, SYS writes are
    # receipt-bound.
    if not _has_inventory_basis(project_id):
        return None
    if not _text(validation_id) or not _text(source_row_key):
        raise ValidationError("System SYS creation requires validation_id and source_row_key from a current inventory receipt")
    receipt = system_inventory_validation_repo.get_active(project_id, validation_id.strip())
    if receipt is None or receipt.basis_fingerprint != basis_snapshot(project_id)[1]:
        raise ConflictError("validation_id is not a current System inventory receipt; validate again")
    proposals = json.loads(receipt.proposed_items_json or "[]")
    proposal = next((row for row in proposals if row.get("key") == source_row_key.strip()), None)
    if proposal is None or proposal.get("existing_item_number"):
        raise ConflictError("source_row_key is not a new validated System item")
    expected = {field: proposal.get(field) for field in ("kind", "subject", "source", "driver", "found_in_round")}
    if {field: payload.get(field) for field in expected} != expected:
        raise ConflictError("SYS payload differs from the server-validated proposed item")
    created = json.loads(receipt.created_items_json or "{}")
    if source_row_key.strip() in created:
        raise ConflictError("this validated source_row_key was already created")
    return receipt


def record_created(receipt, source_row_key, item_number):
    if receipt is None:
        return
    created = json.loads(receipt.created_items_json or "{}")
    created[source_row_key.strip()] = item_number
    receipt.created_items_json = json.dumps(created, ensure_ascii=False, sort_keys=True)
    system_inventory_validation_repo.save(receipt)


def status(project_id):
    """Read-only status consumed by delivery_gate; never calls the gate."""
    receipt = system_inventory_validation_repo.get_active(project_id)
    if receipt is None:
        if not _has_inventory_basis(project_id):
            return {"complete": True, "has_receipt": False, "errors": [], "pending_keys": []}
        return {"complete": False, "has_receipt": False, "errors": ["no current System inventory receipt"], "pending_keys": []}
    if receipt.basis_fingerprint != basis_snapshot(project_id)[1]:
        return {"complete": False, "has_receipt": True, "errors": ["System inventory basis changed"], "pending_keys": []}
    try:
        proposals, created = json.loads(receipt.proposed_items_json), json.loads(receipt.created_items_json)
    except (TypeError, ValueError):
        return {"complete": False, "has_receipt": True, "errors": ["System inventory receipt is corrupt"], "pending_keys": []}
    items = {item.item_number: item for item in delivery_item_repo.list_by_project(project_id, kinds=_KINDS)}
    pending, errors = [], []
    for row in proposals:
        is_new = not row.get("existing_item_number")
        number = row.get("existing_item_number") or created.get(row.get("key"))
        if is_new and number is None:
            pending.append(row.get("key"))
            continue
        item = items.get(number)
        if item is None or _proposal_identity(row) != _proposal_identity(item.to_dict()):
            errors.append(f"mapped System item missing or changed: {row.get('key')}")
        elif item.status == "open":
            pending.append(row.get("key"))
    return {"complete": not errors and not pending, "has_receipt": True, "errors": errors, "pending_keys": pending}
