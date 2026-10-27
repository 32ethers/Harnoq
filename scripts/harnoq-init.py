#!/usr/bin/env python3
"""Install a generated Harnoq coding-agent configuration into the current repo.

Downloads the zip Harnoq generates for a project and extracts it at the current
working directory. The config points at Harnoq's remote MCP endpoint over a URL,
so there is nothing machine-local to substitute — no Harnoq checkout needed.

Most developers just download the zip from the Harnoq web UI and unzip it; this
script is the command-line equivalent.
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


def _get(url, *, binary=False):
    token = os.environ.get("HARNOQ_API_TOKEN", "")
    outbound = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}) if token else url
    try:
        with urllib.request.urlopen(outbound, timeout=30) as response:
            data = response.read()
            return data if binary else json.loads(data.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail).get("error", detail)
        except (TypeError, ValueError):
            pass
        raise RuntimeError(f"Harnoq returned HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"cannot reach Harnoq at {url}: {exc.reason}") from None


def _safe_destination(root, relative_path):
    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"Harnoq returned unsafe configuration path: {relative_path}")
    destination = (root / path).resolve()
    if root != destination and root not in destination.parents:
        raise RuntimeError(f"Harnoq returned unsafe configuration path: {relative_path}")
    return destination


def _project_id(base_url, project_number):
    for project in _get(base_url + "/projects"):
        if project.get("project_number") == project_number:
            return project["id"]
    raise RuntimeError(f"project {project_number} not found")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Install generated Harnoq Claude Code or opencode configuration."
    )
    parser.add_argument("--project", required=True, help="Harnoq project business number, e.g. P1")
    parser.add_argument("--harnoq-url", required=True, help="Harnoq base URL, e.g. http://127.0.0.1:5000")
    parser.add_argument("--flavor", required=True, choices=("claude", "opencode"))
    args = parser.parse_args(argv)

    base_url = args.harnoq_url.rstrip("/")
    project_id = _project_id(base_url, args.project)
    query = urllib.parse.urlencode({"flavor": args.flavor})
    archive_bytes = _get(f"{base_url}/projects/{project_id}/agent-template?{query}", binary=True)

    root = Path.cwd().resolve()
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        names = [n for n in archive.namelist() if not n.endswith("/")]
        if not names:
            raise RuntimeError("Harnoq returned an empty agent configuration archive")
        for name in names:
            destination = _safe_destination(root, name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            contents = archive.read(name)
            if name == ".mcp.json" and destination.exists():
                old = json.loads(destination.read_text(encoding="utf-8"))
                new = json.loads(contents)
                if not isinstance(old, dict) or not isinstance(old.get("mcpServers", {}), dict):
                    raise RuntimeError("existing .mcp.json must contain an MCP server mapping")
                old["mcpServers"] = old.get("mcpServers", {}) | {"harnoq": new["mcpServers"]["harnoq"]}
                contents = (json.dumps(old, ensure_ascii=False, indent=2) + "\n").encode()
            destination.write_bytes(contents)
            print(destination.relative_to(root))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"harnoq-init: {exc}", file=sys.stderr)
        raise SystemExit(1)
