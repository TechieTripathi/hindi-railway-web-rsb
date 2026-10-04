"""shared/datewindow.py — one date parser for both pipelines.

Why this exists
---------------
The two crawlers see dates in different shapes and neither is ISO throughout:

    web   page meta / <time>   "2026-09-06", sometimes "2026-09-06T18:27:00+05:30"
    rsb   CRIS listing table   "27-08-2026"   (DD-MM-YYYY)

A "last 7 days" window therefore cannot compare strings. Everything is parsed to a
`datetime.date` first, and anything unparseable is reported as such rather than
silently treated as old (which would delete it from the run).

    win = Window.last_days(7)
    win.contains("27-08-2026")     -> True / False
    win.contains("")               -> None   (no date on the page)
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

#: Accepted input shapes, most specific first.
_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d.%m.%Y", "%d %b %Y",
            "%d %B %Y", "%b %d, %Y", "%B %d, %Y")


def parse_date(value):
    """A `date`, or None if `value` carries no usable date.

    Tolerates an ISO timestamp by taking its date part. Ambiguity is resolved by
    order: "%Y-%m-%d" is tried before "%d-%m-%Y", so a leading 4-digit group is
    always read as the year.
    """
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    # "2026-09-06T18:27:00+05:30" -> "2026-09-06"
    text = re.split(r"[T ]", text)[0] if re.match(r"^\d{4}-\d{2}-\d{2}[T ]", text) else text
    for fmt in _FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


class Window:
    """An inclusive date range, with `None` meaning unbounded on that side."""

    def __init__(self, start=None, end=None, days=None):
        self.start = parse_date(start) if start else None
        self.end = parse_date(end) if end else None
        self.days = days

    @classmethod
    def last_days(cls, days, today=None):
        """The `days`-day window ending today, inclusive of both ends.

        days=1 means today only; days=7 means today and the six days before it.
        """
        days = max(1, int(days))
        end = today or date.today()
        return cls(start=end - timedelta(days=days - 1), end=end, days=days)

    @classmethod
    def open(cls):
        return cls()

    @property
    def is_open(self):
        return self.start is None and self.end is None

    def contains(self, value):
        """True / False, or **None when the value carries no date at all**.

        Callers decide what an undated item means — see `KEEP_UNDATED` in either
        config. Returning None rather than False keeps that decision out of here.
        """
        if self.is_open:
            return True
        d = parse_date(value)
        if d is None:
            return None
        if self.start and d < self.start:
            return False
        if self.end and d > self.end:
            return False
        return True

    def filter(self, items, key=lambda a: a.get("date"), keep_undated=True):
        """Partition `items`. Returns (kept, dropped_old, undated).

        `undated` items are in `kept` when keep_undated is True, and are always
        returned separately so a caller can report the count.
        """
        kept, old, undated = [], [], []
        for it in items:
            verdict = self.contains(key(it))
            if verdict is None:
                undated.append(it)
                if keep_undated:
                    kept.append(it)
            elif verdict:
                kept.append(it)
            else:
                old.append(it)
        return kept, old, undated

    def describe(self):
        if self.is_open:
            return "any date"
        if self.days:
            return "last %d day%s (%s to %s)" % (
                self.days, "" if self.days == 1 else "s", self.start, self.end)
        return "%s to %s" % (self.start or "any", self.end or "any")

    def __repr__(self):
        return "Window(%s)" % self.describe()


if __name__ == "__main__":
    w = Window.last_days(7)
    print(w.describe())
    for probe in ["2026-09-06", "27-08-2026", "2026-01-01",
                  "2026-09-06T18:27:00+05:30", "", "not a date"]:
        print("  %-28r -> %s" % (probe, w.contains(probe)))
