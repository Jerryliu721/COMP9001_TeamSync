# -*- coding: utf-8 -*-
"""Import busy time from an iCalendar (.ics) file.

Why this feature exists: every student already keeps their life in a calendar.
Exporting the week as an .ics file and handing it to TeamSync is far less work
than retyping every lecture, shift and dentist appointment as an availability
window.  The imported events become *busy blocks* on that member: the planner
then treats those minutes as unavailable, even if they fall inside a free
window.

The parser is deliberately small.  It understands the parts of RFC 5545 that
real calendar exports actually use:

    * line unfolding (a long line continued with a leading space)
    * one-off VEVENTs with DTSTART / DTEND
    * UTC values (...Z), TZID=... values and floating values
    * RRULE FREQ=DAILY / FREQ=WEEKLY with INTERVAL, COUNT, UNTIL and BYDAY
    * SUMMARY, used as the title shown in "Explain a time"

and it skips, with an explicit warning, the parts it does not:

    * all-day events (VALUE=DATE) -- use an away date for those instead
    * monthly and yearly repeats
    * events without a DTEND

Warnings are returned as a list of strings instead of being printed, so the GUI
can show them in a dialog and the tests can check them.
"""

import os
from datetime import datetime, timedelta, timezone

from cities import load_tz
from models import BusyBlock, TeamSyncError

WEEKDAY_CODES = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
MAX_BLOCKS = 1000


class IcsError(TeamSyncError):
    """Raised when a file is not a usable iCalendar file at all."""


def unfold(text):
    """RFC 5545 line unfolding: a line starting with a space continues the last."""
    lines = []
    for raw in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def split_property(line):
    """'DTSTART;TZID=Europe/London:20260302T090000' -> (name, params, value)."""
    if ":" not in line:
        return "", {}, ""
    left, _, value = line.partition(":")
    parts = left.split(";")
    name = parts[0].strip().upper()
    params = {}
    for item in parts[1:]:
        if "=" in item:
            key, _, val = item.partition("=")
            params[key.strip().upper()] = val.strip().strip('"')
    return name, params, value.strip()


def parse_ics_datetime(value, params, label, warnings):
    """Return ``(aware_datetime, zone)`` for a DTSTART/DTEND value, or (None, None).

    The datetime keeps the event's own zone, which matters for repeating events:
    a 09:00 London lecture stays at 09:00 London when the clocks change, so the
    expansion below has to work in local wall time and convert to UTC only at
    the end.
    """
    if params.get("VALUE", "").upper() == "DATE":
        warnings.append("%s is an all-day event; use an away date instead" % label)
        return None, None
    stamp = value.strip()
    if stamp.endswith("Z"):
        stamp = stamp[:-1]
        zone = timezone.utc
    else:
        tzid = params.get("TZID")
        if tzid:
            try:
                zone = load_tz(tzid)
            except TeamSyncError:
                warnings.append("%s uses unknown timezone %r; assumed UTC"
                                % (label, tzid))
                zone = timezone.utc
        else:
            warnings.append("%s has no timezone; assumed UTC" % label)
            zone = timezone.utc
    pattern = "%Y%m%dT%H%M%S" if len(stamp) >= 15 else "%Y%m%dT%H%M"
    try:
        naive = datetime.strptime(stamp, pattern)
    except ValueError:
        warnings.append("%s has an unreadable date %r" % (label, value))
        return None, None
    return naive.replace(tzinfo=zone), zone


def parse_rrule(value):
    """'FREQ=WEEKLY;BYDAY=MO,WE;COUNT=10' -> {'FREQ': 'WEEKLY', ...}."""
    rule = {}
    for chunk in str(value).split(";"):
        if "=" in chunk:
            key, _, val = chunk.partition("=")
            rule[key.strip().upper()] = val.strip()
    return rule


def parse_until(value):
    """UNTIL -> aware UTC datetime, or None when absent/unreadable."""
    if not value:
        return None
    stamp = value.strip()
    if len(stamp) == 8:                                   # 20260401
        try:
            return datetime.strptime(stamp, "%Y%m%d").replace(
                tzinfo=timezone.utc, hour=23, minute=59)
        except ValueError:
            return None
    if stamp.endswith("Z"):
        stamp = stamp[:-1]
    try:
        return datetime.strptime(stamp, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_byday(value):
    """'MO,WE' -> [0, 2].  Ordinals such as '1MO' are accepted and ignored."""
    days = []
    for chunk in str(value).split(","):
        code = chunk.strip().upper()[-2:]
        if code in WEEKDAY_CODES and WEEKDAY_CODES[code] not in days:
            days.append(WEEKDAY_CODES[code])
    return days


def rule_number(rule, key, default):
    try:
        return max(1, int(rule.get(key, default)))
    except (TypeError, ValueError):
        return default


def repeated_blocks(start, end, rule, window_start, window_end, title, label,
                    warnings):
    """Expand FREQ=DAILY / FREQ=WEEKLY into BusyBlocks inside the window.

    ``start``/``end`` are aware datetimes in the event's own zone.  Every
    occurrence is converted to UTC separately, so a weekly 09:00 lecture keeps
    its local time across a daylight saving switch.
    """
    frequency = rule.get("FREQ", "").upper()
    if frequency not in ("DAILY", "WEEKLY"):
        warnings.append("%s repeats %s; only daily and weekly repeats are imported"
                        % (label, frequency or "an unknown way"))
        return []

    zone = start.tzinfo
    wall_time = start.timetz()
    duration = end - start
    interval = rule_number(rule, "INTERVAL", 1)
    limit = None
    if "COUNT" in rule:
        try:
            limit = int(rule["COUNT"])
        except ValueError:
            limit = None
    until = parse_until(rule.get("UNTIL"))
    blocks = []
    index = 0

    if frequency == "DAILY":
        day = start.date()
        while day <= window_end.date() and len(blocks) < MAX_BLOCKS:
            index += 1
            if limit is not None and index > limit:
                break
            moment = datetime.combine(day, wall_time).replace(tzinfo=zone)
            moment = moment.astimezone(timezone.utc)
            if until is not None and moment > until:
                break
            if moment + duration > window_start and moment < window_end:
                blocks.append(BusyBlock(moment, moment + duration, title))
            if moment >= window_end:
                break
            day += timedelta(days=interval)
        return blocks

    byday = parse_byday(rule.get("BYDAY", "")) or [start.weekday()]
    week_monday = start.date() - timedelta(days=start.weekday())
    while week_monday <= window_end.date() and len(blocks) < MAX_BLOCKS:
        for weekday in sorted(byday):
            day = week_monday + timedelta(days=weekday)
            if day < start.date():
                continue                          # before DTSTART: not an occurrence
            index += 1
            if limit is not None and index > limit:
                return blocks
            moment = datetime.combine(day, wall_time).replace(tzinfo=zone)
            moment = moment.astimezone(timezone.utc)
            if until is not None and moment > until:
                return blocks
            if moment + duration > window_start and moment < window_end:
                blocks.append(BusyBlock(moment, moment + duration, title))
            if moment >= window_end:
                return blocks
        week_monday += timedelta(weeks=interval)
    return blocks


def event_blocks(properties, window_start, window_end, warnings):
    """Turn one parsed VEVENT (a dict of properties) into BusyBlocks."""
    title = properties.get("SUMMARY", ("", ""))[1] or "Busy"
    label = "event %r" % (title,)

    if "DTSTART" not in properties:
        warnings.append("%s has no DTSTART and was skipped" % label)
        return []
    params, value = properties["DTSTART"]
    start, _ = parse_ics_datetime(value, params, label, warnings)
    if start is None:
        return []
    if "DTEND" not in properties:
        warnings.append("%s has no DTEND and was skipped" % label)
        return []
    params, value = properties["DTEND"]
    end, _ = parse_ics_datetime(value, params, label, warnings)
    if end is None:
        return []
    if end <= start:
        warnings.append("%s ends before it starts and was skipped" % label)
        return []

    if "RRULE" not in properties:
        return [BusyBlock(start.astimezone(timezone.utc),
                          end.astimezone(timezone.utc), title)]

    rule = parse_rrule(properties["RRULE"][1])
    return repeated_blocks(start, end, rule, window_start, window_end,
                           title, label, warnings)


def parse_events(text, window_start, window_end):
    """Parse iCalendar text -> ``(blocks, warnings)``.

    ``window_start`` / ``window_end`` are aware UTC datetimes.  One-off events
    are always kept (so the team file still knows about them later); repeats are
    expanded only inside that window, which keeps a weekly lecture from turning
    into thousands of JSON lines.
    """
    if "BEGIN:VCALENDAR" not in str(text).upper():
        raise IcsError("this file does not look like an iCalendar (.ics) file")

    blocks, warnings, properties = [], [], {}
    inside = False
    events = 0
    for line in unfold(text):
        upper = line.strip().upper()
        if upper.startswith("BEGIN:VEVENT"):
            inside, properties = True, {}
            continue
        if upper.startswith("END:VEVENT"):
            inside = False
            events += 1
            blocks.extend(event_blocks(properties, window_start, window_end,
                                       warnings))
            continue
        if not inside or not line.strip():
            continue
        name, params, value = split_property(line)
        if name:
            properties[name] = (params, value)

    if events == 0:
        raise IcsError("the file has no VEVENT entries to import")

    # the same event can appear twice after a re-export; keep one copy
    seen, unique = set(), []
    for block in blocks:
        key = (block.start, block.end, block.title)
        if key in seen:
            continue
        seen.add(key)
        unique.append(block)
    unique.sort(key=lambda block: block.start)
    return unique, warnings


def read_ics(path, window_start, window_end):
    """Read ``path`` and return ``(blocks, warnings)``."""
    if not os.path.isfile(path):
        raise IcsError("cannot find the calendar file: %s" % path)
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError as exc:
        raise IcsError("cannot read %s: %s" % (path, exc))
    return parse_events(text, window_start, window_end)
