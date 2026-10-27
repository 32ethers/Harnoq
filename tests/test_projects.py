"""Project API tests."""


def test_create_project(client):
    response = client.post("/projects", json={"name": "Harnoq", "description": "dev system"})
    assert response.status_code == 201
    data = response.get_json()
    assert data["id"] > 0
    assert data["name"] == "Harnoq"
    assert data["description"] == "dev system"
    assert data["status"] == "active"
    assert data["created_at"] is not None
    assert data["updated_at"] is not None


def test_create_project_requires_name(client):
    response = client.post("/projects", json={"description": "no name"})
    assert response.status_code == 400


def test_list_projects(client):
    client.post("/projects", json={"name": "A"})
    client.post("/projects", json={"name": "B"})
    response = client.get("/projects")
    assert response.status_code == 200
    names = [project["name"] for project in response.get_json()]
    assert names == ["A", "B"]


def test_get_project(client):
    created = client.post("/projects", json={"name": "X"}).get_json()
    response = client.get(f"/projects/{created['id']}")
    assert response.status_code == 200
    assert response.get_json()["name"] == "X"


def test_get_project_not_found(client):
    response = client.get("/projects/9999")
    assert response.status_code == 404


def test_update_project(client):
    created = client.post("/projects", json={"name": "X"}).get_json()
    response = client.patch(
        f"/projects/{created['id']}", json={"name": "Y", "status": "archived"}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["name"] == "Y"
    assert data["status"] == "archived"


def test_update_project_invalid_status(client):
    created = client.post("/projects", json={"name": "X"}).get_json()
    response = client.patch(f"/projects/{created['id']}", json={"status": "nope"})
    assert response.status_code == 400
