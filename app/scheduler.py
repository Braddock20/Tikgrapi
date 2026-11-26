"""Scheduled automations.

A small cron-style runner that lets the operator say things like
"every 30 minutes, post the next queued video" or "every day at 09:00,
report the account's stats" — and have them happen without anyone watching.

Schedules are written in the plainest form that still covers the real cases:

============================  ==================================================
``every 30m``                 every thirty minutes
``every 2h`` / ``every 1d``   every two hours / every day
``hourly``                    on the hour
``daily`` / ``daily 09:30``   once a day, at 09:30
``*/15 * * * *``              a five-field cron expression
============================  ==================================================

Jobs survive a restart: they are stored in the same database as everything
else, so a redeploy does not lose them.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

STATE_KEY = "scheduler_jobs"

_MINUTE = 60
_HOUR = 3600
_DAY = 86400


@dataclass
class Job:
    id: str
    schedule: str
    command: str
    created_at: float = field(default_factory=time.time)
    last_run: float | None = None
    last_result: str = ""
    runs: int = 0
    enabled: bool = True

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "schedule": self.schedule,
            "command": self.command,
            "created_at": self.created_at,
            "last_run": self.last_run,
            "last_result": self.last_result[:300],
            "runs": self.runs,
            "enabled": self.enabled,
        }


class ScheduleError(ValueError):
    """The schedule could not be understood."""


def parse_schedule(text: str) -> dict:
    """Turn a schedule into a matcher description.

    Returns a dict with ``kind`` and the parameters the runner needs.
    """
    text = (text or "").strip().lower()
    if not text:
        raise ScheduleError("the schedule is empty")

    if text in ("hourly", "every hour", "every 1h"):
        return {"kind": "interval", "seconds": _HOUR, "label": "every hour"}
    if text in ("daily", "every day", "every 24h"):
        return {"kind": "daily", "at": "09:00", "label": "every day at 09:00"}

    match = re.fullmatch(r"every\s+(\d+)\s*(m|min|mins|minute|minutes|h|hr|hour|hours|d|day|days)", text)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if unit.startswith("m"):
            seconds, label = amount * _MINUTE, f"every {amount} minute(s)"
        elif unit.startswith("h"):
            seconds, label = amount * _HOUR, f"every {amount} hour(s)"
        else:
            seconds, label = amount * _DAY, f"every {amount} day(s)"
        if seconds < 60:
            raise ScheduleError("the shortest schedule is one minute")
        return {"kind": "interval", "seconds": seconds, "label": label}

    match = re.fullmatch(r"daily\s+(\d{1,2}):(\d{2})", text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 23 or minute > 59:
            raise ScheduleError("that time of day does not exist")
        return {
            "kind": "daily",
            "at": f"{hour:02d}:{minute:02d}",
            "label": f"every day at {hour:02d}:{minute:02d}",
        }

    if len(text.split()) == 5:
        fields = text.split()
        bounds = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
        for spec, (low, high) in zip(fields, bounds):
            if not _valid_cron_field(spec, low, high):
                raise ScheduleError(
                    f"'{spec}' is not a valid cron field (it must be a number, "
                    f"a range, a list or '*', between {low} and {high})"
                )
        return {
            "kind": "cron",
            "expression": fields,
            "label": "cron " + " ".join(fields),
        }

    raise ScheduleError(
        "I did not understand that schedule. Try: every 30m, hourly, daily 09:00, or */15 * * * *"
    )


def _valid_cron_field(spec: str, low: int, high: int) -> bool:
    """Is this a usable cron field?

    Accepts ``*``, ``*/step``, ``a``, ``a-b``, ``a-b/step`` and comma lists
    of those — the same shapes :func:`cron_matches` understands.
    """
    for part in spec.split(","):
        part = part.strip()
        if not part:
            return False
        step = None
        if "/" in part:
            part, _, step_text = part.partition("/")
            if not step_text.isdigit():
                return False
            step = int(step_text)
            if step <= 0:
                return False
            if part == "":
                part = "*"
        if part == "*":
            continue
        if "-" in part:
            left, _, right = part.partition("-")
            if not left.isdigit() or not right.isdigit():
                return False
            if not (low <= int(left) <= high and low <= int(right) <= high):
                return False
            continue
        if not part.isdigit():
            return False
        if not (low <= int(part) <= high):
            return False
    return True


def cron_matches(expression: list[str], when: time.struct_time) -> bool:
    """Does a five-field cron expression match this moment?"""
    minute, hour, dom, month, dow = expression

    def field_matches(spec: str, value: int) -> bool:
        if spec == "*":
            return True
        for part in spec.split(","):
            step = 1
            if "/" in part:
                part, _, step_text = part.partition("/")
                try:
                    step = int(step_text)
                except ValueError:
                    return False
            if part in ("*", ""):
                start, end = 0, 59
            elif "-" in part:
                low, _, high = part.partition("-")
                try:
                    start, end = int(low), int(high)
                except ValueError:
                    return False
            else:
                try:
                    start = end = int(part)
                except ValueError:
                    return False
            if start <= value <= end and (value - start) % step == 0:
                return True
        return False

    return (
        field_matches(minute, when.tm_min)
        and field_matches(hour, when.tm_hour)
        and field_matches(dom, when.tm_mday)
        and field_matches(month, when.tm_mon)
        and field_matches(dow, (when.tm_wday + 1) % 7)
    )


class Scheduler:
    """Runs commands on a schedule."""

    def __init__(self, dispatcher: Any = None, store: Any = None) -> None:
        self.dispatcher = dispatcher
        self.store = store
        self.jobs: dict[str, Job] = {}
        self._task: asyncio.Task | None = None
        self._running = False

    # -- persistence -----------------------------------------------------
    async def load(self) -> None:
        if self.store is None:
            return
        try:
            raw = await self.store.load_state(STATE_KEY)
        except Exception as exc:  # noqa: BLE001
            log.info("could not load the scheduled jobs: %s", exc)
            return
        if not raw:
            return
        try:
            for row in raw.get("jobs", []):
                job = Job(
                    id=str(row.get("id")),
                    schedule=str(row.get("schedule", "")),
                    command=str(row.get("command", "")),
                    created_at=float(row.get("created_at") or time.time()),
                    last_run=row.get("last_run"),
                    last_result=str(row.get("last_result", "")),
                    runs=int(row.get("runs") or 0),
                    enabled=bool(row.get("enabled", True)),
                )
                self.jobs[job.id] = job
        except (TypeError, ValueError) as exc:
            log.warning("the stored schedule was malformed: %s", exc)
        log.info("loaded %d scheduled job(s)", len(self.jobs))

    async def save(self) -> None:
        if self.store is None:
            return
        payload = {"jobs": [job.as_dict() for job in self.jobs.values()]}
        try:
            await self.store.save_state(STATE_KEY, payload)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not save the scheduled jobs: %s", exc)

    # -- management ------------------------------------------------------
    async def add(self, schedule: str, command: str) -> Job:
        parse_schedule(schedule)  # validate before storing
        job = Job(id=uuid.uuid4().hex[:8], schedule=schedule.strip(), command=command.strip())
        self.jobs[job.id] = job
        await self.save()
        log.info("scheduled %s: %s", job.id, job.command)
        return job

    async def add_from_chat(self, schedule: str, command: str) -> str:
        try:
            job = await self.add(schedule, command)
        except ScheduleError as exc:
            return str(exc)
        return f"scheduled [{job.id}] {job.schedule} → {job.command}"

    async def remove(self, job_id: str) -> str:
        job = self.jobs.pop(job_id, None)
        if job is None:
            for key in list(self.jobs):
                if key.startswith(job_id):
                    job = self.jobs.pop(key)
                    break
        if job is None:
            return f"no job called '{job_id}'"
        await self.save()
        return f"removed [{job.id}] {job.command}"

    async def list(self) -> list[dict]:
        return [job.as_dict() for job in self.jobs.values()]

    async def describe(self) -> str:
        if not self.jobs:
            return (
                "no scheduled jobs yet.\n"
                "add one with: /cron add every 30m <command>\n"
                "schedules: every 30m · every 2h · hourly · daily 09:00 · */15 * * * *"
            )
        lines = ["scheduled jobs:"]
        for job in self.jobs.values():
            state = "on" if job.enabled else "off"
            lines.append(f"[{job.id}] {job.schedule} ({state}) → {job.command}")
            if job.runs:
                lines.append(f"    last run {job.last_result[:90]}")
        return "\n".join(lines)

    # -- running ---------------------------------------------------------
    async def run_now(self, job_id: str) -> str:
        job = self.jobs.get(job_id)
        if job is None:
            for key in list(self.jobs):
                if key.startswith(job_id):
                    job = self.jobs[key]
                    break
        if job is None:
            return f"no job called '{job_id}'"
        ok, detail = await self._execute(job)
        return detail

    async def _execute(self, job: Job) -> tuple[bool, str]:
        if self.dispatcher is None:
            return False, "there is no dispatcher to run the command"
        try:
            reply = await self.dispatcher.run_text(job.command)
        except Exception as exc:  # noqa: BLE001
            job.last_result = f"failed: {type(exc).__name__}: {exc}"
            return False, job.last_result
        job.runs += 1
        job.last_run = time.time()
        job.last_result = (reply or "(no output)").replace("\n", " ")[:280]
        return True, f"[{job.id}] {job.command} → {job.last_result}"

    def _is_due(self, job: Job, now: float, when: time.struct_time) -> bool:
        try:
            spec = parse_schedule(job.schedule)
        except ScheduleError:
            return False
        if spec["kind"] == "interval":
            if job.last_run is None:
                return True
            return (now - job.last_run) >= spec["seconds"]
        if spec["kind"] == "daily":
            hour, minute = (int(x) for x in spec["at"].split(":"))
            if when.tm_hour != hour or when.tm_min != minute:
                return False
            if job.last_run is None:
                return True
            return (now - job.last_run) >= _DAY - 60
        if spec["kind"] == "cron":
            if not cron_matches(spec["expression"], when):
                return False
            if job.last_run is None:
                return True
            return (now - job.last_run) >= 60
        return False

    async def tick(self) -> int:
        """Run whatever is due. Returns how many jobs ran."""
        now = time.time()
        when = time.localtime(now)
        ran = 0
        for job in list(self.jobs.values()):
            if not job.enabled:
                continue
            try:
                if not self._is_due(job, now, when):
                    continue
            except Exception:  # noqa: BLE001
                continue
            log.info("running the scheduled job %s: %s", job.id, job.command)
            await self._execute(job)
            ran += 1
        if ran:
            await self.save()
        return ran

    async def run_forever(self, interval: float = 30.0) -> None:
        self._running = True
        while self._running:
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001
                log.warning("the scheduler tick failed: %s", exc)
            await asyncio.sleep(interval)

    def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None


__all__ = ["Job", "ScheduleError", "Scheduler", "cron_matches", "parse_schedule"]
