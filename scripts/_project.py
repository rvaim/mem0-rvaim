"""Resolve workspace id and branch (local, no cloud).

Resolution priority (workspace_id):
  1. MEM0_WORKSPACE_ID env var (explicit override)
  2. ~/.mem0/local/config/workspace_map.json lookup by cwd
  2b. same map lookup by git remote hash (self-healing on folder moves)
  3. Git remote slug: git@github.com:owner/repo.git -> owner-repo
  4. Fallback: basename of cwd

This is a fork of the official plugin's ``_project.py`` with the
``app_id`` semantics renamed to ``workspace_id``.  It is pure local
logic: no network, no cloud key.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess

_MAP_PATH = os.path.expanduser(os.path.join("~", ".mem0", "local", "config", "workspace_map.json"))


def resolve_workspace_id(cwd: str | None = None) -> str:
    if cwd is None:
        cwd = os.getcwd()

    explicit = os.environ.get("MEM0_WORKSPACE_ID", "").strip()
    if explicit:
        return explicit

    if os.path.isfile(_MAP_PATH):
        try:
            with open(_MAP_PATH, encoding="utf-8") as fh:
                workspace_map = json.load(fh)
            mapped = workspace_map.get(cwd, "").strip()
            if mapped:
                return mapped
            remote_key = _remote_hash_key(cwd)
            if remote_key:
                mapped = workspace_map.get(remote_key, "").strip()
                if mapped:
                    workspace_map[cwd] = mapped
                    try:
                        with open(_MAP_PATH, "w", encoding="utf-8") as fh:
                            json.dump(workspace_map, fh, indent=2)
                    except OSError:
                        pass
                    return mapped
        except (OSError, json.JSONDecodeError, AttributeError):
            pass

    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, check=True, cwd=cwd,
        )
        remote_url = result.stdout.strip()
        if remote_url:
            slug = _remote_url_to_slug(remote_url)
            if slug:
                return slug
    except (subprocess.CalledProcessError, OSError):
        pass

    return os.path.basename(cwd) or "unknown"


def resolve_branch(cwd: str | None = None) -> str:
    if cwd is None:
        cwd = os.getcwd()
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, check=True, cwd=cwd,
        )
        branch = result.stdout.strip()
        return branch if branch else "unknown"
    except (subprocess.CalledProcessError, OSError):
        return "unknown"


def save_workspace_mapping(cwd: str, workspace_id: str) -> None:
    os.makedirs(os.path.dirname(_MAP_PATH), exist_ok=True)
    workspace_map: dict[str, str] = {}
    if os.path.isfile(_MAP_PATH):
        try:
            with open(_MAP_PATH, encoding="utf-8") as fh:
                workspace_map = json.load(fh)
        except (OSError, json.JSONDecodeError):
            workspace_map = {}
    workspace_map[cwd] = workspace_id
    remote_key = _remote_hash_key(cwd)
    if remote_key:
        workspace_map[remote_key] = workspace_id
    try:
        with open(_MAP_PATH, "w", encoding="utf-8") as fh:
            json.dump(workspace_map, fh, indent=2)
    except OSError:
        pass


# ---- compatibility aliases (official naming) ---------------------------
def resolve_project_id(cwd: str | None = None) -> str:
    return resolve_workspace_id(cwd)


def save_project_mapping(cwd: str, project_id: str) -> None:
    save_workspace_mapping(cwd, project_id)


def _remote_hash_key(cwd: str | None = None) -> str:
    if cwd is None:
        cwd = os.getcwd()
    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, check=True, cwd=cwd,
        )
        url = result.stdout.strip()
        if not url:
            return ""
        digest = hashlib.sha256(url.encode()).hexdigest()[:16]
        return f"remote:{digest}"
    except (subprocess.CalledProcessError, OSError):
        return ""


def _remote_url_to_slug(url: str) -> str:
    slug = url.strip()
    if slug.endswith(".git"):
        slug = slug[:-4]
    for prefix in ("https://", "http://", "ssh://", "git://"):
        if slug.startswith(prefix):
            slug = slug[len(prefix):]
            break
    else:
        slug = re.sub(r"^git@", "", slug)
    slug = slug.replace(":", "/", 1)
    parts = [p for p in slug.split("/") if p]
    if len(parts) >= 2:
        owner, repo = parts[-2], parts[-1]
        slug = f"{owner}-{repo}"
    elif parts:
        slug = parts[-1]
    else:
        return ""
    slug = slug.replace("/", "-").replace(":", "-")
    return slug
