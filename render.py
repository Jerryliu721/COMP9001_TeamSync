# -*- coding: utf-8 -*-
"""Pure presentation helpers: model objects in, strings out.

Nothing here prints, opens a file or touches a window, so the very same helpers
serve the command line, the GUI and the exported report -- and they are trivial to
unit-test.

The output is deliberately plain ASCII. The Windows console defaults to code page
936, where box-drawing characters come out as mojibake, so the grid uses "." for an
empty cell and "[4]" for a full house. The GUI, which owns real Unicode strings,
is free to do better.
"""

import planner
from cities import load_tz
from models import WEEKDAY_NAMES


def slot_tag(local_minutes):
    """'  <- late' / '  <- early' / '' for one member's local starting time."""
    if local_minutes >= 22 * 60:
        return "  <- late"
    if local_minutes < 7 * 60:
        return "  <- early"
    return ""


def offset_text(hours):
    """10.0 -> 'UTC+10'; -5.0 -> 'UTC-5'; 5.5 -> 'UTC+5:30'."""
    sign = "+" if hours >= 0 else "-"
    whole = int(abs(hours))
    minutes = int(round((abs(hours) - whole) * 60))
    if minutes:
        return "UTC%s%d:%02d" % (sign, whole, minutes)
    return "UTC%s%d" % (sign, whole)


def member_rows(roster):
    """The data behind the members tab: one dictionary per member.

    Dictionaries (rather than a seven-item tuple) keep the two front ends from
    having to remember what position each column is.
    """
    rows = []
    for member in roster:
        rows.append({
            "name": member.name,
            "city": member.city,
            "tzkey": member.tzkey,
            "role": "required" if member.required else "optional",
            "hours": "%.1f" % (member.weekly_minutes / 60.0),
            "away": member.away_text(),
            "busy": member.busy_text(),
            "windows": member.describe_windows(),
        })
    return rows


def members_text(roster):
    """The table printed by the command line and used in the report."""
    lines = ["%-10s %-12s %-19s %-9s %5s %-9s %s"
             % ("Name", "City", "Timezone", "Role", "Hrs", "Busy", "Local windows"),
             "-" * 110]
    for row in member_rows(roster):
        lines.append("%-10s %-12s %-19s %-9s %5s %-9s %s"
                     % (row["name"], row["city"], row["tzkey"], row["role"],
                        row["hours"], row["busy"], row["windows"]))
    away = ["%s %s" % (row["name"], row["away"])
            for row in member_rows(roster) if row["away"] != "-"]
    if away:
        lines.append("")
        lines.append("Away (local dates): %s" % "; ".join(away))
    optional = [row["name"] for row in member_rows(roster)
                if row["role"] == "optional"]
    if optional:
        lines.append("Optional members: %s" % ", ".join(optional))
    return "\n".join(lines)


def grid_text(plan, dot="."):
    """The week grid: UTC hours across the top, UTC days down the side.

        Mon 03-02    .  1. 1. 1. .  .  .  .  1. 2. 3.[4] 3. 3. 1. .  ...

    Each hour shows the best coverage of its two half-hour cells, so a 30-minute
    window is never averaged away.
    """
    total = len(plan.roster)
    lines = [" " * 12 + "".join(" %-2d" % hour for hour in range(24))]
    for day, counts in plan.hourly_rows():
        cells = "".join(
            "%3s" % ("[%d]" % total if count == total
                     else ("%2d%s" % (count, dot) if count else " %s " % dot))
            for count in counts)
        lines.append("%-10s  %s" % ("%s %s" % (WEEKDAY_NAMES[day.weekday()],
                                              day.strftime("%m-%d")), cells))
    return "\n".join(lines)


def cards_text(plan, max_score=None):
    """The recommendation cards, ranked best first."""
    total = len(plan.roster)
    if max_score is None:
        max_score = plan.max_score
    lines = []
    for rank, item in enumerate(plan.recommendations, 1):
        lines.append("  -- Recommendation %d | %d/%d members | score %d/%d"
                     " | continuous %.1f h --"
                     % (rank, item.count, total, item.score, max_score,
                        item.continuous_hours))
        lines.append("     Meet at %s UTC  (%d min)"
                     % (item.start.strftime("%Y-%m-%d (%a) %H:%M"), item.duration_min))
        for member in item.members:
            moment = member.local(item.start)
            lines.append("       + %-6s %-10s %-8s %s %02d:%02d %s%s"
                         % (member.name, member.city,
                            "" if member.required else "optional",
                            moment.strftime("%a"),
                            moment.hour, moment.minute, moment.strftime("%Z"),
                            slot_tag(moment.hour * 60 + moment.minute)))
        if item.missing:
            lines.append("       - missing: %s"
                         % ", ".join("%s (%s)" % (
                             member.name,
                             "required" if member.required else "optional")
                             for member in item.missing))
        else:
            lines.append("       + every member can attend")
        lines.append("")
    return "\n".join(lines)


def invite_text(plan, team_name="TeamSync"):
    """A ready-to-paste invitation for the top recommendation.

    Pure text on purpose: the GUI copies it to the clipboard, but it can also be
    printed by the command line or dropped into a test without a window.
    """
    best = plan.best
    if best is None:
        return "%s: no meeting slot fits this week." % team_name
    lines = ["%s - meeting time" % team_name,
             "%s UTC (%d minutes)"
             % (best.start.strftime("%a %d %b %Y, %H:%M"), best.duration_min),
             "%d/%d members can attend" % (best.count, len(plan.roster)),
             ""]
    for member in best.members:
        moment = member.local(best.start)
        lines.append("  %-8s %s %02d:%02d  (%s)"
                     % (member.name, moment.strftime("%a"), moment.hour,
                        moment.minute, member.city))
    if best.missing:
        lines.append("")
        lines.append("Cannot attend: %s"
                     % ", ".join(member.name for member in best.missing))
    lines.append("")
    lines.append("Reply OK if this works for you.")
    return "\n".join(lines)


def explain_text(checks, moment):
    """The per-member breakdown shown by 'Explain a time'."""
    lines = ["  Check %s UTC -- who can meet then?"
             % moment.strftime("%Y-%m-%d (%a) %H:%M"), "",
             "  %-10s %-12s %-9s %-16s %-12s %-30s %s"
             % ("Name", "City", "Free", "Local time", "UTC offset",
                "Window", "Note")]
    lines.append("  " + "-" * 104)
    for check in checks:
        local = check.local
        if check.free:
            verdict = "yes"
        elif check.away:
            verdict = "AWAY"
        elif check.busy is not None:
            verdict = "BUSY"
        else:
            verdict = "no"
        if check.busy is not None:
            note = "busy: %s" % check.busy.title
        elif check.away:
            note = "away"
        elif not check.free:
            note = "outside window"
        else:
            note = ""
        lines.append("  %-10s %-12s %-9s %-16s %-12s %-30s %s"
                     % (check.member.name, check.member.city, verdict,
                        local.strftime("%a %H:%M"),
                        offset_text(local.utcoffset().total_seconds() / 3600.0),
                        check.window_text, note))
    free = sum(1 for check in checks if check.free)
    lines.append("")
    lines.append("  %d of %d members are free at that moment." % (free, len(checks)))
    return "\n".join(lines)


def weeks_text(plans):
    """A multi-week comparison table plus any daylight saving switches."""
    lines = [" Week of      Best time (UTC)        Cover  Score     Note",
             " " + "-" * 74]
    roster = plans[0].roster if plans else ()
    for item in plans:
        best = item.best
        if best is None:
            lines.append(" %-12s %-22s %-6s %-9s %s"
                         % (item.week_start.strftime("%Y-%m-%d"), "-", "-", "-",
                            "no shared free time"))
            continue
        note = "all required present" if best.required_ok else "required member missing"
        lines.append(" %-12s %-22s %-6s %-9s %s"
                     % (item.week_start.strftime("%Y-%m-%d"),
                        best.start.strftime("%a %H:%M"),
                        "%d/%d" % (best.count, len(roster)),
                        "%d/%d" % (best.score, item.max_score), note))
    changes = []
    if plans:
        changes = planner.offset_changes(roster, plans[0].week_start, len(plans))
    if changes:
        lines.append("")
        lines.append(" Daylight saving changes in this range:")
        for change in changes:
            lines.append("   %s: %s -> %s on %s"
                         % (change.member.city,
                            offset_text(change.before_hours),
                            offset_text(change.after_hours),
                            change.day.strftime("%Y-%m-%d")))
    return "\n".join(lines)


def durations_text(options, requested=None):
    """Which meeting lengths fit, e.g. when 90 minutes is simply impossible."""
    lines = [" Length   Best time (UTC)        Cover  Score",
             " " + "-" * 52]
    fitting = []
    partial = False
    for length, item in options:
        best = item.best
        if best is None:
            lines.append(" %4d min  %-22s %-6s %s"
                         % (length, "-", "-", "no slot long enough"))
            continue
        fitting.append(length)
        mark = ""
        if not best.required_ok:
            mark = "*"
            partial = True
        lines.append(" %4d min  %-22s %-6s %d/%d%s"
                     % (length, best.start.strftime("%a %H:%M"),
                        "%d/%d" % (best.count, len(item.roster)),
                        best.score, item.max_score, mark))

    lines.append("")
    if not fitting:
        lines.append(" No length works this week -- try the multi-week scan.")
    else:
        best_of_all = None
        for length, item in options:
            if length == requested:
                best_of_all = item.best
        if requested is not None and requested not in fitting:
            lines.append(" %d min is not possible this week; the longest length that"
                         " fits is %d min." % (requested, max(fitting)))
        elif best_of_all is None:
            lines.append(" Longest length that fits: %d min." % max(fitting))
        elif best_of_all.required_ok:
            lines.append(" %d min fits with every required member present."
                         % requested)
        else:
            lines.append(" %d min fits, but only %d of %d members can attend."
                         % (requested, best_of_all.count, len(options[0][1].roster)))
    if partial:
        lines.append(" * = a required member cannot attend that time.")
    return "\n".join(lines)


def report_text(plan, team_name, stamp=None):
    """The full plain-text report, shared by --cli and the .txt export."""
    rule = "=" * 66
    required = plan.required_count
    total = len(plan.roster)
    lines = [rule,
             "TeamSync - Best meeting times",
             "Team: %s" % team_name,
             "Members: %d (%d required, %d optional)"
             % (total, required, total - required),
             "Week of: %s (Mon-Fri)" % plan.week_start.strftime("%Y-%m-%d"),
             "Meeting length: %d min" % plan.duration_min,
             "All times shown as UTC unless stated",
             rule,
             grid_text(plan),
             "",
             "Recommendations",
             "-" * 66]

    if plan.recommendations:
        lines.append(cards_text(plan))
    else:
        lines.append("No slot is long enough for a %d min meeting this week."
                     % plan.duration_min)
        lines.append("The longest window in which one group stays free together is"
                     " %.1f h -- try a shorter meeting or another week."
                     % (plan.best_continuous_min / 60.0))

    footer = "Generated locally by TeamSync (offline, no network)."
    if stamp:
        footer = "Generated %s by TeamSync (offline, no network)." % stamp
    lines += ["", "-" * 66, footer]
    return "\n".join(lines) + "\n"


def reference_text(plan, tzkey, label):
    """The same recommendations again, seen from one chosen timezone.

    Useful when somebody outside the roster needs the time, and a quick way to
    sanity-check that the UTC answer really is the time you think it is.
    """
    zone = load_tz(tzkey)
    lines = ["", "The same times seen from %s (%s)" % (label, tzkey), "-" * 66]
    if not plan.recommendations:
        lines.append("  (no recommendation to convert)")
        return "\n".join(lines)
    for rank, item in enumerate(plan.recommendations, 1):
        moment = item.start.astimezone(zone)
        lines.append("  #%d  %s  %s" % (rank, moment.strftime("%a %Y-%m-%d %H:%M"), label))
    return "\n".join(lines)
