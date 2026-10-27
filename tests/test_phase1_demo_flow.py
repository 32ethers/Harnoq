"""P1.9 capstone: the full Phase 1 closed-loop demo as an integration test.

Asserts that ``run_phase1_demo`` walks every main-line transition from a
SourceRequirement through ``completed`` offline. The manual PR model removed all
direct git-hosting-provider integration: PR linking/merging is pure manual
record-keeping, driven through the same HTTP client as every other step —
there is nothing left to stub or inject. This is the same demo the
``flask demo-phase1`` CLI command runs, so the CLI and the test share one
source of truth. CI is not a per-task gate (see docs/cn/design-evolution.md §8).
"""
from app.demo.phase1_flow import run_phase1_demo
from app.repositories import pull_request_repo


# The full happy-path task-status sequence the demo must produce, in order.
_EXPECTED_STATUS_SEQUENCE = [
    "claimable",
    "claimed",
    "pre_reviewing",
    "ready_for_development",
    "in_progress",  # start_coding opens the build unit, once
    "in_progress",  # coder pass: runs are evidence, they do not move the task
    "in_progress",  # qa pass: same — qa_passed=True is what changed
    "ready_for_pr",  # mark_ready_for_pr
    "ready_for_pr",  # link_pr: PullRequestRecord.status carries pr_created now
    "ready_for_pr",  # mark_pr_merged: PullRequestRecord.status carries merged now
    "completed",
]


def _status_sequence(steps):
    """The task-status sequence, starting at task generation.

    Earlier steps carry non-task statuses, so we slice from ``create_task``
    onward and drop None statuses.
    """
    task_steps = [s for s in steps if _is_task_step(s["step"])]
    return [s["status"] for s in task_steps if s["status"] is not None]


# Steps that mutate (or read) the DevelopmentTask status. Everything before
# create_task is requirement/design setup and is excluded from the task
# status sequence asserted by the demo.
_TASK_STEPS = {
    "create_task", "claim_task", "start_pre_review", "approve_pre_review",
    "start_coding", "coder_run_passed",
    "qa_run_passed", "mark_ready_for_pr",
    "link_pr", "mark_pr_merged", "mark_completed",
}


def _is_task_step(label: str) -> bool:
    return label in _TASK_STEPS


def test_phase1_demo_reaches_completed_with_full_status_sequence(app, client):
    steps = run_phase1_demo(client)

    # Every step succeeded.
    assert all(s["ok"] for s in steps), steps

    # The task-status sequence matches the documented happy path.
    statuses = _status_sequence(steps)
    assert _EXPECTED_STATUS_SEQUENCE == statuses
    assert statuses[-1] == "completed"


def test_phase1_demo_generates_pr_record(app, client):
    steps = run_phase1_demo(client)
    # The task id is the one created in create_task; recover it from the DB.
    # There is exactly one task in this isolated DB.
    from app.repositories import development_task_repo

    with app.app_context():
        tasks = development_task_repo.list_by_project(1)
        assert len(tasks) == 1
        task_id = tasks[0].id

        # A PullRequestRecord exists and reached "merged".
        pr = pull_request_repo.get_by_task(task_id)
        assert pr is not None
        assert pr.status == "merged"
        assert pr.pr_number == 7

    # The final step is the completed transition.
    assert steps[-1] == {"step": "mark_completed", "status": "completed", "ok": True}
