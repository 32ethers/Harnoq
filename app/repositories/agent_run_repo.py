"""Database access for AgentRun."""
from app.models.agent_run import AgentRun


def create(**fields):
    return AgentRun.create(**fields)


def list_by_task(task_id):
    return list(
        AgentRun.select()
        .where(AgentRun.task_id == task_id)
        .order_by(AgentRun.id)
    )


def latest_for_role(task_id, role):
    return (
        AgentRun.select()
        .where((AgentRun.task_id == task_id) & (AgentRun.role == role))
        .order_by(AgentRun.id.desc())
        .first()
    )
