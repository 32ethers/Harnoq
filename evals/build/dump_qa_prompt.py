"""Print the qa agent prompt exactly as a project's bundle ships it.

The point of dumping rather than hand-writing the prompt is that the eval must
measure what agents actually receive. ``agent_template_builder`` composes the
role prompt from several pieces; a prompt retyped into the README drifts from
the shipped one the first time either changes, and then the eval is measuring a
file nobody runs.

    HARNOQ_SQLITE_PATH=/tmp/harnoq-build-eval.db \\
        uv run python evals/build/dump_qa_prompt.py P1 > /tmp/qa_prompt.md
"""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

from app.core.config import Config  # noqa: E402
from app.db.base import database_proxy  # noqa: E402
from app.db.session import create_database  # noqa: E402


def main(project_number):
    database_proxy.initialize(create_database(Config()))
    from app.services import agent_template_builder, project_constitution_service
    from app.repositories import project_repo

    project = next(
        (p for p in project_repo.list_all() if p.project_number == project_number), None
    )
    if project is None:
        raise SystemExit(f"no such project: {project_number}")
    constitution = project_constitution_service.require_for_project(project.id)

    bundle = agent_template_builder.build_claude_code(
        project, constitution, "http://127.0.0.1:5000/mcp"
    )
    qa = next((path for path in bundle if path.endswith("qa.md")), None)
    if qa is None:
        raise SystemExit(f"no qa agent in the bundle; got {sorted(bundle)}")
    sys.stdout.write(bundle[qa])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: dump_qa_prompt.py <PROJECT-NUMBER>")
    if os.path.basename(os.environ.get("HARNOQ_SQLITE_PATH", "harnoq.db")) == "harnoq.db":
        raise SystemExit("set HARNOQ_SQLITE_PATH to the eval database")
    main(sys.argv[1])
