"""Recycle-bin reads at the project level.

Each child entity owns its own recycle bin (see each entity service's
``list_deleted``), so per-project trash is no longer aggregated here — it is read
per entity type on that entity's own page. What remains project-level is the
list of soft-deleted *projects*, shown on the projects list page.
"""
from app.repositories import project_repo


def deleted_projects():
    return project_repo.list_deleted()
