from peewee import BooleanField, CharField, DateTimeField, ForeignKeyField, TextField

from app.db.base import SoftDeleteModel, TimestampedModel
from app.core.time import utcnow

_AUDIT_LABELS = {
    "auth.login": "登录", "auth.logout": "退出登录", "auth.change_password": "修改密码",
    "auth.create_token": "创建凭证", "auth.revoke_token": "撤销凭证",
    "users.create_user": "添加用户", "users.update_user": "编辑用户", "users.delete_user": "删除用户",
    "users.assign_task": "分配任务", "users.assign_task_by_number": "分配任务",
    "users.assign_requirement": "分配需求", "users.assign_requirement_by_number": "分配需求",
    "users.assign_delivery_item": "分配事项", "users.assign_delivery_item_by_number": "分配事项",
    "claim_task": "认领任务", "release_task": "放弃认领", "start_pre_review": "开始预审",
    "approve_pre_review": "通过预审", "reject_pre_review": "预审退回",
    "start_coding": "开始开发", "record_agent_run": "记录 Agent 结果",
    "mark_ready_for_pr": "开发验收通过", "link_pr": "关联 PR", "mark_pr_merged": "确认 PR 已合并",
    "mark_completed": "完成任务", "reopen_completed_task": "打回重做",
    "project_context": "查看项目上下文", "task_progress": "查看任务进度",
}


class User(SoftDeleteModel):
    user_number = CharField(unique=True)
    username = CharField(unique=True)
    display_name = CharField()
    password_hash = TextField()
    role = CharField(default="member")
    is_active = BooleanField(default=True)

    class Meta:
        table_name = "harnoq_user"

    def to_dict(self):
        return {
            "user_number": self.user_number,
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
            "is_active": self.is_active,
            "deleted": self.deleted_at is not None,
            "created_at": self.created_at.isoformat(),
        }


class AccessCredential(TimestampedModel):
    user = ForeignKeyField(User, backref="credentials", on_delete="RESTRICT")
    jti = CharField(unique=True)
    name = CharField(default="")
    kind = CharField()
    expires_at = DateTimeField()
    revoked_at = DateTimeField(null=True)

    class Meta:
        table_name = "access_credential"

    def to_dict(self):
        return {
            "credential_number": f"TOKEN-{self.jti}",
            "name": self.name,
            "kind": self.kind,
            "expires_at": self.expires_at.isoformat(),
            "revoked": self.revoked_at is not None,
            "expired": self.expires_at <= utcnow(),
        }


class AuditRecord(TimestampedModel):
    actor = ForeignKeyField(User, backref="audit_records", on_delete="RESTRICT")
    action = CharField()
    target_number = CharField(default="")
    detail = TextField(default="")

    class Meta:
        table_name = "audit_record"

    def to_dict(self):
        suffix = self.action.rsplit(".", 1)[-1]
        label = _AUDIT_LABELS.get(self.action, _AUDIT_LABELS.get(suffix))
        if label is None:
            label = next((title for verb, title in (("create", "新增内容"), ("update", "更新内容"),
                ("delete", "删除内容"), ("restore", "恢复内容"), ("confirm", "确认内容"),
                ("reopen", "重新打开")) if verb in suffix), "项目操作")
        return {
            "actor_number": self.actor.user_number,
            "actor_name": self.actor.display_name,
            "action": self.action,
            "action_label": label,
            "target_number": self.target_number,
            "detail": self.detail,
            "created_at": self.created_at.isoformat(),
        }


class LoginFailure(TimestampedModel):
    username = CharField(index=True)
    remote_address = CharField(index=True)

    class Meta:
        table_name = "login_failure"
