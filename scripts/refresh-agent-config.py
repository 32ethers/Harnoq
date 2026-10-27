#!/usr/bin/env python3
"""Refresh a downstream repo's generated agent config in place.

The prompts live in Harnoq but reach the agents as a downloaded snapshot, so
every template change makes every checkout stale — and a plain re-download also
clobbers the per-agent `model` values a developer picked for their own account,
which is the part they will not notice until a cheap subagent starts running on
an expensive model.

This overwrites the generated prose (AGENTS.md / CLAUDE.md / the agent
definitions) wholesale — those are Harnoq's to own — while merging
opencode.json so local model choices survive.

    uv run scripts/refresh-agent-config.py <repo-path> --project P1
        [--flavor opencode] [--harnoq http://localhost:5000]
"""
import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

#: Keys in the generated opencode.json that belong to the local checkout, not
#: to Harnoq. Model choice is per-developer (each account has different
#: providers configured), so a refresh must not reset it.
_LOCAL_KEYS = ("model",)


def _authenticated_request(url):
    token = os.environ.get("HARNOQ_API_TOKEN", "")
    return urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}) if token else url


def _get_projects(harnoq):
    url = f"{harnoq}/projects"
    try:
        with urllib.request.urlopen(_authenticated_request(url), timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"Harnoq returned HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"cannot reach Harnoq at {url}: {exc.reason}") from None


def _project_id(harnoq, project_number):
    for project in _get_projects(harnoq):
        if project.get("project_number") == project_number:
            return project["id"]
    raise RuntimeError(f"project {project_number} not found")


def _fetch(harnoq, project_id, flavor, model):
    query = urllib.parse.urlencode({"flavor": flavor, "model": model})
    url = f"{harnoq}/projects/{project_id}/agent-template?{query}"
    with urllib.request.urlopen(_authenticated_request(url), timeout=30) as response:
        return zipfile.ZipFile(io.BytesIO(response.read()))


def _merge_opencode(old_text, new_text):
    """New permissions and MCP wiring, old model choices."""
    new = json.loads(new_text)
    try:
        old = json.loads(old_text)
    except (ValueError, TypeError):
        return json.dumps(new, ensure_ascii=False, indent=2) + "\n"

    for key in _LOCAL_KEYS:
        if key in old:
            new[key] = old[key]
    for name, agent in new.get("agent", {}).items():
        previous = old.get("agent", {}).get(name, {})
        for key in _LOCAL_KEYS:
            if key in previous:
                agent[key] = previous[key]
    return json.dumps(new, ensure_ascii=False, indent=2) + "\n"


def _merge_mcp(old_text, new_text):
    old, new = json.loads(old_text), json.loads(new_text)
    if not isinstance(old, dict) or not isinstance(old.get("mcpServers", {}), dict):
        raise RuntimeError("existing .mcp.json must contain an MCP server mapping")
    old["mcpServers"] = old.get("mcpServers", {}) | {"harnoq": new["mcpServers"]["harnoq"]}
    return json.dumps(old, ensure_ascii=False, indent=2) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path)
    parser.add_argument(
        "--project",
        required=True,
        help="Harnoq project business number, e.g. P1 (not the database ID)",
    )
    parser.add_argument("--flavor", default="opencode")
    parser.add_argument("--harnoq", default="http://localhost:5000")
    parser.add_argument(
        "--model",
        default="",
        help="fallback top-level model, used only when the repo has no config yet",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="only compare the local template fingerprint; write nothing",
    )
    args = parser.parse_args()

    if not args.repo.is_dir():
        sys.exit(f"not a directory: {args.repo}")

    harnoq = args.harnoq.rstrip("/")
    project_id = _project_id(harnoq, args.project)
    archive = _fetch(harnoq, project_id, args.flavor, args.model)
    manifest_name = ".harnoq-agent-template.json"
    incoming_manifest = json.loads(archive.read(manifest_name).decode("utf-8"))
    local_manifest_path = args.repo / manifest_name
    try:
        local_manifest = json.loads(local_manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, TypeError):
        local_manifest = None
    incoming_fingerprint = incoming_manifest["prompt_fingerprint"]
    local_fingerprint = (
        local_manifest.get("prompt_fingerprint") if local_manifest else None
    )
    if local_fingerprint == incoming_fingerprint:
        print(f"agent template is current ({incoming_fingerprint})")
        if args.check:
            return
    else:
        print(
            "agent template is stale: "
            f"{local_fingerprint or 'missing'} -> {incoming_fingerprint}"
        )
        if args.check:
            raise SystemExit(1)

    config_name = ".opencode/opencode.json"
    for name in archive.namelist():
        target = args.repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        incoming = archive.read(name).decode("utf-8")
        if name == ".mcp.json" and target.exists():
            target.write_text(_merge_mcp(target.read_text(encoding="utf-8"), incoming), encoding="utf-8")
            print(f"  merged  {name} (kept other MCP servers)")
        elif name == config_name and target.exists():
            kept = _merge_opencode(target.read_text(encoding="utf-8"), incoming)
            target.write_text(kept, encoding="utf-8")
            models = json.loads(kept).get("agent", {})
            print(f"  merged  {name} (kept models: " + ", ".join(
                f"{role}={cfg['model']}" for role, cfg in models.items()
                if "model" in cfg
            ) + ")")
        else:
            target.write_text(incoming, encoding="utf-8")
            print(f"  wrote   {name}")


if __name__ == "__main__":
    main()
