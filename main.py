"""TeamSync entry point.

    python main.py                     -> graphical window (falls back to the menu)
    python main.py --cli               -> terminal menu only
    python main.py --report            -> print the report and exit, no prompts
    python main.py --weeks 4           -> compare the next four weeks
    python main.py --export txt,csv    -> write the files and exit

Every route calls exactly the same planner, render and storage functions, so the
front ends (and the batch mode) can never drift apart.
"""

import argparse
import os
from datetime import datetime, timedelta, timezone

import ics_import
import render
import storage
from cities import UnknownCity, city_names, load_tz, lookup, make_member
from models import (TeamSyncError, parse_date_list, parse_days, parse_windows)
from planner import (best_week, demo_week, duration_options, explain, parse_week,
                     plan, plan_weeks, week_start_utc)

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO_TEAM = os.path.join(HERE, "data", "demo_team.json")
DEMO_ICS = os.path.join(HERE, "data", "example_calendar.ics")
DEMO_CSV = os.path.join(HERE, "data", "example_roster.csv")
RULE = "=" * 62
THIN = "-" * 62


def resolve_zone(text):
    """A city name or a raw IANA key, whichever the user happened to type."""
    try:
        label, tzkey = lookup(text)
    except UnknownCity:
        label, tzkey = text, text
    load_tz(tzkey)
    return label, tzkey


class CommandLine:
    """A small menu-driven shell around the shared modules."""

    def __init__(self, team_path=None, week=None, duration=None, top=3):
        self.team_name = "COMP9001 Group 7"
        self.week = demo_week()
        self.duration = 60
        self.top = top
        self.roster = []
        self.plan = None                 # the most recent single-week plan
        self.plans = []                  # the most recent multi-week scan
        self.options = []                # the most recent length check
        self.reference = None            # (label, tzkey) once option 9 is used

        self.load(team_path or DEMO_TEAM)
        if week:
            self.week = parse_week(week)
        if duration:
            self.duration = int(duration)

    # ------------------------------------------------------------------ shell

    def run(self):
        while True:
            self.menu()
            choice = input("Select [0-12]: ").strip() or "0"
            if choice == "0":
                print("Bye.")
                return 0
            self.dispatch(choice)

    def menu(self):
        print(RULE)
        print("  TeamSync - Cross-Time-Zone Meeting Planner     [%s]" % self.team_name)
        print(RULE)
        print("   1. Show team members")
        print("   2. Add member / edit availability")
        print("   3. Plan a meeting                          <- core feature")
        print("   4. Compare several weeks")
        print("   5. Check possible meeting lengths")
        print("   6. Explain who can meet at a given time")
        print("   7. Import a busy calendar (.ics)")
        print("   8. Import a roster (.csv)")
        print("   9. Change display timezone")
        print("  10. Save / load team configuration")
        print("  11. Launch graphical window                 <- same engine, nicer UI")
        print("  12. Export last result (txt / ics / html / csv)")
        print("   0. Quit")
        print(THIN)

    def dispatch(self, choice):
        actions = {"1": self.show_members, "2": self.add_member,
                   "3": self.plan_meeting, "4": self.compare_weeks,
                   "5": self.check_lengths, "6": self.explain_time,
                   "7": self.import_calendar, "8": self.import_roster,
                   "9": self.change_reference, "10": self.save_load,
                   "11": self.launch_gui, "12": self.export_last}
        action = actions.get(choice)
        if action is None:
            print("Please pick a number between 0 and 12.")
            return
        try:
            action()
        except (TeamSyncError, ValueError, OSError) as exc:
            print("! %s" % exc)
        print()

    # --------------------------------------------------------------- options

    def load(self, path):
        try:
            self.team_name, self.week, self.duration, self.roster = storage.load_team(path)
        except (TeamSyncError, ValueError, KeyError, OSError) as exc:
            print("! could not read %s: %s" % (path, exc))
            return False
        return True

    def show_members(self):
        if not self.roster:
            print("The team is empty -- pick 2 to add somebody.")
            return
        print(render.members_text(self.roster))

    def add_member(self):
        name = input("Name: ").strip()
        city = input("City, for example Beijing: ").strip()
        days = input("Days [Mon-Fri]: ").strip() or "Mon-Fri"
        text = input("Local windows, for example 09:00-12:00 + 18:00-22:00: ").strip()
        role = input("Must attend? [Y/n]: ").strip().lower()
        away = input("Away dates, e.g. 2026-03-04 or 2026-03-06..2026-03-08 [none]: ").strip()
        member = make_member(name, city, parse_windows(text, parse_days(days.split())),
                             required=(role not in ("n", "no")),
                             away=parse_date_list(away))
        self.roster.append(member)
        print("Added %s (%s, %s, %s)."
              % (member.name, member.city, member.tzkey,
                 "required" if member.required else "optional"))

    def ask_week_and_duration(self):
        text = input("Week starting Monday [%s]: " % self.week.isoformat()).strip()
        if text:
            self.week = parse_week(text)
        text = input("Meeting length in minutes [%d]: " % self.duration).strip()
        if text:
            self.duration = int(text)

    def plan_meeting(self):
        if not self.roster:
            print("Add at least one member first (option 2).")
            return
        self.ask_week_and_duration()

        self.plan = plan(self.roster, self.week, self.duration, top=self.top)
        self.plans = []
        print()
        print(render.report_text(self.plan, self.team_name, storage.utc_stamp()))
        if self.reference:
            label, tzkey = self.reference
            print(render.reference_text(self.plan, tzkey, label))

        path = os.path.join(storage.ensure_output_dir(), "latest.json")
        storage.save_json(path, self.plan, self.team_name)
        print("\nWrote %s" % path)

    def compare_weeks(self):
        if not self.roster:
            print("Add at least one member first (option 2).")
            return
        text = input("How many weeks to compare? [4]: ").strip() or "4"
        weeks = int(text)
        self.ask_week_and_duration()

        self.plans = plan_weeks(self.roster, self.week, self.duration, weeks, top=self.top)
        self.plan = best_week(self.plans)
        print()
        print(render.weeks_text(self.plans))
        if self.plan is not None and self.plan.best is not None:
            print("\nBest week of the range: %s (kept for the next export)."
                  % self.plan.week_start.strftime("%Y-%m-%d"))
            print(render.cards_text(self.plan))

    def check_lengths(self):
        if not self.roster:
            print("Add at least one member first (option 2).")
            return
        text = input("Week starting Monday [%s]: " % self.week.isoformat()).strip()
        if text:
            self.week = parse_week(text)
        self.options = duration_options(self.roster, self.week)
        print()
        print(render.durations_text(self.options, requested=self.duration))

    def explain_time(self):
        if not self.roster:
            print("Add at least one member first (option 2).")
            return
        text = input("UTC time to check, e.g. 2026-03-02 11:00: ").strip()
        moment = datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        checks = explain(self.roster, moment)
        print()
        print(render.explain_text(checks, moment))

    def find_member(self, name):
        wanted = str(name).strip().lower()
        for member in self.roster:
            if member.name.strip().lower() == wanted:
                return member
        raise TeamSyncError("no member called %r -- try option 1 to list them"
                            % (name,))

    def import_calendar(self):
        """Turn one member's exported .ics into busy blocks."""
        if not self.roster:
            print("Add at least one member first (option 2).")
            return
        member = self.find_member(input("Member name: ").strip())
        path = input("Calendar file [%s]: " % DEMO_ICS).strip() or DEMO_ICS
        start = week_start_utc(self.week)
        blocks, warnings = ics_import.read_ics(path, start,
                                               start + timedelta(weeks=8))
        if not blocks:
            print("No busy events were found in that calendar.")
        elif member.busy:
            answer = input("%s already has %d busy event(s). Replace them? [y/N]: "
                           % (member.name, len(member.busy))).strip().lower()
            if answer not in ("y", "yes"):
                print("Kept the existing calendar.")
                return
        if blocks:
            member.busy = blocks
            print("Imported %d busy event(s) for %s."
                  % (len(blocks), member.name))
            for block in blocks[:8]:
                print("   %s  %s" % (block.describe(), block.title))
            if len(blocks) > 8:
                print("   ... and %d more" % (len(blocks) - 8))
        for warning in warnings[:10]:
            print("  ! %s" % warning)

    def import_roster(self):
        """Replace the roster from a spreadsheet (name, city, role, days, ...)."""
        path = input("Roster CSV [%s]: " % DEMO_CSV).strip() or DEMO_CSV
        members, warnings = storage.load_roster_csv(path)
        if self.roster:
            answer = input("Replace the current roster with %d member(s)? [y/N]: "
                           % len(members)).strip().lower()
            if answer not in ("y", "yes"):
                print("Kept the current roster.")
                return
        self.roster = members
        self.plan = None
        self.plans = []
        print("Loaded %d member(s)." % len(members))
        for warning in warnings[:10]:
            print("  ! %s" % warning)

    def change_reference(self):
        if self.reference:
            print("Current reference timezone: %s (%s)" % self.reference)
        print("A few of the bundled cities: %s"
              % ", ".join(city_names()[:8]))
        text = input("Reference city or IANA zone ['none' to clear]: ").strip()
        if not text or text.lower() in ("none", "no", "clear", "-"):
            self.reference = None
            print("Reference timezone cleared -- results stay in UTC only.")
            return
        self.reference = resolve_zone(text)
        print("Reference timezone: %s (%s)" % self.reference)

    def save_load(self):
        text = input("(s)ave or (l)oad? ").strip().lower()
        if text.startswith("l"):
            path = input("Team JSON path [%s]: " % DEMO_TEAM).strip() or DEMO_TEAM
            if self.load(path):
                print("Loaded %d member(s)." % len(self.roster))
        elif text.startswith("s"):
            path = input("Save team as: ").strip()
            if path:
                storage.save_team(path, self.team_name, self.week, self.duration, self.roster)
                print("Saved %s" % path)
        else:
            print("Answer s or l.")

    def launch_gui(self):
        import gui

        path = os.path.join(storage.ensure_output_dir(), "team_session.json")
        storage.save_team(path, self.team_name, self.week, self.duration, self.roster)
        print("Opening the window; it starts from the team you have right here.")
        gui.main(path)

    def export_last(self):
        if self.plan is None:
            print("Plan a meeting first (option 3).")
            return
        kind = input("Format [txt/ics/html/csv]: ").strip().lower() or "txt"
        if kind not in storage.EXPORTERS:
            print("Pick one of: %s" % ", ".join(sorted(storage.EXPORTERS)))
            return
        print("Wrote %s" % storage.export(self.plan, self.team_name, kind))


# ---------------------------------------------------------------- batch mode

def run_batch(args):
    """Compute without a menu: used by --report, --export and --weeks.

    Returns a process exit code. Any problem is printed as one readable line
    instead of a traceback, because this is also how the markers will run it.
    """
    # validate the requested formats before doing any work, so a typo does not
    # print a whole report and then complain
    kinds = []
    if args.export:
        for kind in str(args.export).replace(" ", "").split(","):
            if not kind:
                continue
            if kind not in storage.EXPORTERS:
                print("! unknown export format %r -- pick from %s"
                      % (kind, ", ".join(sorted(storage.EXPORTERS))))
                return 1
            kinds.append(kind)
    if args.weeks is not None and args.weeks < 1:
        print("! --weeks needs a number of weeks of at least 1")
        return 1
    if args.top < 1:
        print("! --top needs to be at least 1")
        return 1

    try:
        team_name, week, duration, roster = storage.load_team(args.file or DEMO_TEAM)
        if args.week is not None:
            week = parse_week(args.week)
        if args.duration is not None:
            duration = args.duration
        if not roster:
            raise TeamSyncError("the team file has no members")

        plans = []
        result = plan(roster, week, duration, top=args.top)
        if args.weeks is not None:
            plans = plan_weeks(roster, week, duration, args.weeks, top=args.top)
            print(render.weeks_text(plans))
            chosen = best_week(plans)
            if chosen is not None:
                result = chosen
            print()

        print(render.report_text(result, team_name, storage.utc_stamp()))
        storage.save_json(os.path.join(storage.ensure_output_dir(), "latest.json"),
                          result, team_name)

        for kind in kinds:
            print("\nWrote %s" % storage.export(result, team_name, kind))
    except (TeamSyncError, ValueError, KeyError, OSError) as exc:
        print("! %s" % exc)
        return 1
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="TeamSync",
        description="Find the best meeting time for a team spread across time zones.")
    parser.add_argument("--cli", action="store_true",
                        help="stay in the terminal even when a window is available")
    parser.add_argument("--file", metavar="PATH", help="team JSON file to load")
    parser.add_argument("--week", metavar="YYYY-MM-DD",
                        help="Monday that starts the week to plan")
    parser.add_argument("--duration", type=int, metavar="MIN",
                        help="meeting length in minutes")
    parser.add_argument("--top", type=int, default=3, metavar="N",
                        help="how many recommendations to show (default 3)")
    parser.add_argument("--weeks", type=int, metavar="N",
                        help="compare N consecutive weeks (implies a batch run)")
    parser.add_argument("--report", action="store_true",
                        help="print the report and exit without a menu")
    parser.add_argument("--export", metavar="FORMATS",
                        help="write files and exit, e.g. --export txt,csv")
    parser.add_argument("--cities", action="store_true",
                        help="list the bundled cities and exit")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.cities:
        names = city_names()
        print("%d cities are bundled in data/cities.json:" % len(names))
        for start in range(0, len(names), 4):
            print("  " + "".join("%-18s" % name
                                 for name in names[start:start + 4]).rstrip())
        return 0

    if (args.report or args.export is not None or args.weeks is not None):
        return run_batch(args)

    if not args.cli:
        try:
            import gui
        except ImportError as exc:
            print("(no graphical window available: %s)" % exc)
            print("(staying in the terminal)")
        else:
            if args.week or args.duration:
                print("Note: --week and --duration apply to the terminal menu only;"
                      " set them in the window instead.")
            return gui.main(args.file)

    return CommandLine(args.file, args.week, args.duration, args.top).run()


if __name__ == "__main__":
    raise SystemExit(main())
