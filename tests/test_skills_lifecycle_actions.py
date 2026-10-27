"""Skill-layer lifecycle actions: confirm/reopen for the gate entities, and
delete/restore where they exist. Every gate entity now shares the 3-state
lifecycle, so there is no mark_reviewed action anywhere (see docs/cn/design-evolution.md §6).
INPUT-* (SourceRequirement) has no skill-layer delete/restore, so only
confirm/reopen are tested for it here.

delete/restore are exercised via their business number (DESIGN-*), same as
every other skill call — restore in particular needs a number lookup that can
find a *soft-deleted* row, which the live-only get_by_number can't do (see
get_deleted_by_number in each repo/service).
"""

_DESIGN_FIELDS = {
    "problem_statement": "Users need login.",
    "selected_solution": "Email login.",
    "selection_reason": "Smallest viable scope.",
    "risks": "Credential stuffing.",
    "test_strategy": "Auth unit tests and login integration test.",
    "acceptance_criteria": "User can log in with valid credentials.",
}


def _write_body(pnum, payload, key):
    return {
        "project_number": pnum,
        "actor": "alice",
        "session_id": "s1",
        "idempotency_key": key,
        "payload": payload,
    }


def _make_project(client):
    project = client.post("/projects", json={"name": "P"}).get_json()
    return project["project_number"]


def _confirmed_design_number(client, pnum):
    feature = client.post(
        "/skills/feature-create",
        json=_write_body(pnum, {"name": "F"}, "f1"),
    ).get_json()
    design = client.post(
        "/skills/design-case-create",
        json=_write_body(pnum, {"feature_number": feature["object_id"]}, "d1"),
    ).get_json()
    design_number = design["object_id"]
    client.post(
        "/skills/design-case-update",
        json=_write_body(pnum, {"design_number": design_number, **_DESIGN_FIELDS}, "d2"),
    )
    client.post(
        "/skills/design-case-confirm",
        json=_write_body(pnum, {"design_number": design_number}, "d3"),
    )
    return design_number


def test_design_case_delete_then_restore_via_skill(client):
    pnum = _make_project(client)
    design_number = _confirmed_design_number(client, pnum)

    deleted = client.post(
        "/skills/design-case-delete",
        json=_write_body(pnum, {"design_number": design_number}, "k1"),
    ).get_json()
    assert deleted["status"] == "succeeded"

    restored = client.post(
        "/skills/design-case-restore",
        json=_write_body(pnum, {"design_number": design_number}, "k2"),
    ).get_json()
    assert restored["status"] == "succeeded"
    assert restored["object_id"] == design_number


def test_reconfirm_after_reopen_actually_persists(client):
    """The reported incident: confirm/reopen carry only {design_number}, so
    every confirm of a design has an identical payload. With the old
    payload-hash idempotency cache, a re-confirm AFTER a reopen (needed to
    merge sibling content) replayed the first confirm's cached "succeeded"
    without ever running confirm() again — the design stayed draft though the
    API said OK. Writes are no longer deduped at all (see skill_service
    module docstring), so the re-confirm must actually run and land."""
    from app.repositories import design_case_repo

    pnum = _make_project(client)
    feature = client.post(
        "/skills/feature-create",
        json=_write_body(pnum, {"name": "F"}, "k-feat"),
    ).get_json()
    design = client.post(
        "/skills/design-case-create",
        json=_write_body(pnum, {"feature_number": feature["object_id"]}, "k-create"),
    ).get_json()
    dnum = design["object_id"]
    client.post(
        "/skills/design-case-update",
        json=_write_body(pnum, {"design_number": dnum, **_DESIGN_FIELDS}, "k-upd"),
    )

    # Same key + payload for both confirms.
    confirm_body = _write_body(pnum, {"design_number": dnum}, "k-confirm")
    first = client.post("/skills/design-case-confirm", json=confirm_body).get_json()
    assert first["status"] == "succeeded"

    # Reopen to merge sibling content (drops back to draft), then re-confirm
    # with the SAME key + payload as the first confirm.
    client.post(
        "/skills/design-case-reopen",
        json=_write_body(pnum, {"design_number": dnum}, "k-reopen"),
    )
    second = client.post("/skills/design-case-confirm", json=confirm_body).get_json()
    assert second["status"] == "succeeded"

    # The write must have actually run this time — DB reflects confirmed, not a
    # replayed stale success.
    fetched = client.post(
        "/skills/design-case",
        json={"project_number": pnum, "action": "get",
              "payload": {"design_number": dnum}},
    ).get_json()
    with client.application.app_context():
        row = design_case_repo.get(fetched["design_case"]["id"])
        assert row.status == "confirmed"


def test_delete_after_restore_actually_deletes_again(client):
    """Same bug class as the confirm/reopen incident: delete/restore also
    carry only {design_number}, so a delete -> restore -> delete cycle has an
    identical payload for both deletes. With the old payload-hash idempotency
    cache, the second delete replayed the first delete's cached "succeeded"
    without re-running soft_delete — leaving the row live though the API said
    deleted. Writes are no longer deduped at all, so the second delete must
    actually run."""
    from app.repositories import design_case_repo

    pnum = _make_project(client)
    dnum = _confirmed_design_number(client, pnum)

    first_delete = client.post(
        "/skills/design-case-delete",
        json=_write_body(pnum, {"design_number": dnum}, "k-del"),
    ).get_json()
    assert first_delete["status"] == "succeeded"

    client.post(
        "/skills/design-case-restore",
        json=_write_body(pnum, {"design_number": dnum}, "k-res"),
    )

    # Same key + payload as the first delete.
    second_delete = client.post(
        "/skills/design-case-delete",
        json=_write_body(pnum, {"design_number": dnum}, "k-del"),
    ).get_json()
    assert second_delete["status"] == "succeeded"

    with client.application.app_context():
        fetched = design_case_repo.get_by_number(_project_id(pnum), dnum)
        assert fetched is None  # live lookup must NOT find it: it must be deleted again
        deleted = design_case_repo.get_deleted_by_number(_project_id(pnum), dnum)
        assert deleted is not None and deleted.deleted_at is not None


def _project_id(pnum):
    from app.repositories import project_repo

    return project_repo.get_by_number(pnum).id


def test_source_requirement_confirm_reopen_via_skill(client):
    pnum = _make_project(client)
    unit = client.post(
        "/skills/source-requirement-create",
        json=_write_body(pnum, {"title": "u", "source_type": "new_feature"}, "u1"),
    ).get_json()
    input_number = unit["object_id"]

    confirmed = client.post(
        "/skills/source-requirement-confirm",
        json=_write_body(pnum, {"source_requirement_number": input_number}, "c1"),
    ).get_json()
    assert confirmed["status"] == "succeeded"
    assert confirmed["object_id"] == input_number

    # Locked: a plain update is refused until reopened.
    blocked = client.post(
        "/skills/source-requirement-update",
        json=_write_body(pnum, {"source_requirement_number": input_number, "title": "t2"}, "u2"),
    )
    assert blocked.status_code == 409

    reopened = client.post(
        "/skills/source-requirement-reopen",
        json=_write_body(pnum, {"source_requirement_number": input_number}, "r1"),
    ).get_json()
    assert reopened["status"] == "succeeded"

    client.post(
        "/skills/source-requirement-confirm",
        json=_write_body(pnum, {"source_requirement_number": input_number}, "c2"),
    )

    # No mark_reviewed action exists on any surface — the endpoint is gone.
    gone = client.post(
        "/skills/source-requirement-mark-reviewed",
        json=_write_body(pnum, {"source_requirement_number": input_number}, "mr1"),
    )
    assert gone.status_code == 404


def test_no_entity_has_a_mark_reviewed_skill(client):
    # needs_review is retired (see docs/cn/design-evolution.md §6), so the whole action family is
    # gone from the skill surface — not just for the newest entity.
    pnum = _make_project(client)
    for path in (
        "/skills/source-requirement-mark-reviewed",
        "/skills/constitution-mark-reviewed",
        "/skills/design-case-mark-reviewed",
    ):
        response = client.post(path, json=_write_body(pnum, {}, "x1"))
        assert response.status_code == 404, path
