"""Load plugin settings.

Settings come from ``~/.mem0/settings.json`` (user-editable, same location
as the official plugin) plus the local daemon config
``~/.mem0/local/config/config.json``.  Env vars always win.

No cloud dependencies.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULTS = {
    "auto_save": True,
    "auto_search": True,
    "search_limit": 10,
    "retention_session_days": 90,
    "confidence_threshold": 0.3,
    "debug": False,
    "prefetch": True,
}


def load_settings() -> dict:
    settings = dict(DEFAULTS)

    user_settings = Path.home() / ".mem0" / "settings.json"
    if user_settings.is_file():
        try:
            with open(user_settings, encoding="utf-8") as fh:
                settings.update(json.load(fh))
        except (OSError, json.JSONDecodeError):
            pass

    root = Path(os.environ.get("MEM0_LOCAL_ROOT") or Path.home() / ".mem0" / "local")
    daemon_config = root / "config" / "config.json"
    if daemon_config.is_file():
        try:
            with open(daemon_config, encoding="utf-8") as fh:
                data = json.load(fh)
            for key in ("auto_save", "auto_search", "search_limit",
                        "retention_session_days", "confidence_threshold", "debug"):
                if key in data:
                    settings[key] = data[key]
        except (OSError, json.JSONDecodeError):
            pass

    if os.environ.get("MEM0_AUTO_SAVE") is not None:
        settings["auto_save"] = os.environ["MEM0_AUTO_SAVE"].lower() not in ("0", "false", "no", "off")
    if os.environ.get("MEM0_AUTO_SEARCH") is not None:
        settings["auto_search"] = os.environ["MEM0_AUTO_SEARCH"].lower() not in ("0", "false", "no", "off")
    if os.environ.get("MEM0_SEARCH_LIMIT"):
        try:
            settings["search_limit"] = int(os.environ["MEM0_SEARCH_LIMIT"])
        except ValueError:
            pass
    if os.environ.get("MEM0_DEBUG") is not None:
        settings["debug"] = os.environ["MEM0_DEBUG"].lower() not in ("0", "false", "no", "off")
    return settings
