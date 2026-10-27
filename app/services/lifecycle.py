"""Shared 3-state lifecycle for the gate objects (Constitution, INPUT,
DESIGN). One vocabulary, learned once:

    draft  ⇄  needs_clarification        →  confirmed
    (editable, being worked on)             (locked, feeds next stage)

- ``draft`` / ``needs_clarification`` are EDITABLE. ``needs_clarification`` is a
  draft blocked on a human answer — kept mainly for multi-person collaboration
  (a colleague can flag "needs clarifying" and hand it off).
- ``confirmed`` is LOCKED and stays locked. Editing it is only possible after
  :func:`reopen` (→ draft).

There is no ``needs_review`` (see docs/cn/design-evolution.md#changes). It used to mean "confirmed, but
something upstream moved" — a fact about an EDGE between two objects, stored as
a state ON the downstream object. That conflation cost us: propagation had to
decide how far to cascade, "still fine" needed its own action to undo a flag
nobody asked for, and an object's state stopped describing the object. The same
fact is now derived on read from version snapshots (suspect links), so the
object's status only ever says how far its own content got.

"不做了" (dropping something) is deletion — soft-delete / recycle bin, an
orthogonal axis — NOT a lifecycle state. There is no ``cancelled``.

The user-facing actions map to transitions like this (each service wires its own
side effects — confirm field checks etc.):

    create        → draft
    PATCH status  : draft ⇄ needs_clarification         (the only PATCH-able moves)
    confirm()     : {draft, needs_clarification} → confirmed
    reopen()      : confirmed → draft
"""
from app.core.errors import ConflictError, ValidationError

DRAFT = "draft"
NEEDS_CLARIFICATION = "needs_clarification"
CONFIRMED = "confirmed"

LIFECYCLE_STATUSES = (DRAFT, NEEDS_CLARIFICATION, CONFIRMED)

EDITABLE_STATES = frozenset({DRAFT, NEEDS_CLARIFICATION})
LOCKED_STATES = frozenset({CONFIRMED})

# The ONLY status moves allowed through a plain PATCH. confirm / reopen are
# dedicated service actions, not PATCH transitions.
PATCH_TRANSITIONS = {
    DRAFT: {NEEDS_CLARIFICATION},
    NEEDS_CLARIFICATION: {DRAFT},
    CONFIRMED: set(),
}


def is_editable(status):
    return status in EDITABLE_STATES


def is_locked(status):
    return status in LOCKED_STATES


def ensure_editable(status, label):
    """Guard content edits: locked objects must be reopened first."""
    if status not in EDITABLE_STATES:
        raise ConflictError(
            f"cannot edit {label} in state '{status}'; reopen it first"
        )


def validate_patch_transition(current, target, label):
    """Validate a plain status PATCH (only draft ⇄ needs_clarification)."""
    if target not in LIFECYCLE_STATUSES:
        raise ValidationError(f"invalid status: {target}")
    if target not in PATCH_TRANSITIONS.get(current, set()):
        raise ConflictError(
            f"status '{target}' cannot be set via PATCH; "
            "use confirm / reopen"
        )
