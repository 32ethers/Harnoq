"""Print the exact project-context payload a planning agent receives.

Usage:  uv run python evals/dump_context.py P1 > /tmp/ctx.json
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.core.config import Config  # noqa: E402
from app.db.base import database_proxy  # noqa: E402
from app.db.session import create_database  # noqa: E402

_db = create_database(Config())
database_proxy.initialize(_db)
_db.connect(reuse_if_open=True)

from app.api.skills import _project_context_read_action  # noqa: E402
from app.services import project_service  # noqa: E402

if len(sys.argv) != 2:
    raise SystemExit("usage: dump_context.py <PROJECT_NUMBER>")

wanted = sys.argv[1]
match = [p for p in project_service.list_all() if p.project_number == wanted]
if not match:
    raise SystemExit(f"no project {wanted}")

print(json.dumps(_project_context_read_action(match[0].id),
                 ensure_ascii=False, indent=1))
