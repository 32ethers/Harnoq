"""HTML shells for the browser dashboard.

The pages live under ``/ui`` (plus a ``/`` redirect) so they never collide with
the JSON API, which owns ``/projects`` etc. These views intentionally do not
query domain services; app/static/app.js fetches the existing REST/JSON API and
renders dynamic data in the browser.
"""
import os
from pathlib import Path

from flask import Blueprint, g, redirect, render_template, request, url_for

from app.models.project import Project

bp = Blueprint("web", __name__)


@bp.get("/ui/login")
def login_page():
    return render_template("login.html")


@bp.get("/ui/users")
def users_page():
    return render_template("users.html")


@bp.get("/ui/account")
def account_page():
    return render_template("account.html")

# The OpenWebUI system prompt lives in skills/ as the single source of truth
# (operators also paste it from there). The config page reads that same file so
# the page never drifts from the prompt the skill docs ship. This is static doc
# content, not domain data, so it is rendered server-side instead of fetched by
# app.js like the rest of the dashboard.
_SYSTEM_PROMPT_PATH = (
    Path(__file__).resolve().parents[2] / "skills" / "openwebui-system-prompt.md"
)


@bp.get("/")
def home():
    return redirect(url_for("web.projects_list"))


@bp.get("/ui/config")
def config_page():
    try:
        prompt_path = _SYSTEM_PROMPT_PATH.with_name("openwebui-system-prompt.en.md") if g.locale == "en" else _SYSTEM_PROMPT_PATH
        system_prompt = prompt_path.read_text(encoding="utf-8")
    except OSError:
        system_prompt = ""
    web_base_url = f"{request.scheme}://{request.host}"
    mcp_url = os.environ.get("HARNOQ_MCP_URL") or (
        f"{request.scheme}://{request.host}/mcp"
    )
    return render_template(
        "config.html",
        system_prompt=system_prompt,
        web_base_url=web_base_url,
        mcp_url=mcp_url,
    )


@bp.get("/ui/projects")
def projects_list():
    return render_template("projects.html")


@bp.get("/ui/projects/<int:project_id>")
def project_detail(project_id):
    return render_template("project_detail.html", project_id=project_id)


@bp.get("/ui/projects/<int:project_id>/source-requirements")
def source_requirements_list(project_id):
    return render_template("source_requirements_list.html", project_id=project_id)


@bp.get("/ui/projects/<int:project_id>/constitution")
def constitution_page(project_id):
    return render_template("constitution_detail.html", project_id=project_id)


@bp.get("/ui/projects/<int:project_id>/glossary")
def glossary_page(project_id):
    return render_template("glossary.html", project_id=project_id)


@bp.get("/ui/projects/<int:project_id>/change-requests")
def change_requests_list(project_id):
    project = Project.get_or_none(Project.id == project_id)
    return render_template(
        "change_requests_list.html",
        project_id=project_id,
        project_number=project.project_number if project else "",
    )


@bp.get("/ui/projects/<int:project_id>/design-cases")
def design_cases_list(project_id):
    return render_template("design_cases_list.html", project_id=project_id)


@bp.get("/ui/projects/<int:project_id>/requirements")
def requirement_digest_page(project_id):
    """Read-only: the whole project's requirement, derived from the designs."""
    return render_template("requirement_digest.html", project_id=project_id)


@bp.get("/ui/projects/<int:project_id>/nfr-coverage")
def nfr_coverage_page(project_id):
    """Read-only: which constitution NFR rows a design claims to verify."""
    return render_template("nfr_coverage.html", project_id=project_id)


# Both pages are the same page, filtered to a different subset of
# DeliveryItem.kind — see docs/cn/design-evolution.md#delivery
# ("两个页面都是同一种页面"). Labels live here (not duplicated in app.js)
# because the template needs them to render the <select> options too.
_DELIVERY_KIND_LABELS = {
    "assembly": "装配",
    "seam": "接缝",
    "duplicate": "重复",
    "journey": "用户旅程",
    "nfr": "NFR 承诺",
}


@bp.get("/ui/projects/<int:project_id>/integration")
def integration_delivery_items_page(project_id):
    project = Project.get_or_none(Project.id == project_id)
    kinds = ("assembly", "seam", "duplicate")
    return render_template(
        "delivery_items.html",
        project_id=project_id,
        project_number=project.project_number if project else "",
        page_kind="integration",
        page_title="集成事项",
        page_hint=(
            "分块并行在接缝上留下的账：要接的部件（assembly）、模块之间声明的邻接"
            "（seam）、结构扫描发现的重复实现（duplicate）。每一项要么被兑现"
            "（covered，指出兑现它的 DESIGN-*/TASK-* 和测试），要么被明确豁免"
            "（waived，写下理由）；待处理事项在下面的计数条中突出显示。"
        ),
        kind_options=[(k, _DELIVERY_KIND_LABELS[k]) for k in kinds],
    )


@bp.get("/ui/projects/<int:project_id>/system")
def system_delivery_items_page(project_id):
    project = Project.get_or_none(Project.id == project_id)
    kinds = ("journey", "nfr")
    return render_template(
        "delivery_items.html",
        project_id=project_id,
        project_number=project.project_number if project else "",
        page_kind="system",
        page_title="系统事项",
        page_hint=(
            "从需求清点出来、没有任何设计天然认领的东西：用户旅程（journey，来自 "
            "INPUT-* 原话）和 NFR 承诺（nfr，来自规约 nfr_baseline 里 "
            "strength=commitment 的行）。每一项要么被剧本覆盖（covered），要么被"
            "明确豁免（waived，写下理由）；待处理事项在下面的计数条中突出显示。"
        ),
        kind_options=[(k, _DELIVERY_KIND_LABELS[k]) for k in kinds],
    )


@bp.get("/ui/projects/<int:project_id>/features")
def features_list(project_id):
    return render_template("features_list.html", project_id=project_id)


@bp.get("/ui/features/<int:feature_id>")
def feature_detail(feature_id):
    return render_template("feature_detail.html", feature_id=feature_id)


@bp.get("/ui/design-cases/<int:design_case_id>")
def design_case_detail(design_case_id):
    return render_template("design_case_detail.html", design_case_id=design_case_id)


@bp.get("/ui/projects/<int:project_id>/tasks")
def development_tasks_list(project_id):
    return render_template("development_tasks_list.html", project_id=project_id)


@bp.get("/ui/tasks/<int:task_id>")
def development_task_detail(task_id):
    return render_template("development_task_detail.html", task_id=task_id)


@bp.get("/ui/source-requirements/<int:source_requirement_id>")
def source_requirement_detail(source_requirement_id):
    return render_template("source_requirement_detail.html", source_requirement_id=source_requirement_id)
