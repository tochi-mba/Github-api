"""The log window the hub asks for: from a marker, else the tail; always with counts."""

from __future__ import annotations

from github_api.github.logs import window

LOG = "\n".join(
    [
        "﻿2026-01-01T00:00:00.1234567Z ##[group]Run pytest",
        "2026-01-01T00:00:01.0000000Z collected 3 items",
        "2026-01-01T00:00:02Z tests/test_a.py F",
        "plain line without a stamp",
        "2026-01-01T00:00:03.0000000Z Error: 1 failed",
        "2026-01-01T00:00:04.0000000Z ##[endgroup]",
    ]
)


def test_from_the_first_line_containing_the_marker() -> None:
    excerpt = window(LOG, starting_at="test_a", lines=2)
    assert excerpt.text == "tests/test_a.py F\nplain line without a stamp"
    assert (excerpt.shown, excerpt.total, excerpt.truncated) == (2, 6, True)


def test_without_a_marker_or_a_match_the_tail() -> None:
    assert window(LOG, starting_at="", lines=2).text == "Error: 1 failed\n##[endgroup]"
    assert window(LOG, starting_at="no such text", lines=1).text == "##[endgroup]"


def test_timestamps_are_removed_and_a_short_log_is_whole() -> None:
    excerpt = window(LOG, starting_at="", lines=500)
    assert excerpt.text.splitlines()[0] == "##[group]Run pytest"
    assert (excerpt.shown, excerpt.total, excerpt.truncated) == (6, 6, False)


def test_an_empty_log() -> None:
    excerpt = window("", starting_at="x", lines=10)
    assert (excerpt.text, excerpt.shown, excerpt.total, excerpt.truncated) == ("", 0, 0, False)
