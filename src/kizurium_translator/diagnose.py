"""What is installed, and whether the missing part is the problem.

`--doctor` has to answer one question - "why is this not working" - and list what
it has to cover. Both care about the difference the old output could not express:

    a required piece missing   -> the reason nothing works
    an optional piece missing  -> not the reason

quickshell was in `REQUIRED_TOOLS` and is therefore a *core* dependency, so
`--doctor` exits 1 on a machine where everything the overlay needs is present and
the only absent thing is the region selector. That is a mistake worth naming: an
optional model reported as a core failure. Here the selector is its own section
and nothing it needs affects the exit code for live translation.

The statuses are words rather than glyphs, because the output is read by people
and by grep: `OK`, `MISSING`, `DISABLED`, `ERROR`, `UNKNOWN`. `MISSING` means not
installed and nothing is wrong; `ERROR` means installed and broken.

Nothing here logs a raw path, a credential or screen text. The paths a user needs
in order to act are reported as "which file", not as its contents: the doctor
output carries no raw paths, and the reason is that this output ends up in
issue trackers.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Status(str, Enum):
    """The four states a single check can report, plus unknown for "not checkable"."""

    OK = "OK"
    MISSING = "MISSING"
    DISABLED = "DISABLED"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"

    def glyph(self) -> str:
        # Kept so the terminal output stays readable at a glance, but the word is
        # what a person or a grep reads.
        return {
            Status.OK: "✓",
            Status.MISSING: "·",
            Status.DISABLED: "-",
            Status.ERROR: "✗",
            Status.UNKNOWN: "?",
        }[self]


@dataclass(frozen=True)
class Line:
    """One reported item."""

    name: str
    status: Status
    detail: str = ""
    required: bool = False
    """Whether the absence of this is the reason the product does not work."""

    def text(self, width: int = 24) -> str:
        mark = f"{self.status.glyph()} {self.status.value:8}"
        return f"  {mark} {self.name:<{width}} {self.detail}".rstrip()


@dataclass
class Section:
    name: str
    lines: list[Line] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.lines is None:
            self.lines = []

    def add(self, name: str, status: Status, detail: str = "", *, required: bool = False) -> None:
        self.lines.append(Line(name=name, status=status, detail=detail, required=required))

    def add_line(self, line: Line) -> None:
        self.lines.append(line)

    @property
    def broken(self) -> bool:
        """Whether anything required here is missing or broken."""
        return any(line.required and line.status in (Status.MISSING, Status.ERROR) for line in self.lines)

    def render(self) -> list[str]:
        out = [self.name]
        out.extend(line.text() for line in self.lines)
        return out

    def counts(self) -> dict[Status, int]:
        out: dict[Status, int] = {}
        for line in self.lines:
            out[line.status] = out.get(line.status, 0) + 1
        return out


SECTION_NAMES = (
    "Core",
    "Wayland",
    "Selector",
    "OCR",
    "Translation",
    "Models",
    "Config",
)


def render_doctor(
    sections: list[Section],
    *,
    notes: list[str] | None = None,
) -> tuple[str, int]:
    """The whole report, and the exit code.

    The exit code is decided by the sections, not by the process: a required item
    that is MISSING or ERROR fails it. An UNKNOWN is not a failure - it means this
    machine cannot answer the question, and saying so is the honest answer.
    """
    out: list[str] = ["kizurium-translator doctor", ""]
    for section in sections:
        if not section.lines:
            continue
        out.extend(section.render())
        out.append("")
    for note in notes or []:
        out.append(note)
    ok = not any(section.broken for section in sections)
    return "\n".join(out).rstrip() + "\n", 0 if ok else 1
