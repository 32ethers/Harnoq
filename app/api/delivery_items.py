"""DeliveryItem CRUD HTTP endpoints — see app/models/delivery_item.py."""
from flask import Blueprint, jsonify, request

from app.core.errors import ValidationError
from app.services import delivery_item_service as service

bp = Blueprint("delivery_items", __name__)

_INTEGRATION_KINDS = frozenset({"assembly", "seam", "duplicate"})
_SYSTEM_KINDS = frozenset({"journey", "nfr"})


def _stage_kind_scope():
    """Return the kind allow-list for a stage-specific alias, if applicable."""
    if request.path.endswith("/integration-items") or request.path.endswith("/integration-items/counts"):
        return _INTEGRATION_KINDS
    if request.path.endswith("/system-items") or request.path.endswith("/system-items/counts"):
        return _SYSTEM_KINDS
    return None


def _stage_payload(data):
    scope = _stage_kind_scope()
    if scope is not None and data.get("kind") not in scope:
        raise ValidationError(
            f"this stage-specific endpoint accepts only kinds {sorted(scope)}"
        )
    return data


@bp.post("/projects/<int:project_id>/integration-items")
@bp.post("/projects/<int:project_id>/system-items")
@bp.post("/projects/<int:project_id>/delivery-items")
def create_delivery_item(project_id):
    item = service.create(project_id, _stage_payload(request.get_json(silent=True) or {}))
    return jsonify(item.to_dict()), 201


@bp.get("/projects/<int:project_id>/integration-items")
@bp.get("/projects/<int:project_id>/system-items")
@bp.get("/projects/<int:project_id>/delivery-items")
def list_delivery_items(project_id):
    kinds = request.args.getlist("kind") or None
    scope = _stage_kind_scope()
    if scope is not None:
        requested = set(kinds or scope)
        if not requested <= scope:
            raise ValidationError(
                f"this stage-specific endpoint accepts only kinds {sorted(scope)}"
            )
        kinds = sorted(requested)
    items = service.list_by_project(project_id, kinds=kinds)
    return jsonify([item.to_dict() for item in items])


@bp.get("/projects/<int:project_id>/integration-items/counts")
@bp.get("/projects/<int:project_id>/system-items/counts")
@bp.get("/projects/<int:project_id>/delivery-items/counts")
def delivery_item_counts(project_id):
    counts = service.counts_by_kind(project_id)
    scope = _stage_kind_scope()
    if scope is not None:
        counts = {kind: counts.get(kind, 0) for kind in scope}
    return jsonify(counts)


@bp.get("/delivery-items/<int:item_id>")
def get_delivery_item(item_id):
    return jsonify(service.get(item_id).to_dict())


@bp.patch("/delivery-items/<int:item_id>")
def update_delivery_item(item_id):
    return jsonify(service.update(item_id, request.get_json(silent=True) or {}).to_dict())


@bp.delete("/delivery-items/<int:item_id>")
def delete_delivery_item(item_id):
    return jsonify(service.delete(item_id).to_dict())
