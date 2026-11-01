# -*- coding: utf-8 -*-
"""TeamSync domain model.

Seven small classes carry the whole project:

    Availability       a recurring local-time window such as "Mon-Fri 19:00-23:00"
    BusyBlock          one imported calendar event, as a UTC interval
    Member             a person: city, timezone, attendance role, days away,
                       availability windows and busy blocks
    AvailabilityCheck  one member's local opinion of one candidate meeting time
    Slot               one cell of the UTC scan timeline
    Segment            a run of consecutive slots that share the same free members
    Recommendation     a ranked candidate meeting time, ready to be shown

Three ideas deserve a word here because they are new since version 1:

* ``Member.required`` -- the meeting cannot count as a full success without a
  required member, while an optional member is a bonus.  The planner prices the
  two roles differently, and a recommendation remembers who is missing.
* ``Member.away`` -- whole local dates on which the member is unavailable
  (leave, an exam, a public holiday).  "Away" is a *local calendar* fact, so a
  member in Sydney is away on Sydney's 4 March, not on UTC's.
* ``Member.busy`` -- absolute intervals imported from an .ics calendar.  They
  are absolute UTC instants (not local wall-clock rules), which is exactly what
  a one-off appointment is.

Nothing in this module touches the filesystem, prints anything, or knows that
tkinter exists -- which is exactly why the unit tests can cover the same code the
GUI runs.
"""

from datetime import datetime, timedelta, timezone

SLOT_MINUTES = 30
MINUTES_PER_DAY = 24 * 60

WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class TeamSyncError(Exception):
    """Base class for every error TeamSync raises on purpose."""


class InvalidTimeFormat(TeamSyncError):
    """Raised when a time string is not in HH:MM form."""


class InvalidDate(TeamSyncError):
    """Raised when a date string is not a real calendar day in YYYY-MM-DD form."""


def parse_hhmm(text):
    """'19:00' -> 1140 (minutes since local midnight). Raises InvalidTimeFormat."""
    parts = str(text).strip().split(":")
    if len(parts) != 2:
        raise InvalidTimeFormat("expected HH:MM, got %r" % (text,))
    try:
        hours, minutes = int(parts[0]), int(parts[1])
    except ValueError:
        raise InvalidTimeFormat("expected HH:MM, got %r" % (text,))
    if not (0 <= hours < 24 and 0 <= minutes < 60):
        raise InvalidTimeFormat("out of range: %r" % (text,))
    return hours * 60 + minutes


def format_hhmm(minutes):
    """1140 -> '19:00'."""
    minutes %= MINUTES_PER_DAY
    return "%02d:%02d" % (minutes // 60, minutes % 60)


def parse_iso_date(text):
    """'2026-03-04' -> date(2026, 3, 4). Raises InvalidDate."""
    try:
        return datetime.strptime(str(text).strip(), "%Y-%m-%d").date()
    except ValueError:
        raise InvalidDate("expected a date such as 2026-03-04, got %r" % (text,))


def format_iso_date(day):
    """date(2026, 3, 4) -> '2026-03-04'."""
    return "%04d-%02d-%02d" % (day.year, day.month, day.day)


def parse_date_list(text):
    """'2026-03-04, 2026-03-06..2026-03-08' -> {date, date, ...}.

    Commas (or semicolons) separate items; ``..`` writes an inclusive range.  An
    empty string is allowed and simply means "no days away", which is what the
    GUI's away-dates box holds for almost every member.
    """
    days = set()
    for chunk in str(text).replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ".." not in chunk:
            days.add(parse_iso_date(chunk))
            continue
        first, _, last = chunk.partition("..")
        start, end = parse_iso_date(first), parse_iso_date(last)
        if end < start:
            raise InvalidDate("date range must run forwards: %r" % (chunk,))
        day = start
        while day <= end:
            days.add(day)
            day += timedelta(days=1)
    return days


def format_date_list(days):
    """{date, date} -> '2026-03-04, 2026-03-05'."""
    return ", ".join(format_iso_date(day) for day in sorted(days))


def parse_utc_stamp(text):
    """'2026-03-02T09:00Z' -> aware datetime in UTC. Raises InvalidDate."""
    try:
        moment = datetime.strptime(str(text).strip(), "%Y-%m-%dT%H:%MZ")
    except ValueError:
        raise InvalidDate("expected a UTC stamp such as 2026-03-02T09:00Z, got %r"
                          % (text,))
    return moment.replace(tzinfo=timezone.utc)


def format_utc_stamp(moment):
    """aware datetime -> '2026-03-02T09:00Z' (always rendered in UTC)."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def weekday_index(raw):
    """'Wed', 'wednesday' -> 2."""
    token = str(raw).strip().lower()[:3]
    for position, name in enumerate(WEEKDAY_NAMES):
        if name.lower() == token:
            return position
    raise TeamSyncError("unknown weekday: %r" % (raw,))


def parse_days(items):
    """['Mon', 'Tue'] or ['Mon-Fri'] -> {0, 1}.

    Accepts English short or long names in any case, single days or ranges
    written with a hyphen. ``describe_days`` only ever produces strings this
    function accepts, so the two round-trip -- the GUI shows an existing window
    back in the very form the user typed.
    """
    days = set()
    for raw in items:
        if str(raw).strip().lower() in ("every day", "everyday", "daily", "all"):
            days.update(range(len(WEEKDAY_NAMES)))
            continue
        for token in str(raw).replace(",", " ").split():
            if "-" not in token:
                days.add(weekday_index(token))
                continue
            first, _, last = token.partition("-")
            start, end = weekday_index(first), weekday_index(last)
            if start > end:
                raise TeamSyncError("weekday range must run forwards: %r" % (token,))
            days.update(range(start, end + 1))
    if not days:
        raise TeamSyncError("a window needs at least one weekday")
    return days


def parse_windows(text, days=(0, 1, 2, 3, 4)):
    """'09:00-12:00 + 18:00-22:00' -> [Availability, ...].

    The separator is ``+`` and every window in the string shares the same
    weekday set. ``Member.windows_text`` writes exactly this form, so an edit box
    can be filled from a member and read straight back.
    """
    windows = []
    for chunk in str(text).replace(",", "+").split("+"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" not in chunk:
            raise InvalidTimeFormat(
                "expected a window such as 09:00-12:00, got %r" % (chunk,))
        start_text, _, end_text = chunk.partition("-")
        windows.append(Availability(days, parse_hhmm(start_text), parse_hhmm(end_text)))
    if not windows:
        raise InvalidTimeFormat("no usable window in %r" % (text,))
    return windows


def describe_days(days):
    """{0,1,2,3,4} -> 'Mon-Fri'; {0,2,4} -> 'Mon, Wed, Fri'."""
    ordered = sorted(days)
    if not ordered:
        return "-"
    if len(ordered) == len(WEEKDAY_NAMES):
        return "Every day"
    chunks, start, previous = [], ordered[0], ordered[0]
    for day in ordered[1:] + [None]:
        if day is not None and day == previous + 1:
            previous = day
            continue
        chunks.append(WEEKDAY_NAMES[start] if start == previous
                      else "%s-%s" % (WEEKDAY_NAMES[start], WEEKDAY_NAMES[previous]))
        if day is not None:
            start = previous = day
    return ", ".join(chunks)


class Availability:
    """A recurring local-time window.

    ``days`` lists the weekdays on which the window *starts* (0 = Monday).
    When ``end_min <= start_min`` it wraps past midnight, so "Mon 22:00-02:00"
    also covers Tuesday 00:00-01:59; ``start_min == end_min`` means all day.
    """

    def __init__(self, days, start_min, end_min):
        self.days = frozenset(days)
        self.start_min = start_min
        self.end_min = end_min

    @property
    def wraps_midnight(self):
        return self.end_min <= self.start_min

    @property
    def length_min(self):
        """How many minutes one occurrence of this window lasts."""
        if self.start_min == self.end_min:
            return MINUTES_PER_DAY
        if self.wraps_midnight:
            return MINUTES_PER_DAY - self.start_min + self.end_min
        return self.end_min - self.start_min

    def covers(self, weekday, minute):
        """True if local ``weekday`` + ``minute`` falls inside this window."""
        if not self.wraps_midnight:
            return weekday in self.days and self.start_min <= minute < self.end_min
        if minute >= self.start_min:
            return weekday in self.days
        if minute < self.end_min:
            return (weekday - 1) % 7 in self.days
        return False

    def describe(self):
        """'Mon-Fri 19:00-23:00' -- used by the members tab and the JSON export."""
        return "%s %s-%s" % (describe_days(self.days),
                             format_hhmm(self.start_min),
                             format_hhmm(self.end_min))

    def to_dict(self):
        return {"days": [WEEKDAY_NAMES[day] for day in sorted(self.days)],
                "start": format_hhmm(self.start_min),
                "end": format_hhmm(self.end_min)}

    @classmethod
    def from_dict(cls, data):
        return cls(parse_days(data["days"]),
                   parse_hhmm(data["start"]),
                   parse_hhmm(data["end"]))

    def __repr__(self):
        return "Availability(%s)" % self.describe()


class BusyBlock:
    """One imported calendar event, as a half-open UTC interval ``[start, end)``.

    Busy blocks come from a member's .ics file (see ``ics_import.py``).  They
    are absolute instants, so a 09:00 London lecture stays at 09:00 London even
    when British Summer Time starts -- unlike an Availability window, which is a
    repeating local rule.
    """

    def __init__(self, start, end, title=""):
        if end <= start:
            raise TeamSyncError("a busy block must end after it starts")
        self.start = start
        self.end = end
        self.title = title or ""

    def covers(self, utc_dt):
        return self.start <= utc_dt < self.end

    def describe(self):
        return "%s - %s UTC" % (self.start.strftime("%a %H:%M"),
                                self.end.strftime("%a %H:%M"))

    def to_dict(self):
        return {"start_utc": format_utc_stamp(self.start),
                "end_utc": format_utc_stamp(self.end),
                "title": self.title}

    @classmethod
    def from_dict(cls, data):
        return cls(parse_utc_stamp(data["start_utc"]),
                   parse_utc_stamp(data["end_utc"]),
                   data.get("title", ""))

    def __repr__(self):
        return "BusyBlock(%s, %r)" % (self.describe(), self.title)


class Member:
    """A person: display name, city, IANA zone, role, days away and windows."""

    def __init__(self, name, city, tzkey, windows, required=True, away=(),
                 busy=()):
        self.name = name
        self.city = city
        self.tzkey = tzkey
        self.windows = list(windows)
        self.required = bool(required)   # False = nice to have, not essential
        self.away = frozenset(away)      # whole local dates with no availability
        self.busy = list(busy)           # imported calendar events (UTC intervals)
        self._tz = None

    @property
    def tz(self):
        """ZoneInfo for this member, read from the bundled files on first use."""
        if self._tz is None:
            # imported here rather than at module level: cities.py imports this
            # module for its exception types, so a top-level import would cycle
            from cities import load_tz
            self._tz = load_tz(self.tzkey)
        return self._tz

    def local(self, utc_dt):
        """Convert a UTC datetime into this member's local time."""
        return utc_dt.astimezone(self.tz)

    def window_at(self, local_moment):
        """The Availability window covering this *local* moment, or None."""
        minute = local_moment.hour * 60 + local_moment.minute
        for window in self.windows:
            if window.covers(local_moment.weekday(), minute):
                return window
        return None

    def is_free(self, local_moment):
        """True if this local moment is inside a window and not a day away."""
        if local_moment.date() in self.away:
            return False
        return self.window_at(local_moment) is not None

    def busy_at(self, utc_dt):
        """The imported calendar event covering ``utc_dt``, or None."""
        for block in self.busy:
            if block.covers(utc_dt):
                return block
        return None

    def available_at(self, utc_dt):
        """True if ``utc_dt`` is in a local window, not away, and not busy."""
        if self.busy_at(utc_dt) is not None:
            return False
        return self.is_free(self.local(utc_dt))

    @property
    def weekly_minutes(self):
        """Total scheduled availability in one week, capped at a whole week.

        Windows may overlap on purpose (a member who is free in the morning and
        again late in the evening is not free in between), so the sum can in
        theory exceed 7 days; we clamp it instead of showing 190 hours.
        """
        total = 0
        for window in self.windows:
            total += window.length_min * len(window.days)
        return min(total, 7 * MINUTES_PER_DAY)

    def describe_windows(self):
        """'Mon-Fri 09:00-12:00 + Mon-Fri 18:00-22:00' -- the table in the GUI."""
        return " + ".join(window.describe() for window in self.windows) or "-"

    def windows_text(self):
        """'09:00-12:00 + 18:00-22:00' -- the form the edit box in the GUI uses."""
        return " + ".join("%s-%s" % (format_hhmm(window.start_min),
                                     format_hhmm(window.end_min))
                          for window in self.windows)

    def away_text(self):
        """'2026-03-04, 2026-03-05', or '-' when the member is never away."""
        return format_date_list(self.away) or "-"

    def busy_text(self):
        """'3 events', or '-' when no calendar has been imported."""
        if not self.busy:
            return "-"
        if len(self.busy) == 1:
            return "1 event"
        return "%d events" % len(self.busy)

    def to_dict(self):
        """JSON shape. Optional fields are only written when they differ from
        the defaults, so a simple team file stays short and readable."""
        data = {"name": self.name, "city": self.city, "tzkey": self.tzkey}
        if not self.required:
            data["required"] = False
        if self.away:
            data["away"] = [format_iso_date(day) for day in sorted(self.away)]
        data["availability"] = [window.to_dict() for window in self.windows]
        if self.busy:
            data["busy"] = [block.to_dict() for block in self.busy]
        return data

    @classmethod
    def from_dict(cls, data):
        return cls(data["name"], data.get("city", ""), data["tzkey"],
                   [Availability.from_dict(item) for item in data["availability"]],
                   required=data.get("required", True),
                   away=[parse_iso_date(item) for item in data.get("away", [])],
                   busy=[BusyBlock.from_dict(item)
                         for item in data.get("busy", [])])

    def __repr__(self):
        return "Member(%r, %s%s)" % (self.name, self.tzkey,
                                     "" if self.required else ", optional")


class AvailabilityCheck:
    """One member's local view of one candidate meeting time.

    The planner builds these; ``render`` turns them into a table.  Keeping the
    answer as data (not as a formatted string) lets the GUI show it in a dialog
    and the CLI print it, from the same code.
    """

    def __init__(self, member, local_dt, free, window, away, busy=None):
        self.member = member
        self.local = local_dt
        self.free = free
        self.window = window
        self.away = away
        self.busy = busy              # a BusyBlock, or None

    @property
    def reason(self):
        """'', 'away', 'busy' or 'outside window' -- why they cannot attend."""
        if self.away:
            return "away"
        if self.busy is not None:
            return "busy"
        if not self.free:
            return "outside window"
        return ""

    @property
    def window_text(self):
        return self.window.describe() if self.window else "-"

    def __repr__(self):
        return "AvailabilityCheck(%r, %s)" % (self.member.name,
                                              "free" if self.free else self.reason)


class Slot:
    """One cell of the UTC scan timeline."""

    def __init__(self, start, members, score):
        self.start = start            # UTC datetime, always on :00 or :30
        self.members = frozenset(members)
        self.score = score            # summed points of those members

    @property
    def count(self):
        return len(self.members)

    def __repr__(self):
        return "Slot(%s, %d free, score=%d)" % (
            self.start.strftime("%a %H:%M"), self.count, self.score)


class Segment:
    """A run of consecutive slots that all share the same set of free members."""

    def __init__(self, slots, members):
        self.slots = list(slots)
        self.members = frozenset(members)

    @property
    def start(self):
        return self.slots[0].start

    @property
    def end(self):
        """UTC datetime just past the last slot (exclusive)."""
        return self.slots[-1].start + timedelta(minutes=SLOT_MINUTES)

    @property
    def duration_min(self):
        return len(self.slots) * SLOT_MINUTES

    @property
    def count(self):
        return len(self.members)

    def __repr__(self):
        return "Segment(%s, %d min, cover=%d)" % (
            self.start.strftime("%a %H:%M"), self.duration_min, self.count)


class Recommendation:
    """A ranked candidate meeting time, ready to be printed or exported."""

    def __init__(self, start, duration_min, members, score, continuous_min,
                 missing=()):
        self.start = start                    # UTC
        self.duration_min = duration_min
        self.members = tuple(members)         # people who can come
        self.missing = tuple(missing)         # people who cannot
        self.score = score                    # coverage points + comfort points
        self.continuous_min = continuous_min  # how long the group stays free

    @property
    def count(self):
        return len(self.members)

    @property
    def total(self):
        return self.count + len(self.missing)

    @property
    def required_ok(self):
        """True when every *required* member is in the room.

        Optional members may be missing; a required member may not.  The planner
        sorts on this first, so a full-attendance slot always beats a partial one.
        """
        for member in self.missing:
            if member.required:
                return False
        return True

    @property
    def missing_required(self):
        return tuple(member for member in self.missing if member.required)

    @property
    def continuous_hours(self):
        return self.continuous_min / 60.0

    def __repr__(self):
        return "Recommendation(%s UTC, %d min, cover=%d/%d, score=%d)" % (
            self.start.strftime("%Y-%m-%d %H:%M"), self.duration_min,
            self.count, self.total, self.score)
