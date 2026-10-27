"""A change request whose one decision lands in more than one place.

The impact analysis is DERIVED from the targets, so the set of targets is the
set of things that can be found. A live request revised two designs, recorded
one, and the second design's already-completed task never appeared in the
blast radius at all — not because anyone forgot to mention it, but because the
record had nowhere to say the second design was being changed.
"""
from tests.test_change_requests import (
    _confirm_design,
    _cr,
    _design,
    _feature,
    _project,
    _task,
)


def _two_designs_with_tasks(client):
    project = _project(client)
    feature = _feature(client, project["id"])
    first = _design(client, project["id"], feature["id"])
    second = _design(client, project["id"], feature["id"])
    _confirm_design(client, first["id"])
    _confirm_design(client, second["id"])
    return project, first, second, _task(client, first["id"]), _task(client, second["id"])


def _numbers(impact):
    return sorted(entry["object_number"] for entry in impact)


def test_impact_covers_every_target_not_just_the_primary(client):
    project, first, second, task_one, task_two = _two_designs_with_tasks(client)
    cr = _cr(
        client, project["id"],
        target_type="design_case", target_id=first["id"],
        reason="一个决定同时改两个设计",
        also_changes=[{"target_type": "design_case", "target_id": second["id"]}],
    ).get_json()

    client.post(f"/change-requests/{cr['id']}/analyze")
    analysed = client.get(f"/change-requests/{cr['id']}").get_json()
    assert _numbers(analysed["impact"]) == sorted(
        [task_one["task_number"], task_two["task_number"]]
    )


def test_without_the_second_target_its_task_is_invisible(client):
    """The failure this exists to prevent, pinned as its own test: the record
    is not merely quieter, the analysis genuinely cannot see the work."""
    project, first, second, task_one, task_two = _two_designs_with_tasks(client)
    cr = _cr(
        client, project["id"],
        target_type="design_case", target_id=first["id"],
        reason="只记了一个目标",
    ).get_json()

    client.post(f"/change-requests/{cr['id']}/analyze")
    impact = client.get(f"/change-requests/{cr['id']}").get_json()["impact"]
    assert _numbers(impact) == [task_one["task_number"]]
    assert task_two["task_number"] not in _numbers(impact)


def test_a_task_shared_by_two_targets_is_reported_once(client):
    project = _project(client)
    feature = _feature(client, project["id"])
    design = _design(client, project["id"], feature["id"])
    _confirm_design(client, design["id"])
    task = _task(client, design["id"])
    cr = _cr(
        client, project["id"],
        target_type="design_case", target_id=design["id"],
        reason="主目标和 also_changes 指向同一个任务的两端",
        also_changes=[{"target_type": "development_task", "target_id": task["id"]}],
    ).get_json()

    client.post(f"/change-requests/{cr['id']}/analyze")
    impact = client.get(f"/change-requests/{cr['id']}").get_json()["impact"]
    assert _numbers(impact) == [task["task_number"]]


def test_a_delivered_task_can_be_the_target_itself(client):
    """A fix to merged code that moves no specification. Filing it against a
    design it never touches leaves a record whose own resolution has to say the
    design was unaffected — a target the change does not change."""
    project = _project(client)
    feature = _feature(client, project["id"])
    design = _design(client, project["id"], feature["id"])
    _confirm_design(client, design["id"])
    task = _task(client, design["id"])

    cr = _cr(
        client, project["id"],
        target_type="development_task", target_id=task["id"],
        reason="已交付代码要修，规格不动",
    )
    assert cr.status_code == 201
    body = cr.get_json()
    assert body["target_number"] == task["task_number"]

    client.post(f"/change-requests/{body['id']}/analyze")
    impact = client.get(f"/change-requests/{body['id']}").get_json()["impact"]
    assert _numbers(impact) == [task["task_number"]]


def test_also_changes_is_validated_not_trusted(client):
    """An unreachable id here would contribute nothing to the analysis, which
    is exactly the blind spot the field exists to close — so it fails loudly."""
    project, first, second, _, _ = _two_designs_with_tasks(client)

    def attempt(also):
        return _cr(
            client, project["id"],
            target_type="design_case", target_id=first["id"],
            reason="r", also_changes=also,
        ).status_code

    assert attempt("not a list") == 400
    assert attempt([{"target_type": "design_case", "target_id": 9999}]) == 400
    assert attempt(["P1-DESIGN-2"]) == 400
    # 'new' is not something this change also rewrites — it does not exist yet.
    assert attempt([{"target_type": "new", "target_id": None}]) == 400
    # The primary target repeated would double-count nothing but reads as two.
    assert attempt([{"target_type": "design_case", "target_id": first["id"]}]) == 400


def test_the_skill_surface_takes_business_numbers(client):
    """Agents never see raw ids (编号/id 原则), so the multi-target path has to
    be reachable by number or it is not reachable by an agent at all."""
    project, first, second, task_one, task_two = _two_designs_with_tasks(client)
    pnum = project["project_number"]
    response = client.post(
        "/skills/change-request-create",
        json={
            "project_number": pnum,
            "payload": {
                "design_number": first["design_number"],
                "also_changes_numbers": [second["design_number"]],
                "reason": "一个决定改两个设计",
            },
        },
    )
    assert response.status_code == 200, response.get_json()
    number = response.get_json()["object_id"]

    analysed = client.post(
        "/skills/change-request-analyze",
        json={"project_number": pnum, "payload": {"change_request_number": number}},
    )
    assert analysed.status_code == 200, analysed.get_json()

    stored = next(
        c for c in client.get(
            f"/projects/{project['id']}/change-requests"
        ).get_json() if c["change_request_number"] == number
    )
    impact = stored["impact"]
    assert _numbers(impact) == sorted(
        [task_one["task_number"], task_two["task_number"]]
    )
