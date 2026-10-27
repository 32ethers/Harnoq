"""DevelopmentTask creation directly under a confirmed DESIGN-* (the direct-task model:
DRAFT layer dropped, create_task is one call = one claimable task)."""

CONFIRM_FIELDS = {
    "problem_statement": "Users need login.",
    "selected_solution": "Email login.",
    "selection_reason": "Smallest viable scope.",
    "risks": "Credential stuffing.",
    "test_strategy": "Auth unit tests and login integration test.",
    "acceptance_criteria": "User can log in with valid credentials.",
}


def _confirmed_design(client):
    project = client.post("/projects", json={"name": "P"}).get_json()
    feature = client.post(
        f"/projects/{project['id']}/features", json={"name": "F"}
    ).get_json()
    design = client.post(
        f"/projects/{project['id']}/design-cases", json={"feature_id": feature["id"]}
    ).get_json()
    client.patch(f"/design-cases/{design['id']}", json=CONFIRM_FIELDS)
    client.post(f"/design-cases/{design['id']}/confirm")
    return project, design


def _create_task(client, design, title="A", **overrides):
    payload = {"title": title, "task_type": "feature", "goal": f"do {title}"}
    payload.update(overrides)
    return client.post(f"/design-cases/{design['id']}/tasks", json=payload)


def test_create_task_creates_claimable_task(client):
    project, design = _confirmed_design(client)
    response = _create_task(client, design)
    assert response.status_code == 201
    task = response.get_json()
    assert task["task_number"].startswith("P1-TASK-")
    assert task["design_case_id"] == design["id"]
    assert task["source_kind"] == "design"
    assert task["status"] == "claimable"

    listed = client.get(f"/projects/{project['id']}/tasks").get_json()
    assert len(listed) == 1


def test_create_task_from_no_req_foundation_design(client):
    project = client.post("/projects", json={"name": "P"}).get_json()
    feature = client.post(
        f"/projects/{project['id']}/features",
        json={
            "name": "Platform",
            "orientation": "foundation",
            "subdomain_type": "generic",
        },
    ).get_json()
    design = client.post(
        f"/projects/{project['id']}/design-cases",
        json={"feature_id": feature["id"]},
    ).get_json()
    client.patch(f"/design-cases/{design['id']}", json=CONFIRM_FIELDS)
    client.post(f"/design-cases/{design['id']}/confirm")

    response = _create_task(
        client, design,
        title="Platform skeleton", task_type="infrastructure",
        goal="Build the thin platform path.",
        acceptance_criteria="Smoke path runs.",
        constraints="Stay within the platform design.",
        non_goals="No vertical slice UI.",
    )
    assert response.status_code == 201
    task = response.get_json()
    assert task["design_case_id"] == design["id"]
    assert task["design_number"] == design["design_number"]
    assert task["status"] == "claimable"


def test_create_task_inherits_fields(client):
    _, design = _confirmed_design(client)
    task = _create_task(client, design, title="A", goal="do A").get_json()
    assert task["title"] == "A"
    assert task["description"] == "do A"  # goal -> description
    assert task["task_type"] == "feature"


def test_create_task_requires_confirmed_design(client):
    project = client.post("/projects", json={"name": "P"}).get_json()
    feature = client.post(
        f"/projects/{project['id']}/features", json={"name": "F"}
    ).get_json()
    design = client.post(
        f"/projects/{project['id']}/design-cases", json={"feature_id": feature["id"]}
    ).get_json()
    response = _create_task(client, design)
    assert response.status_code == 409


def test_create_task_validates_enums(client):
    _, design = _confirmed_design(client)
    response = _create_task(client, design, task_type="not-a-real-type")
    assert response.status_code == 400


def test_create_task_is_one_to_one_with_design(client):
    """A DESIGN has one execution TASK; additional work uses a source task."""
    _, design = _confirmed_design(client)
    first = _create_task(client, design, title="A").get_json()
    second = _create_task(client, design, title="B")
    assert second.status_code == 409


def test_get_task_not_found(client):
    assert client.get("/tasks/9999").status_code == 404


def test_list_tasks_project_not_found(client):
    assert client.get("/projects/9999/tasks").status_code == 404


def test_create_task_always_produces_claimable_tasks(client):
    # create_task never stores a gate — every task starts "claimable" (meaning
    # "created, awaiting claim"); whether it can actually be claimed right
    # now is computed live from the Feature graph at claim time. See
    # docs/cn/design-evolution.md §5 and
    # test_feature_dependency_gating.py for the live gate itself.
    _, design = _confirmed_design(client)
    task = _create_task(client, design).get_json()
    assert task["status"] == "claimable"
