# Harnoq connections and deployment

[中文](../../cn/skills/README.md) · [English](README.md)

Operator/developer documentation for wiring Harnoq into an LLM client.

Harnoq exposes its workflow through **one shared contract** — the HTTP/OpenAPI
skill API (`app/api/skills.py` + `skills/openapi.yaml`). Two integration paths
fan out from it; pick whichever your client speaks:

```
                    ┌─→ OpenWebUI            (OpenAPI tool server)  → imports skills/openapi.yaml
Harnoq HTTP/OpenAPI ┤
                    └─→ Claude / Codex / …   (MCP client)          → harnoq_mcp MCP server
```

OpenWebUI consumes the OpenAPI directly. Claude Code, Claude Desktop and Codex
speak MCP and use the `harnoq_mcp` server (a thin wrapper over the *same*
endpoints — see [MCP adapter](../mcp/README.md)). Don't bridge MCP back into OpenWebUI:
it already eats the OpenAPI natively, so an MCP→OpenAPI proxy would just be a
round-trip.

The single source of truth for the *working method* is
`app/api/stage_methods.py::_stage_methods()`, delivered at runtime as
`active_stage_method` (hoisted to the top of every `harnoq_project_context`
response) — no client restates it. MCP clients (Claude Code, Claude Desktop,
Codex) also receive a thin pointer, `HARNOQ_INSTRUCTIONS`, from the `harnoq_mcp`
server automatically, so they need **no pasted system prompt**. Only OpenWebUI
(a plain OpenAPI tool server with no server-level instructions) needs
`skills/openwebui-system-prompt.en.md` pasted into its system prompt — and that
file is itself a thin pointer now: identity, business ids
(INPUT-* / FEATURE-* / DESIGN-* / TASK-*), lifecycle and tool discipline, plus
"always read and obey active_stage_method". It does not restate the method.

## 0. Prerequisite — run Harnoq (one process, one port)

Every path reads the same running instance. **One command, one port (5000)**:

```bash
uv run python main.py                       # web + MCP, binds 0.0.0.0:5000
```

This runs a single ASGI process (uvicorn) that serves both:

- The **web** app at `/` — the OpenAPI skills (OpenWebUI) and the REST API.
- The **MCP** endpoint at `/mcp` — a long-lived streamable-http endpoint at
  `http://<host>:5000/mcp`. MCP clients (Claude Code / Desktop / Codex) connect
  to it over a URL — no per-client Harnoq checkout or local spawn.

Configure with `HARNOQ_MCP_URL` to override the derived MCP URL (e.g. behind a
reverse proxy). To run the MCP server as its own standalone process/port
instead (not needed for normal use), see `harnoq_mcp/__main__.py`
(`uv run --group mcp python -m harnoq_mcp`, configurable via `HARNOQ_MCP_HOST`
/ `HARNOQ_MCP_PORT`, forwarding to `HARNOQ_BASE_URL`).

Harnoq requires a registered user and JWT. Bootstrap an administrator with
`uv run flask --app main create-admin`, log into the web UI, and create a
personal MCP/API credential in **My account**. Set it in the client process's
`HARNOQ_API_TOKEN` environment variable. Credentials are revocable, expire,
and stop working when their user is disabled or their password changes.
The server attributes operations to that authenticated person; caller-supplied
`actor` and task `assignee` text cannot impersonate another user.

## 1. OpenWebUI (and other OpenAPI tool-server platforms)

`openapi.yaml` describes the skill endpoints (full behavior in [the API reference](../api.md)
under "OpenWebUI Skills"). It is plain OpenAPI 3.0.3 for broad importer
compatibility — any platform that imports an OpenAPI tool server (OpenWebUI,
Chatbox, …) can use this one spec.

1. Run Harnoq (section 0).
2. **Settings → Tools → Add Tool Server**, choose "OpenAPI", and point it at
   `http://<harnoq-lan-ip>:5000/openapi.json` (or `/openapi.yaml`). The app
   rewrites `servers.url` in the served spec to the request origin, so it
   imports the same host you used. Don't use `127.0.0.1` unless OpenWebUI runs
   on the same machine outside containers. If you split "base URL" and "spec
   path", don't leave a trailing slash on the base URL (some builds otherwise
   request `//openapi.json`).
3. Enable the tool for the model/chat. Each `operationId` becomes a callable
   tool — the generic multi-action tools (`harnoq_source_requirement`, …) plus
   explicit ones (`harnoq_source_requirement_append_content`,
   `harnoq_design_case_update`, `harnoq_task_create`, …) so the
   model needn't infer `action=…`.
   Configure the tool server's Bearer authentication with your personal JWT.
4. Paste `skills/openwebui-system-prompt.en.md` into the model/chat system prompt.

Read-only calls (`project-context`, `task-progress`) can omit `actor`/
`session_id`; Harnoq records conservative fallback trace values. Write calls
accept `actor`, `session_id` and `idempotency_key` too, but only for the audit
trail — they are optional and NOT deduped (writes always re-run; see
`skill_service.run_write_skill`). The explicit write tools fill conservative
defaults so callers can omit them regardless.

## 2. MCP clients — Claude Code, Claude Desktop, Codex

Each MCP client registers the `harnoq_mcp` server URL and personal JWT. With
the remote streamable-http endpoint (§0), the configuration differs
only slightly per client below.

All clients point at the **same MCP endpoint** — `http://<host>:5000/mcp`
(§0), served by the same process as the web app, not a per-client spawn. No
Harnoq checkout on the client machine. `HARNOQ_INSTRUCTIONS` is baked when
that process starts, so after editing `harnoq_mcp/server.py` **restart
`main.py`** to pick it up.

### Claude Code

`.mcp.json` (project) or `~/.claude.json` (user), or `claude mcp add`:
```json
{
  "mcpServers": {
    "harnoq": { "type": "http", "url": "http://<host>:5000/mcp", "headers": { "Authorization": "Bearer ${HARNOQ_API_TOKEN}" } }
  }
}
```
```bash
claude mcp add --transport http harnoq http://<host>:5000/mcp
```
`type: "http"` is required (an entry with a `url` but no `type` is read as a
stdio server and skipped); `streamable-http` is accepted as an alias. First load
prompts you to trust the server.

### Claude Desktop

Edit `claude_desktop_config.json` (macOS `~/Library/Application Support/Claude/`,
Windows `%APPDATA%\Claude\`), then restart:
```json
{
  "mcpServers": {
    "harnoq": { "type": "http", "url": "http://<host>:5000/mcp", "headers": { "Authorization": "Bearer <PERSONAL_JWT>" } }
  }
}
```

### Codex

Codex connects to the endpoint by URL (codex-cli ≥ 0.144) — no checkout:
```bash
codex mcp add harnoq --url http://<host>:5000/mcp
```
or in `~/.codex/config.toml`:
```toml
[mcp_servers.harnoq]
url = "http://<host>:5000/mcp"
bearer_token_env_var = "HARNOQ_API_TOKEN"
```
`main.py` (§0) must be running.

The environment-token settings follow the official
[Claude Code MCP](https://code.claude.com/docs/en/mcp),
[OpenCode MCP](https://opencode.ai/docs/mcp-servers/), and
[Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference)
documentation. Generated OpenCode configs disable OAuth and use
`Authorization: Bearer {env:HARNOQ_API_TOKEN}`. Actual tokens belong in local
client secrets or environment settings, not committed configuration files.

### Instructions (all MCP clients)

None required — the server ships `HARNOQ_INSTRUCTIONS` and every
`harnoq_project_context` call returns `active_stage_method` (the binding method)
plus `business_id_rules` / `workflow_guidance`. So **you do not need to paste
`skills/openwebui-system-prompt.en.md`** anywhere (pasting the thin prompt is
harmless but redundant).

## 3. Initialize a downstream engineering repository

After a project constitution is confirmed, install its coding-agent files into
an engineering repository (this does not create a repository, commit, push, or
touch git). Most developers just **download the zip from the project page in the
Harnoq web UI and unzip it at their repo root** — the generated config points at
the remote MCP URL, so there is nothing machine-local to fill in.

The command-line equivalent, run from the target repository directory:

The init and refresh scripts also read `HARNOQ_API_TOKEN` for authenticated
downloads. Set the variable before running them.

```bash
uv run python <ABS_PATH_TO_HARNOQ>/scripts/harnoq-init.py \
  --project P1 \
  --harnoq-url http://127.0.0.1:5000 \
  --flavor opencode
```

Use `--flavor claude` for Claude Code. The script resolves P*, downloads
the generated zip, and extracts it into the current directory (with a
path-traversal guard). No `--harnoq-path` — the MCP endpoint is a URL, filled in
by the server. It never performs a git operation.

## Notes

- Write actions (`create`/`update`/…) accept `actor`, `session_id` and
  `idempotency_key` for the audit trail only — all three are optional and
  writes are NOT deduped by them (every call re-runs). The explicit write
  tools / MCP wrapper fill conservative defaults so callers can omit them.
  Reads can omit them too.
- Source-requirement writes accept `source_requirement_number` as an alias and
  `description` as an alias of `raw_content`; create falls back `source_type` to
  `manual_note` when omitted. INPUT-* is the raw-material requirement layer;
  there is no separate structured requirement layer between FEATURE-* and
  DESIGN-* — requirement mining is part of the DESIGN stage method.
- Prefer `harnoq_source_requirement_append_content` for adding text to an
  existing `INPUT-*` instead of rewriting the whole document.
- Write tools report success through the envelope `status: "succeeded"`. Only
  then is the write durable.
- Development tasks are created directly from a confirmed design via
  `harnoq_task_create` (skill `design-case-create-task`); there is no
  intermediate draft and no separate human-only generation gate (see [design rationale: objects](../design-evolution.md#objects)).
  The AI may create tasks itself. Task delete tools are not exposed through the
  skill API.
