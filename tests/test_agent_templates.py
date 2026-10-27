"""Generated Claude Code/opencode configurations and their API surface."""
import io
import json
import zipfile

import pytest

from app.core.errors import ConflictError, NotFoundError
from app.services import agent_template_builder, agent_template_service

_MCP_URL = "http://harnoq-host:5001/mcp"
_MODEL = "openai/gpt-5.4"


class _Project:
    def __init__(self, name="Acme", description="Ship login"):
        self.name = name
        self.description = description


class _Constitution:
    status = "confirmed"


def _confirmed_project(client):
    project = client.post(
        "/projects", json={"name": "P", "description": "Project description"}
    ).get_json()
    client.patch(
        f"/projects/{project['id']}/constitution",
        json={"system_nature": "A governed product"},
    )
    assert client.post(
        f"/projects/{project['id']}/constitution/confirm"
    ).status_code == 200
    return project


#: Project-wide Planning reads belong to the dual-mode primary manager and
#: never to task-scoped coder/qa subagents.
_PLANNING_ONLY_TOOLS = (
    "harnoq_project_context",
    "harnoq_source_requirement_list",
    "harnoq_feature_list",
)
#: The manager may survey these to help the human pick a task to start; its
#: subagents, which are handed one task, may not.
_MANAGER_SURVEY_TOOLS = (
    "harnoq_task_progress",
    "harnoq_design_case_list",
)
_PROJECT_WIDE_TOOLS = _PLANNING_ONLY_TOOLS + _MANAGER_SURVEY_TOOLS


def _assert_context_and_boundary(content):
    for text in (
        "harnoq_task", "harnoq_constitution_get", "harnoq_task_get",
        "human", "autonomously schedule",
    ):
        assert text in content


def _assert_blocked_is_reachable(content, who, role=None):
    """Every role must know ``blocked`` exists, alongside its own verdicts.

    A subagent that cannot reach Harnoq will report SOMETHING. If the only
    outcomes it knows are pass and fail, it reports one of those — and a verdict
    reached without the design is indistinguishable from a real one. The
    observed case (TASK-1, 2026-07-21) survived only because that particular
    model volunteered its blindness.

    Checked against the role's real verdict vocabulary, so a prompt that offers
    a word the service rejects — qa's old ``failed`` — fails here too.
    """
    flat = " ".join(content.split())
    assert "blocked" in flat.lower(), f"{who} never mentions the blocked outcome"
    if role is None:
        return
    assert agent_template_builder._ROLE_OUTCOMES[role] in flat, (
        f"{who} does not state its own verdicts next to blocked"
    )
    if role == "qa":
        assert "`failed`" not in flat, f"{who} still offers the dropped qa failed"


def _assert_qa_evidence_is_demanded(content, who):
    flat = " ".join(content.split())
    for phrase in ("criteria_verdicts", "design_number", "evidence"):
        assert phrase in flat, f"{who} does not ask qa for {phrase}"


def _assert_qa_evidence_reaches_production(content, who):
    """The qa subagent must anchor "the system does X" evidence to the path the
    product actually runs.

    The observed escape: a collaborator stubbed to report "not applicable"
    without doing the work, and a capable routine nothing on the start-up path
    called — both green in unit tests, neither exercised by the shipped
    product, so qa reported `pass` on nine criteria while two went unmet. This
    lives in the qa subagent prompt, not the manager file, which only passes
    the verdicts through.
    """
    flat = " ".join(content.split())
    for phrase in (
        "path the product actually runs",
        "does not prove the shipped entry point",
        "trace the production entry",
    ):
        assert phrase in flat, f"{who} does not anchor qa evidence to production: {phrase!r}"


def _assert_qa_weighs_test_cost(content, who):
    """qa must judge a suite's COST, not only its coverage — and the guardrail
    against reading that as licence to cut coverage must survive intact.

    See docs/cn/design-evolution.md §13: every existing instruction
    about testing pushed one direction (cover more, trust less, dig deeper),
    leaving nowhere in the prompt that asked whether a given test was worth
    what it cost. The guardrail is the single most fragile part of this
    addition — write it weakly and this section quietly becomes licence to
    fail criteria or drop coverage, which is the opposite of its point — so
    it is asserted by its own substrings, not folded into the general list.
    """
    flat = " ".join(content.split())
    for phrase in (
        "ORDERS OF MAGNITUDE",
        "the conclusion never depended",
        "cannot fail as written",
    ):
        assert phrase in flat, f"{who} lost a cost-of-tests shape: {phrase!r}"
    for phrase in (
        "is a report, not a verdict",
        "never grounds for",
        "slow is not the property that marks a test out",
        "expensive AND uninformative",
        "manager's and the human's call, never yours",
    ):
        assert phrase in flat, f"{who} lost the cost-of-tests guardrail: {phrase!r}"
    # Placement matters: the construction ticket names the exact two anchors
    # this section must sit between, so drift here is drift from the ticket,
    # not just a missing phrase.
    before = flat.index("expect that some criteria no test covers")
    middle = flat.index("Judge the suite")
    after = flat.index("For any criterion that asserts the SYSTEM does")
    assert before < middle < after, (
        f"{who} cost-of-tests section is not between its required anchors"
    )


def _assert_coder_estimates_before_building(content, who):
    """coder must estimate an expensive measurement's answer BEFORE building
    it, and report the estimate whichever way the decision goes — see
    docs/cn/design-evolution.md §13."""
    flat = " ".join(content.split())
    for phrase in (
        "thirty seconds estimating",
        "order of magnitude",
        "clears the bound by a wide margin",
        "let them decide whether it is still worth",
        "close to the bound",
        "cannot produce one with any confidence",
        "Report the estimate in your write-up either way",
    ):
        assert phrase in flat, f"{who} lost the estimate-first guidance: {phrase!r}"
    before = flat.index("slow is not a reason to skip")
    after = flat.index("Estimate before you build")
    assert before < after, f"{who} estimate-first section precedes its anchor"


def _assert_coder_lints_before_reporting(content, who):
    """coder must run the repository's OWN lint before reporting, never a tool
    it introduces — see docs/cn/design-evolution.md §13."""
    flat = " ".join(content.split())
    for phrase in (
        "OWN configured lint",
        "never a tool you bring in yourself",
        "the exact command you ran",
        "has no lint or formatting check configured at all, say that plainly",
    ):
        assert phrase in flat, f"{who} lost the lint-before-report guidance: {phrase!r}"


def _assert_qa_evidence_is_structured(content, who):
    """`evidence` must be requested as {tests, code, note}, with the
    anti-anchoring guardrail against treating a prior verdict as settled —
    see docs/cn/design-evolution.md §13."""
    flat = " ".join(content.split())
    for phrase in (
        "tests: [...], code: [...], note",
        "nothing automated is watching this criterion",
        "STARTING POINT",
        "added a new layer in front of the code",
        "no longer describes what actually runs",
    ):
        assert phrase in flat, f"{who} lost structured-evidence guidance: {phrase!r}"


def _assert_pre_review_is_a_reconciliation(content):
    """Pre-review must ask for a divergence list, not a design summary.

    The observed failure was a manager that restated the design's scope and
    asked for approval — nothing the human did not already know, and nothing
    to decide. Its unique job is checking the design's stated assumptions
    against the repository as upstream tasks have actually left it, which is
    the one thing the design gate could not do.
    """
    flat = " ".join(content.split())
    for phrase in (
        # Three ordered parts, each feeding the next.
        "THREE parts",
        "Orientation",
        "Reconciliation",
        "Recommendation",
        # Part 2 must produce findings, not intentions. The observed failure
        # was a table whose second column said "检查 web/app.py" — a plan, not
        # a result — so evidence is what separates looking from claiming.
        "with EVIDENCE",
        "is not a finding, it is an intention",
        "GO LOOK in the repository",
        # Part 3 must not swallow part 2.
        "It must NOT absorb part 2",
        # The rubber-stamp close survived two rewrites; pin it explicitly.
        "Do not close by asking them to reply",
        "not a second design review",
        # A mechanism the solution mandates but no criterion can catch the
        # absence of leaves the qa gate blind — the observed escape where a
        # solution-mandated enforcement was replaced by a weaker one that
        # passed the same behavioural criterion.
        "no acceptance criterion can catch",
        "weaker one that happens to pass the same observable behaviour",
    ):
        assert phrase in flat, f"pre-review guidance lost: {phrase!r}"


def _assert_task_scoped(content, where):
    """No coder/qa subagent may be handed a project-wide browse tool."""
    for tool in _PROJECT_WIDE_TOOLS:
        assert tool not in content, f"{where} grants project-wide {tool}"


def _assert_manager_routes_planning_and_build(content, where):
    """One primary manager must continue the whole project or one TASK-*.

    Stage methods remain dynamic: the durable template only carries the mode
    router, so ordinary Build requests avoid the large project context while a
    project-level Integration/System request cannot proceed without it.
    """
    flat = " ".join(content.split())
    for phrase in (
        "DUAL-MODE",
        "ROUTE BEFORE YOU READ",
        "harnoq_project_context",
        "active_stage_method",
        "harnoq_task_progress",
        "generic `next_step`",
        "Planning/Delivery round may create an ordinary TASK-*",
        "P*-INTEG-* / P*-SYS-* exactly as returned",
    ):
        assert phrase in flat, f"{where} lost dual-mode routing: {phrase!r}"
    assert flat.index("ROUTE BEFORE YOU READ") < flat.index(
        "Treat `harnoq_constitution_get`"
    )


def test_service_generates_each_flavor_from_confirmed_constitution(client, app):
    project = _confirmed_project(client)
    with app.app_context():
        claude = agent_template_service.generate(project["id"], "claude", _MCP_URL, _MODEL)
        opencode = agent_template_service.generate(project["id"], "opencode", _MCP_URL, _MODEL)
    assert "CLAUDE.md" in claude
    assert json.loads(claude[".mcp.json"])["mcpServers"]["harnoq"]["headers"] == {
        "Authorization": "Bearer ${HARNOQ_API_TOKEN}"
    }
    assert "AGENTS.md" in opencode
    for files, flavor in ((claude, "claude"), (opencode, "opencode")):
        manifest = json.loads(files[agent_template_service.TEMPLATE_MANIFEST])
        assert manifest["format_version"] == 1
        assert manifest["flavor"] == flavor
        assert manifest["refresh_command"] == (
            f"uv run scripts/refresh-agent-config.py <repo-path> "
            f"--project {project['project_number']} --flavor {flavor}"
        )
        assert f"--project {project['id']} --flavor" not in manifest["refresh_command"]
        assert manifest["prompt_fingerprint"].startswith("sha256:")
        assert manifest["prompt_fingerprint"] == agent_template_service.prompt_fingerprint()


def test_service_requires_known_project_and_confirmed_constitution(client, app):
    with app.app_context():
        with pytest.raises(NotFoundError):
            agent_template_service.generate(9999, "claude", _MCP_URL, _MODEL)

    project = client.post("/projects", json={"name": "P"}).get_json()
    client.patch(f"/projects/{project['id']}/constitution", json={"system_nature": "x"})
    with app.app_context():
        with pytest.raises(ConflictError):
            agent_template_service.generate(project["id"], "claude", _MCP_URL, _MODEL)


def test_service_rejects_unknown_flavor(client, app):
    project = _confirmed_project(client)
    with app.app_context():
        with pytest.raises(Exception, match="flavor must be"):
            agent_template_service.generate(project["id"], "other", _MCP_URL, _MODEL)


def test_agent_template_api_returns_zip_with_remote_url_and_has_no_put(client):
    project = _confirmed_project(client)
    response = client.get(f"/projects/{project['id']}/agent-template?flavor=opencode")
    assert response.status_code == 200
    assert response.mimetype == "application/zip"
    assert "attachment" in response.headers["Content-Disposition"]
    assert response.headers["X-Harnoq-Agent-Template-Fingerprint"].startswith(
        "sha256:"
    )

    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        names = set(archive.namelist())
        assert "AGENTS.md" in names
        assert agent_template_service.TEMPLATE_MANIFEST in names
        manifest = json.loads(archive.read(agent_template_service.TEMPLATE_MANIFEST))
        config = json.loads(archive.read(".opencode/opencode.json"))
    assert manifest["refresh_command"].endswith(
        f"--project {project['project_number']} --flavor opencode"
    )
    assert manifest["prompt_fingerprint"] == response.headers[
        "X-Harnoq-Agent-Template-Fingerprint"
    ]
    harnoq = config["mcp"]["harnoq"]
    assert harnoq["type"] == "remote"
    # URL is derived from the request host, same port as the web server.
    assert harnoq["url"].endswith("/mcp")
    assert ":5001" not in harnoq["url"]
    assert client.put(f"/projects/{project['id']}/agent-template").status_code == 405


def test_agent_template_api_validates_flavor_and_constitution(client):
    project = client.post("/projects", json={"name": "P"}).get_json()
    assert client.get(f"/projects/{project['id']}/agent-template?flavor=claude").status_code == 404
    project = _confirmed_project(client)
    assert client.get(f"/projects/{project['id']}/agent-template?flavor=other").status_code == 400


def test_agent_template_migration_removed_derived_tables(app):
    with app.app_context():
        db = app.config["DATABASE"]
        assert not db.table_exists("agent_project_template")
        assert not db.table_exists("task_package")
        assert not db.table_exists("development_instruction")


def test_opencode_permission_keys_carry_the_mcp_server_prefix():
    """opencode exposes an MCP tool as ``<server key>_<tool name>``.

    Harnoq's server key is "harnoq" and its tools are themselves named
    ``harnoq_*``, so the live name doubles the prefix:
    ``harnoq_harnoq_task_get``. This is the only assertion here that looks at
    the literal string rather than the builder's own constant, and it has to
    be: the builder wrote the UNDOUBLED name for months and the test asserted
    the same undoubled name back, so generator and test agreed with each other
    while agreeing with nothing opencode does.

    The failure mode is silent and total. `harnoq_*: deny` matches the doubled
    names, but every per-tool re-allow matches nothing, so a deny-then-re-allow
    map collapses to deny-everything: subagents get NO Harnoq tools and can
    only report that they cannot read their own task. On the manager, whose
    map is allow-then-claw-back, the same slip inverts — the project-wide
    denials miss and it browses the whole project.

    Measured against opencode 1.18.4: with the doubled names, a probe agent's
    tool list contained exactly the re-allowed harnoq tools and no others.
    """
    files = agent_template_builder.build_opencode(
        _Project(), _Constitution(), _MCP_URL, _MODEL
    )
    config = json.loads(files[".opencode/opencode.json"])
    server_key, = config["mcp"].keys()

    for role in ("coder", "qa"):
        perm = config["agent"][role]["permission"]
        assert f"{server_key}_harnoq_task_get" in perm
        assert perm[f"{server_key}_harnoq_task_get"] == "allow"
        # The undoubled form is the bug: present, it matches no live tool.
        assert "harnoq_task_get" not in perm
    assert config["agent"]["manager"]["permission"][
        f"{server_key}_harnoq_project_context"
    ] == "allow"
    assert "harnoq_project_context" not in config["agent"]["manager"]["permission"]


def test_opencode_and_claude_grant_the_same_tools_under_each_client_s_naming():
    """One tool list, two namings — neither may drift from the other."""
    claude = agent_template_builder.build_claude_code(
        _Project(), _Constitution(), _MCP_URL
    )
    opencode = json.loads(
        agent_template_builder.build_opencode(
            _Project(), _Constitution(), _MCP_URL, _MODEL
        )[".opencode/opencode.json"]
    )
    tools_line = next(
        line for line in claude[".claude/agents/qa.md"].splitlines()
        if line.startswith("tools:")
    )
    perm = opencode["agent"]["qa"]["permission"]
    for tool in agent_template_builder._READ_ONLY_MCP_TOOL_NAMES:
        assert f"mcp__harnoq__{tool}" in tools_line, f"claude qa lost {tool}"
        assert perm.get(f"harnoq_{tool}") == "allow", f"opencode qa lost {tool}"


def _assert_branch_discipline(content, who):
    """The manager verifies the branch before dispatching a subagent."""
    flat = " ".join(content.split())
    assert "git branch --show-current" in flat, f"{who} never checks the branch"


def test_manager_branches_before_dispatching_and_coder_verifies():
    claude = agent_template_builder.build_claude_code(
        _Project(), _Constitution(), _MCP_URL
    )
    opencode = agent_template_builder.build_opencode(
        _Project(), _Constitution(), _MCP_URL, _MODEL
    )
    for manager_doc, who in (
        (claude["CLAUDE.md"], "claude manager"),
        (opencode["AGENTS.md"], "opencode manager"),
    ):
        _assert_branch_discipline(manager_doc, who)
        flat = " ".join(manager_doc.split())
        # Branching is a precondition of dispatch, not a commit-time chore.
        assert "BEFORE dispatching coder" in flat, f"{who} branches too late"
        assert "Continue in the current project directory" in flat
        assert "Manager, coder and qa share its code and working changes" in flat
        assert "Integration and System each reuse one stage branch across all item groups" in flat
        assert "git status --porcelain" in flat
        assert "Keep unrelated changes intact and out of the task's commits" in flat
        assert "stop and report it to the human" not in flat

    for coder_doc, who in (
        (claude[".claude/agents/coder.md"], "claude coder"),
        (opencode[".opencode/agents/coder.md"], "opencode coder"),
    ):
        flat = " ".join(coder_doc.split())
        assert "Manager verifies the task branch before dispatch" in flat
        assert "Confirm your working directory is that path" in flat
        assert "Do not run git" in flat
        assert "For an Integration run or approved-CR source TASK" in flat
        assert "do not request a second human plan approval" in flat
        assert "blocked" in flat.lower()


def test_manager_owns_project_wide_integration_inventory():
    claude = agent_template_builder.build_claude_code(
        _Project(), _Constitution(), _MCP_URL
    )
    opencode = agent_template_builder.build_opencode(
        _Project(), _Constitution(), _MCP_URL, _MODEL
    )
    for manager_doc in (claude["CLAUDE.md"], opencode["AGENTS.md"]):
        flat = " ".join(manager_doc.split())
        assert "manager owns the project-wide inventory" in flat
        assert "Do not delegate the whole inventory" in flat
        assert "A helper may collect a small, explicit evidence slice" in flat
        assert "manager defines its inputs and output" in flat
        assert "manager's stage work" in flat
        assert "group linked INTEG-* findings by common root cause" in flat
        assert "do not send one item per coder invocation" in flat
        assert "Reconcile all groups into one build" in flat


def test_task_scoped_coder_reads_integration_findings_one_by_one():
    opencode = agent_template_builder.build_opencode(
        _Project(), _Constitution(), _MCP_URL, _MODEL
    )
    coder = " ".join(opencode[".opencode/agents/coder.md"].split())
    assert "harnoq_integration_item_get" in coder
    assert "EACH assigned item" in coder
    assert "If an assigned item cannot be read" in coder
    permission = json.loads(opencode[".opencode/opencode.json"])["agent"]["coder"]["permission"]
    assert permission["harnoq_harnoq_task_get"] == "allow"
    assert permission["harnoq_harnoq_integration_item_get"] == "allow"
    assert permission["harnoq_*"] == "deny"
    assert "harnoq_harnoq_integration_item_list" not in permission


def test_manager_probes_child_tool_access_after_config_changes():
    files = agent_template_builder.build_opencode(
        _Project(), _Constitution(), _MCP_URL, _MODEL
    )
    manager = " ".join(files["AGENTS.md"].split())
    assert "subagent's EFFECTIVE permissions" in manager
    assert "relaunch the client/child session" in manager
    assert "child can actually read one assigned source" in manager
