"""Stage-item forms and change-request task controls stay distinct."""


def test_delivery_pages_have_direct_evidence_fields_without_task_creation(client):
    project = client.post("/projects", json={"name": "P"}).get_json()
    for stage in ("integration", "system"):
        body = client.get(f"/ui/projects/{project['id']}/{stage}").get_data(as_text=True)
        assert "evidence_command" in body
        assert "verified_revision" in body
        assert "repair_record" in body
        assert "Create a task from selected items" not in body
    change_requests = client.get(f"/ui/projects/{project['id']}/change-requests").get_data(as_text=True)
    assert "Create a task from selected approved changes" in change_requests
