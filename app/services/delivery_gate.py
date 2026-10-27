"""Derived project-wide Integration and System gates.

Capability tasks finish before Integration starts. Each delivery stage then
has one stage-run record plus its inventory, item evidence and resource
checks. Integration repairs are recorded on the stage run and INTEG-* items, which
does not become a capability dependency. System repairs stay on direct items.
A closed run is current only for the capability/design basis it captured.
"""
from app.repositories import (
    delivery_item_repo,
    development_task_repo,
    task_event_repo,
    delivery_stage_run_repo,
)


def _completion_time(task, completed_event_at):
    """The moment a task actually became ``completed``.

    Prefers the ``created_at`` of the task's latest REAL transition into
    ``completed`` (see ``task_event_repo.latest_completed_at_by_task``).
    ``task.updated_at`` is NOT used as the primary source: it is bumped by
    ANY save on the task -- including ``realign_basis``, which touches an
    already-completed task's row without changing its status -- so using it
    here would make the gate misread routine housekeeping as fresh delivery
    and re-open a gate that nothing actually reopened.

    Falls back to ``task.updated_at`` only when no such ``task_event`` row
    exists at all. ``task_event`` started being recorded at a specific
    migration; a task that completed before that point has no row for it.
    That fallback is acceptable (rather than a bug to route around) because
    it only ever affects tasks that finished before this gate could have
    existed to observe them -- there is no live migration path through which
    a *current* completion silently lacks its event row.
    """
    if completed_event_at is not None:
        return completed_event_at
    return task.updated_at


def gate_status(project_id):
    """Report owed stages from live capability work, basis, audits and runs."""
    tasks = development_task_repo.list_by_project(project_id)
    delivery_items = delivery_item_repo.list_by_project(project_id)

    empty = {
        "integration_owed": False,
        "system_owed": False,
        "last_capability_completion": None,
        "last_integration_completion": None,
        "last_system_completion": None,
        "open_integration_item_numbers": [],
        "open_system_item_numbers": [],
        "capability_outstanding": False,
        "outstanding_capability_task_numbers": [],
        "system_inventory_outstanding": False,
        "system_inventory_errors": [],
        "system_inventory_pending_keys": [],
        "integration_inventory_outstanding": False,
        "integration_inventory_errors": [],
    }
    if not tasks and not delivery_items:
        return empty

    # Two project-wide reads, then everything is resolved in memory. Walking
    # task.design_case per row is a lazy FK dereference and therefore a query
    # per task — invisible on a fixture, linear on a real project, and this
    # runs on every project-context call.
    completed_tasks = [task for task in tasks if task.status == "completed"]
    completed_event_at_by_task = task_event_repo.latest_completed_at_by_task(
        [task.id for task in completed_tasks]
    )

    # A capability task that has not finished holds the gate shut: there is no
    # point integrating a product whose parts are still being built.
    capability_outstanding = any(task.status != "completed" for task in tasks)

    open_item_numbers = {"integration": [], "system": []}
    for item in delivery_items:
        if item.status != "open":
            continue
        bucket = "integration" if item.kind in ("assembly", "seam", "duplicate") else "system"
        open_item_numbers[bucket].append(item.item_number)

    last_capability = None
    for task in completed_tasks:
        completed_at = _completion_time(
            task, completed_event_at_by_task.get(task.id)
        )
        if last_capability is None or completed_at > last_capability:
            last_capability = completed_at
    integration_run = delivery_stage_run_repo.latest_closed(project_id, "integration")
    system_run = delivery_stage_run_repo.latest_closed(project_id, "system")
    last_integration = integration_run.closed_at if integration_run else None
    last_system = system_run.closed_at if system_run else None
    from app.services.integration_inventory_validator import completion_basis_fingerprint
    current_basis = completion_basis_fingerprint(project_id)

    integration_work_outstanding = bool(open_item_numbers["integration"])
    integration_is_stale = (
        last_capability is not None
        and (integration_run is None or integration_run.basis_fingerprint != current_basis)
    )
    from app.services import integration_inventory_validator
    integration_audit = integration_inventory_validator.completion_status(project_id)
    # An audit is evidence for the round, not proof that the project-wide
    # execution round has been closed.  In particular, it can be submitted
    # before starting the run without making that run impossible to start.
    integration_audit_closes_round = (
        integration_audit["complete"] and not integration_work_outstanding
        and integration_run is not None and not integration_is_stale
    )
    integration_owed = (
        not capability_outstanding
        and last_capability is not None
        and not integration_audit_closes_round
        and (integration_is_stale or integration_work_outstanding or (integration_audit["has_audit"] and not integration_audit["complete"]))
    )
    system_work_outstanding = bool(open_item_numbers["system"])
    system_is_stale = (
        last_integration is not None
        and (system_run is None or system_run.basis_fingerprint != current_basis
             or last_integration > last_system)
    )
    # A current System receipt is a close-out condition, not merely a create
    # permission.  Import locally so the validator's low-level reader stays
    # independent of this gate.
    from app.services import system_inventory_validator
    inventory = system_inventory_validator.status(project_id)
    system_inventory_outstanding = not inventory["complete"]
    # A receipt whose exact integration basis is current and whose complete
    # inventory is settled is an intentional terminal state.  It covers an
    # empty pass, a pass of waivers, and successful reruns of already-covered
    # scripts: none should require inventing a new System TASK merely to move
    # ``last_system_completion`` beyond the Integration timestamp.
    system_audit_closes_round = (
        inventory["complete"] and not system_work_outstanding
        and system_run is not None and not system_is_stale
    )
    system_owed = (
        not capability_outstanding
        and not integration_owed
        and (last_integration is not None or integration_audit["complete"])
        and not system_audit_closes_round
        and (
            system_is_stale
            or (integration_audit["complete"] and last_integration is None)
            or system_work_outstanding
            or system_inventory_outstanding
        )
    )

    return {
        "integration_owed": integration_owed,
        "system_owed": system_owed,
        "last_capability_completion": last_capability.isoformat() if last_capability else None,
        "last_integration_completion": last_integration.isoformat() if last_integration else None,
        "last_system_completion": last_system.isoformat() if last_system else None,
        "open_integration_item_numbers": open_item_numbers["integration"],
        "open_system_item_numbers": open_item_numbers["system"],
        "integration_inventory_outstanding": not integration_audit["complete"],
        "integration_inventory_errors": integration_audit["errors"],
        "capability_outstanding": capability_outstanding,
        "outstanding_capability_task_numbers": [
            task.task_number for task in tasks
            if task.status != "completed"
        ],
        "system_inventory_outstanding": system_inventory_outstanding,
        "system_inventory_errors": inventory["errors"],
        "system_inventory_pending_keys": inventory["pending_keys"],
    }
