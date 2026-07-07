"""Tests for app.log_recovery.parse_registration_log."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app.log_recovery import parse_registration_log  # noqa: E402


def _line(event, action, pid, name, *, language="EN", fmt="OPD", role="S",
          csl=True, exp=2, fjl=False, sr=None, prefix=True):
    """Build one log line exactly as _log_registration would emit it (%r for
    name/special_request)."""
    pre = "2026-07-07 23:38:57,834 INFO [registration] " if prefix else ""
    return (
        f"{pre}event={event} action={action} pid={pid} name={name!r} "
        f"language={language} format={fmt} role={role} "
        f"could_speak_last={csl} experience={exp} forced_judge_last={fjl} "
        f"special_request={sr!r}"
    )


def test_basic_registers():
    log = "\n".join([
        _line("733765829", "register", 1, "Jan"),
        _line("733765829", "register", 2, "Paula", role="J"),
        _line("733765829", "register", 3, "Erwin", language="DE", fmt="BP"),
    ])
    out = parse_registration_log(log)
    assert [p.name for p in out] == ["Jan", "Paula", "Erwin"]
    assert out[1].role == "J"
    assert out[2].language == "DE" and out[2].format == "BP"
    print("basic registers ok")


def test_ignores_access_log_and_junk():
    log = "\n".join([
        'INFO:     172.18.0.1:39438 - "GET /api/events/1/public HTTP/1.0" 200 OK',
        'INFO:     127.0.0.1:58647 - "GET /static/favicon.svg HTTP/1.1" 200 OK',
        _line("733765829", "register", 1, "Jan"),
        "",
        "garbage line that mentions event= but nothing else",
    ])
    out = parse_registration_log(log)
    assert [p.name for p in out] == ["Jan"]
    print("ignores access-log + junk ok")


def test_modify_updates_in_place():
    log = "\n".join([
        _line("111111111", "register", 5, "Jan", role="S", exp=1),
        _line("111111111", "modify", 5, "Jonathan", role="SJ", exp=3),
    ])
    out = parse_registration_log(log)
    assert len(out) == 1
    assert out[0].name == "Jonathan"
    assert out[0].role == "SJ" and out[0].experience == 3
    print("modify updates in place ok")


def test_unregister_removes():
    log = "\n".join([
        _line("111111111", "register", 1, "Jan"),
        _line("111111111", "register", 2, "Paula"),
        _line("111111111", "unregister", 1, "Jan"),
    ])
    out = parse_registration_log(log)
    assert [p.name for p in out] == ["Paula"]
    print("unregister removes ok")


def test_reregister_after_unregister():
    log = "\n".join([
        _line("111111111", "register", 1, "Jan"),
        _line("111111111", "unregister", 1, "Jan"),
        _line("111111111", "register", 2, "Jan"),
    ])
    out = parse_registration_log(log)
    assert [p.name for p in out] == ["Jan"]
    print("re-register after unregister ok")


def test_multi_lifetime_pid_reset():
    # A paste spanning a restart: pid resets to 1, different person. Both must
    # survive as distinct people because `register` always starts fresh.
    log = "\n".join([
        _line("111111111", "register", 1, "Anna"),
        _line("111111111", "register", 2, "Ben"),
        # ---- restart, pid counter resets ----
        _line("111111111", "register", 1, "Cara"),
    ])
    out = parse_registration_log(log)
    assert sorted(p.name for p in out) == ["Anna", "Ben", "Cara"]
    print("multi-lifetime pid reset ok")


def test_name_with_spaces_and_quotes():
    tricky = "O'Brien von der Alm"  # apostrophe forces double-quoted repr
    log = _line("111111111", "register", 1, tricky, sr="needs a chair, please")
    out = parse_registration_log(log)
    assert len(out) == 1
    assert out[0].name == tricky
    assert out[0].special_request == "needs a chair, please"
    print("name with spaces/quotes ok")


def test_special_request_none_vs_value():
    log = "\n".join([
        _line("111111111", "register", 1, "A", sr=None),
        _line("111111111", "register", 2, "B", sr="vegan"),
    ])
    out = parse_registration_log(log)
    assert out[0].special_request is None
    assert out[1].special_request == "vegan"
    print("special_request None vs value ok")


def test_invalid_enum_skipped():
    log = "\n".join([
        _line("111111111", "register", 1, "Good"),
        # experience 9 is outside CHECK(1,2,3) — must be dropped, not crash.
        _line("111111111", "register", 2, "Bad", exp=9),
        # bogus language.
        "event=111111111 action=register pid=3 name='X' language=FR format=BP "
        "role=S could_speak_last=True experience=2 forced_judge_last=False "
        "special_request=None",
    ])
    out = parse_registration_log(log)
    assert [p.name for p in out] == ["Good"]
    print("invalid enum lines skipped ok")


def test_multiple_events_kept_separate():
    log = "\n".join([
        _line("111111111", "register", 1, "Jan"),
        _line("222222222", "register", 1, "Jan"),  # same name, other event
    ])
    out = parse_registration_log(log)
    # Two distinct people because they belong to different events.
    assert len(out) == 2
    assert {p.event_code for p in out} == {"111111111", "222222222"}
    print("multiple events kept separate ok")


def test_no_prefix_lines():
    # Some paste sources strip the timestamp/level prefix — must still parse.
    log = _line("111111111", "register", 1, "Jan", prefix=False)
    out = parse_registration_log(log)
    assert [p.name for p in out] == ["Jan"]
    print("prefix-less lines ok")


def test_empty_and_whitespace():
    assert parse_registration_log("") == []
    assert parse_registration_log("   \n\n  ") == []
    print("empty input ok")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\ntest_log_recovery: ALL CHECKS PASSED")
