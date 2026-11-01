# -*- coding: utf-8 -*-
"""Tests for the version-2 features.

Run from the project folder with either of::

    python -m unittest discover tests
    python tests/test_features.py

They cover the four additions that carry real logic -- attendance roles, days
away, multi-week scanning and the "explain this time" breakdown -- plus the new
exports and the text helpers that feed both front ends.

The GUI smoke test skips itself when tkinter (or a display) is missing, so the
suite stays green on the machines our markers happen to use.
"""

import csv
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
import models
import planner
import render
import storage
from models import Availability

try:
    import tkinter

    HAS_TK = True
except ImportError:                      # pragma: no cover - machine dependent
    HAS_TK = False

DEMO_TEAM = os.path.join(ROOT, "data", "demo_team.json")
EXTENDED_TEAM = os.path.join(ROOT, "data", "demo_team_extended.json")
WEEK_A = date(2026, 3, 2)


def demo_roster(path=DEMO_TEAM):
    _, _, _, roster = storage.load_team(path)
    return roster


def member(name, start, end, city="Beijing", required=True, away=()):
    """A member free ``start``-``end`` Beijing time, Monday to Friday.

    Beijing is UTC+8 all year, so local 09:00 is 01:00 UTC and the expected
    answers in these tests stay easy to check by hand.
    """
    return cities.make_member(
        name, city,
        [Availability((0, 1, 2, 3, 4),
                      models.parse_hhmm(start), models.parse_hhmm(end))],
        required=required, away=away)


def utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


class TestAttendanceRoles(unittest.TestCase):

    def test_optional_member_is_worth_less(self):
        roster = [member("A", "09:00", "10:00"),
                  member("C", "09:00", "10:00", required=False)]
        best = planner.plan(roster, WEEK_A, 60).best
        self.assertEqual(best.score,
                         planner.COVERAGE_BONUS + planner.OPTIONAL_BONUS
                         + 2 * planner.COMFORT_WORK)

    def test_missing_optional_still_full_attendance(self):
        roster = [member("A", "09:00", "10:00"),
                  member("O", "21:00", "22:00", required=False)]
        best = planner.plan(roster, WEEK_A, 60).best
        self.assertTrue(best.required_ok)
        self.assertEqual([item.name for item in best.members], ["A"])
        self.assertEqual([item.name for item in best.missing], ["O"])

    def test_missing_required_is_flagged(self):
        roster = [member("A", "09:00", "10:00"),
                  member("B", "21:00", "22:00")]
        best = planner.plan(roster, WEEK_A, 60).best
        self.assertFalse(best.required_ok)
        self.assertEqual([item.name for item in best.missing_required], ["B"])

    def test_required_coverage_beats_extra_optional_people(self):
        """Two attendees with both required members beat three with one missing."""
        roster = [member("A", "09:00", "11:00"),
                  member("B", "10:00", "11:00"),
                  member("C", "09:00", "10:00", required=False),
                  member("D", "09:00", "10:00", required=False)]
        result = planner.plan(roster, WEEK_A, 60)
        best = result.best
        self.assertEqual(best.count, 2)
        self.assertTrue(best.required_ok)
        self.assertEqual(best.start.strftime("%H:%M"), "02:00")   # 10:00 Beijing
        self.assertEqual([item.name for item in best.members], ["A", "B"])


class TestAwayDates(unittest.TestCase):

    def test_away_blocks_a_whole_local_day(self):
        alice = member("Alice", "09:00", "12:00", away=[date(2026, 3, 2)])
        monday = utc(2026, 3, 2, 1)          # 09:00 Monday in Beijing
        tuesday = utc(2026, 3, 3, 1)
        self.assertFalse(alice.available_at(monday))
        self.assertTrue(alice.available_at(tuesday))
        # the window itself is unchanged -- only that one date is skipped
        self.assertIsNotNone(alice.window_at(alice.local(monday)))

    def test_parse_date_list(self):
        days = models.parse_date_list("2026-03-04, 2026-03-06..2026-03-08")
        self.assertEqual(len(days), 4)
        self.assertIn(date(2026, 3, 4), days)
        self.assertIn(date(2026, 3, 8), days)
        self.assertEqual(models.parse_date_list(""), set())
        with self.assertRaises(models.InvalidDate):
            models.parse_date_list("2026-02-30")
        with self.assertRaises(models.InvalidDate):
            models.parse_date_list("2026-03-08..2026-03-04")

    def test_format_date_list(self):
        self.assertEqual(
            models.format_date_list({date(2026, 3, 5), date(2026, 3, 4)}),
            "2026-03-04, 2026-03-05")
        alice = member("Alice", "09:00", "12:00", away=[date(2026, 3, 4)])
        self.assertEqual(alice.away_text(), "2026-03-04")
        self.assertEqual(member("Bo", "09:00", "12:00").away_text(), "-")

    def test_weekly_minutes(self):
        self.assertEqual(member("A", "09:00", "12:00").weekly_minutes, 15 * 60)
        wraps = models.Member("W", "Beijing", "Asia/Shanghai",
                              [Availability((0, 1, 2, 3, 4), 22 * 60, 2 * 60)])
        self.assertEqual(wraps.weekly_minutes, 20 * 60)
        all_day = models.Member("D", "Beijing", "Asia/Shanghai",
                                [Availability((0,), 0, 0)])
        self.assertEqual(all_day.weekly_minutes, 24 * 60)


class TestMultiWeek(unittest.TestCase):

    def test_plan_weeks_steps_one_week_at_a_time(self):
        plans = planner.plan_weeks(demo_roster(), WEEK_A, 60, 4)
        self.assertEqual(len(plans), 4)
        self.assertEqual([item.week_start.strftime("%Y-%m-%d") for item in plans],
                         ["2026-03-02", "2026-03-09", "2026-03-16", "2026-03-23"])
        self.assertEqual(plans[0].best.start.strftime("%H:%M"), "11:00")

    def test_best_week_prefers_more_required_coverage(self):
        plans = planner.plan_weeks(demo_roster(), WEEK_A, 60, 4)
        chosen = planner.best_week(plans)
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.best.count, 4)
        self.assertEqual(chosen.best.score, max(item.best.score for item in plans))

    def test_best_week_handles_an_empty_scan(self):
        self.assertIsNone(planner.best_week([]))

    def test_dst_changes_are_found_on_the_right_day(self):
        changes = planner.offset_changes(demo_roster(), WEEK_A, 4)
        new_york = [item for item in changes if item.member.city == "New York"]
        london = [item for item in changes if item.member.city == "London"]
        self.assertEqual(len(new_york), 1)
        self.assertEqual(new_york[0].day, date(2026, 3, 8))
        self.assertEqual((new_york[0].before_hours, new_york[0].after_hours),
                         (-5, -4))
        self.assertEqual(len(london), 1)
        self.assertEqual(london[0].day, date(2026, 3, 29))
        self.assertEqual((london[0].before_hours, london[0].after_hours), (0, 1))

    def test_weeks_text_mentions_the_switch(self):
        text = render.weeks_text(planner.plan_weeks(demo_roster(), WEEK_A, 60, 4))
        self.assertIn("2026-03-02", text)
        self.assertIn("Daylight saving changes", text)
        self.assertIn("New York", text)
        self.assertIn("2026-03-08", text)


class TestDurationOptions(unittest.TestCase):

    def test_short_meeting_fits_long_one_does_not(self):
        roster = [member("A", "09:00", "10:00"),
                  member("B", "09:00", "10:00")]
        options = dict(planner.duration_options(roster, WEEK_A))
        self.assertIsNotNone(options[30].best)
        self.assertIsNotNone(options[60].best)
        self.assertIsNone(options[90].best)

    def test_durations_text_explains_the_fallback(self):
        roster = [member("A", "09:00", "10:00"),
                  member("B", "09:00", "10:00")]
        options = planner.duration_options(roster, WEEK_A)
        text = render.durations_text(options, requested=90)
        self.assertIn("90 min is not possible", text)
        self.assertIn("60 min", text)

    def test_a_45_minute_meeting_needs_two_cells(self):
        """The start must leave 45 real minutes, not just one 30-minute cell."""
        roster = [member("A", "08:30", "09:30"),
                  member("B", "08:30", "09:30")]
        best = planner.plan(roster, WEEK_A, 45).best
        self.assertEqual(best.start.strftime("%H:%M"), "00:30")   # 08:30 Beijing
        self.assertEqual(best.duration_min, 45)

    def test_a_length_must_be_positive(self):
        roster = [member("A", "09:00", "10:00")]
        for bad in (0, -30):
            with self.assertRaises(ValueError):
                planner.plan(roster, WEEK_A, bad)


class TestExplain(unittest.TestCase):

    def test_everybody_is_free_at_the_best_time(self):
        result = planner.plan(demo_roster(), WEEK_A, 60)
        checks = planner.explain(result.roster, result.best.start)
        self.assertEqual(len(checks), 4)
        self.assertTrue(all(check.free for check in checks))
        self.assertTrue(all(check.reason == "" for check in checks))

    def test_outside_window_and_away_reasons(self):
        away_guy = member("Dan", "09:00", "12:00", away=[date(2026, 3, 2)])
        early = member("Chen", "19:00", "22:00")
        checks = planner.explain([away_guy, early], utc(2026, 3, 2, 1))
        by_name = dict((check.member.name, check) for check in checks)
        self.assertEqual(by_name["Dan"].reason, "away")
        self.assertEqual(by_name["Chen"].reason, "outside window")
        self.assertFalse(by_name["Chen"].free)

    def test_explain_text_lists_people_and_local_times(self):
        result = planner.plan(demo_roster(), WEEK_A, 60)
        text = render.explain_text(planner.explain(result.roster, result.best.start),
                                   result.best.start)
        for name in ("Alice", "Bo", "Chen", "Dan"):
            self.assertIn(name, text)
        self.assertIn("4 of 4 members are free", text)


class TestRenderText(unittest.TestCase):

    def test_offset_text(self):
        self.assertEqual(render.offset_text(10), "UTC+10")
        self.assertEqual(render.offset_text(-5), "UTC-5")
        self.assertEqual(render.offset_text(0), "UTC+0")
        self.assertEqual(render.offset_text(5.5), "UTC+5:30")

    def test_cards_show_role_and_missing(self):
        roster = [member("A", "09:00", "10:00"),
                  member("O", "21:00", "22:00", required=False)]
        text = render.cards_text(planner.plan(roster, WEEK_A, 60))
        self.assertIn("missing: O (optional)", text)

    def test_members_text_says_who_is_optional(self):
        text = render.members_text(demo_roster(EXTENDED_TEAM))
        self.assertIn("Optional members: Eve", text)
        self.assertIn("Away (local dates)", text)
        self.assertIn("2026-03-04", text)

    def test_report_mentions_the_roles(self):
        text = render.report_text(planner.plan(demo_roster(), WEEK_A, 60), "Team")
        self.assertIn("Members: 4 (4 required, 0 optional)", text)
        self.assertIn("score 205/280", text)

    def test_invite_text_for_the_best_slot(self):
        plan = planner.plan(demo_roster(), WEEK_A, 60)
        text = render.invite_text(plan, "Group 7")
        self.assertIn("Group 7 - meeting time", text)
        self.assertIn("Mon 02 Mar 2026, 11:00 UTC (60 minutes)", text)
        self.assertIn("4/4 members can attend", text)
        for name in ("Alice", "Bo", "Chen", "Dan"):
            self.assertIn(name, text)
        self.assertIn("Reply OK", text)

    def test_invite_text_when_nothing_fits(self):
        roster = [member("A", "09:00", "10:00"),
                  member("B", "21:00", "22:00")]
        text = render.invite_text(planner.plan(roster, WEEK_A, 120), "Group")
        self.assertIn("no meeting slot fits", text)


class TestNewExports(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="teamsync_v3_")
        self.plan = planner.plan(demo_roster(), WEEK_A, 60)

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_four_exporters_write_files(self):
        for kind in ("txt", "ics", "html", "csv"):
            path = storage.export(self.plan, "Test Team", kind, folder=self.folder)
            self.assertTrue(os.path.isfile(path), kind)
            self.assertGreater(os.path.getsize(path), 0, kind)

    def test_csv_is_readable_by_a_spreadsheet(self):
        path = storage.export(self.plan, "Test Team", "csv", folder=self.folder)
        with open(path, encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(rows[0][0], "rank")
        self.assertIn("Alice (Sydney)", rows[0])
        self.assertEqual(len(rows) - 1, len(self.plan.recommendations))
        self.assertEqual(int(rows[1][0]), 1)

    def test_html_has_a_heatmap(self):
        path = storage.export(self.plan, "Test Team", "html", folder=self.folder)
        with open(path, encoding="utf-8") as handle:
            document = handle.read()
        self.assertIn("class='heat'", document)
        self.assertIn("<h2>Team</h2>", document)

    def test_ics_warns_about_a_missing_required_member(self):
        roster = [member("A", "09:00", "10:00"),
                  member("B", "09:00", "12:00"),
                  member("C", "12:00", "13:00")]
        result = planner.plan(roster, WEEK_A, 60)
        path = storage.export(result, "Test Team", "ics", folder=self.folder)
        with open(path, encoding="utf-8") as handle:
            document = handle.read()
        # RFC 5545 folds long lines, so undo the folding before looking for text
        self.assertIn("MISSING REQUIRED: C", document.replace("\n ", ""))

    def test_json_snapshot_has_the_new_fields(self):
        path = os.path.join(self.folder, "latest.json")
        storage.save_json(path, self.plan, "Test Team")
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        top = data["recommendations"][0]
        self.assertTrue(top["required_ok"])
        self.assertEqual(top["missing"], [])
        self.assertEqual(top["total"], 4)
        self.assertEqual(data["required_members"], 4)
        self.assertIn("away", data["members"][0])
        self.assertIn("required", data["members"][0])

    def test_team_file_round_trip_keeps_roles_and_away_days(self):
        path = os.path.join(self.folder, "team.json")
        roster = [member("A", "09:00", "12:00", required=False,
                         away=[date(2026, 3, 4)])]
        storage.save_team(path, "Team", WEEK_A, 60, roster)
        _, _, _, loaded = storage.load_team(path)
        self.assertFalse(loaded[0].required)
        self.assertEqual(loaded[0].away, frozenset([date(2026, 3, 4)]))
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        self.assertIs(raw["members"][0]["required"], False)
        self.assertEqual(raw["members"][0]["away"], ["2026-03-04"])

    def test_broken_team_file_says_what_is_missing(self):
        path = os.path.join(self.folder, "broken.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('{"team": "Broken", "members": []}')
        with self.assertRaises(models.TeamSyncError) as caught:
            storage.load_team(path)
        self.assertIn("week", str(caught.exception))

    def test_unknown_export_format_is_an_error(self):
        with self.assertRaises(models.TeamSyncError):
            storage.export(self.plan, "Test Team", "pdf", folder=self.folder)


class TestBatchMode(unittest.TestCase):
    """The --report / --export / --weeks route must never print a traceback."""

    def test_cities_list_runs(self):
        import io
        import contextlib
        import main

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main.main(["--cities"])
        self.assertEqual(code, 0)
        self.assertIn("cities are bundled", buffer.getvalue())

    def test_bad_options_are_rejected_before_any_work(self):
        import io
        import contextlib
        import main

        for argv in (["--export", "pdf"], ["--weeks", "0"],
                     ["--report", "--top", "0"]):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = main.main(argv)
            self.assertEqual(code, 1, argv)
            self.assertIn("!", buffer.getvalue(), argv)

    def test_missing_team_file_is_one_readable_line(self):
        import io
        import contextlib
        import main

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main.main(["--file", "no_such_team.json", "--report"])
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", buffer.getvalue())


@unittest.skipUnless(HAS_TK, "tkinter is not available")
class TestGui(unittest.TestCase):
    """Window tests share one Tk root: several roots in one process upset ttk."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tkinter.Tk()
        except tkinter.TclError as exc:                # no display at all
            raise unittest.SkipTest("no display: %s" % exc)
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        for child in list(self.root.winfo_children()):
            child.destroy()

    def tearDown(self):
        for child in list(self.root.winfo_children()):
            child.destroy()

    def test_members_table_has_role_and_away_columns(self):
        import gui

        app = gui.TeamSyncApp(self.root, EXTENDED_TEAM)
        self.root.update_idletasks()
        self.assertEqual(len(app.members["columns"]), 7)
        self.assertEqual(len(app.members.get_children()), 5)

    def test_compute_can_scan_several_weeks(self):
        import gui

        app = gui.TeamSyncApp(self.root, DEMO_TEAM)
        app.auto_save.set(False)
        app.weeks.set("4")
        result = app.compute()
        self.root.update_idletasks()
        self.assertIsNotNone(result)
        self.assertEqual(len(app.plans), 4)
        self.assertIn("best of 4 weeks", app.status.get())
        # the local-clock table has one row per member
        self.assertEqual(len(app.localview.get_children()), 4)
        first = app.localview.item(app.localview.get_children()[0], "values")
        self.assertEqual(first[0], "Alice")
        self.assertEqual(first[1], "required")
        self.assertEqual(len(first), 5)
        self.assertIn("UTC", app.localview.heading("r1", "text"))

    def test_empty_state_until_compute(self):
        import gui

        app = gui.TeamSyncApp(self.root, DEMO_TEAM)
        self.root.update_idletasks()
        self.assertEqual(len(app.results_view.inner.winfo_children()), 1)
        app.auto_save.set(False)
        app.compute()
        self.root.update_idletasks()
        self.assertGreater(len(app.results_view.inner.winfo_children()), 1)

    def test_filter_keeps_the_right_member_selected(self):
        import gui

        app = gui.TeamSyncApp(self.root, DEMO_TEAM)
        self.root.update_idletasks()
        app.filter_var.set("beij")
        self.root.update_idletasks()
        self.assertEqual(len(app.members.get_children()), 1)
        app.members.selection_set(app.members.get_children()[0])
        self.assertEqual(app.selected_member().name, "Bo")
        app.filter_var.set("")
        self.root.update_idletasks()
        self.assertEqual(len(app.members.get_children()), 4)

    def test_copy_invite_puts_text_on_the_clipboard(self):
        import gui

        app = gui.TeamSyncApp(self.root, DEMO_TEAM)
        app.auto_save.set(False)
        app.compute()
        app.copy_invite()
        self.root.update()
        text = self.root.clipboard_get()
        self.assertIn("Reply OK", text)
        self.assertIn("Alice", text)
        self.assertIn("11:00", text)

    def test_week_arrows_move_and_recompute(self):
        import gui

        app = gui.TeamSyncApp(self.root, DEMO_TEAM)
        app.auto_save.set(False)
        app.compute()
        app.shift_week(7)
        self.root.update_idletasks()
        self.assertEqual(app.week.get(), "2026-03-09")
        self.assertEqual(app.plan.week_start.strftime("%Y-%m-%d"), "2026-03-09")
        app.shift_week(-7)
        self.assertEqual(app.week.get(), "2026-03-02")

    def test_heatmap_cell_math_and_hover(self):
        from types import SimpleNamespace
        import gui

        app = gui.TeamSyncApp(self.root, DEMO_TEAM)
        app.auto_save.set(False)
        app.compute()
        event = SimpleNamespace(x=app.HEAT_LEFT + 11 * app.CELL_W + 2,
                                y=app.HEAT_TOP + 2)
        self.assertEqual(app.heat_cell(event), (0, 11))
        app.heat_motion(event)
        self.assertIn("4/4 free", app.hover_text.get())
        app.heat_leave()
        self.assertEqual(app.hover_text.get(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
