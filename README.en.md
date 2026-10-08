<div align="center">

<a href="README.en.md"><img src="https://img.shields.io/badge/lang-English-b4befe?style=for-the-badge&logo=readme&logoColor=white" alt="English"></a>
<a href="README.md"><img src="https://img.shields.io/badge/ЯЗЫК-русский-cba6f7?style=for-the-badge&logo=readme&logoColor=white" alt="Русский"></a>

<br><br>

# Kizurium Translator

**Wayland-native OCR, screen translation and real-time translation overlay for Linux**

<p>
  Designed for <strong>Wayland</strong>, with support for <strong>Hyprland</strong>,
  <strong>Arch Linux</strong> / Arch-based systems and <strong>Nix/NixOS</strong>.
</p>

<br>

<a href="#-screenshots-"><kbd>&nbsp;&nbsp;<br>SCREENSHOTS<br>&nbsp;&nbsp;</kbd></a>&ensp;
<a href="#-quick-start-"><kbd>&nbsp;&nbsp;<br>QUICK START<br>&nbsp;&nbsp;</kbd></a>&ensp;
<a href="#-features-"><kbd>&nbsp;&nbsp;<br>FEATURES<br>&nbsp;&nbsp;</kbd></a>&ensp;
<a href="docs/trust.md"><kbd>&nbsp;&nbsp;<br>PRIVACY<br>&nbsp;&nbsp;</kbd></a>&ensp;
<a href="#-documentation-"><kbd>&nbsp;&nbsp;<br>DOCS<br>&nbsp;&nbsp;</kbd></a>&ensp;
<a href="https://github.com/Kizuchann/kizurium-translator/issues"><kbd>&nbsp;&nbsp;<br>ISSUES<br>&nbsp;&nbsp;</kbd></a>

<br><br>

<img src="https://img.shields.io/github/stars/Kizuchann/kizurium-translator?style=for-the-badge&color=cba6f7&label=stars" alt="stars">
<img src="https://img.shields.io/github/forks/Kizuchann/kizurium-translator?style=for-the-badge&color=b4befe&label=forks" alt="forks">
<img src="https://img.shields.io/badge/license-AGPL--3.0-cba6f7?style=for-the-badge" alt="license">
<img src="https://img.shields.io/badge/platform-Wayland%20%7C%20Linux-b4befe?style=for-the-badge" alt="platform">

</div>

<br>

## • overview •

**Kizurium Translator** is a **Wayland-native OCR and screen translation tool for Linux**.

Select an area of the screen and Kizurium recognizes the text, translates it, and displays the result directly over the original content as a separate **Wayland overlay**. The application underneath does not need to be replaced or controlled.

For continuous translation, **Live Translation** watches a selected screen region, recognizes changing text, and updates the overlay as new content appears.

Kizurium is built for **Wayland desktops**, including **Hyprland**, with support for **Arch Linux / Arch-based distributions** and **Nix/NixOS**.

## • features •

### OCR

Recognize text from a selected region of the screen and return the original text.

### Translation

Recognize on-screen text, translate it between configurable source and target languages, and display the result over the original content.

### Live Translation

Continuously recognize changing text in a selected region and refresh the translation without requiring a new manual capture for every change.

### Games and visual novels

Designed for dialogue, subtitles, menus, UI elements, and other text that appears directly on screen.

Game-specific profiles and dictionaries are optional. The default mode stays general-purpose and does not require a game profile.

### Local and offline workflows

Kizurium includes local OCR components and provides an `--offline-only` mode for workflows that must avoid network translation backends.

---

## • screenshots •

<div align="center">

### Menu

<img src="assets/screenshots/menu-ru.png" width="48%" alt="Kizurium — translated menu">
&nbsp;
<img src="assets/screenshots/menu-en.png" width="48%" alt="Kizurium — original menu">

</div>

<br>

<div align="center">

### Dialogue

<img src="assets/screenshots/dialogue-ru.png" width="48%" alt="Kizurium — translated dialogue">
&nbsp;
<img src="assets/screenshots/dialogue-en.png" width="48%" alt="Kizurium — original dialogue">

</div>

<br>

<div align="center">

**The translated text is rendered directly over the original screen content.**

</div>

---

## • quick start •

### Arch Linux

```bash
git clone https://github.com/Kizuchann/kizurium-translator.git
cd kizurium-translator
makepkg -si
```

### Nix / NixOS

```bash
nix profile add github:Kizuchann/kizurium-translator
```

### Manual installation

```bash
git clone https://github.com/Kizuchann/kizurium-translator.git
cd kizurium-translator
./install.sh
```

Full installation guide:

[`docs/install.md`](docs/install.md)

### First run

Pick an area of the screen and start translation:

```bash
kizurium-translator --toggle
```

Start Live Translation for a region:

```bash
kizurium-translator --live -g 0,0 1920x1080
```

---

## • commands •

| Command | What it does |
|---|---|
| `--toggle` | pick a region with the mouse and start translation |
| `--live -g 0,0 1920x1080` | live translation of the specified region |
| `--ocr-copy` | recognize a region and copy the text to the clipboard |
| `--text` | open the text translator |
| `--stop` | stop the current session |
| `--status` | show session status |
| `--doctor` | check the environment and dependencies |
| `--profile ID` | enable a game profile |
| `--offline-only` | disable network backends |
| `--pack-install ID` | install an offline pack |

---

## • installation and dependencies •

The Arch package includes the components required by Kizurium for OCR and normal operation. Manual installation uses the required Wayland/GTK system packages together with the project's Python dependencies.

<details>
<summary>Main dependencies</summary>

```text
Pillow
numpy
pytesseract
requests

python-gobject
python-cairo
gtk4
gtk4-layer-shell

tesseract
tesseract-data-eng
tesseract-data-jpn
tesseract-data-rus

grim
slurp
quickshell
wl-clipboard
aria2
libnotify
```

</details>

---

## • privacy •

Kizurium does not use telemetry and does not contain built-in API keys.

**Screen captures are not sent over the network by the application itself.**

When a network translation backend is selected, only the text being translated is sent to that backend.

More details:

[`docs/trust.md`](docs/trust.md)

For a local-only workflow:

```bash
kizurium-translator --offline-only
```

---

## • dictionaries and profiles •

Your own dictionary takes priority over profile, game-pack, and shared dictionaries.

Import one with:

```bash
kizurium-translator --import-dictionary ~/my-dict.tsv
```

Priority order:

```text
your dictionary
      ↓
profile
      ↓
game pack
      ↓
shared dictionary
```

Game vocabulary is provided through **opt-in** packs and enabled with `--profile`.

Without a profile, Kizurium stays in general-purpose mode.

---

## • documentation •

| Document | Contents |
|---|---|
| [`docs/install.md`](docs/install.md) | installation, setup, and hotkeys |
| [`docs/trust.md`](docs/trust.md) | privacy and network requests |
| [`docs/layout-families.md`](docs/layout-families.md) | distinguishing dialogue lines from buttons and other UI text |
| [`docs/external-dictionaries.md`](docs/external-dictionaries.md) | external dictionary formats |
| [`docs/language-packs.md`](docs/language-packs.md) | offline packs |
| [`docs/licenses.md`](docs/licenses.md) | component licenses |

---

## • development •

Run the test suite:

```bash
uv run pytest -q
```

Run the linter:

```bash
uv run ruff check src/
```

---

## • known limitations •

- Speaker gender is inferred from the `Name: line` form. Without a visible speaker name, it cannot be determined automatically.
- Text inside complex UI elements can be recognized incorrectly. A profile can mark such regions as `not text`.
- Slant and weight are estimated from the ink structure; for ink heights below 34 px, slant measurement is skipped.

---

<div align="center">

**Kizurium Translator** · **Wayland** · **Hyprland** · **Linux** · **Arch Linux** · **Nix/NixOS** · **OCR** · **Screen Translation** · **Live Translation**

<br><br>

**AGPL-3.0-or-later** · [LICENSE](LICENSE) · [NOTICE](NOTICE)

<br>

<sub>Screenshots are from games owned by their respective rights holders and are shown solely to demonstrate the translator.</sub>

</div>
