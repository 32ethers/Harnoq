"""Phase 1 closed-loop demo driver.

``run_phase1_demo`` walks one task end-to-end from a SourceRequirement through
``completed`` against a Flask test client, exercising every state transition
in the main-line state machine. The manual PR model removed all direct
git-hosting-provider integration: Harnoq never calls a GitHub/Gitea API or
polls PR/CI status itself — the PR/merge steps here are pure manual
record-keeping (``link-pr`` with a made-up branch/PR number, then
``mark-pr-merged`` standing in for a human's confirmation on the git host),
driven through the same HTTP client as every other step. CI is not a per-task
gate (see docs/cn/design-evolution.md#quality): completion only requires coder+qa passed and a merged PR
record.

The happy-path task-status sequence the caller asserts is:

    claimable -> claimed -> pre_reviewing -> ready_for_development
              -> coding -> agent_qa -> (qa pass, stays agent_qa) -> ready_for_pr
              -> pr_created (link_pr) -> merged (mark_pr_merged) -> completed

This module doubles as an integration test (``tests/test_phase1_demo_flow.py``),
not a throwaway script.
"""
from __future__ import annotations

from typing import Any

from app.services import acceptance_criteria

# The six design fields required to confirm a design case. Mirrors the
# CONFIRM_FIELDS used across tests/test_development_task_lifecycle.py.
_DESIGN_CONFIRM_FIELDS = {
    "problem_statement": "Users need login.",
    "selected_solution": "Email login.",
    "selection_reason": "Smallest viable scope.",
    "risks": "Credential stuffing.",
    "test_strategy": "Auth unit tests and login integration test.",
    "acceptance_criteria": "User can log in with valid credentials.",
}


def _post(client, path: str, json: dict | None = None) -> Any:
    """POST helper that asserts success and returns the parsed JSON body."""
    resp = client.post(path, json=json or {})
    if resp.status_code >= 400:
        raise RuntimeError(
            f"demo POST {path} failed: {resp.status_code} {resp.get_data(as_text=True)!r}"
        )
    return resp.get_json()


def _patch(client, path: str, json: dict | None = None) -> Any:
    """PATCH helper that asserts success and returns the parsed JSON body.

    Mirrors ``_post`` so a 4xx on the design-case confirm PATCH surfaces at the
    PATCH itself rather than confusingly at the later ``/confirm`` call.
    """
    resp = client.patch(path, json=json or {})
    if resp.status_code >= 400:
        raise RuntimeError(
            f"demo PATCH {path} failed: {resp.status_code} {resp.get_data(as_text=True)!r}"
        )
    return resp.get_json()


def _record(steps: list[dict], label: str, status: str | None, ok: bool = True) -> None:
    steps.append({"step": label, "status": status, "ok": ok})


def run_phase1_demo(client) -> list[dict]:
    """Drive the full Phase 1 pipeline against ``client`` to ``completed``.

    ``client``: a Flask test client (works from the CLI via
    ``current_app.test_client()`` and from pytest's ``client`` fixture).

    Returns a list of step records ``{"step", "status", "ok"}`` in execution
    order. Raises ``RuntimeError`` on any HTTP failure so the CLI prints a clear
    failure line.
    """
    steps: list[dict] = []

    # --- Requirement pipeline: input -> feature -> design case ---------------
    # INPUT (SourceRequirement) is the sole requirement layer now, holistic
    # free text with a confirm gate. FEATURE (a DDD-lite block) and DESIGN
    # carry no formal link back to INPUT-* — see app/models/feature.py /
    # app/models/design_case.py for why.
    project = _post(client, "/projects", {"name": "Harnoq Demo"})
    _record(steps, "create_project", None)

    # Honor "需求进入系统": start from a SourceRequirement.
    source_requirement = _post(
        client,
        f"/projects/{project['id']}/source-requirements",
        {"title": "Login requirement", "source_type": "new_feature",
         "raw_content": "Users must be able to log in."},
    )
    _record(steps, "create_source_requirement", source_requirement["status"])  # draft

    feature = _post(
        client,
        f"/projects/{project['id']}/features",
        {"name": "Login feature"},
    )
    _record(steps, "create_feature", None)

    design = _post(
        client, f"/projects/{project['id']}/design-cases", {"feature_id": feature["id"]}
    )
    _patch(client, f"/design-cases/{design['id']}", json=_DESIGN_CONFIRM_FIELDS)
    confirmed_design = _post(client, f"/design-cases/{design['id']}/confirm")
    _record(steps, "confirm_design_case", confirmed_design["status"])

    # --- Create a claimable task directly under the confirmed design --------
    task = _post(
        client,
        f"/design-cases/{design['id']}/tasks",
        {"title": "Implement login", "task_type": "feature"},
    )
    _record(steps, "create_task", task["status"])  # claimable

    # --- Pre-development review ---------------------------------------------
    claimed = _post(client, f"/tasks/{task['id']}/claim", {"assignee": "alice"})
    _record(steps, "claim_task", claimed["status"])  # claimed

    reviewing = _post(client, f"/tasks/{task['id']}/start-pre-review")
    _record(steps, "start_pre_review", reviewing["status"])  # pre_reviewing

    approved = _post(
        client, f"/tasks/{task['id']}/approve-pre-review", {"reviewer": "alice"}
    )
    _record(steps, "approve_pre_review", approved["status"])  # ready_for_development

    # --- AI development loop ------------------------------------------------
    coding = _post(client, f"/tasks/{task['id']}/start-coding")
    _record(steps, "start_coding", coding["status"])  # coding

    coder_run = _post(
        client,
        f"/tasks/{task['id']}/agent-runs",
        {"role": "coder", "status": "passed", "summary": "implemented"},
    )
    _record(steps, "coder_run_passed", coder_run["task"]["status"])  # agent_qa

    # A qa verdict must carry one verdict per acceptance criterion of the
    # design it validated, plus a structured test_run record (see
    # development_task_service._qa_evidence / _qa_test_run), so the demo
    # produces the same evidence a real qa subagent would. evidence itself
    # uses the structured {tests, code, note} shape (_qa_evidence_value) that
    # a real qa run is now asked for, rather than the legacy free-text string
    # — the demo's own record should look like what it is meant to model.
    qa_run = _post(
        client,
        f"/tasks/{task['id']}/agent-runs",
        {
            "role": "qa",
            "status": "passed",
            "summary": "green and reviewed",
            "design_number": design["design_number"],
            "criteria_verdicts": [
                {
                    "criterion": criterion,
                    "verdict": "pass",
                    "evidence": {
                        "tests": ["test_login_integration"],
                        "code": [],
                        "note": "login integration test covers this path",
                    },
                }
                for criterion in acceptance_criteria.split(
                    _DESIGN_CONFIRM_FIELDS["acceptance_criteria"]
                )
            ],
            "test_run": {
                "command": "run the project's test suite",
                "exit_code": 0,
                "collected": 12,
                "passed": 12,
                "failed": 0,
                "skipped": 0,
            },
        },
    )
    _record(steps, "qa_run_passed", qa_run["task"]["status"])  # stays agent_qa

    ready = _post(client, f"/tasks/{task['id']}/mark-ready-for-pr")
    _record(steps, "mark_ready_for_pr", ready["status"])  # ready_for_pr

    # --- Manual PR record-keeping (see docs/cn/design-evolution.md#quality) ------------------------------
    # Harnoq never talks to a git host: in real use the manager runs `gh pr
    # create` itself and reports the resulting branch/number/url here.
    linked_pr = _post(
        client,
        f"/tasks/{task['id']}/link-pr",
        {"pr_number": 7, "branch_name": "feature", "pr_url": "https://example.invalid/pull/7"},
    )
    _record(steps, "link_pr", linked_pr["task"]["status"])  # stays ready_for_pr

    # A human confirms on the git host that the PR merged; the agent then
    # records that fact. No API call, no polling.
    merged = _post(client, f"/tasks/{task['id']}/mark-pr-merged")
    _record(steps, "mark_pr_merged", merged["task"]["status"])  # stays ready_for_pr

    completed = _post(client, f"/tasks/{task['id']}/mark-completed")
    _record(steps, "mark_completed", completed["status"])  # completed

    return steps
