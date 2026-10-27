"""Agent-facing prose: the stage methods and the standing guidance blocks.

Split out of :mod:`app.api.skills` (see docs/cn/design-evolution.md#methods). That module is an HTTP API;
this one is a document. Fusing them meant every wording change edited a
3,500-line request-handling file, and the prose — 75k characters of stage
method alone — made the API code hard to find inside it. Nothing here is
logic: every function returns a literal, and they are re-exported from
``app.api.skills`` so existing imports keep working.

The single source of truth for how an agent works a stage lives HERE. The
other channels (skills/openwebui-system-prompt.md, harnoq_mcp/server.py's
instructions, skills/openapi.yaml) must stay THIN and point at
``active_stage_method`` rather than mirroring it — a mirror drifts, and it
turns every wording change into a four-file edit.
"""


def _available_tool_guidance():
    return {
        "write_tools_are_enabled": True,
        "common_intent_map": [
            {
                "user_intent": "append/add/supplement text to an existing INPUT-* source requirement",
                "tool_name": "harnoq_source_requirement_append_content",
                "minimal_arguments": {
                    "source_requirement_number": "INPUT-*",
                    "raw_content_to_append": "text to add",
                },
            },
            {
                "user_intent": "rewrite/edit fields on an existing INPUT-* source requirement",
                "tool_name": "harnoq_source_requirement_update",
                "minimal_arguments": {
                    "source_requirement_number": "INPUT-*",
                    "raw_content": "replacement full content or fields to change",
                },
            },
        ],
        "read_tools": [
            {
                "tool_name": "harnoq_integration_item_get",
                "use_when": "A coder or qa assigned an Integration finding needs its exact INTEG-* source; pass the project and item business numbers.",
            },
            {
                "tool_name": "harnoq_integration_inventory_get",
                "use_when": "Recover the saved Integration manifest, current blockers and revision token before close-out or reentry.",
            },
            {
                "tool_name": "harnoq_project_context",
                "use_when": "Need full project state, business numbers, or next-step guidance.",
            },
            {
                "tool_name": "harnoq_task_progress",
                "use_when": "Need development-task execution or quality-gate status.",
            },
        ],
        "validation_tools": [
            {
                "tool_name": "harnoq_integration_inventory_revise",
                "use_when": "After inventory_get and reviewing changes, submit evidence updates and changed sections; the server merges and revalidates the full inventory.",
            },
            {
                "tool_name": "harnoq_integration_inventory_validate",
                "use_when": (
                    "Integration Round 1 has a complete structured manifest and "
                    "needs server-computed reconciliation before item creation. "
                    "A valid=true receipt authorizes the bound INTEG-* payloads; "
                    "it is not a request for per-item human approval."
                ),
            },
            {
                "tool_name": "harnoq_system_inventory_validate",
                "use_when": (
                    "System Round 1 has audited every confirmed INPUT and NFR "
                    "commitment, reconciled existing SYS rows, and needs the "
                    "server receipt before bound SYS creation."
                ),
            },
        ],
        "write_tools": {
            "source_requirement": {
                "create": "harnoq_source_requirement_create",
                "update": "harnoq_source_requirement_update",
                "append_content": "harnoq_source_requirement_append_content",
            },
            "design_case": {
                "create": "harnoq_design_case_create",
                "update": "harnoq_design_case_update",
                "confirm": "harnoq_design_case_confirm",
                "reopen": "harnoq_design_case_reopen",
                "realign_basis": "harnoq_design_case_realign_basis",
                "delete": "harnoq_design_case_delete",
                "restore": "harnoq_design_case_restore",
            },
            "development_task": {
                "create": "harnoq_task_create",
                "update": "harnoq_task_update",
                "note": (
                    "harnoq_task_create makes a claimable DevelopmentTask (TASK-*) "
                    "directly under a confirmed DESIGN-*; approved change-request "
                    "work uses the source-task tool. "
                    "A design has at most one TASK; source tasks have explicit FEATURE "
                    "and source-item associations. A task stays editable while "
                    "unclaimed ('claimable'); once claimed its content fields lock. "
                    "Exception: a source-backed TASK in needs_revision is editable "
                    "through harnoq_task_update. Read its current sources, update the "
                    "authorized contract changes, read back with harnoq_task_get, then "
                    "harnoq_task action='reopen' on the SAME TASK. Do not create a replacement."
                ),
            },
            "feature": {
                "create": "harnoq_feature_create",
                "update": "harnoq_feature_update",
                "assign": "harnoq_feature_assign",
                "delete": "harnoq_feature_delete",
            },
            "constitution": {
                "update": "harnoq_constitution_update",
                "confirm": "harnoq_constitution_confirm",
                "reopen": "harnoq_constitution_reopen",
            },
            "glossary": {
                "create": "harnoq_glossary_create",
                "update": "harnoq_glossary_update",
                "delete": "harnoq_glossary_delete",
            },
            "integration_item": {
                "create": "harnoq_integration_item_create",
                "list": "harnoq_integration_item_list",
                "update": "harnoq_integration_item_update",
                "note": (
                    "An INTEG-* or SYS-* item is one thing that must have an owner before "
                    "delivery: an integration-stage assembly part / declared "
                    "seam / found duplicate, or a system-stage user journey / "
                    "NFR commitment. Round 1 of the integration/system stage "
                    "method is BATCH CREATION of these, not prose. Move an "
                    "item to status=covered only with covered_by naming what "
                    "discharges it, or to status=waived only with a "
                    "waive_reason — both are enforced server-side. Agent "
                    "writes are also stage-gated: Integration accepts only "
                    "assembly/seam/duplicate and System only journey/nfr. "
                    "Exception: record a user-authorized waiver on an existing item "
                    "with only item_number, status='waived' and waive_reason, even "
                    "while another stage is active. This does not authorize creation, "
                    "coverage or edits to the obligation itself. "
                    "Create also requires a non-empty provenance source and "
                    "an explicit found_in_round. Existing implementation or "
                    "a previously green capability test is not a waiver. "
                "Integration and System Round 1 must pass their respective "
                "inventory validation gate before their validated batch is created."
                ),
            },
            "system_item": {
                "create": "harnoq_system_item_create",
                "list": "harnoq_system_item_list",
                "update": "harnoq_system_item_update",
                "note": "System-stage SYS-* obligations use the corresponding system item tools; an existing item's user-authorized waiver may be recorded outside System using only item_number, status='waived' and waive_reason.",
            },
            "change_request": {
                "create": "harnoq_change_request_create",
                "analyze": "harnoq_change_request_analyze",
                "approve": "harnoq_change_request_approve",
                "apply": "harnoq_change_request_apply",
                "close": "harnoq_change_request_close",
                "note": (
                    "A CR-* is for changing something that already has work built "
                    "on it. Editing a draft needs no change request — just edit it. "
                    "A bounded repair discovered by a SYS-* or INTEG-* scenario "
                    "may stay in its existing source TASK without a CR; follow the "
                    "active delivery method and change.approval_policy. For a CR that "
                    "is actually needed, analyze impact and let the manager approve "
                    "an authorized repair without another user confirmation. "
                    "Applying a CR-* records that the change was made; it never "
                    "edits an object itself."
                ),
            },
        },
    }


def _business_id_rules():
    return {
        "project_number": "P* (e.g. P1)",
        "source_requirement": "P*-INPUT-*",
        "feature": "P*-FEATURE-*",
        "input_number": "Legacy alias of source_requirement_number for P*-INPUT-*",
        "design_case": "P*-DESIGN-*",
        "development_task": "P*-TASK-*",
        "glossary": "No business number: identify a glossary term by its unique term_native within the project.",
        "integration_item": "P*-INTEG-*",
        "system_item": "P*-SYS-*",
        "notes": [
            "Use business numbers, never raw database ids.",
            "Type labels such as DESIGN-* in method prose are not aliases for a "
            "real row. Whenever an existing object is cited in a report, source "
            "field, decision, or tool call, copy its fully qualified P*-... "
            "number exactly as returned; never replace it with initials or a "
            "local ordinal shorthand.",
            "Use source_requirement_number for INPUT-*; the API also accepts the legacy name input_number.",
            "Do not invent fields like source_requirement_id or item_id.",
        ],
    }


def _workflow_guidance():
    return [
        "Orient before any stage: read project-context FIRST and understand the "
        "project before decomposing, designing or planning anything — what kind "
        "of system it is, the fixed stack, and where the object you are about to "
        "work on sits in the order of work and among its sibling "
        "INPUT-*/DESIGN-* objects. Never decompose, design or plan an object in isolation.",
        "Respect the stage gate — it is a HARD STOP, not a suggestion, and it "
        "covers the WHOLE stage (the analysis / feature-map / breakdown conversation, "
        "not just the write tool). A stage may start only when its upstream is "
        "CONFIRMED: constitution consolidation needs every INPUT-* confirmed; the "
        "feature map needs the project constitution confirmed; task_planning needs "
        "its DESIGN-* confirmed; draft / needs_clarification both count as NOT "
        "ready. If the upstream is not ready, do NOT proceed and do NOT sketch the "
        "downstream work anyway to be helpful — stop, name the exact items that block, "
        "and send the user back to finish the upstream stage. Being eager to help does "
        "not override the gate. And never bulk-confirm upstream items just to unblock "
        "the next stage: confirmation is a per-item review done upstream on its own "
        "merits, not a formality to open the next door — an INPUT-* is confirmed only "
        "once its discussion has genuinely gone deep (goal / acceptance / constraints / "
        "failure modes), never just to advance the pipeline.",
        "THE PIPELINE, in order: INPUT-* (mine the raw requirement) -> project "
        "constitution -> FEATURE-* (the capability map) -> DESIGN-* (one per "
        "module) -> TASK-*. One layer is routinely skipped by agents working "
        "from generic experience, so name it explicitly: the constitution sits "
        "between confirmed INPUT-* and any feature work. You do NOT go from a "
        "FEATURE-* straight to a DESIGN-* without first engaging its INPUT-* "
        "material.",
        "Say where you are and what comes next. Whenever you finish or report on "
        "a stage, state the stage you are in, what is still missing before it can "
        "close, and which stage follows — by name, from the pipeline above. Never "
        "let the user infer the next step, and never substitute a plausible-"
        "sounding next step from general software practice for the one the "
        "pipeline actually specifies.",
        "INPUT-* (SourceRequirement) is free-text raw material you mine by "
        "conversation (edit / append raw_content) until the requirement is "
        "genuinely understood, THEN confirm; confirm has no required-field "
        "checklist of its own, and every live INPUT-* must be confirmed before "
        "the requirement stage is done.",
        "The project constitution is a governance step BETWEEN mined INPUT-* and "
        "design: it consolidates the fixed stack, quality bar, UI conventions, NFR baseline "
        "and boundaries the requirement conversation surfaced into one per-project "
        "record, gets confirmed, and then governs feature / design / task. Design may "
        "not start until it is confirmed.",
        "Terminology is a separate, always-editable project glossary: before writing "
        "requirements, designs, or tasks, copy any listed term_native/term_en exactly. "
        "Never use a term listed in avoid; add a new concept and confirm it with the "
        "user before using it. It stops WRONG WORDS, it is not the vocabulary of the "
        "domain: a term belongs only if you can write the sentence someone would produce "
        "with the wrong word in it — usually a generic catch-all this project overloads "
        "— and say what that breaks. That wrong word goes in avoid. Two concepts that "
        "merely sit next to each other, each with its own distinct name, are adjacency, "
        "not confusion, and earn no rows. Being coined here, being the user's word or "
        "being in the source material qualifies nothing. "
        "Glossary changes do not reopen governance or trigger review, and the glossary "
        "gates nothing: never make settling a term a precondition for confirming an "
        "INPUT-* / DESIGN-* / anything else, and never fuse the two into one ask.",
        "Feature is the lightweight plan/split unit between the mined INPUT-* and "
        "the DESIGN-* under it; once the Feature map is agreed, persist "
        "each FEATURE-* with its metadata, dependency edges and optional "
        "assignee. Dependencies live ONLY here, at the Feature level.",
        "When mining requirements, first read the relevant INPUT-* objects and "
        "interrogate the user to deepen them (goal / acceptance / constraints / "
        "non-goals / failure modes / the need behind the need) before composing "
        "features. Write the deepened understanding back into the INPUT-* text "
        "(harnoq_source_requirement_update / append_content), or add a new INPUT-* for "
        "a distinct newly-surfaced requirement.",
        "Requirement mining and feature composition are two separate steps, in "
        "that order: deepen the INPUT-* understanding first; then group the "
        "mined material into deliverable FEATURE-*. Only then does a DESIGN-* "
        "get created under a feature.",
        "Only edit / append an INPUT-* after the user agrees; do not silently rewrite "
        "their raw material.",
        "When the user asks to add text to an existing INPUT-* raw requirement "
        "document, call harnoq_source_requirement_append_content.",
        "DesignCase is the solution record for a module under one FEATURE-*. It "
        "owns its tasks directly (no intermediate plan object).",
        "Revising a confirmed upstream is not done when the upstream is saved — "
        "carry the change DOWN. When you change an already-confirmed FEATURE-* or "
        "DESIGN-* — a boundary move, a split, a merge, a reopen-to-edit — the "
        "TASK-* created from the old shape does NOT update or invalidate itself, "
        "and nothing flags them: a TASK-* has no needs-review state, so a "
        "downstream item silently keeps pointing at a shape that no longer "
        "exists. So after any such change you MUST walk the downstream chain — "
        "each affected DESIGN-*'s task_numbers, and the project's tasks (all in "
        "project-context) — and, for EACH existing TASK-*, put the choice to the "
        "user explicitly: keep as-is, edit to match the new shape (only while "
        "still unclaimed), delete, or leave unstarted for now. Name the specific "
        "downstream numbers; never stop at 'the feature map is updated' and "
        "leave the tasks dangling. This is not a stage of its own — the stage "
        "indicator may still read task-planning or planning-complete while you "
        "do it — it is an obligation attached to changing any confirmed "
        "upstream.",
        "active_stage_method IS the method for the stage you are in, and the "
        "only one you get: it is resolved from current_stage and hoisted to the "
        "top of the context. Other stages' methods are not sent — do not work "
        "from a remembered version of one, and if the stage shown is not the "
        "one you believe you are in, say so instead of proceeding on the method "
        "you expected.",
    ]


# _current_stage returns coarse pipeline-position labels; these differ from the
# stage_methods keys in two spots. Keep the map next to _current_stage so adding
# a stage can't silently produce a None active_stage_method.
_STAGE_METHOD_KEY = {
    "requirement": "requirement_decomposition",
    "planning_complete": "task_planning",
}


def _stage_directive(stage):
    """Loud, binding next_step that points at the hoisted active_stage_method.

    Counters the observed failure where an agent skims the context, never reads
    the buried method, and partitions from its own generic DDD knowledge.
    """
    if stage == "planning_complete":
        return (
            "Task planning is COMPLETE for this project: every confirmed DESIGN-* "
            "already has its TASK-* (see each design's task_count / task_numbers "
            "in design_cases). Do NOT create or re-propose a task for a design that "
            "already has one. If the user wants changes, edit the EXISTING TASK-* "
            "while it is still unclaimed (claimable); once claimed its content "
            "fields are locked and the fix belongs to the coding phase instead. "
            "A source-backed TASK already in needs_revision is the exception: "
            "correct its contract through harnoq_task_update, read back, then "
            "harnoq_task action='reopen' on that same task. "
            "If delivery_gate.capability_outstanding is true, finish the existing "
            "outstanding_capability_task_numbers through their task-scoped Build/QA "
            "flow, then reread context for Integration and System revalidation. "
            "Delivery stage items do not imply missing designs."
        )
    if stage == "change":
        return (
            "This project has an OPEN CR-* (see change_requests in this "
            "context). active_stage_method is the change method — read it IN "
            "FULL and carry that request to its next state before starting "
            "other work. Classify its approval boundary first: an ordinary defect "
            "repair within existing authorization proceeds without another user "
            "confirmation; a product decision needs the user's decision. The "
            "pipeline position underneath it has not gone anywhere. If "
            "the impact analysis comes back with nothing downstream, this was "
            "never a change request — say so and just make the edit."
        )
    if stage == "integration":
        return (
            "Integration is owed. Read active_workflow_vocabulary before "
            "interpreting any INTEG-* kind. active_stage_method is BINDING. "
            "EXIT ASSERTION: "
            "deliver a product that starts and runs; every criterion is judged by "
            "code that runs and exits. In Round 1, finish round_1_workflow and "
            "let the server validate the manifest; the user is not asked to review "
            "technical evidence. After valid=true, create the bound INTEG-* rows "
            "and start one delivery-stage run. Record EVERY distinct finding as "
            "its own INTEG-* item. Group findings by common repair, execute "
            "one Integration branch and one final PR directly under the stage "
            "run; do not create a TASK for stage repairs. Then rerun each item's direct "
            "check on the merged revision before closing. A helper failure or "
            "interrupted tool call does not finish Round 1: inspect what was "
            "persisted, take over missing work yourself, and retry validation. "
            "Do not stop after a status report while the inventory is unfinished. "
            "The manager owns the cross-design inventory and final manifest; "
            "do not hand the whole audit to a task-scoped coder or qa agent."
        )
    if stage == "system":
        return (
            "Integration is caught up; System verification is owed on the current "
            "basis or still has outstanding obligations. "
            "Read active_workflow_vocabulary before interpreting any SYS-* kind. "
            "active_stage_method (top of this context) is the BINDING method "
            "for the SYSTEM stage — read it IN FULL before doing anything. "
            "EXIT ASSERTION: every required journey and committed NFR is verified "
            "black-box through the real product or explicitly waived by the user; "
            "no unwaived failure or blocked System item remains. SELF-TEST: "
            "the script's exit code must reflect assertions of the promised user "
            "result, never a mere successful launch or your narrated account. "
            "Reuse existing environments, scenarios and scripts. Before declaring "
            "completion, read project context again and verify inventory completeness "
            "and system_owed=false; report accepted limitations separately."
        )
    label = {
        "constitution": "PROJECT-CONSTITUTION consolidation",
        "requirement": "REQUIREMENT decomposition",
        "design": "FEATURE decomposition + DESIGN",
        "task_planning": "TASK planning",
    }.get(stage, stage)
    return (
        f"You are at the {label} stage. active_stage_method (top of this context) "
        "is the BINDING method for it — read it IN FULL and follow it verbatim "
        "before you propose a Feature map or create/confirm any "
        "INPUT-*/FEATURE-*/DESIGN-*/TASK-*. Do NOT work from your own generic "
        "DDD/architecture knowledge. A Feature map divides ONE project into a "
        "handful of ownable blocks named after what they do, cut where something "
        "changes for its own reasons; every Feature carries orientation + "
        "subdomain_type tags. "
        "SELF-TEST before answering: if your feature list has no "
        "orientation/subdomain_type tags, has no Architecture Foundation, or is "
        "mostly platforms / cores / hosts / layers, you did NOT follow the "
        "method — stop and re-read active_stage_method."
    )


def _stage_methods():
    """Stage-specific working methods, delivered dynamically with the context
    so the OpenWebUI system prompt can stay lean. The model should apply only
    the entry that matches the stage it is currently working on (see
    workflow_guidance), not blend several stages' methods together. New stages
    add a bucket here instead of bloating the always-on system prompt.
    """
    from app.models.task_enums import TASK_TYPES

    _task_type_values = ", ".join(TASK_TYPES)
    methods = {
        "requirement_decomposition": {
            "when": (
                "Mining an INPUT-* source requirement by conversation until the "
                "requirement is genuinely, deeply understood — then writing that "
                "deepened understanding back into the INPUT-* text and confirming it. "
                "INPUT-* stays free text and confirm has no required-field checklist. "
                "The requirement stage is not done, and the constitution stage "
                "cannot start, until every live INPUT-* in the project is confirmed."
            ),
            "principles": [
                "Do not passively transcribe. Treat the raw INPUT-* as an opening "
                "statement to be pressured and deepened, not a finished spec to file "
                "away. The goal of this stage is 充分澄清需求，识别未明确表达的业务目标 "
                "(clarify the requirement and identify its unstated business need).",
                "Cover, in prose, the dimensions the old structured fields used to force — "
                "and more: what the user actually wants and WHY (the underlying motivation "
                "/ business goal, not a restatement of the ask), what DONE looks like "
                "(acceptance in concrete, observable terms), constraints, explicit "
                "non-goals, edge cases, failure modes, and assumptions being made. If any "
                "of these is still blank or shallow after the conversation, you are not "
                "done — keep asking.",
                "Challenge from a software-engineering angle, do not just tidy up what the "
                "user said. For each requirement question whether the assumption holds, "
                "whether it is feasible at the stated scale and at what cost, the "
                "worst-case or irreversible failure (data loss / cannot undo / hang), "
                "conflicts with other requirements, and cheaper alternatives. "
                "Structuring is not the same as critiquing.",
                "State ASSUMPTIONS for the user to strike through; do not open by asking "
                "them to settle an abstract priority. 'When accuracy and usability "
                "conflict, which wins for v1?', paired with a default nobody would "
                "refuse, looks strategic and extracts nothing: the user learns nothing "
                "about their own requirement and no answer changes what gets built. Quick "
                "test — if the same sentence could be asked verbatim about an unrelated "
                "system (a payroll run, a flight scheduler, a warehouse robot), it is not "
                "a question about THIS requirement. Answering from a blank page is hard; "
                "spotting a wrong statement is easy, so make statements. Read the INPUT-*, "
                "then list what you had to ASSUME to read it as a coherent system: what it "
                "is for, what counts as failure, where approximation is fine, how the "
                "pieces fit. Hidden premises are often about PRECISION — asked to estimate "
                "1111/1110 you will compute decimals nobody wanted, because 'roughly 1 is "
                "fine' was never said out loud — so put those on the list too. Every "
                "assumption carries two things: where it came from (quote the line, or say "
                "plainly that you inferred it), and what you would build DIFFERENTLY if it "
                "is wrong, in this project's own objects. An assumption whose consequence "
                "you cannot state does not matter — drop it rather than pad the list. The "
                "ones the user corrects are exactly the unstated premises this stage "
                "exists to find.",
                "Clarify unknowns first: keep unfamiliar terms/libraries verbatim, mark "
                "them 待确认(unknown), and never invent their API or behavior before the "
                "user explains them.",
                "Anchor terminology HERE, in the user's own words. When the user names a "
                "domain concept, that exact wording is the ORIGINAL — and this "
                "conversation is the only place it exists. Every later stage works from "
                "your paraphrase, so a term not captured now loses its anchor forever and "
                "drift becomes undetectable (support request -> case -> legal case -> a "
                "lawsuit). Capturing terms is part of this stage's job, not an "
                "optional extra: see round 2.",
                "System-shape first: identify what kind of system this is and infer its "
                "load-bearing structures (e.g. an e-commerce system is held up by "
                "catalog/SKU, inventory, pricing/promotion, cart, order, payment, refund, "
                "idempotency and audit state). Derive the "
                "foundation from THIS project — never start from UI screens, buttons, "
                "filters or settings. Find the source-of-truth records, lifecycle/state "
                "machines, irreversible/high-risk operations, async/batch pipelines, "
                "external boundaries, and cross-module contracts first.",
                "Supply the CAPABILITIES the stated goal requires, then ask whether the "
                "user wants them. Users state an outcome and leave out things that "
                "outcome cannot hold without. Take their goal, work out what the system "
                "must be able to do for it to hold at all, name what the text never "
                "mentioned, and ask. Anchor every suggestion to THEIR goal, never to what "
                "systems of this category usually ship — that anchor is the brake that "
                "keeps this from becoming a feature catalogue the user rubber-stamps. "
                "SCOPE TEST, apply it to every item before you raise it: can the user say "
                "in their own words 'without this I cannot do X' or 'without this I do not "
                "know what happens when Y'? If instead it names a layer, component, queue, "
                "store, scheduler, cache or execution strategy of your intended "
                "implementation, it is an ARCHITECTURE decision — it belongs to DESIGN, "
                "and the stack-level part of it to the project constitution. Raising it "
                "here is an altitude violation: it makes the user rule on technology "
                "before the feature map or the constitution exists, and once they agree it "
                "gets written into the INPUT-* and binds design forever. Re-express such "
                "an item as the behaviour the user would observe, or drop it. Two tiers, "
                "kept apart: (1) BREAKS THE CHAIN — without it the flow the user described "
                "cannot run end to end (something has to create the records, something has "
                "to make them reachable, something has to undo a wrong one, something has "
                "to say what the user sees when a step fails). Name which link is missing "
                "and ask directly; a requirement missing one of these is not complete. "
                "(2) COMMON BUT OPTIONAL — usual in systems like this, main flow runs fine "
                "without it: put these in ONE pick-list, every row defaulted to OUT, and "
                "never walk the user through them one at a time. A whole missing "
                "capability is the expensive failure at this stage: every later stage "
                "looks at one feature or one module at a time, so nobody downstream is "
                "positioned to notice an absent block.",
                "Do not fabricate — the ban is on WRITING, not on ASKING. Anything not in "
                "the source text must be tagged, and nothing the user has not agreed to "
                "may be written into the INPUT-* text; once they do agree, mark it as "
                "added in this conversation so it never reads back as their own original "
                "wording. Raising a missing capability in conversation is the opposite of "
                "fabricating — it is the only way the gap gets closed, and staying silent "
                "about it is a failure of this stage, not caution.",
                "Dependencies / build order do NOT live on the requirement. INPUT-* is "
                "for getting the requirement understood, not for expressing build order "
                "— that lives at the Feature level (depends_on_feature_numbers) once "
                "INPUT-* are composed into features in the design stage.",
            ],
            "rounds": [
                "Round 1 — interrogate, do NOT write yet: read the INPUT-* in full, then "
                "ask focused questions ONE AT A TIME and keep going until the requirement "
                "is genuinely thorough. Work OUTWARD-IN. Cover (A) background — goal, "
                "users, "
                "boundary, scale; mark unstated items 待确认. (A2) the ASSUMPTION LIST — "
                "what you had to assume to read this as a coherent system (purpose, what "
                "counts as failure, where approximation is fine, how the pieces fit), "
                "each with its source and with what changes if it is wrong, handed over "
                "for the user to strike through. Not an abstract priority question: a "
                "first message spent on one of those has wasted the round. (B) clarify "
                "unknown concepts "
                "before any other questions. (C) identify the system type and likely "
                "pressure surface: actors, core objects, data volume, read/write ratio, "
                "peak pattern, latency-sensitive paths, consistency-sensitive paths, "
                "batch/async throughput and recovery expectations. (D) dig for the need "
                "BEHIND the need — the deep, unstated motivation, not the literal ask. "
                "(D2) the CAPABILITIES THE GOAL REQUIRES that the text never mentioned: "
                "ask the chain-breaking ones directly, each naming the link that is "
                "missing, and put the common-but-optional ones in a single pick-list "
                "defaulting to OUT. D2 is EXEMPT from one-at-a-time — it is a sweep of "
                "the whole goal and splitting it into single questions destroys it, so "
                "deliver it as one pass. D2 is also MANDATORY output: if you conclude "
                "nothing is missing, say that in as many words and name the links you "
                "checked. Silence is not an allowed answer, and do not skip D2 because "
                "the text reads complete — text that reads complete is exactly where "
                "whole blocks go missing. Every item passes the scope test first: a "
                "capability the user can feel the absence of, never a component of your "
                "intended architecture. "
                "(E) up to 8 feasibility challenges (feasible at the stated scale and "
                "cost? irreversible failure? conflicts? cheaper alternative?), each with "
                "source / challenge / suggestion. (F) up to 10 other open questions ranked "
                "by impact. Every item in (E) and (F) carries two things or it does not "
                "ship: the assumption from (A2) or the goal it serves, and a PROPOSED "
                "DEFAULT — the answer you will take if the user says nothing. The default "
                "is what turns an interrogation into a decision the user can wave through, "
                "and writing it forces you to find the hidden assumption instead of "
                "outsourcing it. A default is only real if its OPPOSITE is a choice some "
                "reasonable user would make; if you cannot picture that user, you have "
                "written a rhetorical question — cut it and spend the slot on something "
                "that actually splits the build. Prefer few well-anchored questions over a "
                "full quota of flat ones; a list you cannot rank means (A2) is still "
                "shallow, not that you were thorough. Then stop and wait for the user.",
                "Round 2 — write the deepened understanding back into the INPUT-*: only "
                "after the conversation has genuinely covered goal / acceptance / "
                "constraints / non-goals / edge cases / failure modes, write it back. "
                "DEFAULT TO APPEND (harnoq_source_requirement_append_content), NOT "
                "harnoq_source_requirement_update — update replaces the WHOLE raw_content "
                "in one shot, so a bad rewrite can silently drop parts of the original "
                "requirement with no visible diff; append can only add, never destroy. If "
                "the new conclusion CONTRADICTS or SUPERSEDES something already in the "
                "INPUT-*, do NOT silently leave the two contradicting statements side by "
                "side either — append the clarification with an explicit note naming what "
                "it replaces (e.g. '以下澄清替代上文关于 XX 的说法：...'), so the document "
                "stays internally consistent without touching the original text. Only use "
                "harnoq_source_requirement_update — a real rewrite/consolidation of the "
                "document — when the USER EXPLICITLY asks you to clean up or merge the "
                "INPUT-*; never decide on your own that a rewrite is 'cleaner'. If the "
                "conversation "
                "surfaced a genuinely DISTINCT new requirement, add it as its own new "
                "INPUT-* (harnoq_source_requirement_create) rather than burying two "
                "requirements in one input. Separately, surface every performance / scale "
                "/ throughput / latency target as a performance register (metric / target "
                "or 待确认 / source / applies-to) for discussion — but do NOT persist it as a "
                "requirement: performance is a global quality attribute that lives in the "
                "project constitution's nfr_baseline, consolidated at the constitution stage. "
                "Keep functional INPUT-* free of performance numbers. Finally, run a "
                "SUBSTITUTION AUDIT and record only what it catches — this, and nothing "
                "else, is what the glossary is for. Do not walk the nouns in the "
                "document; that produces a translation table. Instead hunt for a WORD "
                "that will be written wrongly, and prove each candidate by writing out "
                "the sentence a later reader would actually produce with the wrong word "
                "in it. The candidate survives only if all three hold: (1) the wrong "
                "word is one somebody would really type — typically a GENERIC CATCH-ALL "
                "this project has quietly overloaded (an ordinary word used both for a "
                "request and for the record it creates, or both for a login account and "
                "for the human behind it), or the natural English name of a neighbouring "
                "concept. It must NOT be the name of another term you are also adding: "
                "if avoid points at a sibling row, that is ADJACENCY POSING AS "
                "CONFUSION, and both rows are worthless. (2) the wrong sentence reads "
                "naturally — a slip that looks absurd on sight will never be made, so it "
                "needs no guard. (3) you can say what breaks: which identifier lands "
                "wrong in code, which criterion lands wrong in a design, which operation "
                "hits the wrong object. Cannot write that sentence? There is no row — "
                "skip it silently. "
                "TWO CONCEPTS SITTING NEXT TO EACH OTHER ARE NOT A ROW: if each already "
                "has its own distinct name, nobody will swap them, and the relation "
                "between them (one is excluded from the other, one is derived from the "
                "other) is a CONSTRAINT for the constitution or the design, not a "
                "glossary entry. The rows that pay off usually BAN A BARE WORD and force "
                "the writer to pick a side. Being coined here, being domain "
                "vocabulary, being the user's own word, or appearing in the source "
                "material is NOT a qualification — material that already names something "
                "unambiguously needs no row, and what is worth recording is usually a "
                "distinction the SOURCE NEVER DREW, which is why it is still floating on "
                "one overloaded word. A literal compound noun whose English is "
                "word-for-word is a description, not a term. Expect very few rows: on "
                "precise material, none is a correct outcome; a row per noun means you ran "
                "a translation pass. For each row that survives, call "
                "harnoq_glossary_create with term_native = the wording actually used for "
                "the concept, copied VERBATIM (never invent a native phrase by "
                "back-translating the English identifier you chose), term_en = the "
                "authoritative English name coder will use for identifiers, definition_en "
                "= what it is and, in one clause, what it is NOT (never numbers, "
                "technology choices or behaviour rules, which belong to the constitution "
                "and design), and avoid = the wrong WORD from the substitution sentence. "
                "When one overloaded word covers two things, both sides get a row and "
                "both list that same bare word in avoid — never each other. Skip anything "
                "project_context.glossary "
                "already lists. Persist now and review in round 3 — the glossary has no "
                "confirm gate and is editable at any time by design, so a wrong entry is "
                "cheap to fix.",
                "Round 3 — TWO SEPARATE ASKS, in this order, never fused into one "
                "'请确认' the user answers once. (3a) TERMINOLOGY REVIEW — not a "
                "confirmation, and it gates nothing: show the glossary entries you just "
                "persisted as a term_native / term_en / avoid table and ask the user to "
                "CORRECT the English names, since that translation is where drift starts "
                "and they are the only authority on their own vocabulary. Fix disputed "
                "entries with harnoq_glossary_update. Present each row as the SLIP IT "
                "PREVENTS — the wrong word, plus the sentence somebody would write with "
                "it and what that would break — not as a term/translation table, so the "
                "user is judging a real risk rather than proofreading a dictionary. Drop "
                "with harnoq_glossary_delete every row whose slip the user does not "
                "recognise as something anyone would actually write: a term nobody would "
                "have gotten wrong is noise in a table every later stage has to read. Say "
                "plainly that the glossary has no confirm gate and stays editable at any "
                "time, so nothing here has to be settled now. NEVER make terminology a "
                "precondition for confirming the INPUT-* and never offer to 'confirm the "
                "terms and the INPUT-* together': an unsettled English name is free to fix "
                "later, whereas holding the INPUT-* hostage to it pushes the user to wave "
                "the terms through — exactly the wrong side to cut. Unreviewed terms are "
                "not a reason to withhold confirm. "
                "(3b) INPUT-* CONFIRMATION — a separate question with one criterion: does "
                "the written text capture the requirement at the right depth. Re-read it "
                "back to the user and ask that, and that only. If it is still shallow "
                "(missing the why, the done-criteria, the failure modes), go back and "
                "interrogate more — do NOT call confirm yet. Only once the user agrees the "
                "DEPTH is right, call harnoq_source_requirement_confirm on it. A single "
                "'好' after 3a answers 3a alone: ask 3b on its own and wait for its own "
                "answer. Repeat for every INPUT-* in the "
                "project; the constitution stage is blocked until all of them are "
                "confirmed. Never bulk-confirm to unblock the next stage — each INPUT-* "
                "is confirmed on its own merits.",
            ],
            "depth_bar": {
                "goal": "WHY this requirement exists — the user value or cost avoided, "
                "the need behind the literal ask. Not a restatement of the title.",
                "acceptance": "What DONE looks like in concrete, observable terms — "
                "functional given/when/then scenarios verifiable by behavior (describe "
                "what the system does/returns/rejects, never vague words like '精准'/'快'; "
                "cover at least one edge/failure/negative path). No performance numbers.",
                "constraints": "Concrete bounds the solution must honor: fixed "
                "stack/library choices, scale, explicit out-of-scope, risks and open "
                "unknowns. Tag anything not stated in the source 待确认.",
                "non_goals": "What is explicitly NOT in scope, so creep is visible.",
                "failure_modes": "The worst-case / irreversible outcomes and how they "
                "should be contained.",
            },
            "performance_register": (
                "Collect all performance / scale / throughput / latency targets into ONE "
                "register for discussion — each row: metric, target value (propose one and "
                "tag 待确认 when the source gave no number; if a metric genuinely has no "
                "sensible target, say so explicitly), source, and which INPUT/feature it "
                "applies to. This register is NOT a requirement — performance is a global "
                "quality attribute that lives in the project constitution's nfr_baseline, "
                "consolidated at the constitution stage. Surface it here, keep functional "
                "INPUT-* free of performance numbers, but do not persist it as a "
                "requirement. (You may seed nfr_baseline early via "
                "harnoq_constitution_update if the user wants, but it is not required at "
                "this stage.)"
            ),
            "write": (
                "Write the deepened understanding back into the INPUT-*: DEFAULT to "
                "harnoq_source_requirement_append_content (append a section, marking what it "
                "supersedes if it contradicts existing text); use "
                "harnoq_source_requirement_update (replace the whole raw_content) ONLY "
                "when the user explicitly asks for a rewrite/consolidation — never as a "
                "default, since a bad rewrite can silently drop original content with no "
                "visible diff. For a distinct "
                "newly-surfaced requirement, harnoq_source_requirement_create a new "
                "INPUT-*. Once the user agrees the depth is right, "
                "harnoq_source_requirement_confirm it (bare status flip, no field "
                "checklist) — reopen with harnoq_source_requirement_reopen if it needs "
                "further editing after being confirmed. INPUT-* carries no dependency "
                "graph — do not try to record 'depends on' here; that concept lives at "
                "the Feature level once INPUT-* are composed into a Feature. Do NOT bake "
                "performance / scale targets into a functional INPUT-* — those go to the "
                "constitution's nfr_baseline. status=succeeded means it was written."
            ),
        },
        "constitution": {
            "when": (
                "After the INPUT-* set is confirmed and BEFORE creating any "
                "FEATURE-*/DESIGN-*: consolidate the governance content the "
                "decomposition already surfaced into the single project "
                "constitution, then get it confirmed."
            ),
            "principles": [
                "CONSOLIDATING IS NOT THE SAME AS LEAVING BLANKS: a load-bearing "
                "form choice must not reach the design stage unset. Consolidation "
                "only ever collects what the INPUT-* happened to mention, so "
                "whatever nobody thought to state stays empty — and design is told "
                "to treat this document as given, which means an empty slot is not "
                "inherited, it is re-invented by whoever hits it first, differently "
                "each time. Before confirming, check fixed_stack for the choices "
                "that BIND EVERY FEATURE: how the product is delivered and run, how "
                "the user interface is realised (if there is one), where and in "
                "what form data is kept, and how the thing is packaged and "
                "started. For each one still unset, PROPOSE a concrete value with "
                "your reason and settle it with the user here — this is the one "
                "place in the pipeline where a project-wide choice has an owner. "
                "Do NOT push it down as 'the design will decide': a design decides "
                "for itself, and the next design decides again. Choices scoped to "
                "ONE feature stay out of here.",
                "Consolidate, do not re-invent: the constitution's content already "
                "exists, scattered — the fixed stack sits in each INPUT-*'s "
                "constraints, the NFR baseline in the performance register surfaced "
                "during decomposition (from the source material and any perf "
                "constraints on INPUT-*), the UI conventions in the UI-related INPUT-* material, the "
                "system type in the decomposition Round-1 analysis. Your job is "
                "to aggregate + de-duplicate + elevate them and let the user "
                "review one assembled draft, not to type governance from scratch.",
                "nfr_baseline is governance, never a requirement: performance / scale "
                "targets are NOT a INPUT-* (decomposition surfaces them as a register but "
                "does not persist them as a requirement). Assemble nfr_baseline here "
                "from that register plus any perf constraints noted on INPUT-*, each row "
                "keeping its applies-to INPUT/feature for traceability. Give the table a "
                "STRENGTH column and settle each row with the user as either "
                "`commitment` — a number the project promises to hit, which some design "
                "must later take responsibility for proving, at the cost of a fixture or "
                "an instrumented run at that scale — or `direction`, a stance that "
                "constrains how designs are chosen while asserting no number to measure. "
                "Ask for each figure whether it is a promise or an intention; a number "
                "said in passing, recorded as a commitment, becomes a MUST acceptance "
                "criterion in every design that touches it, and each of those then needs "
                "real evidence. Downstream reads this column: only commitments are asked "
                "for an owning design, and only commitments should turn into acceptance "
                "criteria. Elevate the UI "
                "conventions too: move them from the UI-related INPUT-* material into ui_conventions "
                "(that INPUT-*'s fate is a project call — the shared-component DESIGN-* "
                "may still exist and reference the constitution).",
                "The constitution governs downstream; do not restate it: the fixed "
                "stack is written ONCE here and referenced by design / task, not "
                "re-copied into every INPUT-*/DESIGN-*. During consolidation, mark the "
                "now-duplicate stack declarations in INPUT constraints as elevated.",
                "Boundaries use the three tiers always / ask-first / never, sourced "
                "from each INPUT-*'s out-of-scope notes, 待确认 items and the "
                "high-risk irreversible operations found in decomposition.",
                "Do not bloat: each section is stable governance, not a transcript "
                "of requirements. Fold small items into boundaries rather than "
                "listing them; for a section the project never discussed (e.g. "
                "quality_bar), propose a sensible default and tag it 待确认 instead "
                "of hard-coding or padding.",
                "Seed: if the user already fixed hard constraints back at the raw "
                "requirement stage (e.g. 'Flask + SQLite only, offline, single "
                "user'), merge them into the matching section before consolidation.",
            ],
            "rounds": [
                "Round 1 — consolidate a draft, do NOT persist yet: walk the confirmed INPUT-* "
                "set and assemble the six sections per source_map below, elevating the "
                "NFR / UI items the requirement conversation surfaced. (This stage is "
                "only reachable once every live INPUT-* is confirmed — see the "
                "requirement stage gate — so the material here should already be at "
                "real depth; if something still reads shallow, send the user back to "
                "reopen and clarify that INPUT-* before consolidation.) Present the "
                "six-section draft plus a 'source INPUT-*' column, "
                "then stop and wait for the user to edit.",
                "Round 2 — persist after the user confirms: write the constitution "
                "(harnoq_constitution_update, then harnoq_constitution_confirm — "
                "status→confirmed). Only once the constitution is confirmed may the "
                "design stage begin.",
            ],
            "source_map": {
                "system_nature": "requirement Round-1 system-type / pressure-surface analysis + each INPUT-*'s goal / underlying motivation",
                "fixed_stack": "the fixed stack/library declarations surfaced in the INPUT-* constraints (deduped into one), PLUS the project-wide form choices no INPUT-* happened to state — delivery/run form, user-interface form, data-keeping form, packaging and startup — each proposed and settled with the user here rather than left for a design to invent",
                "quality_bar": "if the requirement conversation never discussed it, propose a default (test bar, definition of done) tagged 待确认",
                "ui_conventions": "the UI-related INPUT-* material (page style and interaction conventions)",
                "nfr_baseline": "the performance register surfaced during the requirement conversation + any perf constraints on INPUT-* (metric / target / strength / applies-to, strength being commitment vs direction, settled with the user per row); performance is never an INPUT-*",
                "boundaries": "each INPUT-*'s out-of-scope + 待确认 + high-risk irreversible operations → always / ask-first / never",
            },
            "write": (
                "harnoq_constitution_update writes the draft (status=draft); "
                "harnoq_constitution_confirm confirms it (status→confirmed). Both "
                "locate the singleton constitution by project_number. "
                "status=succeeded means it was written."
            ),
        },
        "design": {
            "when": (
                "Building a Feature map from confirmed INPUT-* material, then "
                "turning each Feature into one or more DESIGN-* modules. "
                "Designing straight from a FEATURE-* name without engaging the "
                "INPUT-* material behind it is the most common way this stage "
                "goes wrong; the pipeline is FEATURE-* -> DESIGN-*. "
                "THIS STAGE ALSO MINES THE REQUIREMENTS. There is no separate layer "
                "between a Feature and its designs, so the detail an implementer "
                "needs gets dug out HERE, in the same conversation that settles the "
                "solution. That is why the two must stay visibly apart in what you "
                "show the user — a solution the user waves through carries every "
                "requirement assumption inside it along unexamined, and that is the "
                "characteristic failure of doing both at once."
            ),
            "principles": [
                "Treat the constitution as a real gate — a HARD STOP that covers the "
                "feature map too, not just DESIGN-* creation: design work, INCLUDING "
                "drawing the feature map, only proceeds once the project constitution is "
                "confirmed. If it is missing or not confirmed, STOP before mapping "
                "anything — send the user back to the constitution stage; do not produce "
                "a feature map or a DESIGN-* without it just to be helpful. (Every "
                "INPUT-* was already required to be confirmed to reach the constitution "
                "stage in the first place, so there is no separate requirement-"
                "confirmation precondition to re-check here — only the constitution's.)",
                "Design proceeds FEATURE-BY-FEATURE over a Feature map defined by "
                "information hiding and bounded-context analysis, NOT one-INPUT-at-a-time — and "
                "FEATURE and DESIGN-* remain two DIFFERENT granularities; do not "
                "collapse them into one map. Each Feature hides ONE "
                "independently-changing decision behind a NARROW, STABLE interface "
                "(Parnas) and must pass the two-part test: narrow interface + its own "
                "reason to change. Every Feature is tagged on TWO ORTHOGONAL axes. (1) "
                "orientation: foundation (a project-level platform layer users do not "
                "touch directly — Architecture Foundation, UI Foundation, a "
                "computation engine or a runtime adapter) vs vertical (a user-visible "
                "feature slice — whatever this project's users actually open and act "
                "in: 下单 / 排班 / 对账 / 设置). (2) subdomain_type "
                "(DDD): core (the lifeblood — hardest, framework-agnostic, must be "
                "validated FIRST; mark it is_spike) / supporting (needed but not "
                "differentiating — good-enough is fine) / generic (already solved by "
                "industry, to be bought / reused / thinly wrapped — serving, "
                "cross-process transport, object storage, document rendering). The "
                "two axes answer DIFFERENT questions "
                "and neither implies the other (a foundation Feature may be a self-built "
                "core OR a bought generic), so BOTH must be set. A FEATURE is the "
                "OWNERSHIP / plan unit — the coarsest STILL-INDEPENDENTLY-OWNABLE "
                "Feature — and a DESIGN-* is a Parnas MODULE INSIDE it. Neither carries "
                "a formal INPUT-* link: each is identified by its own name/description, "
                "established by reading the INPUT-* material. Justify a foundation / "
                "generic Feature from the constitution's fixed_stack / boundaries, never "
                "by fabricating an INPUT-* for the platform. Functional modules and page "
                "modules remain separate DESIGN-* records within a Feature, each scoped "
                "to it (feature_id).",
                "Foundation design comes first INSIDE the DESIGN stage. The agreed map "
                "must name exactly one Architecture Foundation (foundation_kind=architecture) "
                "and, whenever the project has a user interface, exactly one UI Foundation "
                "(foundation_kind=ui). They are project-level foundation Features, not "
                "user-visible functionality. "
                "THE ARCHITECTURE FOUNDATION'S SCOPE IS THE PROJECT'S CODE BACKBONE — "
                "the decisions every other Feature would otherwise each make for "
                "itself: module seams and layering, persistence and data access, "
                "cross-cutting concerns (logging / observability, configuration, "
                "error strategy), process topology, build and test scaffolding, and "
                "the contract for adding a new module. Scope it from what a backbone "
                "consists of, not from whatever runtime capability the INPUT-* "
                "happens to talk about — a foundation described as a capability "
                "hands the next stages a scope with nothing in it to design. The "
                "CHOICES inside that scope are made in its "
                "DESIGN-*; the Feature only fixes what falls inside it. "
                "Every vertical Feature depends on the "
                "Architecture Foundation and, when present, the UI Foundation; the UI "
                "Foundation may depend on the Architecture Foundation. Do not decide the "
                "architecture inside a vertical Feature map — it is decided by the "
                "Architecture Foundation DESIGN-* after the map is confirmed.",
                "FEATURE SIZE: a FEATURE-* is a goal too big for one sitting — it "
                "groups several goals that a single user COULD each finish in one "
                "sitting and see the result of. Use this in both directions when "
                "drawing the map: if you cannot name even one one-sitting goal under "
                "a proposed feature, it is not a feature but stack, boundary or "
                "policy material, which belongs to the constitution; and a feature "
                "that would carry only one such goal is probably a slice of a "
                "neighbouring feature rather than its own.",
                "A DESIGN-* IS NOT A SMALLER GOAL. A requirement sits in problem "
                "space and is sized by what a user can accomplish; a design sits in "
                "SOLUTION space and is sized by whether one mechanism holds together. "
                "One design normally covers SEVERAL of its feature's one-sitting "
                "goals and is never carved up to mirror them one for one — do not "
                "produce a design per goal, and do not treat the individual "
                "acceptance criteria you mined as designs waiting to happen.",
                "Admission test — a DESIGN-* is a MODULE in the software-engineering "
                "sense (information hiding, Parnas), not a screen and not a processing "
                "step. Each one encapsulates ONE design decision / 'secret' that can "
                "change independently behind a stable contract; decompose by hidden "
                "decisions, NOT by flowchart steps and NOT by screen. Two boundary tests "
                "decide it. (a) Cohesion & coupling: keep highly-coupled things that must "
                "agree on the same data/decision and change together in ONE module (a "
                "stable id, task state, storage boundary, recovery belong together — "
                "splitting them creates 'who owns the id' cross-talk); prefer the "
                "COARSEST still-cohesive grouping and split a sub-part out ONLY when BOTH "
                "its coupling to the rest passes through a narrow, stable interface AND it "
                "has its own reason to change (an external-service wrapper / anti-"
                "corruption layer). Grouping because parts run in the same pass "
                "(temporal) or touch the same store (communicational), or splitting by "
                "technical layer (data-model / pipeline / domain-model / API layer), are "
                "INVALID reasons — that is decomposition-by-steps, the classic anti-"
                "pattern. Cross-cutting concerns (consistency, transactions, recovery, "
                "error policy) belong to the module that OWNS the decision — normally the "
                "producer that establishes it — or are an explicit shared contract, never "
                "attached to whichever module merely touches the same store. (b) "
                "Component testability: a real module is a component you could hand off "
                "and verify on its own against its contract while stubbing the rest. A "
                "candidate you cannot accept without another not-yet-built feature (a bare "
                "data model, half a pipeline, an internal helper) is NOT a module — merge "
                "it into the cohesive function it belongs to. A user-facing page is only "
                "ONE kind of module; a HEADLESS importer, rule evaluator, or integration layer is "
                "an equally first-class DESIGN-*, verified by contract/behavior not by a "
                "screen — do NOT force it to fold into a consuming page. Naming smell: "
                "'X 模型 / X 流水线 / X 域 / X 层' usually names a stratum that fails both "
                "tests — rename it to the job it does, or merge it.",
                "Challenge before solving: restate the problem in your own words "
                "and confirm it is the right problem; question scope, assumptions "
                "and constraints before proposing any solution. A design that "
                "solves the wrong problem cleanly is still wrong.",
                "FOUR SWEEPS FOR WHAT THE MATERIAL DOES NOT SETTLE. 'Ask thoroughly' "
                "is not something you can act on — you cannot ask about what you did "
                "not think of. These four you CAN act on, because each is a search "
                "with a fixed input and a fixed output. Run all four before "
                "presenting anything. "
                "(1) UNCLAIMED GROUND — read every Feature's description across the "
                "whole map, its owns-list against its does-not-do list, and name each "
                "thing that some feature disclaims while no feature claims. Those "
                "gaps are invisible from inside one feature, which is why they "
                "survive to the build. "
                "(2) SENTENCES THAT FIGHT — read the source material against itself "
                "and find the pairs that cannot both hold as written. This one cannot "
                "be reached by scanning for blanks: nothing is missing, two things "
                "are present and incompatible, and whichever you silently pick "
                "becomes a decision the user never made. "
                "(3) THE SECOND ORDER OF EVERY NUMBER — take each figure the user "
                "gave and ask what it forces that nobody has mentioned. A stated "
                "volume implies storage, paging, and what happens when the limit is "
                "reached; a stated deadline implies what gets dropped to meet it. "
                "(4) UNQUANTIFIED WORDS — hunt down every 'immediately', 'quickly', "
                "'large', 'simple', 'as needed'. Each is a number the user has in "
                "mind and has not said, and each is a place two people will read the "
                "same sentence differently.",
                "WHO DECIDES DEPENDS ON WHICH KIND OF FEATURE THIS IS, and the map "
                "already says which. For a foundation Feature the user cannot see "
                "any of it, and asking them to choose between mechanisms they will "
                "never touch wastes the one thing this stage has to spend: their "
                "attention. Decide it yourself, show the decision with its reason, "
                "and ask for a veto rather than a choice. For a vertical Feature "
                "almost every open point is something only they can settle — what "
                "they see, what they can do, what happens on the second try — so "
                "there you ASK BEFORE you decide. Getting this backwards is expensive "
                "in both directions: questions the user has no basis to answer, or "
                "product decisions taken on their behalf and buried in a solution. "
                "WHICH OPEN POINTS ARE WORTH RAISING AT ALL — apply this before "
                "the orientation rule, because the failure it prevents is the "
                "common one. A point earns the user's attention only if reversing "
                "it later would touch more than one part, OR it fixes a contract "
                "other designs will build on, OR a quality attribute drives it. "
                "Everything else — a default value, a name, a port, a poll "
                "interval, a launch mode, a retry count with no consequence "
                "beyond itself — you DECIDE, state, and move past. "
                "Test each candidate by naming what has to be rebuilt if the "
                "answer flips; if the honest answer is 'one line of "
                "configuration', it was never a question. "
                "AND SIGNIFICANT IS NOT THE SAME AS ASKABLE: a point can bind "
                "several parts and still be one the user has no basis to judge "
                "(how two stores stay consistent, how a crash is recovered from, "
                "how a boundary is kept safe). Those you decide and put up for "
                "veto WITH the reason — handing over a menu of mechanisms is "
                "just as wrong when the mechanisms are load-bearing. "
                "ALL OF THIS IS SETTLED BY THE DECISION TABLE, a REQUIRED "
                "OUTPUT of the draft round — see its shape there. A rule "
                "attached to raising points is satisfied by raising none, so "
                "the table, not this principle, is what has to be filled.",
                "A CHOICE THE CONSTITUTION NEVER MADE IS STILL YOURS TO MAKE, AND "
                "IT HAS TO LAND SOMEWHERE. Given is a FLOOR, not a ceiling: what "
                "the constitution settled is settled, but a choice it never made "
                "does NOT thereby belong to some later stage — if this design "
                "stands on it, THIS design names it. The ask-first boundary means "
                "ask the user in the conversation where the decision comes up, "
                "which is this one; it never means defer to implementation, and "
                "'to be chosen before implementation' is that deferral wearing a "
                "different hat. THEN PUT IT WHERE IT WILL BE FOUND: a choice that "
                "binds OTHER Features — the ASR test above — left in this "
                "design's own text governs nothing, because the next Feature's "
                "design reads the constitution, not your DESIGN-*, and decides "
                "again; and the constitution that ships to the coder never "
                "mentions it. Once the user has settled such a choice, carry it "
                "up into fixed_stack (harnoq_constitution_reopen -> "
                "harnoq_constitution_update -> harnoq_constitution_confirm, or a "
                "CR-* when work already stands on the old wording) and say "
                "plainly that you are doing so. The decision table's 归属 column "
                "is where this gets decided row by row; a value scoped to this "
                "design alone stays here.",
                "PRESENT IT IN TWO COLUMNS, ALWAYS: 'what I understand you want' and "
                "'what I intend to build'. This stage settles both in one "
                "conversation, so without the split an approval of the second silently "
                "approves the first, and the requirement assumptions ride along "
                "unexamined — which is exactly how a task ends up coded against a "
                "guess. EVERY LINE OF THE FIRST COLUMN CARRIES ITS SOURCE — 用户说的 "
                "for what they actually said, 我补的 for what you supplied; an "
                "unmarked column reads as agreed material. The two markers do not "
                "collide: in the DRAFT, 我补的 means supplied and "
                "not yet ruled on, while 待确认 is banned as a VALUE (it is the gap "
                "that never gets corrected). Only when the column lands in the "
                "written design does an unconfirmed supplied line become 待确认 "
                "there — never resolved by you quietly picking a value. The first column "
                "has a home in the record: it "
                "lands in the mined_requirements field. A requirement detail "
                "settled in conversation and never written there is gone.",
                "Default to one recommendation, surface options only on genuine "
                "forks: give your single recommended design and make the key "
                "decisions behind it explicit — what you chose and WHY, and the "
                "notable alternatives you considered and rejected, so the decision "
                "stays auditable. Do NOT manufacture a throwaway option just to look "
                "thorough. Present an actual choice to the user ONLY when there is a "
                "real fork you cannot resolve from the requirement / constitution / "
                "context on your own — one where the alternatives lead to materially "
                "different structure or cost. Fabricating a strawman alternative to a "
                "decision you are already confident about wastes the user's decision "
                "and dulls the moments that genuinely need them.",
                "Name the failure modes: for the chosen solution state the risks, "
                "the worst-case / irreversible outcomes, and how the test strategy "
                "covers them. Mark anything still unknown as an open question "
                "instead of papering over it.",
                "UI Foundation: if the project has any web/app pages there "
                "must be one dedicated UI Foundation DESIGN-* — covering the "
                "页面风格与交互规范 foundation described in the INPUT-* material — that fixes the shared basis for "
                "every page: navigation model, layout, list/detail/modal patterns, "
                "common controls, form behavior, empty/loading/error/disabled states, "
                "status badges, destructive-action confirmation, responsive behavior "
                "and visual language. Design this foundation after Architecture Foundation and before any concrete "
                "feature page, and make each page build on it instead of re-deciding "
                "conventions.",
                "Split capability from page, and key page designs to the SCREEN, "
                "not to the requirement text: the functional DESIGN-* stays about the what / why "
                "/ logic (problem, solution, data / state / API behavior). The "
                "user-facing surface is designed as a separate 页面 DESIGN-*, and its "
                "unit is one screen — page type, information hierarchy, entry point, "
                "controls, state transitions, validation / errors, status display and "
                "how the UI talks to APIs / state machines. When several capabilities render "
                "on the SAME screen (e.g. a timeline with its filter and time "
                "display, or a search-results screen), do NOT create rival page "
                "designs: make ONE 页面 DESIGN-* for that screen that covers only the "
                "page-specific layout / interaction / state and REFERENCES — in prose, "
                "never as a formal coverage record — the functional DESIGN-*(s) whose "
                "capability it renders; rendering or consuming a capability on a page is "
                "a prose reference, never a duplicate module. Each page DESIGN-* "
                "depends on the UI Foundation DESIGN-* and on the "
                "functional DESIGN-*(s) it renders; state those dependencies in prose "
                "(there is no structured design-dependency field) and reference the "
                "foundation instead of restating its conventions. A functional "
                "DESIGN-* may have no page of its own and is still a first-class module, "
                "accepted against its contract: write 'no direct page' and name its "
                "contract plus the screens / flows that consume it. Only a fragment that "
                "cannot be independently accepted — a bare data model or half a job — "
                "folds into the cohesive module it belongs to, rather than becoming its "
                "own DESIGN-*.",
                "A capability that several modules need is REFERENCED, not buried "
                "inside one of them: bury it and the others end up reaching into that "
                "module's internals, and the boundary you drew stops meaning "
                "anything. But do NOT turn this into consumer-counting — a tally of "
                "who-uses-what, applied mechanically, manufactures a module per "
                "shared routine and leaves you with strata. The question is whether "
                "the capability changes for its OWN reasons behind an interface its "
                "callers do not need to see: if it does it is a module wherever it "
                "sits, and if it does not, having two callers does not make it one.",
                "Do not fabricate — THE BAN IS ON WRITING, NOT ON DRAFTING, and "
                "confusing the two is what starves this stage of detail. What may "
                "never be invented is what gets WRITTEN INTO THE DESIGN FIELDS: an "
                "API you have not seen, a library's behaviour, a constraint or a "
                "requirement the source material did not state — keep unfamiliar "
                "libraries/terms verbatim and 待确认 THERE until the user explains "
                "them. The Round-3 draft is the opposite case and the rule inverts: "
                "it is shown in conversation and never persisted, and its whole job "
                "is to be wrong somewhere the user can see. Leaving a blank there, "
                "or writing 待确认 in place of a value, is the ERROR — a gap is "
                "invisible and nobody corrects it, while a wrong specific gets "
                "struck out on sight. Guess a concrete value, say it is a guess, "
                "and let them strike it.",
                "Give the solution a buildable shape — and a one-line "
                "responsibility per part IS NOT ONE: a sentence naming the job "
                "constrains nobody, and whoever builds it invents the whole "
                "interface. We take the decomposition criterion from Parnas "
                "(information hiding, above) and we owe the DOCUMENTS that go with "
                "it. selected_solution must carry all three. "
                "(1) WHAT EACH PART HIDES — the one decision it encapsulates, so a "
                "reader can tell what may change behind it. "
                "(2) THE INTERFACE — one row per operation another part calls, in "
                "the fixed-column table the draft round specifies. A part named "
                "in the solution with no operations is undesigned, and any "
                "behaviour other parts depend on that no row expresses is a hole "
                "each caller will fill differently. "
                "(3) THE USES RELATION — for each part, which other parts it is "
                "ALLOWED to call, and the directions that are FORBIDDEN. It must "
                "have no cycles; if two parts must call each other, the boundary "
                "is wrong (go back) or one direction is really an event the other "
                "records. Without this, call direction gets decided at build time "
                "by whoever needs something, and the boundaries stop meaning "
                "anything. "
                "ALTITUDE, and the line is NOT where it looks: the INTERFACE is "
                "design and belongs here — it is the thing other modules and the "
                "coder are bound by. What belongs to the coder is everything "
                "BEHIND it: how the module is split into functions internally, "
                "file and package layout, algorithms, and the data structures "
                "that are not part of the contract. Do not use 'that is "
                "implementation detail' to avoid naming an operation others must "
                "call. "
                "The test: a coder agent building from this design should have to "
                "invent nothing that another part can observe.",
                "Persistence must be CONCRETE, not prose — this is per-design, NOT "
                "reserved for one single 'foundation': ANY DESIGN-* that OWNS "
                "persistent state — it defines a table, an index, or a persisted "
                "status / state machine (an asset store, a sync-state model, a path "
                "index) — AND every Architecture Foundation DESIGN-* that establishes a core model many "
                "later designs build on (the data/domain counterpart to UI Foundation) MUST, "
                "in selected_solution, pin down two things. (1) The module's NARROW "
                "INTERFACE — the concrete operations/signatures OTHER modules call and "
                "its own reason to change, per the Parnas framing above (the "
                "module-boundary contract, NOT the internal function names / file "
                "layout the coder still owns). (2) The CONCRETE STORED SHAPE — the key "
                "tables + their columns (or equivalent stored shape), the identity / "
                "uniqueness / ownership invariants (e.g. '当前路径 -> asset_id 是 1:1'), "
                "and the explicit STATE VALUES when it persists a status (e.g. 写入中 / "
                "已同步 / 删除中 / 失败待修复), with ONE single source of truth per fact "
                "(no fact stored in two places). Prose like '维护主记录与同步状态' or "
                "'保存 asset_id 及路径映射' is NOT a design — name the actual fields and "
                "the actual states. Design ONLY the persistence / interface the Feature "
                "ACTUALLY OWNS: a generic platform Feature that owns storage → its "
                "tables + the upward contract (BOTH); a core engine Feature usually owns "
                "a core data structure / on-disk index format (NOT necessarily a "
                "relational table) → 'persistence-concrete' here means pinning down "
                "THAT shape + its query interface; a PURE-UI Feature owns NEITHER → do "
                "NOT fabricate a table for it, design its component contracts / "
                "interactions instead. SELF-CHECK before PRESENTING the written "
                "design (Round 5), not merely before confirming: if a "
                "persistence-owning selected_solution contains no table / column / "
                "state names, it is UNDER-specified — go deeper before showing it. "
                "Waiting until confirm to check means the user reviews prose that "
                "says WHAT is stored without ever saying HOW, and approves it.",
            ],
            "rounds": [
                "Round 1 — the stage gate, then the BLOCKS ONLY: what the project "
                "divides into and what each block covers. No dependencies (Round 2) "
                "and no design yet. START by reading project-context "
                "(NOT the bare INPUT-* list — that is what makes agents slide back to "
                "one-INPUT-at-a-time); it carries the EXISTING FEATURE-* map, the "
                "constitution and this method. The design unit is a FEATURE (a "
                "coherent capability or project-level foundation — Feature carries no formal "
                "covered-item list), NEVER a lone piece of the requirement text walked "
                "one line at a time. If "
                "FEATURE-* records ALREADY EXIST, ADOPT that map instead of "
                "redrawing it: pick the Feature to design (a core / foundation Feature "
                "before the verticals that depend on it), state explicitly which "
                "FEATURE-* you are about to design and what it is scoped to (from its "
                "name/description and the relevant INPUT-* material), then "
                "design it as ONE deliverable — skip Rounds 1 and 2 entirely (the blocks "
                "and their order already exist) and go straight to Round 3. Draw the "
                "map from scratch ONLY when NO FEATURE-* exist yet. "
                "GATE FIRST: verify the project constitution is confirmed (see "
                "project-context); if it is missing or not confirmed, stop and complete "
                "constitution consolidation first — design must not start without it. (Every "
                "INPUT-* was already required to be confirmed to reach this stage, so "
                "there is no separate requirement-confirmation precondition to re-check "
                "here — only the constitution's.) Then READ the "
                "constitution and treat it as given: the fixed stack, NFR baseline and "
                "UI conventions come from it — use them, do not re-derive them and do "
                "not re-cover them as INPUT-*/DESIGN-* content (what to do about "
                "a choice the constitution never made is in the who-decides "
                "principle, not here). Once the gate passes, "
                "DRAW THE FEATURE MAP. A Feature is a slice of the project whose "
                "coverage you can state in one line, and that the user would "
                "recognise either as something the product does or as something it "
                "is built on. Cut where something changes for its own reasons behind "
                "a narrow interface — never by processing step, never by technical "
                "layer. KEEP THE MAP AS SMALL AS THE PROJECT IS: this is one project "
                "divided into a handful of ownable blocks, not an architecture "
                "diagram. Two blocks are given: the code backbone (Architecture "
                "Foundation, foundation_kind=architecture, scope as set out above) "
                "and, when the project has pages, the shared interface conventions "
                "(UI Foundation, foundation_kind=ui). Whatever this project's hardest "
                "or least certain computation turns out to be gets its own Feature, "
                "marked is_spike so it can be proved early. Everything else is named "
                "after what it does. "
                "CHECK THE CUT WITH REAL REQUIREMENTS, never with an inventory of "
                "capabilities: take a handful of things the user actually asked for — "
                "the awkward ones — and for each ask which SINGLE Feature would "
                "deliver it. If the answer is three Features cooperating, or if it "
                "takes a paragraph to explain who owns it, the boundary is wrong and "
                "the requirement is not the vague thing. Splitting until every "
                "capability has a box of its own passes this test on paper and fails "
                "it in practice: when most of the map is platforms, cores, hosts and "
                "layers, it was cut by layer whatever the blocks are called. "
                "For every Feature: (i) tag it "
                "orientation (foundation / vertical) AND subdomain_type (core / "
                "supporting / generic) — the two axes are independent, set BOTH; mark "
                "is_spike on each core Feature. For the two project foundations set "
                "foundation_kind=architecture or foundation_kind=ui respectively. "
                "(ii) A foundation / generic Feature "
                "carries no INPUT-* link at all (no Feature does) — justify it from the "
                "constitution's fixed_stack / "
                "boundaries and state WHAT INTERFACE it provides upward. "
                "One engine expressed piecemeal across the material is still ONE "
                "engine, and stays one Feature rather than being scattered among the "
                "Features that use it. "
                "A FEATURE stays the coarsest STILL-OWNABLE unit — neither a synonym "
                "for a DESIGN-* module nor a whole infrastructure layer; a block "
                "named like a stratum ('… backbone / 层 / 基础设施 / 域') is one you "
                "have not finished thinking about. Cross-cutting conventions — "
                "errors, retries, logging, consistency — are the Architecture "
                "Foundation's, not blocks of their own. "
                "DO NOT WORK OUT WHAT WAITS FOR WHAT IN THIS ROUND — that is Round 2, "
                "deliberately separated. Deciding the order while you are still "
                "deciding what the blocks ARE biases the cut: a map of strata stacks "
                "into a tidy sequence, so anything trying to produce a tidy sequence "
                "drifts into strata, whereas a map of things the user recognises has "
                "an irregular order and that is correct. "
                "Present the blocks — each Feature, its one-line coverage and its two "
                "tags — and wait for the user before going on.",
                "Round 2 — dependencies, which are also the check on Round 1's cut. "
                "WHAT A DEPENDENCY MEANS HERE, AND IT MEANS ONLY THIS: 'A depends on "
                "B' says B MUST BE FINISHED BEFORE WORK ON A CAN START. That is the "
                "one question this answers, because it is what the system computes "
                "from it — the order work becomes claimable in. "
                "IT IS NOT 'A USES B'. If A uses something B produces but the bulk of "
                "A can still be built and delivered first, that is not a dependency. "
                "Do not record how data flows, who calls whom, or what sits on top of "
                "what — those are all true of almost every pair of blocks, which is "
                "how this turns into a list nobody can act on. "
                "GO BLOCK BY BLOCK, one line each: which blocks this one waits for, "
                "AND WHAT OF IT IS STUCK until they are done. If you cannot name the "
                "specific thing that is stuck, there is no dependency. "
                "THE TWO MISTAKES DO NOT COST THE SAME, SO SPEND YOUR ATTENTION "
                "ACCORDINGLY. What you write here only ever HOLDS WORK BACK; it never "
                "makes anything go sooner. One wait too many just means that block "
                "starts later — harmless. One wait MISSING means something gets built "
                "while what it needs does not exist yet, against a contract nobody "
                "has written — that is the failure that costs, and it is not visible "
                "until someone is already building. So DO NOT PRUNE FOR TIDINESS: a "
                "wait you are unsure about stays, and if only a corner of the block "
                "is stuck the wait still stays (starting later costs nothing). If "
                "that corner makes you think the block was cut wrong, say so — but "
                "say it, do not express it by dropping the wait. "
                "THEN SWEEP FOR WHAT IS MISSING, which is the pass that actually "
                "matters: go through the blocks a second time and ask what each one "
                "needs to ALREADY EXIST — the data it reads, the contract it calls, "
                "the thing its own description says it REUSES rather than "
                "reimplements. A block whose description says it reuses another "
                "block's work while not waiting for that block is the mistake to hunt "
                "for; it reads as clean and it is the one that hurts. "
                "DO NOT DRAW A TREE OR A DIAGRAM. The per-block lines above are the "
                "whole answer; a hand-drawn picture beside them has contradicted them "
                "every time and is not what gets stored. "
                "TWO BLOCKS MUST NEVER WAIT FOR EACH OTHER — neither could ever "
                "start. If that comes out, the two are really one block or the "
                "boundary between them is in the wrong place: say so and go back to "
                "Round 1. "
                "TWO THINGS ARE WORTH NOTICING, THOUGH NEITHER IS A DEFECT ON ITS "
                "OWN — mention them, do not silently redraw anything to avoid them. "
                "(a) A block marked is_spike that waits for many others: it carries "
                "that mark so the core risk gets proved EARLY, so say whether that "
                "still happens. (b) A block that waits for most of the project: often "
                "a sign it was cut as a page rather than as a capability. "
                "Note that many blocks waiting on the project backbone is normal and "
                "correct — it is the bottleneck by nature, and writing that out costs "
                "nothing: the claim gate only ever looks at what a block waits for "
                "directly. Present these lines, and after the user "
                "confirms them, FIRST materialize Architecture Foundation, then UI "
                "Foundation, then the remaining Features with harnoq_feature_create (name, "
                "description, orientation, foundation_kind when applicable, "
                "subdomain_type, is_spike, optional assignee) AND record what each "
                "block waits for in depends_on_feature_numbers — a dependency that "
                "stays in the conversation was never recorded. "
                "description is NOT optional decoration — it is the Feature's DESIGN-ENTRY "
                "CARD, the only context an agent has if it later opens this ONE FEATURE "
                "without re-reading the whole INPUT-*. Write it as FOUR separate markdown "
                "lines, one per label below — never collapse them into one paragraph, "
                "each label must visibly appear so a reader (and a reviewer) can check "
                "none was skipped: "
                "(a) 归属: what this Feature owns / is responsible for and WHY it is an independent Feature; "
                "(b) 契约: what contract it exposes upward and what it depends on; "
                "(c) 关键数据: the key data / state / invariants it owns; "
                "(d) 不做: what it explicitly does NOT own. "
                "A description that just restates the name (e.g. '提供一致检索语义'), or "
                "that skips any of the 4 labels, or that merges them into flowing prose, "
                "is UNACCEPTABLE — it leaves the boundary for whoever reads this feature "
                "later to invent from scratch.",
                "Round 3 — the DESIGN DRAFT: one concrete design, both columns, "
                "then stop. "
                "ONE PASS OF ROUNDS 3-5 COVERS ONE WHOLE FEATURE — all of its "
                "DESIGN-* together. It is NOT run once per DESIGN-*: the draft is "
                "written for the feature, and rounds 4-5 walk that feature's "
                "designs inside a single round each. "
                "FOUNDATIONS BEFORE VERTICALS: the Architecture Foundation is "
                "designed first, then the UI Foundation when the project has "
                "pages, and only after both are confirmed a vertical Feature — a "
                "vertical draft builds on them and references their conventions "
                "instead of restating them. "
                "Open with a short frame — the problem restated in your own "
                "words, this feature's boundary (design_scope), "
                "non_goals, and the constraints the constitution already fixes — "
                "then go straight into the draft in the same turn: the frame is "
                "orientation, not a checkpoint, and it never stops for approval. "
                "(Every INPUT-* was already confirmed to reach this stage; the "
                "only gate to re-check is the constitution's.) "
                "THE DRAFT IS ONE TABLE OF THREE COLUMNS — the forcing table and "
                "the two columns of the principle above are the SAME table, not "
                "three artifacts; kept apart you write each requirement twice. "
                "Column 1, 材料: the requirement, constraint or figure as "
                "STATED, each marked 用户说的 or 我补的. ONE MATERIAL PER ROW: "
                "the forcing column is the detector — if 强迫出的结构 answers "
                "only PART of what 材料 states, the row is carrying two "
                "materials and the one that got no answer is escaping; split "
                "the row. A foundation Feature's first column is naturally "
                "short — that is correct, do not pad it. "
                "Column 2, 强迫出的结构: what that row FORCES — what must "
                "therefore exist, be separated, be ordered, be bounded or be "
                "given up. Push it until it names something structural: a "
                "boundary, an ownership, an ordering, a state that must survive, "
                "a thing that may not happen. A cell that merely restates "
                "column 1 in other words has not been pushed. "
                "Column 3, 我打算这么做: the decision that answers it. Every row "
                "gets one; a row you cannot answer yet is the open point, and it "
                "goes to the user by the significance rule, not into a blank "
                "cell. "
                "Two rows that force incompatible structures are a "
                "REQUIREMENT CONFLICT — name it as one, do not quietly satisfy "
                "one side; and a figure that forces nothing means you have not "
                "worked out its second order yet. REQUIRED FINAL ROW IS "
                "冲突检查: the table's own last row, not a paragraph after it — "
                "材料 fixed to the literal 冲突检查, and the remaining cells "
                "hold what cross-checking the 强迫出的结构 column against "
                "itself found: the row pairs that cannot both hold and what "
                "each forces, or the literal 无冲突 when none do. Missing this "
                "row means the table is not finished — an obligation written "
                "beside a spec gets skipped; only what is part of the "
                "artifact's own shape survives. "
                "THEN, BELOW THE TABLE, THE SHAPE THE THIRD COLUMN ADDS UP TO — "
                "the design itself: the parts, WHAT EACH HIDES, its INTERFACE "
                "and the USES RELATION per the buildable-shape principle; where "
                "the feature owns persistent state, the concrete stored shape "
                "per the persistence principle; where it owns a screen, the "
                "walk-through of what the user opens, does and sees — the first "
                "time, the second time when state already exists, and what "
                "failing looks like. "
                "THE INTERFACE IS A TABLE, NOT PROSE — FIXED COLUMNS, ONE ROW PER "
                "OPERATION: 操作 | 入参 | 返回 | 前置 | 后置 | 失败. Every "
                "operation any other part calls gets a row, and every cell gets "
                "something: an empty cell is written 无 so it reads as a decision "
                "rather than an omission. 散文会衰减(第一个部件写满, 后面瘦成裸"
                "签名), 格子不会——A cell cannot thin out: it is filled or it is "
                "visibly empty. The form also closes the two dodges that read "
                "as done: "
                "module-wide bullets ('writes commit atomically', 'callers never "
                "get a raw connection') are a policy note that fills no row and "
                "may accompany the table but never replace it; and a placeholder "
                "type — `read(request) -> Result`, `write(data)`, `handle(event)` "
                "— cannot fill 入参 or 返回, because 'a request' is not a shape "
                "anyone can build against. Name the fields it carries, or the "
                "closed set of forms it may take. "
                "CHECK EVERY ROW AGAINST WHAT ITS PART CLAIMS TO HIDE: an 入参 "
                "or 返回 that exposes the very thing the part claims to hide "
                "means the secret is LEAKED, and there are exactly two "
                "legitimate fixes — changing the interface, or narrowing the "
                "secret declaration to what the part actually hides; forcing "
                "that choice is what this check is for. "
                "ORDER THE PARTS BY HOW MANY ARROWS POINT AT THEM in the uses "
                "relation, most-depended-on FIRST — attention runs out down a "
                "list, so spend it where vagueness spreads furthest. "
                "THIS ROUND WRITES THE SOLUTION SHAPE AND NOTHING ELSE. "
                "acceptance_criteria, risks and test_strategy are Round-5 "
                "fields, and criteria written now have not been past the "
                "Round-4 audit. Same for a separate "
                "scope/non-goals block if the frame above already stated it — say "
                "each boundary ONCE. "
                "WHAT THE DRAFT DECIDES IS READ OFF THE FEATURE'S OWN "
                "DESCRIPTION: its 归属/契约/关键数据/不做 lines say which "
                "decisions belong to this feature, and the draft makes exactly "
                "those decisions, concretely. A feature that owns structure gets "
                "its structure decided here — how the code divides, what may "
                "depend on what, where shared code lives and what qualifies to "
                "enter it, how the next module plugs in; a feature that owns "
                "behaviour gets its behaviour decided. 高度画错的草稿挖不出东西"
                "——对具体错误决定的纠正就是你没想到要问的那个问题。 "
                "GUESS, DO NOT LEAVE GAPS: every open point and every number "
                "gets one specific value marked 我补的, never an unresolved "
                "placeholder — see the fabrication principle, whose ban on "
                "invention inverts for the draft because the draft is shown, "
                "never persisted. "
                "Give ONE recommended design with its reasons and name "
                "real alternatives only at genuine forks (per the recommendation "
                "principle); the standing exception is the Architecture "
                "Foundation, which MUST compare at least two genuine architecture "
                "options — module seams, persistence, cross-cutting concerns "
                "(logging, configuration, error strategy), process topology, "
                "build/test scaffolding and a walking skeleton — with "
                "rejected_solutions retained. THE OPTIONS ARE NAMED, NOT SHAPED: "
                "an architecture with the load-bearing technology left blank "
                "cannot be compared, costed or vetoed — each option NAMES what it "
                "stands on wherever the constitution left the choice open, and the "
                "recommendation says why that one. Presenting the named choice "
                "for veto here IS how the constitution's ask-first boundary gets "
                "satisfied — it is the asking; handing it forward leaves it to be "
                "answered mid-build around a blank. "
                "CLOSE WITH THE DECISION TABLE — REQUIRED, THREE COLUMNS: "
                "决定 | 翻过来要重建什么 | 归属. One row per decision this draft "
                "makes that the user could sensibly overturn. The middle cell "
                "names what has to be REBUILT if the answer flips, never 'easy to "
                "change later'; the moment you write 'one config value' there, "
                "you have your answer — take the row out, decide it yourself, and "
                "state it as 我补的 in the draft where the veto still catches it. "
                "归属 is 本设计 or 上收 fixed_stack, and the test is the ASR test: "
                "a decision that binds OTHER Features goes up, and after the user "
                "settles it you carry it there per the who-decides principle. "
                "Ending with a bare 'tell me what to change' is this pass NOT "
                "RUN — so is a table where no row carries a cost. "
                "THEN STOP, per WHO DECIDES: for a foundation Feature ask for a "
                "VETO of the decisions; for a vertical Feature ask the user to "
                "strike and correct both columns. Do NOT run the sweeps, do NOT "
                "raise challenges, do NOT ask a question list in this turn — that "
                "is Round 4, and it is worth more once the draft has been "
                "corrected. (One fold is allowed: a small Feature with no genuine "
                "fork may take Round 4 in this same turn, draft, sweeps and "
                "questions in one message. The correction stop and the Round-5 "
                "approval of the written text never fold, and nothing ever runs "
                "per DESIGN-*.)",
                "Round 4 — audit the CORRECTED draft, then ask what is still open. "
                "Work from the draft AS THE USER MARKED IT UP, never from your "
                "original: their corrections are the truest material this stage "
                "will get, and each one typically kills a question you were about "
                "to spend their attention on. Then run the four sweeps "
                "(unclaimed ground, sentences that fight, the second order of every "
                "number, unquantified words) and walk the depth_bar over the draft — "
                "all five keys VERBATIM (trigger, inputs_outputs, figures, "
                "failure_and_rejection, boundaries), each marked 已定 / 部分 / 空 "
                "with what is missing spelled out. The depth_bar audits the draft; "
                "it does not replace it, because every one of the five can read 已定 "
                "while nobody knows what actually happens step by step. Put the open "
                "points to the user as MULTIPLE-CHOICE questions — one decision per "
                "question, 2 to 4 mutually exclusive options answering that same "
                "question, exactly one marked as your proposed DEFAULT so they can "
                "strike a wrong choice instead of answering from a blank page. A "
                "numeric open point gets the same treatment: a default plus at least "
                "one alternative someone would plausibly pick, never an open-ended "
                "'what should this be?'. Whether you ask before deciding or decide "
                "and ask for a veto follows the feature's orientation (see the "
                "principle on who decides). "
                "Decide whether this feature has a direct user-facing "
                "surface. If yes: confirm the UI Foundation DESIGN-* already exists (if "
                "the project has pages but it does not, design that foundation first) "
                "and treat it as the basis, then map the capability (as described in the "
                "INPUT-* material relevant to this feature) to the screen(s) it renders "
                "on, and "
                "list per screen the entry points, primary user actions and state / "
                "empty / error / loading / disabled cases that screen's 页面 DESIGN-* "
                "must cover. If no direct page, it is a headless module: name its "
                "contract and a standalone acceptance criterion (verifiable by stubbing "
                "the rest), plus the screens / flows that consume it. Only a fragment "
                "that cannot be independently accepted folds into the module it belongs "
                "to. Raise "
                "up to 8 design-level "
                "challenges (right problem? feasible at the stated scale and cost? "
                "irreversible failure? conflicts with other parts of the requirement or "
                "other DESIGN-*? cheaper "
                "alternative?), each with source / challenge / suggestion. Then stop "
                "and wait for the user.",
                "Round 5 — write, present the written text, confirm only on fresh "
                "approval. After the corrected draft and the Round-4 answers "
                "settle the direction, fill the writable fields via "
                "harnoq_design_case_update for each of this feature's DESIGN-* to "
                "the depth bar in field_standards, leaving each as a DRAFT. THE "
                "CORRECTED ROUND-3 DRAFT'S LEFT COLUMN LANDS IN "
                "mined_requirements, carried over line by line — it is the only "
                "place the mined requirement is stored, so do not compress it "
                "into the solution text and do not leave it in the "
                "conversation: a detail the user corrected and did not land "
                "here is lost, and the coder builds against the guess it "
                "replaced. Keep "
                "the functional DESIGN-*'s design_scope / selected_solution about the "
                "what / why / logic plus the component shape (per field_standards); in the 页面 DESIGN-* put the concrete page / "
                "interaction design into design_scope and selected_solution, "
                "reference the UI Foundation DESIGN-* and the functional DESIGN-* in "
                "constraints / design_scope, put UI-specific risks in risks, UI test "
                "coverage in test_strategy and visible behavior in "
                "acceptance_criteria. "
                "Writing is not approval: filling forced sub-decisions (exact "
                "component split, state/threshold rules, acceptance wording) the "
                "user never saw, so present the COMPLETE written design back "
                "field by field, call out those sub-decisions, and record "
                "anything still uncertain in open_questions. BEFORE presenting, "
                "run THREE CHECKS and state each result. (1) SCOPE-CLOSED: walk "
                "acceptance_criteria against non_goals one criterion at a time — "
                "every criterion must be provable from what this design delivers "
                "plus its declared upstream; one that reaches into something "
                "non_goals disowns is a defect to fix NOW. (2) COVERAGE, the "
                "reverse direction: list every capability the selected_solution "
                "says this design OWNS and confirm each is pinned by at least "
                "one criterion that would FAIL if that capability were missing. "
                "Criteria gravitate to whatever is easiest to phrase as "
                "observable behaviour — a latency number, a bind address — and a "
                "capability carrying no criterion never gets designed past a "
                "one-liner, which is how the cross-cutting parts of a backbone "
                "stay vague while the metrics collect all the criteria. This is "
                "the same back-check task planning runs; it runs HERE first "
                "because fixing it now is free and fixing it there is a reopen. "
                "(3) DEPTH, on the text you are about to show — read the tables, "
                "do not re-argue the bar: the interface table has a row for "
                "every operation and no cell left blank (无 counts, empty does "
                "not) and no 入参/返回 cell holding a placeholder type or "
                "exposing what its part claims to hide; the uses "
                "relation is present and free of cycles; and wherever this "
                "design owns persistent state — and in every Architecture "
                "Foundation design — the concrete tables, columns and state "
                "values are there. A part carrying only a one-line "
                "responsibility is undesigned, and the build will invent "
                "whatever it needs. DEPTH ALSO "
                "CLOSES THE UNNAMED DEPENDENCY: nothing this design stands on may "
                "leave here unnamed. 'Not yet selected', 'to be decided before "
                "implementation' or 'subject to asking first' about a load-bearing "
                "dependency is NOT an open question and does not belong in "
                "open_questions — open_questions is for what genuinely cannot be "
                "settled yet, never a place to park a decision this conversation "
                "was the right moment to make. Either it was put to the user and "
                "answered in this stage, or the design is not finished: go name "
                "it, say why, and ask for the veto. All three checks "
                "cost nothing here and cost a generation, a claim and a rework "
                "after the task is claimed. Then obtain a FRESH explicit "
                "approval of THIS written "
                "text and confirm each design only then — never fill-and-confirm "
                "in one turn. If the user wants changes, edit and re-present; do "
                "not confirm until they approve the written design as-is. If a "
                "confirmed design must change, harnoq_design_case_reopen is the "
                "only path back to draft before editing. Coverage gate — do not "
                "trust your own tally: after confirming the feature designs, read "
                "feature_map.pending_design_feature_numbers in project-context. "
                "It must be empty. Any FEATURE-* still listed there has no "
                "confirmed DESIGN-* yet — give it one. This mechanical check is "
                "the source of truth for coverage, not a hand count of the map.",
            ],
            "writable_fields": [
                "problem_statement", "mined_requirements", "verifies_nfr",
                "design_scope",
                "non_goals", "assumptions",
                "constraints", "selected_solution", "selection_reason",
                "rejected_solutions", "impact_areas", "risks", "test_strategy",
                "acceptance_criteria", "open_questions",
            ],
            "field_standards": {
                "problem_statement": "The problem restated as a DESIGN problem: "
                "what must be decided here and why it is hard — not a copy of "
                "the requirement text.",
                "mined_requirements": "需求(我理解你要的是) — the Round-3 draft "
                "AS THE USER CORRECTED IT: the walk-through, the second-time "
                "path, the failure paths and the numbers that got settled, all "
                "in behaviour terms. ONE LINE PER REQUIREMENT, each marked "
                "用户确认 / 我补的(已认可) / 待确认 so a later reader can tell "
                "what was said from what was supplied — the marks are read by "
                "the project-level requirement view, so a line without one is "
                "invisible there. This is the ONLY place the mined requirement "
                "is stored: no upstream object holds it, so whatever is missing "
                "here is missing from the project. A detail elicited later, in "
                "any stage, comes back HERE — this field is the requirement's "
                "single home no matter which stage discovered it.",
                "verifies_nfr": "Which COMMITMENT rows of the project "
                "constitution's nfr_baseline THIS design proves, one per line, "
                "each quoting "
                "the metric name as the constitution writes it and saying how "
                "it is measured. When this design proves none — most do not — "
                "write 无 rather than leaving it blank: confirming a design "
                "requires an ANSWER to this question whenever the constitution "
                "states any commitment, and blank is not an answer, it is the "
                "question never having been put. Rows the baseline marks as a "
                "direction are not claimed here and must not become acceptance "
                "criteria — a direction asserts no number, so a criterion built "
                "on one can only be discharged by a test that measures nothing. "
                "It is its own field because a "
                "commitment named "
                "only inside test_strategy prose cannot be counted, and what "
                "cannot be counted cannot be shown MISSING: the baseline binds "
                "the whole project, so a commitment no design claims is a "
                "promise with no owner, and that is invisible until someone "
                "asks each "
                "design in turn. Do not claim a row to look thorough — a false "
                "claim is worse than an empty field, because it retires the "
                "question. Claiming a commitment binds THIS design to produce "
                "evidence at the stated scale, so weigh what that costs before "
                "claiming, and if the figure looks like an aspiration rather "
                "than a promise, raise it with the user — the strength belongs "
                "in the constitution, not worked around here.",
                "design_scope": "范围 — what this design owns vs. what it "
                "explicitly leaves to neighboring DESIGN-*, naming the adjacent "
                "designs it feeds or consumes, so the boundary is checkable. "
                "The requirement itself does NOT go here; it goes in "
                "mined_requirements.",
                "selected_solution": "The chosen rules/behavior AND the buildable "
                "shape, per the buildable-shape principle: the parts, WHAT EACH "
                "ONE HIDES, the data it owns, and — for every part — ITS "
                "INTERFACE, one entry per operation others call (name, inputs, "
                "returns, preconditions, postconditions, what happens when it "
                "cannot comply), plus the USES RELATION saying which part may "
                "call which and which directions are forbidden, and the contracts "
                "consumed from / provided to adjacent DESIGN-*. A part with a "
                "one-line responsibility and no operations is undesigned. The "
                "interface is design; what is behind it — internal function "
                "split, file layout, algorithms, non-contract data structures — "
                "belongs to the coder. "
                "ARCHITECTURE FOUNDATION ONLY — NAME WHAT ASSEMBLES THE PARTS: "
                "a set of parts that each work is not a product. State what the "
                "product RUNS: the entry point that wires these parts into "
                "something startable, and what it starts. If this design builds "
                "it, it is a part here like any other, with its interface. If "
                "another DESIGN-* builds it, name that design. 'Nobody' is not "
                "an available answer — every part being someone's and assembly "
                "being no one's is how a foundation ships a library that no one "
                "can run, while each part passes its own criteria.",
                "rejected_solutions": "A JSON LIST of objects, each "
                "{name, description, reason} — the service rejects anything "
                "that is not a list, so prose here costs a failed write and a "
                "retry. name: the option as you presented it (named, per the "
                "options-are-named rule); description: what it was, at "
                "component level; reason: the concrete trade-off that lost, "
                "never 'more complex'.",
                "open_questions": "A JSON LIST of strings — the service "
                "rejects anything else. What genuinely cannot be settled yet; "
                "NOT a parking space for a load-bearing choice this stage "
                "should have made (see the depth check).",
                "selection_reason": "Why this option beat the concrete "
                "alternatives, tied to the trade-offs (risk, cost, reversibility, "
                "testability) — never a bare 'it is simpler'.",
                "risks": "Concrete failure modes of the CHOSEN solution and the "
                "worst-case / irreversible outcomes, each with how it is contained "
                "or detected — not generic project risks.",
                "test_strategy": "How the tester will PROVE the acceptance "
                "criteria: the kinds of tests and the scenarios they must "
                "construct, concrete enough to derive test cases from without "
                "re-reading the whole design.",
                "acceptance_criteria": "Verifiable given/when/then covering the "
                "happy path plus at least one failure / edge path; observable "
                "behavior only, no vague words like 精准/快. SCOPE-CLOSED: each "
                "criterion must be provable using ONLY what THIS design "
                "delivers plus the upstream it explicitly depends on. A "
                "well-formed given/when/then that exercises something this "
                "design's own non_goals exclude is NOT acceptable — it reads "
                "as testable and cannot be tested, so the task either invents "
                "the missing piece or quietly skips the criterion while QA "
                "still ticks it off. Check every criterion against non_goals "
                "before confirming: if it needs a page, dataset or capability "
                "you disowned, either rewrite it as a contract at YOUR "
                "boundary (component/API level, verifiable with the rest "
                "stubbed) or pull that piece into scope. This is checked here "
                "because it costs nothing here; catching it after the task is "
                "claimed costs the generation, the claim and a rework.",
                "impact_areas": "A JSON list drawn ONLY from this fixed enum "
                "(any other value is rejected): frontend, backend, database, api, "
                "auth, infrastructure, ci_cd, testing, security, performance, "
                "documentation. Pick the areas this design actually touches (e.g. a "
                "UI-foundation design = [\"frontend\"]).",
            },
            "required_for_confirm": [
                "problem_statement", "selected_solution", "selection_reason",
                "risks", "test_strategy", "acceptance_criteria",
            ],
            "depth_bar": {
                "trigger": "What starts it: who or what acts, in what state, and "
                "the preconditions that must hold.",
                "inputs_outputs": "What goes in and what comes back, in concrete "
                "terms — fields, formats, units, and what the caller or user "
                "actually sees afterwards.",
                "figures": "The limits and numbers this stands on (sizes, counts, "
                "thresholds, timeouts, precision, retention). Any the user has not "
                "settled is recorded 待确认, never silently omitted. Pointing at the "
                "constitution is not settling one; and if there genuinely are none "
                "of its own, say so outright rather than filling the space.",
                "failure_and_rejection": "What happens when it cannot comply: what "
                "is rejected, what is reported, what state is left behind, and "
                "whether it can be retried.",
                "boundaries": "The edge values and the cases just outside them, "
                "with the expected behaviour on each side stated explicitly.",
            },
            #: Rounds 1-2 draw and order the Feature map. Once FEATURE-* exist
            #: they are dead text — the method already says to skip them — so
            #: project-context stops shipping them (see
            #: _drop_map_rounds_once_mapped). Keep this in step with the rounds.
            "map_rounds": 2,
            "feature_map_confirmation_required": True,
            "write": (
                "Round 5: harnoq_design_case_update to fill fields, leaving the design in DRAFT status (not yet confirmed). "
                "Then, only after presenting the filled design and getting a fresh "
                "approval of the written text (not just the draft direction): "
                "harnoq_design_case_confirm. Confirm requires problem_statement, "
                "selected_solution, selection_reason, risks, test_strategy and "
                "acceptance_criteria to be non-empty; the service rejects confirm "
                "otherwise. If a locked design needs content changes, call "
                "harnoq_design_case_reopen first. status=succeeded means it "
                "was written."
            ),
        },
        "task_planning": {
            "when": "Creating a TASK-* directly under a confirmed DESIGN-*.",
            "orient_first": (
                "FIRST check what already exists: each design in project-context "
                "carries task_count / task_numbers. A DESIGN-* whose task_count "
                "> 0 is ALREADY decomposed — do NOT propose or create another task "
                "for it unless the user explicitly asks for a split; read its "
                "existing TASK-* and only adjust one (while still unclaimed) if the "
                "user asks. If every confirmed design already has a task, there is "
                "nothing to create — say so instead of re-listing a full plan. "
                "Then, for a design that still needs a task, read project-context "
                "and place this DESIGN-* in the whole project: the FEATURE-* it "
                "belongs to and that feature's dependencies, the sibling DESIGN-* "
                "around it, and whether "
                "an adjacent design already owns work this design would touch. Note the fixed stack and any Architecture Foundation "
                "/ UI Foundation it builds on. A TASK-* must fit the project, not "
                "restate this one design in isolation."
            ),
            "principles": [
                "Create only from a confirmed design: a task is created directly "
                "under the current confirmed DESIGN-*; if the design is not "
                "confirmed, finish design first instead of planning around an "
                "unsettled solution. This covers the proposal itself, not just the "
                "write — do NOT sketch a TASK-* for an unconfirmed "
                "design to be helpful; stop and return to design.",
                "COVERAGE BACK-CHECK before you create a task: you are already "
                "reading the whole design, so while you do, list every capability "
                "the selected_solution says THIS design OWNS and confirm each is "
                "pinned by at least one acceptance_criterion — one that would FAIL "
                "if that capability were missing. A deliverable named in the "
                "solution with no criterion guarding it is a hole: the task gets "
                "built, its qa walks the criteria, and the gap ships unseen because "
                "nothing tests for it. Do NOT paper over it by inventing a task goal "
                "for the uncovered piece — that just buries an unverifiable "
                "deliverable inside a task. Stop and send the DESIGN-* back to add "
                "the missing criterion (reopen -> edit -> confirm), THEN plan. This "
                "is the mirror of the design stage's scope-closed rule: there, "
                "criteria may not reach past what the design delivers; here, the "
                "design's deliverables may not reach past what the criteria verify.",
                "The freeze point is CLAIM, not creation: a task just created is "
                "stored 'claimable' and its content fields stay editable (or "
                "deletable) until someone claims it. Once claimed, its definition "
                "is locked — the coder must build against the text they claimed, "
                "not one that shifted under them mid-flight.",
                "A design-backed task is a PR-sized unit — check it against all SIX "
                "marks: (1) it binds exactly ONE DESIGN-* (its spec source), not "
                "spanning designs; (2) it is PR-sized — the smallest "
                "independently-mergeable change that one person / agent finishes "
                "in one go and passes the coder→qa two-gate loop (NOT 'implement "
                "the whole module', NOT 'change one line'); (3) SINGLE concern; "
                "(4) carries its own tests / acceptance; (5) exactly ONE "
                "task_type; (6) its product is a reviewable diff.",
                "DESIGN→TASK is strictly one-to-one: one DESIGN-* = ONE task; one confirmed DESIGN-* has one "
                "live design-backed TASK-*. The service and database enforce this. "
                "Never create a second task for the same DESIGN-* for context overflow, "
                "parallelism, size, or extra concerns. Additional work must be "
                "represented by an approved CR source and a source-backed TASK-* "
                "when it changes capability scope; Integration repair uses one "
                "TASK sourced by its INTEG-* findings, while System verification "
                "is recorded directly on SYS-* items.",
                "Splitting RELOCATES validation to regression — the dangerous part: "
                "cutting a design into build-stages reduces the context of BUILDING "
                "each piece but NOT the context of proving the pieces work TOGETHER; "
                "that proof just moves to regression / integration, whose context is "
                "the union of all pieces (the largest) and where the LLM is weakest. "
                "So an over-split trades cheap task-level acceptance for expensive, "
                "error-prone regression. If work is broader, make separate "
                "source-backed TASK-* records with explicit ownership and "
                "acceptance rather than splitting one DESIGN-* task.",
                "Never split by structure: do NOT cut a design into technical layers "
                "(model / service / view) or a process's sequential stages "
                "(normalize -> transform -> finalize) — that is layering in "
                "disguise and just serializes one design while shoving its "
                "integration onto regression. Cross-design parallelism already "
                "comes for free from what the Features wait for (independent "
                "designs are independent tasks); do NOT split WITHIN a design to "
                "chase parallelism.",
                "Testing and review live INSIDE every task, not as separate tasks: "
                "each DevelopmentTask is implemented, tested and reviewed by its own "
                "coder->qa agents (there is a qa_passed gate per task). Never "
                "create a standalone 'write tests' / 'add coverage' / 'write docs' "
                "task for a feature design — its test strategy belongs in each "
                "task's acceptance_criteria and test scope. Project-level "
                "Integration/System verification later uses direct stage items.)",
                "PRESENT the tasks in the order the Features wait: Architecture "
                "Foundation, then UI Foundation, then the vertical blocks — read "
                "what each waits for from project-context, do not guess the order "
                "by hand. That order is for the READER, not a gate on creation: "
                "creation is never blocked by an unfinished upstream, and refusing "
                "to create a task because its Feature is still waiting is WRONG. "
                "The dependency order is enforced at CLAIM, computed live from the "
                "Feature graph every time and impossible to bypass — a task whose "
                "upstream is unfinished is stored 'claimable' and still refuses to "
                "be claimed. 'claimable' is a STATE NAME, not a promise that it can "
                "be claimed right now. So creating the whole set up front is safe "
                "and is the right answer when the user asks for it: nothing can be "
                "worked on out of order, and every task stays editable and "
                "deletable until someone claims it. Restating a live machine gate "
                "as a rule here only ever subtracts — the gate cannot decay, this "
                "sentence can, and when it does it refuses something safe.",
                "Bootstrap folds into the first task only for an ordinary Feature "
                "module and is NOT a reason to split. Project-level Architecture "
                "Foundation is the explicit exception: its confirmed DESIGN-* owns "
                "an independent first task that builds the walking skeleton, "
                "package/adapter seams, config and test harness. Do not hide that "
                "project foundation work inside the first vertical Feature task; "
                "do not assume a framework is already standing, and never create a "
                "separate 'set up project' task for a normal module. A core / "
                "is_spike Feature's risk is addressed in the DESIGN stage through "
                "its spike depth, not by creating a separate walking-skeleton task "
                "here.",
                "A design-backed TASK-* is intentionally thin, not starved: the coder agent "
                "automatically receives the linked DESIGN-*'s full text "
                "(mined_requirements, selected_solution, design_scope, "
                "acceptance_criteria, "
                "test_strategy, impact_areas) PLUS the project constitution (fixed "
                "stack, boundaries, NFR, quality bar, UI conventions), joined for "
                "it. So a task must NEVER re-summarize or re-encode the design: "
                "compressing a well-defined design into one dense sentence only "
                "produces lossy, jargon-heavy noise that can silently DRIFT from "
                "the design it duplicates. In the DEFAULT one-DESIGN = one-task "
                "case there is nothing to split, so the task's description is a "
                "POINTER, not a spec: name the design and its one-line scope, and "
                "let acceptance/constraints/non_goals DEFER to the design instead "
                "of restating it. A source-backed task instead carries its own "
                "concrete goal, acceptance, constraints and non-goals for the "
                "explicit sources it groups. If you catch yourself compressing a "
                "design into a terse paragraph, point at the design.",
            ],
            "anti_patterns": [
                "One task per section/heading of the design (restating the design "
                "outline as tasks).",
                "Slicing one process's sequential stages into chained blocking "
                "tasks (normalize -> transform -> finalize as three tasks).",
                "A separate test or documentation task for a feature design — "
                "testing is per-task via the qa agent.",
                "Splitting by layer (model / service / view) instead of by "
                "standalone deliverable.",
                "Fragmenting a design that FITS one agent's context into build-stages "
                "(skeleton -> feature -> recovery) — even if each stage looks "
                "independent, it pushes 'do the pieces work together' onto regression.",
                "Creating a second TASK-* for one DESIGN-* because of parallelism, "
                "size, layers, or context — prohibited; use source-backed work for "
                "additional scope.",
                "A VAGUE FRAGMENT description that names neither a whole design nor "
                "a clear slice — different from a deliberate 1:1 pointer at the "
                "design, which is fine because the full design is joined; the "
                "anti-pattern is the ambiguous half-phrase, not the explicit "
                "whole-design reference.",
                "Re-compressing a 1:1 design into a dense coined-jargon paragraph "
                "instead of pointing at it — it adds no information over the joined "
                "design and can drift from it.",
            ],
            "granularity_check": (
                "Before creating a design-backed task, verify the DESIGN-* is "
                "confirmed and has no live design-backed TASK-*. If one exists, "
                "do not create another. For additional work, identify the explicit "
                "approved CR sources and create the minimum source-backed TASK batch. "
                "Record ordinary delivery verification on INTEG-*/SYS-* items."
            ),
            "rounds": [
                "Round 1 — orient, then propose: first read project-context and "
                "situate this DESIGN-* (its INPUT-*, dependencies, sibling "
                "designs/tasks, fixed stack). Verify the target DESIGN-* is "
                "currently confirmed. Then propose exactly one design-backed task, "
                "or a minimum batch of source-backed tasks for approved CR "
                "or approved CR sources. Never propose multiple tasks for one DESIGN-*. "
                "For source-backed work, the manager creates the minimum batch after "
                "its technical check; do not ask the user to review technical grouping. "
                "Design-backed task direction still follows the human decision gate.",
                "Round 2 — after the human agrees for design-backed work, or after "
                "the manager's technical check for source-backed work, create the task(s) to the "
                "task_field_standards form for the case at hand: for a 1:1 design "
                "a deliberate POINTER at the design is the CORRECT fill and is NOT "
                "an echo — do not pad it into a re-compression of the design; for "
                "a source-backed task must state the concrete sources it groups and "
                "its own acceptance; do not invent a design split.",
                "Round 3 — review: verify each created task has exactly one allowed "
                "source mode (one confirmed DESIGN-* or one/more approved CR "
                "sources), and that it stays editable (still 'claimable') "
                "if the user wants further changes before claiming. "
                "There is no TaskPlan entity or plan-confirmation action — the "
                "task itself is the plan.",
            ],
            "task_required_fields": [
                "title", "task_type", "goal", "acceptance_criteria", "constraints",
                "non_goals",
            ],
            "task_field_standards": {
                "task_type": (
                    "Each task needs a task_type. It is its OWN enum — NOT the "
                    "source_type enum (there is no 'new_feature' / "
                    "'implementation' task_type; passing those is rejected). Pick "
                    f"exactly one of: {_task_type_values}. Default to 'feature' for a "
                    "normal build task; use 'infrastructure' for the greenfield "
                    "skeleton/harness, 'bug_fix' / 'refactor' / 'test' / 'doc' when that "
                    "is literally the task, and 'mock_env' / 'mock_data' for scaffolding "
                    "a stand-in environment or fixture."
                ),
                "goal": "Design-backed: name the confirmed DESIGN-* and its one-line "
                "scope; do not re-compress its internals. Source-backed: state the "
                "concrete outcome for the selected INTEG-*, SYS-* or CR sources.",
                "acceptance_criteria": "Design-backed: defer to the confirmed "
                "design's full criteria, which are joined when the task is read. "
                "Source-backed: provide verifiable criteria for the selected sources. "
                "If the design scope grew, revise and re-confirm the DESIGN-* before "
                "creating its one task. Previous wording about splitting a design is "
                "obsolete. "
                "The criteria must be verifiable given/when/then and may not be "
                "invented to rescue an unverified design scope; revise and "
                "re-confirm that DESIGN-* first.",
                "constraints": "Design-backed: defer to the design and constitution. "
                "Source-backed: state constraints specific to the selected sources.",
                "non_goals": "Design-backed: defer to the design. Source-backed: "
                "state explicit exclusions so grouped sources have clear boundaries.",
            },
            "write": (
                "harnoq_task_create makes the task directly — no intermediate "
                "object and no separate generation step. A task created here does "
                "not carry an implicit dependency to another task; explicit task "
                "dependencies are used whenever source-backed work must be ordered. "
                "Order between "
                "DESIGN-*/Feature units lives at the Feature level "
                "(depends_on_feature_numbers), not here. If the design is not "
                "confirmed, the write is refused, and a DESIGN-* with an existing "
                "live TASK-* cannot receive another design-backed task; "
                "status=succeeded means it was written."
            ),
        },
        "change": {
            "when": (
                "The user wants to change something that already has work built on "
                "it — a confirmed DESIGN-*, or anything with a task under it. "
                "During delivery, use this method for contract/architecture changes "
                "or a confirmed design/contract change; "
                "ordinary Integration repairs stay in the active stage run and "
                "ordinary System repairs stay in the active stage run. "
                "NOT for editing a draft: a draft is edited directly, with no "
                "ceremony at all."
            ),
            "orient_first": (
                "Before proposing anything, ask what the change would actually "
                "touch: harnoq_change_request_impact is read-only and creates "
                "nothing. If the answer is 'nothing downstream', this is not a "
                "change request — it is an edit. Say so and just do it."
            ),
            "principles": [
                "Once something has SHIPPED, the default answer is to ADD, not to "
                "rewrite. A new requirement or change is captured as an approved "
                "CR; one or more CRs may be grouped into one source-backed TASK, "
                "but each CR belongs to at most one TASK. No new DESIGN is required "
                "for that execution. The delivered work stays as it is. "
                "Reopening completed work is the "
                "exception, and it is only honest when you can point at the "
                "specific design clause the delivery failed to meet — if you cannot, "
                "then what is wanted was never designed, and reopening would add "
                "code no design authorises.",
                "Classify approval by the actual impact, not by the existence of a CR. "
                "An ordinary Integration repair records each finding in an INTEG-* item "
                "and groups implementation in its active stage run; an ordinary System repair stays in its active "
                "stage run. Neither needs a CR. A confirmed design "
                "or contract change uses a CR and may be approved under existing "
                "authorization after analysis without another user confirmation. "
                "Product decisions remain the user's; "
                "never soften the impact or disguise new scope as a repair. Apply the "
                "approval_policy below before choosing whether to pause.",
                "Applying a change request records that the edit was made; it does "
                "not make it. Do the real edit through the normal update/confirm "
                "tools, then mark it applied.",
                "Governance and staleness are separate. After the edit, downstream "
                "objects read as suspect on their own — work through them one by "
                "one, and never realign an object to clear a flag without reading "
                "the change it is pointing at.",
            ],
            "approval_policy": {
                "autonomous_repair": (
                    "Existing authorization to implement, integrate, verify or repair "
                    "the product includes ordinary defect repairs needed to meet its "
                    "confirmed requirements and binding acceptance criteria. Record "
                    "System fixes and executable evidence on affected SYS-* items. "
                    "Integration fixes stay on the active run and affected INTEG-* items. "
                    "No CR is needed. "
                    "For a confirmed design or contract change, read the contract and "
                    "failure evidence, analyze all "
                    "affected targets and check scope, constraints and authorized cost. "
                    "Then the manager approves the CR, creates or reuses the minimum "
                    "source TASK batch, performs technical pre-review and continues "
                    "Build/QA without another user confirmation. Report "
                    "progress without turning the report into a permission question. "
                    "Multiple affected tasks or required regression runs alone are not "
                    "new approval boundaries. Respect any explicit user ask-first rule."
                ),
                "ask_user": (
                    "Ask only for a concrete decision not already authorized: new or "
                    "changed requirements, product trade-offs, lowering acceptance or "
                    "quality/security standards, waivers, changes to fixed constraints, "
                    "significant extra cost, destructive or irreversible actions, or "
                    "missing access/resources requiring user action. If contract intent "
                    "remains ambiguous after reading its sources, ask about that specific "
                    "ambiguity. Do not request a generic approval of the technical impact list."
                ),
                "evidence": (
                    "Record the violated contract clause, failure evidence, repair boundary "
                    "and existing authorization in the SYS/INTEG item evidence for a "
                    "bounded repair, or in the CR reason when a CR is required. Do not fabricate "
                    "a fresh user approval. A symptom alone does not establish its root "
                    "cause: continue reversible diagnosis within scope. Keep analysis, "
                    "QA, PR and apply/close records; approval is not evidence of repair."
                ),
            },
            "rounds": [
                "Round 1 — read the impact WITHOUT proposing anything, and tell the "
                "user plainly what the change would reach and what each piece would "
                "cost given how far it got. This is a progress report, not an automatic "
                "stop; classify the change using approval_policy.",
                "Round 2 — only for a confirmed design or contract change, "
                "harnoq_change_request_create with "
                "the request or defect evidence and authorization basis, then "
                "harnoq_change_request_analyze. Reuse an existing CR for the same "
                "finding. Ask only if the analysed impact crosses approval_policy.ask_user.",
                "Round 3 — approve an ordinary repair under existing authorization "
                "without another user confirmation; for a product decision, obtain "
                "the necessary user acceptance first. Then "
                "choose the minimum source-backed TASK batch and call "
                "harnoq_task_create_from_sources with "
                "source_kind='change_request' and the approved CR numbers. Execute "
                "the TASK Build/QA loop; only after the real edit is verified call "
                "harnoq_change_request_apply, then close it with a resolution "
                "that says how it ended.",
            ],
            "write": (
                "harnoq_change_request_create takes a reason plus ONE of "
                "design_number / target_type='new'. Use "
                "'new' for an increment — which is the normal answer for a change "
                "arriving after delivery."
            ),
        },
    }

    # Delivery verification is project-wide; ordinary repairs stay on stage items.
    delivery_fields = [
        "kind", "subject", "source", "driver", "status", "covered_by",
        "evidence_command", "evidence_output", "evidence_exit_code",
        "verified_revision", "repair_record", "blocked_reason", "waive_reason",
    ]
    repair_policy = (
        "Keep System repairs in the active project-wide stage run. Record the failure, "
        "change, exact executable evidence and current code revision on the affected item. "
        "Do not create a System TASK, dependency or CR merely because code changed. A confirmed "
        "design or contract hole uses the change workflow; continue unrelated checks. "
        "System code repairs rerun affected Integration checks within the same System run. "
        "If a required external resource, artifact or access is absent, mark affected work "
        "blocked and ask the user specifically what is needed, why, format/location and "
        "verification. Never invent, download, substitute or mock it. An explicit user "
        "waiver can resolve the obligation. If a confirmed design change invalidates the "
        "audit basis, abandon that run and restart Integration after capability work."
    )
    workspace_policy = (
        "Continue in the current project directory shared by manager, coder and qa. "
        "Reuse one stage branch across all item groups; preserve existing working changes."
    )
    common_principles = [
        "One project-wide run owns the stage; do not create stage Features, DESIGNs or TASKs.",
        workspace_policy,
        "Coverage requires exact executable evidence on the current code revision.",
        "An ordinary fix records failure, change and verification on its INTEG-*/SYS-* item.",
        "A missing required user resource blocks the affected item; ask for the concrete resource.",
        "Proceed with authorized technical work without another user confirmation.",
    ]
    common_rounds = [
        "Round 1 — page through the complete project index, reconcile and validate the full obligation inventory, and create or update direct INTEG-*/SYS-* rows.",
        "Round 2 — start one delivery-stage run and use one branch when code changes. Record each repair and its executable evidence on its item; continue unrelated work when one item is blocked.",
        "Round 3 — request a missing required user resource with purpose, format/location and verification; do not substitute it. A waiver requires an explicit user decision.",
        "Round 4 — close only with current evidence or waiver for every item and all inventory gates passed. One changed branch has one final merge reference; an evidence-only run needs no PR. System fixes rerun affected Integration checks in the same run.",
    ]
    methods["integration"] = {
        "when": "The delivery gate says integration_owed=true. Finish when the complete inventory and every direct INTEG-* obligation have current evidence or an explicit waiver, the run is closed and integration_owed=false.",
        "principles": [
            workspace_policy,
            "Scan the whole delivered product before repair: inspect real production entries, every declared part, every cross-design producer-to-consumer seam and source-level duplicate responsibility.",
            "Record each distinct missing edge, unwired or unverified seam, and justified convergence as a separate INTEG-* item; never collapse different defects into one launcher item.",
            "The manager owns the full scan, cross-design conclusions, A–G manifest and validator corrections. Delegate only bounded mechanical evidence slices with named inputs and output; independently reconcile them against the sources. Coder and qa handle stage-run repair and verification after the audit, not the project-wide inventory.",
            "The Integration run and direct INTEG-* items own repairs. Do not create a TASK, Feature or dependency for ordinary Integration fixes. One changed run has one branch and one final PR.",
            "INTEG-* is a finding/verification unit, not a one-at-a-time coding unit. Before dispatch, the manager groups all open findings by shared root cause, owner module and executable repair; one coder assignment handles the whole coherent group and names every included item number. Do not default to one coder call or one full QA cycle per item. Reconcile all groups into one working build, then run independent run-wide QA; targeted checks during coding are fine. Keep each assignment bounded for the coder's context. Each coder reads assigned items by business number with harnoq_integration_item_get; do not replace source records with a local summary.",
            "Coverage requires exact executable evidence on the current merged code revision for each item, including direct producer-to-consumer tests for seams.",
            "A missing required user resource blocks the affected item; ask for the concrete resource.",
            "Proceed with authorized technical work without another user confirmation.",
            "Production entry points must reach observable boundaries; declared seams need direct producer-to-consumer tests; convergence preserves caller behavior.",
            "Required external resources need real readiness and configured production-call evidence; a health check or test double does not prove use.",
        ],
        "round_1_workflow": {
            "rule": "Build one complete A–G manifest and submit it to harnoq_integration_inventory_validate. Correct valid=false findings before creating bound items. On reentry revise the saved full audit with current evidence. If delegated work produces no usable artifact, do the remaining work yourself. If a tool call is interrupted or its result is unknown, read back saved state before retrying; never infer success or end the turn merely because the helper or call failed. Continue until server validation succeeds or a concrete user resource, product decision, or persistent external outage actually blocks progress.",
            "steps": ['A. Existing records — list existing INTEG-* by business number, establish the next round, and '
 'rerun every covered item’s exact check. Put its current command, exit code and raw output in '
 'this inventory.',
 'B. Command Evidence — every evidence record has a unique key plus role, literal command, actual '
 'exit_code, expected_exit_codes, and INLINE raw stdout/stderr strings. Shape commands so relevant '
 'output is short; rerun with deterministic filters instead of truncating afterward. A '
 "description, excerpt, ellipsis, external output file, 'key lines', 'same as above', or "
 'cross-section reference is invalid. Include entry discovery, one exact-identifier scan per '
 'source language excluding generated/vendor code, and targeted skeleton/caller evidence. A '
 'nonzero result is usable only when its exit code was expected. External Resource Audit — submit '
 'external_resources with scan_evidence_keys pointing to production code/configuration scans, '
 'design_audits covering every capability DESIGN exactly once, and resources equal to the union of '
 'resource_keys. Each design has resource_keys or a no_resources_reason. Each resource records '
 'key, name, source, actual configuration and status. verified requires entry_keys, '
 'uses_test_double=false, operation, observed_result and separate successful '
 'readiness_evidence_key (role=resource-check) and call_evidence_key (role=resource-call), both '
 'with raw output. Use missing/invalid/unverified plus preparation when unavailable; a resource '
 'may be ready yet remain unverified. Use waived only with waiver_item_number identifying its live '
 'user-waived INTEG. A substitute resource or fake implementation cannot verify the declared '
 'production dependency.',
 'C. Assembly Matrix — first create declared_parts by copying EACH explicit part entry from '
 'selected_solution of every confirmed capability DESIGN-* (exclude integration/system). Do not '
 'infer parts from files/routes, split one declared entry, or combine separate entries; '
 'declaration repeats the source wording verbatim. A production entry OPERATES delivered behavior '
 'and reaches an observable boundary. Build/bundle/compile/package/codegen/test/dev/preview '
 'commands and a source file named main/start/run are discovery evidence, NEVER entries by '
 "themselves. Each production entry's operational invocation MUST equal its launch evidence "
 'command and separately cite observable output; a browser entry needs a verified served/deployed '
 'load path using a URL or browser-driver observation, so a bundle alone is not reachable. Add one '
 'design_coverage row for EVERY confirmed capability DESIGN-* using the current '
 'selected_solution/design_scope hashes and the part/seam keys extracted from it. Create one '
 'assembly row with the SAME key per declared part. reachable=true requires a verified entry plus '
 'every wired edge in its path, the declared part name in the path, and a passing executable test '
 'key for that part. A health check or homepage 200 alone cannot prove downstream parts. '
 'Definition/import/unit-test construction is false; zero verified '
 'entries means zero reachable parts. For reachable=false, name the exact missing producer -> '
 'consumer edge and one gap_key. Reuse the gap_key for every part blocked by that SAME edge; never '
 'merge different edges or split one edge because it affects several parts. After a shared earlier '
 'gap, continue hypothetically to expose later independent missing edges.',
 'D. Seam Graph — mechanically extract design_scope declarations from confirmed capability '
 'DESIGN-*. One row is exactly ONE producer endpoint -> ONE consumer endpoint; split fan-out and '
 'never merge by cause/fix/test. Record both fully qualified P*-DESIGN-* numbers, exact '
 'declaration, wiring evidence, the fully qualified declaring DESIGN-*, and an exact test '
 'node/command or null. A filename alone is not a test. Copy declaration verbatim from that '
 "DESIGN's design_scope and do not invent undeclared adjacencies. Set wired from production wiring "
 'evidence. Set integration_verified=true only when test_evidence_key names a passing test whose '
 'literal command equals test and directly drives this producer -> consumer; otherwise false.',
 'E. Exact-name Candidates — report only complete identifiers exactly equal in at least two '
 'modules. Shared prefix/suffix/base/category is not a hit. Create one identifier_collisions row '
 'per hit and preserve the concise raw per-language scan output from B. Add a source_scans row for '
 'every scanned source language/root and make its collision keys reconcile exactly.',
 'F. Responsibility Candidates — use targeted skeleton and caller evidence to reconcile every E '
 'hit plus the different-name/same-job pass into responsibility candidates (show that pass even '
 'when empty). Each candidate names at least two module-qualified implementations; an inseparable '
 'private helper/constant belongs to its owning responsibility, not a second candidate. Every E '
 'collision key maps to exactly one responsibility candidate; a different-name candidate maps to '
 'none. DEFAULT RETAIN. Converge only when ALL are proven: same contract, identical required '
 'caller semantics, one legitimate canonical owner, divergence would be a defect, boundary-safe '
 'ownership, and a baseline test evidence record. Contract, caller, owner and test evidence have '
 'distinct roles; the same generic scan cannot prove all four. Same name/body/fields, trivial '
 'helpers, local policy constants or look-alike DTOs alone are insufficient. Investigate is '
 'unfinished.',
 'G. Proposed INTEG payloads — create or reconcile one item per demonstrated assembly gap, '
 'unverified seam and justified convergence. Each row has standalone kind, subject, source and '
 'round. Distinct missing edges and distinct producer-to-consumer seams are distinct items; '
 'do not use one general launcher item to stand for unrelated findings.',
 'H. Server audit — submit the complete A–G manifest to harnoq_integration_inventory_validate. On '
 'reentry revise the saved full audit with current evidence. Correct valid=false errors and create '
 'only bound INTEG-* items. A valid audit permits stage execution but does not close the '
 'run. After ALL findings are recorded, start the project-wide Integration run. '
 'Repair open items directly within that run; no TASK or dependency is created.'],
            "reply_template": ["A. Existing Records", "B. Command Evidence", "C. Assembly Matrix", "D. Seam Graph", "E. Exact-name Candidates", "F. Responsibility Candidates", "G. Proposed INTEG Payloads", "H. Server Audit"],
            "user_handoff": "The A–H manifest is internal; do not paste it to the user by default. Report a short status and count unless asked for the full audit. A progress report is not a handoff or stop condition: resume the remaining inventory and validation work in the same turn when possible.",
            "server_validated_invariants": ['declared_parts keys = assembly_rows keys',
 'design_coverage keys = every confirmed capability DESIGN-*',
 'source scan collision/different-name keys = responsibility-candidate inputs',
 'assembly items = unique missing-edge gap keys covering all unreachable parts',
 'seam items = seam keys that are unwired or not directly integration-verified',
 'duplicate items = responsibility candidates ending converge',
 'identifier collision keys are an exact partition across responsibility candidates',
 'proposed item keys are the exact union above, with no key reused',
 'new item count = assembly gaps + seam gaps + converge candidates',
 'every new payload is standalone and every existing reconciliation is exact'],
            "decision_boundary": "Complete technical analysis and direct item repair without routine approval requests. Ask for a missing required resource, product decision, waiver, significant extra cost or destructive action.",
        },
        "repair_policy": (
            "Finish the whole scan before repair. The manager groups related INTEG-* "
            "findings and assigns bounded coder work directly under the active Integration run. "
            "Independent qa verifies the assembled result before the one branch and PR merge, "
            "then reruns on the merged revision for the final report; "
            "record each repair and its direct executable check on its INTEG-* item. "
            "Record the independent assembled-run QA in run.evidence as a JSON object (not a JSON-encoded string) with "
            "role=qa, item_numbers (every covered INTEG-* exactly once), command, "
            "raw output, exit_code=0 and the current code_revision. Do not "
            "substitute a green unit suite or healthy root endpoint for producer-to-consumer "
            "proof. Rerun each item on the merged revision. Design or contract changes still use "
            "the change workflow. Ask the user for a required missing resource or product decision."
        ),
        "writable_fields": delivery_fields,
        "field_standards": {
            "evidence": "Each item needs its own exact command, raw result and current revision. A changed run also needs run.evidence object: role=qa, all covered item_numbers, command, raw output, exit_code=0, code_revision=current revision.",
            "repair_record": "State the failure, change and verification briefly.",
            "inventory_revision": "Update repaired assembly_rows and seams together with proposed_items in one inventory revision. proposed_items maps only remaining current gaps; repaired historical INTEG rows stay in the database and need current test evidence matching their evidence_command. Remove obsolete proposals, not the historical items. covered_by is a human reference, not an executable command.",
            "blocked_reason": "State the missing user resource/access and why and where it is needed.",
        },
        "rounds": [
            "Round 1 — scan production entry, all declared parts, all declared cross-design seams, duplicates and resources; validate the complete inventory and create one INTEG-* item for each distinct finding.",
            "Round 2 — start one Integration run. Group open findings by common repair, give coder bounded item-number groups, integrate all code, run independent run-wide QA, and merge one PR on one branch. Record repairs directly on their INTEG-* items; create no TASK.",
            "Round 3 — rerun QA, every affected entry and producer-to-consumer seam on the merged revision; update each INTEG-* with its own command, raw result and repair record and put the run-wide QA JSON in run.evidence; revisit the complete inventory.",
            "Round 4 — close the run only after all items are covered or explicitly waived, the current full inventory audit passes, and any changed branch has a merge reference. An evidence-only zero-finding run needs no PR.",
        ],
        "write": "Use harnoq_integration_item_create/update and delivery-stage-run start/update/close. Ordinary Integration repairs create no TASK; only a confirmed design or contract hole uses the change workflow.",
    }
    methods["system"] = {
        "when": "The delivery gate says system_owed=true. Finish when every journey and committed NFR has current executable evidence or an explicit waiver, the run is closed and system_owed=false.",
        "perspective": "Exercise complete user journeys and committed quality targets in the real supported environment through actual product entry points.",
        "principles": common_principles + [
            "Enumerate every confirmed INPUT-* functional journey and every constitution nfr_baseline row marked strength=commitment. Reconcile existing SYS-* rows rather than duplicating them.",
            "Choose and record the executable driver per scenario. Assert observable user outcomes and committed thresholds; skipped tests, readiness checks and swallowed errors do not pass.",
            "Use real configured resources for success paths. Fault simulation is valid only for a fault scenario and must be disclosed.",
        ],
        "repair_policy": repair_policy,
        "writable_fields": delivery_fields,
        "field_standards": {
            "driver": "Choose a repeatable driver that exercises the real entry point and asserts the outcome.",
            "evidence": "Exact executable command, exit code, raw output, environment/resource identity and current code revision.",
            "repair_record": "State the failure, change and verification briefly.",
            "blocked_reason": "State the missing user resource/access and why and where it is needed.",
        },
        "rounds": common_rounds,
        "write": "Use harnoq_system_item_create/update and delivery-stage-run start/update/close. Validate the complete scenario inventory before creating bound items; only a confirmed design or contract hole uses the change workflow.",
    }
    for method in methods.values():
        method["language_policy"] = (
            "Use the user's requested language for explanations and new project prose. "
            "Preserve source quotations, glossary terms, identifiers and enum values. "
            "Chinese labels in this method have these English equivalents: "
            "用户说的 = User-stated; 我补的 = Agent-supplied; "
            "用户确认 = User-confirmed; 我补的(已认可) = Agent-supplied (accepted); "
            "待确认 = Pending confirmation; 冲突检查 = Conflict check; "
            "材料 = Material; 来源 = Source; 强迫出的结构 = Required structure; "
            "我打算这么做 = Proposed approach; 需求材料 = Requirement material. "
            "Translate presentation labels only; keep the same evidence and approval rules."
        )
    return methods


def _write_policy_guidance():
    return {
        "create_vs_update": [
            "Use create tools only when adding a new object.",
            "Use update tools when revising an existing object identified by INPUT-*, FEATURE-*, DESIGN-*, or TASK-*.",
            "Use append_content when adding text to an existing Source Requirement raw_content.",
        ],
        "execution_truth": [
            "Use the returned envelope status to decide whether a write succeeded.",
            "If a write tool returns an error, report the error and the missing or invalid input.",
            "Do not narrate a tool call in prose (e.g. 'calling harnoq_..., creating...'). "
            "Either actually invoke the tool now, or do not claim anything happened.",
            "Never say an object was created/updated/appended until the write tool has "
            "actually returned status=succeeded. Describing a call is not making it.",
            "If you cannot invoke the tool, say so plainly ('I was unable to call the "
            "tool') instead of pretending the write was done.",
            "After a write returns succeeded, read the object back (e.g. via "
            "project-context) and confirm it exists before telling the user it is done.",
        ],
        "deletion": {
            "delete_tools_exposed": True,
            "policy": (
                "delete/restore are exposed for design_case "
                "(harnoq_design_case_delete / harnoq_design_case_restore). '不做了' (dropping something) is "
                "deletion, not a status — there is no cancelled/rejected state to move an "
                "object to instead. Deletion is soft: the object goes to that entity's "
                "recycle bin and can be restored; it is not immediately unrecoverable."
            ),
            "user_confirmation_required": True,
        },
        "approval_rules": [
            "For requirement mining, reflect your deepened understanding of the INPUT-* "
            "back to the user and let them confirm before you rewrite its raw_content.",
            "For destructive or irreversible workflow decisions, require clear user intent before writing.",
            "Always get explicit user confirmation before calling a delete tool, even "
            "though deletion is soft/recoverable — never delete on an inferred or assumed intent.",
        ],
    }
