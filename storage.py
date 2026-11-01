# -*- coding: utf-8 -*-
"""Everything in TeamSync that touches disk.

Two halves:

* reading a team file (``data/demo_team.json`` or any file in the same shape);
* writing results -- ``output/latest.json`` on every compute, plus the TXT, ICS,
  HTML and CSV exports the user asks for.

The three newline traps live here and nowhere else:

    .txt    newline="\\r\\n"  turns our "\\n" into CRLF without doubling it
    .ics    newline=""       we emit CRLF ourselves, otherwise Windows silently
                             rewrites "\\r\\n" into "\\r\\r\\n" and calendars refuse
                             the file
    .html   newline="\\n"     a web file is happier with plain LF
    .csv    newline=""       the csv module writes its own line endings
"""

import csv
import html
import json
import os
from datetime import datetime, timedelta, timezone

from cities import load_tz, lookup, make_member
from models import (Availability, BusyBlock, Member, TeamSyncError,
                    format_iso_date, parse_date_list, parse_days, parse_iso_date,
                    parse_windows)
from planner import COMFORT_WORK, coverage_points, parse_week
from render import report_text

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(HERE, "output")

ICS_FORMAT = "%Y%m%dT%H%M%SZ"


def utc_stamp():
    """'2026-09-20T04:23:01Z' -- the moment a report was produced."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_output_dir(folder=OUTPUT_DIR):
    """Create the output folder if it is not there yet, and return its path."""
    os.makedirs(folder, exist_ok=True)
    return folder


def meeting_filename(plan, extension, folder=OUTPUT_DIR):
    """output/meeting_2026-03-02.txt -- one name per week, so exports overwrite."""
    return os.path.join(folder, "meeting_%s.%s"
                        % (plan.week_start.strftime("%Y-%m-%d"), extension))


def max_score(roster):
    """The best score a slot could possibly reach, shown in the calendar note."""
    total = 0
    for member in roster:
        total += coverage_points(member) + COMFORT_WORK
    return total


def load_team(path):
    """Read a team file and return ``(team_name, week_date, duration_min, roster)``.

    Every member's timezone file is loaded right away, so a missing or broken data
    file fails here with a clear message instead of halfway through a scan.
    """
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)

    for field in ("week", "duration_min", "members"):
        if field not in data:
            raise TeamSyncError("the team file is missing the %r field" % field)
    if not isinstance(data["members"], list):
        raise TeamSyncError("the team file's 'members' field must be a list")

    roster = []
    for entry in data["members"]:
        if "name" not in entry or "availability" not in entry:
            raise TeamSyncError("every member needs a name and an availability list")
        tzkey = entry.get("tzkey")
        if not tzkey:
            _, tzkey = lookup(entry["city"])
        load_tz(tzkey)
        roster.append(Member(
            entry["name"], entry.get("city", ""), tzkey,
            [Availability.from_dict(item) for item in entry["availability"]],
            required=entry.get("required", True),
            away=[parse_iso_date(item) for item in entry.get("away", [])],
            busy=[BusyBlock.from_dict(item) for item in entry.get("busy", [])]))

    return (data.get("team", "TeamSync"),
            parse_week(data["week"]),
            int(data["duration_min"]),
            roster)


def save_team(path, team_name, week, duration_min, roster):
    """Write a team file that ``load_team`` can read straight back."""
    payload = {"team": team_name,
               "week": week.strftime("%Y-%m-%d"),
               "duration_min": int(duration_min),
               "members": [member.to_dict() for member in roster]}
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return path


def load_roster_csv(path):
    """Read members from a spreadsheet and return ``(members, warnings)``.

    The file is a normal CSV with a header row.  Only ``name``, ``city`` and
    ``windows`` are required; ``role``, ``days`` and ``away`` have sensible
    defaults::

        name,city,role,days,windows,away
        Alice,Sydney,required,Mon-Fri,19:00-23:00,
        Bo,Beijing,required,Mon-Fri,09:00-12:00; 18:00-22:00,
        Eve,Singapore,optional,Mon-Fri,08:00-20:00,2026-03-04

    A broken row is skipped with a warning instead of failing the whole import,
    so one typo in a 30-person roster does not send the user back to Excel.
    """
    members, warnings = [], []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise TeamSyncError("the roster CSV is empty: %s" % path)
        columns = [str(name).strip().lower() for name in reader.fieldnames]
        for needed in ("name", "city", "windows"):
            if needed not in columns:
                raise TeamSyncError("the roster CSV needs a %r column" % needed)

        for line, row in enumerate(reader, 2):
            values = {}
            for key, value in row.items():
                if key is not None:
                    values[str(key).strip().lower()] = (value or "").strip()
            if not values.get("name"):
                if any(values.values()):
                    warnings.append("line %d has no name and was skipped" % line)
                continue
            try:
                role = values.get("role", "").lower()
                required = role not in ("optional", "no", "n", "false", "0")
                days = parse_days(values.get("days", "Mon-Fri").split())
                windows = parse_windows(
                    values.get("windows", "").replace(";", "+").replace("|", "+"),
                    days)
                away = parse_date_list(values.get("away", ""))
                members.append(make_member(values["name"], values["city"], windows,
                                           required=required, away=away))
            except (TeamSyncError, ValueError, KeyError) as exc:
                warnings.append("line %d: %s" % (line, exc))

    if not members:
        raise TeamSyncError("no usable member rows found in %s"
                            % os.path.basename(path))
    return members, warnings


def member_json(member):
    """The full machine-readable shape of one member (defaults included)."""
    return {
        "name": member.name,
        "city": member.city,
        "tzkey": member.tzkey,
        "required": member.required,
        "away": [format_iso_date(day) for day in sorted(member.away)],
        "busy": [block.to_dict() for block in member.busy],
        "availability": [window.to_dict() for window in member.windows],
    }


def save_json(path, plan, team_name):
    """Machine-readable snapshot, rewritten on every compute."""
    payload = {
        "team": team_name,
        "week": plan.week_start.strftime("%Y-%m-%d"),
        "duration_min": plan.duration_min,
        "generated_utc": utc_stamp(),
        "required_members": plan.required_count,
        "max_score": plan.max_score,
        "members": [member_json(member) for member in plan.roster],
        "recommendations": [
            {
                "rank": rank,
                "start_utc": item.start.strftime("%Y-%m-%dT%H:%MZ"),
                "cover": item.count,
                "total": item.total,
                "required_ok": item.required_ok,
                "missing": [member.name for member in item.missing],
                "score": item.score,
                "continuous_h": round(item.continuous_hours, 1),
                "members": [
                    {
                        "name": member.name,
                        "city": member.city,
                        "required": member.required,
                        "local": member.local(item.start).strftime("%a %H:%M %Z"),
                        "warning": ("late" if member.local(item.start).hour >= 22
                                    else "early" if member.local(item.start).hour < 7
                                    else ""),
                    }
                    for member in item.members
                ],
            }
            for rank, item in enumerate(plan.recommendations, 1)
        ],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    return path


def save_txt(path, plan, team_name):
    """Plain ASCII report -- the format that survives being pasted into a chat."""
    with open(path, "w", encoding="utf-8", newline="\r\n") as handle:
        handle.write(report_text(plan, team_name, stamp=utc_stamp()))
    return path


def fold(line):
    """Fold a line longer than 75 bytes, as RFC 5545 demands.

    Continuation lines start with a single space. The count is kept in UTF-8 bytes
    and characters are appended one at a time, so a multi-byte character is never
    sliced in half.
    """
    if len(line.encode("utf-8")) <= 75:
        return line
    parts, current, used = [], "", 0
    for char in line:
        size = len(char.encode("utf-8"))
        if used + size > 75:
            parts.append(current)
            current, used = " ", 1
        current += char
        used += size
    parts.append(current)
    return "\r\n".join(parts)


def escape_ics(text):
    """Escape the four characters iCalendar treats specially."""
    return (text.replace("\\", "\\\\").replace(";", "\\;")
                .replace(",", "\\,").replace("\n", "\\n"))


def save_ics(path, plan, team_name):
    """Calendar file: one VEVENT per recommendation, published in UTC."""
    stamp = datetime.now(timezone.utc).strftime(ICS_FORMAT)
    total = len(plan.roster)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0",
             "PRODID:-//TeamSync//Meeting Planner 3.0//EN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]

    for rank, item in enumerate(plan.recommendations, 1):
        end = item.start + timedelta(minutes=item.duration_min)
        if item.required_ok:
            attendance = ("Attendance: %d/%d, all required members present"
                          % (item.count, total))
        else:
            attendance = "Attendance: %d/%d, MISSING REQUIRED: %s" % (
                item.count, total,
                ", ".join(member.name for member in item.missing_required))
        body = ["TeamSync best slot %d of %d" % (rank, len(plan.recommendations)),
                "Team: " + team_name, attendance, ""]
        for member in item.members:
            moment = member.local(item.start)
            body.append("%s (%s)%s: %s %02d:%02d %s"
                        % (member.name, member.city,
                           "" if member.required else " [optional]",
                           moment.strftime("%a"), moment.hour, moment.minute,
                           moment.strftime("%Z")))
        body.append("Score: %d/%d" % (item.score, plan.max_score))

        lines += ["BEGIN:VEVENT",
                  "UID:teamsync-%s-%d@local" % (item.start.strftime("%Y%m%dT%H%M"), rank),
                  "DTSTAMP:" + stamp,
                  "DTSTART:" + item.start.strftime(ICS_FORMAT),
                  "DTEND:" + end.strftime(ICS_FORMAT),
                  "SUMMARY:" + escape_ics("TeamSync meeting (%d/%d members)"
                                          % (item.count, total)),
                  "DESCRIPTION:" + escape_ics("\n".join(body)),
                  "END:VEVENT"]

    lines.append("END:VCALENDAR")
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write("\r\n".join(fold(line) for line in lines) + "\r\n")
    return path


def save_csv(path, plan, team_name):
    """Spreadsheet-friendly export: one row per recommendation, one column per member.

    Written as UTF-8 with a BOM (``utf-8-sig``) so that Excel on Windows shows
    Chinese member names correctly instead of mojibake.
    """
    roster = plan.roster
    header = ["rank", "start_utc", "weekday", "duration_min", "attendees", "total",
              "required_ok", "score", "max_score", "continuous_min", "missing"]
    header += ["%s (%s)" % (member.name, member.city) for member in roster]

    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for rank, item in enumerate(plan.recommendations, 1):
            row = [rank, item.start.strftime("%Y-%m-%dT%H:%MZ"),
                   item.start.strftime("%a"), item.duration_min,
                   item.count, item.total,
                   "yes" if item.required_ok else "no",
                   item.score, plan.max_score, item.continuous_min,
                   "; ".join(member.name for member in item.missing)]
            for member in roster:
                moment = member.local(item.start)
                cell = moment.strftime("%a %H:%M")
                if member not in item.members:
                    cell += " (missing)"
                row.append(cell)
            writer.writerow(row)
    return path


HTML_STYLE = """
body{font:14px/1.6 "Segoe UI",system-ui,sans-serif;max-width:920px;margin:32px auto;
     padding:0 16px;color:#22303f}
h1{border-bottom:3px solid #2b6cb0;padding-bottom:8px;font-size:22px}
h2{font-size:15px;margin:0 0 6px}
h2 span{font-weight:400;color:#7a8899;font-size:12px;float:right}
section{border:1px solid #dde3ea;border-radius:8px;padding:12px 16px;margin:16px 0;
        background:#fafbfc}
p.t{margin:0 0 8px;color:#2b6cb0;font-weight:600}
table{border-collapse:collapse;width:100%}
td,th{padding:4px 8px;border-bottom:1px solid #eef1f5;font-size:13px;text-align:left}
th{font-weight:600;color:#4a5a6b}
footer{color:#93a1b1;font-size:12px;margin:24px 0}
.heat td{width:3.2%;text-align:center;padding:5px 2px;border:1px solid #eef1f5;
         font-size:11px}
.heat th{font-size:11px;text-align:center;padding:2px}
.c0{background:#f7f9fb;color:#b9c2cc}
.c1{background:#e3eefc}.c2{background:#c3dcf8}.c3{background:#9cc4f0}
.c4{background:#74abe8}.c5{background:#4d91de;color:#fff}
.c6{background:#2b6cb0;color:#fff}.c7{background:#1f4f85;color:#fff}
.full{background:#1a7f37;color:#fff;font-weight:700}
.legend{font-size:12px;color:#7a8899}
"""


def save_html(path, plan, team_name):
    """Self-contained web report: inline CSS, no external requests.

    It carries three things the plain text cannot: coloured recommendation cards,
    a coverage heatmap for the week, and the member table (role, days away and
    weekly availability hours).
    """
    escape = html.escape
    total = len(plan.roster)
    sections = []

    for rank, item in enumerate(plan.recommendations, 1):
        rows = ["<tr><th>Name</th><th>City</th><th>Role</th>"
                "<th>Local time</th><th>Note</th></tr>"]
        for member in item.members:
            moment = member.local(item.start)
            minutes = moment.hour * 60 + moment.minute
            note = "late" if minutes >= 22 * 60 else "early" if minutes < 7 * 60 else ""
            rows.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s %02d:%02d %s</td>"
                        "<td>%s</td></tr>"
                        % (escape(member.name), escape(member.city),
                           "required" if member.required else "optional",
                           moment.strftime("%a"), moment.hour, moment.minute,
                           escape(moment.strftime("%Z")), note))
        for member in item.missing:
            rows.append("<tr><td>%s</td><td>%s</td><td>%s</td>"
                        "<td colspan='2'><strong>missing</strong></td></tr>"
                        % (escape(member.name), escape(member.city),
                           "required" if member.required else "optional"))
        if not item.missing:
            status = "every member can attend"
        else:
            status = "missing " + ", ".join(
                member.name + ("" if member.required else " (optional)")
                for member in item.missing)
        sections.append(
            "<section><h2>Recommendation %d<span>%d/%d members &middot; score %d/%d"
            " &middot; %s</span></h2><p class='t'>%s UTC &middot; %d min"
            " &middot; continuous %.1f h</p><table>%s</table></section>"
            % (rank, item.count, total, item.score, plan.max_score, escape(status),
               item.start.strftime("%Y-%m-%d (%a) %H:%M"), item.duration_min,
               item.continuous_hours, "".join(rows)))

    # heatmap: one coloured cell per UTC hour, best of its two half-hour cells
    header_cells = "".join("<th>%02d</th>" % hour for hour in range(24))
    heat_rows = []
    for day, counts in plan.hourly_rows():
        cells = []
        for count in counts:
            if count == total:
                cells.append("<td class='full'>%d</td>" % count)
            elif count:
                cells.append("<td class='c%d'>%d</td>" % (min(count, 7), count))
            else:
                cells.append("<td class='c0'>.</td>")
        heat_rows.append("<tr><th>%s %s</th>%s</tr>"
                         % (day.strftime("%a"), day.strftime("%m-%d"),
                            "".join(cells)))
    heatmap = ("<table class='heat'><tr><th>UTC</th>%s</tr>%s</table>"
               % (header_cells, "".join(heat_rows)))

    member_rows = []
    for row in _member_table(plan.roster):
        member_rows.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                           "<td>%s</td><td>%s h</td><td>%s</td></tr>" % row)
    member_table = ("<table><tr><th>Name</th><th>City</th><th>Timezone</th>"
                    "<th>Role</th><th>Away</th><th>Hours/week</th>"
                    "<th>Local windows</th></tr>%s</table>"
                    % "".join(member_rows))

    document = (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        "<title>TeamSync report - %s</title>\n<style>%s</style>\n</head>\n<body>\n"
        "<h1>TeamSync &mdash; Meeting Time Report</h1>\n"
        "<p>Team <strong>%s</strong> &middot; week of %s &middot; meeting length %d min"
        " &middot; all times UTC unless stated</p>\n"
        "%s\n<h2>Weekly availability (UTC)</h2>\n%s\n"
        "<p class='legend'>Green = everybody free, darker blue = more people free,"
        " dot = nobody free. Each cell is the better of its two half-hour slots.</p>\n"
        "<h2>Team</h2>\n%s\n"
        "<footer>Generated locally by TeamSync &mdash; timezone data bundled,"
        " no network required.</footer>\n</body>\n</html>\n"
        % (escape(team_name), HTML_STYLE, escape(team_name),
           plan.week_start.strftime("%Y-%m-%d"), plan.duration_min,
           "".join(sections), heatmap, member_table))

    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(document)
    return path


def _member_table(roster):
    """Rows of already-escaped HTML cells for the member table."""
    escape = html.escape
    rows = []
    for member in roster:
        rows.append((escape(member.name), escape(member.city), escape(member.tzkey),
                     "required" if member.required else "optional",
                     escape(member.away_text()),
                     "%.1f" % (member.weekly_minutes / 60.0),
                     escape(member.describe_windows())))
    return rows


EXPORTERS = {"txt": save_txt, "ics": save_ics, "html": save_html, "csv": save_csv}


def export(plan, team_name, kind, folder=OUTPUT_DIR):
    """Write one export and return its path.

    ``kind`` is 'txt', 'ics', 'html' or 'csv'.
    """
    if kind not in EXPORTERS:
        raise TeamSyncError("unknown export format %r -- pick from %s"
                            % (kind, ", ".join(sorted(EXPORTERS))))
    ensure_output_dir(folder)
    path = meeting_filename(plan, kind, folder)
    return EXPORTERS[kind](path, plan, team_name)
