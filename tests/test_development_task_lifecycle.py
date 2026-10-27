"""DevelopmentTask claim + pre-development review (P1.4 acceptance #1-#5)."""

CONFIRM_FIELDS = {
    "problem_statement": "Users need login.",
    "selected_solution": "Email login.",
    "selection_reason": "Smallest viable scope.",
    "risks": "Credential stuffing.",
    "test_strategy": "Auth unit tests and login integration test.",
    "acceptance_criteria": "User can log in with valid credentials.",
}


def _claimable_task(client):
    """Create a single claimable task via the full pipeline.

    INPUT (SourceRequirement) is the sole requirement layer now, so the pipeline
    is project -> FEATURE -> DESIGN -> TASK, created directly (no REQ-*
    layer, no DRAFT-* intermediate, and DesignCase carries no INPUT-* link of
    its own)."""
    project = client.post("/projects", json={"name": "P"}).get_json()
    feature = client.post(
        f"/projects/{project['id']}/features", json={"name": "F"}
    ).get_json()
    design = client.post(
        f"/projects/{project['id']}/design-cases", json={"feature_id": feature["id"]}
    ).get_json()
    client.patch(f"/design-cases/{design['id']}", json=CONFIRM_FIELDS)
    client.post(f"/design-cases/{design['id']}/confirm")
    return client.post(
        f"/design-cases/{design['id']}/tasks", json={"title": "T", "task_type": "feature"}
    ).get_json()


def test_claim_only_claimable_task(client):
    # acceptance #1: only claimable tasks can be claimed
    task = _claimable_task(client)
    assert task["status"] == "claimable"
    response = client.post(f"/tasks/{task['id']}/claim", json={"assignee": "alice"})
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "claimed"
    assert data["assignee"] == "alice"
    assert data["claimed_at"] is not None

    # already claimed -> cannot claim again
    assert (
        client.post(f"/tasks/{task['id']}/claim", json={"assignee": "bob"}).status_code
        == 409
    )


def test_claim_requires_assignee(client):
    task = _claimable_task(client)
    assert client.post(f"/tasks/{task['id']}/claim", json={}).status_code == 400


def test_upstream_feature_unsatisfied_task_cannot_be_claimed(client):
    # Task generation never stores a gate (status is "claimable" either way);
    # a task whose Feature depends on an unsatisfied upstream Feature is
    # rejected live at claim time. See
    # docs/cn/design-evolution.md §5 and
    # test_feature_dependency_gating.py for the full gate coverage.
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
    design = client.post(
        f"/projects/{project['id']}/design-cases",
        json={"feature_id": downstream["id"]},
    ).get_json()
    client.patch(f"/design-cases/{design['id']}", json=CONFIRM_FIELDS)
    client.post(f"/design-cases/{design['id']}/confirm")
    task = client.post(
        f"/design-cases/{design['id']}/tasks", json={"title": "B", "task_type": "feature"}
    ).get_json()
    assert task["status"] == "claimable"  # no stored gate — upstream not yet built
    assert (
        client.post(f"/tasks/{task['id']}/claim", json={"assignee": "x"}).status_code
        == 409
    )


def test_claimed_task_enters_pre_review(client):
    # acceptance #2: a claimed task can enter pre-review
    task = _claimable_task(client)
    client.post(f"/tasks/{task['id']}/claim", json={"assignee": "alice"})
    response = client.post(f"/tasks/{task['id']}/start-pre-review")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "pre_reviewing"


def test_start_pre_review_requires_claimed(client):
    task = _claimable_task(client)
    # not yet claimed -> 409
    assert client.post(f"/tasks/{task['id']}/start-pre-review").status_code == 409


def test_release_returns_claimed_task_to_claimable(client):
    # Handing a task back is not a statement about the design: from claimed /
    # pre_reviewing nothing has been produced, so release is the honest move
    # and reject-pre-review would write a false record.
    task = _claimable_task(client)
    client.post(f"/tasks/{task['id']}/claim", json={"assignee": "alice"})
    data = client.post(f"/tasks/{task['id']}/release").get_json()
    assert data["status"] == "claimable"
    assert data["assignee"] in (None, "")
    assert data["claimed_at"] is None
    # ...and someone else can pick it up
    assert (
        client.post(f"/tasks/{task['id']}/claim", json={"assignee": "bob"}).get_json()[
            "assignee"
        ]
        == "bob"
    )


def test_release_from_pre_reviewing(client):
    task = _claimable_task(client)
    client.post(f"/tasks/{task['id']}/claim", json={"assignee": "alice"})
    client.post(f"/tasks/{task['id']}/start-pre-review")
    assert client.post(f"/tasks/{task['id']}/release").get_json()["status"] == "claimable"


def test_release_records_a_transition_event(client):
    task = _claimable_task(client)
    client.post(f"/tasks/{task['id']}/claim", json={"assignee": "alice"})
    client.post(f"/tasks/{task['id']}/release")
    entries = client.get(f"/tasks/{task['id']}/timeline").get_json()
    releases = [
        e for e in entries if e["kind"] == "status" and "claimable" in e["title"]
    ]
    assert releases, entries
    assert any("alice" in (e.get("detail") or "") for e in releases)


def test_release_from_ready_for_development_discards_the_approval(client):
    # An approval records a specific person judging a specific task fit to
    # build. Carrying it into a claimable task would let the next holder start
    # coding on a verdict they cannot attribute, so releasing drops it and the
    # next holder pre-reviews again.
    task = _claimable_task(client)
    client.post(f"/tasks/{task['id']}/claim", json={"assignee": "alice"})
    client.post(f"/tasks/{task['id']}/start-pre-review")
    client.post(f"/tasks/{task['id']}/approve-pre-review", json={"reviewer": "alice"})
    data = client.post(f"/tasks/{task['id']}/release").get_json()
    assert data["status"] == "claimable"
    assert data["pre_reviewer"] in (None, "")
    assert data["pre_reviewed_at"] is None
    # the next holder must go through pre-review again, not straight to coding
    client.post(f"/tasks/{task['id']}/claim", json={"assignee": "bob"})
    assert client.post(f"/tasks/{task['id']}/start-coding").status_code == 409
    client.post(f"/tasks/{task['id']}/start-pre-review")
    client.post(f"/tasks/{task['id']}/approve-pre-review", json={"reviewer": "bob"})
    assert client.post(f"/tasks/{task['id']}/start-coding").status_code == 200


def test_release_refused_once_coding_started(client):
    # Work exists from in_progress on: "unclaimed, nothing started" would be a
    # false record while a branch is open. Handover there is an assignee edit.
    task = _claimable_task(client)
    assert client.post(f"/tasks/{task['id']}/release").status_code == 409  # claimable
    client.post(f"/tasks/{task['id']}/claim", json={"assignee": "alice"})
    client.post(f"/tasks/{task['id']}/start-pre-review")
    client.post(f"/tasks/{task['id']}/approve-pre-review", json={"reviewer": "alice"})
    client.post(f"/tasks/{task['id']}/start-coding")
    assert client.post(f"/tasks/{task['id']}/release").status_code == 409


def test_approve_sets_ready_for_development(client):
    # acceptance #3: approval -> ready_for_development
    task = _claimable_task(client)
    client.post(f"/tasks/{task['id']}/claim", json={"assignee": "alice"})
    client.post(f"/tasks/{task['id']}/start-pre-review")
    response = client.post(f"/tasks/{task['id']}/approve-pre-review", json={"reviewer": "alice"})
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ready_for_development"
    assert data["pre_reviewer"] == "alice"
    assert data["pre_reviewed_at"] is not None


def test_cannot_skip_review_into_ai_development(client):
    """acceptance #5: nothing reaches ready_for_development without an explicit
    approval, and nothing can be approved before it is claimed.

    What this does NOT require is a preceding ``start_pre_review``: that call
    records no actor, no time and no judgement, so demanding it gated only
    whether the manager had called a free endpoint on itself moments earlier.
    The approval is the gate.
    """
    task = _claimable_task(client)
    # Unclaimed: there is no holder whose review this could be.
    assert (
        client.post(f"/tasks/{task['id']}/approve-pre-review", json={}).status_code == 409
    )
    client.post(f"/tasks/{task['id']}/claim", json={"assignee": "alice"})
    # Claiming alone does not advance it.
    assert client.get(f"/tasks/{task['id']}").get_json()["status"] == "claimed"


def test_approve_is_accepted_straight_from_claimed(client):
    """The 409 this removes fired on the first development round of every task
    and cost a retry each time, while proving nothing about the review."""
    task = _claimable_task(client)
    client.post(f"/tasks/{task['id']}/claim", json={"assignee": "alice"})
    response = client.post(
        f"/tasks/{task['id']}/approve-pre-review", json={"reviewer": "alice"}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ready_for_development"
    assert data["pre_reviewer"] == "alice"
    assert data["pre_reviewed_at"]


def test_approve_still_works_after_start_pre_review(client):
    """start_pre_review stays available as a marker; it must not become a path
    that now fails."""
    task = _claimable_task(client)
    client.post(f"/tasks/{task['id']}/claim", json={"assignee": "alice"})
    client.post(f"/tasks/{task['id']}/start-pre-review")
    response = client.post(f"/tasks/{task['id']}/approve-pre-review", json={})
    assert response.status_code == 200
    assert response.get_json()["status"] == "ready_for_development"


def test_reject_always_goes_back_to_the_design(client):
    """Rejection has one destination — the design that produced the task — and
    that is the branch with a way home (fix, re-confirm, reopen). The old
    rejected_to_task_planning branch had none."""
    task = _claimable_task(client)
    client.post(f"/tasks/{task['id']}/claim", json={"assignee": "alice"})
    client.post(f"/tasks/{task['id']}/start-pre-review")
    response = client.post(
        f"/tasks/{task['id']}/reject-pre-review",
        json={"comment": "acceptance criteria are not verifiable", "reviewer": "alice"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "needs_revision"
    assert data["pre_review_comment"] == "acceptance criteria are not verifiable"


def test_reopen_after_design_reconfirmed(client):
    project = client.post("/projects", json={"name": "P"}).get_json()
    feature = client.post(
        f"/projects/{project['id']}/features", json={"name": "F"}
    ).get_json()
    design = client.post(
        f"/projects/{project['id']}/design-cases", json={"feature_id": feature["id"]}
    ).get_json()
    client.patch(f"/design-cases/{design['id']}", json=CONFIRM_FIELDS)
    client.post(f"/design-cases/{design['id']}/confirm")
    task = client.post(
        f"/design-cases/{design['id']}/tasks", json={"title": "T", "task_type": "feature"}
    ).get_json()
    client.post(f"/tasks/{task['id']}/claim", json={"assignee": "alice"})
    client.post(f"/tasks/{task['id']}/start-pre-review")
    client.post(
        f"/tasks/{task['id']}/reject-pre-review",
        json={"comment": "needs redesign", "reviewer": "alice"},
    )

    # design is still confirmed (only the task moved) -> reopen should work immediately
    response = client.post(f"/tasks/{task['id']}/reopen")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "claimable"
    assert data["pre_review_comment"] == ""

    # can be claimed again like a fresh task
    response = client.post(f"/tasks/{task['id']}/claim", json={"assignee": "bob"})
    assert response.status_code == 200
    assert response.get_json()["status"] == "claimed"


def test_reopen_requires_rejected_to_design_state(client):
    task = _claimable_task(client)
    assert client.post(f"/tasks/{task['id']}/reopen").status_code == 409


def test_reopen_requires_confirmed_design(client):
    project = client.post("/projects", json={"name": "P"}).get_json()
    feature = client.post(
        f"/projects/{project['id']}/features", json={"name": "F"}
    ).get_json()
    design = client.post(
        f"/projects/{project['id']}/design-cases", json={"feature_id": feature["id"]}
    ).get_json()
    client.patch(f"/design-cases/{design['id']}", json=CONFIRM_FIELDS)
    client.post(f"/design-cases/{design['id']}/confirm")
    task = client.post(
        f"/design-cases/{design['id']}/tasks", json={"title": "T", "task_type": "feature"}
    ).get_json()
    client.post(f"/tasks/{task['id']}/claim", json={"assignee": "alice"})
    client.post(f"/tasks/{task['id']}/start-pre-review")
    client.post(
        f"/tasks/{task['id']}/reject-pre-review",
        json={"comment": "needs redesign", "reviewer": "alice"},
    )
    # design gets reopened for editing -> no longer confirmed
    client.post(f"/design-cases/{design['id']}/reopen")
    assert client.post(f"/tasks/{task['id']}/reopen").status_code == 409


def test_reject_requires_a_comment(client):
    """The reason is the only thing a rejection must carry — it is what the
    designer works from."""
    task = _claimable_task(client)
    client.post(f"/tasks/{task['id']}/claim", json={"assignee": "alice"})
    client.post(f"/tasks/{task['id']}/start-pre-review")
    assert (
        client.post(f"/tasks/{task['id']}/reject-pre-review", json={}).status_code
        == 400
    )
    assert (
        client.post(
            f"/tasks/{task['id']}/reject-pre-review", json={"comment": "   "}
        ).status_code
        == 400
    )


def test_reject_is_accepted_straight_from_claimed(client):
    """A rejection is no more dependent on the marker call than an approval."""
    task = _claimable_task(client)
    client.post(f"/tasks/{task['id']}/claim", json={"assignee": "alice"})
    response = client.post(
        f"/tasks/{task['id']}/reject-pre-review", json={"comment": "x"}
    )
    assert response.status_code == 200
    assert response.get_json()["status"] == "needs_revision"


def test_reject_requires_a_claim_first(client):
    task = _claimable_task(client)
    assert (
        client.post(
            f"/tasks/{task['id']}/reject-pre-review",
            json={"comment": "x"},
        ).status_code
        == 409
    )


# --- reject-to-design: a design defect found once coding is under way ------
#
# pre_reviewing is no longer reachable once start_coding has run, so a task
# whose design turns out to be wrong mid-build previously had nowhere to go.
# reject-to-design is the entrance reject-pre-review cannot reach from there;
# reopen (reopen_rejected_to_design) is the way home both share.


def _in_progress_task(client, assignee="alice"):
    task = _claimable_task(client)
    client.post(f"/tasks/{task['id']}/claim", json={"assignee": assignee})
    client.post(f"/tasks/{task['id']}/start-pre-review")
    client.post(f"/tasks/{task['id']}/approve-pre-review", json={"reviewer": assignee})
    client.post(f"/tasks/{task['id']}/start-coding")
    return task


def test_reject_to_design_happy_path(client):
    task = _in_progress_task(client)
    response = client.post(
        f"/tasks/{task['id']}/reject-to-design",
        json={"reason": "the acceptance criterion assumes a field the chosen API never returns"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "needs_revision"


def test_reject_to_design_clears_blocking_issues_but_leaves_agent_runs(client):
    task = _in_progress_task(client)
    run = client.post(
        f"/tasks/{task['id']}/agent-runs",
        json={"role": "coder", "status": "failed", "summary": "build broke"},
    )
    assert run.status_code == 200
    assert (
        client.get(f"/tasks/{task['id']}").get_json()["blocking_issues"]
        == "build broke"
    )
    data = client.post(
        f"/tasks/{task['id']}/reject-to-design",
        json={"reason": "the design's chosen data shape cannot hold what the criterion demands"},
    ).get_json()
    assert data["blocking_issues"] == ""
    # History is not erased: the coder run stays in the timeline.
    timeline = client.get(f"/tasks/{task['id']}/timeline").get_json()
    assert any(e["title"] == "coder failed" for e in timeline)


def test_reject_to_design_leaves_pre_review_fields_untouched(client):
    """Nothing pre-reviewed anything on this path — writing the reason into
    pre_reviewer/pre_reviewed_at/pre_review_comment would misattribute it to a
    review step that never ran."""
    task = _in_progress_task(client, assignee="alice")
    before = client.get(f"/tasks/{task['id']}").get_json()
    assert before["pre_reviewer"] == "alice"
    assert before["pre_reviewed_at"] is not None
    data = client.post(
        f"/tasks/{task['id']}/reject-to-design",
        json={"reason": "the design omits the case the criterion requires"},
    ).get_json()
    assert data["pre_reviewer"] == before["pre_reviewer"]
    assert data["pre_reviewed_at"] == before["pre_reviewed_at"]
    assert data["pre_review_comment"] == ""


def test_reject_to_design_records_the_reason_in_the_timeline(client):
    task = _in_progress_task(client)
    client.post(
        f"/tasks/{task['id']}/reject-to-design",
        json={"reason": "the criterion contradicts the constraint stated two paragraphs above it"},
    )
    timeline = client.get(f"/tasks/{task['id']}/timeline").get_json()
    entries = [e for e in timeline if "needs_revision" in e["title"]]
    assert entries, timeline
    assert any(
        "contradicts the constraint" in (e.get("detail") or "") for e in entries
    )


def test_reject_to_design_requires_a_reason(client):
    task = _in_progress_task(client)
    assert client.post(f"/tasks/{task['id']}/reject-to-design", json={}).status_code == 400
    assert (
        client.post(
            f"/tasks/{task['id']}/reject-to-design", json={"reason": "   "}
        ).status_code
        == 400
    )


def test_reject_to_design_refused_from_claimable(client):
    task = _claimable_task(client)
    assert (
        client.post(
            f"/tasks/{task['id']}/reject-to-design", json={"reason": "x"}
        ).status_code
        == 409
    )


def test_reject_to_design_refused_from_rejected_to_design(client):
    task = _in_progress_task(client)
    client.post(
        f"/tasks/{task['id']}/reject-to-design", json={"reason": "first defect found"}
    )
    assert (
        client.post(
            f"/tasks/{task['id']}/reject-to-design", json={"reason": "second try"}
        ).status_code
        == 409
    )


def test_reject_to_design_refused_from_ready_for_pr(client):
    """The two-step path (withdraw-from-pr, then reject-to-design) already
    exists and already leaves its own trail — a direct entrance from
    ready_for_pr would just be a shortcut around it."""
    task = _in_progress_task(client)
    client.post(
        f"/tasks/{task['id']}/agent-runs",
        json={"role": "coder", "status": "passed", "summary": "done"},
    )
    row = client.get(f"/tasks/{task['id']}").get_json()
    from tests.test_agent_runs import _qa_evidence

    client.post(
        f"/tasks/{task['id']}/agent-runs",
        json={"role": "qa", "status": "passed", "summary": "verified"}
        | _qa_evidence(row),
    )
    ready = client.post(f"/tasks/{task['id']}/mark-ready-for-pr")
    assert ready.status_code == 200
    assert (
        client.post(
            f"/tasks/{task['id']}/reject-to-design", json={"reason": "x"}
        ).status_code
        == 409
    )


def test_reject_to_design_round_trip_back_to_claimable(client):
    """Reject from in_progress, then reopen once the design is re-confirmed
    (already confirmed here — only the task moved), landing back at
    claimable exactly like reject_pre_review's own round trip."""
    task = _in_progress_task(client)
    rejected = client.post(
        f"/tasks/{task['id']}/reject-to-design",
        json={"reason": "the design's ordering guarantee does not hold for the chosen mechanism"},
    ).get_json()
    assert rejected["status"] == "needs_revision"

    reopened = client.post(f"/tasks/{task['id']}/reopen")
    assert reopened.status_code == 200
    data = reopened.get_json()
    assert data["status"] == "claimable"

    # claimable like a fresh task
    claimed = client.post(f"/tasks/{task['id']}/claim", json={"assignee": "bob"})
    assert claimed.status_code == 200
    assert claimed.get_json()["status"] == "claimed"
