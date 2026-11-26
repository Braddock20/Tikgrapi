"""Custom commands.

An alias gives a name to a command the operator uses often — ``/post``
for ``/upload``, ``/vids`` for ``/myvideos`` — so the chat stays short on a
phone. Aliases are stored alongside the schedules, so they survive a restart.
"""
from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

STATE_KEY = "bot_aliases"

_NAME_RE = re.compile(r"^[a-z0-9_-]{1,24}$")


class AliasStore:
    """The operator's own commands."""

    def __init__(self, store: Any = None) -> None:
        self.store = store
        self.aliases: dict[str, str] = {}

    async def load(self) -> None:
        if self.store is None:
            return
        try:
            state = await self.store.load_state(STATE_KEY)
        except Exception as exc:  # noqa: BLE001
            log.info("could not load the aliases: %s", exc)
            return
        if not state:
            return
        raw = state.get("aliases") or {}
        self.aliases = {
            str(k).lower(): str(v)
            for k, v in raw.items()
            if isinstance(raw, dict)
        }
        log.info("loaded %d alias(es)", len(self.aliases))

    async def save(self) -> None:
        if self.store is None:
            return
        try:
            await self.store.save_state(STATE_KEY, {"aliases": self.aliases})
        except Exception as exc:  # noqa: BLE001
            log.warning("could not save the aliases: %s", exc)

    # -- management ------------------------------------------------------
    async def add(self, name: str, command: str) -> str:
        name = (name or "").lstrip("/").lower()
        command = (command or "").strip()
        if not _NAME_RE.match(name):
            return "an alias name may use letters, numbers, dash and underscore (up to 24)"
        if not command:
            return "an alias needs a command to run"
        if not command.startswith("/"):
            command = "/" + command
        self.aliases[name] = command
        await self.save()
        return f"/{name} now runs {command}"

    async def remove(self, name: str) -> str:
        name = (name or "").lstrip("/").lower()
        if name not in self.aliases:
            return f"no alias called '{name}'"
        command = self.aliases.pop(name)
        await self.save()
        return f"removed /{name} (was {command})"

    async def list(self) -> list[str]:
        return sorted(self.aliases)

    async def describe(self) -> str:
        if not self.aliases:
            return (
                "no custom commands yet.\n"
                "add one with: /alias add <name> <command>\n"
                "for example: /alias add vids /myvideos"
            )
        lines = ["custom commands:"]
        for name in sorted(self.aliases):
            lines.append(f"/{name} → {self.aliases[name]}")
        return "\n".join(lines)

    def resolve(self, token: str) -> str | None:
        """The command an alias stands for, or None."""
        return self.aliases.get((token or "").lstrip("/").lower())


__all__ = ["AliasStore"]
