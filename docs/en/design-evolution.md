# Harnoq: design evolution and rationale

[中文](../cn/design-evolution.md) · [English](design-evolution.md)

Harnoq evolved through implementation and use. Extra workflow layers did not always add useful judgment, and passing every module task did not establish product readiness. The resulting design removes intermediaries without independent responsibilities and gives missing decisions, change analysis, and whole-product verification explicit homes.

The sections follow related problems rather than a strict chronology. Historical implementations, considered alternatives, and current rules are distinguished. Interface definitions belong in the [API reference](api.md), connection instructions in the [guide](skills/README.md), and future work in the [roadmap](phased_delivery_plan.md).

- [Scope and responsibility](#scope)
- [Object simplification](#objects)
- [Design and governance](#governance)
- [Change management](#changes)
- [Agent execution](#agents)
- [Delivery verification](#delivery)
- [Identity and engineering support](#identity)
- [Limits](#limits)
- [Terminology](#terminology)
- [Sources](#sources)
- [Implementation and history](#implementation)

<a id="scope"></a>

## 1. How far should the system constrain AI?

The [initial design](initial_design.md) focused on sustained engineering, not isolated code generation. Good local implementations can still omit implicit requirements, cross-module agreements, and quality obligations. Longer contexts and additional roles make a single comprehensive conversation unreliable. Requirements, rationale, bounded tasks, independent verification, and human intervention therefore formed the starting point.

Existing PM/ALM products were considered. The choice to build Harnoq rested on AI reducing CRUD/state-machine implementation costs enough to justify control over the data model, stage methods, and context size. This was not evidence that other products are generally inadequate. An unattended model-pool scheduler was considered but never adopted or implemented.

Early plans included provider integration, PR/CI checks, and automatic merge progression. Responsibility narrowed to project records, state, evidence, and reported PR/merge facts; agents use existing repository tools. Maintaining another provider client expands scope without replacing product judgment.

Today, people decide goals and trade-offs, agents perform planning and implementation, and Harnoq records evidence and enforces mechanically checkable conditions. A valid transition cannot prove human consent, and a test report cannot prove universal correctness.

<a id="objects"></a>

## 2. Which workflow layers have an independent responsibility?

### RequirementItem was removed, restored, and removed again

An early REQ layer normalized raw material into structured requirements. It was merged into SourceRequirement, restored for traceability/change analysis, then removed again. Requirement analysis remains valuable without a separate persistent requirement entity.

The independent layer added questions, fields, confirmation, and coverage checks without reliably eliciting unstated requirements. Premature atomic requirements also encouraged boundaries around items rather than around decisions to encapsulate. INPUT now preserves holistic source material; DESIGN records detailed requirements alongside concrete solutions that a person can inspect.

The intermediate steps mattered. After the first removal, coverage temporarily attached to INPUT. When REQ returned, atomic items belonged to features and were covered by non-overlapping designs. The final removal followed repeated attempts to improve questions: dimension checks found blanks, but a concrete draft that people could correct elicited more useful feedback.

This reduces duplicate approvals while giving up a fully enforced atomic requirements matrix. Feature has no direct INPUT foreign key; design excerpts and references are semantic provenance, not database-enforced exhaustive coverage.

INPUT confirmation also changed. Removing its gate let the workflow jump to constitution as soon as a document existed. Confirmation returned to ensure all active INPUTs are clarified and accepted first, without reinstating structured field checklists or REQ.

### TaskPlan and TaskDraft disappeared in separate steps

Twelve inspected plans had empty split strategies; eleven contained only one draft. A plan was effectively one-to-one with a design, so independent approval added no decision. Its responsibility moved into DesignCase, initially retaining drafts under designs.

TaskDraft then proved to be a field copy of the final task. Generation duplicated content and introduced two editing regimes. Direct task creation replaced it, moving the definition lock from generation to claiming.

```text
Early:          DESIGN → TaskPlan → TaskDraft → TASK
Without plans:  DESIGN → TaskDraft → TASK
Current:        confirmed DESIGN → TASK (claimable; definition locks on claim)
```

Removing PLAN merged design and split approval while initially retaining human-only generation. Removing DRAFT also allowed agents to create tasks. Design confirmation and applicable pre-review remained; copying fields no longer carried a separate approval.

DesignCase remains the current design record, not a remnant of removed planning layers. An earlier exception allowed multiple tasks for one oversized design, but QA required each task to prove the entire design contract. Current behavior permits at most one active design-backed task per active design. Reassess oversized module boundaries instead of evading full-contract acceptance through repeated splits. Confirmation does not automatically create a task. Unclaimed definitions are editable; source-backed contracts can also be revised in `needs_revision`.

### Why Feature remains

Requirements originally grouped concerns while tasks grouped deliverables; binding them nearly one-to-one made cross-cutting work awkward. DESIGN first covered multiple REQs and temporarily served as the feature unit. Scope, ownership, and execution order nevertheless needed to exist before designs did, and a feature could contain several module designs. Feature therefore retained an independent planning and ownership role.

`INPUT → constitution → FEATURE → DESIGN → TASK` is workflow order, not an unbroken foreign-key chain. INPUT, constitution, and Feature belong to the project; DESIGN belongs to Feature; a design-backed task references DESIGN. Feature progress derives from active designs instead of another confirmation state machine.

<a id="dependencies"></a>

### Dependencies express distinct prerequisites, not copied graphs

REQ, Feature, Draft, and Task once carried copied dependencies that drifted after edits. These collapsed into a feature DAG. CR source-backed work later introduced explicit TaskDependency edges for additional task-level prerequisites, not copies of the feature graph.

Neither graph is a runtime call graph. Claiming checks explicit task prerequisites and transitive feature prerequisites; related gates recheck upstream conditions. Read-time availability avoids stale persisted blocking flags.

Methods separate feature boundaries from scheduling, preventing neat graphs from forcing technical-layer decomposition. The emphasis also shifted from removing redundant edges to detecting missing prerequisites: an extra edge delays work, but a missing edge can authorize development against a nonexistent contract. Transitive checks prevent deeper reopened prerequisites from being overlooked.

Confirmed designs mean design work is complete. Implementation additionally requires corresponding tasks and completion of all active work; empty features are not complete by default.

<a id="governance"></a>

## 3. How does design remain thorough without REQ?

### Global rules and local solutions need different homes

The constitution consolidates agreed stack, quality bar, UI conventions, NFR baseline, and boundaries after requirements are clarified. It is a project singleton without its own business number. It avoids repeating global decisions in every design or manufacturing a task for each performance constraint.

Consolidation occurs after clarification because earlier information is insufficient. It deduplicates and elevates agreements, rather than repeating requirements analysis.

The glossary has a different update frequency. It remains independently editable without a confirmation gate, is delivered with planning context, and is available through read-only tools downstream. Models may not know which term they misunderstand, so retrieval alone is insufficient. Earlier constitution reopening cascaded into design review, making glossary updates particularly costly; that cascade was later removed, but the distinction in update frequency remains.

Terms retain the user's native wording, English identifier terminology, and prohibited alternatives. Definitions are stored once to avoid bilingual drift. Terms are project-local identifiers without separate business numbers; RAG is not used for glossary lookup.

<a id="boundaries"></a>

### Foundations and module contracts precede local implementation

Treating architecture as a skeleton built by the first task delayed and scattered load-bearing decisions. Architecture Foundation now defines cross-cutting policies, persistence, runtime topology, and extension contracts. UI projects can also have a UI Foundation for the design system, shell, and shared interactions. Both reuse Feature/DESIGN instead of adding planning entities.

This does not mandate a large framework or identical components in every project. A minimal walking skeleton should prove important connections. Boundaries follow independently changing decisions, information hiding, and bounded contexts, not processing stages or model/service/view layers. Foundation/vertical and core/generic labels describe a conclusion; they are not decomposition algorithms. Reuse mature generic capabilities and validate risky core capabilities early.

Designs specify operations, inputs/outputs, preconditions, postconditions, failures, and usage relationships. Require only interfaces and persistence the module owns; do not invent tables for a pure UI module. Internal functions and file layout remain implementation choices.

Choices affecting boundaries, dependency direction, persistence, or runtime topology must be named and justified in design. Shared decisions return to the constitution. “Not specified in the constitution” cannot mean “leave undecided”. Such obligations became standing guidance rather than rules hidden in rounds skipped when a feature map already exists.

<a id="methods"></a>

### Make decisions inspectable instead of prescribing ceremony

Requiring two alternatives often produced one real proposal and a straw alternative. Current methods prefer a justified recommendation and compare alternatives only when there is a genuine choice. They expose a draft, contracts, acceptance, and mappings from source requirements to structural decisions.

Agents recommend technical choices with reasons where users lack the information to decide, rather than turning engineering into a questionnaire. Some bounded review and questioning practices were borrowed from superpowers, without importing another file-based spec/plan hierarchy.

After REQ removal, a separate requirements-only draft and behavioral quotas briefly reintroduced parallel process. They were withdrawn: requirement understanding and the proposed solution appear in the same design round, separately inspectable. Goals, acceptance, constraints, boundaries, and failures are review dimensions, not separate approval pipelines.

`mined_requirements` later separated requirement content from `design_scope` to make it visible and aggregatable. The digest derives from designs without another stored copy, preserving user-originated versus accepted supplementary requirements. Confirmed designs without records are highlighted; a nonempty field alone does not prove completeness and did not become a new confirmation gate.

Only `active_stage_method` is returned for the current stage. Previously all methods plus the current method duplicated content and attention cost. Method text moved out of HTTP routes, and prompt mirrors were removed. MCP/OpenAPI contract descriptions remain because their clients cannot read each other's declarations.

Generic prompts describe mechanisms and criteria, never fixture-specific universal obligations. Examples must be unrelated to the current test project and span domains. Longer instructions are not evidence of better behavior.

<a id="changes"></a>

## 4. Does an upstream change require downstream rework?

A first simplification unified entity states into a four-state lifecycle, still including cascading `needs_review`. This conflated content maturity with outdated premises. The next step separated three questions:

| Question | Mechanism |
| --- | --- |
| Is this content confirmed? | `draft / needs_clarification / confirmed`; reopen before editing |
| Have its premises changed? | Version snapshots and suspect notices |
| Why change it, who authorized it, and was it applied? | ChangeRequest |

Deletion is separate soft deletion, not maturity. Snapshots support history and restoration. Suspect compares FEATURE → DESIGN → TASK source versions, including missing/deleted sources, but does not block or reopen automatically. Review and align valid content, or revise and reconfirm it.

The constitution stopped cascading independently: designs comply with global constraints without being copies of them. Ordinary revisions should not reopen everything; fundamental stack or boundary changes still need human review. Version numbers distinguish meaningful premises from unrelated timestamps. Feature gained history without a new maturity lifecycle.

### Application checks and reverse traceability

CRs originally could be marked applied without a changed target. Creation-time target versions and reverse links now expose whether versionable targets actually changed. No change rejects application; partial change is reported; unversioned targets are not claimed as verified. CRs still do not edit targets. A version difference proves modification, not correctness.

Multi-target CRs prevent secondary changes from escaping impact analysis. Task targets cover changes to delivered code without changing the design specification. Aligning completed work with a revised baseline requires a reason because it asserts continued code compliance. CRs also had to enter project-context routing: existing models and tools alone did not make the workflow reachable.

### New delivered scope is not failure of the old task

New requirements normally enter approved CR source-backed tasks. Tasks belong directly to a feature and may reference several approved CRs; each CR belongs to at most one task. A new design shell is unnecessary. Reopen completed tasks only when the original delivery failed its original contract. Ordinary delivery repairs are distinguished below.

<a id="agents"></a>

## 5. Context handoff and task completion

### Read authoritative sources by role

Tester and reviewer merged into QA to reduce handoffs while keeping implementation independent from verification. Coder implements; QA tests and reviews contract compliance, scope, and edge cases; manager communicates, coordinates, and judges evidence.

DevelopmentInstruction and TaskPackage duplicated task/design/constitution content and could be absent or stale. Their removal also addressed a practical failure: code could be complete while the missing package left workflow status behind. Task reads now include the bound contract and confirmed constitution verbatim; missing content is explicit rather than hidden by summaries.

Manager reads project context for Planning/Delivery and task context for Build. Coder and QA receive bounded read tools for their task, assigned sources, design, constitution, and glossary. Tool separation is clearer than “read only” prose on a multi-action tool, but does not isolate every possible shell access route.

### Pre-review examines repository reality

Originally pre-review existed only in the UI while manager tools jumped from claiming to dispatch, leaving no usable conversational path. Manager actions now record applicable review while preserving UI controls. Authenticated identity and state do not independently prove conversational approval.

Design confirmation reviews specification consistency and whether criteria are provable within declared scope/upstreams. Pre-review after claim checks real repository assumptions and upstream promises, reporting paths, symbols, or commands rather than repeating the design. Missing upstream work cannot silently become this task's scope.

Review also identifies explicit solution obligations that acceptance criteria cannot distinguish from a weaker implementation. It should not expand design arbitrarily. `start_pre_review` became an optional progress marker: a verdict can be recorded directly from claimed state.

<a id="quality"></a>

### Completion evidence instead of provider/CI state

The per-task CI gate was removed first, retaining PR queries; provider adaptation and polling were removed later when a self-hosted repository could not use the GitHub-only integration. Automatic merge was an abandoned proposal, not an implemented feature later deleted.

Today `completed` means coder/QA checks passed and the current linked PR's merge was recorded. Harnoq does not independently build code or verify remote CI. A first task also need not satisfy CI infrastructure that does not yet exist.

`coding / agent_qa / fixing` collapsed into `in_progress`, with rounds in AgentRun. Cached review/quality mirrors and unobserved transitional states were removed. Gates require the latest coder and QA to pass and QA to follow coder. Current claim attempts delimit PR evidence; an old merged PR cannot complete new rework or prevent withdrawing the current attempt.

### QA produces checkable evidence

`blocked` means verification could not occur, such as unavailable tools or context. It is distinct from `needs_fix`; an earlier pass cannot override the latest blocked run.

QA reports every criterion, verdict, evidence, actual command, exit code, and test counts. `deviates` means working code violates the agreed contract, not a pass. Green component tests cannot establish the product's actual path. Derived `cleared_failures` identifies criteria removed or newly passed without added coverage; it informs judgment without manufacturing a minimum test count.

<a id="evidence"></a>

### Reduce rediscovery cost, not acceptance responsibility

A review initially blamed excessive code and criteria, but recounting found many lines required by the constitution and features required by source material. Criterion quotas and suggestions to reduce acceptance were withdrawn. Actual costs were duplicated mechanisms and repeatedly locating evidence, addressed through integration convergence and historical evidence indexes.

Prior QA evidence is a starting location, not an inherited verdict. Changed wrappers can invalidate behavior even when old files, tests, or criteria are unchanged. Verify the full current contract on its real path.

Estimate costly measurement before running it; coder explains scale and QA explains information value and cost. Authorized decision-makers choose trade-offs. Expensive tests can protect real risks and are not automatically unnecessary. NFR `commitment` is a promise; `direction` guides choices without becoming a hard target. A later review also found an optimization was unnecessary because the real path already met the target; absence of that optimization was not failure.

Another apparent model-compliance failure was traced to stale downloaded configuration. Planning methods are live, but Build templates are generated files. Refresh tools preserve local model choices while updating text, and fingerprints identify delivered versions. Inspect effective configuration before blaming agent behavior.

<a id="delivery"></a>

## 6. Why completed tasks do not prove a shippable product

Local tasks can all pass while the production entry point remains disconnected, seams are unwired, or modules duplicate responsibilities. No individual DESIGN necessarily owns these whole-product obligations.

Integration verifies assembly, seams, and justified convergence. System verifies user journeys and NFR commitments. Early proposals separated getting the product running from behavior-preserving convergence; both later entered Integration. Global duplicate-operation checks before every design were considered but not adopted: bounded local contexts intentionally accept some duplication cost for later whole-product review.

Inventories were not present initially. Avoiding test-case/mock entities was briefly misread as avoiding all new entities, leaving no visible place for outstanding stage obligations. INTEG/SYS items corrected that gap.

### Verification obligations, runs, and scope changes are distinct

Delivery work first used DESIGN/TASK slices, then direct source tasks without designs, then execution runs. A later attempt to batch integration findings into repair tasks was also withdrawn.

| Record | Responsibility |
| --- | --- |
| INTEG / SYS | Verification obligation, source, and coverage |
| RUN | Current code/design baseline, evidence, repairs, resources, and blockers |
| CR source-backed TASK | Approved design/contract changes requiring independently delivered work |

Ordinary checks and repairs stay in items/runs. Approved contract changes may create or reuse source tasks. Current task sources accept approved CRs, not direct INTEG/SYS assignments. Agents perform technical inventory; people decide product trade-offs, waivers, major extra cost/resources, destructive actions, and unresolved requirement ambiguity.

### Current execution evidence, not just test names

Integration proves real production entry points, traces wired reachability, checks directed producer→consumer seams, and analyzes duplicate responsibility. Building is not running; similar names or code are not proof of duplication. Compare contracts, caller semantics, and ownership. Convergence preserves caller behavior and adds structural regression protection.

System inventories source user scenarios and NFR commitments and verifies repeatable product-boundary outcomes. Missing resources mean blocked verification, not mock success. `verifies_nfr` and its derived coverage view identify unclaimed promises and unmatched claims through exact metric matching. Explicit answers are required for commitments where applicable; directions need no artificial claims. Component ownership and system-boundary proof remain distinct.

Evidence binds to a run and revision. Real TaskEvent completion times avoid treating reassignment or baseline alignment as new delivery. Inventory validity is separate from final completion. A zero-gap rerun can create a genuine completion audit without dummy tasks or PRs. New deliveries and relevant design/item changes invalidate prior audits; restore Integration before System.

Saved inventories support retrieval and incremental revisions guarded by read tokens. Baseline changes still require current execution evidence. Resource readiness and real product-path calls need separate proof; health checks or directory existence do not replace calls. Authorized waivers can be recorded across stages to break circular waits, but do not independently complete a task or stage.

See [delivery validation and audits](api.md#delivery-audit) for exact contracts.

<a id="identity"></a>

## 7. Identity, storage, and integration support

Accounts, JWT, browser CSRF checks, and revocable API credentials replaced unreliable free-text identity. Ownership history is separate from content versions: early handoff requires applicable review, active development retains its work, and historical completions stay attributed to the owner at completion. Status elapsed time is not actual effort.

This does not establish full project isolation. Signed-in users currently share projects; administrators manage users and assignment, while members face ownership constraints. Credentials establish identity, not independent human consent. Internal/test paths retain legacy fallbacks.

<a id="storage-ui"></a>

### Current models, explicit rebuilds, and stable UI conventions

Repeated model removal made historical migration chains recreate obsolete entities and sometimes import incompatible current models. The incremental chain was removed in favor of creating missing tables from current models. `create-schema` does not upgrade columns. A specific copy/rebuild utility reads the source and converts a copy after inspecting its cleanup plan; it is not a general migration framework.

Disposable test databases relax durability to avoid repeated synchronization costs. Production uses WAL for concurrent reads/writes. Test-only settings must not be copied into production.

Business numbers are project-scoped, include the project prefix, and are not reused after deletion. Hyphens work in URL path segments. Users/agents see these identifiers; database foreign keys and service/repository calls retain raw IDs.

Lists, add/edit modals, deletion confirmation, and Markdown rendering form stable UI conventions. Mermaid replaced manual SVG layout. Ownership graphs and dependency graphs represent different relationships.

### Shared remote MCP and effective client configuration

Per-client stdio required every developer to check out and run Harnoq. Remote HTTP first used a separate port, then shared the Web/API process at `/mcp`. Synchronous self-HTTP deadlocked the event loop, so co-hosted tools now dispatch in-process to the same business API. Standalone MCP can still use HTTP.

Generated configurations point to the service; clients manage repositories and Git. Manager, coder, and QA use the same project directory and branch baseline, with explicit paths to prevent handoff mismatches.

### Check the complete call chain during refactors

The first REQ removal updated models/services but left skill/MCP imports broken and removed tests that should have been rewritten. Later work restored coverage across UI, API, skills, MCP, and descriptions. CR routing showed the same lesson: an entity and its tools are insufficient if context cannot reach them. Historical test-count thresholds are not permanent quality rules.

### Generic write deduplication was removed

Content-derived idempotency keys could not distinguish a retry from another legitimate identical operation. They swallowed repeated design creation, confirmation after reopening, and deletion after restoration while replaying old success. Writes now execute every time; services decide validity and SkillInvocation records audits. Repeated appends are not automatically deduplicated, an explicit cost preferable to silent lost writes.

<a id="limits"></a>

## 8. Trade-offs and unproven guarantees

Every maintained record should answer an independent question. Maturity, stale premises, approval, and deletion are separate concepts. Bounded execution needs explicit whole-product verification. Removing ceremony must not remove clarification, contract acceptance, or human decisions.

These principles explain both removed REQ/PLAN/DRAFT/packages and retained INPUT/constitution/Feature/Design/Task plus CR/items/runs. Fewer entities is not the goal by itself.

Cheap edits without dependent deliveries need no mandatory CR, so diffs may omit a separate decision reason. Retrospectively confirmed designs do not prove the prescribed reasoning process; timing is not reliable proof either.

- Modified versions do not prove reasonable changes, and valid states do not prove human consent.
- Recorded tests and merges do not prove Harnoq verified remote facts or whole-product delivery.
- Refreshed/fingerprinted templates do not prove agents followed them.
- A single project review does not establish cross-domain or cross-model reliability.

See [open questions](phased_delivery_plan.md#open-questions). Preserved historical ideas do not become pending requirements merely because they remain documented.

<a id="terminology"></a>

## Appendix A. Prompt terminology

Use actual API names and established business identifiers. `harnoq_design_case_*` is current DESIGN functionality, not obsolete merely because its name is older. Mark genuine compatibility aliases explicitly.

| Term | Meaning and boundary |
| --- | --- |
| Source Requirement | INPUT-* free-text requirement material; use “requirement clarification” for the activity. |
| Project Constitution | Consolidated, confirmed project governance; avoid harvesting metaphors. |
| Feature | Delivery/ownership unit and dependency node; includes both vertical capabilities and foundations. |
| Module | One DESIGN-* encapsulating independently changing decisions behind a contract; not a synonym for Feature. |
| Architecture Foundation | Cross-cutting architecture, persistence, topology, and walking skeleton. |
| UI Foundation | Design system and app shell. |
| foundation / vertical | Orientation enum values; descriptive labels, not decomposition rules. |
| Dependency graph | FeatureDependency DAG; not a runtime uses graph. |
| Consumer analysis | Identify consumers to determine shared ownership boundaries. |
| Required design checklist | Explicit design completeness review. |
| Requirement clarification | Resolve goals, acceptance, constraints, boundaries, and failure modes through discussion. |
| Consolidation | Turn confirmed material into project-wide agreements. |
| INTEG-* | One independent assembly, seam, or evidenced duplication obligation with source, status, and run. Not every matrix row becomes an item. |
| SYS-* | One journey or NFR commitment with driver, source, and run; not a DESIGN or a compressed report covering unrelated scenarios. |
| Source-backed TASK | Execution work sourced by explicit foreign keys to approved CRs; not a mandatory task per delivery item. |
| Explicit TASK dependency | Task prerequisite checked for completion before claim, with cycle detection; not inferred from kind or creation order. |
| Integration inventory manifest | Structured inventory evidence and proposed items validated before item creation; internal working material, not a line-by-line user approval document. |
| Production entry point | A root that executes intended delivery behavior and reaches an observable boundary. Build/test/dev commands or a file merely named main are insufficient. |
| Production-reachable | Reachable through proven wired edges from a proven production root; isolated correct subtrees do not qualify. |
| Assembly gap | A specific missing producer→consumer edge. Inventory all components, but count missing edges rather than affected components. |
| Assembly matrix | Complete component reachability evidence, not an automatic work-item batch. |
| Seam | A declared directed adjacency. Create an item only when wiring or a passing direct producer→consumer test is missing. |
| Seam graph | Complete adjacencies extracted from confirmed capability designs, not speculative relationships. |
| Duplicate implementation | Multiple implementations of the same contract/caller semantics/ownership whose divergence would be a defect. Similar names or shapes alone are insufficient. |
| Exact-name collision | The complete identifier is identical across modules, not merely a shared prefix or superclass. |
| Source skeleton | Types, signatures, and fields, without implementation bodies; inspect callers as well. |
| Convergence | Merge proven duplicate responsibilities while preserving behavior and preventing recurrence structurally. |
| Journey | An ordered end-to-end user scenario without a natural single-design owner. |
| NFR commitment | A promise verified at the system boundary; component tests do not establish system success. |

<a id="sources"></a>

## Appendix B. Influences and adoption boundaries

These influenced the design; Harnoq does not claim complete implementation or certification:

- ISO/IEC/IEEE 29148:2018: clarification, analysis, specification, validation, and management remain responsibilities without separate entities for every activity.
- SAFe governance and Enablers: distinct governance responsibilities and explicit infrastructure delivery, without importing its full hierarchy.
- V-model and traceability: connect acceptance with design and execution evidence without claiming a complete requirement matrix.
- OSLC change management and suspect links: separate change decisions from stale-premise notices; no OSLC protocol implementation.
- Parnas, *On the Criteria To Be Used in Decomposing Systems into Modules* (1972), *Designing Software for Ease of Extension and Contraction* (1979), and Parnas/Clements, *A Rational Design Process: How and Why to Fake It* (1986): information hiding, interfaces, and uses relations.
- Meyer, *Object-Oriented Software Construction*: preconditions, postconditions, and invariants.
- Bass/Clements/Kazman, *Software Architecture in Practice*: architecturally significant requirements and quality attribute scenarios.

Historical vendor comparisons and prices are not current recommendations. Revalidate them before a new selection.

<a id="implementation"></a>

## Appendix C. Implementation and historical evidence

| Area | Current implementation |
| --- | --- |
| Objects and tasks | [Design service](../../app/services/design_case_service.py), [task service](../../app/services/development_task_service.py), [features](../../app/services/feature_service.py), [dependencies](../../app/services/feature_dependency_service.py) |
| Governance | [Requirement digest](../../app/services/requirement_digest_service.py), [NFR coverage](../../app/services/nfr_coverage_service.py), [constitution](../../app/services/project_constitution_service.py), [glossary](../../app/services/glossary_term_service.py), [stage methods](../../app/api/stage_methods.py) |
| Change | [Lifecycle](../../app/services/lifecycle.py), [suspect](../../app/services/suspect_service.py), [CR service](../../app/services/change_request_service.py) |
| Execution | [Templates](../../app/services/agent_template_builder.py), [task reads](../../app/api/agent_tasks.py), [PR tests](../../tests/test_pr_lifecycle.py), [QA evidence tests](../../tests/test_qa_evidence_shape.py), [prior evidence tests](../../tests/test_prior_evidence.py) |
| Delivery | [Items](../../app/services/delivery_item_service.py), [runs](../../app/services/delivery_stage_service.py), [gates](../../app/services/delivery_gate.py), [sources](../../app/services/task_source_service.py) |
| Connections | [MCP](../../harnoq_mcp/server.py), [entry point](../../main.py), [write service](../../app/services/skill_service.py) |
| Storage and identity | [Authentication](../../app/core/authentication.py), [ownership](../../app/services/work_owner_service.py), [schema](../../app/db/schema.py), [rebuild](../../app/db/rebuild_migration.py), [numbering](../../app/services/numbering.py), [templates](../../app/templates) |

Use `git show <commit>` for original changes. Historical implementation details are not current requirements.

| Turning point | Commits |
| --- | --- |
| Lifecycle consolidation; removal of needs_review | `76124cf`, `537d268` → `6956a79` |
| Multi-requirement designs; independent Feature | `13e93b7` → `cfc8e38` |
| Constitution and foundation responsibilities | `182eb64`, `73a4e94`, `d64815f`, `f87d72b` |
| REQ removed, restored, removed again | `362fec0` → `14d59e0` → `74a7c7f`; cross-layer fix `80d4fe1`; design clarification `4023e83` |
| INPUT confirmation restored | `8262e7d` |
| TaskPlan then TaskDraft removed | `68e005f` → `b648cd9`, `3857cfc` |
| Dependency copying removed; missing/transitive checks | `86bbe60`, `41a0134`, `172e9fc`, `4d219c1` |
| Independent glossary and verbatim constitution | `38e5691`, `cb46eab` |
| Combined requirement/design draft and standing rules | `7ef133c`, `a509d0d`, `9b994bb`, `0120bc7` |
| On-demand stage methods and removed mirrors | `2e75e60`, `e947057` |
| Requirement digest and NFR coverage/strength | `934cd0c`, `3712e5a`, `55d40d0`, `74d8d57` |
| Constitution cascade removed; versions and CR | `0a9afb2`, `400081c`, `1a9bfdc`, `ecb11f7` |
| Multi-target CR and application checks | `32885dd`, `1de70c2`, `934cd0c` |
| Merged QA and bounded live context | `8b92834`, `7290ceb`, `db7ca2c`, `6ac5c58` |
| Conversational review and optional progress marker | `7af95d7`, `765da51`, `0e355e2`, `7dd7098`, `906d76d` |
| Provider queries added; CI gate and provider removed | `ad0bde4` → `c1b7a44` → `e4a4de4` |
| Execution and mirror states simplified | `83f0d79`, `a39e08c`, `b4d12f4` |
| Attempt-scoped PR evidence | `dc1f372` |
| Blocked/deviates, test counts, cleared failures | `8435061`, `5fb448a`, `03f2854` |
| Withdrawn code-size/performance/criteria diagnoses | `cbb409e`, `2e01ef6`, `d2b150b` |
| Evidence reuse, cost, effective configuration | `f427ad5`, `9919eaa`, `e368a4d`, `1bbbd68` |
| Whole-product verification and inventories | `1919cea`, `9479832`, `61a4ce6`, `59211e7` |
| Delivery tasks replaced by runs | `06c3589` → `ab5a1f2` → `c4d67c5` |
| Batched integration tasks tried and removed | `518e58f` → `946c454` |
| Zero-gap audits, real resources, inventory recovery | `5f61a7e`, `22c39e4`, `acb8bad` |
| Account identity and ownership history | `1b0fa45` |
| Migration removal and database tuning | `d6ca389`, `934cd0c` |
| Project-scoped numbers and automatic diagrams | `f28fce6`, `f63a5eb`, `a62dcc7` |
| Remote and co-hosted MCP | `a8b1db8`, `8aa0b89`, `331157a`, `0794df0` |
| Shared role directory/branch | `7b4118c`, `6f5a2ed` |
| Generic write deduplication removed | `537442e` |

<a id="history-coverage"></a>

### Historical document coverage

Snapshot `094c900` retains the original refactor notes, research, and first retrospective. Use `git show 094c900:<path>`. Later delivery and open-issue notes are available with `git show 6ab0983^:docs/delivery-validation.md` and `git show 6ab0983^:docs/open-issues.md`.

The original refactor topics are retained above: feature/design separation and task-layer removal in [objects](#objects); dependency copying in [dependencies](#dependencies); constitution, foundations, terminology, and requirement drafts in [governance](#governance); lifecycle, source versions, and CRs in [changes](#changes); onboarding, context packages, QA, and pre-review in [agents](#agents); CI/provider removal in [quality](#quality); corrected retrospective diagnoses in [evidence](#evidence); inventory/runs, stage order, reentry, and completion audit in [delivery](#delivery); PM/ALM research in [scope](#scope) and [sources](#sources).

The [Chinese historical-path index](../cn/design-evolution.md#history-coverage) preserves the exact old filename-to-section mapping. Old “to implement” instructions, fixture entities, temporary numeric thresholds, and construction steps describe their original context; they do not reintroduce abandoned work into the current backlog. Current API specifications and future goals remain in their respective documents.
