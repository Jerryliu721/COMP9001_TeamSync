# -*- coding: utf-8 -*-
"""The TeamSync core: scan the week, cut it into segments, rank meeting slots.

Everything is anchored on a single UTC timeline that starts at Monday 00:00 and
runs for five days (``SLOTS_PER_WEEK`` half-hour cells). Members are converted to
their own local time only when we ask whether they are free, and again when a
result is displayed -- keeping one authoritative clock keeps the arithmetic sane.

Scoring has two parts:

    coverage points  40 for a required member, 20 for an optional member
    comfort points   how civilised the meeting start is in that member's own local
                     time: 09:00-18:00 = 30, 07:00-09:00 and 18:00-22:00 = 15,
                     anything else = 0, minus 10 at the weekend

Only the *start* cell is scored. A meeting that lasts three hours must not outrank
a better one simply because it touches more comfortable cells -- see
``tests/test_planner.py::test_score_uses_start_only``.

Ranking puts "every required member can come" first, then the number of
attendees, then the score, then the earliest time. That one line is what lets an
optional member be a bonus rather than a blocker.

The module also answers the three questions the command line and the GUI ask
around the core plan:

    plan_weeks()        which week in the next month works best?
    duration_options()  what meeting lengths are actually possible?
    explain()           why can (or can't) this person come at that moment?
"""

from datetime import datetime, timedelta, timezone

from models import AvailabilityCheck, Recommendation, SLOT_MINUTES, Segment, Slot

SLOTS_PER_DAY = 24 * 60 // SLOT_MINUTES   # 48
SLOTS_PER_WEEK = 5 * SLOTS_PER_DAY        # 240

COVERAGE_BONUS = 40          # a required member who can attend
OPTIONAL_BONUS = 20          # an optional member who can attend
COMFORT_WORK = 30
COMFORT_EDGE = 15
COMFORT_NIGHT = 0
WEEKEND_PENALTY = 10

WORK_START, WORK_END = 9 * 60, 18 * 60    # 09:00 .. 18:00
EDGE_EARLY, EDGE_LATE = 7 * 60, 22 * 60   # 07:00 .. 22:00

DEFAULT_LENGTHS = (30, 45, 60, 90, 120)


class WeekFormatError(ValueError):
    """Raised when a week string is not YYYY-MM-DD."""


def parse_week(text):
    """'2026-03-02' -> date(2026, 3, 2). Raises WeekFormatError."""
    try:
        return datetime.strptime(str(text).strip(), "%Y-%m-%d").date()
    except ValueError:
        raise WeekFormatError("expected YYYY-MM-DD, got %r" % (text,))


def week_start_utc(day):
    """Normalise a date to the Monday 00:00 UTC that opens its working week."""
    if isinstance(day, datetime):
        day = day.date()
    moment = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return moment - timedelta(days=moment.weekday())


def comfort_points(minute_of_day):
    """How reasonable is a meeting that starts at this local time?"""
    if WORK_START <= minute_of_day < WORK_END:
        return COMFORT_WORK
    if EDGE_EARLY <= minute_of_day < WORK_START or WORK_END <= minute_of_day < EDGE_LATE:
        return COMFORT_EDGE
    return COMFORT_NIGHT


def coverage_points(member):
    """A required attendee is worth twice as much as an optional one."""
    return COVERAGE_BONUS if member.required else OPTIONAL_BONUS


def score_slot(members, utc_dt):
    """Total points for a meeting that starts at ``utc_dt`` with ``members`` attending."""
    total = 0
    for member in members:
        total += coverage_points(member)
        moment = member.local(utc_dt)
        points = comfort_points(moment.hour * 60 + moment.minute)
        if moment.weekday() >= 5:
            points = max(0, points - WEEKEND_PENALTY)
        total += points
    return total


def scan(roster, week_start):
    """Return one Slot per half-hour cell of the UTC working week."""
    start = week_start_utc(week_start)
    slots = []
    for index in range(SLOTS_PER_WEEK):
        moment = start + timedelta(minutes=index * SLOT_MINUTES)
        free = [member for member in roster if member.available_at(moment)]
        slots.append(Slot(moment, free, score_slot(free, moment)))
    return slots


def find_segments(slots):
    """Group consecutive cells that share exactly the same set of free members.

    This is run-length encoding: instead of 240 cells we end up with a handful of
    labelled stretches, which is what "3 hours of overlap" really means.
    """
    segments, current = [], []
    for slot in slots:
        if current and slot.members != current[-1].members:
            segments.append(Segment(current, current[-1].members))
            current = []
        current.append(slot)
    if current:
        segments.append(Segment(current, current[-1].members))
    return [segment for segment in segments if segment.count]


def roster_order(members, roster):
    """Sort a set of members into roster order.

    Membership is kept as a frozenset while scanning, and a frozenset has no
    meaningful order -- without this the same team would list its members
    differently on every run.
    """
    return tuple(member for member in roster if member in members)


def shortlist(roster, segments, duration_min, top=3):
    """Rank the best places to put a ``duration_min`` meeting in ``segments``.

    Split out from ``recommend`` so ``plan`` can scan the week once and reuse the
    result. ``recommend`` still exists for callers that only have a roster.

    The number of cells a meeting needs is rounded *up*: a 45-minute meeting
    still needs two 30-minute cells, otherwise it could start in the last cell of
    a segment and run out of room.
    """
    if duration_min <= 0:
        raise ValueError("meeting length must be at least 1 minute, got %r"
                         % (duration_min,))
    need = max(1, (int(duration_min) + SLOT_MINUTES - 1) // SLOT_MINUTES)
    candidates = []

    for segment in segments:
        if segment.duration_min < duration_min:
            continue
        # the start slides within the segment: position matters, because a later
        # start often lands on a more comfortable local hour
        window = segment.slots[:len(segment.slots) - need + 1]
        chosen = max(range(len(window)),
                     key=lambda index: (window[index].score, -index))
        members = roster_order(segment.members, roster)
        candidates.append(Recommendation(
            start=window[chosen].start,
            duration_min=duration_min,
            members=members,
            score=window[chosen].score,
            continuous_min=segment.duration_min,
            missing=[member for member in roster if member not in segment.members]))

    # full attendance first, then more people, then a better score, then earlier
    ranked = sorted(candidates,
                    key=lambda item: (not item.required_ok, -item.count,
                                      -item.score, item.start))

    # the same UTC clock time recurs on several weekdays; keep the best day
    seen, unique = set(), []
    for item in ranked:
        stamp = item.start.strftime("%H:%M")
        if stamp in seen:
            continue
        seen.add(stamp)
        unique.append(item)
    return unique[:top]


def recommend(roster, week_start, duration_min, top=3):
    """Rank the best places to put a ``duration_min`` meeting inside the week."""
    return shortlist(roster, find_segments(scan(roster, week_start)),
                     duration_min, top)


def plan(roster, week_start, duration_min, top=3):
    """Run the whole pipeline: scan once -> segments -> shortlist."""
    start = week_start_utc(week_start)
    slots = scan(roster, start)
    segments = find_segments(slots)
    return Plan(roster, start, duration_min, slots, segments,
                shortlist(roster, segments, duration_min, top))


def rank_plan(item):
    """How good is the best slot of a plan? Higher is better.

    Used to choose one week out of a multi-week scan: a week where every required
    member can come beats a week with more attendees but a missing required one.
    """
    best = item.best
    if best is None:
        return (-1, -1, -1)
    return (1 if best.required_ok else 0, best.count, best.score)


def best_week(plans):
    """The plan with the best top recommendation, or None for an empty list."""
    chosen = None
    for item in plans:
        if chosen is None or rank_plan(item) > rank_plan(chosen):
            chosen = item
    return chosen


def plan_weeks(roster, week_start, duration_min, weeks=4, top=1):
    """Plan several consecutive weeks -- "is next month better than this one?".

    This is a plain loop over ``plan``: every week gets its own scan, which is
    what makes daylight saving changes show up honestly instead of being
    approximated.
    """
    start = week_start_utc(week_start)
    count = max(1, int(weeks))
    return [plan(roster, start + timedelta(weeks=index), duration_min, top)
            for index in range(count)]


def duration_options(roster, week_start, lengths=DEFAULT_LENGTHS, top=1):
    """``[(minutes, Plan), ...]`` -- which meeting lengths actually fit?

    When a 90-minute meeting is impossible, this says so *and* shows the longest
    length that still works, instead of leaving the user with an empty screen.
    """
    options = []
    for length in lengths:
        options.append((int(length), plan(roster, week_start, int(length), top)))
    return options


def explain(roster, moment):
    """One AvailabilityCheck per member: local time, free?, which window, away?"""
    checks = []
    for member in roster:
        local = member.local(moment)
        away = local.date() in member.away
        window = member.window_at(local)
        block = member.busy_at(moment)
        checks.append(AvailabilityCheck(
            member, local,
            free=(window is not None and not away and block is None),
            window=window, away=away, busy=block))
    return checks


def offset_hours(zone, day):
    """UTC offset of ``zone`` at noon on ``day``, in hours (e.g. 10.0, 5.5).

    Noon is far from every real transition, so a normal day gives one stable
    answer and the two days around a switch give the two different ones.
    """
    moment = datetime(day.year, day.month, day.day, 12, 0,
                      tzinfo=timezone.utc).astimezone(zone)
    return moment.utcoffset().total_seconds() / 3600.0


class OffsetChange:
    """A daylight saving switch that happened inside a multi-week scan."""

    def __init__(self, member, day, before_hours, after_hours):
        self.member = member
        self.day = day
        self.before_hours = before_hours
        self.after_hours = after_hours

    def __repr__(self):
        return "OffsetChange(%r, %s, %+g -> %+g)" % (
            self.member.name, self.day.isoformat(),
            self.before_hours, self.after_hours)


def offset_changes(roster, week_start, weeks=4):
    """Find every DST switch inside a multi-week scan, day by day.

    Checking one noon per day is enough: an offset can only change twice a year,
    and never twice inside a single day. Reporting the exact day matters -- "New
    York changes on 8 March" is a much better answer than "the Monday after".
    """
    start = week_start_utc(week_start)
    last_day = 7 * max(1, int(weeks)) - 1
    changes = []
    for member in roster:
        previous = None
        for index in range(last_day + 1):
            day = start.date() + timedelta(days=index)
            current = offset_hours(member.tz, day)
            if previous is not None and current != previous:
                changes.append(OffsetChange(member, day, previous, current))
            previous = current
    return changes


class Plan:
    """Everything one compute run produces, ready to be rendered."""

    def __init__(self, roster, week_start, duration_min, slots, segments,
                 recommendations):
        self.roster = tuple(roster)
        self.week_start = week_start          # Monday 00:00 UTC
        self.duration_min = duration_min
        self.slots = slots
        self.segments = segments
        self.recommendations = recommendations

    @property
    def best(self):
        """Top recommendation, or None when nobody is free at the same time."""
        return self.recommendations[0] if self.recommendations else None

    @property
    def best_continuous_min(self):
        """Longest stretch in which one fixed group stays free together."""
        return max((segment.duration_min for segment in self.segments), default=0)

    @property
    def max_score(self):
        """The best score any slot could reach for this roster."""
        total = 0
        for member in self.roster:
            total += coverage_points(member) + COMFORT_WORK
        return total

    @property
    def required_count(self):
        return sum(1 for member in self.roster if member.required)

    def hourly_rows(self):
        """[(date, [24 hour-by-hour counts])] -- the data behind the week grid.

        Each hour shows the best coverage seen in either of its two half-hour
        cells, so a 30-minute window is never hidden by averaging it away.
        """
        rows = []
        for day in range(len(self.slots) // SLOTS_PER_DAY):
            base = day * SLOTS_PER_DAY
            counts = [max(self.slots[base + hour * 2].count,
                          self.slots[base + hour * 2 + 1].count)
                      for hour in range(24)]
            rows.append((self.slots[base].start.date(), counts))
        return rows


def demo_week():
    """The Monday used by the README examples."""
    return datetime(2026, 3, 2).date()
