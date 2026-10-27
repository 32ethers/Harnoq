"""Shared enums for task-plan drafts and development tasks (English stable values)."""

TASK_TYPES = (
    "feature",
    "bug_fix",
    "refactor",
    "test",
    "mock_env",
    "mock_data",
    "doc",
    "infrastructure",
)

TASK_PRIORITIES = ("low", "normal", "high", "urgent")

TASK_COMPLEXITIES = ("small", "medium", "large", "xlarge")

TASK_RISK_LEVELS = ("low", "medium", "high")

TASK_SOURCE_TYPES = (
    "requirement",
    "bug",
    "requirement_change",
    "test_failure",
    "manual",
)

# How a task entered the execution queue.  ``source_type`` above remains the
# work classification (bug/refactor/etc.); this is the traceability category.
# A task may have many source rows, so ``mixed`` is a derived display value
# rather than a source row of its own.
TASK_SOURCE_CATEGORIES = (
    "design",
    "change_request",
)
