"""Structural checks for external resources and real production calls.

The execution agent supplies repository/runtime facts. This checks that each
design was audited and that readiness, real execution, or a scoped waiver has
explicit evidence; it does not independently execute the external resource.
"""


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _rows(value):
    return value if isinstance(value, list) else []


def check(manifest, design_numbers, items):
    """Return malformed-audit errors, completion blockers and preparation requests."""
    errors, blockers, requests = [], [], []
    audit = manifest.get("external_resources")
    if not isinstance(audit, dict):
        return ["external_resources audit is required; explicitly audit every capability DESIGN"], [], []

    evidence = {row.get("key"): row for row in _rows(manifest.get("evidence"))
                if isinstance(row, dict) and _text(row.get("key"))}
    entries = {row.get("key") for row in _rows(manifest.get("production_entries"))
               if isinstance(row, dict) and _text(row.get("key"))}

    def proof(key, role, owner, *, success=True):
        if not _text(key):
            errors.append(f"{owner} requires a {role} evidence key")
            return
        row = evidence.get(key)
        if row is None or row.get("role") != role:
            errors.append(f"{owner} must reference role={role} evidence")
            return
        code = row.get("exit_code")
        accepted = [0] if success else row.get("expected_exit_codes", [0])
        if type(code) is not int or not isinstance(accepted, list) or code not in accepted:
            errors.append(f"{owner} evidence did not succeed")
        if not _text(row.get("command")) or (success and not any(_text(row.get(k)) for k in ("stdout", "stderr"))):
            errors.append(f"{owner} needs an actual command and observable raw output")

    scans = audit.get("scan_evidence_keys")
    if not isinstance(scans, list) or not scans:
        errors.append("external_resources.scan_evidence_keys must audit production code/configuration")
    else:
        for key in scans:
            proof(key, "source-scan", "external resource discovery", success=False)

    reviews = audit.get("design_audits")
    if not isinstance(reviews, list):
        errors.append("external_resources.design_audits must be a list")
        reviews = []
    seen, referenced = set(), set()
    for row in reviews:
        if not isinstance(row, dict) or not _text(row.get("design_number")):
            errors.append("external resource design audit needs a DESIGN business number")
            continue
        number = row["design_number"]
        if number in seen or number not in design_numbers:
            errors.append(f"external resource design audit duplicate or foreign: {number}")
        seen.add(number)
        keys = row.get("resource_keys")
        if not isinstance(keys, list) or any(not _text(k) for k in keys):
            errors.append(f"{number}.resource_keys must be a string list")
            continue
        if len(set(keys)) != len(keys):
            errors.append(f"{number}.resource_keys contains duplicates")
        if bool(keys) == _text(row.get("no_resources_reason")):
            errors.append(f"{number} requires resource_keys xor no_resources_reason")
        referenced.update(keys)
    if seen != set(design_numbers):
        errors.append("external resource design_audits must cover every confirmed capability DESIGN exactly once")

    resources = audit.get("resources")
    if not isinstance(resources, list):
        errors.append("external_resources.resources must be a list")
        resources = []
    found, waivers = set(), set()
    live = {row.item_number: row for row in items}
    for resource in resources:
        if not isinstance(resource, dict) or not _text(resource.get("key")):
            errors.append("external resource needs a unique key")
            continue
        key = resource["key"]
        if key in found:
            errors.append(f"duplicate external resource: {key}")
        found.add(key)
        for field in ("name", "source", "configuration"):
            if not _text(resource.get(field)):
                errors.append(f"external resource {key}.{field} is required")
        state = resource.get("status")
        if not isinstance(state, str) or state not in {"verified", "missing", "invalid", "unverified", "waived"}:
            errors.append(f"external resource {key} has invalid status")
            continue
        if state == "verified":
            path = resource.get("entry_keys")
            if not isinstance(path, list) or not path or any(not _text(k) or k not in entries for k in path):
                errors.append(f"external resource {key} must identify the real production entry path")
            if resource.get("uses_test_double") is not False:
                errors.append(f"external resource {key}: a test double cannot prove real invocation")
            for field in ("operation", "observed_result"):
                if not _text(resource.get(field)):
                    errors.append(f"external resource {key}.{field} is required")
            proof(resource.get("readiness_evidence_key"), "resource-check", f"external resource {key} readiness")
            proof(resource.get("call_evidence_key"), "resource-call", f"external resource {key} real call")
        elif state == "waived":
            number = resource.get("waiver_item_number")
            item = live.get(number) if _text(number) else None
            # The existing human-authorized waiver mechanism owns this decision.
            # Exact source equality prevents using an unrelated waiver as proof.
            if (item is None or item.status != "waived" or not _text(item.waive_reason)
                    or item.source != resource.get("source") or number in waivers):
                errors.append(f"external resource {key} requires its own live waived INTEG with matching source")
            waivers.add(number if _text(number) else "")
        else:
            preparation = resource.get("preparation")
            if not _text(preparation):
                errors.append(f"external resource {key}.preparation must explain what is needed and how to verify it")
            blockers.append(f"external resource {key} is {state}: {preparation or 'resource preparation and a real call are required'}")
            requests.append({"key": key, "name": resource.get("name"), "status": state,
                             "configuration": resource.get("configuration"), "preparation": preparation})
    if found != referenced:
        errors.append("external resources must exactly match the union of design resource_keys")
    return errors, blockers, requests
