"""An existing source task can recover after its binding contract changes."""
from tests.test_agent_tasks import _contains_internal_id, _numbers
from tests.test_development_task_lifecycle import _claimable_task


def test_design_rejection_still_requires_design_revision(client):
    task = _claimable_task(client)
    project_number, task_number = _numbers(client, task)
    base = f"/projects/{project_number}/tasks/{task_number}"
    assert client.post(f"{base}/claim", json={"assignee": "manager"}).status_code == 200
    assert client.post(f"{base}/reject-pre-review", json={
        "reviewer": "manager", "comment": "Design criteria need correction.",
    }).status_code == 200
    assert client.post(f"/design-cases/{task['design_case_id']}/reopen").status_code == 200
    assert client.post(f"{base}/update", json={"acceptance_criteria": "Weaker criteria"}).status_code == 409
    assert client.post(f"{base}/reopen").status_code == 409


def test_unclaimed_contract_remains_editable(client):
    task = _claimable_task(client)
    project_number, task_number = _numbers(client, task)
    base = f"/projects/{project_number}/tasks/{task_number}"
    response = client.post(f"{base}/update", json={"title": "Clarified title", "priority": "high"})
    assert response.status_code == 200
    assert response.get_json()["status"] == "claimable"
    assert response.get_json()["title"] == "Clarified title"
    assert response.get_json()["priority"] == "high"
