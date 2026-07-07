"""Reconstruct a participant roster from pasted stdout logs.

The DB is in-memory and wiped on every restart (see app.db). The only trace
of who registered is the forensic line `_log_registration` prints for each
accepted registration/modification/unregistration (app.routers.events). This
module turns a paste of those lines back into a list of participants so the
admin can rebuild an event after a crash + restart.

The log line looks like (formatter prefix varies, so we don't depend on it):

    2026-07-07 23:38:57,834 INFO [registration] event=733765829 \
        action=register pid=1 name='awdaw' language=EN format=OPD role=S \
        could_speak_last=True experience=2 forced_judge_last=False \
        special_request='awdaw'

`name` and `special_request` are Python reprs (`%r`) so they may contain
spaces, quotes and escapes; every other field is a bare token. We parse with
one anchored regex over the distinctive `event= … special_request=` field run
(prefix-agnostic) and decode the two repr fields with ast.literal_eval.

Replay semantics — the log is a journal, not a snapshot:
    * register    → a new participant appears,
    * modify      → the participant with that pid is updated in place,
    * unregister  → the participant with that pid is gone.
pid is the identity link *within one server lifetime*; a paste may span
several restarts (pids reset to 1 each time), so `register` always starts a
fresh participant and modify/unregister target the most-recent still-active
participant with that (event, pid). The surviving set is then deduped by
(event, name) keeping the latest state — a name is unique per event, so a
re-registration after a restart collapses onto one person.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Optional

# Valid domains — mirror the CHECK constraints in app.db._SCHEMA. A line whose
# enum fields fall outside these is not a registration we can restore, so we
# skip it rather than let it blow up the INSERT later.
_LANGS = {"DE", "EN", "DE/EN"}
_FORMATS = {"BP", "OPD", "egal"}
_ROLES = {"S", "J", "SJ"}
_EXPERIENCES = {1, 2, 3}

# A Python str repr: single- or double-quoted with backslash escapes.
_REPR = r"""(?:'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")"""

_LINE_RE = re.compile(
    r"event=(?P<event>\d{9})\s+"
    r"action=(?P<action>\w+)\s+"
    r"pid=(?P<pid>\d+)\s+"
    rf"name=(?P<name>{_REPR})\s+"
    r"language=(?P<language>\S+)\s+"
    r"format=(?P<format>\S+)\s+"
    r"role=(?P<role>\S+)\s+"
    r"could_speak_last=(?P<csl>True|False)\s+"
    r"experience=(?P<exp>\d+)\s+"
    r"forced_judge_last=(?P<fjl>True|False)\s+"
    rf"special_request=(?P<sr>None|{_REPR})"
)


@dataclass
class ParsedParticipant:
    """One recovered participant. `event_code` is the *original* event the log
    line belonged to — the caller decides which event to import into."""
    event_code: str
    name: str
    language: str
    format: str
    role: str
    could_speak_last: bool
    experience: int
    special_request: Optional[str]
    forced_judge_last: bool


def _decode_repr(token: str) -> Optional[str]:
    """`ast.literal_eval` a repr token; None-token → None. Returns None on any
    malformed token so the caller can skip the line."""
    if token == "None":
        return None
    try:
        value = ast.literal_eval(token)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, str) else None


def parse_registration_log(text: str) -> list[ParsedParticipant]:
    """Replay the registration journal in `text` into the surviving roster.

    Ignores any line that isn't a well-formed registration record (server
    access logs, blank lines, truncated/garbled lines). Order of survivors
    follows first appearance in the paste.
    """
    # Each entry: [key, ParsedParticipant] where key = (event_code, pid).
    survivors: list[list] = []

    for line in text.splitlines():
        m = _LINE_RE.search(line)
        if m is None:
            continue

        name = _decode_repr(m.group("name"))
        if not name or not name.strip():
            continue  # a registration must have a name
        special_request = _decode_repr(m.group("sr"))

        language = m.group("language")
        fmt = m.group("format")
        role = m.group("role")
        if language not in _LANGS or fmt not in _FORMATS or role not in _ROLES:
            continue
        experience = int(m.group("exp"))
        if experience not in _EXPERIENCES:
            continue

        rec = ParsedParticipant(
            event_code=m.group("event"),
            name=name,
            language=language,
            format=fmt,
            role=role,
            could_speak_last=m.group("csl") == "True",
            experience=experience,
            special_request=special_request,
            forced_judge_last=m.group("fjl") == "True",
        )
        key = (rec.event_code, m.group("pid"))
        action = m.group("action")

        if action == "register":
            survivors.append([key, rec])
        elif action == "modify":
            for entry in reversed(survivors):
                if entry[0] == key:
                    entry[1] = rec
                    break
            else:
                # modify without a preceding register (log truncated before it)
                # still tells us this person existed — keep them.
                survivors.append([key, rec])
        elif action == "unregister":
            for i in range(len(survivors) - 1, -1, -1):
                if survivors[i][0] == key:
                    del survivors[i]
                    break
        # any other action: ignore.

    # Dedupe by (event_code, name), keeping the latest state for each name.
    deduped: dict[tuple[str, str], ParsedParticipant] = {}
    for _key, rec in survivors:
        deduped[(rec.event_code, rec.name)] = rec
    return list(deduped.values())
