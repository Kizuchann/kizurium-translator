# Layout families

Profiles do **not** detect the game from pixels. They are opt-in priors
(`--profile`, env, config). This file is the other side of that decision: what
the **universal** path must understand from vision alone, distilled from a
range of screens that have nothing in common but pixels.

Goal: common visual features, not nine special hacks.

## How profiles relate

| Mode | Who chooses | What changes |
| --- | --- | --- |
| Default (no profile) | nobody | only general heuristics |
| Profile on | user / config / env | OCR order, gap numbers, lexicon pack, soft UI-zone role hints |

A still image in a viewer works the same as a live game: OCR sees pixels.
Selecting a profile for whatever is on screen is a **test convenience**, not
auto-detection.

## Families (game-agnostic)

### A — Novel dialogue strip

Examples: visual-novel dialogue with a speaker name and a body, and dialogue
drawn over character art; DDLC, Steins;Gate, Danganronpa.

Signals:

- dark band near the bottom (often y ≳ 0.70 of frame)
- short left token (speaker) + longer right/body text, wide gap
- name plate just above a multi-line or text-heavy body, small vertical gap
- choices and short system labels stay their own blocks, not part of the body
- a second text system far from the strip (phone, toast) stays independent
- optional mid-screen choice stack

Already covered by: name/dialogue gap, speaker scoring, roles, bottom-band VN
heuristics in prepare.

### B — Top / corner HUD controls

Examples: AUTO/OFF/SKIP corner labels over a dialogue strip; many mobile UIs.

Signals: short labels, top fringe, high contrast, separate boxes that must
not merge.

### Many zones on one screen

A narration block, a title beside it, HUD labels and a status line are
different zones. A long sentence in a panel is a paragraph, not a button:
sizing it as one label blows the type up to the box height.

### C — Dense multi-column panels

Examples: stat and menu tables, and a two-mirrored-column screen.

Signals: gutters, column independence, no cross-merge.

### D — Sloped / rotated text

Examples: a rhythm-game results screen with angled labels.

Signals: per-block angle, mixed with axis-aligned neighbours.

### E — Mixed script in one frame

Examples: Japanese and English in the same frame, including furigana over kana.

Signals: per-block language; JP must not suppress EN beside it.

### Gameplay HUD beside changing lines

A quest line, a loot toast, a spoken subtitle and a status chip are four
zones. The toast and a "pick up" prompt leave when the line does. The status
chip stays. Nothing here is a title branch.

### F — Gameplay HUD over 3D / art

Examples: open-world HUDs with quest text and transient toasts over rendered
art; Elden Ring, GTA, Honkai.

Signals: sparse overlay text, transient toasts, non-Latin runes, must not
clear stable HUD when one line changes.

### G — Modals / system copy

Examples: a download-confirmation modal, an age-rating notice, a web page.

Signals: centred card, two button styles, mostly static.

## What this does *not* do

- No `if game == "…"`.
- No shipping copyrighted screenshots in the repo.
- No pretending profiles auto-pick from the screen.

Research for titles we have no samples of (DDLC, Danganronpa, …) feeds
**family A–G** above when it teaches a new signal; it does not add a per-title
branch.

## Checklist when adding a heuristic

1. Name the **family** (A–G), not the game.
2. Prove it on the screen where you found it **and** two others from different
   families.
3. If a profile TOML only stores numbers that general code already defaults to,
   delete the redundant field — do not leave dead DATA.
