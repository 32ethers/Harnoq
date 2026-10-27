from datetime import datetime

from app.models.task_event import TaskEvent
from app.models.task_owner import TaskOwner, TaskWorkEvent
from app.services import development_task_service, task_owner_service, user_service
from tests.test_development_task_lifecycle import _claimable_task


def _user(name):
    return user_service.create({"username": name, "display_name": name.title(), "password": "1"})


def test_legacy_link_preserves_task_and_completion_dates_and_is_repeatable(app, client):
    data = _claimable_task(client)
    with app.app_context():
        user = _user("legacy_owner")
        task = development_task_service.get(data["id"])
        task.assignee, task.status = "historical-agent", "completed"
        task.save()
        timestamps = [datetime(2025, 1, 2), datetime(2025, 2, 3)]
        for timestamp in timestamps:
            TaskEvent.create(task=task, from_status="ready_for_pr", to_status="completed", created_at=timestamp)
        before = task.to_dict()
        assert task_owner_service.link_legacy_tasks(user.user_number) == {
            "user_number": user.user_number, "linked_tasks": 1, "completion_events": 2,
        }
        after = development_task_service.get(task.id).to_dict()
        assert after["assignee_number"] == user.user_number
        assert after["assignee"] == user.display_name
        assert development_task_service.get(task.id).assignee == "historical-agent"
        for field in ("status", "claimed_at", "pre_reviewer", "pre_reviewed_at", "created_at", "updated_at"):
            assert after[field] == before[field]
        completions = list(TaskWorkEvent.select().where(TaskWorkEvent.action == "completed").order_by(TaskWorkEvent.created_at))
        assert [event.created_at for event in completions] == timestamps
        assert all(event.actor_id is None and event.owner_id == user.id for event in completions)
        assert task_owner_service.workload()["users"][0]["completed_in_period"] == 1
        assert task_owner_service.workload(since=datetime(2025, 2, 1), until=datetime(2025, 3, 1))["users"][0]["completed_in_period"] == 1
        assert task_owner_service.workload(since=datetime(2026, 1, 1))["users"][0]["completed_in_period"] == 0
        assert task_owner_service.link_legacy_tasks(user.user_number)["linked_tasks"] == 0
        assert TaskWorkEvent.select().where(TaskWorkEvent.action == "completed").count() == 2


def test_legacy_link_includes_deleted_tasks_and_skips_existing_user_history(app, client):
    tasks = [_claimable_task(client) for _ in range(3)]
    with app.app_context():
        target, previous = _user("target_owner"), _user("previous_owner")
        deleted = development_task_service.get(tasks[0]["id"])
        deleted.deleted_at = datetime(2025, 3, 1)
        deleted.save()
        TaskOwner.create(task=tasks[1]["id"], user=previous)
        TaskWorkEvent.create(task=tasks[2]["id"], previous_owner=previous, action="unassigned")
        assert task_owner_service.link_legacy_tasks(target.user_number)["linked_tasks"] == 1
        assert TaskOwner.get(TaskOwner.task == deleted.id).user_id == target.id
        assert TaskOwner.get(TaskOwner.task == tasks[1]["id"]).user_id == previous.id
        assert task_owner_service.owner(tasks[2]["id"]) is None
        assert task_owner_service.workload()["users"][0]["tasks"] == []


def test_legacy_link_cli_uses_explicit_user_and_project(app, client):
    first, second = _claimable_task(client), _claimable_task(client)
    with app.app_context():
        user = _user("cli_owner")
    runner = app.test_cli_runner()
    assert runner.invoke(args=["link-legacy-tasks"]).exit_code != 0
    result = runner.invoke(args=["link-legacy-tasks", "--user", user.user_number, "--project", "P1"])
    assert result.exit_code == 0, result.output
    assert "linked 1 historical tasks" in result.output
    with app.app_context():
        assert task_owner_service.owner(first["id"]).user_id == user.id
        assert task_owner_service.owner(second["id"]) is None
        user.is_active = False
        user.save()
    result = runner.invoke(args=["link-legacy-tasks", "--user", user.user_number])
    assert result.exit_code == 1 and "inactive" in result.output
