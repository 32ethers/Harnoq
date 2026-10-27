"""PullRequestRecord — link_pr / mark_pr_merged / mark_completed.

The manual PR model: Harnoq never calls a git-hosting-provider API or polls PR/CI
status itself. PR linking/merging is pure manual record-keeping: an agent
records a PR's branch/number/url with link_pr; a human confirms the merge
happened on the git host; the agent then calls mark_pr_merged to record that.
mark_completed only trusts that manual merged record. CI is not a per-task
gate (see docs/cn/design-evolution.md §8): completion only requires coder+qa passed and a merged PR,
so there is no CI polling or exemption path here.
"""
import pytest

from app.core.errors import ConflictError, ValidationError
from app.repositories import pull_request_repo
from app.services import development_task_service, feature_service
from tests.test_development_tasks import CONFIRM_FIELDS
from tests.test_agent_runs import (
    _agent_run,
    _ready_for_development_task,
    _start_coding,
)


def _ready_for_pr_task(client):
    task = _ready_for_development_task(client)
    _start_coding(client, task)
    _agent_run(client, task, "coder", "passed")
    _agent_run(client, task, "qa", "passed")
    client.post(f"/tasks/{task['id']}/mark-ready-for-pr")
    return client.get(f"/tasks/{task['id']}").get_json()


def _complete_generated_task(app, client, task, project_id):
    del project_id
    client.post(f"/tasks/{task['id']}/claim", json={"assignee": "alice"})
    client.post(f"/tasks/{task['id']}/start-pre-review")
    client.post(f"/tasks/{task['id']}/approve-pre-review", json={"reviewer": "alice"})
    _start_coding(client, task)
    _agent_run(client, task, "coder", "passed")
    _agent_run(client, task, "qa", "passed")
    client.post(f"/tasks/{task['id']}/mark-ready-for-pr")
    client.post(
        f"/tasks/{task['id']}/link-pr",
        json={"pr_number": task["id"], "branch_name": f"task-{task['id']}"},
    )
    client.post(f"/tasks/{task['id']}/mark-pr-merged")
    with app.app_context():
        return development_task_service.mark_completed(task["id"])


# --- link_pr ---


def test_link_pr_requires_ready_for_pr(app, client):
    task = _ready_for_development_task(client)
    with app.app_context():
        with pytest.raises(ConflictError):
            development_task_service.link_pr(task["id"], {"pr_number": 1})


def test_link_pr_keeps_task_ready_for_pr(app, client):
    """The task stays ready_for_pr; the PullRequestRecord (not the task) is
    the source of truth for pr_created (see model docstring)."""
    task = _ready_for_pr_task(client)
    resp = client.post(
        f"/tasks/{task['id']}/link-pr",
        json={
            "pr_number": 7,
            "branch_name": "feature",
            "pr_url": "https://example.invalid/acme/widget/pull/7",
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["task"]["status"] == "ready_for_pr"
    assert data["pull_request_record"]["pr_number"] == 7
    assert data["pull_request_record"]["status"] == "pr_created"
    assert "provider" not in data["pull_request_record"]
    assert "repository_integration_id" not in data["pull_request_record"]


def test_link_pr_validates_required_fields(app, client):
    task = _ready_for_pr_task(client)
    assert client.post(f"/tasks/{task['id']}/link-pr", json={}).status_code == 400


# --- mark_pr_merged ---


def _linked_task(app, client):
    task = _ready_for_pr_task(client)
    client.post(
        f"/tasks/{task['id']}/link-pr",
        json={"pr_number": 7, "branch_name": "feature"},
    )
    return client.get(f"/tasks/{task['id']}").get_json()


def test_mark_pr_merged_requires_linked_pr(app, client):
    task = _ready_for_pr_task(client)
    with app.app_context():
        with pytest.raises(ConflictError):
            development_task_service.mark_pr_merged(task["id"])


def test_mark_pr_merged_updates_record_not_task_status(app, client):
    """merged is tracked on the PullRequestRecord, not the task (see model
    docstring) — mark_completed reads record.status, not task.status."""
    task = _linked_task(app, client)
    with app.app_context():
        updated, record = development_task_service.mark_pr_merged(task["id"])
        assert updated.status == "ready_for_pr"
        assert record.status == "merged"
        assert record.merged_at is not None


def test_mark_pr_merged_requires_ready_for_pr(app, client):
    task = _ready_for_development_task(client)
    with app.app_context():
        with pytest.raises(ConflictError):
            development_task_service.mark_pr_merged(task["id"])


# --- mark_completed ---


def test_mark_completed_requires_merged(app, client):
    task = _ready_for_pr_task(client)
    with app.app_context():
        with pytest.raises(ConflictError):
            development_task_service.mark_completed(task["id"])


def test_mark_completed_succeeds_when_pr_merged(app, client):
    task = _linked_task(app, client)
    with app.app_context():
        development_task_service.mark_pr_merged(task["id"])
        completed = development_task_service.mark_completed(task["id"])
        assert completed.status == "completed"


def test_mark_completed_satisfies_feature_for_downstream_claim(app, client):
    # Completing a Feature's only task through the full PR flow makes that
    # Feature "satisfied" (see feature_service.feature_satisfied), which is
    # what a downstream Feature's task claim checks live — there is no stored
    # gate to "unblock" (docs/cn/design-evolution.md §5).
    project = client.post("/projects", json={"name": "P"}).get_json()
    upstream = client.post(
        f"/projects/{project['id']}/features",
        json={"name": "Upstream", "orientation": "foundation", "subdomain_type": "generic"},
    ).get_json()
    downstream = client.post(
        f"/projects/{project['id']}/features",
        json={
            "name": "Downstream",
            "orientation": "foundation",
            "subdomain_type": "generic",
            "depends_on_feature_ids": [upstream["id"]],
        },
    ).get_json()
    up_design = client.post(
        f"/projects/{project['id']}/design-cases", json={"feature_id": upstream["id"]}
    ).get_json()
    client.patch(f"/design-cases/{up_design['id']}", json=CONFIRM_FIELDS)
    client.post(f"/design-cases/{up_design['id']}/confirm")
    task_a = client.post(
        f"/design-cases/{up_design['id']}/tasks", json={"title": "A", "task_type": "feature"}
    ).get_json()

    down_design = client.post(
        f"/projects/{project['id']}/design-cases", json={"feature_id": downstream["id"]}
    ).get_json()
    client.patch(f"/design-cases/{down_design['id']}", json=CONFIRM_FIELDS)
    client.post(f"/design-cases/{down_design['id']}/confirm")
    task_b = client.post(
        f"/design-cases/{down_design['id']}/tasks", json={"title": "B", "task_type": "feature"}
    ).get_json()

    assert client.post(f"/tasks/{task_b['id']}/claim", json={"assignee": "x"}).status_code == 409

    _complete_generated_task(app, client, task_a, project["id"])

    assert client.post(f"/tasks/{task_b['id']}/claim", json={"assignee": "x"}).status_code == 200


# --- HTTP layer for mark-pr-merged / mark-completed (link-pr covered above) ---


def test_api_mark_pr_merged_endpoint(app, client):
    task = _linked_task(app, client)
    resp = client.post(f"/tasks/{task['id']}/mark-pr-merged")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["task"]["status"] == "ready_for_pr"
    assert data["pull_request_record"]["status"] == "merged"


def test_api_mark_pr_merged_conflict_without_linked_pr(client):
    task = _ready_for_pr_task(client)
    resp = client.post(f"/tasks/{task['id']}/mark-pr-merged")
    assert resp.status_code == 409


def test_api_mark_completed_endpoint(app, client):
    task = _linked_task(app, client)
    client.post(f"/tasks/{task['id']}/mark-pr-merged")
    resp = client.post(f"/tasks/{task['id']}/mark-completed")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "completed"


def test_api_mark_completed_conflict_when_not_merged(client):
    task = _ready_for_pr_task(client)
    resp = client.post(f"/tasks/{task['id']}/mark-completed")
    assert resp.status_code == 409


# --- reopen_completed (rework after integration testing) ---


def _completed_task(app, client):
    """Drive a task all the way to completed with PR #1 merged."""
    task = _ready_for_pr_task(client)
    client.post(
        f"/tasks/{task['id']}/link-pr",
        json={"pr_number": 1, "branch_name": "task-1-first-attempt"},
    )
    client.post(f"/tasks/{task['id']}/mark-pr-merged")
    resp = client.post(f"/tasks/{task['id']}/mark-completed")
    assert resp.get_json()["status"] == "completed"
    return task


def test_reopen_completed_returns_task_to_claimable_and_clears_the_gate(app, client):
    task = _completed_task(app, client)
    with app.app_context():
        reopened = development_task_service.reopen_completed(
            task["id"], {"reason": "integration test: logging module absent"}
        )
    assert reopened.status == "claimable"
    # The previous attempt's verdict must not carry over — otherwise the retry
    # inherits a passed gate it never earned.
    assert reopened.assignee is None
    assert reopened.blocking_issues == "integration test: logging module absent"


def test_reopen_completed_requires_a_reason(app, client):
    task = _completed_task(app, client)
    with app.app_context():
        with pytest.raises(ValidationError):
            development_task_service.reopen_completed(task["id"], {"reason": "  "})
        with pytest.raises(ValidationError):
            development_task_service.reopen_completed(task["id"], {})


def test_reopen_completed_rejects_a_task_that_is_not_completed(app, client):
    task = _ready_for_pr_task(client)
    with app.app_context():
        with pytest.raises(ConflictError):
            development_task_service.reopen_completed(task["id"], {"reason": "x"})


def test_reopen_completed_keeps_the_merged_pr_and_the_retry_adds_a_new_one(app, client):
    """The first merge really happened; a retry must not overwrite that row."""
    task = _completed_task(app, client)
    with app.app_context():
        development_task_service.reopen_completed(task["id"], {"reason": "regressed"})

    client.post(f"/tasks/{task['id']}/claim", json={"assignee": "bob"})
    client.post(f"/tasks/{task['id']}/start-pre-review")
    client.post(f"/tasks/{task['id']}/approve-pre-review", json={"reviewer": "bob"})
    _start_coding(client, task)
    _agent_run(client, task, "coder", "passed")
    _agent_run(client, task, "qa", "passed")
    client.post(f"/tasks/{task['id']}/mark-ready-for-pr")
    client.post(
        f"/tasks/{task['id']}/link-pr",
        json={"pr_number": 2, "branch_name": "task-1-rework"},
    )

    with app.app_context():
        history = pull_request_repo.list_by_task(task["id"])
        assert [r.pr_number for r in history] == [1, 2]
        assert history[0].status == "merged"
        assert history[0].merged_at is not None
        # Gating reads the attempt in flight, not the older merge.
        assert pull_request_repo.get_by_task(task["id"]).pr_number == 2


def test_reopen_completed_unsatisfies_the_feature(app, client):
    """A retracted foundation must re-block downstream claims."""
    task = _completed_task(app, client)
    with app.app_context():
        feature_id = feature_service._feature_id_of_task(
            development_task_service.get(task["id"])
        )
        assert feature_service.feature_satisfied(feature_id) is True
        development_task_service.reopen_completed(task["id"], {"reason": "regressed"})
        assert feature_service.feature_satisfied(feature_id) is False


def test_api_reopen_completed_endpoint_and_reason_validation(app, client):
    task = _completed_task(app, client)
    assert client.post(f"/tasks/{task['id']}/reopen-completed", json={}).status_code == 400
    resp = client.post(
        f"/tasks/{task['id']}/reopen-completed", json={"reason": "integration failure"}
    )
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "claimable"


def test_timeline_records_every_status_transition_in_order(app, client):
    """The whole point: reconstruct how a task actually moved.

    Asserted as the exact transition sequence rather than a count, because a
    missing step is the failure mode — every status write goes through
    _save_transition precisely so a thirteenth transition site cannot be added
    without its event.
    """
    task = _completed_task(app, client)
    with app.app_context():
        development_task_service.reopen_completed(
            task["id"], {"reason": "集成测试：日志模块缺失"}
        )
        entries = development_task_service.timeline(task["id"])

    moves = [e["title"] for e in entries if e["kind"] == "status"]
    assert moves == [
        "claimable → claimed",
        "claimed → pre_reviewing",
        "pre_reviewing → ready_for_development",
        "ready_for_development → in_progress",
        # No per-round transitions: coder and qa rounds are AgentRun rows, and
        # the timeline renders them from there rather than as status churn.
        "in_progress → ready_for_pr",
        "ready_for_pr → completed",
        "completed → claimable",
    ]
    # The reopen's reason must survive onto the timeline — an unexplained
    # retraction of a completion is the thing this record exists to prevent.
    assert entries[-1]["detail"] == "集成测试：日志模块缺失"
    # Chronological across all three sources, not grouped by kind.
    stamps = [e["at"] for e in entries]
    assert stamps == sorted(stamps)


def test_timeline_merges_agent_runs_and_pull_requests(app, client):
    """A task from before migration 047 has no status rows; its agent runs and
    PRs were always stored, so the timeline must still show them rather than
    looking as if nothing happened."""
    task = _completed_task(app, client)
    with app.app_context():
        entries = development_task_service.timeline(task["id"])
    kinds = {e["kind"] for e in entries}
    assert kinds == {"status", "agent_run", "pull_request"}
    assert any(e["title"] == "coder passed" for e in entries)
    assert any(e["title"] == "PR #1 合并" for e in entries)


def test_timeline_404s_for_an_unknown_task(client):
    assert client.get("/tasks/999999/timeline").status_code == 404


def test_timeline_endpoint_returns_entries(app, client):
    task = _completed_task(app, client)
    resp = client.get(f"/tasks/{task['id']}/timeline")
    assert resp.status_code == 200
    assert [e["kind"] for e in resp.get_json()][0] == "status"


def test_reopened_task_pull_requests_stay_in_the_timeline(app, client):
    """Two reopens: all three PRs must appear, oldest first."""
    task = _completed_task(app, client)
    for attempt in (2, 3):
        with app.app_context():
            development_task_service.reopen_completed(
                task["id"], {"reason": f"integration failure {attempt}"}
            )
        client.post(f"/tasks/{task['id']}/claim", json={"assignee": "bob"})
        client.post(f"/tasks/{task['id']}/start-pre-review")
        client.post(f"/tasks/{task['id']}/approve-pre-review", json={"reviewer": "bob"})
        _start_coding(client, task)
        _agent_run(client, task, "coder", "passed")
        _agent_run(client, task, "qa", "passed")
        client.post(f"/tasks/{task['id']}/mark-ready-for-pr")
        client.post(
            f"/tasks/{task['id']}/link-pr",
            json={"pr_number": attempt, "branch_name": f"attempt-{attempt}"},
        )
        client.post(f"/tasks/{task['id']}/mark-pr-merged")
        client.post(f"/tasks/{task['id']}/mark-completed")

    with app.app_context():
        merged = [
            e["title"] for e in development_task_service.timeline(task["id"])
            if e["kind"] == "pull_request" and "合并" in e["title"]
        ]
    assert merged == ["PR #1 合并", "PR #2 合并", "PR #3 合并"]


# --- gates that were missing: design state and upstream retraction ---


def test_claim_refuses_while_the_design_is_being_revised(app, client):
    """Reopening a design to revise it must not leave its tasks claimable.

    The binding spec is the design; claiming against one mid-edit means coding
    to text that is about to change. reopen_rejected_to_design already refused
    this, claim never did.
    """
    from app.services import design_case_service

    task = _completed_task(app, client)
    with app.app_context():
        development_task_service.reopen_completed(task["id"], {"reason": "rework"})
        design_case_service.reopen(
            development_task_service.get(task["id"]).design_case_id
        )
        with pytest.raises(ConflictError):
            development_task_service.claim(task["id"], {"assignee": "alice"})


def test_reopen_completed_refuses_while_the_design_is_being_revised(app, client):
    from app.services import design_case_service

    task = _completed_task(app, client)
    with app.app_context():
        design_case_service.reopen(
            development_task_service.get(task["id"]).design_case_id
        )
        with pytest.raises(ConflictError):
            development_task_service.reopen_completed(task["id"], {"reason": "x"})


def test_completion_refuses_when_an_upstream_feature_was_retracted(app, client):
    """A task already in flight must not record completion on a withdrawn base.

    Upstream satisfaction was only ever checked at claim time, so a task
    claimed before its upstream got reopened could still be marked done —
    writing exactly the false record that let a downstream task believe an
    upstream capability existed.
    """
    from tests.test_feature_dependency_gating import (
        _confirmed_design_for_feature,
        _force_completed,
        _foundation_feature,
        _generate_one_task,
        _project,
    )

    project = _project(client)
    upstream = _foundation_feature(client, project["id"], "A")
    downstream = _foundation_feature(
        client, project["id"], "B", depends_on_feature_ids=[upstream["id"]]
    )
    up_design = _confirmed_design_for_feature(client, project["id"], upstream["id"])
    up_task = _generate_one_task(client, up_design["id"])
    down_design = _confirmed_design_for_feature(client, project["id"], downstream["id"])
    down_task = _generate_one_task(client, down_design["id"])
    _force_completed(app, up_task["id"])

    # Downstream is legitimately claimed while the upstream is satisfied.
    assert client.post(
        f"/tasks/{down_task['id']}/claim", json={"assignee": "bob"}
    ).status_code == 200

    # Now the upstream is sent back: its Feature stops being satisfied.
    with app.app_context():
        development_task_service.reopen_completed(
            up_task["id"], {"reason": "missing API"}
        )
        with pytest.raises(ConflictError):
            development_task_service.mark_ready_for_pr(down_task["id"])


# --- withdraw_from_pr: the way back out of ready_for_pr --------------------
#
# ready_for_pr used to be a one-way door. A problem noticed after the quality
# gate closed but before anything merged had no expressible response: merge
# work believed wrong and reopen it, or park the task forever.


def test_withdraw_from_pr_reopens_the_build_unit(client):
    task = _ready_for_pr_task(client)
    result = client.post(
        f"/tasks/{task['id']}/withdraw-from-pr",
        json={"reason": "DESIGN-1 要求两个进程，实现是线程"},
    ).get_json()
    assert result["status"] == "in_progress"
    assert "两个进程" in result["blocking_issues"]
    # No re-entry action: the unit is simply open again, and coder just runs.
    assert client.post(f"/tasks/{task['id']}/start-coding").status_code == 409
    assert _agent_run(client, task, "coder", "passed", "reworked").status_code == 200


def test_withdraw_from_pr_requires_a_reason(client):
    task = _ready_for_pr_task(client)
    assert client.post(
        f"/tasks/{task['id']}/withdraw-from-pr", json={}
    ).status_code == 400
    assert client.post(
        f"/tasks/{task['id']}/withdraw-from-pr", json={"reason": "   "}
    ).status_code == 400
    assert client.get(f"/tasks/{task['id']}").get_json()["status"] == "ready_for_pr"


def test_withdrawn_task_must_re_earn_the_gate_with_a_fresh_qa_run(client):
    """The stale passing qa run must not survive to vouch for reworked code."""
    task = _ready_for_pr_task(client)
    client.post(
        f"/tasks/{task['id']}/withdraw-from-pr", json={"reason": "wrong topology"}
    )
    _agent_run(client, task, "coder", "passed")
    assert client.post(f"/tasks/{task['id']}/mark-ready-for-pr").status_code == 409
    _agent_run(client, task, "qa", "passed", "re-verified")
    assert client.post(
        f"/tasks/{task['id']}/mark-ready-for-pr"
    ).get_json()["status"] == "ready_for_pr"


def test_withdraw_is_refused_once_the_pr_merged(client):
    task = _ready_for_pr_task(client)
    client.post(f"/tasks/{task['id']}/link-pr", json={"pr_number": 7, "branch_name": "b"})
    client.post(f"/tasks/{task['id']}/mark-pr-merged")
    response = client.post(
        f"/tasks/{task['id']}/withdraw-from-pr", json={"reason": "too late"}
    )
    assert response.status_code == 409
    assert "reopen_completed" in response.get_json()["error"]


def test_withdraw_is_allowed_after_link_pr_before_merge(client):
    task = _ready_for_pr_task(client)
    client.post(f"/tasks/{task['id']}/link-pr", json={"pr_number": 8, "branch_name": "b"})
    result = client.post(
        f"/tasks/{task['id']}/withdraw-from-pr", json={"reason": "reviewer found a gap"}
    )
    assert result.status_code == 200
    # The PR record is history, not erased.
    assert [
        record.pr_number
        for record in development_task_service.list_pull_requests(task["id"])
    ] == [8]


def test_withdraw_only_from_ready_for_pr(client):
    task = _ready_for_development_task(client)
    assert client.post(
        f"/tasks/{task['id']}/withdraw-from-pr", json={"reason": "x"}
    ).status_code == 409


def test_agent_can_withdraw_through_business_number_route(client):
    """Reachable from the manager's own surface, not just internal ids."""
    task = _ready_for_pr_task(client)
    project = client.get(f"/projects/{task['project_id']}").get_json()
    base = f"/projects/{project['project_number']}/tasks/{task['task_number']}"
    result = client.post(base + "/withdraw-from-pr", json={"reason": "gap"}).get_json()
    assert result["status"] == "in_progress"
    assert "id" not in result


# --- the PR of THIS attempt, not of an earlier one -------------------------
#
# get_by_task returns the newest row, which after a reopen is still the
# previous attempt's merged PR until link_pr runs again. Two gates read it
# during that window and both answered about the wrong attempt (TASK-2,
# 2026-07-22).


def _reopened_at_ready_for_pr(app, client):
    """A task on its SECOND attempt, at ready_for_pr, carrying a merged PR
    from the first attempt and none of its own."""
    task = _completed_task(app, client)
    client.post(f"/tasks/{task['id']}/reopen-completed", json={"reason": "整合测试失败"})
    client.post(f"/tasks/{task['id']}/claim", json={"assignee": "alice"})
    client.post(f"/tasks/{task['id']}/start-pre-review")
    client.post(f"/tasks/{task['id']}/approve-pre-review", json={"reviewer": "alice"})
    _start_coding(client, task)
    _agent_run(client, task, "coder", "passed")
    _agent_run(client, task, "qa", "passed")
    assert client.post(
        f"/tasks/{task['id']}/mark-ready-for-pr"
    ).get_json()["status"] == "ready_for_pr"
    return task


def test_completion_refuses_a_pr_merged_before_this_attempt(app, client):
    """The observed failure: a task recorded complete whose rework was never
    branched, on the strength of a PR merged before the reopen."""
    task = _reopened_at_ready_for_pr(app, client)
    response = client.post(f"/tasks/{task['id']}/mark-completed")
    assert response.status_code == 409
    assert "this attempt" in response.get_json()["error"]
    assert client.get(f"/tasks/{task['id']}").get_json()["status"] == "ready_for_pr"


def test_completion_succeeds_once_this_attempt_has_its_own_merged_pr(app, client):
    task = _reopened_at_ready_for_pr(app, client)
    client.post(
        f"/tasks/{task['id']}/link-pr",
        json={"pr_number": 2, "branch_name": "task-second-attempt"},
    )
    client.post(f"/tasks/{task['id']}/mark-pr-merged")
    assert client.post(
        f"/tasks/{task['id']}/mark-completed"
    ).get_json()["status"] == "completed"
    # Both attempts stay in the history.
    assert [
        record.pr_number
        for record in development_task_service.list_pull_requests(task["id"])
    ] == [1, 2]


def test_withdraw_is_allowed_when_the_merged_pr_predates_this_attempt(app, client):
    """The mirror-image failure: withdrawal locked out of exactly the tasks
    that need it, because every reworked task carries an older merged PR."""
    task = _reopened_at_ready_for_pr(app, client)
    response = client.post(
        f"/tasks/{task['id']}/withdraw-from-pr", json={"reason": "错误边界未随路由重置"}
    )
    assert response.status_code == 200
    assert response.get_json()["status"] == "in_progress"


def test_mark_pr_merged_refuses_an_earlier_attempts_pr(app, client):
    task = _reopened_at_ready_for_pr(app, client)
    response = client.post(f"/tasks/{task['id']}/mark-pr-merged")
    assert response.status_code == 409
    assert "this attempt" in response.get_json()["error"]
