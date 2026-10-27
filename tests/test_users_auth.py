import asyncio
from datetime import timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest

from app.core.time import utcnow
from app.models.task_owner import TaskOwner, TaskWorkEvent
from app.models.user import AuditRecord, LoginFailure
from app.models.skill_invocation import SkillInvocation
from app.services import development_task_service, user_service
from tests.test_development_task_lifecycle import _claimable_task


PASSWORD = "a-test-password-123"


@pytest.fixture
def identities(app):
    with app.app_context():
        users = {
            name: user_service.create({"username": name, "display_name": name.title(),
                                       "password": PASSWORD, "role": "admin" if name == "admin" else "member"})
            for name in ("admin", "alice", "other")
        }
    app.config["HARNOQ_CONFIG"].auth_required = True
    return users


def _client(app, username):
    client = app.test_client()
    response = client.post("/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.get_json()
    client.environ_base["HTTP_AUTHORIZATION"] = "Bearer " + response.get_json()["access_token"]
    return client, response.get_json()


def test_all_surfaces_require_authentication(app, identities):
    client = app.test_client()
    for path in ("/projects", "/users", "/users/workload", "/projects/P1/tasks/P1-TASK-1", "/skills/project-context"):
        response = client.post(path, json={}) if path.startswith("/skills/") else client.get(path)
        assert response.status_code == 401
    assert client.get("/ui/projects").status_code == 302
    assert client.get("/ui/login").status_code == 200
    assert client.get("/healthz").status_code == 200


def test_jwt_expiry_signature_and_algorithm_are_checked(app, identities):
    client, login = _client(app, "alice")
    assert client.get("/auth/me").get_json()["user"]["user_number"] == identities["alice"].user_number
    secret = app.config["JWT_SECRET"]
    claims = jwt.decode(login["access_token"], secret, algorithms=["HS256"], audience="harnoq", issuer="harnoq")
    expired = claims | {"exp": int((utcnow() - timedelta(seconds=1)).replace(tzinfo=timezone.utc).timestamp())}
    for token in (jwt.encode(expired, secret, algorithm="HS256"),
                  jwt.encode(claims, "wrong-secret-key-with-at-least-32-bytes", algorithm="HS256"),
                  jwt.encode(claims, "", algorithm="none")):
        assert client.get("/projects", headers={"Authorization": "Bearer " + token}).status_code == 401


def test_cookie_csrf_logout_and_cross_origin(app, identities):
    client = app.test_client()
    login = client.post("/auth/login", json={"username": "alice", "password": PASSWORD})
    assert "HttpOnly" in login.headers["Set-Cookie"]
    assert "SameSite=Lax" in login.headers["Set-Cookie"]
    assert client.post("/auth/logout").status_code == 403
    csrf = login.get_json()["csrf_token"]
    headers = {"X-CSRF-Token": csrf}
    assert client.post("/auth/logout", headers=headers | {"Origin": "https://untrusted.example"}).status_code == 403
    assert client.post("/auth/logout", headers=headers).status_code == 200
    assert client.get("/projects", headers={"Authorization": "Bearer " + login.get_json()["access_token"]}).status_code == 401
    assert "Access-Control-Allow-Origin" not in client.get("/auth/me", headers={"Origin": "https://untrusted.example"}).headers


def test_user_management_roles_numbers_and_last_admin(app, identities):
    admin, _ = _client(app, "admin")
    member, _ = _client(app, "alice")
    assert member.post("/users", json={}).status_code == 403
    assert member.patch("/users/USER-1", json={"role": "admin"}).status_code == 403
    response = admin.post("/users", json={"username": "newperson", "display_name": "New", "password": PASSWORD})
    assert response.status_code == 201
    assert response.get_json()["user_number"].startswith("USER-")
    for user in admin.get("/users").get_json():
        assert "id" not in user and "password_hash" not in user
    assert admin.delete("/users/" + identities["admin"].user_number).status_code == 409
    assert admin.patch("/users/" + identities["admin"].user_number, json={"role": "member"}).status_code == 409


def test_disable_and_reset_password_invalidate_existing_credentials(app, identities):
    admin, _ = _client(app, "admin")
    member, _ = _client(app, "alice")
    number = identities["alice"].user_number
    assert admin.patch("/users/" + number, json={"is_active": False}).status_code == 200
    assert member.get("/projects").status_code == 401
    assert admin.patch("/users/" + number, json={"is_active": True, "password": "a-new-password-123"}).status_code == 200
    assert member.get("/projects").status_code == 401
    assert app.test_client().post("/auth/login", json={"username": "alice", "password": PASSWORD}).status_code == 401
    assert app.test_client().post("/auth/login", json={"username": "alice", "password": "a-new-password-123"}).status_code == 200


def test_short_passwords_work_for_creation_and_password_change(app, identities):
    admin, _ = _client(app, "admin")
    data = {"username": "shortpass", "display_name": "Short", "password": "1"}
    assert admin.post("/users", json=data | {"password": ""}).status_code == 400
    assert admin.post("/users", json=data).status_code == 201
    client = app.test_client()
    login = client.post("/auth/login", json={"username": "shortpass", "password": "1"})
    assert login.status_code == 200
    client.environ_base["HTTP_AUTHORIZATION"] = "Bearer " + login.get_json()["access_token"]
    assert client.post("/auth/password", json={"current_password": "1", "password": ""}).status_code == 400
    assert client.post("/auth/password", json={"current_password": "1", "password": "2"}).status_code == 200
    assert app.test_client().post("/auth/login", json={"username": "shortpass", "password": "2"}).status_code == 200


def test_individual_api_tokens_can_be_revoked_only_by_their_owner(app, identities):
    member, _ = _client(app, "alice")
    other, _ = _client(app, "other")
    created = member.post("/auth/tokens", json={"name": "editor", "days": 2}).get_json()
    listed = member.get("/auth/tokens").get_json()
    assert "access_token" not in listed[0]
    credential_number = listed[0]["credential_number"]
    assert other.delete("/auth/tokens/" + credential_number).status_code == 404
    bearer = {"Authorization": "Bearer " + created["access_token"]}
    assert app.test_client().get("/projects", headers=bearer).status_code == 200
    assert member.delete("/auth/tokens/" + credential_number).status_code == 200
    assert app.test_client().get("/projects", headers=bearer).status_code == 401


def test_api_token_days_have_no_policy_upper_limit(app, identities):
    member, _ = _client(app, "alice")
    for days in (91, 365, 36500):
        response = member.post("/auth/tokens", json={"name": "long-lived", "days": days})
        assert response.status_code == 201
        token = response.get_json()["access_token"]
        claims = jwt.decode(token, app.config["JWT_SECRET"], algorithms=["HS256"],
                            audience="harnoq", issuer="harnoq")
        assert claims["exp"] - claims["iat"] == days * 86400
        assert app.test_client().get("/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 200
    for days in (0, -1, True, 1.5, "365", 10**20):
        assert member.post("/auth/tokens", json={"name": "invalid", "days": days}).status_code == 400
    assert len(member.get("/auth/tokens").get_json()) == 3


def test_assignment_claim_and_release_use_registered_identity(app, identities):
    admin, _ = _client(app, "admin")
    alice, _ = _client(app, "alice")
    other, _ = _client(app, "other")
    task = _claimable_task(admin)
    path = f"/tasks/{task['id']}"
    assigned = admin.post(path + "/assignee", json={"assignee_number": identities["alice"].user_number}).get_json()
    assert assigned["status"] == "claimable"
    assert other.post(path + "/claim", json={"assignee": "Alice"}).status_code == 403
    assert other.patch(path, json={"title": "tampered"}).status_code == 403
    claimed = alice.post(path + "/claim", json={"assignee": "Someone else"}).get_json()
    assert claimed["assignee_number"] == identities["alice"].user_number
    assert claimed["assignee"] == "Alice"
    assert other.post(path + "/start-coding").status_code == 403
    assert alice.post(path + "/release").status_code == 200
    assert alice.get(path).get_json()["assignee_number"] is None
    with app.app_context():
        assert TaskOwner.select().count() == 0
        assert list(TaskWorkEvent.select().order_by(TaskWorkEvent.id))[-1].action == "unassigned"


def test_cannot_assign_inactive_user_or_bypass_review(app, identities):
    admin, _ = _client(app, "admin")
    alice, _ = _client(app, "alice")
    task = _claimable_task(admin)
    path = f"/tasks/{task['id']}"
    number = identities["alice"].user_number
    assert admin.patch("/users/" + number, json={"is_active": False}).status_code == 200
    assert admin.post(path + "/assignee", json={"assignee_number": number}).status_code == 400
    assert admin.post(path + "/start-coding").status_code == 409
    assert alice.get(path).status_code == 401


def test_audit_uses_jwt_identity_and_never_stores_passwords(app, identities):
    admin, _ = _client(app, "admin")
    project = admin.post("/projects", json={"name": "Test"}).get_json()
    assert project["project_number"]
    with app.app_context():
        record = AuditRecord.select().order_by(AuditRecord.id.desc()).get()
        assert record.actor_id == identities["admin"].id
        assert record.target_number == project["project_number"]
        assert PASSWORD not in record.detail
    activity = admin.get(f"/users/{identities['admin'].user_number}/activity").get_json()
    assert activity[0]["actor_number"] == identities["admin"].user_number


def test_completion_history_keeps_owner_across_reopen_and_reassignment(app, identities):
    admin, _ = _client(app, "admin")
    task = _claimable_task(admin)
    path = f"/tasks/{task['id']}"
    alice = identities["alice"].user_number
    other = identities["other"].user_number
    admin.post(path + "/assignee", json={"assignee_number": alice})
    with app.app_context():
        model = development_task_service.get(task["id"])
        model.status = "completed"
        development_task_service._save_transition(model, "ready_for_pr")
    assert admin.post(path + "/assignee", json={"assignee_number": other}).status_code == 409
    assert admin.post(path + "/reopen-completed", json={"reason": "Acceptance criterion was not met."}).status_code == 200
    admin.post(path + "/assignee", json={"assignee_number": other})
    work = admin.get("/users/workload").get_json()
    by_number = {row["user_number"]: row for row in work["users"]}
    assert by_number[alice]["completed_in_period"] == 1
    assert by_number[other]["pending"] == 1
    assert by_number[other]["completed_in_period"] == 0
    assert admin.get("/users/workload?since=2099-01-01").get_json()["users"][1]["completed_in_period"] == 0


def test_mcp_forwarding_preserves_per_request_identity(app, identities, monkeypatch):
    from harnoq_mcp import server
    _, login = _client(app, "alice")
    request = SimpleNamespace(headers={"Authorization": "Bearer " + login["access_token"]})
    monkeypatch.setattr(server.mcp, "get_context", lambda: SimpleNamespace(request_context=SimpleNamespace(request=request)))
    monkeypatch.setattr(server, "_wsgi_app", app)
    assert server.get_harnoq("/auth/me")["user"]["user_number"] == identities["alice"].user_number
    monkeypatch.setenv("HARNOQ_API_TOKEN", "server-wide-token-must-not-be-used")
    request.headers = {}
    with pytest.raises(server.HarnoqError, match="401"):
        server.get_harnoq("/auth/me")


def test_mcp_task_lifecycle_records_jwt_owner_and_operator_through_completion(app, identities, monkeypatch):
    from harnoq_mcp import server
    from app.services import acceptance_criteria
    from tests.test_development_task_lifecycle import CONFIRM_FIELDS

    admin, _ = _client(app, "admin")
    task = _claimable_task(admin)
    _, login = _client(app, "alice")
    incoming = SimpleNamespace(headers={"Authorization": "Bearer " + login["access_token"]})
    monkeypatch.setattr(server.mcp, "get_context", lambda: SimpleNamespace(request_context=SimpleNamespace(request=incoming)))
    monkeypatch.setattr(server, "_wsgi_app", app)
    def call(action, **payload):
        return server.harnoq_task(action=action, project_number="P1", task_number=task["task_number"], **payload)

    claimed = call("claim", assignee="invented-agent")
    assert claimed["assignee_number"] == identities["alice"].user_number
    assert claimed["assignee_user"]["username"] == "alice"
    assert claimed["assignee"] == "Alice"
    _, other_login = _client(app, "other")
    incoming.headers = {"Authorization": "Bearer " + other_login["access_token"]}
    with pytest.raises(server.HarnoqError, match="403"):
        call("start_pre_review")
    incoming.headers = {"Authorization": "Bearer " + login["access_token"]}
    assert call("approve_pre_review", reviewer="invented-reviewer")["pre_reviewer"] == "Alice"
    assert call("start_coding")["status"] == "in_progress"
    call("record_agent_run", role="coder", status="passed", summary="implemented")
    call("record_agent_run", role="qa", status="passed", summary="verified",
         design_number=task["design_number"], criteria_verdicts=[
             {"criterion": clause, "verdict": "pass", "evidence": {"tests": ["test_contract"], "code": [], "note": "contract checked"}}
             for clause in acceptance_criteria.split(CONFIRM_FIELDS["acceptance_criteria"])
         ], test_run={"command": "pytest", "exit_code": 0, "collected": 1, "passed": 1, "failed": 0, "skipped": 0})
    assert call("mark_ready_for_pr")["status"] == "ready_for_pr"
    call("link_pr", branch_name="feature", pr_number=7, pr_url="https://example.invalid/pull/7")
    call("mark_pr_merged")
    completed = call("mark_completed")
    assert completed["status"] == "completed"
    assert completed["assignee_number"] == identities["alice"].user_number
    assert completed["assignee_user"]["username"] == "alice"
    with app.app_context():
        event = TaskWorkEvent.get(TaskWorkEvent.action == "completed")
        assert event.owner_id == event.actor_id == identities["alice"].id
        records = list(AuditRecord.select().where(AuditRecord.action.startswith("agent_tasks.")))
        assert len(records) == 9
        assert all(record.actor_id == identities["alice"].id for record in records)
    work = admin.get("/users/workload").get_json()
    item = next(user for user in work["users"] if user["username"] == "alice")
    assert item["completed"] == item["completed_in_period"] == 1


def test_mcp_transport_requires_auth_before_tools_are_listed(app, identities):
    from harnoq_mcp.auth import AuthenticatedMCP
    _, login = _client(app, "alice")
    calls, responses = [], []
    async def inner(scope, receive, send):
        calls.append(True)
    async def send(message):
        responses.append(message)
    middleware = AuthenticatedMCP(inner, flask_app=app)
    scope = {"type": "http", "method": "POST", "headers": []}
    asyncio.run(middleware(scope, None, send))
    assert responses[0]["status"] == 401 and not calls
    scope["headers"] = [(b"authorization", ("Bearer " + login["access_token"]).encode())]
    asyncio.run(middleware(scope, None, send))
    assert len(calls) == 1


def test_user_and_account_pages_render_for_authenticated_people(app, identities):
    admin, _ = _client(app, "admin")
    for path in ("/ui/users", "/ui/account", "/ui/projects"):
        response = admin.get(path)
        assert response.status_code == 200
        assert b'csrf-token' in response.data
    assert b'id="user-modal"' in admin.get("/ui/users").data


def test_transfer_before_coding_clears_review_and_requires_new_claim(app, identities):
    admin, _ = _client(app, "admin")
    alice, _ = _client(app, "alice")
    other, _ = _client(app, "other")
    task = _claimable_task(admin)
    path = f"/tasks/{task['id']}"
    assert alice.post(path + "/claim").status_code == 200
    assert alice.post(path + "/approve-pre-review").status_code == 200
    changed = admin.post(path + "/assignee", json={"assignee_number": identities["other"].user_number}).get_json()
    assert changed["status"] == "claimable"
    assert changed["pre_reviewer"] is None and changed["pre_reviewed_at"] is None
    assert other.post(path + "/start-coding").status_code == 409
    assert other.post(path + "/claim").status_code == 200
    assert alice.post(path + "/start-coding").status_code == 403


def test_skill_actor_is_authenticated_user_not_caller_text(app, identities):
    admin, _ = _client(app, "admin")
    task = _claimable_task(admin)
    response = admin.post("/skills/project-context", json={"project_number": "P1", "actor": "Impersonated user"})
    assert response.status_code == 200
    with app.app_context():
        record = SkillInvocation.select().order_by(SkillInvocation.id.desc()).get()
        assert record.actor == identities["admin"].user_number


def test_login_failure_limit_is_enforced_and_account_deletion_keeps_history(app, identities):
    client = app.test_client()
    with app.app_context():
        for _ in range(12):
            LoginFailure.create(username="alice", remote_address="127.0.0.1")
    assert client.post("/auth/login", json={"username": "alice", "password": PASSWORD}).status_code == 429
    admin, _ = _client(app, "admin")
    number = identities["alice"].user_number
    assert admin.delete("/users/" + number).status_code == 200
    assert admin.get(f"/users/{number}/activity").status_code == 200


def test_cli_bootstraps_administrator_without_a_default_password(app):
    app.config["HARNOQ_CONFIG"].auth_required = True
    result = app.test_cli_runner().invoke(args=["create-admin", "--username", "firstadmin", "--display-name", "First Admin"], input=PASSWORD + "\n" + PASSWORD + "\n")
    assert result.exit_code == 0, result.output
    assert "USER-1" in result.output and PASSWORD not in result.output
    client, _ = _client(app, "firstadmin")
    assert client.get("/auth/me").get_json()["user"]["role"] == "admin"


def test_new_endpoints_reject_non_object_json(app, identities):
    admin, _ = _client(app, "admin")
    for path in ("/auth/login", "/auth/tokens", "/users"):
        assert admin.post(path, json=["unexpected"]).status_code == 400


def test_explicit_allowed_origin_can_use_bearer_credentials(app, identities):
    app.config["HARNOQ_CONFIG"].allowed_origins = ("https://client.example",)
    admin, _ = _client(app, "admin")
    response = admin.post("/projects", json={"name": "Allowed"}, headers={"Origin": "https://client.example"})
    assert response.status_code == 201
    assert response.headers["Access-Control-Allow-Origin"] == "https://client.example"
    assert admin.post("/projects", json={"name": "Refused"}, headers={"Origin": "https://unknown.example"}).status_code == 403


def test_demo_requires_credentials_when_authentication_is_enabled(app, monkeypatch):
    app.config["HARNOQ_CONFIG"].auth_required = True
    monkeypatch.delenv("HARNOQ_API_TOKEN", raising=False)
    result = app.test_cli_runner().invoke(args=["demo-phase1"])
    assert result.exit_code == 1 and "HARNOQ_API_TOKEN" in result.output
