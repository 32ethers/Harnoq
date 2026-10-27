"""Skill contract layer.

This is the single controlled entry point through which the design-time AI
(via OpenWebUI skills) interacts with Harnoq. It provides:

* :func:`build_envelope` — the fixed 6-key response shape every skill returns.
* :func:`run_write_skill` — write executor: always re-runs the write action.
* :func:`run_read_skill` — read-only executor.

Neither writes nor reads deduplicate by idempotency key. This used to be
"true idempotency" keyed on ``(project_id, actor, skill_name,
idempotency_key)``, with a payload-hash key auto-synthesized for callers
(OpenWebUI, MCP) that don't supply one. That auto-key could not tell "the
same logical operation retried" from "the same shape of operation done again
on purpose" — and every write payload that carries only a business number
(a DESIGN-* create is just ``{feature_number}``; confirm/reopen/delete/
restore are just ``{design_number}``) hashes identically every time it is
called on that object. In production this silently ate three separate
writes: a 2nd DESIGN-* create under one FEATURE replayed the 1st instead of
minting a new one; a re-confirm after a reopen replayed a stale success
without running ``confirm()``, leaving the object in ``draft`` though the API
said ``succeeded``; a delete after a restore replayed the first delete and
left the row live. Trying to patch the heuristic action-by-action (which
actions "legitimately recur") fixed each incident but not the class — the
lookup key was never a real request identity in the first place, since none
of these callers can supply one.

So this layer no longer attempts write dedup at all. ``write_action`` always
runs. Domain services are the correctness boundary against anything that
must not happen twice (e.g. ``ConflictError`` on confirming an already-
confirmed design) — a duplicate call fails loud there instead of being
silently absorbed here. ``actor`` / ``session_id`` / ``idempotency_key`` are
optional and recorded on SkillInvocation purely for the audit trail ("who
did what, when"), never for rejection or dedup.

The actual write/read work is delegated to a callback so the contract layer
stays generic; design-time write skills supply the callback that calls
the existing domain services.
"""
import json

from app.core.errors import ApiError, NotFoundError
from app.repositories import project_repo, skill_invocation_repo

# Truncate stored summaries so a single row never grows unbounded.
_SUMMARY_MAX = 2000
_ACTOR_FALLBACK = "openwebui"
_READ_SESSION_FALLBACK = "openwebui-read"
_WRITE_SESSION_FALLBACK = "openwebui-write"


def _truncate(value):
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text[:_SUMMARY_MAX]


def _request_summary(action, payload):
    """Serialize a request into a compact, truncated string for tracing."""
    try:
        rendered = json.dumps({"action": action, "payload": payload}, ensure_ascii=False)
    except (TypeError, ValueError):
        rendered = str({"action": action, "payload": payload})
    return _truncate(rendered)


def build_envelope(object_type, object_id, status, change_summary,
                   open_questions=None, next_step=None):
    """Return the fixed 6-key skill response envelope."""
    return {
        "object_type": object_type,
        "object_id": object_id,
        "status": status,
        "change_summary": change_summary,
        "open_questions": list(open_questions) if open_questions else [],
        "next_step": next_step,
    }


def _ensure_project(project_id):
    if project_repo.get(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")


def run_write_skill(*, project_id, skill_name, actor, session_id, idempotency_key,
                    action, payload, write_action):
    """Execute a write skill. Always calls ``write_action`` — no dedup, no
    identity requirement (see module docstring for why).

    ``write_action`` is a callable taking no args and returning a 5-tuple
    ``(target_type, target_id, change_summary, open_questions, next_step)``.

    On success, records a succeeded SkillInvocation and returns the envelope.
    On ``ApiError``, records a failed SkillInvocation (for tracing) and
    re-raises so the API layer maps the status code.
    """
    _ensure_project(project_id)
    from app.services.auth_service import current_user
    user = current_user()
    actor = user.user_number if user else actor or _ACTOR_FALLBACK
    session_id = session_id or _WRITE_SESSION_FALLBACK
    idempotency_key = idempotency_key or ""

    try:
        target_type, target_id, change_summary, open_questions, next_step = write_action()
    except ApiError as error:
        skill_invocation_repo.create(
            project=project_id,
            skill_name=skill_name,
            actor=actor,
            session_id=session_id,
            idempotency_key=idempotency_key,
            status="failed",
            request_summary=_request_summary(action, payload),
            response_summary=_truncate(error.message),
        )
        raise

    envelope = build_envelope(
        object_type=target_type,
        object_id=target_id,
        status="succeeded",
        change_summary=change_summary,
        open_questions=open_questions,
        next_step=next_step,
    )
    skill_invocation_repo.create(
        project=project_id,
        skill_name=skill_name,
        actor=actor,
        session_id=session_id,
        idempotency_key=idempotency_key,
        target_type=target_type,
        target_id=target_id,
        request_summary=_request_summary(action, payload),
        response_summary=_truncate(_envelope_summary(envelope)),
        status="succeeded",
    )
    return envelope


def run_read_skill(*, project_id, skill_name, actor, session_id, action, payload,
                   read_action):
    """Execute a read-only skill.

    ``actor`` / ``session_id`` are used for tracing; when a tool caller omits
    them (common in generic OpenAPI integrations), conservative fallback
    values are recorded so the read still succeeds.

    ``read_action`` is a callable taking no args and returning an envelope dict
    (built via :func:`build_envelope`).
    """
    _ensure_project(project_id)
    from app.services.auth_service import current_user
    user = current_user()
    actor = user.user_number if user else actor or _ACTOR_FALLBACK
    session_id = session_id or _READ_SESSION_FALLBACK

    envelope = read_action()
    skill_invocation_repo.create(
        project=project_id,
        skill_name=skill_name,
        actor=actor,
        session_id=session_id,
        idempotency_key="",
        target_type=envelope.get("object_type") or "",
        target_id=envelope.get("object_id"),
        request_summary=_request_summary(action, payload),
        response_summary=_truncate(_envelope_summary(envelope)),
        status="succeeded",
    )
    return envelope


def _envelope_summary(envelope):
    """Compact string snapshot of an envelope, stored for tracing only (not
    replayed — see module docstring)."""
    return json.dumps(
        {
            "object_type": envelope.get("object_type"),
            "object_id": envelope.get("object_id"),
            "status": envelope.get("status"),
            "change_summary": envelope.get("change_summary"),
            "open_questions": envelope.get("open_questions") or [],
            "next_step": envelope.get("next_step"),
        },
        ensure_ascii=False,
    )
