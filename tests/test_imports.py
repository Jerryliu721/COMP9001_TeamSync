# -*- coding: utf-8 -*-
"""Tests for the v3 import features.

Run from the project folder with either of::

    python -m unittest discover tests
    python tests/test_imports.py

Two importers are covered:

* ``ics_import`` -- read a member's .ics calendar into busy blocks
* ``storage.load_roster_csv`` -- bulk-add members from a spreadsheet

The interesting case is the daylight saving one: a weekly 09:00 London lecture
must stay at 09:00 London when the clocks change, which means its UTC time moves
from 09:00 to 08:00.  A naive "add seven days in UTC" implementation gets that
wrong, so it gets its own test.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import cities
import ics_import
import models
import planner
import render
import storage
from models import Availability, BusyBlock

DEMO_TEAM = os.path.join(ROOT, "data", "demo_team.json")
EXAMPLE_ICS = os.path.join(ROOT, "data", "example_calendar.ics")
EXAMPLE_CSV = os.path.join(ROOT, "data", "example_roster.csv")
WEEK_A = date(2026, 3, 2)


def utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def calendar(*events):
    """Wrap VEVENT bodies into a minimal VCALENDAR document."""
    body = "\n".join("BEGIN:VEVENT\n%s\nEND:VEVENT" % event.strip()
                     for event in events)
    return "BEGIN:VCALENDAR\nVERSION:2.0\n%s\nEND:VCALENDAR\n" % body


class TestIcsParsing(unittest.TestCase):

    def test_shipped_example_calendar(self):
        blocks, warnings = ics_import.read_ics(EXAMPLE_ICS, utc(2026, 3, 2),
                                               utc(2026, 3, 30))
        titles = [block.title for block in blocks]
        self.assertEqual(len(blocks), 6)
        self.assertEqual(titles.count("COMP9001 lecture"), 4)
        self.assertIn("Doctor appointment", titles)
        self.assertIn("Supervisor meeting", titles)
        self.assertEqual(len(warnings), 1)           # the all-day event
        self.assertIn("all-day", warnings[0])

    def test_utc_event_is_kept_as_is(self):
        text = calendar("""
DTSTART:20260303T090000Z
DTEND:20260303T103000Z
SUMMARY:Doctor""")
        blocks, warnings = ics_import.parse_events(text, utc(2026, 3, 2),
                                                   utc(2026, 3, 9))
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].start, utc(2026, 3, 3, 9))
        self.assertEqual(blocks[0].end, utc(2026, 3, 3, 10, 30))
        self.assertEqual(warnings, [])

    def test_tzid_event_is_converted_to_utc(self):
        text = calendar("""
DTSTART;TZID=Asia/Shanghai:20260303T090000
DTEND;TZID=Asia/Shanghai:20260303T100000
SUMMARY:Class""")
        blocks, _ = ics_import.parse_events(text, utc(2026, 3, 2), utc(2026, 3, 9))
        self.assertEqual(blocks[0].start, utc(2026, 3, 3, 1))

    def test_weekly_repeat_keeps_the_local_wall_time_across_dst(self):
        """0.9: a 09:00 London stand-up stays 09:00 London, so UTC moves."""
        text = calendar("""
DTSTART;TZID=Europe/London:20260323T090000
DTEND;TZID=Europe/London:20260323T100000
RRULE:FREQ=WEEKLY
SUMMARY:Stand-up""")
        blocks, warnings = ics_import.parse_events(text, utc(2026, 3, 23),
                                                   utc(2026, 4, 6))
        self.assertEqual([block.start for block in blocks],
                         [utc(2026, 3, 23, 9), utc(2026, 3, 30, 8)])
        self.assertEqual(warnings, [])

    def test_weekly_byday_count(self):
        text = calendar("""
DTSTART;TZID=Asia/Shanghai:20260304T190000
DTEND;TZID=Asia/Shanghai:20260304T200000
RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=5
SUMMARY:Study group""")
        blocks, _ = ics_import.parse_events(text, utc(2026, 3, 2), utc(2026, 4, 1))
        # Wed 4, Mon 9, Wed 11, Mon 16, Wed 18 March -- five, then COUNT stops it
        self.assertEqual(len(blocks), 5)
        self.assertEqual(blocks[0].start, utc(2026, 3, 4, 11))   # Wed 19:00 CST
        self.assertEqual(blocks[1].start, utc(2026, 3, 9, 11))   # Mon 19:00 CST
        self.assertEqual(blocks[-1].start, utc(2026, 3, 18, 11))

    def test_all_day_event_is_skipped_with_a_warning(self):
        text = calendar("""
DTSTART;VALUE=DATE:20260304
DTEND;VALUE=DATE:20260305
SUMMARY:Holiday""")
        blocks, warnings = ics_import.parse_events(text, utc(2026, 3, 2),
                                                   utc(2026, 3, 9))
        self.assertEqual(blocks, [])
        self.assertIn("all-day", warnings[0])

    def test_monthly_repeat_is_skipped_with_a_warning(self):
        text = calendar("""
DTSTART:20260302T090000Z
DTEND:20260302T100000Z
RRULE:FREQ=MONTHLY
SUMMARY:Monthly review""")
        blocks, warnings = ics_import.parse_events(text, utc(2026, 3, 2),
                                                   utc(2026, 3, 9))
        self.assertEqual(blocks, [])
        self.assertIn("monthly", warnings[0].lower())

    def test_missing_dtend_is_skipped(self):
        text = calendar("""
DTSTART:20260302T090000Z
SUMMARY:No end""")
        blocks, warnings = ics_import.parse_events(text, utc(2026, 3, 2),
                                                   utc(2026, 3, 9))
        self.assertEqual(blocks, [])
        self.assertIn("DTEND", warnings[0])

    def test_unknown_timezone_falls_back_to_utc(self):
        text = calendar("""
DTSTART;TZID=Mars/Olympus:20260302T090000
DTEND;TZID=Mars/Olympus:20260302T100000
SUMMARY:Odd zone""")
        blocks, warnings = ics_import.parse_events(text, utc(2026, 3, 2),
                                                   utc(2026, 3, 9))
        self.assertEqual(blocks[0].start, utc(2026, 3, 2, 9))
        self.assertTrue(any("unknown timezone" in item for item in warnings))

    def test_folded_lines_are_rejoined(self):
        # RFC 5545: a fold is CRLF + one space, and unfolding drops that space.
        self.assertEqual(ics_import.unfold("SUMMARY:one \n two"),
                         ["SUMMARY:one two"])
        folded = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\n"
                  "DTSTART:20260302T090000Z\nDTEND:20260302T100000Z\n"
                  "SUMMARY:A rather long title that wraps \n onto the next line\n"
                  "END:VEVENT\nEND:VCALENDAR\n")
        name, params, value = ics_import.split_property("SUMMARY;LANGUAGE=en:A:b")
        self.assertEqual((name, params.get("LANGUAGE"), value),
                         ("SUMMARY", "en", "A:b"))
        blocks, _ = ics_import.parse_events(folded, utc(2026, 3, 2), utc(2026, 3, 9))
        self.assertEqual(blocks[0].title,
                         "A rather long title that wraps onto the next line")

    def test_duplicate_events_are_merged(self):
        body = """
DTSTART:20260302T090000Z
DTEND:20260302T100000Z
SUMMARY:Duplicate"""
        text = calendar(body, body)
        blocks, _ = ics_import.parse_events(text, utc(2026, 3, 2), utc(2026, 3, 9))
        self.assertEqual(len(blocks), 1)

    def test_file_without_events_is_rejected(self):
        with self.assertRaises(ics_import.IcsError):
            ics_import.parse_events("BEGIN:VCALENDAR\nEND:VCALENDAR",
                                    utc(2026, 3, 2), utc(2026, 3, 9))
        with self.assertRaises(ics_import.IcsError):
            ics_import.parse_events("not a calendar", utc(2026, 3, 2),
                                    utc(2026, 3, 9))

    def test_missing_file_is_rejected(self):
        with self.assertRaises(ics_import.IcsError):
            ics_import.read_ics("no_such_calendar.ics", utc(2026, 3, 2),
                                utc(2026, 3, 9))


class TestBusyBlocks(unittest.TestCase):

    def busy_member(self):
        return cities.make_member(
            "Dan", "New York",
            [Availability((0, 1, 2, 3, 4), 9 * 60, 12 * 60)],
            busy=[BusyBlock(utc(2026, 3, 2, 15), utc(2026, 3, 2, 16), "Exam")])

    def test_busy_time_is_not_available(self):
        member = self.busy_member()
        # 09:00 New York is 14:00 UTC in March (EST, UTC-5)
        self.assertTrue(member.available_at(utc(2026, 3, 2, 14)))
        self.assertFalse(member.available_at(utc(2026, 3, 2, 15)))
        self.assertIsNotNone(member.busy_at(utc(2026, 3, 2, 15)))
        self.assertIsNone(member.busy_at(utc(2026, 3, 2, 17)))

    def test_explain_reports_a_busy_event(self):
        member = self.busy_member()
        check = planner.explain([member], utc(2026, 3, 2, 15))[0]
        self.assertFalse(check.free)
        self.assertEqual(check.reason, "busy")
        self.assertEqual(check.busy.title, "Exam")
        text = render.explain_text([check], utc(2026, 3, 2, 15))
        self.assertIn("BUSY", text)
        self.assertIn("Exam", text)

    def test_planner_avoids_busy_hours(self):
        """A busy block removes slots without touching the availability rules."""
        member = cities.make_member(
            "Solo", "Beijing",
            [Availability((0,), 9 * 60, 12 * 60)],
            busy=[BusyBlock(utc(2026, 3, 2, 1), utc(2026, 3, 2, 3), "Training")])
        result = planner.plan([member], WEEK_A, 60)
        self.assertIsNotNone(result.best)
        # without the block the best start would be 01:00 UTC (09:00 Beijing)
        self.assertEqual(result.best.start, utc(2026, 3, 2, 3))
        self.assertEqual(result.best.continuous_min, 60)

    def test_team_file_round_trip_keeps_busy_blocks(self):
        folder = tempfile.mkdtemp(prefix="teamsync_busy_")
        try:
            path = os.path.join(folder, "team.json")
            roster = [self.busy_member()]
            storage.save_team(path, "Team", WEEK_A, 60, roster)
            _, _, _, loaded = storage.load_team(path)
            self.assertEqual(len(loaded[0].busy), 1)
            self.assertEqual(loaded[0].busy[0].title, "Exam")
            self.assertEqual(loaded[0].busy[0].start, utc(2026, 3, 2, 15))
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
            self.assertEqual(raw["members"][0]["busy"][0]["start_utc"],
                             "2026-03-02T15:00Z")
        finally:
            shutil.rmtree(folder, ignore_errors=True)


class TestRosterCsv(unittest.TestCase):

    def test_shipped_example_roster(self):
        members, warnings = storage.load_roster_csv(EXAMPLE_CSV)
        self.assertEqual([member.name for member in members],
                         ["Alice", "Bo", "Chen", "Eve"])
        self.assertEqual(warnings, [])
        self.assertTrue(members[0].required)
        self.assertFalse(members[3].required)
        self.assertEqual(len(members[1].windows), 2)       # morning and evening
        self.assertEqual(members[3].away, frozenset([date(2026, 3, 4)]))
        self.assertEqual(members[0].tzkey, "Australia/Sydney")

    def test_bad_row_is_skipped_with_a_warning(self):
        folder = tempfile.mkdtemp(prefix="teamsync_csv_")
        try:
            path = os.path.join(folder, "roster.csv")
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write("name,city,role,days,windows,away\n")
                handle.write("Zoe,Atlantis,required,Mon-Fri,09:00-12:00,\n")
                handle.write("Bo,Beijing,required,Mon-Fri,09:00-12:00,\n")
            members, warnings = storage.load_roster_csv(path)
            self.assertEqual([member.name for member in members], ["Bo"])
            self.assertEqual(len(warnings), 1)
            self.assertIn("Atlantis", warnings[0])
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_missing_column_is_rejected(self):
        folder = tempfile.mkdtemp(prefix="teamsync_csv_")
        try:
            path = os.path.join(folder, "roster.csv")
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write("name,city\nAlice,Sydney\n")
            with self.assertRaises(models.TeamSyncError) as caught:
                storage.load_roster_csv(path)
            self.assertIn("windows", str(caught.exception))
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_header_only_file_is_rejected(self):
        folder = tempfile.mkdtemp(prefix="teamsync_csv_")
        try:
            path = os.path.join(folder, "roster.csv")
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write("name,city,role,days,windows,away\n")
            with self.assertRaises(models.TeamSyncError):
                storage.load_roster_csv(path)
        finally:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
