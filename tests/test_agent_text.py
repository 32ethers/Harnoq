"""Read-side newline repair for agent-facing responses."""
from app.core.agent_text import normalize_agent_text


def test_literal_backslash_n_becomes_a_real_break():
    got = normalize_agent_text("1. Given a\\n2. Given b")
    assert got == "1. Given a\n2. Given b"


def test_other_escapes_are_left_alone():
    # Deliberately narrow: a scan found \t/\r never occur in prose, while \u
    # and \\ are legitimate json.dumps output in JSON columns that a general
    # un-escaper would corrupt. Only \n is touched.
    assert normalize_agent_text("a\\tb") == "a\\tb"
    assert normalize_agent_text("a\\rb") == "a\\rb"
    assert normalize_agent_text("\\u4e2d\\u6587") == "\\u4e2d\\u6587"
    assert normalize_agent_text("path\\\\to") == "path\\\\to"


def test_real_newlines_and_plain_text_are_untouched():
    assert normalize_agent_text("a\nb") == "a\nb"
    assert normalize_agent_text("no escapes here") == "no escapes here"


def test_walks_dicts_and_lists_and_leaves_non_strings():
    payload = {
        "design": {"acceptance_criteria": "x\\ny", "count": 3, "ok": True},
        "items": ["a\\nb", None, 7],
    }
    assert normalize_agent_text(payload) == {
        "design": {"acceptance_criteria": "x\ny", "count": 3, "ok": True},
        "items": ["a\nb", None, 7],
    }


def test_agent_task_endpoint_repairs_the_joined_design_body(client):
    """The design a coder implements against must not arrive as one line.

    Covers the after_request on the agent_tasks blueprint end to end: a stored
    literal \\n in the design body comes back as a real break through the
    agent surface, while the UI-facing id route keeps it verbatim.
    """
    from tests.test_agent_runs import _ready_for_development_task

    task = _ready_for_development_task(client)
    project = client.get(f"/projects/{task['project_id']}").get_json()
    # Rewrite the linked design's criteria to contain a literal escape, via the
    # repository so no normalization runs on the way in.
    from app.repositories import design_case_repo

    design = design_case_repo.get(task["design_case_id"])
    design.acceptance_criteria = "1. Given a\\n2. Given b"
    design_case_repo.save(design)

    agent_view = client.get(
        f"/projects/{project['project_number']}/tasks/{task['task_number']}"
    ).get_json()
    assert "\\n" not in agent_view["design"]["acceptance_criteria"]
    assert "\n" in agent_view["design"]["acceptance_criteria"]

    # UI-facing route (internal id, different blueprint) is left verbatim.
    ui_view = client.get(f"/design-cases/{design.id}").get_json()
    assert "\\n" in ui_view["acceptance_criteria"]
