# Harnoq MCP server (Claude-side adapter)

[中文](../../cn/mcp/README.md) · [English](README.md)

Exposes Harnoq's skill endpoints as native MCP tools for Claude Code / Claude
Desktop / any MCP client. It is a **thin wrapper over the same HTTP API**
(`app/api/skills.py` / `skills/openapi.yaml`) that OpenWebUI consumes directly —
no new business logic. The shared contract is the REST/OpenAPI layer; this is
just the MCP face of it.

HTTP MCP requests require `Authorization: Bearer <personal JWT>`, including
initialization and tool discovery. Create the credential on Harnoq's account
page. Each tool forwards the incoming request's credential to the API, in both
co-hosted and standalone modes; a server-wide credential never substitutes for
an HTTP caller. Environment credentials are used only for calls without an
HTTP request context. Client configuration is documented in [the connection guide](../skills/README.md).

```
                    ┌─→ OpenWebUI            (imports skills/openapi.yaml directly)
Harnoq HTTP/OpenAPI ┤
                    └─→ Claude / MCP client  (this package)
```

## Run

1. Start Harnoq so the HTTP API is reachable:
   ```
   uv run python main.py        # binds 0.0.0.0:5000
   ```
2. Run the server standalone:
   ```
   uv run --group mcp python -m harnoq_mcp
   ```

The `mcp` SDK is a core dependency because the default server co-hosts MCP.
The `mcp` group remains available for the standalone command.

**Per-client setup (Claude Code, Claude Desktop, Codex) and the OpenWebUI path
live in [`../skills/README.md`](../skills/README.md)** — one place so the configs
don't drift. From outside this repo the launch command needs `--directory
<ABS_PATH_TO_HARNOQ>`; see that doc.

## Config (env)

- `HARNOQ_BASE_URL` — Harnoq HTTP base (default `http://127.0.0.1:5000`).
- `HARNOQ_ACTOR` / `HARNOQ_SESSION_ID` — trace identity recorded on writes
  (defaults `claude-code` / `claude-code-mcp`).
- `HARNOQ_TIMEOUT` — per-call timeout seconds (default `30`).

## Tools

Read: `harnoq_project_context`, `harnoq_task_progress`.
Write: source-requirement create / append-content / update / confirm / reopen;
design-case create /
update / confirm / reopen; task create
(`harnoq_task_create`, directly under a confirmed design-case — no
intermediate object, no separate generation step).
SourceRequirement (INPUT-*) is the free-text requirement source; there is no
structured requirement layer between a Feature and its DesignCase-* (that
layer, RequirementItem/REQ-*, has been removed — requirement mining
is now part of the DESIGN stage method). INPUT-*
shares the 3-state lifecycle (draft/needs_clarification/confirmed): confirm is a
bare status flip with no required-field checklist, and every INPUT-* in a project
must be confirmed before the constitution stage can begin. Neither Feature nor
DesignCase carries a formal INPUT-* link (INPUT-* is a holistic document, not
an atomic item) — DesignCase attaches only to a Feature (`feature_id`).
Feature dependencies (`depends_on_feature_ids`) express feature ordering.
Explicit task dependencies add task-level prerequisites; both are checked at claim time.
INPUT-* carries no dependency graph.

Each tool POSTs a flat JSON body to one skill endpoint and returns its envelope.
Writes succeed only when the envelope reports `status: "succeeded"`.
