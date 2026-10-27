"""P1.9 capstone demo package.

Drives the full Phase 1 closed-loop pipeline (requirement -> completed)
against a Flask test client with NO real git-hosting-provider network:
the manual PR model removed all direct GitHub/Gitea integration, so PR
linking/merging is pure manual record-keeping via
``development_task_service.link_pr`` / ``mark_pr_merged``, driven through the
same HTTP client as every other step.
"""
