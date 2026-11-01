"""Tkinter front end for TeamSync.

This window only moves data around: it reads the widgets, calls the same
planner / render / storage functions that the command line uses, and puts the
results back on screen.  There is no scoring and no timezone arithmetic in this
file -- that is the one rule that stops the GUI and the CLI from ever
disagreeing about the answer.

The window has three pages, chosen from the left sidebar:

    Team             the roster, attendance roles, away dates, busy calendars
    Recommendations  one card per ranked slot, plus the multi-week comparison
    Week grid        a coloured coverage heatmap and each member's local time

Everything visual lives in this file (colours, fonts, the heatmap canvas); the
numbers come from the shared core, so the GUI can never drift from the CLI.
"""

import os
import subprocess
import sys
import tkinter as tk
from datetime import datetime, timedelta, timezone
from tkinter import filedialog, messagebox, simpledialog, ttk

import ics_import
import planner
import render
import storage
from cities import city_names, make_member
from models import (TeamSyncError, describe_days, format_date_list,
                    parse_date_list, parse_days, parse_windows)
from planner import best_week, duration_options, parse_week, plan, plan_weeks

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TEAM = os.path.join(HERE, "data", "demo_team.json")
EXTENDED_TEAM = os.path.join(HERE, "data", "demo_team_extended.json")
EXAMPLE_ICS = os.path.join(HERE, "data", "example_calendar.ics")
EXAMPLE_CSV = os.path.join(HERE, "data", "example_roster.csv")

LENGTHS = ("30", "45", "60", "90", "120")
WEEK_CHOICES = ("1", "2", "4", "8")

FONT = "Segoe UI"
MONO = "Consolas"

# palette: one navy header, one accent, neutral greys
NAVY = "#16213a"
ACCENT = "#2563eb"
ACCENT_DARK = "#1d4ed8"
BG = "#eef2f7"
CARD = "#ffffff"
BORDER = "#d8e0ea"
TEXT = "#1f2937"
MUTED = "#6b7280"
GOOD = "#15803d"
WARN = "#b45309"
BAD = "#b91c1c"


def members_summary(roster):
    """'5 members  -  4 required  -  1 optional' for the members page header."""
    total = len(roster)
    required = sum(1 for member in roster if member.required)
    busy = sum(1 for member in roster if member.busy)
    parts = ["%d member%s" % (total, "" if total == 1 else "s"),
             "%d required" % required]
    if total - required:
        parts.append("%d optional" % (total - required))
    if busy:
        parts.append("%d with imported calendars" % busy)
    return "  -  ".join(parts)


class MemberDialog(tk.Toplevel):
    """Modal form for one member: name, city, role, weekdays, windows, days away.

    Every window in the form shares one weekday set, which is all the demo team
    needs and keeps the dialog to six fields.
    """

    def __init__(self, parent, title, member=None):
        super().__init__(parent)
        self.result = None
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.configure(bg="white")

        away = ""
        if member is not None and member.away:
            away = format_date_list(member.away)
        self.name_var = tk.StringVar(value=member.name if member else "")
        self.city_var = tk.StringVar(value=member.city if member else "")
        self.role_var = tk.StringVar(
            value="Optional" if member is not None and not member.required else "Required")
        self.days_var = tk.StringVar(
            value=describe_days(member.windows[0].days) if member and member.windows
            else "Mon-Fri")
        self.windows_var = tk.StringVar(
            value=member.windows_text() if member else "09:00-12:00 + 18:00-22:00")
        self.away_var = tk.StringVar(value=away)

        body = tk.Frame(self, bg="white", padx=18, pady=16)
        body.pack(fill="both", expand=True)
        fields = (("Name", self.name_var, None),
                  ("City", self.city_var, city_names()),
                  ("Attendance", self.role_var, ("Required", "Optional")),
                  ("Days", self.days_var, None),
                  ("Local windows", self.windows_var, None),
                  ("Away dates", self.away_var, None))
        for row, (label, variable, choices) in enumerate(fields):
            tk.Label(body, text=label, bg="white", fg=TEXT, font=(FONT, 9)).grid(
                row=row, column=0, sticky="w", pady=4)
            if choices:
                box = ttk.Combobox(body, textvariable=variable, values=choices,
                                   width=30, state="readonly")
            else:
                box = ttk.Entry(body, textvariable=variable, width=32)
            box.grid(row=row, column=1, sticky="we", padx=(10, 0), pady=4)

        tk.Label(body, foreground=MUTED, background="white", justify="left",
                 wraplength=360, font=(FONT, 8),
                 text="Days accepts Mon-Fri or Mon,Wed. Windows are local times,"
                      " for example 09:00-12:00 + 18:00-22:00. Away dates are local"
                      " dates too: 2026-03-04, 2026-03-06..2026-03-08.").grid(
            row=len(fields), column=0, columnspan=2, sticky="w", pady=(10, 0))

        bar = tk.Frame(body, bg="white")
        bar.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="Save", style="Accent.TButton",
                   command=self.confirm).pack(side="right", padx=(0, 8))

        self.bind("<Return>", lambda event: self.confirm())
        self.bind("<Escape>", lambda event: self.destroy())
        self.grab_set()
        self.wait_window(self)

    def confirm(self):
        name = self.name_var.get().strip()
        try:
            if not name:
                raise TeamSyncError("please give the member a name")
            windows = parse_windows(self.windows_var.get(),
                                    parse_days(self.days_var.get().split()))
            member = make_member(name, self.city_var.get().strip(), windows,
                                 required=(self.role_var.get() != "Optional"),
                                 away=parse_date_list(self.away_var.get()))
        except (TeamSyncError, ValueError, OSError) as exc:
            messagebox.showerror("TeamSync", str(exc), parent=self)
            return
        self.result = member
        self.destroy()


class ScrollFrame(tk.Frame):
    """A canvas-backed frame that scrolls vertically (used by the results page)."""

    def __init__(self, parent, background=BG):
        super().__init__(parent, bg=background)
        self.canvas = tk.Canvas(self, bg=background, highlightthickness=0)
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")

        self.inner = tk.Frame(self.canvas, bg=background)
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    def _on_inner(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)

    def _bind_wheel(self, event):
        self.canvas.bind_all("<MouseWheel>", self._wheel)

    def _unbind_wheel(self, event):
        self.canvas.unbind_all("<MouseWheel>")

    def _wheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def clear(self):
        for child in self.inner.winfo_children():
            child.destroy()
        self.canvas.yview_moveto(0)


class TeamSyncApp:
    """The main window. ``team_path`` is the JSON file loaded at start-up."""

    # heatmap geometry (a class constant, because two methods share it)
    HEAT_LEFT = 88
    HEAT_TOP = 34
    CELL_W = 33
    CELL_H = 38
    HEAT_WIDTH = HEAT_LEFT + 24 * CELL_W + 2
    HEAT_HEIGHT = HEAT_TOP + 5 * CELL_H + 6

    def __init__(self, root, team_path=None):
        self.root = root
        self.roster = []
        self.plan = None
        self.plans = []
        self.team_name = tk.StringVar(value="COMP9001 Group 7")
        self.length = tk.StringVar(value="60")
        self.weeks = tk.StringVar(value="1")
        self.week = tk.StringVar(value="2026-03-02")
        self.status = tk.StringVar(value="Ready. Press Compute best times.")
        self.auto_save = tk.BooleanVar(value=True)
        self.summary = tk.StringVar(value="")
        self.filter_var = tk.StringVar(value="")
        self.hover_text = tk.StringVar(value="")
        self._hover = None

        root.title("TeamSync - Meeting Time Planner")
        root.geometry("1180x780")
        root.minsize(1150, 700)
        root.configure(bg=BG)

        self._style()
        self._build()
        self._bind_shortcuts()
        self.load_team(team_path or DEFAULT_TEAM)

    # ---------------------------------------------------------------- style

    def _style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=(FONT, 10), background=BG, foreground=TEXT)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED,
                        font=(FONT, 9))
        style.configure("H1.TLabel", background=NAVY, foreground="white",
                        font=(FONT, 15, "bold"))
        style.configure("TButton", padding=(10, 6), borderwidth=0,
                        background="#e2e8f0", foreground=TEXT, font=(FONT, 9))
        style.map("TButton", background=[("active", "#d3dced")])
        style.configure("Accent.TButton", background=ACCENT, foreground="white",
                        font=(FONT, 9, "bold"), padding=(14, 7), borderwidth=0)
        style.map("Accent.TButton", background=[("active", ACCENT_DARK)])
        style.configure("Nav.TButton", background="#f7f9fc", foreground="#334155",
                        anchor="w", padding=(16, 11), borderwidth=0,
                        font=(FONT, 10))
        style.map("Nav.TButton", background=[("active", "#e3ebf7")])
        style.configure("NavActive.TButton", background=ACCENT, foreground="white",
                        anchor="w", padding=(16, 11), borderwidth=0,
                        font=(FONT, 10, "bold"))
        style.map("NavActive.TButton", background=[("active", ACCENT)])
        style.configure("Treeview", background="white", fieldbackground="white",
                        rowheight=26, borderwidth=0, font=(FONT, 9))
        style.configure("Treeview.Heading", background="#e8eef6",
                        foreground="#334155", relief="flat",
                        font=(FONT, 9, "bold"), padding=(4, 6))
        style.map("Treeview", background=[("selected", "#dbeafe")],
                  foreground=[("selected", "#111827")])
        style.map("Treeview.Heading", background=[("active", "#dce6f2")])
        style.configure("TCombobox", padding=(4, 4))
        style.configure("TEntry", padding=(4, 4))
        style.configure("TCheckbutton", background=CARD, foreground=TEXT,
                        font=(FONT, 9))

    def _bind_shortcuts(self):
        """Keyboard shortcuts for the actions used most during a demo."""
        self.root.bind("<Control-Return>", lambda event: self.compute())
        self.root.bind("<F5>", lambda event: self.compute())
        self.root.bind("<Control-s>", lambda event: self.save_team_file())
        self.root.bind("<Control-i>", lambda event: self.import_busy())

    # ---------------------------------------------------------------- layout

    def _build(self):
        self.pages = {}
        self.nav_buttons = {}
        self._build_menu()
        self._build_header()
        self._build_sidebar()
        self._build_status()
        self._build_footer()

        self.content = tk.Frame(self.root, bg=BG)
        self.content.pack(side="left", fill="both", expand=True,
                          padx=18, pady=(14, 4))
        self._build_settings()
        self.stack = tk.Frame(self.content, bg=BG)
        self.stack.pack(fill="both", expand=True)
        self.stack.grid_rowconfigure(0, weight=1)
        self.stack.grid_columnconfigure(0, weight=1)

        self._build_members_page()
        self._build_results_page()
        self._build_grid_page()
        self.show_page("members")

    def _build_header(self):
        header = tk.Frame(self.root, bg=NAVY, height=58)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="TeamSync", bg=NAVY, fg="white",
                 font=(FONT, 16, "bold")).pack(side="left", padx=(20, 8))
        tk.Label(header, text="find the least painful meeting time",
                 bg=NAVY, fg="#9fb0c9", font=(FONT, 9)).pack(side="left")

    def _build_sidebar(self):
        sidebar = tk.Frame(self.root, bg="#f7f9fc", width=190)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text="WORKSPACE", bg="#f7f9fc", fg="#94a3b8",
                 font=(FONT, 8, "bold")).pack(anchor="w", padx=18, pady=(20, 8))
        for key, label in (("members", "Team"), ("results", "Recommendations"),
                           ("grid", "Week grid")):
            button = ttk.Button(sidebar, text="  " + label, style="Nav.TButton",
                                command=lambda k=key: self.show_page(k))
            button.pack(fill="x", padx=10, pady=2)
            self.nav_buttons[key] = button
        tk.Label(sidebar, text="FILES", bg="#f7f9fc", fg="#94a3b8",
                 font=(FONT, 8, "bold")).pack(anchor="w", padx=18, pady=(22, 6))
        for label, command in (("Import team JSON...", self.pick_team_file),
                               ("Save team JSON...", self.save_team_file),
                               ("Import roster CSV...", self.import_roster),
                               ("Open output folder", self.open_output)):
            ttk.Button(sidebar, text=label, style="Nav.TButton",
                       command=command).pack(fill="x", padx=10, pady=1)

    def _build_settings(self):
        card = tk.Frame(self.content, bg=CARD, highlightbackground=BORDER,
                        highlightthickness=1)
        card.pack(fill="x", pady=(0, 12))
        inner = tk.Frame(card, bg=CARD, padx=14, pady=10)
        inner.pack(fill="x")

        tk.Label(inner, text="Team", bg=CARD, fg=MUTED, font=(FONT, 8)).grid(
            row=0, column=0, sticky="w")
        ttk.Entry(inner, textvariable=self.team_name, width=26).grid(
            row=1, column=0, sticky="w", padx=(0, 12))

        tk.Label(inner, text="Length", bg=CARD, fg=MUTED, font=(FONT, 8)).grid(
            row=0, column=1, sticky="w")
        ttk.Combobox(inner, textvariable=self.length, width=5, state="readonly",
                     values=LENGTHS).grid(row=1, column=1, sticky="w", padx=(0, 10))

        tk.Label(inner, text="Weeks", bg=CARD, fg=MUTED, font=(FONT, 8)).grid(
            row=0, column=2, sticky="w")
        ttk.Combobox(inner, textvariable=self.weeks, width=3, state="readonly",
                     values=WEEK_CHOICES).grid(row=1, column=2, sticky="w", padx=(0, 10))

        tk.Label(inner, text="Week of (Mon)", bg=CARD, fg=MUTED,
                 font=(FONT, 8)).grid(row=0, column=3, sticky="w")
        week_box = tk.Frame(inner, bg=CARD)
        week_box.grid(row=1, column=3, sticky="w", padx=(0, 12))
        ttk.Button(week_box, text="<", width=2,
                   command=lambda: self.shift_week(-7)).pack(side="left")
        ttk.Entry(week_box, textvariable=self.week, width=12).pack(
            side="left", padx=3)
        ttk.Button(week_box, text=">", width=2,
                   command=lambda: self.shift_week(7)).pack(side="left")

        ttk.Button(inner, text="Compute best times", style="Accent.TButton",
                   command=self.compute).grid(row=1, column=4, padx=(4, 6))
        ttk.Button(inner, text="Reset demo", command=self.reset).grid(
            row=1, column=5)

    def _build_footer(self):
        footer = tk.Frame(self.root, bg=CARD, highlightbackground=BORDER,
                          highlightthickness=1)
        footer.pack(side="bottom", fill="x")
        row = tk.Frame(footer, bg=CARD, padx=12, pady=8)
        row.pack(fill="x")
        tk.Label(row, text="EXPORT", bg=CARD, fg=MUTED,
                 font=(FONT, 8, "bold")).pack(side="left", padx=(0, 8))
        for kind, label in (("txt", "TXT"), ("ics", "ICS"),
                            ("html", "HTML"), ("csv", "CSV")):
            ttk.Button(row, text=label,
                       command=lambda k=kind: self.export(k)).pack(side="left", padx=2)
        ttk.Button(row, text="Copy invite",
                   command=self.copy_invite).pack(side="left", padx=(12, 2))
        ttk.Checkbutton(row, text="Auto-save latest.json",
                        variable=self.auto_save).pack(side="left", padx=16)
        ttk.Button(row, text="Open output folder",
                   command=self.open_output).pack(side="right")

    def _build_status(self):
        tk.Label(self.root, textvariable=self.status, bg="#0f172a", fg="#cbd5e1",
                 anchor="w", padx=14, pady=6, font=(FONT, 9)).pack(
            side="bottom", fill="x")

    def show_page(self, name):
        """Raise one page and highlight its sidebar button."""
        self.pages[name].tkraise()
        for key, button in self.nav_buttons.items():
            button.configure(style="NavActive.TButton" if key == name
                             else "Nav.TButton")

    # ------------------------------------------------------------ members page

    def _build_members_page(self):
        page = tk.Frame(self.stack, bg=BG)
        page.grid(row=0, column=0, sticky="nsew")
        self.pages["members"] = page

        toolbar = tk.Frame(page, bg=BG)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(toolbar, text="Add member", style="Accent.TButton",
                   command=self.add_member).pack(side="left", padx=(0, 6))
        for label, command in (("Edit selected", self.edit_member),
                               ("Remove selected", self.remove_member),
                               ("Import busy .ics...", self.import_busy),
                               ("Clear busy", self.clear_busy)):
            ttk.Button(toolbar, text=label, command=command).pack(
                side="left", padx=3)
        tk.Label(toolbar, text="Filter", bg=BG, fg=MUTED,
                 font=(FONT, 8)).pack(side="left", padx=(14, 4))
        ttk.Entry(toolbar, textvariable=self.filter_var, width=16).pack(
            side="left")
        self.filter_var.trace_add("write", lambda *args: self.refresh_members())
        tk.Label(toolbar, textvariable=self.summary, bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(side="right")

        card = tk.Frame(page, bg=CARD, highlightbackground=BORDER,
                        highlightthickness=1)
        card.pack(fill="both", expand=True)
        columns = ("name", "city", "tzkey", "role", "away", "busy", "windows")
        headings = ("Name", "City", "Timezone", "Role", "Away", "Busy",
                    "Local windows")
        widths = (95, 85, 135, 65, 95, 70, 330)
        self.members = ttk.Treeview(card, columns=columns, show="headings",
                                    height=11)
        for column, heading, width in zip(columns, headings, widths):
            self.members.heading(column, text=heading)
            self.members.column(column, width=width, anchor="w")
        self.members.tag_configure("odd", background="#f8fafc")
        self.members.tag_configure("optional", foreground=MUTED)
        self.members.pack(side="left", fill="both", expand=True,
                          padx=(10, 0), pady=10)
        scroll = ttk.Scrollbar(card, orient="vertical", command=self.members.yview)
        self.members.configure(yscrollcommand=scroll.set)
        scroll.pack(side="left", fill="y", pady=10)
        self.members.bind("<Double-1>", lambda event: self.edit_member())

    # ----------------------------------------------------------- results page

    def _build_results_page(self):
        page = tk.Frame(self.stack, bg=BG)
        page.grid(row=0, column=0, sticky="nsew")
        self.pages["results"] = page
        self.results_view = ScrollFrame(page, background=BG)
        self.results_view.pack(fill="both", expand=True)
        self._show_empty_results()

    # -------------------------------------------------------------- grid page

    def _build_grid_page(self):
        page = tk.Frame(self.stack, bg=BG)
        page.grid(row=0, column=0, sticky="nsew")
        self.pages["grid"] = page

        card = tk.Frame(page, bg=CARD, highlightbackground=BORDER,
                        highlightthickness=1)
        card.pack(fill="x")
        self.heat = tk.Canvas(card, bg=CARD, width=self.HEAT_WIDTH,
                              height=self.HEAT_HEIGHT, highlightthickness=0)
        self.heat.pack(anchor="w", padx=12, pady=(10, 2))
        self.heat.configure(cursor="hand2")
        self.heat.bind("<Button-1>", self.heat_click)
        self.heat.bind("<Motion>", self.heat_motion)
        self.heat.bind("<Leave>", self.heat_leave)

        legend = tk.Frame(card, bg=CARD)
        legend.pack(fill="x", padx=14, pady=(0, 10))
        tk.Label(legend, text="Coverage:", bg=CARD, fg=MUTED,
                 font=(FONT, 8)).pack(side="left")
        for count in range(1, 5):
            self._swatch(legend, self.coverage_colour(count, 5), str(count))
        self._swatch(legend, GOOD, "everyone")
        tk.Label(legend, text="Click a cell for local times."
                              "  Outlined cells are the recommendations.",
                 bg=CARD, fg=MUTED, font=(FONT, 8)).pack(side="left", padx=12)
        tk.Label(legend, textvariable=self.hover_text, bg=CARD, fg=ACCENT,
                 font=(FONT, 8, "bold")).pack(side="right")

        table_card = tk.Frame(page, bg=CARD, highlightbackground=BORDER,
                              highlightthickness=1)
        table_card.pack(fill="x", pady=(12, 0))
        tk.Label(table_card, text="Each member's local start time",
                 bg=CARD, fg=TEXT, font=(FONT, 9, "bold")).pack(
            anchor="w", padx=14, pady=(10, 2))
        self.localview = ttk.Treeview(
            table_card, columns=("member", "role", "r1", "r2", "r3"),
            show="headings", height=5)
        self.localview.heading("member", text="Member")
        self.localview.heading("role", text="Role")
        self.localview.column("member", width=140, anchor="w")
        self.localview.column("role", width=90, anchor="w")
        for name in ("r1", "r2", "r3"):
            self.localview.heading(name, text="-")
            self.localview.column(name, width=190, anchor="w")
        self.localview.pack(fill="x", padx=10, pady=(0, 10))
        self.localview.tag_configure("odd", background="#f8fafc")

    def _swatch(self, parent, colour, text):
        box = tk.Frame(parent, bg=colour, width=18, height=14,
                       highlightbackground=BORDER, highlightthickness=1)
        box.pack(side="left", padx=(8, 3))
        box.pack_propagate(False)
        tk.Label(parent, text=text, bg=CARD, fg=TEXT, font=(FONT, 8)).pack(
            side="left", padx=(0, 4))

    @staticmethod
    def coverage_colour(count, total):
        """Light blue -> deep blue for partial coverage, green for everybody."""
        if count <= 0:
            return "#eef1f5"
        if total and count >= total:
            return GOOD
        shades = ("#dbeafe", "#93c5fd", "#60a5fa", "#3b82f6")
        step = int((count / float(total or 1)) * len(shades))
        return shades[min(step, len(shades) - 1)]

    @staticmethod
    def coverage_text_colour(count, total):
        if count >= total or (total and count / float(total) > 0.6):
            return "white"
        return "#1e3a5f"

    def draw_heatmap(self, result):
        canvas = self.heat
        canvas.delete("all")
        self._hover = None
        self.hover_text.set("")
        total = len(result.roster)
        for hour in range(24):
            canvas.create_text(self.HEAT_LEFT + hour * self.CELL_W + self.CELL_W / 2,
                               16, text="%02d" % hour, fill=MUTED,
                               font=(FONT, 8))
        for row, (day, counts) in enumerate(result.hourly_rows()):
            top = self.HEAT_TOP + row * self.CELL_H
            canvas.create_text(10, top + self.CELL_H / 2,
                               text=day.strftime("%a %d %b"), anchor="w",
                               fill=TEXT, font=(FONT, 9))
            for hour, count in enumerate(counts):
                left = self.HEAT_LEFT + hour * self.CELL_W
                fill = self.coverage_colour(count, total)
                canvas.create_rectangle(left, top, left + self.CELL_W - 2,
                                        top + self.CELL_H - 2, fill=fill,
                                        outline="")
                if count:
                    canvas.create_text(left + (self.CELL_W - 2) / 2,
                                       top + (self.CELL_H - 2) / 2,
                                       text="ALL" if count == total else str(count),
                                       fill=self.coverage_text_colour(count, total),
                                       font=(FONT, 8, "bold"))
        # a dark outline marks the hours that actually got recommended
        for item in result.recommendations:
            row = (item.start.date() - result.week_start.date()).days
            hour = item.start.hour
            if 0 <= row < 5 and 0 <= hour < 24:
                left = self.HEAT_LEFT + hour * self.CELL_W
                top = self.HEAT_TOP + row * self.CELL_H
                canvas.create_rectangle(left + 1, top + 1,
                                        left + self.CELL_W - 3,
                                        top + self.CELL_H - 3,
                                        outline="#0f172a", width=2)

    def heat_cell(self, event):
        """Which (row, hour) cell of the heatmap is under the pointer?"""
        hour = int((event.x - self.HEAT_LEFT) // self.CELL_W)
        row = int((event.y - self.HEAT_TOP) // self.CELL_H)
        if 0 <= hour < 24 and 0 <= row < 5:
            return row, hour
        return None

    def heat_motion(self, event):
        """Live coverage read-out next to the legend while hovering."""
        cell = self.heat_cell(event)
        if cell is None or self.plan is None:
            self.heat_leave()
            return
        if cell == self._hover:
            return
        self._hover = cell
        row, hour = cell
        moment = self.plan.week_start + timedelta(days=row, hours=hour)
        counts = self.plan.hourly_rows()[row][1]
        self.hover_text.set("%s %02d:00 UTC  -  %d/%d free"
                            % (moment.strftime("%a %d %b"), hour, counts[hour],
                               len(self.plan.roster)))

    def heat_leave(self, event=None):
        self._hover = None
        self.hover_text.set("")

    def heat_click(self, event):
        """Click on a heatmap cell -> explain that UTC hour."""
        if self.plan is None:
            self.warn("Compute the week first.", "Then click a coloured cell.")
            return
        cell = self.heat_cell(event)
        if cell is None:
            return
        row, hour = cell
        moment = self.plan.week_start + timedelta(days=row, hours=hour)
        self.explain_at(moment)

    # ------------------------------------------------------------------ data

    def warn(self, message, hint=None):
        text = message if hint is None else "%s\n\n%s" % (message, hint)
        messagebox.showerror("TeamSync", text, parent=self.root)
        self.status.set(message.splitlines()[0])

    def refresh_members(self):
        """Rebuild the table, honouring the filter box.

        The row id is the index in ``self.roster``, not the position in the
        filtered view, so editing still works while the table is filtered.
        """
        self.members.delete(*self.members.get_children())
        wanted = self.filter_var.get().strip().lower()
        for index, row in enumerate(render.member_rows(self.roster)):
            haystack = "%s %s %s" % (row["name"], row["city"], row["tzkey"])
            if wanted and wanted not in haystack.lower():
                continue
            tags = []
            if index % 2:
                tags.append("odd")
            if row["role"] == "optional":
                tags.append("optional")
            self.members.insert("", "end", iid=str(index), tags=tuple(tags), values=(
                row["name"], row["city"], row["tzkey"], row["role"],
                row["away"], row["busy"], row["windows"]))
        self.summary.set(members_summary(self.roster))

    def load_team(self, path):
        try:
            team_name, week, duration, roster = storage.load_team(path)
        except (TeamSyncError, ValueError, KeyError, OSError) as exc:
            self.warn("Could not read the team file.", str(exc))
            return
        self.team_name.set(team_name)
        self.week.set(week.strftime("%Y-%m-%d"))
        self.length.set(str(duration))
        self.roster = list(roster)
        self.plan = None
        self.plans = []
        self.filter_var.set("")          # a stale filter must not hide the new team
        self.refresh_members()
        self._show_empty_results()
        self.heat.delete("all")
        self.localview.delete(*self.localview.get_children())
        self.show_page("members")
        self.status.set("Loaded %s with %d member(s). Press Compute best times."
                        % (os.path.basename(path), len(self.roster)))

    def reset(self):
        self.load_team(DEFAULT_TEAM)

    def pick_team_file(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="Load team JSON",
            initialdir=os.path.dirname(DEFAULT_TEAM),
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if path:
            self.load_team(path)

    def save_team_file(self):
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Save team as", defaultextension=".json",
            initialfile="team.json", filetypes=[("JSON files", "*.json")])
        if not path:
            return
        try:
            week = parse_week(self.week.get())
        except (TeamSyncError, ValueError) as exc:
            self.warn("Cannot save the team file.", str(exc))
            return
        try:
            storage.save_team(path, self.team_name.get().strip() or "TeamSync",
                              week, int(self.length.get()), self.roster)
        except OSError as exc:
            self.warn("Could not save the team file.", str(exc))
            return
        self.status.set("Saved %s" % os.path.basename(path))

    # ------------------------------------------------------------ member edits

    def add_member(self):
        dialog = MemberDialog(self.root, "Add member")
        if dialog.result:
            self.roster.append(dialog.result)
            self.refresh_members()
            self.status.set("Added %s." % dialog.result.name)

    def edit_member(self):
        selection = self.members.selection()
        if not selection:
            self.warn("Select a member in the table first.")
            return
        index = int(selection[0])
        dialog = MemberDialog(self.root, "Edit member", self.roster[index])
        if dialog.result:
            busy = self.roster[index].busy
            self.roster[index] = dialog.result
            self.roster[index].busy = busy        # editing a name keeps the calendar
            self.refresh_members()
            self.status.set("Updated %s." % dialog.result.name)

    def remove_member(self):
        selection = self.members.selection()
        if not selection:
            self.warn("Select a member in the table first.")
            return
        for index in sorted((int(item) for item in selection), reverse=True):
            self.roster.pop(index)
        self.refresh_members()
        self.status.set("%d member(s) left." % len(self.roster))

    def selected_member(self):
        selection = self.members.selection()
        if not selection:
            return None
        return self.roster[int(selection[0])]

    def import_busy(self):
        """Read a member's .ics calendar and turn it into busy blocks."""
        member = self.selected_member()
        if member is None:
            self.warn("Select the member whose calendar you want to import.")
            return
        try:
            week = parse_week(self.week.get())
        except (TeamSyncError, ValueError) as exc:
            self.warn(str(exc), "Week of (Mon) must look like 2026-03-02.")
            return
        path = filedialog.askopenfilename(
            parent=self.root, title="Import busy calendar for %s" % member.name,
            initialdir=os.path.dirname(EXAMPLE_ICS),
            filetypes=[("Calendar files", "*.ics"), ("All files", "*.*")])
        if not path:
            return
        start = planner.week_start_utc(week)
        try:
            blocks, warnings = ics_import.read_ics(
                path, start, start + timedelta(weeks=8))
        except (TeamSyncError, OSError) as exc:
            self.warn("Could not import the calendar.", str(exc))
            return
        if not blocks:
            self.warn("No busy events were found in that calendar.",
                      "\n".join(warnings[:5]) or None)
            return
        if member.busy and not messagebox.askyesno(
                "TeamSync",
                "Replace the %d busy event(s) already on %s?"
                % (len(member.busy), member.name), parent=self.root):
            return
        member.busy = blocks
        self.refresh_members()
        self.status.set("Imported %d busy event(s) for %s."
                        % (len(blocks), member.name))
        if warnings:
            self.text_window("Calendar import warnings",
                             "\n".join(warnings[:30]))

    def clear_busy(self):
        member = self.selected_member()
        if member is None:
            self.warn("Select a member in the table first.")
            return
        if not member.busy:
            self.status.set("%s has no imported calendar." % member.name)
            return
        count = len(member.busy)
        member.busy = []
        self.refresh_members()
        self.status.set("Cleared %d busy event(s) for %s." % (count, member.name))

    def import_roster(self):
        """Bulk-add members from a spreadsheet and replace the current roster."""
        path = filedialog.askopenfilename(
            parent=self.root, title="Import roster CSV",
            initialdir=os.path.dirname(EXAMPLE_CSV),
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        try:
            members, warnings = storage.load_roster_csv(path)
        except (TeamSyncError, OSError) as exc:
            self.warn("Could not import the roster.", str(exc))
            return
        if self.roster and not messagebox.askyesno(
                "TeamSync", "Replace the current roster with %d member(s)?"
                % len(members), parent=self.root):
            return
        self.roster = members
        self.refresh_members()
        self.plan = None
        self.plans = []
        self._show_empty_results()
        self.heat.delete("all")
        self.localview.delete(*self.localview.get_children())
        self.status.set("Imported %d member(s) from %s."
                        % (len(members), os.path.basename(path)))
        if warnings:
            self.text_window("Roster import warnings", "\n".join(warnings[:30]))

    # --------------------------------------------------------------- compute

    def compute(self):
        """Run the shared pipeline and show it. Returns the Plan, or None."""
        if not self.roster:
            self.warn("This team has no members yet.", "Add a member first.")
            return None
        try:
            week = parse_week(self.week.get())
        except (TeamSyncError, ValueError) as exc:
            self.warn(str(exc), "Week of (Mon) must look like 2026-03-02.")
            return None
        try:
            duration = int(self.length.get())
            weeks = int(self.weeks.get())
        except ValueError:
            self.warn("Length and Weeks must be numbers.")
            return None

        try:
            if weeks > 1:
                self.plans = plan_weeks(self.roster, week, duration, weeks, top=3)
                result = best_week(self.plans)
            else:
                self.plans = []
                result = plan(self.roster, week, duration)
        except (TeamSyncError, ValueError, OSError) as exc:
            self.warn("Could not compute the schedule.", str(exc))
            return None

        self.plan = result
        self.show_results(result)
        self.draw_heatmap(result)
        self.show_local_times(result)
        if self.auto_save.get():
            try:
                storage.save_json(os.path.join(storage.ensure_output_dir(), "latest.json"),
                                  result, self.team_name.get().strip() or "TeamSync")
            except OSError as exc:
                self.warn("Could not write latest.json.", str(exc))
                return result
        best = result.best
        suffix = " (best of %d weeks)" % len(self.plans) if self.plans else ""
        self.status.set("%d recommendation(s)%s. Best: %s UTC (%d/%d members)."
                        % (len(result.recommendations), suffix,
                           best.start.strftime("%a %H:%M") if best else "none",
                           best.count if best else 0, len(result.roster)))
        return result

    def show_results(self, result):
        """Rebuild the recommendation cards from scratch."""
        view = self.results_view
        view.clear()
        total = len(result.roster)

        if not result.recommendations:
            self._card_message(
                "No meeting fits this week",
                "No slot is long enough for %d minutes. The longest shared window"
                " is %.1f h." % (result.duration_min,
                                 result.best_continuous_min / 60.0),
                WARN)
            options = duration_options(self.roster, result.week_start)
            self._text_block("Meeting lengths that fit",
                             render.durations_text(options,
                                                   requested=result.duration_min))
        for rank, item in enumerate(result.recommendations, 1):
            self._recommendation_card(rank, item, total, result)

        if self.plans:
            self._text_block("Multi-week scan", render.weeks_text(self.plans))

        if result.recommendations:
            best = result.best
            missing = "" if not best.missing else (
                "  Missing: " + ", ".join(
                    "%s%s" % (member.name,
                              "" if member.required else " (optional)")
                    for member in best.missing))
            self._card_message(
                "Why this time?",
                "Ranking: all required members present, then number of attendees,"
                " then comfort score, then the earliest time. Only the start cell"
                " is scored, so a longer meeting is not rewarded for covering more"
                " comfortable hours." + missing, MUTED)

    def _recommendation_card(self, rank, item, total, plan):
        winner = rank == 1
        card = tk.Frame(self.results_view.inner, bg=CARD,
                        highlightbackground=ACCENT if winner else BORDER,
                        highlightthickness=2 if winner else 1)
        card.pack(fill="x", pady=(0, 10))

        top = tk.Frame(card, bg=CARD)
        top.pack(fill="x", padx=16, pady=(12, 0))
        tk.Label(top, text=("%s Recommendation %d" % ("\u2605" if winner else "",
                                                     rank)).strip(),
                 bg=CARD, fg=ACCENT if winner else TEXT,
                 font=(FONT, 11, "bold")).pack(side="left")
        tk.Label(top, text="%d/%d members  -  score %d/%d  -  %.1f h window"
                 % (item.count, total, item.score, plan.max_score,
                    item.continuous_hours),
                 bg=CARD, fg=MUTED, font=(FONT, 9)).pack(side="right")

        when = tk.Frame(card, bg=CARD)
        when.pack(fill="x", padx=16, pady=(6, 0))
        tk.Label(when, text=item.start.strftime("%a %d %b  %H:%M UTC"), bg=CARD,
                 fg=ACCENT, font=(FONT, 16, "bold")).pack(side="left")
        tk.Label(when, text="%d minutes" % item.duration_min, bg=CARD,
                 fg=MUTED, font=(FONT, 9)).pack(side="left", padx=(10, 0))

        table = tk.Frame(card, bg=CARD)
        table.pack(fill="x", padx=16, pady=(8, 14))
        for column, text in enumerate(("Member", "City", "Their local time",
                                       "Role", "Note")):
            tk.Label(table, text=text, bg=CARD, fg=MUTED, font=(FONT, 8),
                     anchor="w").grid(row=0, column=column, sticky="w",
                                      padx=(0, 16), pady=(0, 2))
        row = 1
        for member in item.members:
            moment = member.local(item.start)
            minutes = moment.hour * 60 + moment.minute
            note = "late" if minutes >= 22 * 60 else "early" if minutes < 7 * 60 else ""
            self._member_row(table, row, member, moment.strftime("%a %H:%M"),
                             "required" if member.required else "optional",
                             note, WARN)
            row += 1
        for member in item.missing:
            self._member_row(table, row, member, "cannot attend",
                             "required" if member.required else "optional",
                             "missing", BAD)
            row += 1

    def _member_row(self, parent, row, member, local_text, role, note, note_colour):
        tk.Label(parent, text=member.name, bg=CARD, fg=TEXT,
                 font=(FONT, 9, "bold"), anchor="w").grid(
            row=row, column=0, sticky="w", padx=(0, 16), pady=1)
        tk.Label(parent, text=member.city, bg=CARD, fg=MUTED,
                 anchor="w").grid(row=row, column=1, sticky="w", padx=(0, 16))
        tk.Label(parent, text=local_text, bg=CARD, fg=TEXT, anchor="w").grid(
            row=row, column=2, sticky="w", padx=(0, 16))
        tk.Label(parent, text=role, bg=CARD, fg=MUTED, anchor="w").grid(
            row=row, column=3, sticky="w", padx=(0, 16))
        tk.Label(parent, text=note, bg=CARD,
                 fg=note_colour if note else TEXT, anchor="w").grid(
            row=row, column=4, sticky="w")

    def _card_message(self, title, text, colour):
        card = tk.Frame(self.results_view.inner, bg=CARD,
                        highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x", pady=(0, 10))
        body = tk.Frame(card, bg=CARD, padx=16, pady=12)
        body.pack(fill="x")
        tk.Label(body, text=title, bg=CARD, fg=colour,
                 font=(FONT, 10, "bold"), anchor="w").pack(fill="x")
        tk.Label(body, text=text, bg=CARD, fg=TEXT, justify="left", anchor="w",
                 wraplength=820, font=(FONT, 9)).pack(fill="x", pady=(4, 0))

    def _text_block(self, title, text):
        card = tk.Frame(self.results_view.inner, bg=CARD,
                        highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x", pady=(0, 10))
        body = tk.Frame(card, bg=CARD, padx=16, pady=12)
        body.pack(fill="x")
        tk.Label(body, text=title, bg=CARD, fg=TEXT,
                 font=(FONT, 10, "bold"), anchor="w").pack(fill="x")
        tk.Label(body, text=text, bg=CARD, fg="#334155", justify="left",
                 anchor="w", font=(MONO, 9)).pack(fill="x", pady=(6, 0))

    def show_local_times(self, result):
        for slot in ("r1", "r2", "r3"):
            self.localview.heading(slot, text="-")
        for index, item in enumerate(result.recommendations[:3]):
            self.localview.heading("r%d" % (index + 1),
                                   text="#%d  %s UTC"
                                        % (index + 1, item.start.strftime("%a %H:%M")))
        self.localview.delete(*self.localview.get_children())
        for index, member in enumerate(result.roster):
            row = [member.name, "required" if member.required else "optional"]
            for item in result.recommendations[:3]:
                row.append(member.local(item.start).strftime("%a %d %b %H:%M"))
            while len(row) < 5:
                row.append("")
            tags = ("odd",) if index % 2 else ()
            self.localview.insert("", "end", tags=tags, values=row)

    def _show_empty_results(self):
        self.results_view.clear()
        self._card_message(
            "No plan yet",
            "Choose a week and press Compute best times. The top three slots"
            " appear here, translated into every member's own clock.",
            ACCENT)

    def shift_week(self, days):
        """Move the planned week by a number of days (the < and > buttons)."""
        try:
            week = parse_week(self.week.get())
        except (TeamSyncError, ValueError) as exc:
            self.warn(str(exc), "Week of (Mon) must look like 2026-03-02.")
            return
        self.week.set((week + timedelta(days=days)).strftime("%Y-%m-%d"))
        if self.plan is not None:
            self.compute()

    def copy_invite(self):
        """Put a ready-to-paste invitation for the best slot on the clipboard."""
        if self.plan is None or self.plan.best is None:
            self.warn("There is no recommendation to copy yet.",
                      "Press Compute best times first.")
            return
        text = render.invite_text(self.plan,
                                  self.team_name.get().strip() or "TeamSync")
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()          # keeps the clipboard alive on X11
        self.status.set("Invite copied - paste it into your chat or email.")

    def show_lengths(self):
        """Menu action: which meeting lengths work in the selected week?"""
        if not self.roster:
            self.warn("This team has no members yet.", "Add a member first.")
            return
        try:
            week = parse_week(self.week.get())
        except (TeamSyncError, ValueError) as exc:
            self.warn(str(exc), "Week of (Mon) must look like 2026-03-02.")
            return
        options = duration_options(self.roster, week)
        self.text_window("Meeting lengths",
                         render.durations_text(options, requested=int(self.length.get())))

    def ask_explain(self):
        """Menu action: explain an arbitrary UTC moment."""
        if not self.roster:
            self.warn("This team has no members yet.", "Add a member first.")
            return
        default = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        if self.plan is not None and self.plan.best is not None:
            default = self.plan.best.start.strftime("%Y-%m-%d %H:%M")
        text = simpledialog.askstring(
            "Explain a time", "UTC time (YYYY-MM-DD HH:MM):",
            initialvalue=default, parent=self.root)
        if not text:
            return
        try:
            moment = datetime.strptime(text.strip(), "%Y-%m-%d %H:%M")
        except ValueError:
            self.warn("That is not a time.", "Use the form 2026-03-02 11:00.")
            return
        self.explain_at(moment.replace(tzinfo=timezone.utc))

    def explain_at(self, moment):
        checks = planner.explain(self.roster, moment)
        window = tk.Toplevel(self.root)
        window.title("Explain %s UTC" % moment.strftime("%Y-%m-%d %H:%M"))
        window.geometry("900x420")
        window.configure(bg=CARD)
        window.transient(self.root)

        tk.Label(window, text=moment.strftime("%A %d %B %Y, %H:%M UTC"),
                 bg=CARD, fg=TEXT, font=(FONT, 13, "bold")).pack(
            anchor="w", padx=16, pady=(14, 0))
        tk.Label(window, text="Who can meet then, in their own clock?",
                 bg=CARD, fg=MUTED, font=(FONT, 9)).pack(anchor="w", padx=16)

        table = ttk.Treeview(window, columns=("name", "city", "status", "local",
                                              "offset", "window", "note"),
                             show="headings", height=8)
        for column, heading, width in (
                ("name", "Member", 120), ("city", "City", 100),
                ("status", "Free", 80), ("local", "Local time", 130),
                ("offset", "UTC offset", 90), ("window", "Window", 220),
                ("note", "Note", 150)):
            table.heading(column, text=heading)
            table.column(column, width=width, anchor="w")
        table.tag_configure("free", foreground=GOOD)
        table.tag_configure("blocked", foreground=BAD)
        for check in checks:
            local = check.local
            status = ("yes" if check.free else
                      "AWAY" if check.away else
                      "BUSY" if check.busy is not None else "no")
            note = (check.busy.title if check.busy is not None else
                    "away" if check.away else "" if check.free else "outside window")
            table.insert("", "end", tags=("free",) if check.free else ("blocked",),
                         values=(check.member.name, check.member.city, status,
                                 local.strftime("%a %H:%M"),
                                 render.offset_text(
                                     local.utcoffset().total_seconds() / 3600.0),
                                 check.window_text, note))
        table.pack(fill="both", expand=True, padx=16, pady=10)

        free = sum(1 for check in checks if check.free)
        footer = tk.Frame(window, bg=CARD)
        footer.pack(fill="x", padx=16, pady=(0, 12))
        tk.Label(footer, text="%d of %d members are free at that moment."
                 % (free, len(checks)), bg=CARD, fg=TEXT,
                 font=(FONT, 9, "bold")).pack(side="left")
        ttk.Button(footer, text="Close", command=window.destroy).pack(side="right")

    def text_window(self, title, content):
        """A read-only window for long text (import warnings, help)."""
        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry("820x440")
        window.configure(bg=CARD)
        window.transient(self.root)
        frame = tk.Frame(window, bg=CARD, padx=12, pady=12)
        frame.pack(fill="both", expand=True)
        widget = tk.Text(frame, wrap="none", font=(MONO, 9), background="white",
                         relief="flat", padx=10, pady=8)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=widget.yview)
        widget.configure(yscrollcommand=scroll.set)
        widget.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        widget.insert("1.0", content)
        widget.configure(state="disabled")
        ttk.Button(window, text="Close", command=window.destroy).pack(pady=(0, 10))
        window.grab_set()

    # ----------------------------------------------------------------- files

    def export(self, kind):
        """Recompute, then hand the plan to the shared exporter."""
        result = self.compute()
        if result is None:
            return
        try:
            path = storage.export(result, self.team_name.get().strip() or "TeamSync", kind)
        except (OSError, TeamSyncError) as exc:
            self.warn("Could not write the %s file." % kind.upper(), str(exc))
            return
        self.status.set("Saved %s" % os.path.basename(path))

    def open_output(self):
        folder = storage.ensure_output_dir()
        opener = getattr(os, "startfile", None)
        try:
            if opener is not None:
                opener(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except OSError as exc:
            self.warn("Could not open the output folder.", str(exc))
            return
        self.status.set(folder)

    # ------------------------------------------------------------------ help

    def show_help(self):
        self.text_window("Quick start", HELP_TEXT)

    def show_about(self):
        messagebox.showinfo(
            "About TeamSync",
            "TeamSync 3.0\n"
            "A cross-time-zone meeting planner for COMP9001.\n\n"
            "Pure Python standard library: tkinter, zoneinfo, json, csv, "
            "datetime.\nNo network, no installation, timezone data bundled.",
            parent=self.root)

    # ------------------------------------------------------------------ menu

    def _build_menu(self):
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Load team JSON...", command=self.pick_team_file)
        file_menu.add_command(label="Save team JSON...", command=self.save_team_file)
        file_menu.add_command(label="Import roster CSV...", command=self.import_roster)
        file_menu.add_separator()
        for kind, label in (("txt", "Export TXT"), ("ics", "Export ICS"),
                            ("html", "Export HTML"), ("csv", "Export CSV")):
            file_menu.add_command(label=label,
                                  command=lambda k=kind: self.export(k))
        file_menu.add_separator()
        file_menu.add_command(label="Open output folder", command=self.open_output)
        file_menu.add_command(label="Exit", command=self.root.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

        tools_menu = tk.Menu(menubar, tearoff=0)
        tools_menu.add_command(label="Compute best times", command=self.compute)
        tools_menu.add_command(label="Explain a UTC time...", command=self.ask_explain)
        tools_menu.add_command(label="Check possible lengths",
                               command=self.show_lengths)
        tools_menu.add_separator()
        tools_menu.add_command(label="Import busy calendar (.ics)...",
                               command=self.import_busy)
        tools_menu.add_command(label="Clear selected member's calendar",
                               command=self.clear_busy)
        tools_menu.add_separator()
        tools_menu.add_command(label="Load extended demo team",
                               command=lambda: self.load_team(EXTENDED_TEAM))
        tools_menu.add_command(label="Reset demo team", command=self.reset)
        menubar.add_cascade(label="Tools", menu=tools_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="Quick start", command=self.show_help)
        help_menu.add_command(label="About TeamSync", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.root.configure(menu=menubar)


HELP_TEXT = """TeamSync in six steps
=====================

1. Team page -> Add member...
   Name, city, attendance role (Required / Optional), the days and the local
   time windows in which the person is free.  Away dates use local dates:
   2026-03-04 or a range 2026-03-06..2026-03-08.

   In a hurry?  "Import roster CSV..." in the sidebar loads a whole team from a
   spreadsheet with name,city,role,days,windows,away columns.

2. Optional: select a member -> "Import busy .ics..."
   Their exported calendar becomes busy blocks (one-off events and daily or
   weekly repeats).  "Clear busy" removes them again.

3. Set Length and Weeks in the settings bar.
   Weeks = 1 plans a single week.  Weeks = 4 compares four consecutive weeks
   and keeps the best one -- useful when daylight saving changes in between.

4. Press Compute best times.
   Recommendations are ranked by: every required member present, then number
   of attendees, then comfort score, then the earliest time.

5. Week grid page.
   A coloured heatmap of the week; click a cell to see every member's local
   time and why somebody cannot come.

6. Export TXT / ICS / HTML / CSV from the footer.
   "Copy invite" puts a ready-to-paste message on the clipboard.
   The .ics file opens in Outlook, Apple Calendar or Google Calendar.

Shortcuts: Ctrl+Enter or F5 = compute, Ctrl+S = save team, Ctrl+I = import
calendar. The < and > buttons beside the week move it by one week.
"""


def main(team_path=None):
    """Open the window and block until it is closed.

    On a headless machine (Ed, a server, CI) Tk() raises TclError because there
    is no display.  Say so in one line and point at the terminal menu instead of
    dumping a traceback.
    """
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print("Cannot open a window on this machine: %s" % exc)
        print("Run  python main.py --cli  for the terminal version.")
        return 1
    TeamSyncApp(root, team_path)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
