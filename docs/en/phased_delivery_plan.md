# Phased delivery plan

[中文](../cn/phased_delivery_plan.md) · [English](phased_delivery_plan.md)

This extends the [initial design](initial_design.md), separating the original roadmap from present implementation. Phase numbers do not mean later capabilities are absent, and existing code does not by itself establish complete product acceptance.

## Current progress

| Phase | Current position |
| --- | --- |
| 1: task development loop | Implemented. Direct design tasks, source-backed tasks, explicit dependencies, and applicable pre-review replace several earlier mechanisms. |
| 2: project verification | INTEG/SYS inventories, runs, evidence, resource audits, and repair reentry exist. Validate inventory completeness and real-resource behavior rather than reviving design shells or per-check tasks. |
| 3: knowledge and context | Rationale, versions, change traceability, and context retrieval exist. Unified search, a complete document library, and RAG remain under evaluation. |
| 4: enterprise capabilities | Accounts/JWT, ownership, assignment, workload, and some audit trails exist. Membership isolation, finer permissions, and governance remain future work. |

Implementation references: [task sources](../../app/services/task_source_service.py), [tasks and dependencies](../../app/services/development_task_service.py), [delivery gates](../../app/services/delivery_gate.py), [runs](../../app/services/delivery_stage_service.py), and [users](../../app/services/user_service.py). See [design rationale](design-evolution.md) and [delivery API rules](api.md#delivery-audit).

## Purpose and principles

The roadmap defines what to build first, the completion boundary of each phase, and what to defer. It does not prescribe database fields or detailed implementation.

The core hypothesis is that AI agents should operate within a controlled engineering process. Establish a complete loop before expanding automation; prove task quality before project quality; retain key rationale before building a knowledge platform; preserve human control before adding autonomous orchestration; avoid duplicating code hosting, PRs, and CI/CD.

## Phase 1: minimum development loop

### Goal and workflow

Demonstrate a reliable path from requirement to merged delivery:

```text
Create project → capture requirements → confirm design → create tasks
→ set feature/task dependencies → claim → applicable review
→ read live context and constitution → coder → independent QA
→ repair if needed → PR → reported merge → complete
```

### Scope and changes from the original plan

- Project creation and lists; source requirement input and lifecycle.
- Design records that capture both the solution and rationale.
- Task creation, claiming, ownership, and pre-development approval or rejection.
- Feature dependencies plus explicit TaskDependency edges; both are checked when claiming. Dependencies are not copied into separate generated layers.
- Live task context replaces persisted DevelopmentInstruction and TaskPackage artifacts.
- Manager coordinates coder and QA. QA combines the former tester/reviewer roles while remaining independent of implementation.
- `repo_url` is a text record, not a hosting-provider integration.
- People or agents create PRs outside Harnoq. Harnoq records branch name, PR number, and URL.
- Harnoq does not read or poll CI. After a merge is reported with `mark_pr_merged`, `mark_completed` completes the task.

### Concepts and acceptance

Requirements preserve intent; designs record solutions and reasons; tasks are claimable delivery units; dependencies express prerequisites; pre-review checks the contract against the actual repository; the manager communicates with the user and coordinates evidence.

A demonstration must create a project, requirement, design, and executable work with dependencies; claim and review a task; retrieve current context; record coder and QA runs; repair failed work; link a PR; and complete the task after recording its merge. One active design has at most one active design-backed task; additional approved work can use source-backed tasks.

Phase 1 excludes RAG, complex organization permissions, autonomous scheduling, model pools, complete system/integration testing platforms, complex approval engines, and replacement code-hosting functionality.

## Phase 2: project-level quality

### Goal and workflow

Verify assembly, seams, duplicate responsibilities, user journeys, and non-functional commitments, routing failures into the appropriate repair or change process:

```text
Capability tasks complete → Integration inventory / verification / audit
→ System journey / NFR verification
→ ordinary repairs in RUN; contract changes through CR and source TASK
→ reenter verification against the current baseline
```

Integration precedes System. An individual check or rerun does not require its own task, design, or PR.

### Scope and remaining gaps

1. Derived delivery gates and run start/update/close/abandon operations express stage progress, independently of project `active/archived` status.
2. SYS journey/NFR items and System runs replace individual system-test tasks.
3. INTEG assembly/seam/duplicate items and Integration runs replace individual integration-test tasks.
4. Items record obligations, drivers, and executable evidence. A separate reusable, versioned test-case library is not implemented and remains an evaluation question.
5. `mock_env` and `mock_data` task types exist, but preparing mocks is not a universal prerequisite. Reuse suitable resources; mocks cannot prove real-resource success.
6. Task QA stores `test_run` and criterion evidence. Delivery items store command, output, exit code, revision, round, and inventory evidence. This is not a standalone test-case execution-history product.
7. Ordinary repairs remain in runs and items. Contract changes use CRs and source-backed tasks; there is no independent general-purpose bug entity.
8. Approved CRs can directly source `bug_fix` tasks without artificial design shells.
9. Requirement changes can enter through source material and multi-target CRs. Product scope decisions are not made automatically.

Harnoq checks submitted evidence structure, provenance, and consistency. People or agents execute tests; Harnoq does not replace their test platforms or CI/CD.

### Acceptance

Show that Integration opens after capability completion, System opens after current Integration passes, inventories and runs track obligations, and failed checks produce repair evidence or appropriate changes. Reopening an old task is reserved for failure to satisfy its original contract.

Existing checks include [runs](../../tests/test_delivery_stage_runs.py), [system inventories](../../tests/test_system_inventory_validator.py), [integration resources](../../tests/test_integration_resources.py), and [completion acceptance](../../tests/test_integration_completion_acceptance.py). Cross-project behavior still needs observation.

## Phase 3: knowledge retention and context

The goal is to preserve why a long-lived system works as it does, not merely how it is implemented. Existing rationale fields, history, CR reverse lookup, and API/MCP context are a starting point.

The original scope includes a decision library, structured rationale, project documentation, traceability among requirements/designs/tasks/PRs/results, background retrieval, relevant context attached to agent reads, controlled knowledge tools, and an upkeep process.

Acceptance should demonstrate tracing feature intent and rationale, retrieving task-related decisions and documents, delivering relevant background, and exposing controlled knowledge reads. RAG must not replace structured core relationships; its role would be retrieval of discussions and long documents.

## Phase 4: enterprise capabilities and automation

Accounts, credentials, ownership, assignments, workload, and activity attribution already exist. Project membership isolation and full enterprise governance do not.

The original scope includes multiple users and teams, roles and permissions, approval flows, audits, repository configuration per project, agent templates, model capability adaptation, stronger quality gates, metrics, and more automated orchestration. Repository support means configuration and records, not reviving removed provider integrations.

Optional later ideas include model pools, an autonomous scheduler, task-step decomposition, allocation by dependencies and model availability, and watchdogs for repeated failure. These should follow a stable quality loop and are not MVP obligations.

Acceptance should demonstrate collaboration by different roles, auditable key actions, project-specific repositories and agent configurations, delivery/quality visibility, and task-appropriate execution guidance.

## Delivery order

Prove the task loop first, extend to project verification and defect reentry, strengthen retained knowledge, then expand enterprise governance and automation. Keep early scope bounded: the central value is agents working within requirements, design, review, independent QA, and recorded delivery constraints.

<a id="open-questions"></a>

## Open questions and capability boundaries

These are unresolved observations, not approved implementation requirements or new gates. See [current limitations](design-evolution.md#limits).

### Internal consistency within a design

A revised section can claim ownership that an older exclusion still denies. Multi-target CRs and version comparison reveal changes, but do not prove semantic consistency. Confirmation methods require review; no reliable semantic hard gate has been approved.

### Reasons for inexpensive edits

When no delivery depends on a design, forcing a CR would add unnecessary process. Version history then explains what changed without necessarily preserving a separate decision reason or approval. Retain that trade-off and revisit with new evidence.

### Retrospective design is not proof of process

A quickly created and confirmed design followed by acceptance of merged code does not prove the prescribed design process occurred. Timing thresholds are unreliable. Retrospective documentation can be valid, but guarantees must stop at verifiable content, state, and evidence.

### Account identity is not independent human consent

Authenticated pre-review uses the current account rather than trusting arbitrary reviewer text. An agent holding that account's API credential still acts as that identity; this does not prove explicit conversational approval. Internal/test paths retain legacy reviewer/assignee fallbacks. See [task service](../../app/services/development_task_service.py) and [authentication](../../app/core/authentication.py).

### Cross-model and cross-project behavior

Template fingerprints, refresh tooling, and evaluation scaffolding improve delivery inspection and repeatable measurement. They are not broad validation across domains and models. Observations from one fixture may support hypotheses, but its entities must not enter universal prompts as mandatory rules. See [refresh tests](../../tests/test_refresh_agent_config.py) and [Build evaluations](evals/build/README.md).
