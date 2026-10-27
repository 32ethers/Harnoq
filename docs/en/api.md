# Harnoq REST/JSON API Reference

[中文](../cn/api.md) · [English](api.md)

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
- [Delivery validation and completion audits](#delivery-audit)
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

## Delivery validation and completion audits

See [delivery rationale](design-evolution.md#delivery) for responsibilities and rejected alternatives. The following describes current calls and completion criteria.

### Integration completion audit

An Integration reentry may require only reruns, with no new gaps, code, tasks, or PRs. Completion must represent the actual verification performed in that round.

`harnoq_integration_inventory_validate` validates the full manifest and issues creation receipts. `valid=true` means structurally valid; only `integration_complete=true` means final audit and stage close-out passed for the current baseline. Read `completion_errors` and outstanding work/planning in project context, resolve them, and resubmit the final manifest.

Creation receipts bind task state, real completion events, and confirmed design content. A separate `integration_completion_audit` table stores the full manifest, snapshots of all live INTEG identities, creation mappings, and completion time. Legacy creation receipts lack completion evidence and cannot be promoted automatically.

Final audit requires every obligation to be covered or waived. Each covered item needs successful test evidence in the current manifest: `role=test`, exact test invocation, zero exit code, and raw stdout/stderr. Rewriting `covered_by` does not count as a rerun. Resubmit the final inventory after Build/QA too; a zero-gap round may submit directly without dummy tasks to advance timestamps.

Later delivery, design, or inventory-identity changes invalidate the old audit. Valid retries of identical material preserve completion time. Gates use actual task completion and valid audit completion times; System's baseline includes the audit, so a task-free integration rerun can still trigger subsequent verification.

Harnoq checks structure, source binding, and state consistency. Agents submit external repository facts and execution output; a receipt does not mean Harnoq independently ran product tests.

### Inventory recovery and incremental revisions

The first submission still contains the full manifest. For reentry or final evidence updates, first call `harnoq_integration_inventory_get`. It returns the saved inventory, current blockers, exact test references for covered items, and design/item changes. Even an invalidated audit's material remains recoverable; only an absent manifest requires a first full submission.

After reviewing changes, call `harnoq_integration_inventory_revise` with the returned `validation_id` as `base_validation_id` and `revision_token` as `reviewed_revision_token`. `evidence_updates` adds or replaces complete records by key. `section_updates` replaces entire changed sections; omission preserves a section, while an empty list clears it. The server updates the round, reconciles recorded creation mappings, and runs the full validator. It neither infers repaired wiring from closed items nor refreshes design hashes automatically.

Changes to the manifest, task/design baseline, or item state after reading reject a stale revision and require another read. When the baseline changes, provide fresh execution evidence naming each covered item's exact test in this update. Agents must still inspect affected code, resources, and wiring. Invalid changes do not replace the saved manifest. The distinction between `valid` and `integration_complete` remains.

Payload preparation and ordinary validation recovery are agent responsibilities, not reasons to repeatedly ask the user to say “continue”. When human input is necessary, identify the failure, attempted recovery, and missing resource, permission, or product decision.

### External resources and real calls

The manifest must include `external_resources`. Inspect production code/configuration and every confirmed capability design: identify required resources or explain why none are needed. The resource inventory must match design resource references; an empty list must not silently bypass review.

Each resource records provenance, actual configuration, and state. `verified` requires a production-entry reference, concrete operation and observed outcome, plus separate successful `resource-check` (real resource ready) and `resource-call` (real call through the configured product path) evidence. `uses_test_double` must be false. A health check, existing directory, graceful blocked state, or passing substitute test does not establish successful real use.

`missing`, `invalid`, and `unverified` may appear in a valid inventory but block final completion. Returned `resource_requests` must specify supported source/version, placement or connection method, preparation steps, and verification command. A ready resource not yet called remains `unverified`. Run the real path after preparation and resubmit.

When the user explicitly excludes verification, record the impact through an existing INTEG waiver and reference its live waived item with `waiver_item_number`. Resource and waiver item sources must match exactly. Old completion records without resource audits require re-audit.

Module TASKs verify their component's real dependency calls. Integration verifies actual deployed wiring and calls. System then verifies user outcomes and quality/performance commitments.

### Repair reentry during System verification

Incomplete capability repair tasks close both delivery gates. Project context names them in `outstanding_capability_task_numbers`. Integration/System foundation features use source tasks without requiring DESIGN records or entering the pending-design list.

After repairs, resume Integration first, then System once the current integration audit passes. Dependencies among existing system tasks do not justify returning to design.

An explicitly authorized waiver for an existing item can be recorded across stages with `item_number`, `status='waived'`, and `waive_reason`. This prevents circular waits between repairs and waivers. Source and waiver reasons are still checked; creating items, marking covered, or changing content/source/kind remains gated. Recording a waiver does not require a new capability CR/TASK/PR. Necessary verification-script changes belong to the original system task. This exception does not itself close the stage or complete a task.

### System and gate boundaries

Delivery verification waits for capability completion, and System starts only after current Integration completion. Closing a run rejects open items, stale-revision coverage, and invalid audits. System has its own inventory validation; Integration success is not proof of journeys or NFRs.

Completion time comes from actual TaskEvent transitions into `completed`, avoiding false new deliveries from assignment or baseline updates. Legacy records without events fall back to task timestamps. New delivery or relevant design/item identity changes require reentry.

Implementation: [gates](../../app/services/delivery_gate.py), [runs](../../app/services/delivery_stage_service.py), [integration validation](../../app/services/integration_inventory_validator.py), [system validation](../../app/services/system_inventory_validator.py), and [resource audit](../../app/services/integration_resource_audit.py). Tests: [delivery runs](../../tests/test_delivery_stage_runs.py) and [completion acceptance](../../tests/test_integration_completion_acceptance.py).
