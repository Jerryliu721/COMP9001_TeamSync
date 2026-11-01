# TeamSync — 3-minute presentation script

> COMP9001 Final Project · Week 13 tutorial
> Everything below is timed for **3:00** and has been checked against the
> shipped demo files, so the numbers you say out loud are the numbers on screen.

---

## Before you start (30 seconds of setup)

1. `cd final_project/TeamSync`
2. `python main.py` — the window opens on the demo team.
3. Have the output folder ready: **File → Open output folder**.
4. Keep a terminal in the same folder as a backup:

   ```bash
   python main.py --file data/demo_team_extended.json --weeks 4 --report
   ```

If the projector freezes, run that command and talk over the terminal output.

---

## Timeline

| Time | What | Screen |
| --- | --- | --- |
| 0:00–0:25 | Problem | Team page |
| 0:25–0:55 | What TeamSync does | Team + settings bar |
| 0:55–2:30 | Live demo (6 steps) | window |
| 2:30–3:00 | Technical highlights + wrap | Recommendations page |

---

## 0:00 — The problem (25 s)

> "Every group project starts the same way: someone posts *'when is everyone
> free?'* and twenty messages later you still do not have a time. The hard part
> is not converting time zones. The hard part is that **9 am for me is midnight
> for you**, and nobody wants to be the person who always gets the bad slot.
>
> TeamSync takes everyone's weekly availability and answers one question:
> **what is the least painful time for this specific group?**"

---

## 0:25 — What it does (30 s)

Point at the settings bar and the Team table.

> "Each member has a city, a weekly availability window, an attendance role —
> required or optional — optional days away, and optionally their real calendar
> imported from an `.ics` file.
>
> TeamSync scans every half-hour of the week, scores each slot by who can attend
> and how civilised the local time is, and ranks the result. One rule matters:
> **only the start time is scored** — otherwise a three-hour meeting would win
> just by touching more comfortable cells."

---

## 0:55 — Live demo (95 s)

### Step 1 — load the extended team (10 s)

**Tools → Load extended demo team**

> "Five people. Eve in Singapore is **optional** — she can join if it works, but
> she must not block the meeting. Dan is away on 4 March, Alice on 6 March."

### Step 2 — compute one week (15 s)

Weeks = **1**, press **Compute best times**.

> "Best slot: Monday 11:00 UTC, five out of five. Score 240 out of 330 — the
> maximum is 330 because Eve is optional, so her coverage bonus is 20 instead
> of 40."

### Step 3 — read the heatmap (15 s)

**Week grid** page. Point at the green `ALL` cells, then **click Wed 11:00**.

> "Green means everybody is free. Wednesday drops to four — click it and the
> tool explains why: Dan is **AWAY** that day, and here are everyone else's
> local times and UTC offsets."

### Step 4 — import a real calendar (25 s)

**Team → select Bo → Import busy .ics...** → `data/example_calendar.ics` →
**Import**.

> "This is new in version 3. Bo exported his calendar; TeamSync turned ten
> events into busy blocks. Eight of them are his Monday lecture — a weekly
> repeat, expanded in **Beijing time**, so it stays at 09:00 Beijing even when
> other countries change their clocks. The all-day public holiday is skipped
> with a warning, because all-day events should be entered as away dates."

Press **Compute best times** again, then click **Mon 02:00** on the grid.

> "Now look at Monday 02:00 UTC: Bo is **BUSY — COMP9001 lecture**. The same
> grid, one imported file, and the tool is telling the truth about his day."

### Step 5 — compare four weeks (20 s)

Weeks = **4**, press **Compute best times**.

> "Next four weeks. The best week moves to 9 March and the best time moves from
> 11:00 to 10:00 UTC. And — the bug in every spreadsheet planner — **New York
> enters daylight saving on 8 March**. TeamSync detects the switch and tells
> you the date."

### Step 6 — export (10 s)

**Export ICS**, then **Open output folder**.

> "One click gives a calendar file for Outlook or Google Calendar, plus a
> spreadsheet CSV, a coloured HTML report for the Padlet post, and plain text.
> **Copy invite** puts the same decision on the clipboard as a ready-to-paste
> message with everyone's local time."

---

## 2:30 — Technical highlights + wrap (30 s)

> "Under the hood it is pure standard library: `zoneinfo` reads bundled timezone
> files, so it works offline on any laptop — no `pip install`. The core is a
> scan line over 240 half-hour slots, cut into segments and sorted by *required
> members present, attendance, comfort, earliest time*. The .ics importer is a
> small RFC 5545 parser: unfold lines, read properties, expand daily and weekly
> repeats in the event's own timezone.
>
> 107 unit tests cover it, including daylight-saving transitions, the parser,
> the CSV importer, the command line and the GUI.
>
> TeamSync does not just convert time zones — it explains the compromise and
> exports the decision. Thank you."

---

## Ready-to-paste Padlet description (~100 words)

> TeamSync is a cross-time-zone meeting planner for student teams and remote
> clubs. Add each member's city, weekly availability, attendance role and days
> away — or import their real calendar from an .ics file, and a whole roster
> from a CSV. TeamSync scans every half-hour of the week, scores each slot by
> attendance and local comfort, and ranks the best times. Unlike a plain
> time-zone converter, it explains each suggestion in every member's local time,
> compares the next four weeks as daylight saving shifts, and exports a
> calendar file. Built with the Python standard library only: offline, no
> installation, and it runs on any tutor's laptop.

**Representative image:** use `docs/screenshots/report.png` (the coloured HTML
report — the green heatmap reads well as a thumbnail), `docs/screenshots/grid.png`
(the heatmap page) or `docs/screenshots/results.png` (the recommendation cards).
All were captured from the shipped demo, so the numbers match this script.

---

## Likely questions (and short answers)

**Why is a required member worth 40 and an optional one 20?**
Coverage dominates comfort: one missing required person makes a slot unusable as
a *decision*, while an optional person is extra value. The ranking checks
"all required present" before it ever compares scores, so the numbers only
break ties among slots that are equally viable.

**Why only score the start?**
A long meeting would otherwise collect comfort points from every cell it covers,
which rewards length rather than quality. There is a regression test for it
(`test_score_uses_start_only`).

**How does the .ics import handle recurring events and time zones?**
Daily/weekly rules (`INTERVAL`, `COUNT`, `UNTIL`, `BYDAY`) are expanded in the
event's own timezone, then each occurrence is converted to UTC — so a 09:00
London lecture stays at 09:00 London across British Summer Time. Monthly/yearly
repeats and all-day events are skipped with an explicit warning.

**Why brute force? 240 slots × N members.**
Because 240 × 5 is 1,200 checks — instant, exact and easy to explain. A clever
optimiser would add risk without adding value.

**How do you know the timezone maths is right?**
`zoneinfo` is the standard library's IANA implementation, the data files ship
with the project, and the tests pin the real transitions (New York 8 March,
London 29 March, Sydney 5 April 2026).

**What happens if nobody can attend at the same time?**
You get the longest shared window, and the length check tells you which shorter
duration does fit. Partial attendance is still shown, clearly labelled with who
is missing.

**What would you add next?**
Voting on the top three slots, and reading availability straight from a Google
Calendar link — both need a data source change, not an algorithm change.
