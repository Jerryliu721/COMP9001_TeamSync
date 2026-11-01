# TeamSync — Padlet post (ready to publish)

> Rubric (E) asks for four things: a **title**, a **representative image**, a
> **short description (~100 words) including the target audience**, and the
> **in-class presentation**. Everything below is ready to copy into Padlet.
> The presentation itself is scripted in [`PRESENTATION.md`](PRESENTATION.md).

---

## Title

**TeamSync — find the least painful meeting time**

Alternatives if you want something punchier:

* *TeamSync: the 9 am / midnight problem, solved*
* *Stop asking "when is everyone free?"*

---

## Representative image

Use **`screenshots/report.png`** (the coloured HTML report). The green heatmap
reads well as a thumbnail and shows the product, not just code.

| Image | When to use it |
| --- | --- |
| `screenshots/report.png` | **First choice** — colourful, self-explanatory |
| `screenshots/results.png` | Shows the real window and recommendation cards |
| `screenshots/grid.png` | Shows the heatmap and the local-time table |

All three were captured from the shipped demo, so the numbers match the
presentation script.

---

## Description (~100 words)

> TeamSync is a cross-time-zone meeting planner for student teams and remote
> clubs. Add each member's city, weekly availability, attendance role and days
> away — or import their real calendar from an `.ics` file, and a whole roster
> from a CSV. TeamSync scans every half-hour of the week, scores each slot by
> attendance and local comfort, and ranks the best times. Unlike a plain
> time-zone converter, it explains each suggestion in every member's local time,
> compares the next four weeks as daylight saving shifts, and exports a
> calendar file. Built with the Python standard library only: offline, no
> installation, and it runs on any tutor's laptop.

**Target audience:** university students and small remote teams (3–20 people)
who need to agree on a recurring or one-off meeting across several time zones —
group projects, study groups, clubs, hackathon teams.

---

## Optional extra lines for the post body

* **Try it:** `python main.py` — no `pip install`, no network.
* **Features:** required/optional attendees · days away · `.ics` calendar
  import · CSV roster import · four-week comparison with daylight-saving
  detection · coloured week heatmap · "explain this time" per member ·
  TXT/ICS/HTML/CSV export · "Copy invite" message.
* **Built with:** Python 3, tkinter, `zoneinfo`, `json`, `csv`, `datetime` —
  standard library only, timezone data bundled.
* **Tests:** 107 automated tests, including the daylight-saving and `.ics`
  parser cases.

---

## Post checklist / 发布前检查

- [ ] Title in the post title field.
- [ ] Image uploaded (not just linked), preferably `report.png`.
- [ ] Description pasted (check the word count is ~100).
- [ ] Target audience mentioned.
- [ ] Posted to **your tutor's** tutorial board, not a random one.
- [ ] Code submitted in the Ed Code Submission slide.
- [ ] 3-minute presentation rehearsed with `PRESENTATION.md`.

中文：标题、图片、约 100 词介绍、目标受众、发到**自己 tutor** 的板子、代码在
Ed 提交、按讲稿排练 3 分钟。
