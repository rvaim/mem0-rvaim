"""Schema migrations for state.db (versioned; only the daemon applies them)."""

from __future__ import annotations

from typing import Dict, List

MIGRATIONS: Dict[int, List[str]] = {
    # 1 -> baseline schema (created by StateStore)
}

# Migrations are keyed by target version; StateStore.apply_migration()
# records each applied version in the migrations table.
