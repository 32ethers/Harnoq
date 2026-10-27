"""The refresh CLI resolves user-facing project numbers before downloading."""
import importlib.util
import json
from pathlib import Path


_SCRIPT = Path(__file__).parents[1] / "scripts" / "refresh-agent-config.py"
_SPEC = importlib.util.spec_from_file_location("refresh_agent_config", _SCRIPT)
refresh_agent_config = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(refresh_agent_config)


def test_refresh_updates_harnoq_auth_without_removing_other_mcp_servers():
    old = {"mcpServers": {"other": {"command": "keep-me"}, "harnoq": {"url": "old"}}, "custom": True}
    new = {"mcpServers": {"harnoq": {"url": "new", "headers": {"Authorization": "Bearer ${HARNOQ_API_TOKEN}"}}}}
    merged = json.loads(refresh_agent_config._merge_mcp(json.dumps(old), json.dumps(new)))
    assert merged["mcpServers"]["other"] == old["mcpServers"]["other"]
    assert merged["mcpServers"]["harnoq"] == new["mcpServers"]["harnoq"]
    assert merged["custom"] is True


def test_project_id_resolves_business_number(monkeypatch):
    monkeypatch.setattr(
        refresh_agent_config,
        "_get_projects",
        lambda _harnoq: [
            {"id": 17, "project_number": "P1"},
            {"id": 23, "project_number": "P2"},
        ],
    )

    assert refresh_agent_config._project_id("http://harnoq", "P2") == 23


def test_project_id_does_not_treat_database_id_as_business_number(monkeypatch):
    monkeypatch.setattr(
        refresh_agent_config,
        "_get_projects",
        lambda _harnoq: [{"id": 17, "project_number": "P1"}],
    )

    try:
        refresh_agent_config._project_id("http://harnoq", "17")
    except RuntimeError as exc:
        assert str(exc) == "project 17 not found"
    else:
        raise AssertionError("raw database IDs must not be accepted as project numbers")
