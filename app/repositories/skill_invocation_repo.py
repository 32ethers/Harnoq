"""Database access for SkillInvocation."""
from app.models.skill_invocation import SkillInvocation


def create(**fields):
    return SkillInvocation.create(**fields)


def get(invocation_id):
    return SkillInvocation.get_or_none(SkillInvocation.id == invocation_id)


def list_by_project(project_id):
    return list(
        SkillInvocation.select()
        .where(SkillInvocation.project_id == project_id)
        .order_by(SkillInvocation.id)
    )
