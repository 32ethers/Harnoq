# Harnoq REST/JSON API Reference

[中文](api.md) · [English](../en/api.md)

All endpoints accept and return JSON. The base path is the Flask app root (no
prefix). Error responses use the shape `{"error": "<message>"}` with an
appropriate HTTP status code (`400` validation, `404` not found, `409` state
conflict). This document is sourced from the actual code in `app/api/*.py` and
`app/services/*.py` — the code is ground truth.

## Pipeline at a glance

```
PROJECT
  ├─ SourceRequirement (INPUT-*)    raw user material
  ├─ Project Constitution          project-wide governance singleton
  ├─ DeliveryItem (INTEG-*/SYS-*)   direct delivery verification obligations
  ├─ ChangeRequest (CR-*)          change governance and task sources
  └─ Feature (FEATURE-*)           plan/split unit
       ├─ DesignCase (DESIGN-*)    a module within a Feature
       └─ DevelopmentTask (TASK-*) execution unit
```

A `DesignCase` attaches to a `Feature` (`feature_id`), never to an INPUT-*.
A `DevelopmentTask` can be created either under a **confirmed** `DesignCase`
(exactly one live design TASK) or as a source-backed task with an explicit
`feature_id` and one or more approved `CR-*` sources. Source-backed tasks do not require a DESIGN and are created in one
transaction. Explicit `TaskDependency` rows add task ordering constraints. Claim checks both
these task dependencies and unsatisfied transitive upstream Features.
There is no intermediate draft or batch generation step; historical layer
removal reasons are in [design rationale: objects](design-evolution.md#objects).

Changing something that already has work built on it goes through a
`ChangeRequest` (CR-\*).

There is no RequirementItem/REQ-*, TaskPlan/PLAN-* or TaskDraft/DRAFT-* layer;
requirement clarification feeds directly into design, and confirmed designs
create tasks directly. See [object boundaries and removed layers](design-evolution.md#objects).
Task context is read from its sources, without a persisted task package;
PR and merge results are recorded manually, without git-host or CI polling.
The reasons are documented under [agent context](design-evolution.md#agents)
and [quality and PR](design-evolution.md#quality).

## Contents

- [Authentication and users](#authentication-and-users)
- [Delivery items and stage runs](#delivery-items-and-stage-runs)
- [交付验证与完成审计](#delivery-audit)
- [State machines](#state-machines)
  - [Shared 3-state lifecycle](#shared-3-state-lifecycle)
  - [Suspect links](#suspect-links)
  - [DevelopmentTask state machine](#developmenttask-state-machine)
- [Soft delete / recycle bin](#soft-delete--recycle-bin)
- [Projects](#projects)
- [Source Requirements](#source-requirements)
- [Project Constitution](#project-constitution)
- [Features](#features)
- [Glossary Terms](#glossary-terms)
- [Design Cases](#design-cases)
- [Development Tasks](#development-tasks)
- [Change Requests](#change-requests)
- [Agent task endpoints (business-number keyed)](#agent-task-endpoints-business-number-keyed)
- [Agent Project Templates](#agent-project-templates)
- [Skills (OpenWebUI / MCP)](#skills-openwebui--mcp)
- [Diagrams](#diagrams)
- [Health](#health)
- [Offline demo](#offline-demo)

---

## State machines

### Shared 3-state lifecycle

`SourceRequirement`, `Project Constitution` and `DesignCase`
share one lifecycle (`app/services/lifecycle.py`):

```
draft  ⇄  needs_clarification   →   confirmed
```

- `draft` / `needs_clarification` are **EDITABLE**. `needs_clarification` is a
  draft blocked on a human answer (kept for multi-person collaboration).
- `confirmed` is **LOCKED** and stays locked; `reopen` is the only way back.

There is no `needs_review` and no propagation between objects (see [design rationale: changes](design-evolution.md#changes)):
"my upstream moved" is a property of an EDGE, derived on read from version
snapshots, not a status written onto the downstream object. A status therefore
only ever describes how far that object's own content got. See
[Suspect links](#suspect-links).

### Suspect links

Confirming a `DesignCase` records the version number of its FEATURE-\* (its
only upstream since the REQ-\* layer was removed); creating a task
records the design version it came from
(table `basis_link`, `app/services/suspect_service.py`). Reading a design or a
task compares those recorded numbers against the upstream's current version:

```
suspect  ⇔  upstream.current_version > recorded basis_version
```

Nothing is stored on the object and nothing cascades. The payload carries
`suspect: bool` and `suspect_upstream: [{upstream_type, upstream_id,
basis_version_number, current_version_number}]` on design detail/list, task
detail/list, the agent task read and `project-context`.

**A suspect edge gates nothing** (see [design rationale: changes](design-evolution.md#changes)) — it says a human should
look. Digesting it:

| Situation | Action |
|---|---|
| Read the change, the downstream still holds | `POST .../realign-basis` (re-records the basis; no content or status change) |
| It no longer holds | `reopen` → revise → `confirm` (re-confirming records a fresh basis) |

Reopening a design drops its basis (nothing settled, nothing to be stale
against). Objects confirmed before this feature existed carry no basis and read
as not suspect until their next confirm or realign.

Actions (each entity wires the same shape):

| Action | Transition |
|---|---|
| create | → `draft` |
| `PATCH` (status) | `draft ⇄ needs_clarification` (the only PATCH-able status moves) |
| `confirm` | `{draft, needs_clarification}` → `confirmed` |
| `reopen` | `confirmed` → `draft` (the only way to edit a locked object) |

### DevelopmentTask state machine

Sourced from `app/services/development_task_service.py`. The happy path is the
top row; the rest are the documented ways back.

```
 (create_task)──► claimable ──claim──► claimed ──start-pre-review──► pre_reviewing
                     ▲                                                    │
                     │                              approve ──► ready_for_development
                     │                                                    │ start-coding
                     │                                                    ▼
                     │                                              in_progress
      reopen ◄─ needs_revision ◄── reject                            │ mark-ready-for-pr
                     ▲                                                    ▼
                     │                                              ready_for_pr
                     │                        withdraw-from-pr ◄─────────┤
    release ─────────┤ (claimed / pre_reviewing / ready_for_development)  │ link-pr, mark-pr-merged,
                     │                                                    │ mark-completed
  reopen-completed ◄─┴──────────────────────────────── completed ◄───────┘
```

Notes:

- **Pre-review start is optional.** Approve/reject accept `claimed` or
  `pre_reviewing`; the diagram shows the explicit-marker path. Authenticated
  review records use the current user. Approval follows the applicable human
  or manager authorization for the task.
- **`claimable` ≠ claimable right now.** A generated task is stored `claimable`
  ("created, awaiting claim"). Whether it can actually be claimed is computed
  live from the transitive Feature graph and explicit task dependencies at claim time — `POST .../claim` returns `409` if
  an upstream Feature is unsatisfied. For display, the list batches
  `claim_ready` / `waiting_on_feature_numbers` (see `feature_service`).
- **Freeze point is `claim`** (see [design rationale: objects](design-evolution.md#objects)): an unclaimed task's content fields
  are still editable. Source-backed tasks in `needs_revision` can also edit their
  contract; other claimed states reject content edits (409).
- **Quality gate** (`mark-ready-for-pr`): the latest run for each role
  (`coder`, `qa`) must be `passed`, the `qa` run must be newer than the `coder`
  run, and `blocking_issues` must be empty.
- **PR phase is manual record-keeping** (see [design rationale: quality](design-evolution.md#quality)): Harnoq never calls a
  git host. `link-pr` records the agent-reported PR, `mark-pr-merged` records a
  human's confirmation that it merged, `mark-completed` requires that merged
  record for the current attempt.
- **`release`** returns a not-yet-coding task to the pool (`claimable`), clearing
  the claim; from `ready_for_development` it also discards the pre-review
  approval. Refused once coding started (that is a handover, not a release).
- **`reject-pre-review`** sends the task to `needs_revision` (single
  destination); `reopen` brings it back to `claimable` once the linked design is
  re-confirmed, or a source-backed task contract has been corrected.
- **`reopen-completed`** sends a merged/completed task back to `claimable`; the
  reason must identify the missed binding-contract clause.

Task enums: `task_type` ∈ `feature, bug_fix, refactor, test, mock_env,
mock_data, doc, infrastructure`; `priority` ∈ `low, normal, high, urgent`;
`complexity` ∈ `small, medium, large, xlarge`; `risk_level` ∈ `low, medium,
high`. Agent-run `role` ∈ `coder, qa`.

---

## Soft delete / recycle bin

Spine entities (project, source requirement, feature, design case, development
task; glossary terms are hard-deleted) support soft delete: `DELETE` stamps
`deleted_at` and moves the row to the recycle bin. Live `GET` reads exclude
soft-deleted rows; a soft-deleted row's `GET` returns 404. `restore` clears
`deleted_at`; `purge` permanently deletes and cascades to the subtree. Both
operate only on rows already in the recycle bin (404 otherwise), so permanent
deletion is a deliberate two-step. Per-resource shape: `DELETE /{resource}/{id}`,
`POST /{resource}/{id}/restore`, `DELETE /{resource}/{id}/purge`, plus a
`GET /projects/{id}/{resource}/trash` listing.

---

## Projects

| Method | Path | Body / Notes |
|---|---|---|
| POST | `/projects` | `{name}` → 201 |
| GET | `/projects` | list (live only) |
| GET | `/projects/trash` | soft-deleted projects |
| GET | `/projects/{project_id}` | |
| PATCH | `/projects/{project_id}` | `{name}` |
| DELETE | `/projects/{project_id}` | soft delete |
| POST | `/projects/{project_id}/restore` | |
| DELETE | `/projects/{project_id}/purge` | permanent (cascades) → 204 |

---

## Source Requirements

Raw-material layer (INPUT-\*): free-text user material mined by conversation.
Shares the [4-state lifecycle](#shared-3-state-lifecycle).

| Method | Path | Body / Notes |
|---|---|---|
| POST | `/projects/{project_id}/source-requirements` | `{title, source_type, raw_content, ...}` → 201 |
| GET | `/projects/{project_id}/source-requirements` | list |
| GET | `/projects/{project_id}/source-requirements/trash` | |
| GET | `/source-requirements/{id}` | |
| PATCH | `/source-requirements/{id}` | mutable fields; status only `draft ⇄ needs_clarification` |
| POST | `/source-requirements/{id}/confirm` | → `confirmed` |
| POST | `/source-requirements/{id}/reopen` | `confirmed` → `draft` |
| GET | `/source-requirements/{id}/versions` | content-history, newest first |
| POST | `/source-requirements/{id}/versions/{version_number}/restore` | copy snapshot into a new latest version |
| DELETE / restore / purge | `/source-requirements/{id}[/restore\|/purge]` | see [Soft delete](#soft-delete--recycle-bin) |

`source_type` ∈ `initial_document, new_feature, requirement_change, bug_report,
test_feedback, meeting_minutes, manual_note`. Content edits append a new version;
restoring version N creates a new latest version copied from N (no counter rewind).

---

## Project Constitution

One governance record per project (a singleton, no id in the path). Shares the
[4-state lifecycle](#shared-3-state-lifecycle). Design may not start until it is
confirmed.

**Purpose & scope.** The constitution is the project's shared agreement and
global background — fixed stack, quality bar, UI conventions, NFR baseline,
boundaries — so the design and development stages can understand the whole
project and honour common conventions. It is a **cross-cutting background
constraint, not part of any design**: a design is made *under* it, not derived
*from* it. Consequently it sits **outside the feature→design→task cascade**
(like requirements and the glossary): revising it updates the agreement that
takes effect for all *subsequent* design/development, and does **not** flag
existing features or designs for re-review. Honouring the new agreement happens
at build time; a rare fundamental change (stack swapped, boundaries redrawn) is
handled by a human re-reviewing, not by a standing cascade.

| Method | Path | Body / Notes |
|---|---|---|
| GET | `/projects/{project_id}/constitution` | the singleton (or empty) |
| PATCH | `/projects/{project_id}/constitution` | upsert + edit while `draft`/`needs_clarification` |
| POST | `/projects/{project_id}/constitution/confirm` | → `confirmed` |
| POST | `/projects/{project_id}/constitution/reopen` | → `draft` |
| GET | `/projects/{project_id}/constitution/versions` | content-history |
| POST | `/projects/{project_id}/constitution/versions/{version_number}/restore` | |

---

## Features

The plan/split unit between INPUT-\* and DESIGN-\* (FEATURE-\*). Features carry
the feature dependency graph; explicit TaskDependency records separately add task-level ordering constraints
(see [design rationale: dependencies](design-evolution.md#dependencies)). There is no
structured requirement layer between a Feature and its DesignCase-\*s
(`RequirementItem`/REQ-\* has been removed): requirement mining is part of
the DESIGN stage method instead.

| Method | Path | Body / Notes |
|---|---|---|
| POST | `/projects/{project_id}/features` | `{name, orientation?, subdomain_type?, foundation_kind?, is_spike?, depends_on_feature_ids?}` → 201 |
| GET | `/projects/{project_id}/features` | list |
| GET | `/projects/{project_id}/features/trash` | |
| GET | `/features/{feature_id}` | |
| PATCH | `/features/{feature_id}` | mutable fields incl. dependency edges |
| POST | `/features/{feature_id}/assignee` | `{assignee}` |
| GET | `/features/{feature_id}/versions` | content-history |
| POST | `/features/{feature_id}/versions/{version_number}/restore` | |
| DELETE / restore / purge | `/features/{feature_id}[/restore\|/purge]` | see [Soft delete](#soft-delete--recycle-bin) |

`orientation` ∈ `foundation, vertical`; `subdomain_type` ∈ `core, supporting,
generic`; `foundation_kind` ∈ `architecture, ui` (null for non-foundation);
`is_spike` flags a `core` feature validated first.

---

## Glossary Terms

Always-editable project glossary; terms are hard-deleted (no recycle bin) and
carry no lifecycle gate (see [design rationale: governance](design-evolution.md#governance)).

| Method | Path | Body / Notes |
|---|---|---|
| POST | `/projects/{project_id}/glossary-terms` | `{term_native, term_en, definition_en, avoid?}` → 201 |
| GET | `/projects/{project_id}/glossary-terms` | list |
| GET | `/glossary-terms/{term_id}` | |
| PATCH | `/glossary-terms/{term_id}` | |
| DELETE | `/glossary-terms/{term_id}` | permanent |

---

## Design Cases

A module-level solution (DESIGN-\*) under a Feature. Shares the
[4-state lifecycle](#shared-3-state-lifecycle).

| Method | Path | Body / Notes |
|---|---|---|
| POST | `/projects/{project_id}/design-cases` | `{feature_id (or feature_number), ...}` → 201 |
| GET | `/projects/{project_id}/design-cases` | list |
| GET | `/projects/{project_id}/design-cases/trash` | |
| GET | `/design-cases/{id}` | |
| PATCH | `/design-cases/{id}` | the confirm fields (problem_statement, selected_solution, selection_reason, risks, test_strategy, acceptance_criteria) + optional `rejected_solutions` |
| POST | `/design-cases/{id}/confirm` | requires the confirm fields non-empty |
| POST | `/design-cases/{id}/reopen` | → `draft` |
| POST | `/design-cases/{id}/realign-basis` | digest a suspect edge, see [Suspect links](#suspect-links) |
| POST | `/design-cases/{id}/tasks` | **create one task** from this confirmed design: `{title, task_type, priority?, complexity?, risk_level?, description?, acceptance_criteria?, constraints?, non_goals?}` → 201 |
| GET | `/design-cases/{id}/versions` | content-history |
| POST | `/design-cases/{id}/versions/{version_number}/restore` | |
| DELETE / restore / purge | `/design-cases/{id}[/restore\|/purge]` | see [Soft delete](#soft-delete--recycle-bin) |

`POST /design-cases/{id}/tasks` creates the one design-backed TASK. A second
live task for the same DESIGN is rejected; additional work belongs in a
separate source-backed TASK when it has an approved CR source. Integration and
System verification use direct stage items and one stage run each.

---

## Development Tasks

The execution unit (TASK-\*). See the [state machine](#developmenttask-state-machine).

| Method | Path | Body / Notes |
|---|---|---|
| GET | `/projects/{project_id}/tasks` | list; each carries `claim_ready` / `waiting_on_feature_numbers` (live Feature-graph gate, batched — display only) |
| GET | `/projects/{project_id}/tasks/trash` | |
| GET | `/tasks/{id}` | |
| PATCH | `/tasks/{id}` | content fields — claimable, or source-backed needs_revision; other states reject |
| GET | `/tasks/{id}/timeline` | merged status/agent-run/PR timeline |
| POST | `/tasks/{id}/claim` | `{assignee}` → `claimed` (409 if upstream Feature unsatisfied) |
| POST | `/tasks/{id}/release` | `{claimed, pre_reviewing, ready_for_development}` → `claimable` |
| POST | `/tasks/{id}/start-pre-review` | `claimed` → `pre_reviewing` |
| POST | `/tasks/{id}/approve-pre-review` | `{reviewer}` → `ready_for_development` |
| POST | `/tasks/{id}/reject-pre-review` | `{comment, reviewer?}` → `needs_revision` |
| POST | `/tasks/{id}/reopen` | `needs_revision` → `claimable` (design-backed tasks need the design re-confirmed; source-backed tasks need their contract corrected) |
| POST | `/tasks/{id}/start-coding` | `ready_for_development` → `in_progress` |
| POST | `/tasks/{id}/agent-runs` | `{role, status, summary, design_number?, criteria_verdicts?}` → `{agent_run, task}` |
| POST | `/tasks/{id}/mark-ready-for-pr` | quality gate → `ready_for_pr` |
| POST | `/tasks/{id}/link-pr` | `{pr_number, branch_name?, pr_url?}` → `{pull_request_record, task}` |
| POST | `/tasks/{id}/mark-pr-merged` | no body — records a human's merge confirmation → `{task, pull_request_record}` |
| POST | `/tasks/{id}/withdraw-from-pr` | `{reason}` `ready_for_pr` → `in_progress` (refused once PR merged) |
| POST | `/tasks/{id}/mark-completed` | `ready_for_pr` → `completed` (needs a merged PR for this attempt) |
| POST | `/tasks/{id}/reopen-completed` | `{reason}` `completed` → `claimable` (reason must cite the missed binding-contract clause) |
| POST | `/tasks/{id}/realign-basis` | digest a suspect edge, see [Suspect links](#suspect-links) |
| DELETE / restore / purge | `/tasks/{id}[/restore\|/purge]` | see [Soft delete](#soft-delete--recycle-bin) |

Business-number agent routes (preferred for integrations and tools):

| Method | Path | Body / Notes |
|---|---|---|
| POST | `/projects/{project_number}/tasks/from-sources` | Create one TASK atomically from approved `change_request_numbers` and an explicit capability `feature_number`. |
| POST | `/projects/{project_number}/tasks/{task_number}/change-requests` | Attach one or more approved `CR-*` records to a change-request TASK. |
| POST | `/projects/{project_number}/tasks/{task_number}/dependencies` | Add explicit `depends_on_task_numbers`; cycles and missing tasks are rejected. |
| GET | `/projects/{project_number}/tasks/{task_number}` | Returns source_kind, feature number, direct CR sources, dependencies, and QA evidence without raw ids. |
| POST | `/projects/{project_number}/tasks/{task_number}/update` | MCP `harnoq_task_update`: edit title, goal, acceptance_criteria, constraints, non_goals and task classification while claimable, or source-backed and needs_revision. Omit unchanged fields. No status, ownership or source changes. |

When a source-backed task's contract becomes outdated, move it to
`needs_revision` through the appropriate rejection action, read its current
sources, update the authorized contract changes with `harnoq_task_update`, read
back, then call `harnoq_task(action="reopen")` on the same task. Preserve all
unwaived obligations. Updating the contract does not mark tests or sources as
passed and does not replace the remaining Build/QA and completion checks.
A design-backed rejected task instead requires DESIGN revision and confirmation.

`agent-runs` status: `coder` reports `passed`/`failed`; `qa` reports
`passed`/`needs_fix`, plus `blocked` for either. A design-backed QA run carries
`design_number` and one verdict per DESIGN criterion. A source-backed QA run
uses the TASK's own acceptance criteria and must record executable test evidence.
INTEG/SYS items instead carry exact executable evidence in their stage run.

Source mapping is many-to-one: one design has at most one design-backed TASK,
while multiple approved CR sources may point to one source-backed TASK.

Integration and System use one project-wide stage run apiece. After inventory
validation, the agent creates or reconciles direct INTEG/SYS items, runs their
checks, and records ordinary fixes and executable evidence on the items. Code
changes use one branch and one final merge reference per run. Ask the user for
required resources, product decisions, waivers, significant extra cost,
destructive actions or irreducible ambiguity. A confirmed design or contract
hole follows the change workflow.

---

## Change Requests

CR-\* is the governance record for changing something that already has work
built on it. Editing a draft needs no change request. Applying one records that
the edit was made — it never edits an object itself, and the downstream
staleness signal is [suspect](#suspect-links), computed separately.

```
proposed → analyzing → approved → applied → closed
   └───────────┴───────────┴──── closed (dropped/rejected)
```

| Method | Path | Body / Notes |
|---|---|---|
| POST | `/projects/{project_id}/change-requests` | `{target_type, target_id?, reason}` → 201; `target_type` ∈ `design_case, new` (`new` takes no id) |
| GET | `/projects/{project_id}/change-requests` | list |
| GET | `/projects/{project_id}/change-requests/trash` | |
| GET | `/projects/{project_id}/change-impact` | `?target_type=&target_id=` — what WOULD be affected, creating nothing |
| GET | `/change-requests/{id}` | |
| PATCH | `/change-requests/{id}` | `reason` only, and only before approval |
| POST | `/change-requests/{id}/analyze` | walks the chain, stores `impact` |
| POST | `/change-requests/{id}/approve` | records approval under the applicable user authorization |
| POST | `/change-requests/{id}/apply` | records that the edit was made; sets `applied_at` |
| POST | `/change-requests/{id}/close` | `{resolution}` required |
| DELETE / restore / purge | `/change-requests/{id}[/restore\|/purge]` | see [Soft delete](#soft-delete--recycle-bin) |

Each `impact` entry is `{object_type, object_id, object_number, status,
response}`, where `response` follows the [change policy](design-evolution.md#changes) for how far
that downstream object got — notably, **a shipped task defaults to an
increment** (a new requirement, its design, its task) rather than a rewrite.

After a CR is approved, its implementation may be assigned directly to an
existing or newly created `source_kind=change_request` TASK. Several approved
CRs may share that TASK when they have the same executable owner and explicit
feature. A CR does not force creation of a new DESIGN.

Integration and System are project-wide stage runs. Integration scans the
production entry and every declared cross-design seam first, records each
distinct finding as its own INTEG item, then creates one project-wide repair
TASK sourced by all open findings. That TASK uses one branch and one PR.
Each item still needs its own current executable evidence after the merge.
System repairs are recorded directly on affected SYS items. An evidence-only
run needs no PR. Only a confirmed design or contract hole enters
Change. If that changes the capability basis, abandon the active run with a
reason and start a fresh Integration run after the changed capability work.
Missing required user resources block the affected item or run until supplied
and verified, or until the user explicitly authorizes a waiver.

For a CR that is required, the manager may analyze and approve an ordinary
repair within existing authorization without another user confirmation. New
scope, product trade-offs, lower standards, waivers, significant extra cost
and destructive actions still require the applicable authorization.

Stage-run endpoints use project and run business numbers:

| Method | Path | Purpose |
|---|---|---|
| POST | `/projects/{project_number}/delivery-stage-runs/{integration\|system}` | Start a run |
| PATCH | `/projects/{project_number}/delivery-stage-runs/{run_number}` | Update branch, revision, evidence or resource blocker |
| POST | `/projects/{project_number}/delivery-stage-runs/{run_number}` | Same update for existing clients; run numbers are distinguished from stage names |
| POST | `/projects/{project_number}/delivery-stage-runs/{run_number}/update` | MCP-compatible update of the same run fields |
| POST | `/projects/{project_number}/delivery-stage-runs/{run_number}/close` | Close after current inventory and item evidence pass |
| POST | `/projects/{project_number}/delivery-stage-runs/{run_number}/abandon` | Record `reason` and release a run whose basis changed |

Generated manager instructions isolate a new repair in a separate git worktree
when the current directory belongs to another task or has uncommitted work.
Preserve that directory; send coder/QA the exact repair directory and perform
its git operations there. A dirty sibling worktree is not an approval gate.

---

## Agent task endpoints (business-number keyed)

A parallel task surface for downstream coding agents, keyed by business numbers
(`P-*` / `DESIGN-*` / `TASK-*`) and hiding internal ids
(`app/api/agent_tasks.py`). Same service logic as the page API above.

| Method | Path |
|---|---|
| POST | `/projects/{project_number}/designs/{design_number}/tasks` (create task) |
| GET | `/projects/{project_number}/tasks/{task_number}` (joins the linked DESIGN-\* body under `design`) |
| POST | `/projects/{project_number}/tasks/{task_number}/{action}` |

`{action}` ∈ `claim, release, start-pre-review, approve-pre-review,
reject-pre-review, reopen, reopen-completed, start-coding, agent-runs,
mark-ready-for-pr, link-pr, mark-pr-merged, withdraw-from-pr, mark-completed,
realign-basis`.

---

## Agent Project Templates

| Method | Path | Notes |
|---|---|---|
| GET | `/projects/{project_id}/agent-template` | the generated per-project agent template set (coder/qa/manager configs, MCP config) |

---

## Skills (OpenWebUI / MCP)

These face the skill adapter, not the page API. Discover the spec from
`GET /openapi.json` or `GET /openapi.yaml` (both runtime-rewritten to the request
host). Skill calls share the `/skills` prefix and POST a skill envelope. Every
response carries the fixed 6-key envelope: `object_type`, `object_id`, `status`,
`change_summary`, `open_questions`, `next_step`, plus the resource payload.

Reads: `harnoq_project_context` (`/skills/project-context` — hoists
`active_stage_method`, the binding method for the current pipeline stage) and
`harnoq_task_progress` (`/skills/task-progress`). Task/design read detail also
comes through the business-number GET above.

Writes (each `/skills/<name>`), grouped by object:

| Object | Write skills |
|---|---|
| Source requirement | `source-requirement-create`, `-update`, `-append-content`, `-confirm`, `-reopen` |
| Constitution | `constitution-update`, `-confirm`, `-reopen` |
| Feature | `feature-create`, `-update`, `-delete`, `-assign` |
| Glossary | `glossary-create`, `-update`, `-delete` |
| Change request | `change-request-create`, `-analyze`, `-approve`, `-apply`, `-close` (plus read actions `get`/`list`/`impact`) |
| Design case | `design-case-create`, `-update`, `-confirm`, `-reopen`, `-realign-basis`, `-create-task`, `-delete`, `-restore` |
| Task write-back | via the MCP `harnoq_task` tool → the business-number agent endpoints above |

`design-case-create-task` is how the AI turns a confirmed design into a task —
there is no separate human-only generation gate (see [design rationale: objects](design-evolution.md#objects)).

Common write body: `{project_number, actor?, session_id?, idempotency_key?,
action, payload}`. Writes are **not idempotent** — every call re-runs (see
`skill_service.run_write_skill`); `actor`/`session_id`/`idempotency_key` are
optional audit-trail fields. Reads need only `{project_number}`.

**Identifiers:** the skill layer only accepts/returns business numbers —
`project_number` (`P1`), `source_requirement_number` (`P1-INPUT-6`),
`feature_number` (`P1-FEATURE-5`), `design_number` (`P1-DESIGN-3`), `task_number`
(`P1-TASK-7`) — never the raw database id.

---

## Diagrams

Rendered client-side by [mermaid](https://mermaid.js.org/), vendored at
`app/static/vendor/mermaid.min.js` (no CDN — the app works offline). It is ~2.5MB,
so only the templates that draw something load it, via base.html's
`vendor_scripts` block:

| Page | Diagram |
|---|---|
| Features list | Feature dependency DAG (`flowchart LR`), coloured by `subdomain_type` |
| Feature detail | Ownership chain under that feature: FEATURE → DESIGN → TASK |
| Change requests | Impact graph for the selected target, drawn from the same impact list shown in the table below it |

Every diagram is a second view of data that is also on the page as a table, so a
diagram that fails to render degrades the page rather than breaking it. Layout is
mermaid's job: the previous hand-rolled Sugiyama layering (~240 lines) is gone.
`tests/js_diagram_smoke.mjs` feeds every definition the app builds to mermaid's
parser (needs jsdom; see `tests/test_app_js_smoke.py`).

---

## Health

| Method | Path | Notes |
|---|---|---|
| GET | `/healthz` | `200 {status:"ok", database:"connected"}` or `503 {status:"degraded", ...}` |

---

## Offline demo

`flask demo-phase1` drives one task end-to-end (SourceRequirement →
`completed`) against a Flask test client, no network, no pre-existing data. It
prints one line per step and ends with `phase1 closed loop: OK`.

```bash
uv run flask --app main demo-phase1
```

The driver is `app/demo/phase1_flow.py`. Every step, including task creation
and PR link/merge confirmation, goes through the full HTTP pipeline via the
client; there is no "demo mode" in production or quality-gate code.

## Authentication and users

Normal deployment requires authentication. Web login uses an HttpOnly JWT cookie;
cookie-authenticated writes also require CSRF. API/MCP clients send a personal
Bearer JWT. Roles and work ownership are checked against current user records;
there is no project-membership isolation yet. Setup is in [README](README.md).

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/auth/login` | `{username, password}`; establishes web session |
| GET | `/auth/me` | current user and CSRF token |
| POST | `/auth/logout` | revokes session and clears cookie |
| POST | `/auth/password` | `{current_password, password}` |
| GET / POST | `/auth/tokens` | list credentials / create with `{name, days?}` |
| DELETE | `/auth/tokens/{credential_number}` | revoke own API credential |
| GET / POST | `/users` | list / create users; subject to admin authorization |
| PATCH / DELETE | `/users/{user_number}` | update / delete user |
| GET | `/users/workload` | work and completion statistics |
| GET | `/users/{user_number}/activity` | recorded activity |
| POST | `/tasks/{id}/assignee` | assign or transfer task ownership |
| POST | `/source-requirements/{id}/assignee` | assign input owner |
| POST | `/delivery-items/{id}/assignee` | assign verification owner |

Source: `app/api/auth.py`, `app/api/users.py`; business-number assignment routes
also exist for agent use. Ownership history is distinct from content versions.

## Delivery items and stage runs

INTEG/SYS are verification obligations, not DESIGNs or direct task sources.
`source_kind=integration|system` is rejected by source-task creation; only
`change_request` is accepted. Ordinary delivery repairs stay in the run/item.
Contract changes use approved CRs and source-backed tasks.

| Method | Path | Purpose |
| --- | --- | --- |
| GET / POST | `/projects/{project_id}/integration-items` | list / create assembly, seam, duplicate items |
| GET / POST | `/projects/{project_id}/system-items` | list / create journey, nfr items |
| GET | `/projects/{project_id}/integration-items/counts` | integration counts |
| GET | `/projects/{project_id}/system-items/counts` | system counts |
| GET / PATCH / DELETE | `/delivery-items/{item_id}` | read / edit / soft delete |
| GET | `/projects/{project_number}/delivery-stage-runs` | execution rounds |
| POST | `/projects/{project_number}/delivery-stage-runs/{stage}` | start integration or system round |
| PATCH | `/projects/{project_number}/delivery-stage-runs/{run_number}` | update round |
| POST | `/projects/{project_number}/delivery-stage-runs/{run_number}/close` | validate evidence/audits and close |
| POST | `/projects/{project_number}/delivery-stage-runs/{run_number}/abandon` | abandon round |

`delivery-items` project routes remain compatibility aliases. Items use
`open/covered/waived`; a waiver needs a reason, and coverage needs exact evidence,
run and revision. Runs use `active/blocked/closed/abandoned`; one project may have
only one active or blocked round. A run cannot close with open obligations,
stale covered evidence, blocked resources or missing current inventory audits.

Integration inventory tools validate, recover and revise complete manifests;
`valid=true` differs from `integration_complete=true`. See the maintained
[delivery and audit rules](#delivery-audit), and the tool schemas in
`skills/openapi.yaml` / `harnoq_mcp/server.py` for payload details.

<a id="delivery-audit"></a>

## 交付验证与完成审计

对象职责、盘点方法与被替代方案的理由见[设计依据：交付验证](design-evolution.md#delivery)。以下记录当前调用规则与完成判据。

### Integration 完成审计

Integration 重入后可能只有复验，没有新缺口、代码、任务或 PR。
完成条件必须能够表达这一轮真实的验证结果。

`harnoq_integration_inventory_validate` 继续校验完整清单并签发创建回执。
`valid=true` 仅表示清单结构有效；返回的 `integration_complete=true` 才表示
当前基线的最终审计及阶段收尾通过。调用者应读取 `completion_errors` 和项目
上下文中的未完成任务/规划，处理后再次提交最终清单。

创建回执绑定任务状态、真实完成事件和已确认的设计内容。独立的
`integration_completion_audit` 表保存完整清单、全部现存 INTEG 的身份快照、
创建映射及完成时间。无需给旧回执表增列；旧回执缺少完成证据，不能被自动
升级为完成审计。

最终审计要求所有义务已覆盖或豁免，并检查每个 covered 项在本轮清单中的
成功测试证据：`role=test`、精确测试调用、退出码为零和原始 stdout/stderr。
仅重写 `covered_by` 不构成复验。正常 Build/QA 完成后也要重提最终清单；
零缺口轮次可以直接提交，不制造用于推进时间戳的任务。

后续交付、设计或清单身份变化使旧审计失效。相同材料的有效重试保留完成时间。
门禁读取真实任务完成和有效审计完成时间，System 的基线也包含有效审计，
因此无任务的集成复验同样能触发后续复验。

Harnoq 校验结构、来源绑定和状态一致性。外部仓库的代码事实及运行输出仍由
执行 Agent 提交；审计回执不等于 Harnoq 独立运行了产品测试。

### 清单恢复与增量提交

首次验收仍提交完整清单。重入或最终补证据时，先调用
`harnoq_integration_inventory_get`：取回已保存的完整清单、当前未完成原因、
每个 covered 项的准确测试引用，以及设计/事项变化。旧审计即使已失效，
材料也可以取回；不存在清单时才要求首次完整提交。

Agent 检查变化后调用 `harnoq_integration_inventory_revise`，携带读取结果的
`validation_id`（作为 `base_validation_id`）和 `revision_token`
（作为 `reviewed_revision_token`）。`evidence_updates` 按 key 添加或替换完整
证据记录；`section_updates` 只提交改变的章节，整章替换，省略表示保留，
空列表表示清空。服务端更新轮次、对齐已记录的事项创建映射，并运行原来的
完整校验器；不会根据事项已关闭就推断接线已修好，也不会自动刷新设计哈希。

读取后清单、任务/设计基线或事项状态变化，会拒绝过期 revision，要求重新读取。
基线变化时，每个 covered 项必须在本次更新中提供准确测试名的重新运行证据，
不能只沿用旧证据。Agent 仍须审查受影响的代码、资源和接线。
无效修改不替换已保存清单；`valid=true` 与 `integration_complete=true` 的区别不变。

组装 payload、处理普通校验错误是 Agent 的工作，不构成需要用户反复指示
“继续”的阻塞。需要用户介入时，应指出具体失败、已尝试的恢复动作，以及
缺失的资源、权限或产品决定。

### 外部资源与真实调用

清单必须包含 `external_resources`。Agent 扫描生产代码和配置，并逐个审计
已确认的能力 DESIGN：列出所需外部资源，或明确说明不需要的原因。资源清单
与各设计引用的资源集合必须一致，避免用空列表默认跳过。

每项资源记录来源、实际配置和状态。`verified` 同时要求生产入口引用、具体
操作和观察结果，以及独立的成功证据 `resource-check`（真实资源就绪）与
`resource-call`（沿配置的产品路径真实调用）；`uses_test_double` 必须为 false。
健康检查成功、目录存在、缺失时能进入 blocked、替身测试通过，都不等于真实
调用成功。

`missing`、`invalid`、`unverified` 可以形成有效清单，但会阻止最终完成。
返回的 `resource_requests` 必须给出支持的来源/版本、放置位置或接入方式、
准备步骤和验证命令，让用户知道需要提供什么。资源已经就绪但还没调用过时，
仍应记录 `unverified`。准备后运行真实路径并重新提交清单。

用户明确排除一项验证时，通过现有 INTEG 豁免流程记录影响，再用
`waiver_item_number` 引用其 live waived 项；资源与豁免项的 source 必须完全相同。
没有资源审计的旧完成记录会失效，需要重新审计。

模块 TASK 验证组件自身的真实依赖调用；Integration 验证部署配置下的实际
接线与调用；System 在此基础上验证用户结果和质量、性能等 NFR。

### 系统验证期间的修复回流

能力修复 TASK 未完成时，两道交付门暂不开放；项目上下文通过
`outstanding_capability_task_numbers` 指明需先完成的任务。Integration/System
基础 Feature 使用来源任务，不要求 DESIGN，也不进入“待设计”清单。
修复任务完成后先恢复 Integration，当前集成审计通过后恢复 System；
已有系统任务之间的依赖不构成退回设计阶段的理由。

用户明确授权的既有事项豁免属于决定记录，可以跨阶段提交
`item_number`、`status='waived'`、`waive_reason`，避免“修复任务等豁免、
豁免又等修复完成”的循环。服务仍检查来源和豁免理由；创建事项、标记 covered、
修改内容/来源/类型仍受原门禁限制。仅记录豁免不应另建能力 CR/TASK/PR，
必要的验证脚本修改归原系统任务。此例外不直接关闭阶段或完成任务。

### System 与门禁的实现边界

能力工作未完成时不进入交付验证；System 必须在当前 Integration 完成后开始。轮次关闭拒绝未处理的 open 项、旧代码版本的 covered 证据及无效清单审计。System 另有自己的清单校验，不把 Integration 成功推断为用户旅程或 NFR 通过。

开发完成时间优先取 TaskEvent 中真实转入 completed 的时间，避免一次基线对齐或负责人更新被误当作新的交付；缺少历史事件的旧数据才回退到任务时间。新的能力交付、设计/事项身份变化会让旧审计过时，需要重入。

实现入口：[交付门禁](../../app/services/delivery_gate.py)、[轮次服务](../../app/services/delivery_stage_service.py)、[集成清单校验](../../app/services/integration_inventory_validator.py)、[系统清单校验](../../app/services/system_inventory_validator.py)、[资源审计](../../app/services/integration_resource_audit.py)。行为验证见 [交付轮次测试](../../tests/test_delivery_stage_runs.py)与[集成完成验收](../../tests/test_integration_completion_acceptance.py)。
