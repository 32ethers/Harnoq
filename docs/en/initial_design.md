# Initial design

[中文](../cn/initial_design.md) · [English](initial_design.md)

> This document records the original goals and product direction. An early idea does not automatically become a current implementation requirement. Superseded mechanisms and outstanding goals are distinguished below.

## Goals

Build an engineering system spanning design, implementation, quality, and documentation, with enough constraints to help AI agents produce reliable code. Enterprise development quality is the primary concern.

Support teams working on larger projects. Accounts, JWT authentication, ownership, assignment, workload views, and activity attribution exist today. Project membership, project isolation, and finer-grained authorization remain future work.

Accommodate models with different capabilities. Role model configuration and behavioral evaluation scaffolding exist; cross-model and cross-domain reliability, cost, and context usage still require validation.

The intended chain is **requirements intent → engineering constraints → AI implementation → quality verification → retained organizational knowledge**.

## Strengths and limitations of AI agents

The initial observations were that agents generate code quickly, handle local implementation details well, investigate failures rapidly, and work effectively when a clearly stated task leaves room for independent implementation. These are motivations, not guarantees about every model.

Several weaknesses matter in engineering:

1. Agents may miss implicit requirements. An apparently sophisticated local solution can be inefficient overall if the actual required precision or scope was never made explicit.
2. Improvising on an isolated task is easier than respecting many simultaneous constraints.
3. A growing project cannot depend on one agent understanding everything. Division of work and engineering process remain necessary.
4. Design quality can decline above the class level when assumed requirements diverge from real needs.
5. Terms can be ambiguous across domains, such as “key” in databases and cryptography.
6. Terminology can drift across repeated discussion or translation, eventually changing the original concept.
7. Agents may struggle to identify questions that nobody has already raised.

Long contexts spread attention across too much material. Even a detailed design does not guarantee that all constraints will survive implementation. Smaller task contexts and explicit verification therefore underpin the design.

## Overall design

The current workflow is:

```text
INPUT → constitution → FEATURE → DESIGN → TASK
      → implementation and QA → PR and merge record → Integration → System
```

Planning and execution are connected but distinct. Design-backed tasks follow a strict one-design-to-one-task relationship. Source-backed tasks may reference one or more approved change requests without a new design. There is no direct INPUT–TASK many-to-many relation: designs cite requirement material, while tasks trace their contract through designs or change requests.

### System shape

A Web application exposes management pages and APIs for agents. Clients supporting OpenAPI tools or MCP can connect; a client does not need a feature literally called “skills”. OpenWebUI imports OpenAPI, MCP clients connect to the MCP endpoint, and `project_context` supplies the current stage method dynamically.

Unified cross-requirement, design, and change search remains outstanding; current access primarily uses lists and business numbers.

### Planning

Planning covers requirement collection, design, and executable task definition. Record solutions and, especially, why they were chosen. Feature dependencies and explicit task dependencies express execution prerequisites. Acceptance criteria must be designed alongside the work.

Requirements, design rationale, rejected alternatives, and version history belong in Harnoq. Downstream repositories do not need a duplicate documentation tree or mandatory function descriptions; add useful comments next to code when needed.

RAG may eventually retrieve background knowledge, but is not implemented and must not replace structured relationships. Begin with unified full-text search before deciding whether RAG adds sufficient value.

### Development

Development stays centered on human ownership. A developer claims a task, reviews applicable assumptions, and works on a task branch. The manager coordinates implementation by coder and independent testing/review by QA. Failed verification returns for repair.

The early idea that Harnoq would review or merge PRs itself was dropped. QA performs review, the manager judges evidence, and Harnoq checks workflow gates. A human confirms the merge on the hosting platform; the agent records that fact. Harnoq neither generates CI/CD configurations nor maintains hosting-provider clients.

Developers may use their own editors and processes. Generated OpenCode and Claude configurations provide a shared workflow:

- What to implement, why it is needed, and how it should be verified.
- Where to retrieve project context.
- Manager, coder, and QA responsibilities.
- Research, implementation, QA, and PR steps on the correct branch.
- The tools each role needs.

Review the task before developing. Return invalid contracts for revision, and intervene if execution departs from the intended scope.

### Integration and system verification

After capability work is complete, create INTEG/SYS inventories and record execution evidence in delivery runs. Ordinary verification does not require a new DESIGN or TASK per item.

Reuse existing tests and environments where they prove the obligation. Add runnable checks for actual gaps, recording commands, output, exit codes, and code revisions. Prepare data or mock environments only where appropriate; distinguish real-resource verification from test-double behavior.

Integration verifies assembly, cross-design seams, and duplicate responsibilities. System then verifies user journeys and non-functional requirements. Ordinary repairs stay within the current run; contract or design changes follow change governance. Web projects may use browser automation where appropriate.

### New requirements and defects after delivery

New scope or changes to delivered behavior go through CR analysis and approval, then create or join source-backed tasks followed by development, QA, and PR delivery. Reopen completed tasks only when their original contract was not actually satisfied. Ordinary Integration/System repairs that preserve the agreed contract remain in the delivery run.

### An alternative considered, but not adopted

A fully unattended scheduler could manage a pool of models, split work into implementation/review/test steps, allocate ready steps by dependency and model availability, and use watchdogs to detect loops. This was an idea, not a removed implementation.

Such automation may suit unattended personal work, but reduces opportunities to catch invalid tasks or product decisions. Harnoq prioritizes controllable commercial engineering and human intervention over autonomous scheduling.

### Code hosting

Teams can use hosted or self-hosted Git platforms, including Gitea. Agents operate repository tools such as `git`, `gh`, or `tea`; Harnoq records repository URLs, PRs, and reported merges. It does not implement GitHub/Gitea API clients or poll CI/PR status.

## Future extension: RAG

Background retrieval may reduce search context. RAG remains unimplemented. First establish unified full-text search, then evaluate the appropriate scope based on retrieval results.
