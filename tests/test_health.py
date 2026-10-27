from app import create_app
from app.core.config import Config


def test_healthz_ok(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["database"] == "connected"


def test_healthz_degraded_when_db_unavailable(tmp_path):
    # Parent directory does not exist -> SQLite connect fails on every request.
    bad_path = tmp_path / "missing_dir" / "test.db"
    config = Config(
        db_backend="sqlite",
        sqlite_path=str(bad_path),
        create_schema_on_start=False,
        testing=True,
    )
    app = create_app(config=config)
    client = app.test_client()

    response = client.get("/healthz")

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["status"] == "degraded"
    assert payload["database"] == "unavailable"


def test_cors_headers_present_on_cross_origin_request(client):
    response = client.get("/openapi.json", headers={"Origin": "http://10.0.0.4"})

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == "http://10.0.0.4"
    assert response.headers["Access-Control-Allow-Headers"] == (
        "Content-Type, Authorization"
    )
    assert response.headers["Access-Control-Allow-Methods"] == (
        "GET, POST, PATCH, DELETE, OPTIONS"
    )


def test_cors_preflight_returns_204_with_headers(client):
    response = client.options(
        "/skills/project-context",
        headers={
            "Origin": "http://10.0.0.4",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type, x-session-id",
        },
    )

    assert response.status_code == 204
    assert response.headers["Access-Control-Allow-Origin"] == "http://10.0.0.4"
    assert response.headers["Access-Control-Allow-Headers"] == (
        "content-type, x-session-id"
    )
    assert response.headers["Access-Control-Allow-Methods"] == (
        "GET, POST, PATCH, DELETE, OPTIONS"
    )
