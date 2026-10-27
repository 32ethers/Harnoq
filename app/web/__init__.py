"""Server-rendered web UI (read-only dashboard).

Phase 1 shipped API-only. This package adds human-facing HTML pages that *read*
the same service layer the JSON API uses — a window onto the workflow, not
another way to drive it. Every route is GET and never mutates state.
"""
from .views import bp

__all__ = ["bp"]
