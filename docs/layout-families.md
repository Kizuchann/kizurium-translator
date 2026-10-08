# Layout families

Kizurium does not need to know which game or application is on screen to understand the
general structure of its text.

The universal recognition path works from **visual layout**: position, spacing, text
density, alignment, grouping, orientation, and other features visible in the current
frame.

Profiles are optional. A profile is a user-selected set of hints for a known layout;
it is not a game detector and it is never chosen automatically from the screenshot.

## Profiles and the universal path

| Mode | Selected by | Effect |
|---|---|---|
| **Default** | nobody | Uses only the general layout and OCR heuristics |
| **Profile enabled** | user, config, or environment | Adds optional ordering hints, spacing values, vocabulary, and UI-zone hints |

The same universal rules are used whether the source is a still screenshot or a live
screen.

A profile can make a known layout easier to interpret, but the screen itself remains the
source of truth.

---

## Layout families

These families describe recurring visual patterns that can appear in many unrelated
applications and games.

They are deliberately based on **layout**, not on game-specific names.

### A — Dialogue and narration

Typical structure:

- a speaker name followed by a longer body of text;
- a wide dialogue area near the bottom of the screen;
- a small gap between the speaker label and the body;
- several lines of dialogue grouped into one block;
- choices or short system messages kept separate from the dialogue.

Common variations include dialogue over artwork, a separate name plate, or another text
area elsewhere on the screen.

The important signals are the **relative position, spacing, width, and grouping** of the
text blocks — not the title of the game.

### B — HUD labels and small controls

Typical examples are short labels such as:

- `AUTO`
- `SKIP`
- status indicators;
- small corner controls;
- compact buttons.

These elements are usually short, well separated, and visually distinct from nearby
dialogue or narration.

The main rule is to avoid merging several small controls into one text block.

### C — Multiple independent zones

A single screen may contain several unrelated text areas at once:

- a narration panel;
- a title;
- a quest or status line;
- a temporary notification;
- a button or control.

Each zone should remain independent.

A long sentence inside a panel is still a paragraph even when the panel itself has the
shape of a large UI element. Its font size should not be inferred only from the total
panel height.

### D — Columns and tables

Some screens arrange text into two or more columns:

- statistics;
- inventory or menu panels;
- mirrored information blocks;
- table-like layouts.

Useful signals include:

- consistent column alignment;
- stable gutters between columns;
- repeated horizontal structure.

Text from one column must not be merged with text from a neighbouring column simply
because the lines are vertically close.

### E — Rotated or sloped text

Some interfaces use text that is not aligned to the normal horizontal axis.

The important signals are:

- the orientation of each text block;
- the presence of both rotated and normal text in the same frame;
- consistent orientation inside one block.

Rotated text should be treated as its own block rather than forcing the whole screen into
one orientation.

### F — Mixed scripts

A single screen can contain several writing systems at once, for example:

- Japanese with English UI labels;
- Latin text beside Japanese text;
- furigana above Japanese text;
- symbols mixed with normal text.

Language detection belongs to the individual text block.

Recognizing Japanese in one block must not cause nearby English text to be discarded or
merged incorrectly.

### G — Gameplay overlays and transient UI

Gameplay screens often combine text with very different lifetimes:

- a persistent HUD element;
- a quest or objective;
- a subtitle or spoken line;
- a temporary loot or status notification;
- a contextual interaction prompt.

The important distinction is not only where the text is, but also **which elements belong
to the same visual zone**.

A temporary notification should be allowed to disappear without causing stable HUD text
to be treated as the same block.

### H — Modals and system messages

Modal dialogs and system-style panels usually have a different structure from normal
gameplay text:

- a centered card or panel;
- a heading or explanatory paragraph;
- one or more clearly separated buttons;
- mostly static content while the modal is open.

Buttons, labels, and the main message should remain separate elements even when they are
inside the same card.

---

## Why these families exist

The point of a layout family is to capture a **reusable visual pattern**.

For example, the rule

> "a short label separated from a longer text block"

can apply to many dialogue layouts.

The rule

> "two aligned columns separated by a stable gutter"

can apply to inventory screens, statistics panels, settings pages, and many other
interfaces.

A heuristic should therefore describe a visual relationship that can be reused elsewhere,
not a special case for one particular title.

---

## What profiles are allowed to change

A profile may provide optional information such as:

- OCR ordering preferences;
- expected spacing or gap values;
- vocabulary or glossary data;
- soft hints about the role of a UI zone.

These values are **hints**, not replacements for the universal recognition logic.

A profile must not be required for the application to understand a layout that can be
recognized from the screen itself.

---

## What profiles do not do

Profiles do **not**:

- identify a game automatically;
- inspect pixels and decide which game is running;
- replace general layout heuristics with a per-game implementation;
- turn a title-specific observation into a hard-coded `if game == ...` branch.

Choosing a profile because it matches the current screen is an explicit user action.

---

## Adding a new heuristic

When a new visual pattern is discovered:

1. Describe the **layout relationship**, not the game where it was found.
2. Give the pattern a family or add a new family when it represents a genuinely different
   structure.
3. Test the heuristic on the original screen and on unrelated layouts.
4. Keep profile data only when it provides information that the universal path cannot
   reasonably infer on its own.
5. Remove configuration fields that merely duplicate existing defaults.

A good heuristic should answer:

> **"What visual property makes these text blocks belong together?"**

rather than:

> **"Which game uses this exact layout?"**

---

## Guiding principle

Kizurium should learn **patterns of text layout**, not memorize individual games.

The goal is one reusable recognition system that can handle unfamiliar screens using the
same visual rules it already knows.
