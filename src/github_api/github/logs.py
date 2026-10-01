"""A window on a CI log: where the failure is, and how much of the log that was.

The hub asks for ``lines`` lines starting at the first line that contains ``from`` -- an
error message, a test name -- and, without one, for the end of the log, which is where a
failure usually is. Either way the answer says exactly how many lines it shows of how many,
because nothing truncates silently.

GitHub prefixes every line of an Actions log with an ISO timestamp. It costs a reader a
tenth of every line and says nothing about the failure, so it is removed first.
"""

from __future__ import annotations

import re

from github_api.github.models import LogExcerpt

TIMESTAMP = re.compile(r"^﻿?\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z ?")


def window(raw: str, *, starting_at: str, lines: int) -> LogExcerpt:
    """At most ``lines`` lines: from the first containing ``starting_at``, else the last."""
    every = [TIMESTAMP.sub("", line) for line in raw.splitlines()]
    total = len(every)
    start = None
    if starting_at:
        start = next((i for i, line in enumerate(every) if starting_at in line), None)
    shown = every[start : start + lines] if start is not None else every[-lines:]
    return LogExcerpt(
        text="\n".join(shown), shown=len(shown), total=total, truncated=len(shown) < total
    )


__all__ = ["window"]
