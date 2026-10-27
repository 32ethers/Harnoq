"""Harnoq skill endpoints exposed as MCP tools.

Served over streamable-http, normally mounted at ``/mcp`` inside the same
process as the Flask web app (see ``main.py``, which imports ``mcp`` from
this module and mounts ``mcp.streamable_http_app()``). The ``mcp`` object
below is still host/port-configured via HARNOQ_MCP_HOST/HARNOQ_MCP_PORT for
the optional standalone mode (``harnoq_mcp/__main__.py``), which runs it as
its own process listening on its own port.

Each tool builds a *flat* JSON body and POSTs it to one Harnoq skill endpoint.
The explicit-write endpoints pack every non-reserved key into ``payload`` and
fill conservative actor/session defaults themselves, so the tools stay thin:
identifiers + content fields, no envelope plumbing. Writes are not
idempotent (every call re-runs; see ``skill_service.run_write_skill``), so
there is no idempotency_key to synthesize.

Transport to Harnoq uses stdlib ``urllib`` to match the rest of the codebase
(``app/integrations/github_client.py``) and avoid a runtime ``requests``/``httpx``
dependency in the wrapper. ``post_skill`` is the single network boundary; tests
monkeypatch it to route through the Flask test client in-process.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

MD_CLAUSE = "Markdown — use real line breaks, never the literal characters backslash-n."

HARNOQ_BASE_URL = os.environ.get("HARNOQ_BASE_URL", "http://127.0.0.1:5000")
DEFAULT_ACTOR = os.environ.get("HARNOQ_ACTOR", "claude-code")
DEFAULT_SESSION_ID = os.environ.get("HARNOQ_SESSION_ID", "claude-code-mcp")
_TIMEOUT = float(os.environ.get("HARNOQ_TIMEOUT", "30"))

# Server-level orientation surfaced to the MCP client (Codex/ChatGPT), mirroring
# skills/openwebui-system-prompt.md so BOTH channels drive the model the same way.
# Deliberately thin: it points at active_stage_method and demands it be followed
# strictly — it does NOT restate its content, so the single source of truth stays
# app/api/skills.py::_stage_methods() and the channels cannot diverge. The
# The concise checklist below names only required outcomes; the complete method
# remains in ``active_stage_method``.
HARNOQ_INSTRUCTIONS = (
    "Harnoq is a five-stage pipeline, in this order: INPUT-* (raw requirement, "
    "mined by conversation) -> project constitution -> FEATURE-* (the "
    "capability map) -> DESIGN-* (one module each) -> "
    "TASK-*. One stage gets skipped by agents working from general experience, "
    "so name it: the constitution sits between confirmed INPUT-* and any "
    "feature work. You never go from a "
    "FEATURE-* straight to a DESIGN-* without first engaging its INPUT-* "
    "material. Whenever you report progress, say which "
    "stage you are in, what is still missing to close it, and which stage "
    "comes next by name — do not substitute a plausible-sounding next step "
    "from general software practice for the one this pipeline specifies. "
    "ALWAYS call "
    "harnoq_project_context FIRST and read its active_stage_method (hoisted to the "
    "TOP of the context) BEFORE mining requirements, designing, or planning anything — "
    "that is the BINDING method for your current stage; you cannot design a project you "
    "have not read, and you must NOT work from your own generic DDD/architecture "
    "knowledge. For design/planning, begin with the context's FEATURE map, not an "
    "isolated INPUT-*; a Feature is the planning unit and a DESIGN-* is its "
    "module. The design stage ALSO mines the requirement — there is no layer "
    "between a Feature and its designs — so mine it by drafting something "
    "concrete the user can strike rather than by asking them what they want, "
    "and present the result in two columns ('what I understand you want' / "
    "'what I intend to build') so approving the solution does not silently "
    "approve every requirement assumption inside it. "
    "Follow active_stage_method's rounds in order. A Feature map divides ONE "
    "project into a handful of ownable blocks named after what they do — it is "
    "not an architecture diagram, and a map that is mostly platforms, cores and "
    "layers was cut by layer. Check it by taking a few things the user actually "
    "asked for and asking which SINGLE Feature would deliver each. The blocks and "
    "the order they get built in are settled in SEPARATE rounds, in that order. "
    "'A depends on B' means only one thing — B must be FINISHED before work on A "
    "can start — never 'A uses B'; record it per block, naming what of A is stuck "
    "until B is done. It only ever holds work back, so one wait too many is "
    "harmless while one MISSING lets work start against something that does not "
    "exist yet: hunt for the missing ones, do not prune for tidiness. Every "
    "Feature "
    "carries orientation + subdomain_type tags; there is an Architecture "
    "Foundation (foundation_kind=architecture), and a project with a user "
    "interface has a UI Foundation (foundation_kind=ui). "
    "Stage gates are strict: only start a stage when its upstream is confirmed. "
    "Every live INPUT-* must be confirmed before Project Constitution consolidation; "
    "the constitution must be confirmed before any feature work; a DESIGN-* must "
    "be confirmed before its TASK-*. Never bulk-confirm items merely "
    "to advance the workflow. Confirming a design requires its required fields non-empty or the server "
    "rejects the call. Confirming is a separate approval from the user picking a "
    "design direction: fill the fields first, present them back, and confirm only after "
    "a fresh approval of the written result — never fill-and-confirm in one turn. "
    "All prose/content fields in Harnoq are Markdown — write real line breaks, "
    "never the literal characters backslash-n. Short fields (titles, enums, "
    "numbers) are plain text. "
    "REJECTED TASKS: when pre-review sends a task back for revision, the task "
    "parks in `needs_revision` and the recovery path is entirely tool-driven "
    "— there is NO UI button for it, so never tell the user to 'regenerate' or "
    "'re-submit' the task in the interface, and never expect a new TASK-* number. "
    "For a design-backed task, revise and re-confirm the linked DESIGN-*; "
    "for a CR source task, read current source obligations "
    "and edit its TASK contract through `harnoq_task_update`, then read it back. "
    "In either case, call `reopen` on the SAME TASK-*. "
    "DELIVERY REPAIR: an ordinary defect found by an INTEG-* or SYS-* scenario "
    "stays in the current project-wide stage run. Record failure, change and exact "
    "current-revision executable evidence on the item; create no TASK, claim or dependency. "
    "Only a confirmed contract/design hole uses a CR. If a CR is needed, "
    "ordinary repairs restoring a confirmed contract within "
    "existing authorization may be analyzed and approved by the manager without "
    "another user confirmation; retain failure evidence and the authorization basis. "
    "Use change.approval_policy. Ask for new scope, product trade-offs, waivers, "
    "lower acceptance standards, significant extra cost or destructive actions "
    "only when not already authorized. Never invent a fresh user approval. "
    "DESIGN→TASK is strictly one-to-one. Delivery stages are separate project-wide runs. "
    "Reopening a completed task requires citing the missed design acceptance clause "
    "or, for a source task, its TASK acceptance/source obligation. "
    "Integration ends only when a real production entry starts, every declared "
    "capability part is reachable, every declared producer-to-consumer seam is "
    "wired and directly tested, convergence decisions are complete, and no open "
    "INTEG-* remains. Once the Integration inventory receipt is valid, create its "
    "bound items, start one stage run and record direct evidence and concise repairs. "
    "System owns black-box journeys and NFRs; choose ordinary drivers. Pause affected "
    "verification for required user resources/access and ask for exact format/location; never substitute data. Pause only for product or "
    "requirement trade-offs, waivers, significant extra cost/resources, destructive "
    "actions, or irreducible ambiguity. Known Integration gaps are not deferred to "
    "System. "
    "TERMINOLOGY: in every requirement/design/task document, use standard, "
    "widely-understood vocabulary and name each thing by what it actually is. "
    "Prefer the plain conventional word over an inflated or invented one, and "
    "preserve the user's original meaning rather than reinterpreting it. Three "
    "specific failures to avoid. (1) FIT THE DOMAIN: use the term this project's "
    "own domain uses; never import jargon from an unrelated field (do not call a "
    "plain collection a 'bucket', which belongs to object storage/hashing). A word "
    "can mean different things per domain — 'key' in a database is not 'key' in "
    "cryptography — so when a term is ambiguous across the project's domains, say "
    "which sense you mean or pick an unambiguous word. (2) NO CONCEPT DRIFT: pick a "
    "precise term and keep it stable; never let it slide into a related-but-different "
    "concept (a support request may reasonably be a 'case', but must not become a 'legal case'). "
    "(3) NO CUMULATIVE DRIFT ACROSS ROUNDS: each rename can look reasonable on its "
    "own while the chain ends far from the source (support request -> case -> legal case "
    "-> lawsuit). Always check a term against the user's "
    "ORIGINAL wording, not against the previous step. When you find drift, name it "
    "and ask the user which term is correct — do not silently re-translate."
    " These rules are executed through project_context.glossary: copy listed "
    "term_native/term_en exactly; for a new concept add a glossary entry and "
    "confirm it with the user before using it. If a word matches an avoid entry, "
    "call it out and ask which term is correct rather than silently translating it."
    " The glossary exists to stop a WRONG WORD from being written, and is not the "
    "vocabulary of the domain. A term earns a row only if you can write out the sentence "
    "somebody would actually produce with the wrong word in it — typically a generic "
    "catch-all this project overloads to mean two things — and say what that breaks. "
    "That wrong word goes in avoid. Two concepts that merely sit next to each other, "
    "each already carrying its own distinct name, are adjacency rather than confusion "
    "and earn no rows; their relation is a constraint for the constitution or the "
    "design. Being coined here, being the user's own word or appearing in the source "
    "material qualifies nothing. Every row is read at every stage, so padding costs the "
    "rows that matter."
)

mcp = FastMCP(
    "harnoq",
    instructions=HARNOQ_INSTRUCTIONS,
    host=os.environ.get("HARNOQ_MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("HARNOQ_MCP_PORT", "5001")),
)


class HarnoqError(Exception):
    """A Harnoq skill call failed (HTTP error, unreachable, or bad envelope)."""


# Co-hosted mode: when main.py serves web + MCP in one process/loop, a tool
# calling Harnoq over TCP back to itself deadlocks (the blocking urllib call
# holds the event loop, so uvicorn can't accept the self-connection). In that
# mode main.py registers the Flask WSGI app here and calls dispatch in-process
# instead — synchronous, no socket, no loop involvement. Standalone runs leave
# this None and use urllib over HARNOQ_BASE_URL.
_wsgi_app = None


def use_in_process_wsgi(flask_app) -> None:
    """Route tool self-calls straight into ``flask_app`` in-process (see above)."""
    global _wsgi_app
    _wsgi_app = flask_app


def _wsgi_call(method: str, path: str, json_body: dict | None = None) -> dict:
    response = _wsgi_app.test_client().open(path, method=method, json=json_body, headers=_credential_headers())
    if response.status_code >= 400:
        detail = response.get_json(silent=True)
        message = (
            detail.get("error") if isinstance(detail, dict)
            else response.get_data(as_text=True)
        )
        raise HarnoqError(f"Harnoq {path} -> HTTP {response.status_code}: {message}")
    return response.get_json()


def _credential_headers():
    # HTTP calls carry their own principal; never substitute a server-wide token.
    try:
        incoming = mcp.get_context().request_context.request
    except ValueError:
        incoming = None
    if incoming is not None:
        authorization = incoming.headers.get("Authorization", "")
    else:
        token = os.environ.get("HARNOQ_API_TOKEN", "")
        authorization = f"Bearer {token}" if token else ""
    return {"Authorization": authorization} if authorization else {}


def post_skill(path: str, payload: dict) -> dict:
    """POST a flat JSON body to a Harnoq skill endpoint, return its envelope.

    Drops ``None`` values so optional tool arguments never reach Harnoq as
    explicit nulls. Raises ``HarnoqError`` with the server's message on HTTP
    4xx/5xx, or a "start Harnoq" hint when the host is unreachable.
    """
    body = {key: value for key, value in payload.items() if value is not None or key == "assignee_number"}
    if _wsgi_app is not None:
        return _wsgi_call("POST", path, body)
    url = HARNOQ_BASE_URL.rstrip("/") + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", **_credential_headers()},
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail).get("error", detail)
        except (ValueError, AttributeError):
            pass
        raise HarnoqError(f"Harnoq {path} -> HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise HarnoqError(
            f"cannot reach Harnoq at {url}: {exc.reason}. "
            "Start it with `uv run python main.py`."
        ) from None


def get_harnoq(path: str) -> dict:
    """GET a Harnoq REST endpoint and return its JSON body."""
    if _wsgi_app is not None:
        return _wsgi_call("GET", path)
    url = HARNOQ_BASE_URL.rstrip("/") + path
    headers = _credential_headers()
    outbound = urllib.request.Request(url, headers=headers) if headers else url
    try:
        with urllib.request.urlopen(outbound, timeout=_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail).get("error", detail)
        except (ValueError, AttributeError):
            pass
        raise HarnoqError(f"Harnoq {path} -> HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise HarnoqError(
            f"cannot reach Harnoq at {url}: {exc.reason}. "
            "Start it with `uv run python main.py`."
        ) from None


@mcp.tool()
def harnoq_user_list():
    """List registered people by USER-* number for task assignment."""
    return get_harnoq("/users")


@mcp.tool()
def harnoq_task_assign(project_number: str, task_number: str, assignee_number: str | None):
    """Assign or clear a task owner (administrator credentials required).

    Assignment does not claim the task or bypass its dependency/review gates.
    """
    return post_skill(f"/projects/{project_number}/tasks/{task_number}/assignee", {"assignee_number": assignee_number})


def _call(path: str, project_number: str, fields: dict | None = None,
          extra_fields: dict | None = None) -> dict:
    """Assemble {project_number, actor, session_id, **fields, **extra_fields}.

    ``actor``/``session_id`` are reserved keys (not packed into payload); every
    other key becomes a payload field on the explicit-write endpoints.
    """
    body: dict[str, Any] = {
        "project_number": project_number,
        "actor": DEFAULT_ACTOR,
        "session_id": DEFAULT_SESSION_ID,
    }
    if fields:
        body.update(fields)
    if extra_fields:
        body.update(extra_fields)
    return post_skill(path, body)


def _read_call(path: str, project_number: str, action: str,
               payload: dict | None = None) -> dict:
    """Call a generic read skill action with an explicit tool wrapper."""
    return post_skill(path, {
        "project_number": project_number,
        "actor": DEFAULT_ACTOR,
        "session_id": DEFAULT_SESSION_ID,
        "action": action,
        "payload": payload or {},
    })


# --- read tools ------------------------------------------------------------

@mcp.tool()
def harnoq_project_context(project_number: str) -> dict:
    """Read a project's full context. Call this FIRST before decomposing
    requirements, designing, or planning anything.

    Returns source requirements (INPUT-*), features
    (FEATURE-*), design cases (DESIGN-* with TASK-* created directly under
    them) and development tasks, plus
    business_id_rules, workflow_guidance and — hoisted to the TOP — current_stage
    and active_stage_method, the BINDING working method for your current stage.
    Read active_stage_method IN FULL and follow its rounds STRICTLY and in order
    before you define a Feature map or create/confirm anything — do not work from
    your own generic DDD/architecture knowledge, and do not skip its steps
    (including the design stage's Feature-to-design coverage rule).
    active_stage_method is the ONLY method sent — the other stages' methods are
    not included, so work from what is in front of you rather than a remembered
    version of another stage's method.
    """
    return post_skill("/skills/project-context", {
        "project_number": project_number,
        "actor": DEFAULT_ACTOR, "session_id": DEFAULT_SESSION_ID,
    })


@mcp.tool()
def harnoq_task_progress(project_number: str) -> dict:
    """Read development-task progress, dependencies and quality-gate status for
    a project (P*). Use for execution/test/CI/review/completion state."""
    return post_skill("/skills/task-progress", {
        "project_number": project_number,
        "actor": DEFAULT_ACTOR, "session_id": DEFAULT_SESSION_ID,
    })


# --- downstream development task execution ---------------------------------

def _task_base(project_number: str, task_number: str) -> str:
    encoded_project = urllib.parse.quote(project_number, safe="")
    encoded_task = urllib.parse.quote(task_number, safe="")
    return f"/projects/{encoded_project}/tasks/{encoded_task}"


@mcp.tool()
def harnoq_task_update(
    project_number: str,
    task_number: str,
    title: str | None = None,
    goal: Annotated[str | None, Field(description=f"Task goal. {MD_CLAUSE}")] = None,
    acceptance_criteria: Annotated[str | None, Field(description=f"Binding verification criteria. {MD_CLAUSE}")] = None,
    constraints: Annotated[str | None, Field(description=MD_CLAUSE)] = None,
    non_goals: Annotated[str | None, Field(description=MD_CLAUSE)] = None,
    task_type: str | None = None,
    priority: str | None = None,
    complexity: str | None = None,
    risk_level: str | None = None,
) -> dict:
    """Edit the existing TASK contract by business number (manager-only).

    Allowed while claimable, or for a source-backed TASK in needs_revision.
    Omit unchanged fields; supply at least one field. Does not change status,
    owning Feature, source associations, dependencies, or execution evidence.
    For a source-backed needs_revision task: read task_get and current sources,
    update only the authorized contract changes (retain unwaived obligations),
    read back, then harnoq_task action='reopen' on the SAME task. A design-backed
    rejected task must instead revise/reconfirm its DESIGN before reopening.
    This tool cannot edit a claimed/in-progress/completed task in place, bypass
    QA, approve a waiver, or turn an unverified requirement into a passing result.
    """
    fields = {
        "title": title, "goal": goal, "acceptance_criteria": acceptance_criteria,
        "constraints": constraints, "non_goals": non_goals, "task_type": task_type,
        "priority": priority, "complexity": complexity, "risk_level": risk_level,
    }
    return post_skill(_task_base(project_number, task_number) + "/update", {
        key: value for key, value in fields.items() if value is not None
    })


@mcp.tool()
def harnoq_task_get(project_number: str, task_number: str) -> dict:
    """Read one TASK-* by P* / TASK-* (read-only).

    Returns the task's acceptance criteria, constraints, non-goals,
    ``design_number``, AND a ``design`` object holding the linked DESIGN-*'s
    full text (mined_requirements, verifies_nfr, design_scope, selected_solution,
    constraints, non_goals,
    acceptance_criteria, test_strategy, risks). A 1:1 task's own
    acceptance/constraints are deliberately POINTERS at that design -- the
    binding spec is ``design``, so read it and satisfy its acceptance_criteria
    one by one; the task's own fields are not the spec.

    It ALSO returns ``project_constitution`` (fixed stack, quality bar, UI
    conventions, NFR baseline, boundaries) -- no second call to a constitution
    tool is needed to get the project's governing norms. It is ``null`` when
    the project's constitution is not confirmed yet or does not exist, since
    an unconfirmed constitution is not a binding decision. The constitution is
    a project-level hard boundary that applies ALONGSIDE the design's
    acceptance criteria, not underneath them; when the two disagree, the
    constitution wins.

    ``cleared_failures`` answers, for every criterion qa once judged broken,
    how it stopped being broken -- but only when the answer is one of the two
    that leave the project no better off. `how="no_new_test"`: it was judged
    fail/deviates on a GREEN suite, so no test covered it, and judged pass later
    without the collected count going up, so none was added; it holds on one qa
    agent's reading and nothing will catch it regressing. `how="criterion_removed"`:
    it is not in the design any more -- a later run passed the task without
    judging it, and deleting a standard clears a failure just as thoroughly as
    meeting it. An empty list means every earlier failure was answered.
    It blocks nothing. Weigh it when deciding whether a delivery is believable,
    and report those criteria by name: an all-pass verdict otherwise reads as
    though the suite is now watching, or as though the question was answered.

    ``uncovered_criteria`` answers a related but different question: from the
    task's MOST RECENT qa verdict, which criteria have no test watching them
    right now. It only reads criteria whose evidence used the structured
    ``{tests, code, note}`` shape (see ``record_agent_run`` below) with an
    empty ``tests`` list -- that is qa stating in a queryable form that
    nothing automated covers it, which is a real signal worth weighing, not a
    default to explain away. A criterion whose evidence is a plain string
    (the older, still-accepted free-text shape) is left OUT of this list
    rather than counted as uncovered: prose cannot be split back into a
    tests/code answer without inventing data nobody wrote, so "cannot tell"
    is reported as absence from the list, never folded into "uncovered".

    ``prior_evidence`` is a working aid for a qa run about to re-verify: for
    each criterion of this design that an earlier round already located in the
    structured shape, the ``tests`` and ``code`` positions it found, plus which
    task found them. It spans every task against this design, not only this
    one, because the round that last looked at a criterion is very often a
    different task. It carries NO earlier verdict and no prose note, on
    purpose: relocation is what it is for, and "it passed last time" is the
    most anchoring thing that could be handed to a fresh verdict. A criterion
    absent from it was never located in the structured form or has been
    reworded since — either way there is nothing to reuse.

    ``feature`` identifies the owning FEATURE-* and its ``foundation_kind``.
    Change-request tasks have no DESIGN requirement and list their approved
    ``change_requests`` as direct sources. Integration and System obligations
    belong to their delivery-stage runs rather than to TASK-* records.

    ``pull_requests`` lists every PR the task has had, oldest first --
    a reopened task keeps its earlier attempts, so more than one entry (or an
    already-merged one) means this is a RETRY, not a first delivery; say so
    when reporting. Every role may use this tool; manager exclusively owns
    write actions through ``harnoq_task``.
    """
    return get_harnoq(_task_base(project_number, task_number))


@mcp.tool()
def harnoq_integration_item_get(project_number: str, item_number: str) -> dict:
    """Read one INTEG-* obligation by business number for a delivery-stage run."""
    return get_harnoq(f"/projects/{project_number}/integration-items/{item_number}")


# The manager's whole write surface on a task, as action -> agent-route suffix.
# Module-level so tests can assert it stays in parity with the routes actually
# served by app/api/agent_tasks.py: an action missing here is invisible to the
# manager no matter how well the service layer implements it, which strands the
# task in whatever state that action was supposed to leave.
_TASK_ACTION_PATHS = {
    "claim": "/claim",
    "release": "/release",
    "reopen": "/reopen",
    "start_pre_review": "/start-pre-review",
    "approve_pre_review": "/approve-pre-review",
    "reject_pre_review": "/reject-pre-review",
    "start_coding": "/start-coding",
    "reject_to_design": "/reject-to-design",
    "record_agent_run": "/agent-runs",
    "mark_ready_for_pr": "/mark-ready-for-pr",
    "withdraw_from_pr": "/withdraw-from-pr",
    "link_pr": "/link-pr",
    "mark_pr_merged": "/mark-pr-merged",
    "mark_completed": "/mark-completed",
    "reopen_completed": "/reopen-completed",
    "realign_basis": "/realign-basis",
    "add_dependencies": "/dependencies",
    "remove_dependencies": "/dependencies/remove",
}


@mcp.tool()
def harnoq_task(
    action: str,
    project_number: str,
    task_number: str,
    assignee: Annotated[str | None, Field(description="Legacy compatibility only. Omit: JWT determines the user on claim; administrator assignment uses harnoq_task_assign with USER-*.")] = None,
    role: str | None = None,
    status: str | None = None,
    summary: str | None = None,
    design_number: str | None = None,
    criteria_verdicts: list[dict] | None = None,
    test_run: dict | None = None,
    branch_name: str | None = None,
    pr_number: int | None = None,
    pr_url: str | None = None,
    reviewer: Annotated[str | None, Field(description="Legacy compatibility only. Omit: the authenticated JWT user is recorded as the reviewer.")] = None,
    comment: str | None = None,
    reason: str | None = None,
    depends_on_task_numbers: list[str] | None = None,
) -> dict:
    """Manager-only write actions on one TASK-* (by P* / TASK-*).

    The client's Bearer JWT identifies the human operator. Claim records that
    user's account as owner unless an administrator acts for an already assigned
    owner. Omit assignee/reviewer text; the server derives identity and audit
    attribution from the credential. Assignment to another USER-* uses
    harnoq_task_assign and requires administrator access.

    Actions: `claim`, `release`, `reopen`, `start_pre_review`, `approve_pre_review`,
    `reject_pre_review`, `start_coding`, `reject_to_design`, `record_agent_run`,
    `mark_ready_for_pr`, `withdraw_from_pr`, `link_pr`, `mark_pr_merged`,
    `mark_completed`, `reopen_completed`, `realign_basis`, and
    `add_dependencies` and `remove_dependencies`.
    Call ``start_coding`` ONCE (moves
    `ready_for_development` → `in_progress`) right before dispatching coder;
    coder and qa rounds inside the unit record runs without moving the task, so
    it is not a per-round action. Nothing advances the task
    automatically, so call ``mark_ready_for_pr`` yourself (it re-checks the
    quality gate: latest coder+qa runs passed, no blocking_issues) to reach
    `ready_for_pr` before ``link_pr``.

    `record_agent_run` statuses. For coder: `passed` / `failed`. For qa:
    `passed` / `needs_fix`, plus `blocked` for either role. Report `blocked`
    — never `needs_fix` — when the subagent could not run at all: no Harnoq
    tools in its session, the design unreachable, the test suite unrunnable.
    `blocked` moves the task nowhere (there is nothing for coder to fix) and
    keeps the quality gate shut; resolve the cause and dispatch that role
    again. It requires `summary` naming what was unreachable.

    A qa `passed`/`needs_fix` must carry `criteria_verdicts` and `test_run`.
    A design-backed task also carries its linked `design_number` and judges
    that DESIGN's acceptance criteria. A source-backed task has no DESIGN and
    omits `design_number`; it judges the TASK's own acceptance criteria and
    direct source obligations instead. Each verdict is one entry per
    `{criterion, verdict: "pass"|"fail"|"deviates", evidence}`, quoting the
    criterion as written. Every criterion needs a verdict; `passed` may
    contain no `fail` and no `deviates` entry, `needs_fix` must contain at
    least one of either. `deviates` means the behaviour is present but does
    not match what the design states — a different return shape, error
    channel, or condition — and it is refused under `passed` for the same
    reason `fail` is: a divergence that can ride along inside a passing
    verdict is not recorded as a divergence at all, even when the divergent
    path looks unreachable in the current environment.

    `evidence` accepts either a plain string (kept verbatim) or a structured
    `{tests: [...], code: [...], note: "..."}` object. `tests` names every
    test that covers the criterion, `code` names where in the code it is
    satisfied; either may legitimately be an empty list — an empty `tests` is
    itself the useful answer ("nothing automated watches this"), not
    something to hide by padding it with an unrelated test name. When both
    lists are empty, `note` must say what was actually checked. Prefer the
    structured shape when reporting: it is what lets the NEXT qa run on this
    same task query "what already proves this" instead of re-deriving it from
    prose, and what lets a reader ask "which criteria have zero test
    coverage" as a fact rather than a re-read of every verdict's evidence
    text.

    `test_run` is `{command, exit_code, collected, passed, failed, skipped}`:
    the exact command the qa subagent ran plus the five counts read off the
    suite's own final summary line — `collected` and `passed` are different
    questions, never the same number reported twice, and `passed + failed +
    skipped` may not exceed `collected`. `status="passed"` is refused when
    `failed` is nonzero or `exit_code` is nonzero; report `needs_fix` instead
    in that case. A nonzero `skipped` is never refused — it is recorded so a
    reader can see which evidence the run did not produce. `blocked` requires
    none of this — a run that never reached a verdict has no test-suite
    result to report.

    Pass through what the qa subagent actually reported — a manager filling
    these in from the subagent's prose is fabricating the evidence the gate
    exists to demand, and if qa did not return them, that is a `blocked` run.
    For a design-backed TASK, call ``approve_pre_review`` only after the human
    has explicitly approved the task in conversation. For a source-backed TASK
    created by an approved CR, the manager performs the
    technical contract/source check and may approve it without another user
    round-trip; never ask for a blind technical stamp and never self-approve a
    design-backed task.
    ``start_pre_review`` is an OPTIONAL marker showing a review is under way:
    both `approve_pre_review` and `reject_pre_review` are accepted straight
    from `claimed`, so a task without the marker never needs one added before
    its verdict can be recorded.
    ``release`` drops the claim and returns the task to `claimable` for anyone
    to pick up: use it when the task should be handed back before coding
    starts — the wrong holder, or a claim made by mistake. Allowed from
    `claimed`, `pre_reviewing` and `ready_for_development`; from the last of
    those it DISCARDS the pre-review approval, so the next holder must
    pre-review again. It is NOT the way to drop a task the review found wrong
    — that is `reject_pre_review`, which records why. Once coding has started
    it is refused: there is work to hand over by then, and calling the task
    unclaimed while a branch is open is false. Call
    ``reopen`` after a task enters `needs_revision`: a design-backed task needs
    its linked DESIGN-* re-confirmed, while a source-backed task needs its TASK
    contract/source obligation corrected via `harnoq_task_update` and read back
    with `harnoq_task_get`. It resets the task to `claimable`.
    Reading
    task details is a separate read-only tool, `harnoq_task_get`, which
    subagents use; this tool must not be granted to subagents. Harnoq never
    calls a git-hosting-provider API itself: the manager opens branches/PRs
    via `gh`/`git` CLI, then reports the resulting `branch_name`/`pr_number`/
    `pr_url` through `link_pr`. After a human confirms on the git host that
    the PR merged, call `mark_pr_merged` (no PR-detail params — it just
    records that manual confirmation) before `mark_completed`.

    `add_dependencies` and `remove_dependencies` accept a batch of TASK-* business numbers in
    `depends_on_task_numbers`; all numbers and cycle checks are validated in
    one transaction, so an invalid member leaves no partial edges.

    `reopen_completed` sends a COMPLETED task back for rework. Its `reason`
    cites the missed DESIGN acceptance clause for a design-backed task, or the
    missed TASK acceptance/source obligation for a source-backed task. New
    scope goes through an approved CR source-backed TASK; ordinary delivery
    findings stay on their INTEG-*/SYS-* stage items. Never
    create a second task under one DESIGN. Use it when work outside this task's own cycle —
    integration testing is the usual case — shows the merged task failed its
    binding contract. It returns the task to `claimable` for a
    fresh claim and pre-review, keeps the merged PR record (the retry gets a
    new one), and clears the quality gate. Its Feature stops counting as
    satisfied, which re-blocks not-yet-claimed downstream tasks; downstream
    work already underway is NOT retracted, so tell the human what is now
    standing on a withdrawn foundation instead of assuming the system undid
    it. This is not the path for a task whose contract needs revising while
    work is still open: before completion use `reject_pre_review` (from
    `claimed` or `pre_reviewing`) or `reject_to_design` (from `in_progress`),
    both of which lead to `needs_revision`. Then `reopen`: a design-backed
    task requires its DESIGN to be re-confirmed, while a source-backed task
    revises its TASK acceptance/source obligations without creating a DESIGN.
    `reopen_completed` is only for a defect that surfaces after the PR has
    already merged.

    `realign_basis` requires a `reason` when the task is already `completed`:
    there it asserts that MERGED code still satisfies a design that has since
    changed, nothing re-runs to check, and without a stated reason the record
    cannot tell a review from a click. If you cannot write the reason, the
    honest moves are `reopen_completed` or leaving the edge suspect.
    `realign_basis` answers a suspect edge: `harnoq_task_get` reports
    `suspect: true` when the linked DESIGN-* has been revised since this task
    was created, listing which version it was created against and which is
    current. Read that design change first. If the task as written still
    stands, `realign_basis` records that judgement and the flag clears; if it
    does not, the task needs revising or withdrawing instead. Never call it
    just to make the flag go away — it asserts that a human compared them.

    `withdraw_from_pr` is the same idea one step earlier: it pulls a
    `ready_for_pr` task back to `in_progress` when a
    problem surfaces after the quality gate closed but before anything merged.
    It requires a `reason` naming what must change, and a fresh qa run then has
    to judge every criterion again before the gate can reopen. Once the PR
    has MERGED this is refused — the code is in the default branch and the
    honest path is `mark_completed` then `reopen_completed`, which also
    withdraws the Feature's satisfied status for downstream tasks.

    `reject_to_design` is the compatibility endpoint for `reject_pre_review`'s
    counterpart once coding is already under way. For a design
    defect — neither `claimed` nor
    `pre_reviewing` is reachable by then, so before this action a task in
    that spot had
    nowhere to go. It moves `in_progress` straight to `needs_revision`.
    It is refused from `ready_for_pr`: the path from there is
    `withdraw_from_pr` first (which pulls the task back to `in_progress` and
    records its own reason), then this. It requires a `reason` naming the wrong
    design clause, or the wrong TASK acceptance/source obligation for a
    source-backed task. Call it only after the applicable review establishes that
    the task contract or direct source obligation is at fault; this is not a
    second human approval for source-backed work. The same `reopen` brings the
    task back to `claimable`.
    """
    base = _task_base(project_number, task_number)
    paths = _TASK_ACTION_PATHS
    if action not in paths:
        raise HarnoqError(
            "invalid harnoq_task action: "
            f"{action}; expected claim, release, reopen, start_pre_review, "
            "approve_pre_review, reject_pre_review, start_coding, "
            "reject_to_design, record_agent_run, mark_ready_for_pr, "
            "withdraw_from_pr, link_pr, mark_pr_merged, "
            "mark_completed, reopen_completed, realign_basis, add_dependencies, "
            "or remove_dependencies"
        )
    return post_skill(base + paths[action], {
        "assignee": assignee,
        "role": role,
        "status": status,
        "summary": summary,
        "design_number": design_number,
        "criteria_verdicts": criteria_verdicts,
        "test_run": test_run,
        "branch_name": branch_name,
        "pr_number": pr_number,
        "pr_url": pr_url,
        "reviewer": reviewer,
        "comment": comment,
        "reason": reason,
        "depends_on_task_numbers": depends_on_task_numbers,
    })


# --- source requirements (INPUT-*) -----------------------------------------

@mcp.tool()
def harnoq_source_requirement_assign(project_number: str, source_requirement_number: str, assignee_number: str | None) -> dict:
    """Assign an INPUT-* to a USER-* account, or clear with null. Administrator access required; content and confirmation remain unchanged."""
    return post_skill(f"/projects/{project_number}/source-requirements/{source_requirement_number}/assignee", {"assignee_number": assignee_number})


@mcp.tool()
def harnoq_delivery_item_assign(project_number: str, item_number: str, assignee_number: str | None) -> dict:
    """Assign an INTEG-*/SYS-* to a USER-* account, or clear with null. Administrator access required; status, evidence and inventory receipts remain unchanged."""
    return post_skill(f"/projects/{project_number}/delivery-items/{item_number}/assignee", {"assignee_number": assignee_number})

@mcp.tool()
def harnoq_source_requirement_list(project_number: str) -> dict:
    """List all live source requirements (INPUT-*) in a project."""
    return _read_call("/skills/source-requirement", project_number, "list")


@mcp.tool()
def harnoq_source_requirement_get(
    project_number: str, source_requirement_number: str,
) -> dict:
    """Get one source requirement (INPUT-*) by business number."""
    return _read_call("/skills/source-requirement", project_number, "get", {
        "source_requirement_number": source_requirement_number,
    })


@mcp.tool()
def harnoq_source_requirement_create(
    project_number: str,
    title: Annotated[str, Field(description="Short label for this input document. Plain text.")],
    source_type: Annotated[str | None, Field(
        description="Where this content came from (defaults to manual_note). Plain text enum.",
    )] = None,
    raw_content: Annotated[str | None, Field(
        description=f"Raw user-provided material, captured verbatim. {MD_CLAUSE}",
    )] = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict:
    """Create a new source requirement (INPUT-*) holding raw user material.
    ``source_type`` defaults to manual_note; an invalid value returns the legal
    enum in the error so you can retry. INPUT-* starts as draft — keep
    appending/editing it by conversation until the requirement is genuinely
    understood, then confirm it (harnoq_source_requirement_confirm); every
    INPUT-* in the project must be confirmed before the constitution stage can
    begin. Markdown fields (raw_content): use real line breaks, never the
    literal characters backslash-n."""
    return _call("/skills/source-requirement-create", project_number, {
        "title": title, "source_type": source_type, "raw_content": raw_content,
    }, extra_fields)


@mcp.tool()
def harnoq_source_requirement_append_content(
    project_number: str, source_requirement_number: str,
    raw_content_to_append: Annotated[str, Field(
        description=f"Text block to append to raw_content. {MD_CLAUSE}",
    )],
) -> dict:
    """Append a block of text to an existing INPUT-* raw_content. Prefer this
    over rewriting the whole document when the user just adds material.
    Markdown fields (raw_content_to_append): use real line breaks, never the
    literal characters backslash-n."""
    return _call("/skills/source-requirement-append-content", project_number, {
        "source_requirement_number": source_requirement_number,
        "raw_content_to_append": raw_content_to_append,
    })


@mcp.tool()
def harnoq_source_requirement_update(
    project_number: str, source_requirement_number: str,
    title: Annotated[str | None, Field(description="Short label for this input document. Plain text.")] = None,
    raw_content: Annotated[str | None, Field(
        description=f"Replacement full content. {MD_CLAUSE}",
    )] = None,
    source_type: Annotated[str | None, Field(
        description="Where this content came from. Plain text enum.",
    )] = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict:
    """Update fields of an existing INPUT-* (e.g. fix the title, or replace the
    whole raw_content). Refused (409) once confirmed — reopen it
    first. Markdown fields (raw_content): use real line breaks, never the
    literal characters backslash-n."""
    return _call("/skills/source-requirement-update", project_number, {
        "source_requirement_number": source_requirement_number,
        "title": title, "raw_content": raw_content, "source_type": source_type,
    }, extra_fields)


@mcp.tool()
def harnoq_source_requirement_confirm(project_number: str, source_requirement_number: str) -> dict:
    """Confirm a source requirement (INPUT-*): draft/needs_clarification ->
    confirmed. Bare status flip — no required-field checklist, raw_content
    stays free text. Confirm only once the requirement discussion has
    genuinely gone deep (goal / acceptance / constraints / failure modes) and
    the user agrees — never confirm just to advance the pipeline. Every
    INPUT-* in the project must be confirmed before the constitution stage can
    begin. Confirmed INPUT-* is locked — edits are refused until reopened."""
    return _call("/skills/source-requirement-confirm", project_number, {
        "source_requirement_number": source_requirement_number,
    })


@mcp.tool()
def harnoq_source_requirement_reopen(project_number: str, source_requirement_number: str) -> dict:
    """Reopen a locked source requirement (INPUT-*): confirmed -> draft.
    This is the ONLY way to edit a confirmed INPUT-*."""
    return _call("/skills/source-requirement-reopen", project_number, {
        "source_requirement_number": source_requirement_number,
    })


# --- change requests (CR-*) ------------------------------------------------

@mcp.tool()
def harnoq_change_request_list(project_number: str) -> dict:
    """List all live change requests (CR-*) in a project."""
    return _read_call("/skills/change-request", project_number, "list")


@mcp.tool()
def harnoq_change_request_get(project_number: str, change_request_number: str) -> dict:
    """Get one change request (CR-*) by business number, including its analysed
    impact list."""
    return _read_call("/skills/change-request", project_number, "get", {
        "change_request_number": change_request_number,
    })


@mcp.tool()
def harnoq_change_request_impact(
    project_number: str,
    design_number: Annotated[str | None, Field(
        description="DESIGN-* you are considering changing.",
    )] = None,
) -> dict:
    """Ask what a change WOULD affect, without proposing anything. Read-only.
    Use it to answer "what does this break" before deciding whether the change
    needs a CR-* at all — changing something nothing is built on is just an
    edit."""
    payload: dict[str, Any] = {}
    if design_number is not None:
        payload["design_number"] = design_number
    return _read_call("/skills/change-request", project_number, "impact", payload)


@mcp.tool()
def harnoq_change_request_create(
    project_number: str,
    reason: Annotated[str, Field(
        description=f"Why the change is needed, in the user's terms. {MD_CLAUSE}",
    )],
    design_number: Annotated[str | None, Field(
        description="DESIGN-* to be changed.",
    )] = None,
    task_number: Annotated[str | None, Field(
        description="TASK-* whose DELIVERED code is what changes, when no specification moves. Use instead of design_number for a fix to merged work the design still describes correctly.",
    )] = None,
    also_changes_numbers: Annotated[list[str] | None, Field(
        description="Every OTHER DESIGN-*/TASK-* this same change also rewrites. The impact analysis is DERIVED from the targets, so a design left off here does not merely go unmentioned — its tasks never enter the blast radius at all.",
    )] = None,
    target_type: Annotated[str | None, Field(
        description="Pass 'new' (with no number above) when the change is a NEW requirement rather than an edit of an existing object.",
    )] = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict:
    """Propose a change (CR-*) to something that already has work built on it.

    A change request is NOT needed to edit a draft — edit it. It is for the
    case where a DESIGN-* is settled and something downstream depends on
    it, so the cost has to be seen before the edit. Once a task has SHIPPED the
    default answer is an increment, not a rewrite: propose target_type='new'
    (a new requirement, its own design, its own task) and leave the delivery
    alone.

    ONE DECISION USUALLY LANDS IN SEVERAL PLACES. Name every object it
    rewrites: the one it is mainly about goes in design_number/task_number,
    the rest in also_changes_numbers. This is not bookkeeping — the impact
    analysis walks the targets, so a design you leave out contributes no tasks
    to the blast radius, and a task already completed under it is never
    reported at all. Markdown field (reason): use real line breaks, never the
    literal characters backslash-n."""
    fields: dict[str, Any] = {"reason": reason}
    if design_number is not None:
        fields["design_number"] = design_number
    if task_number is not None:
        fields["task_number"] = task_number
    if also_changes_numbers is not None:
        fields["also_changes_numbers"] = also_changes_numbers
    if target_type is not None:
        fields["target_type"] = target_type
    return _call("/skills/change-request-create", project_number, fields, extra_fields)


@mcp.tool()
def harnoq_change_request_analyze(project_number: str, change_request_number: str) -> dict:
    """Walk the chain under a proposed CR-*'s target and record what is
    downstream, each entry carrying what to do about it given how far it got
    (still editable / confirmed / in flight / already shipped). Report the impact.
    For an ordinary defect repair restoring the confirmed contract within existing
    authorization, continue to manager approval without another user confirmation.
    Ask only if the actual impact crosses change.approval_policy's user boundary.
    """
    return _call("/skills/change-request-analyze", project_number, {
        "change_request_number": change_request_number,
    })


@mcp.tool()
def harnoq_change_request_approve(project_number: str, change_request_number: str) -> dict:
    """Approve an analyzed CR under the applicable authorization (manager-only).

    An ordinary defect repair restoring a confirmed contract within existing
    authorization may be approved by the manager without another user confirmation.
    Read the contract, failure evidence and impact first; record the violated clause,
    repair boundary and authorization basis in the CR reason or bound source TASK.
    Do not fabricate a fresh user approval. Follow change.approval_policy: new scope,
    product trade-offs, waivers, lower acceptance/quality/security standards, changed
    fixed constraints, significant extra cost or destructive actions need the user's
    applicable authorization. A CR number or routine regression is not itself a
    reason to stop. Approval does not replace repair, QA, PR, apply or close.
    """
    return _call("/skills/change-request-approve", project_number, {
        "change_request_number": change_request_number,
    })


@mcp.tool()
def harnoq_change_request_apply(project_number: str, change_request_number: str) -> dict:
    """Record that the upstream edit has actually been made. This transition
    edits nothing itself — make the change through the normal update/confirm
    tools first, then mark it applied. Downstream objects then read as suspect
    on their own; digest them one by one."""
    return _call("/skills/change-request-apply", project_number, {
        "change_request_number": change_request_number,
    })


@mcp.tool()
def harnoq_change_request_close(
    project_number: str, change_request_number: str,
    resolution: Annotated[str, Field(
        description="How it ended: made, dropped, rejected, superseded — in one line.",
    )],
) -> dict:
    """Close a CR-* for good. The resolution is required because "closed after
    applying" and "closed because we decided not to" are the two things anyone
    reading the history later needs to tell apart."""
    return _call("/skills/change-request-close", project_number, {
        "change_request_number": change_request_number,
        "resolution": resolution,
    })


# --- features (FEATURE-*) --------------------------------------------------

@mcp.tool()
def harnoq_feature_list(project_number: str) -> dict:
    """List all live features (FEATURE-*) in a project."""
    return _read_call("/skills/feature", project_number, "list")


@mcp.tool()
def harnoq_feature_get(project_number: str, feature_number: str) -> dict:
    """Get one feature (FEATURE-*) by business number."""
    return _read_call("/skills/feature", project_number, "get", {
        "feature_number": feature_number,
    })


@mcp.tool()
def harnoq_feature_create(
    project_number: str,
    name: Annotated[str, Field(description="Short label for this Feature. Plain text.")],
    description: Annotated[str | None, Field(
        description=(
            "This Feature's DESIGN-ENTRY CARD — the only context a later agent has if "
            "it opens this one FEATURE without re-reading the whole INPUT-*. NOT "
            "optional decoration and NOT a restatement of the name (e.g. '提供一致检索"
            "语义' is unacceptable). Write FOUR separate lines, one per label below — "
            "never collapse into one paragraph, each label must visibly appear: "
            "(a) 归属: what this Feature owns/is responsible for and WHY it is independently "
            "planned; (b) 契约: what contract it exposes upward and what it depends "
            "on; (c) 关键数据: key data/state/invariants it owns; (d) 不做: what it "
            f"explicitly does NOT own. Skipping a label or merging them into prose is unacceptable. {MD_CLAUSE}"
        ),
    )] = None,
    assignee: Annotated[str | None, Field(description="Legacy registered account name or USER-* number. Prefer assignee_number.")] = None,
    orientation: Annotated[str | None, Field(
        description="Plain text enum: 'foundation' (project platform layer) or 'vertical' (user-visible vertical slice). Default 'vertical'.",
    )] = None,
    foundation_kind: Annotated[str | None, Field(
        description="Optional for project-level Features only: 'architecture' (Architecture Foundation), 'ui' (Design System + App Shell), 'integration' (delivery gate: assemble the delivered parts into a product that starts, and reconcile what parallel work duplicated), 'system' (delivery gate: exercise the assembled product from the user's side). One live Feature of each kind is allowed per project.",
    )] = None,
    subdomain_type: Annotated[str | None, Field(
        description="Plain text enum: 'core', 'supporting', or 'generic'. Default 'supporting'.",
    )] = None,
    is_spike: Annotated[bool | None, Field(
        description="Plain boolean: marks a core Feature whose first task should validate a walking skeleton.",
    )] = None,
    depends_on_feature_numbers: Annotated[list[str] | None, Field(
        description="List of FEATURE-* numbers this Feature depends on.",
    )] = None,
    extra_fields: dict[str, Any] | None = None,
    assignee_number: Annotated[str | None, Field(description="Registered USER-* owner number. Assigning a user requires administrator access; omit to leave unassigned.")] = None,
) -> dict:
    """Create a Feature (FEATURE-*), identified through information hiding and
    bounded-context analysis rather than technical layers. Two orthogonal tags
    classify the Feature:

    - orientation: 'foundation' (project platform layer users do not touch
      directly) or 'vertical' (a user-visible feature slice).
      Default 'vertical'.
    - foundation_kind: optional identity for a project-level Feature:
      'architecture' (Architecture Foundation), 'ui' (Design System + App
      Shell), 'integration' (delivery gate: assemble the delivered parts into
      a product that starts, and reconcile what parallel work duplicated), or
      'system' (delivery gate: exercise the assembled product from the user's
      side). There can be one live Feature of each kind per project.
    - subdomain_type: 'core' (system's raison d'etre, hardest, spike first),
      'supporting' (needed, not differentiating), or 'generic'
      (industry-solved, buy/reuse). Default 'supporting'.

    is_spike marks a core Feature whose first task should validate a walking skeleton. A
    FEATURE carries no INPUT-* link at all (see harnoq_source_requirement_*
    for the requirement text) — it is identified by name/description alone.
    depends_on_feature_numbers records
    dependency edges (this Feature depends on those FEATURE-*); the
    server rejects a cycle. Markdown fields (description): use real line
    breaks, never the literal characters backslash-n."""
    fields: dict[str, Any] = {"name": name}
    if description is not None:
        fields["description"] = description
    if assignee is not None:
        fields["assignee"] = assignee
    if assignee_number is not None:
        fields["assignee_number"] = assignee_number
    if orientation is not None:
        fields["orientation"] = orientation
    if foundation_kind is not None:
        fields["foundation_kind"] = foundation_kind
    if subdomain_type is not None:
        fields["subdomain_type"] = subdomain_type
    if is_spike is not None:
        fields["is_spike"] = is_spike
    if depends_on_feature_numbers is not None:
        fields["depends_on_feature_numbers"] = depends_on_feature_numbers
    return _call("/skills/feature-create", project_number, fields, extra_fields)


@mcp.tool()
def harnoq_feature_update(
    project_number: str, feature_number: str,
    name: Annotated[str | None, Field(description="Short label for this Feature. Plain text.")] = None,
    description: Annotated[str | None, Field(
        description=f"What this Feature owns and why it is independently planned. {MD_CLAUSE}",
    )] = None,
    assignee: Annotated[str | None, Field(description="Legacy registered account name or USER-* number. Prefer assignee_number.")] = None,
    orientation: Annotated[str | None, Field(
        description="Plain text enum: 'foundation' (project platform layer) or 'vertical' (user-visible feature slice).",
    )] = None,
    foundation_kind: Annotated[str | None, Field(
        description="Optional for project-level Features only: 'architecture', 'ui', 'integration' or 'system'; pass null through extra_fields to clear it.",
    )] = None,
    subdomain_type: Annotated[str | None, Field(
        description="Plain text enum: 'core', 'supporting', or 'generic'.",
    )] = None,
    is_spike: Annotated[bool | None, Field(
        description="Plain boolean: marks a core Feature whose first task should validate a walking skeleton.",
    )] = None,
    depends_on_feature_numbers: Annotated[list[str] | None, Field(
        description="REPLACES the whole list of FEATURE-* numbers this Feature depends on.",
    )] = None,
    extra_fields: dict[str, Any] | None = None,
    assignee_number: Annotated[str | None, Field(description="Registered USER-* owner number. Changing assignment requires administrator access; use harnoq_feature_assign to clear.")] = None,
) -> dict:
    """Update a Feature's metadata, its classification tags
    (orientation/foundation_kind/subdomain_type/is_spike), or its dependency edges. Pass
    depends_on_feature_numbers to REPLACE the whole edge list (not a delta);
    the server rejects a change that would make the dependency graph cyclic. Omit
    any argument to leave it unchanged. Markdown fields (description): use
    real line breaks, never the literal characters backslash-n."""
    fields: dict[str, Any] = {"feature_number": feature_number}
    if name is not None:
        fields["name"] = name
    if description is not None:
        fields["description"] = description
    if assignee is not None:
        fields["assignee"] = assignee
    if assignee_number is not None:
        fields["assignee_number"] = assignee_number
    if orientation is not None:
        fields["orientation"] = orientation
    if foundation_kind is not None:
        fields["foundation_kind"] = foundation_kind
    if subdomain_type is not None:
        fields["subdomain_type"] = subdomain_type
    if is_spike is not None:
        fields["is_spike"] = is_spike
    if depends_on_feature_numbers is not None:
        fields["depends_on_feature_numbers"] = depends_on_feature_numbers
    return _call("/skills/feature-update", project_number, fields, extra_fields)


@mcp.tool()
def harnoq_feature_delete(project_number: str, feature_number: str) -> dict:
    """Soft-delete a feature (FEATURE-*)."""
    return _call("/skills/feature-delete", project_number, {
        "feature_number": feature_number,
    })


@mcp.tool()
def harnoq_feature_assign(
    project_number: str, feature_number: str,
    assignee_number: Annotated[str | None, Field(description="Registered USER-* owner number, or null to clear. Requires administrator access.")],
) -> dict:
    """Set or clear the assignee on a feature (FEATURE-*)."""
    return _call("/skills/feature-assign", project_number, {
        "feature_number": feature_number,
        "assignee_number": assignee_number,
    })


# --- project constitution --------------------------------------------------

@mcp.tool()
def harnoq_constitution_get(project_number: str) -> dict:
    """Get the project's constitution (the single governance record: fixed
    stack, quality bar, UI conventions, NFR baseline, boundaries). Returns null
    if none exists yet."""
    return _read_call("/skills/constitution", project_number, "get")


@mcp.tool()
def harnoq_constitution_update(
    project_number: str,
    system_nature: Annotated[str | None, Field(
        description=f"System-type / pressure-surface analysis. {MD_CLAUSE}",
    )] = None,
    fixed_stack: Annotated[str | None, Field(
        description=f"The fixed stack/library declarations, deduped. {MD_CLAUSE}",
    )] = None,
    quality_bar: Annotated[str | None, Field(
        description=f"Test bar / definition of done. {MD_CLAUSE}",
    )] = None,
    ui_conventions: Annotated[str | None, Field(
        description=f"Page style and interaction conventions. {MD_CLAUSE}",
    )] = None,
    nfr_baseline: Annotated[str | None, Field(
        description=f"Performance/scale/latency targets as a markdown table (metric, target, strength, applies-to). strength is `commitment` (a number the project promises, which a design must later prove) or `direction` (a stance with no number to measure); a table without the column is read as all commitments. Only commitments are asked for an owning design and only commitments should become acceptance criteria, so settle each row with the user rather than defaulting. {MD_CLAUSE}",
    )] = None,
    boundaries: Annotated[str | None, Field(
        description=f"Out-of-scope + high-risk irreversible operations (always/ask-first/never). {MD_CLAUSE}",
    )] = None,
    status: Annotated[str | None, Field(
        description="Plain text lifecycle enum.",
    )] = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict:
    """Write the project constitution (draft). Upserts the singleton and edits
    the six governance sections, consolidated from the confirmed INPUT-* set. Only
    editable while draft/needs_clarification; reopen a confirmed one first.
    Markdown fields (system_nature, fixed_stack, quality_bar, ui_conventions,
    nfr_baseline, boundaries): use real line breaks, never the literal
    characters backslash-n."""
    return _call("/skills/constitution-update", project_number, {
        "system_nature": system_nature,
        "fixed_stack": fixed_stack,
        "quality_bar": quality_bar,
        "ui_conventions": ui_conventions,
        "nfr_baseline": nfr_baseline,
        "boundaries": boundaries,
        "status": status,
    }, extra_fields)


@mcp.tool()
def harnoq_constitution_confirm(project_number: str) -> dict:
    """Confirm the project constitution: draft/needs_clarification ->
    confirmed. Design may not begin until it is confirmed. Rejected if empty."""
    return _call("/skills/constitution-confirm", project_number, {})


@mcp.tool()
def harnoq_constitution_reopen(project_number: str) -> dict:
    """Reopen a locked constitution: confirmed -> draft. The only way to edit
    a confirmed constitution."""
    return _call("/skills/constitution-reopen", project_number, {})


# --- glossary --------------------------------------------------------------

@mcp.tool()
def harnoq_glossary_get(project_number: str) -> dict:
    """Read the project's terminology glossary. Before writing requirement,
    design, or task prose, copy listed term_native/term_en exactly and never
    use a word listed in avoid. This read-only tool is available to every role."""
    return _read_call("/skills/glossary", project_number, "get")


@mcp.tool()
def harnoq_glossary_create(
    project_number: str,
    term_native: Annotated[str, Field(description="The user's original domain term. Plain text.")],
    term_en: Annotated[str, Field(description="Authoritative English identifier name for code. Plain text.")],
    definition_en: Annotated[str | None, Field(description=f"English definition. {MD_CLAUSE}")] = None,
    avoid: Annotated[str | None, Field(description="Forbidden near-synonyms, one per line. Plain text.")] = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict:
    """Add a project glossary term after confirming the new concept with the user.

    Substitution test: write the sentence somebody would produce with the wrong word in
    it, and put that WORD — usually a generic catch-all this project overloads — in
    `avoid`. No such sentence, no entry. Never point `avoid` at another glossary term:
    concepts with their own distinct names are adjacent, not confusable.
    """
    return _call("/skills/glossary-create", project_number, {
        "term_native": term_native,
        "term_en": term_en,
        "definition_en": definition_en,
        "avoid": avoid,
    }, extra_fields)


@mcp.tool()
def harnoq_glossary_update(
    project_number: str,
    existing_term_native: Annotated[str, Field(description="Current unique native term that identifies the row. Plain text.")],
    term_native: Annotated[str | None, Field(description="Replacement native term, if renaming. Plain text.")] = None,
    term_en: Annotated[str | None, Field(description="Replacement authoritative English identifier. Plain text.")] = None,
    definition_en: Annotated[str | None, Field(description=f"Replacement English definition. {MD_CLAUSE}")] = None,
    avoid: Annotated[str | None, Field(description="Replacement forbidden near-synonyms, one per line. Plain text.")] = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict:
    """Update one glossary term. This intentionally does not trigger review propagation."""
    return _call("/skills/glossary-update", project_number, {
        "existing_term_native": existing_term_native,
        "term_native": term_native,
        "term_en": term_en,
        "definition_en": definition_en,
        "avoid": avoid,
    }, extra_fields)


@mcp.tool()
def harnoq_glossary_delete(project_number: str, term_native: str) -> dict:
    """Soft-delete a glossary term identified by its unique native term."""
    return _call("/skills/glossary-delete", project_number, {"term_native": term_native})


# --- integration/system obligations (INTEG-* / SYS-*) -----------------------
# One thing that must have an owner before a project can be called delivered:
# an integration-stage assembly part / declared seam / found duplicate, or a
# system-stage user journey / NFR commitment. See the integration/system
# stage methods (active_stage_method) for when and how these get created.


class IntegrationEvidenceRecord(BaseModel):
    key: str = Field(description="Unique local evidence key used by other manifest rows.")
    role: str = Field(
        description="entry-launch | entry-observation | entry-launch-observation | source-scan | skeleton | contract | caller | owner | test | resource-check | resource-call | other"
    )
    command: str = Field(description="Literal copy-pasteable command; no prose or ellipsis.")
    exit_code: int
    expected_exit_codes: list[int] = Field(default_factory=lambda: [0])
    stdout: str = Field(description="Relevant raw stdout inline; empty string when empty.")
    stderr: str = Field(description="Relevant raw stderr inline; empty string when empty.")


class IntegrationProductionEntryRecord(BaseModel):
    key: str
    name: str
    entry_type: str = Field(description="service | cli | browser | scheduled")
    operational_invocation: str = Field(
        description="Actual delivered-product launch/load invocation; never build/test/dev/preview."
    )
    observable_boundary: str
    executes_delivered_behavior: bool
    launch_evidence_key: str
    observation_evidence_key: str


class IntegrationDesignCoverageRecord(BaseModel):
    key: str = Field(description="Must equal the fully qualified design_number.")
    design_number: str
    selected_solution_sha256: str
    design_scope_sha256: str
    declared_part_keys: list[str]
    seam_keys: list[str]


class IntegrationSourceScanRecord(BaseModel):
    key: str
    language: str
    roots: list[str]
    scan_evidence_key: str
    collision_keys: list[str]
    different_name_candidate_keys: list[str]


class IntegrationDeclaredPartRecord(BaseModel):
    key: str = Field(description="Unique declared-part key; reused by its assembly row.")
    design_number: str = Field(description="Fully qualified P*-DESIGN-* business number.")
    name: str = Field(description="Exactly one part, copied from selected_solution.")
    declaration: str = Field(
        description="Exact declaration text copied verbatim from selected_solution."
    )


class IntegrationAssemblyRow(BaseModel):
    key: str = Field(description="Must equal exactly one declared_parts key.")
    reachable: bool
    entry_keys: list[str] = Field(
        description="Verified production entry keys; empty when unreachable."
    )
    path: list[str] = Field(
        description="One wired edge per step from entry to part; empty when unreachable."
    )
    gap_key: str | None = Field(
        description="One missing-edge key when unreachable; null when reachable."
    )
    missing_edge: str | None = Field(
        description="Exact missing producer -> consumer edge; null when reachable."
    )
    evidence_keys: list[str]


class IntegrationSeamRow(BaseModel):
    key: str
    declaring_design_number: str
    producer_design_number: str
    consumer_design_number: str
    producer: str = Field(description="Exactly one producer endpoint/contract.")
    consumer: str = Field(description="Exactly one consumer endpoint/contract.")
    declaration: str = Field(
        description="Exact seam declaration copied verbatim from the declaring design_scope."
    )
    wiring: str
    wired: bool
    integration_verified: bool = Field(
        description="True only when one passing test directly exercises this producer -> consumer seam."
    )
    test: str | None = Field(
        default=None,
        description="Exact test node or executable command; null when missing.",
    )
    test_evidence_key: str | None = None
    evidence_keys: list[str]


class IntegrationImplementationRecord(BaseModel):
    module: str
    identifier: str


class IntegrationIdentifierCollisionRecord(BaseModel):
    key: str
    identifier: str = Field(description="Complete identifier exactly equal in all rows.")
    implementations: list[IntegrationImplementationRecord] = Field(min_length=2)
    evidence_keys: list[str]


class IntegrationDuplicateCandidateRecord(BaseModel):
    key: str
    discovery: str = Field(description="exact-name | different-name")
    collision_keys: list[str] = Field(
        description="Exact-name collision keys owned by this one responsibility; empty for different-name."
    )
    implementations: list[IntegrationImplementationRecord] = Field(
        description="At least two separately module-qualified implementations."
    )
    responsibility: str
    verdict: str = Field(description="converge | retain")
    canonical_owner: str | None = None
    caller_semantics: str
    divergence_is_defect: bool = False
    boundary_fit: str | None = None
    baseline_evidence_key: str | None = None
    rationale: str
    evidence_keys: list[str]
    contract_evidence_keys: list[str] = Field(default_factory=list)
    caller_evidence_keys: list[str] = Field(default_factory=list)
    owner_evidence_keys: list[str] = Field(default_factory=list)


class IntegrationProposedItemRecord(BaseModel):
    key: str = Field(description="Exactly one assembly-gap, unsatisfied-seam, or converge key.")
    kind: str = Field(description="assembly | seam | duplicate")
    subject: str = Field(description="Standalone full subject; no common tail or cross-reference.")
    source: str = Field(description="Standalone full provenance with qualified business numbers.")
    found_in_round: int
    existing_item_number: str | None = None


class SystemInputAuditRecord(BaseModel):
    input_number: str
    journey_keys: list[str] = Field(default_factory=list)
    no_journey_reason: str | None = None


class SystemNfrAuditRecord(BaseModel):
    nfr_key: str
    proposed_item_key: str


class SystemProposedItemRecord(BaseModel):
    key: str
    kind: str
    subject: str
    source: str
    driver: str
    found_in_round: int
    existing_item_number: str | None = None


class SystemRerunEvidenceRecord(BaseModel):
    item_number: str
    command: str
    exit_code: int
    stdout: str
    stderr: str


class IntegrationResourceDesignAudit(BaseModel):
    design_number: str
    resource_keys: list[str]
    no_resources_reason: str | None = None


class IntegrationExternalResource(BaseModel):
    key: str
    name: str
    source: str = Field(description="Exact design/code/configuration provenance; a waiver INTEG must carry this same source.")
    configuration: str = Field(description="Actual configured resource location/version or endpoint; no credentials.")
    status: str = Field(description="verified | missing | invalid | unverified | waived")
    entry_keys: list[str] = Field(default_factory=list)
    uses_test_double: bool | None = None
    operation: str | None = None
    observed_result: str | None = None
    readiness_evidence_key: str | None = None
    call_evidence_key: str | None = None
    preparation: str | None = Field(default=None, description="For missing/invalid/unverified: source/version, placement or access, steps and verification command; identify any user action/cost.")
    waiver_item_number: str | None = None


class IntegrationExternalResources(BaseModel):
    scan_evidence_keys: list[str]
    design_audits: list[IntegrationResourceDesignAudit]
    resources: list[IntegrationExternalResource]


def _dump_manifest_rows(rows):
    return [row.model_dump(exclude_none=True) for row in rows]


class IntegrationInventorySectionUpdates(BaseModel):
    """Omitted sections are retained; supplied sections replace the whole section."""

    model_config = ConfigDict(extra="forbid")
    production_entries: list[IntegrationProductionEntryRecord] | None = None
    design_coverage: list[IntegrationDesignCoverageRecord] | None = None
    source_scans: list[IntegrationSourceScanRecord] | None = None
    declared_parts: list[IntegrationDeclaredPartRecord] | None = None
    assembly_rows: list[IntegrationAssemblyRow] | None = None
    seams: list[IntegrationSeamRow] | None = None
    identifier_collisions: list[IntegrationIdentifierCollisionRecord] | None = None
    duplicate_candidates: list[IntegrationDuplicateCandidateRecord] | None = None
    proposed_items: list[IntegrationProposedItemRecord] | None = None
    external_resources: IntegrationExternalResources | None = None


@mcp.tool()
def harnoq_integration_inventory_get(project_number: str) -> dict:
    """Recover the saved full inventory, exact rerun obligations and current blockers.

    Read-only. Returns validation_id and revision_token for a reviewed revision,
    plus design/item changes and basis_changed. Review affected code and evidence
    on basis changes; do not just copy new hashes. No saved inventory requires an
    initial full validate. Never ask the user to reconstruct a technical payload.
    """
    return _call("/skills/integration-inventory-get", project_number, {})


@mcp.tool()
def harnoq_integration_inventory_revise(
    project_number: str,
    base_validation_id: str,
    reviewed_revision_token: str,
    evidence_updates: list[IntegrationEvidenceRecord],
    section_updates: IntegrationInventorySectionUpdates | None = None,
) -> dict:
    """Revise a saved inventory and run the original complete server audit.

    First get_inventory and review changes; echo validation_id and revision_token.
    Evidence is added/replaced by key. Omitted sections are retained; supplied
    sections replace the whole section (an empty list clears it). No deep merge.
    Round and server-recorded created-item references are reconciled automatically.
    On basis changes, supply fresh exact reruns for all covered items and review
    affected paths/resources. Never relabel old outputs as fresh runs. State drift
    requires get_inventory again. valid=true is not integration_complete=true;
    resolve returned errors autonomously. No user approval for payload preparation.
    """
    return _call("/skills/integration-inventory-revise", project_number, {
        "base_validation_id": base_validation_id,
        "reviewed_revision_token": reviewed_revision_token,
        "evidence_updates": _dump_manifest_rows(evidence_updates),
        "section_updates": section_updates.model_dump(exclude_none=True) if section_updates else {},
    })


@mcp.tool()
def harnoq_integration_inventory_validate(
    project_number: str,
    found_in_round: int,
    evidence: list[IntegrationEvidenceRecord],
    production_entries: list[IntegrationProductionEntryRecord],
    design_coverage: list[IntegrationDesignCoverageRecord],
    source_scans: list[IntegrationSourceScanRecord],
    declared_parts: list[IntegrationDeclaredPartRecord],
    assembly_rows: list[IntegrationAssemblyRow],
    seams: list[IntegrationSeamRow],
    identifier_collisions: list[IntegrationIdentifierCollisionRecord],
    duplicate_candidates: list[IntegrationDuplicateCandidateRecord],
    proposed_items: list[IntegrationProposedItemRecord],
    external_resources: IntegrationExternalResources,
) -> dict:
    """Validate the complete Integration Round-1 manifest before item creation.

    Mandatory. Harnoq, not the Agent, computes the assembly, seam, convergence
    and proposed-item sets and checks their exact one-to-one mapping. It creates
    no workflow object or INTEG-* item; valid=true stores a receipt authorizing
    creation of the exact bound payloads, without a separate per-item approval.
    A valid=false response must be corrected and resubmitted.
    At close-out, use harnoq_integration_inventory_get and
    harnoq_integration_inventory_revise to reuse the saved manifest, or resubmit
    the complete current manifest after coverage, including
    successful role=test evidence whose commands name every covered item's exact
    QA test. valid=true alone is not completion: integration_complete=true records
    a final audit on the current basis. Resolve completion_errors if present.
    A zero-gap pass may complete without a new TASK/PR; read project context next.
    Audit external resources for EVERY capability DESIGN, backed by production
    code/configuration scans. verified requires successful resource-check and
    resource-call evidence on the configured production path without test doubles.
    Missing/unverified resources return resource_requests for concrete preparation;
    they block completion. An explicit waiver references a live waived INTEG
    carrying the same source. Never substitute missing-resource handling for a
    real successful invocation; never put credentials in evidence.
    Build/test/dev/preview commands are evidence but are not production entries.
    """
    manifest = {
        "found_in_round": found_in_round,
        "evidence": _dump_manifest_rows(evidence),
        "production_entries": _dump_manifest_rows(production_entries),
        "design_coverage": _dump_manifest_rows(design_coverage),
        "source_scans": _dump_manifest_rows(source_scans),
        "declared_parts": _dump_manifest_rows(declared_parts),
        "assembly_rows": _dump_manifest_rows(assembly_rows),
        "seams": _dump_manifest_rows(seams),
        "identifier_collisions": _dump_manifest_rows(identifier_collisions),
        "duplicate_candidates": _dump_manifest_rows(duplicate_candidates),
        "proposed_items": _dump_manifest_rows(proposed_items),
        "external_resources": external_resources.model_dump(exclude_none=True),
    }
    return _call(
        "/skills/integration-inventory-validate",
        project_number,
        {"manifest": manifest},
    )


@mcp.tool()
def harnoq_system_inventory_validate(
    project_number: str,
    found_in_round: int,
    input_audits: list[SystemInputAuditRecord],
    nfr_audits: list[SystemNfrAuditRecord],
    proposed_items: list[SystemProposedItemRecord],
    rerun_evidence: list[SystemRerunEvidenceRecord] = [],
) -> dict:
    """Validate the complete System journey/NFR inventory before SYS writes.

    The receipt binds confirmed INPUT coverage, every NFR commitment, drivers,
    re-run evidence, and exact SYS payloads.  Create calls consume one key.
    """
    return _call("/skills/system-inventory-validate", project_number, {"manifest": {
        "found_in_round": found_in_round,
        "input_audits": _dump_manifest_rows(input_audits),
        "nfr_audits": _dump_manifest_rows(nfr_audits),
        "proposed_items": _dump_manifest_rows(proposed_items),
        "rerun_evidence": _dump_manifest_rows(rerun_evidence),
    }})

def harnoq_delivery_item_list(
    project_number: str,
    kind: Annotated[
        list[str] | None,
        Field(description="Filter to one or more of: assembly, seam, duplicate, journey, nfr. Omit for all."),
    ] = None,
) -> dict:
    """List live INTEG-*/SYS-* obligations for a project, optionally filtered by kind."""
    payload: dict[str, Any] = {}
    if kind is not None:
        payload["kind"] = kind
    return _read_call("/skills/delivery-item", project_number, "list", payload)


def harnoq_delivery_item_create(
    project_number: str,
    kind: Annotated[str, Field(description="Current Integration stage: assembly, seam, or duplicate. Current System stage: journey or nfr. Cross-stage writes are rejected.")],
    subject: Annotated[str, Field(description=f"What it is. Multi-line for a journey's ordered steps. {MD_CLAUSE}")],
    source: Annotated[str, Field(description="Required provenance line. Cite the exact fully qualified business number and field that declared the finding, or the exact repository command/code location that discovered it. Never abbreviate a returned business number. Plain text.")],
    found_in_round: Annotated[int, Field(description="Required positive inventory pass number: max(existing found_in_round)+1, or 1 when none exist.")],
    driver: Annotated[str | None, Field(description="Only for kind=journey/nfr: how it is driven (browser automation, a load tool, ...). Plain text.")] = None,
    validation_id: Annotated[str | None, Field(description="Required for Integration/System kinds with an inventory source: validation_id returned by the latest valid inventory manifest.")] = None,
    source_row_key: Annotated[str | None, Field(description="Required for receipt-bound Integration/System kinds: the unique validated manifest row key.")] = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict:
    """Record one delivery item. Call harnoq_project_context first: its
    current_stage selects which kinds are writable and its active_stage_method
    defines the batch and its completion contract. Round 1 of Integration/System
    creates one call per item after the validated batch is ready, not a paragraph
    describing several; a per-item response never authorizes
    one TASK-* per item. Receipt-bound Integration/System calls must carry validation_id and source_row_key;
    the server rejects payloads not exactly present in that validated batch. Existing
    wiring or an earlier test is not a waiver."""
    return _call("/skills/delivery-item-create", project_number, {
        "kind": kind,
        "subject": subject,
        "source": source,
        "driver": driver,
        "found_in_round": found_in_round,
        "validation_id": validation_id,
        "source_row_key": source_row_key,
    }, extra_fields)


def harnoq_delivery_item_update(
    project_number: str,
    item_number: Annotated[str, Field(description="INTEG-* or SYS-* identifying the item.")],
    status: Annotated[
        str | None,
        Field(description="open | covered | waived. covered requires current executable evidence in an active stage run; waived requires waive_reason."),
    ] = None,
    covered_by: Annotated[str | None, Field(description="Required when status=covered: name the exact check that discharges this item.")] = None,
    waive_reason: Annotated[str | None, Field(description="Required when status=waived: who decided not to do it, and why. Plain text.")] = None,
    subject: Annotated[str | None, Field(description=f"Replacement content. {MD_CLAUSE}")] = None,
    source: Annotated[str | None, Field(description="Replacement provenance line; may not be blank. Copy fully qualified business numbers exactly. Plain text.")] = None,
    driver: Annotated[str | None, Field(description="Replacement driving method (kind=journey/nfr only). Plain text.")] = None,
    evidence_command: str | None = None,
    evidence_output: str | None = None,
    evidence_exit_code: int | None = None,
    verified_revision: str | None = None,
    repair_record: str | None = None,
    blocked_reason: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict:
    """Update a delivery item in the currently active Integration/System stage
    — most commonly to close it out. Cross-stage Agent writes are rejected.
    Covered requires the exact successful command, raw output and current code
    revision in an active stage run. Record ordinary repairs and required-user-
    resource blockers directly on this item. Waived requires the user's reason."""
    return _call("/skills/delivery-item-update", project_number, {
        "item_number": item_number,
        "status": status,
        "covered_by": covered_by,
        "waive_reason": waive_reason,
        "subject": subject,
        "source": source,
        "driver": driver,
        "evidence_command": evidence_command,
        "evidence_output": evidence_output,
        "evidence_exit_code": evidence_exit_code,
        "verified_revision": verified_revision,
        "repair_record": repair_record,
        "blocked_reason": blocked_reason,
    }, extra_fields)


@mcp.tool()
def harnoq_delivery_stage_run_start(project_number: str, stage: str, code_revision: str,
                                    design_revision: str, branch: str = "") -> dict:
    """Start the one project-wide Integration/System run; it owns direct item evidence."""
    return post_skill(f"/projects/{project_number}/delivery-stage-runs/{stage}", {
        "code_revision": code_revision, "design_revision": design_revision, "branch": branch})


@mcp.tool()
def harnoq_delivery_stage_run_update(project_number: str, run_number: str,
                                     evidence: dict | str | None = None, blocked_reason: str | None = None,
                                     code_revision: str | None = None,
                                     integration_rerun_evidence: list[dict] | str | None = None) -> dict:
    """Record evidence or a blocker on the current revision.

    Pass evidence as a JSON object, not an encoded string. Integration QA needs
    role=qa, item_numbers, command, output, exit_code=0 and code_revision.
    Pass integration_rerun_evidence as an array of command/output/exit_code/
    code_revision objects. Serialized JSON is accepted for existing clients.
    """
    return post_skill(f"/projects/{project_number}/delivery-stage-runs/{run_number}/update", {
        "evidence": evidence, "blocked_reason": blocked_reason, "code_revision": code_revision,
        "integration_rerun_evidence": integration_rerun_evidence})


@mcp.tool()
def harnoq_delivery_stage_run_close(project_number: str, run_number: str,
                                    merge_reference: str | None = None,
                                    integration_rerun_evidence: list[dict] | str | None = None,
                                    evidence: dict | str | None = None) -> dict:
    """Close only after all current stage evidence and inventory gates pass."""
    return post_skill(f"/projects/{project_number}/delivery-stage-runs/{run_number}/close", {"merge_reference": merge_reference, "integration_rerun_evidence": integration_rerun_evidence, "evidence": evidence})


@mcp.tool()
def harnoq_delivery_stage_run_abandon(project_number: str, run_number: str,
                                      reason: str) -> dict:
    """End an uncloseable run after a confirmed basis change, recording why."""
    return post_skill(f"/projects/{project_number}/delivery-stage-runs/{run_number}/abandon", {"reason": reason})




# Canonical stage-specific names.  The generic delivery-item functions above
# are private compatibility helpers and are not registered as MCP tools; new agents
# should use the name that also tells them which kinds and business-number
# family they are operating on.
_INTEGRATION_ITEM_KINDS = ("assembly", "seam", "duplicate")
_SYSTEM_ITEM_KINDS = ("journey", "nfr")


def _stage_item_kinds(kind, allowed):
    selected = list(allowed if kind is None else kind)
    invalid = sorted(set(selected) - set(allowed))
    if invalid:
        raise HarnoqError(
            f"stage-specific item tool accepts only {list(allowed)}; invalid: {invalid}"
        )
    return selected


@mcp.tool()
def harnoq_integration_item_list(
    project_number: str,
    kind: Annotated[list[str] | None, Field(description="Optional filter: assembly, seam, or duplicate.")] = None,
) -> dict:
    """List live INTEG-* integration obligations."""
    return harnoq_delivery_item_list(
        project_number, kind=_stage_item_kinds(kind, _INTEGRATION_ITEM_KINDS)
    )


@mcp.tool()
def harnoq_integration_item_create(
    project_number: str,
    kind: Annotated[str, Field(description="assembly, seam, or duplicate.")],
    subject: Annotated[str, Field(description=f"Standalone obligation subject. {MD_CLAUSE}")],
    source: Annotated[str, Field(description="Fully qualified provenance.")],
    found_in_round: Annotated[int, Field(description="Positive inventory pass number.")],
    validation_id: Annotated[str | None, Field(description="Latest valid inventory receipt.")] = None,
    source_row_key: Annotated[str | None, Field(description="Validated manifest row key.")] = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict:
    """Create one validated INTEG-* obligation; technical review is not required."""
    _stage_item_kinds([kind], _INTEGRATION_ITEM_KINDS)
    return harnoq_delivery_item_create(
        project_number, kind, subject, source, found_in_round,
        driver=None, validation_id=validation_id, source_row_key=source_row_key,
        extra_fields=extra_fields,
    )


@mcp.tool()
def harnoq_integration_item_update(
    project_number: str,
    item_number: str,
    status: str | None = None,
    covered_by: str | None = None,
    waive_reason: str | None = None,
    subject: str | None = None,
    source: str | None = None,
    evidence_command: str | None = None,
    evidence_output: str | None = None,
    evidence_exit_code: int | None = None,
    verified_revision: str | None = None,
    repair_record: str | None = None,
    blocked_reason: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict:
    """Update an INTEG-* obligation with current executable evidence.

    Outside its delivery stage, only a user-authorized waiver of an existing
    item is allowed: send item_number, status='waived' and waive_reason only.
    Do not create a TASK/PR solely to record the decision.
    """
    return harnoq_delivery_item_update(
        project_number, item_number, status=status, covered_by=covered_by,
        waive_reason=waive_reason, subject=subject, source=source, driver=None,
        evidence_command=evidence_command, evidence_output=evidence_output,
        evidence_exit_code=evidence_exit_code, verified_revision=verified_revision,
        repair_record=repair_record, blocked_reason=blocked_reason,
        extra_fields=extra_fields,
    )


@mcp.tool()
def harnoq_system_item_list(
    project_number: str,
    kind: Annotated[list[str] | None, Field(description="Optional filter: journey or nfr.")] = None,
) -> dict:
    """List live SYS-* system obligations."""
    return harnoq_delivery_item_list(
        project_number, kind=_stage_item_kinds(kind, _SYSTEM_ITEM_KINDS)
    )


@mcp.tool()
def harnoq_system_item_create(
    project_number: str,
    kind: Annotated[str, Field(description="journey or nfr.")],
    subject: Annotated[str, Field(description=f"Standalone scenario or commitment. {MD_CLAUSE}")],
    source: Annotated[str, Field(description="Fully qualified provenance.")],
    found_in_round: Annotated[int, Field(description="Positive inventory pass number.")],
    driver: str | None = None,
    validation_id: Annotated[str | None, Field(description="Current System inventory receipt.")] = None,
    source_row_key: Annotated[str | None, Field(description="Validated System manifest row key.")] = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict:
    """Create one SYS-* obligation selected by the agent's system inventory."""
    _stage_item_kinds([kind], _SYSTEM_ITEM_KINDS)
    return harnoq_delivery_item_create(
        project_number, kind, subject, source, found_in_round,
        driver=driver, validation_id=validation_id, source_row_key=source_row_key,
        extra_fields=extra_fields,
    )


@mcp.tool()
def harnoq_system_item_update(
    project_number: str,
    item_number: str,
    status: str | None = None,
    covered_by: str | None = None,
    waive_reason: str | None = None,
    subject: str | None = None,
    source: str | None = None,
    driver: str | None = None,
    evidence_command: str | None = None,
    evidence_output: str | None = None,
    evidence_exit_code: int | None = None,
    verified_revision: str | None = None,
    repair_record: str | None = None,
    blocked_reason: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict:
    """Update a SYS-* obligation with current executable evidence.

    Outside System, only a user-authorized waiver of an existing item is
    allowed: send item_number, status='waived' and waive_reason only. A repair
    need not finish before this decision can be recorded; no new TASK/PR is
    needed solely for the waiver. Coverage and scope edits remain stage-gated.
    """
    return harnoq_delivery_item_update(
        project_number, item_number, status=status, covered_by=covered_by,
        waive_reason=waive_reason, subject=subject, source=source, driver=driver,
        evidence_command=evidence_command, evidence_output=evidence_output,
        evidence_exit_code=evidence_exit_code, verified_revision=verified_revision,
        repair_record=repair_record, blocked_reason=blocked_reason,
        extra_fields=extra_fields,
    )


# --- design cases (DESIGN-*) -----------------------------------------------

@mcp.tool()
def harnoq_design_case_list(
    project_number: str, feature_number: str | None = None,
) -> dict:
    """List live design cases (DESIGN-*). Omit feature_number to list all
    designs in the project; pass FEATURE-* to list designs for one Feature."""
    payload = {}
    if feature_number is not None:
        payload["feature_number"] = feature_number
    return _read_call("/skills/design-case", project_number, "list", payload)


@mcp.tool()
def harnoq_design_case_get(project_number: str, design_number: str) -> dict:
    """Get one design case (DESIGN-*) by business number."""
    return _read_call("/skills/design-case", project_number, "get", {
        "design_number": design_number,
    })


@mcp.tool()
def harnoq_design_case_create(
    project_number: str,
    feature_number: Annotated[str, Field(
        description="FEATURE-* this design belongs to. Plain text business number.",
    )],
    extra_fields: dict[str, Any] | None = None,
) -> dict:
    """Create a design case (DESIGN-*) under a FEATURE-*. A design has
    no direct link to any INPUT-* — read the relevant INPUT-* material and
    cite it in prose (problem_statement) instead. A feature that splits into
    several DESIGN-* (功能 + 页面 + shared module) just creates several
    designs under the same feature_number.

    Fill fields with harnoq_design_case_update, then confirm."""
    fields: dict[str, Any] = {"feature_number": feature_number}
    return _call("/skills/design-case-create", project_number, fields, extra_fields)


@mcp.tool()
def harnoq_design_case_update(
    project_number: str, design_number: str,
    problem_statement: Annotated[str | None, Field(
        description=f"The INPUT-*'s problem restated as a DESIGN problem: what must be decided and why. {MD_CLAUSE}",
    )] = None,
    mined_requirements: Annotated[str | None, Field(
        description=f"需求(我理解你要的是): the requirement as mined at this stage, ONE LINE PER REQUIREMENT, each marked 用户确认 / 我补的(已认可) / 待确认. The only place the mined requirement is stored. {MD_CLAUSE}",
    )] = None,
    verifies_nfr: Annotated[str | None, Field(
        description=f"Which project-constitution nfr_baseline COMMITMENT rows THIS design proves, one per line, quoting the metric name as written and saying how it is measured. Write 无 when it proves none — most designs do — since confirming requires an answer once the baseline states any commitment, and blank cannot be told apart from never having been asked. Rows marked `direction` are not claimed here and must not become acceptance criteria. A commitment no design claims is a project promise with no owner. {MD_CLAUSE}",
    )] = None,
    design_scope: Annotated[str | None, Field(
        description=f"What this design owns vs. leaves to neighboring DESIGN-*. The requirement itself goes in mined_requirements, not here. {MD_CLAUSE}",
    )] = None,
    non_goals: Annotated[str | None, Field(
        description=f"What this design explicitly does NOT cover. {MD_CLAUSE}",
    )] = None,
    assumptions: Annotated[str | None, Field(
        description=f"Assumptions this design relies on. {MD_CLAUSE}",
    )] = None,
    constraints: Annotated[str | None, Field(
        description=f"Concrete bounds the solution must honor. {MD_CLAUSE}",
    )] = None,
    selected_solution: Annotated[str | None, Field(
        description=f"The chosen rules/behavior and buildable shape (components, data, flow, contracts). {MD_CLAUSE}",
    )] = None,
    selection_reason: Annotated[str | None, Field(
        description=f"Why this option beat the concrete alternatives. {MD_CLAUSE}",
    )] = None,
    risks: Annotated[str | None, Field(
        description=f"Concrete failure modes of the chosen solution and how they're contained. {MD_CLAUSE}",
    )] = None,
    test_strategy: Annotated[str | None, Field(
        description=f"How the tester will prove the acceptance criteria. {MD_CLAUSE}",
    )] = None,
    acceptance_criteria: Annotated[str | None, Field(
        description=f"Verifiable given/when/then covering happy path plus failure/edge paths. {MD_CLAUSE}",
    )] = None,
    feature_number: Annotated[str | None, Field(
        description="Move this design to a different FEATURE-*. Plain text business number.",
    )] = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict:
    """Fill or revise a design case (DESIGN-*). Confirm requires (at least)
    test_strategy and the core solution fields to be present. Pass
    feature_number to move it to a different FEATURE-*. Markdown fields
    (problem_statement, mined_requirements, verifies_nfr, design_scope, non_goals,
    assumptions, constraints,
    selected_solution, selection_reason, risks, test_strategy,
    acceptance_criteria): use real line breaks, never the literal characters
    backslash-n."""
    fields = {
        "design_number": design_number,
        "problem_statement": problem_statement,
        "mined_requirements": mined_requirements, "verifies_nfr": verifies_nfr,
        "design_scope": design_scope,
        "non_goals": non_goals, "assumptions": assumptions,
        "constraints": constraints, "selected_solution": selected_solution,
        "selection_reason": selection_reason, "risks": risks,
        "test_strategy": test_strategy, "acceptance_criteria": acceptance_criteria,
    }
    if feature_number is not None:
        fields["feature_number"] = feature_number
    return _call("/skills/design-case-update", project_number, fields, extra_fields)


@mcp.tool()
def harnoq_design_case_confirm(project_number: str, design_number: str) -> dict:
    """Confirm a design case (DESIGN-*): draft/needs_clarification ->
    confirmed. Confirm only after the user approves the solution; the service
    enforces the required fields (problem_statement, selected_solution,
    selection_reason, risks, test_strategy, acceptance_criteria). Confirmed
    DESIGN-* is locked — edits are refused until reopened."""
    return _call("/skills/design-case-confirm", project_number, {
        "design_number": design_number,
    })


@mcp.tool()
def harnoq_design_case_reopen(project_number: str, design_number: str) -> dict:
    """Reopen a locked design case (DESIGN-*): confirmed -> draft.
    This is the ONLY way to edit a confirmed DESIGN-* —
    use it both to revise an approved design and to send one back for rework
    (there is no separate "reject" tool; reopening it and telling the user
    why covers that)."""
    return _call("/skills/design-case-reopen", project_number, {
        "design_number": design_number,
    })


@mcp.tool()
def harnoq_design_case_realign_basis(project_number: str, design_number: str) -> dict:
    """Record that a suspect DESIGN-* still holds. Confirming a design records
    which version of its FEATURE-* it was confirmed against; when that is
    revised later the design reads back with suspect=true and a
    suspect_upstream list. Call this ONLY after reading
    that upstream change and judging the design still stands as written — it
    records that judgement. If it does not stand, reopen and revise instead.
    suspect gates nothing on its own, so never call this merely to clear it."""
    return _call("/skills/design-case-realign-basis", project_number, {
        "design_number": design_number,
    })


@mcp.tool()
def harnoq_design_case_delete(project_number: str, design_number: str) -> dict:
    """Delete a design case (DESIGN-*). Soft delete — it goes to the recycle
    bin and can be restored with harnoq_design_case_restore; it is not
    immediately unrecoverable. "不做了" is deletion, not a status — always get
    explicit user confirmation before calling this."""
    return _call("/skills/design-case-delete", project_number, {
        "design_number": design_number,
    })


@mcp.tool()
def harnoq_design_case_restore(project_number: str, design_number: str) -> dict:
    """Restore a deleted design case (DESIGN-*) from the recycle bin."""
    return _call("/skills/design-case-restore", project_number, {
        "design_number": design_number,
    })


# --- tasks created directly under a design case (DESIGN-* -> TASK-*) -------

@mcp.tool()
def harnoq_task_create_from_sources(
    project_number: str,
    source_kind: Annotated[str, Field(description="change_request")],
    title: Annotated[str, Field(description="Short task title. Plain text.")],
    task_type: Annotated[str, Field(description="feature | bug_fix | test | refactor | infrastructure | doc | mock_env | mock_data")],
    goal: Annotated[str, Field(description=f"What the task builds. {MD_CLAUSE}")],
    acceptance_criteria: Annotated[str, Field(description=f"How QA proves the task. {MD_CLAUSE}")],
    constraints: Annotated[str, Field(description=f"Task boundaries. {MD_CLAUSE}")],
    non_goals: Annotated[str, Field(description=f"Explicitly excluded work. {MD_CLAUSE}")],
    feature_number: Annotated[str | None, Field(description="Owning FEATURE-* for change requests.")] = None,
    change_request_numbers: Annotated[list[str] | None, Field(description="Approved CR-* source numbers for change-request tasks.")] = None,
    priority: str | None = None,
    complexity: str | None = None,
    risk_level: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict:
    """Create one source-backed TASK atomically; no intermediate DESIGN is required."""
    return _call("/skills/task-create-from-sources", project_number, {
        "source_kind": source_kind,
        "feature_number": feature_number,
        "title": title,
        "task_type": task_type,
        "change_request_numbers": change_request_numbers or [],
        "goal": goal,
        "acceptance_criteria": acceptance_criteria,
        "constraints": constraints,
        "non_goals": non_goals,
        "priority": priority,
        "complexity": complexity,
        "risk_level": risk_level,
    }, extra_fields)

@mcp.tool()
def harnoq_task_create(
    project_number: str, design_number: str,
    title: Annotated[str, Field(description="Short label for this task. Plain text.")],
    task_type: Annotated[str, Field(
        description="Plain text enum: feature, infrastructure, bug_fix, refactor, test, doc, mock_env, mock_data.",
    )],
    goal: Annotated[str, Field(
        description=f"What this task BUILDS, concrete enough to start coding from. {MD_CLAUSE}",
    )],
    acceptance_criteria: Annotated[str, Field(
        description=f"For a design-backed TASK, the linked DESIGN's acceptance_criteria is binding; for a source-backed TASK, these are its own binding acceptance criteria. QA judges the applicable binding contract, not invented extra scope. {MD_CLAUSE}",
    )],
    constraints: Annotated[str, Field(
        description=f"Real bounds the task must honor, pulled from the design. {MD_CLAUSE}",
    )],
    non_goals: Annotated[str, Field(
        description=f"What this task explicitly does NOT do. {MD_CLAUSE}",
    )],
    priority: Annotated[str | None, Field(description="Plain text enum.")] = None,
    complexity: Annotated[str | None, Field(description="Plain text enum.")] = None,
    risk_level: Annotated[str | None, Field(description="Plain text enum.")] = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict:
    """Create a claimable TASK-* directly under a confirmed design case
    (DESIGN-*). All of goal, acceptance_criteria, constraints and non_goals
    are required and must be non-empty (skill guard). No intermediate object
    and no separate generation step — this one call produces the real task,
    stored 'claimable'. DESIGN→TASK is strictly one-to-one; never call this
    again for the SAME design to split or supplement work. New scope goes
    through an approved CR source-backed TASK. Delivery verification stays on
    INTEG-*/SYS-* stage items. The task's content fields stay editable while
    it is unclaimed; claiming it locks them. Markdown fields (goal,
    acceptance_criteria, constraints, non_goals): use real line breaks, never
    the literal characters backslash-n."""
    return _call("/skills/design-case-create-task", project_number, {
        "design_number": design_number, "title": title, "task_type": task_type,
        "goal": goal, "acceptance_criteria": acceptance_criteria,
        "constraints": constraints, "non_goals": non_goals,
        "priority": priority, "complexity": complexity, "risk_level": risk_level,
    }, extra_fields)
