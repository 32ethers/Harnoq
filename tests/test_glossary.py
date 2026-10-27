"""Glossary CRUD, terminology invariants, and delivery tests."""
from app.models.glossary_term import GlossaryTerm


def _project(client, name="P"):
    return client.post("/projects", json={"name": name}).get_json()


def _create(client, project_id, native="照片", en="photo", **extra):
    return client.post(
        f"/projects/{project_id}/glossary-terms",
        json={
            "term_native": native,
            "term_en": en,
            "definition_en": "A user-provided photo.",
            "avoid": "ledger\nbucket",
            **extra,
        },
    )


def test_glossary_crud_and_project_local_uniqueness(client):
    first = _project(client, "First")
    second = _project(client, "Second")
    page = client.get(f"/ui/projects/{first['id']}/glossary")
    assert page.status_code == 200
    assert "Add term" in page.get_data(as_text=True)
    created = _create(client, first["id"]).get_json()
    assert created["term_native"] == "照片"
    assert created["term_en"] == "photo"

    listed = client.get(f"/projects/{first['id']}/glossary-terms")
    assert [term["id"] for term in listed.get_json()] == [created["id"]]

    updated = client.patch(
        f"/glossary-terms/{created['id']}",
        json={"term_en": "photograph", "avoid": "ledger"},
    )
    assert updated.status_code == 200
    assert updated.get_json()["term_en"] == "photograph"

    assert _create(client, first["id"], native="照片", en="image").status_code == 400
    assert _create(client, first["id"], native="图像", en="photograph").status_code == 400
    # The same native/English pair is valid in another project.
    assert _create(client, second["id"], native="照片", en="photograph").status_code == 201


def test_soft_deleted_glossary_term_can_be_recreated(client):
    project = _project(client)
    created = _create(client, project["id"]).get_json()
    assert client.delete(f"/glossary-terms/{created['id']}").status_code == 200
    recreated = _create(client, project["id"]).get_json()
    assert recreated["id"] != created["id"]


def test_glossary_cascades_on_physical_project_delete(client, app):
    project = _project(client)
    _create(client, project["id"])
    database = app.config["DATABASE"]
    database.execute_sql("DELETE FROM project WHERE id = ?", (project["id"],))
    assert GlossaryTerm.select().where(GlossaryTerm.project_id == project["id"]).count() == 0


def test_glossary_list_has_no_n_plus_one_queries(client, app, monkeypatch):
    project = _project(client)
    _create(client, project["id"])
    database = app.config["DATABASE"]
    original_execute_sql = database.execute_sql
    statements = []

    def count_queries(sql, *args, **kwargs):
        if sql.lstrip().upper().startswith("SELECT"):
            statements.append(sql)
        return original_execute_sql(sql, *args, **kwargs)

    monkeypatch.setattr(database, "execute_sql", count_queries)
    assert client.get(f"/projects/{project['id']}/glossary-terms").status_code == 200
    one_term_query_count = len(statements)
    statements.clear()
    _create(client, project["id"], native="相册", en="album")
    _create(client, project["id"], native="缩略图", en="thumbnail")
    statements.clear()  # Ignore uniqueness checks from setup writes.
    assert client.get(f"/projects/{project['id']}/glossary-terms").status_code == 200
    assert len(statements) == one_term_query_count


def test_project_context_hoists_glossary_and_mcp_skill_crud(client):
    project = _project(client)
    project_number = project["project_number"]
    created = client.post("/skills/glossary-create", json={
        "project_number": project_number,
        "term_native": "照片",
        "term_en": "photo",
        "definition_en": "A captured image.",
        "avoid": "ledger",
    })
    assert created.status_code == 200
    assert created.get_json()["object_id"] == "照片"

    context = client.post("/skills/project-context", json={"project_number": project_number})
    data = context.get_json()
    assert data["glossary"][0]["term_en"] == "photo"
    assert list(data).index("glossary") < list(data).index("project")
    assert "copy term_native and term_en exactly" in data["glossary_guidance"]

    updated = client.post("/skills/glossary-update", json={
        "project_number": project_number,
        "existing_term_native": "照片",
        "term_en": "photograph",
    })
    assert updated.status_code == 200
    deleted = client.post("/skills/glossary-delete", json={
        "project_number": project_number,
        "term_native": "照片",
    })
    assert deleted.status_code == 200
