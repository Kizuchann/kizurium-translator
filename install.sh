#!/usr/bin/env bash
# Kizurium Translator installer — Arch (pacman) and NixOS / nix.
# Interactive by default; see --help for flags.
set -euo pipefail

PREFIX="${PREFIX:-$HOME/.local}"
INSTALLER_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
REPO_DIR="$(cd -- "$(dirname -- "$INSTALLER_PATH")" && pwd -P)"
STATE_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/kizurium-translator"
XDG_STATE="${XDG_STATE_HOME:-$HOME/.local/state}/kizurium-translator"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/kizurium-translator"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/kizurium-translator"
VENV_DIR="$STATE_DIR/venv"
EXTRAS=""
INSTALL_MODE=package
PREFIX_SET=0

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
err()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

need_value() {
    if (($# < 2)) || [[ -z "$2" ]]; then
        err "$1 требует непустое значение"
        exit 2
    fi
}

validate_extras() {
    local item
    for item in ${1:-}; do
        case "$item" in
            rapid|meiki|deep-translate|local-mt|all) ;;
            *) err "неизвестный extra: $item"; return 1 ;;
        esac
    done
}

validate_bergamot_pairs() {
    local pair
    for pair in ${1:-}; do
        [[ "$pair" =~ ^[[:alnum:]]+-[[:alnum:]]+$ ]] || {
            err "неверная пара Bergamot: $pair (пример: en-ru)"
            return 1
        }
    done
}

unquote_saved_value() {
    local value="$1"
    if [[ "$value" == \"*\" && ${#value} -ge 2 ]]; then
        value="${value:1:${#value}-2}"
    elif [[ "$value" == \'*\' && ${#value} -ge 2 ]]; then
        value="${value:1:${#value}-2}"
    fi
    printf '%s' "$value"
}

# Non-interactive? (CI / piped stdin) — use safe defaults, don't hang.
is_tty() { [[ -t 0 && -t 1 ]]; }

ask_yn() {
    # $1=prompt  $2=default(Y|N)
    local prompt="$1" def="${2:-Y}" reply
    if ! is_tty; then
        [[ "$def" == Y || "$def" == y ]] && return 0 || return 1
    fi
    local hint="[Y/n]"; [[ "$def" == N || "$def" == n ]] && hint="[y/N]"
    read -r -p "  $prompt $hint " reply || reply=""
    if [[ -z "$reply" ]]; then
        [[ "$def" == Y || "$def" == y ]]
        return
    fi
    [[ "$reply" =~ ^[Yy] ]]
}

# ------------------------------------------------------------- interactive TUI
# Number keys always work. Arrows + Space when the terminal supports them.
# No gum-in-$(…): that breaks /dev/tty and looked like instant "отменено".

drain_stdin() {
    while IFS= read -r -t 0 -n 1 _; do :; done || true
}

# Single-choice menu. Args: "id|label"…. Sets MENU_PICK to chosen id.
# Returns 1 on quit/cancel.
menu_pick() {
    local title="$1"; shift
    local -a ids=() labels=()
    local item id label n i cursor=0 key rest lines_used=0
    MENU_PICK=""
    for item in "$@"; do
        id="${item%%|*}"
        label="${item#*|}"
        ids+=("$id")
        labels+=("$label")
    done
    n=${#ids[@]}
    ((n > 0)) || return 1

    if ! is_tty; then
        MENU_PICK="${ids[0]}"
        return 0
    fi

    drain_stdin
    tput civis 2>/dev/null || true
    trap 'tput cnorm 2>/dev/null || true' RETURN

    _menu_draw() {
        if ((lines_used > 0)); then
            printf '\033[%dA' "$lines_used"
        fi
        printf '\033[J'
        printf '\n\033[1m%s\033[0m\n' "$title"
        echo "  ↑↓ или цифра · Enter — выбрать · q — выход"
        echo
        for i in "${!labels[@]}"; do
            local num=$((i + 1))
            if ((i == cursor)); then
                printf '  \033[1;36m› %d) %s\033[0m\n' "$num" "${labels[$i]}"
            else
                printf '    %d) %s\n' "$num" "${labels[$i]}"
            fi
        done
        lines_used=$((n + 4))
    }
    _menu_draw

    while true; do
        IFS= read -rsn1 key || return 1
        if [[ "$key" == $'\x1b' ]]; then
            rest=""
            IFS= read -rsn2 -t 0.05 rest || true
            key+="$rest"
        fi
        case "$key" in
            $'\x1b[A'|k|K) cursor=$(( (cursor - 1 + n) % n )); _menu_draw ;;
            $'\x1b[B'|j|J) cursor=$(( (cursor + 1) % n )); _menu_draw ;;
            [1-9])
                i=$((10#$key - 1))
                if ((i >= 0 && i < n)); then
                    MENU_PICK="${ids[$i]}"
                    break
                fi
                ;;
            ""|$'\n'|$'\r')
                MENU_PICK="${ids[$cursor]}"
                break
                ;;
            q|Q) return 1 ;;
        esac
    done
    tput cnorm 2>/dev/null || true
    printf '\n'
    return 0
}

# Multi-select toggles + explicit Install / Back rows.
# Args: "id|0or1|label"…. Sets CHECKLIST_SELECTED. Returns 1 on Back.
checklist() {
    local -a ids=() labels=() on=()
    local item id def label rest n i cursor key lines_used=0
    local idx_install idx_back total
    CHECKLIST_SELECTED=""
    for item in "$@"; do
        id="${item%%|*}"
        rest="${item#*|}"
        def="${rest%%|*}"
        label="${rest#*|}"
        ids+=("$id")
        labels+=("$label")
        on+=("$def")
    done
    n=${#ids[@]}
    ((n > 0)) || return 1
    idx_install=$n
    idx_back=$((n + 1))
    total=$((n + 2))
    # Start on «Установить» so Enter means install, not a random toggle.
    cursor=$idx_install

    if ! is_tty; then
        for i in "${!ids[@]}"; do
            [[ "${on[$i]}" == 1 ]] && CHECKLIST_SELECTED+="${ids[$i]} "
        done
        return 0
    fi

    drain_stdin
    tput civis 2>/dev/null || true
    trap 'tput cnorm 2>/dev/null || true' RETURN

    _check_draw() {
        if ((lines_used > 0)); then
            printf '\033[%dA' "$lines_used"
        fi
        printf '\033[J'
        printf '\n\033[1m%s\033[0m\n' "Что включить в установку"
        echo "  ↑↓ — строка · Space/цифра — галочка · Enter — кнопка под курсором"
        echo "  RapidOCR всегда. Перевод онлайн по умолчанию (docs/trust.md)."
        echo
        for i in "${!labels[@]}"; do
            local mark=" "
            local num=$((i + 1))
            [[ "${on[$i]}" == 1 ]] && mark="✓"
            if ((i == cursor)); then
                printf '  \033[1;36m› [%s] %d) %s\033[0m\n' "$mark" "$num" "${labels[$i]}"
            else
                printf '    [%s] %d) %s\n' "$mark" "$num" "${labels[$i]}"
            fi
        done
        echo "    ────────────────────────"
        if ((cursor == idx_install)); then
            printf '  \033[1;32m› >>> Установить <<<\033[0m\n'
        else
            printf '      Установить\n'
        fi
        if ((cursor == idx_back)); then
            printf '  \033[1;33m› Назад в меню\033[0m\n'
        else
            printf '      Назад в меню\n'
        fi
        # title + help×2 + blank + n items + rule + 2 actions
        lines_used=$((n + 8))
    }
    _check_draw

    while true; do
        IFS= read -rsn1 key || return 1
        if [[ "$key" == $'\x1b' ]]; then
            rest=""
            IFS= read -rsn2 -t 0.05 rest || true
            key+="$rest"
        fi
        case "$key" in
            $'\x1b[A'|k|K)
                cursor=$(( (cursor - 1 + total) % total )); _check_draw ;;
            $'\x1b[B'|j|J)
                cursor=$(( (cursor + 1) % total )); _check_draw ;;
            " ")
                if ((cursor < n)); then
                    on[$cursor]=$(( 1 - ${on[$cursor]} )); _check_draw
                elif ((cursor == idx_install)); then
                    break
                else
                    return 1
                fi
                ;;
            [1-9])
                i=$((10#$key - 1))
                if ((i >= 0 && i < n)); then
                    on[$i]=$(( 1 - ${on[$i]} ))
                    cursor=$i
                    _check_draw
                fi
                ;;
            ""|$'\n'|$'\r')
                if ((cursor < n)); then
                    # On a checkbox: toggle only — never start install.
                    on[$cursor]=$(( 1 - ${on[$cursor]} )); _check_draw
                elif ((cursor == idx_install)); then
                    break
                else
                    return 1
                fi
                ;;
            q|Q|b|B)
                return 1 ;;
        esac
    done
    tput cnorm 2>/dev/null || true
    printf '\n'
    for i in "${!ids[@]}"; do
        [[ "${on[$i]}" == 1 ]] && CHECKLIST_SELECTED+="${ids[$i]} "
    done
    return 0
}

checklist_has() {
    [[ " $CHECKLIST_SELECTED " == *" $1 "* ]]
}

run_doctor_now() {
    if [[ -x "$BIN_DIR/kizurium-translator" ]]; then
        "$BIN_DIR/kizurium-translator" --doctor || true
    elif have kizurium-translator; then
        kizurium-translator --doctor || true
    else
        warn "команда ещё не установлена — сначала Install"
    fi
}

usage() {
    cat <<EOF
Usage: ./install.sh          # interactive — recommended
       ./install.sh --yes    # accept defaults, no questions
       ./install.sh --uninstall

Options (optional; most people never need them):
  --user              legacy per-user venv install (no pacman registration);
                      use this for Meiki, PaddleOCR, offline packs or Python extras
  --yes / -y          non-interactive: system pkgs + RapidOCR
  --extras "a b"      rapid | meiki | deep-translate | local-mt | all
  --offline-packs     download en→ru / ja→ru CT2 models (~160 MB);
                      on Arch this requires --user (adds local-mt)
  --no-offline-packs  skip offline models (default in express / --yes)
  --bergamot "en-ru"  also install Bergamot local translation: model(s) from Mozilla's
                      verified repository (+ native binding when a wheel exists)
  --no-bergamot       skip Bergamot
  --paddle            also install PaddleOCR (heavy; models are set up in Settings)
  --update            rebuild/reinstall the Arch package; with --user, restore
                      saved choices and repair the Python environment/models
  --meiki             include Meiki OCR (requires --user)
  --no-meiki          skip Japanese Meiki OCR
  --prefix DIR        user command install prefix (requires --user; default: $PREFIX)
  --system            install missing pacman packages without asking
  --uninstall         remove app + venv (asks about config/cache/models)
  --purge             with --uninstall: wipe config + cache + models + logs
  --clean-cache       keep app: wipe translate cache + logs (not models/config)
  -h, --help          this text

Supported installers today: Arch-family (pacman) and Nix (flake).
EOF
}

WITH_SYSTEM=ask
UNINSTALL=0
PURGE=0
CLEAN_CACHE=0
OFFLINE_PACKS=ask
MEIKI=ask
BERGAMOT_PAIRS=""
BERGAMOT=ask
PADDLE=ask
UPDATE=0
ASSUME_YES=0
INTERACTIVE_MENU=1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --extras)            need_value "$@"; EXTRAS="$2"; INTERACTIVE_MENU=0; shift 2 ;;
        --user)              INSTALL_MODE=user; shift ;;
        --offline-packs)     OFFLINE_PACKS=yes; INTERACTIVE_MENU=0; shift ;;
        --no-offline-packs)  OFFLINE_PACKS=no; INTERACTIVE_MENU=0; shift ;;
        --bergamot)          need_value "$@"; BERGAMOT=yes; BERGAMOT_PAIRS="$2"; INTERACTIVE_MENU=0; shift 2 ;;
        --no-bergamot)       BERGAMOT=no; BERGAMOT_PAIRS=""; INTERACTIVE_MENU=0; shift ;;
        --paddle)            PADDLE=yes; INTERACTIVE_MENU=0; shift ;;
        --update)            UPDATE=1; INTERACTIVE_MENU=0; shift ;;
        --meiki)             MEIKI=yes; INTERACTIVE_MENU=0; shift ;;
        --no-meiki)          MEIKI=no; INTERACTIVE_MENU=0; shift ;;
        --prefix)            need_value "$@"; PREFIX="$2"; PREFIX_SET=1; shift 2 ;;
        --system)            WITH_SYSTEM=yes; INTERACTIVE_MENU=0; shift ;;
        --yes|-y)
            # Defaults only for unset knobs — later/earlier --no-offline-packs
            # etc. must still win.
            ASSUME_YES=1
            INTERACTIVE_MENU=0
            [[ "$WITH_SYSTEM" == ask ]] && WITH_SYSTEM=yes
            # Games path: Meiki on; online translate works without HF packs.
            [[ "$MEIKI" == ask ]] && MEIKI=no
            [[ "$OFFLINE_PACKS" == ask ]] && OFFLINE_PACKS=no
            [[ "$BERGAMOT" == ask ]] && BERGAMOT=no
            [[ "$PADDLE" == ask ]] && PADDLE=no
            shift
            ;;
        --uninstall)         UNINSTALL=1; shift ;;
        --purge)             PURGE=1; shift ;;
        --clean-cache)       CLEAN_CACHE=1; shift ;;
        -h|--help)           usage; exit 0 ;;
        *) err "неизвестный аргумент: $1"; usage; exit 2 ;;
    esac
done

if ! validate_extras "$EXTRAS"; then
    usage
    exit 2
fi
if [[ "$BERGAMOT" == yes ]] && ! validate_bergamot_pairs "$BERGAMOT_PAIRS"; then
    usage
    exit 2
fi
if ((PURGE && !UNINSTALL)); then
    err "--purge используется только вместе с --uninstall"
    exit 2
fi
if ((UNINSTALL && CLEAN_CACHE)); then
    err "--uninstall и --clean-cache нельзя запускать одновременно"
    exit 2
fi

# A relative prefix made the generated launcher depend on the caller's cwd.
# Resolve it once so wrappers, desktop entries and removal all use one path.
if [[ "$PREFIX" == '~' ]]; then
    PREFIX="$HOME"
elif [[ "$PREFIX" == '~/'* ]]; then
    PREFIX="$HOME/${PREFIX#~/}"
fi
PREFIX="$(realpath -m -- "$PREFIX")"

BIN_DIR="$PREFIX/bin"

SYS_PKGS=(
    grim
    slurp                # --output / monitor pick; also grim helpers
    wl-clipboard
    gst-plugins-base     # screen capture through the ScreenCast portal (KDE)
    gst-plugin-pipewire
    xdg-desktop-portal
    tesseract
    tesseract-data-eng
    tesseract-data-rus
    tesseract-data-jpn
    gtk4
    gtk4-layer-shell
    libnotify            # optional desktop notifications
    python-gobject
    python-cairo
    python-pillow
    python-numpy
    python-pytesseract
    aria2                # multi-conn HF pack downloads (≈50–100× vs urllib)
)

# KDE answers ScreenCast through its own portal backend.
[[ "${XDG_CURRENT_DESKTOP:-}" == *KDE* ]] && SYS_PKGS+=(xdg-desktop-portal-kde)

if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
fi

# --update: the choices of the last install, no questions.
if ((UPDATE)) && [[ "$INSTALL_MODE" == user ]]; then
    if [[ ! -r "$STATE_DIR/install-options" ]]; then
        err "нет сохранённых опций ($STATE_DIR/install-options): сначала обычная установка ./install.sh"
        exit 1
    fi
    # This is our data file, not shell code. Never source a writable file from
    # the user's data directory; accept only known keys and constrained values.
    saved_system_choice=0
    while IFS='=' read -r key value || [[ -n "${key:-}${value:-}" ]]; do
        [[ -z "${key:-}" ]] && continue
        value="$(unquote_saved_value "${value:-}")"
        case "$key" in
            EXTRAS)
                validate_extras "$value" || exit 1
                EXTRAS="$value"
                ;;
            OFFLINE_PACKS|MEIKI|PADDLE|SYSTEM_PACKAGES)
                [[ "$value" == yes || "$value" == no ]] || {
                    err "повреждены сохранённые опции установки: $key"
                    exit 1
                }
                case "$key" in
                    OFFLINE_PACKS) OFFLINE_PACKS="$value" ;;
                    MEIKI) MEIKI="$value" ;;
                    PADDLE) PADDLE="$value" ;;
                    SYSTEM_PACKAGES) WITH_SYSTEM="$value"; saved_system_choice=1 ;;
                esac
                ;;
            BERGAMOT_PAIRS)
                validate_bergamot_pairs "$value" || exit 1
                BERGAMOT_PAIRS="$value"
                ;;
            *)
                err "неизвестная запись в сохранённых опциях установки: $key"
                exit 1
                ;;
        esac
    done < "$STATE_DIR/install-options"
    ASSUME_YES=1
    INTERACTIVE_MENU=0
    INSTALL_OPTS_SET=1
    # Older installations did not record whether system packages were chosen.
    # Make update predictable and non-interactive by checking/installing them.
    ((saved_system_choice)) || WITH_SYSTEM=yes
    [[ -n "$BERGAMOT_PAIRS" ]] && BERGAMOT=yes
fi

detect_distro() {
    if [[ "${ID:-}" == "nixos" ]] || [[ -f /etc/NIXOS ]]; then
        echo nix
    elif have nix && [[ -d /nix/store ]] && ! have pacman; then
        echo nix
    elif have pacman; then
        echo arch
    elif have nix || [[ -d /nix/store ]]; then
        echo nix
    else
        echo other
    fi
}

detect_compositor_hint() {
    if [[ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]]; then echo hyprland
    elif [[ -n "${NIRI_SOCKET:-}" || -n "${NIRI_SESSION:-}" ]]; then echo niri
    elif [[ -n "${MANGO_INSTANCE_SIGNATURE:-}" || -n "${MANGOWC:-}" ]]; then echo mango
    elif [[ -n "${SWAYSOCK:-}" ]]; then echo sway
    else
        local d="${XDG_CURRENT_DESKTOP:-}"
        case "${d,,}" in
            *hypr*) echo hyprland ;;
            *niri*) echo niri ;;
            *mango*) echo mango ;;
            *sway*) echo sway ;;
            *) echo wayland ;;
        esac
    fi
}

print_hotkey_hint() {
    local c
    c="$(detect_compositor_hint)"
    echo
    bold "Горячая клавиша (вставь в конфиг композитора):"
    case "$c" in
        hyprland)
            cat <<'EOF'
  # ~/.config/hypr/hyprland.lua
  hl.bind("SUPER + SHIFT + T", hl.dsp.exec_cmd("kizurium-translator --toggle"))

  # или hyprland.conf:
  # bind = SUPER_SHIFT, T, exec, kizurium-translator --toggle
EOF
            ;;
        niri)
            cat <<'EOF'
  # ~/.config/niri/config.kdl  (binds { ... })
  Mod+Shift+T { spawn "kizurium-translator" "--toggle"; }
EOF
            ;;
        mango)
            cat <<'EOF'
  # ~/.config/mango/config.conf
  bind=SUPER+SHIFT,t,spawn,kizurium-translator --toggle
EOF
            ;;
        sway)
            cat <<'EOF'
  # ~/.config/sway/config
  bindsym $mod+Shift+t exec kizurium-translator --toggle
EOF
            ;;
        *)
            cat <<'EOF'
  # В конфиге композитора:
  #   kizurium-translator --toggle
EOF
            ;;
    esac
}

# -------------------------------------------------------------- clean-cache
do_clean_cache() {
    step "Чистка кэша и логов (приложение остаётся)"
    if [[ -x "$BIN_DIR/kizurium-translator" ]]; then
        "$BIN_DIR/kizurium-translator" --stop 2>/dev/null || true
    elif have kizurium-translator; then
        kizurium-translator --stop 2>/dev/null || true
    fi
    local wiped=0
    if [[ -d "$CACHE_DIR" ]]; then
        rm -rf "$CACHE_DIR"
        ok "удалён $CACHE_DIR"
        wiped=1
    fi
    if [[ -d "$XDG_STATE" ]]; then
        rm -rf "$XDG_STATE"
        ok "удалён $XDG_STATE"
        wiped=1
    fi
    if ((wiped == 0)); then
        ok "нечего чистить (кэш/логи уже пусты)"
    fi
    cat <<EOF

Оставлено:
  конфиг          $CONFIG_DIR
  offline-модели  $STATE_DIR
  команда/venv    $BIN_DIR/kizurium-translator

Полный снос данных: ./install.sh --uninstall --purge
EOF
    ok "готово"
    exit 0
}

# ------------------------------------------------------------------ uninstall
do_uninstall() {
    step "Удаление Kizurium Translator"
    # Stop via whatever is on PATH or the known wrapper.
    if [[ -x "$BIN_DIR/kizurium-translator" ]]; then
        "$BIN_DIR/kizurium-translator" --stop 2>/dev/null || true
    elif have kizurium-translator; then
        kizurium-translator --stop 2>/dev/null || true
    fi

    if [[ "$INSTALL_MODE" == package ]] && have pacman && pacman -Q kizurium-translator >/dev/null 2>&1; then
        local -a remove_args=(-R kizurium-translator)
        if ((ASSUME_YES)) || ! is_tty; then remove_args+=(--noconfirm); fi
        sudo pacman "${remove_args[@]}"
        ok "пакет удалён через pacman"
    fi

    if [[ -e "$BIN_DIR/kizurium-translator" || -L "$BIN_DIR/kizurium-translator" ]]; then
        rm -f "$BIN_DIR/kizurium-translator"
        ok "удалён $BIN_DIR/kizurium-translator"
    else
        warn "команда не найдена: $BIN_DIR/kizurium-translator"
    fi
    if [[ -d "$VENV_DIR" ]]; then
        rm -rf "$VENV_DIR"
        ok "удалён $VENV_DIR"
    fi
    local data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
    rm -f "$data_home/applications/kizurium-translator.desktop" \
          "$data_home/icons/hicolor/256x256/apps/kizurium-translator.png" "$STATE_DIR/install-options"
    have update-desktop-database && update-desktop-database "$data_home/applications" >/dev/null 2>&1 || true
    # drop empty data dir if nothing else left
    rmdir "$STATE_DIR" 2>/dev/null || true

    local wipe=0
    if ((PURGE)); then
        wipe=1
    elif is_tty; then
        if ask_yn "Также удалить конфиг, кэш, логи и offline-модели?" N; then
            wipe=1
        fi
    fi

    if ((wipe)); then
        [[ -d "$CONFIG_DIR" ]] && rm -rf "$CONFIG_DIR" && ok "удалён $CONFIG_DIR"
        [[ -d "$CACHE_DIR" ]] && rm -rf "$CACHE_DIR" && ok "удалён $CACHE_DIR"
        [[ -d "$STATE_DIR" ]] && rm -rf "$STATE_DIR" && ok "удалён $STATE_DIR"
        [[ -d "$XDG_STATE" ]] && rm -rf "$XDG_STATE" && ok "удалён $XDG_STATE"
    else
        cat <<EOF

Конфиг, кэш и логи оставлены (можно снести вручную):
  $CONFIG_DIR
  $CACHE_DIR
  $STATE_DIR
  $XDG_STATE

Системные пакеты (grim, tesseract, gtk4 …) не трогаем — они могут
нужны другому ПО. На Arch: sudo pacman -Rns <пакет> если уверен.
На Nix: nix profile remove …  или убери из configuration.nix / flake.
EOF
    fi
    remove_path_rc_lines
    if have nix; then
        # Best-effort: list profile entries that look like ours.
        if nix profile list 2>/dev/null | grep -qi kizurium; then
            warn "в nix profile ещё есть kizurium — сними вручную:"
            echo "    nix profile list | grep -i kizurium"
            echo "    nix profile remove <номер-или-имя>"
        fi
    fi
    ok "готово"
    exit 0
}

pacman_install() {
    # $@ = packages. --noconfirm when --yes or non-interactive (piped CI).
    if ((ASSUME_YES)) || ! is_tty; then
        sudo pacman -S --needed --noconfirm "$@"
    else
        echo "  пакеты:"
        printf '    - %s\n' "$@"
        echo
        sudo pacman -S --needed "$@"
    fi
}

remove_path_rc_lines() {
    local marker="# kizurium-translator"
    for rc in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.config/fish/config.fish"; do
        [[ -f "$rc" ]] || continue
        if grep -qF "$marker" "$rc" 2>/dev/null; then
            # portable: rewrite without our marked lines
            local tmp
            tmp="$(mktemp)"
            grep -vF "$marker" "$rc" >"$tmp" || true
            mv "$tmp" "$rc"
            ok "убраны строки PATH из $rc"
        fi
    done
}

ensure_uv() {
    if have uv; then
        ok "uv $(uv --version | awk '{print $2}')"
        return 0
    fi
    step "uv не найден"
    if have pacman; then
        if [[ "$ASSUME_YES" == 1 ]] || ask_yn "Поставить uv через pacman? (нужен для установки)" Y; then
            pacman_install uv || exit 1
            ok "uv установлен"
            return 0
        fi
    fi
    err "нужен uv: https://docs.astral.sh/uv/getting-started/installation/"
    echo "  Arch: sudo pacman -S uv"
    echo "  потом снова: ./install.sh"
    exit 1
}

ensure_path() {
    case ":$PATH:" in
        *":$BIN_DIR:"*) return 0 ;;
    esac
    warn "~/.local/bin не в PATH — без этого команда «не найдётся»"
    echo "    Сейчас можно так:  $BIN_DIR/kizurium-translator"
    mkdir -p "$STATE_DIR"
    local escaped_bin
    printf -v escaped_bin '%q' "$BIN_DIR"
    printf 'export PATH=%s:"$PATH"\n' "$escaped_bin" >"$STATE_DIR/path-hint.sh"
    if ! is_tty || ((ASSUME_YES)); then
        ok "подсказка PATH: $STATE_DIR/path-hint.sh  (source её или добавь в shell rc)"
        return 0
    fi
    if ask_yn "Добавить $BIN_DIR в PATH в конфиг шелла (~/.zshrc / ~/.bashrc)?" N; then
        local line
        printf -v escaped_bin '%q' "$BIN_DIR"
        line="export PATH=$escaped_bin:\$PATH  # kizurium-translator"
        local added=0
        for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
            if [[ -f "$rc" ]] || [[ "$rc" == "$HOME/.zshrc" && -n "${ZSH_VERSION:-}" ]] || [[ "$rc" == "$HOME/.bashrc" && -n "${BASH_VERSION:-}" ]]; then
                touch "$rc"
                if ! grep -qF "# kizurium-translator" "$rc" 2>/dev/null; then
                    printf '\n%s\n' "$line" >>"$rc"
                    ok "добавлено в $rc"
                    added=1
                else
                    ok "уже есть в $rc"
                    added=1
                fi
            fi
        done
        # fish
        if [[ -d "$HOME/.config/fish" ]]; then
            local fishc="$HOME/.config/fish/config.fish"
            touch "$fishc"
            local fish_bin="${BIN_DIR//\\/\\\\}"
            fish_bin="${fish_bin//\'/\\\'}"
            local fline="fish_add_path '$fish_bin'  # kizurium-translator"
            if ! grep -qF "# kizurium-translator" "$fishc" 2>/dev/null; then
                printf '\n%s\n' "$fline" >>"$fishc"
                ok "добавлено в $fishc"
                added=1
            fi
        fi
        if ((added)); then
            warn "открой новый терминал (или: source ~/.zshrc) — тогда сработает просто: kizurium-translator"
        else
            ok "подсказка: $STATE_DIR/path-hint.sh"
        fi
    else
        ok "подсказка: $STATE_DIR/path-hint.sh"
    fi
}

if ((CLEAN_CACHE)); then
    do_clean_cache
fi

if ((UNINSTALL)); then
    do_uninstall
fi

# -------------------------------------------------------------------- banner
DISTRO="$(detect_distro)"
COMP="$(detect_compositor_hint)"

echo
bold "Kizurium Translator"
echo "  Wayland · Arch / NixOS · compositor: $COMP · ${PRETTY_NAME:-${ID:-unknown}}"
echo "  сеть и данные: docs/trust.md"
echo

# -------------------------------------------------------------- main menu
INSTALL_OPTS_SET=0
if ((INTERACTIVE_MENU)) && is_tty && ((ASSUME_YES == 0)); then
    while true; do
        if ! menu_pick "Меню" \
            "install|Установить / обновить" \
            "uninstall|Удалить (конфиг и модели оставить)" \
            "purge|Удалить полностью (config + cache + models + logs)" \
            "clean|Почистить только кэш и логи" \
            "doctor|Проверка окружения (doctor)" \
            "quit|Выход"; then
            ok "выход"
            exit 0
        fi
        case "$MENU_PICK" in
            quit)
                ok "выход"
                exit 0
                ;;
            uninstall)
                PURGE=0
                do_uninstall
                ;;
            purge)
                PURGE=1
                do_uninstall
                ;;
            clean)
                do_clean_cache
                ;;
            doctor)
                run_doctor_now
                echo
                continue
                ;;
            install)
                # «Назад в меню» → checklist fails → loop, not exit.
                if [[ "$INSTALL_MODE" == package ]]; then
                    if ! checklist \
                        "system|1|Зависимости Arch-пакета (pacman); приложение включает RapidOCR" \
                        "bergamot|0|Bergamot: локальная модель en→ru из репозитория Mozilla"; then
                        continue
                    fi
                    if checklist_has system; then WITH_SYSTEM=yes; else WITH_SYSTEM=no; fi
                    if checklist_has bergamot; then BERGAMOT=yes; BERGAMOT_PAIRS="${BERGAMOT_PAIRS:-en-ru}"; else BERGAMOT=no; fi
                    break
                fi
                if ! checklist \
                    "system|1|Системные пакеты (gtk4-layer-shell, tesseract, GStreamer/PipeWire для захвата …)" \
                    "meiki|1|Meiki OCR — японские игры" \
                    "packs|0|Offline language packs en→ru / ja→ru (~160 МБ HF)" \
                    "bergamot|0|Bergamot: локальная модель en→ru из репозитория Mozilla (~34 МБ)" \
                    "paddle|0|PaddleOCR — тяжёлый движок (модели настраиваются в Настройках)"; then
                    continue
                fi
                if checklist_has system; then WITH_SYSTEM=yes; else WITH_SYSTEM=no; fi
                if checklist_has meiki; then MEIKI=yes; else MEIKI=no; fi
                if checklist_has packs; then OFFLINE_PACKS=yes; else OFFLINE_PACKS=no; fi
                if checklist_has bergamot; then BERGAMOT=yes; BERGAMOT_PAIRS="${BERGAMOT_PAIRS:-en-ru}"; else BERGAMOT=no; BERGAMOT_PAIRS=""; fi
                if checklist_has paddle; then PADDLE=yes; else PADDLE=no; fi
                INSTALL_OPTS_SET=1
                ok "выбрано: system=$WITH_SYSTEM  meiki=$MEIKI  offline-packs=$OFFLINE_PACKS  bergamot=${BERGAMOT_PAIRS:-нет}  paddle=$PADDLE"
                echo
                break
                ;;
            *)
                err "неизвестный пункт меню"
                exit 2
                ;;
        esac
    done
elif ((ASSUME_YES)); then
    [[ "$BERGAMOT" == ask ]] && BERGAMOT=no
    [[ "$PADDLE" == ask ]] && PADDLE=no
    [[ "$WITH_SYSTEM" == ask ]] && { ((UPDATE)) || WITH_SYSTEM=yes; }
    [[ "$MEIKI" == ask ]] && MEIKI=yes
    [[ "$OFFLINE_PACKS" == ask ]] && OFFLINE_PACKS=no
    INSTALL_OPTS_SET=1
fi

# -------------------------------------------------------------------- nix
install_nix() {
    step "Nix / NixOS"
    if ! have nix; then
        err "найден /nix, но нет команды nix в PATH"
        echo "  открой новый шелл или: source /etc/profile"
        exit 1
    fi
    ok "nix $(nix --version 2>/dev/null | head -1 || echo ok)"

    if [[ ! -f "$REPO_DIR/flake.nix" ]]; then
        err "нет flake.nix в $REPO_DIR"
        exit 1
    fi

    echo
    bold "Как поставить (выбери):"
    cat <<EOF
  [1] nix run .#          — попробовать без установки (из этой папки)
  [2] nix profile add .#kizurium-translator
                          — в user profile (команда в PATH)
  [3] NixOS module hint   — programs.kizurium-translator.enable (см. docs/install.md)
EOF
    local choice="2"
    if is_tty && ((ASSUME_YES == 0)); then
        read -r -p "  выбор [1/2/3, Enter=2]: " choice || choice="2"
        [[ -z "$choice" ]] && choice="2"
    elif ((ASSUME_YES)); then
        choice="2"
    fi

    case "$choice" in
        1)
            step "nix run"
            echo "  Запуск:  nix run $REPO_DIR#"
            echo "  Doctor:  nix run $REPO_DIR# -- --doctor"
            (cd "$REPO_DIR" && nix run .# -- --doctor) || {
                warn "doctor ругнулся — смотри вывод выше (часто нет grim в PATH)"
                echo "  Добавь в environment.systemPackages: grim slurp wl-clipboard gtk4-layer-shell tesseract …"
                echo "  Подробно: docs/install.md"
            }
            ;;
        2)
            step "nix profile add"
            (cd "$REPO_DIR" && nix profile add .#kizurium-translator)
            ok "пакет в nix profile"
            if have kizurium-translator; then
                kizurium-translator --doctor || true
            else
                warn "открой новый терминал или: hash -r"
                echo "  потом: kizurium-translator --doctor"
            fi
            ;;
        3)
            cat <<'EOF'

NixOS (configuration.nix / flake):

  environment.systemPackages = [
    (builtins.getFlake "path:/path/to/kizurium-translator").packages.${pkgs.system}.default
    # плюс runtime:
    pkgs.grim pkgs.slurp pkgs.wl-clipboard pkgs.tesseract
    pkgs.gtk4 pkgs.gtk4-layer-shell
  ];
  # tesseract languages: eng rus jpn — через tesseract.protocols / tessdata

Home-manager:
  home.packages = [ …тот же package… ];

Полная шпаргалка: docs/install.md
EOF
            ;;
        *)
            err "неизвестный выбор: $choice"
            exit 2
            ;;
    esac

    # Optional offline packs if binary is available
    if have kizurium-translator; then
        if [[ "$OFFLINE_PACKS" == ask ]]; then
            if ask_yn "Скачать offline-модели en→ru / ja→ru (~160 МБ)? Для перевода на Nix нужен отдельный runtime ctranslate2." N; then
                OFFLINE_PACKS=yes
            else
                OFFLINE_PACKS=no
            fi
        fi
        if [[ "$OFFLINE_PACKS" == yes ]]; then
            step "Офлайн language packs"
            kizurium-translator --pack-install all || warn "пакеты не все скачались — позже: kizurium-translator --pack-install all"
            warn "Nix-пакет не включает ctranslate2: модели скачаны, но локальный перевод"
            warn "заработает только после добавления local-mt в окружение (docs/language-packs.md)."
        fi
    fi

    print_hotkey_hint
    cat <<EOF

$(bold "Готово (Nix).")
  меню / область     kizurium-translator
  toggle live        kizurium-translator --toggle
  диагностика        kizurium-translator --doctor
  подробности        docs/install.md
EOF
    exit 0
}

if [[ "$DISTRO" == nix ]]; then
    # If both nix AND pacman exist (e.g. someone on Arch with nix), prefer asking.
    if have pacman && is_tty && ((ASSUME_YES == 0)); then
        echo "  На машине есть и nix, и pacman."
        if ask_yn "Ставить через Nix flake (рекомендуется для NixOS)?" Y; then
            install_nix
        fi
        # else fall through to Arch path
    else
        install_nix
    fi
fi

if [[ "$DISTRO" != arch ]] && ! have pacman; then
    step "Дистрибутив не поддержан установщиком автоматически"
    err "сейчас есть ветки: Arch-family (pacman) и Nix/NixOS"
    echo
    echo "Что сделать:"
    echo "  1) поставь аналоги пакетов сам:"
    printf '     %s\n' "${SYS_PKGS[*]}"
    echo "  2) uv venv --system-site-packages && uv sync --extra rapid --extra local-mt"
    echo "  3) uv run kizurium-translator --doctor"
    echo
    echo "Подробно: docs/install.md  (раздел Если что-то не так)"
    exit 1
fi

# Build as the invoking user; only dependency installation and pacman -U
# require privilege. Never source PKGBUILD as root.
install_arch_package() {
    step "Arch-пакет (pacman)"
    if ((PREFIX_SET)); then
        err "--prefix доступен только с --user; Arch-пакет устанавливается в /usr."
        exit 2
    fi
    if [[ "$OFFLINE_PACKS" == yes || "$MEIKI" == yes || "$PADDLE" == yes || -n "$EXTRAS" ]]; then
        err "Meiki, PaddleOCR и Python extras требуют режима --user."
        err "Повторите ту же команду с --user, либо уберите дополнительные компоненты для установки Arch-пакета."
        exit 2
    fi
    if ((EUID == 0)); then
        err "Запустите ./install.sh обычным пользователем: makepkg не работает от root."
        exit 1
    fi
    local -a build_deps=(base-devel python-build python-installer python-hatchling rust maturin "${SYS_PKGS[@]}")
    [[ "$BERGAMOT" == yes ]] && build_deps+=(uv)
    local -a missing_build=() make_args=(--force)
    local dep package_file
    for dep in "${build_deps[@]}"; do
        pacman -Q "$dep" >/dev/null 2>&1 || missing_build+=("$dep")
    done
    if ((${#missing_build[@]})); then
        if [[ "$WITH_SYSTEM" == no ]]; then
            err "Не хватает зависимостей: ${missing_build[*]}"
            exit 1
        fi
        pacman_install "${missing_build[@]}"
    fi
    [[ "$WITH_SYSTEM" == no ]] || make_args+=(--syncdeps)
    if ((ASSUME_YES)) || ! is_tty; then make_args+=(--noconfirm); fi
    # Absolute paths work even when the checkout has spaces. makepkg's output
    # list, rather than a wildcard, selects the archive built by this recipe.
    (cd "$REPO_DIR" && makepkg "${make_args[@]}")
    local -a archives=()
    mapfile -t archives < <(cd "$REPO_DIR" && makepkg --packagelist)
    if ((${#archives[@]} != 1)) || [[ ! -f "${archives[0]}" ]]; then
        err "makepkg не создал единственный ожидаемый архив пакета"
        exit 1
    fi
    package_file="${archives[0]}"
    local -a install_args=(-U)
    if ((ASSUME_YES)) || ! is_tty; then install_args+=(--noconfirm); fi
    sudo pacman "${install_args[@]}" -- "$package_file"
    pacman -Q kizurium-translator

    # Migrate only our generated launcher; keep unrelated user commands.
    if [[ -f "$BIN_DIR/kizurium-translator" ]] &&
       grep -qF '# Generated by kizurium-translator install.sh' "$BIN_DIR/kizurium-translator"; then
        rm -f -- "$BIN_DIR/kizurium-translator"
    fi
    local data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
    local desktop_file="$data_home/applications/kizurium-translator.desktop"
    if [[ -f "$desktop_file" ]] && grep -qF "$BIN_DIR/kizurium-translator" "$desktop_file"; then
        rm -f -- "$desktop_file"
    fi
    hash -r
    if [[ "$BERGAMOT" == yes ]]; then
        /usr/bin/kizurium-translator --bergamot-runtime-install || warn "Bergamot: повторите установку runtime из настроек"
        for dep in $BERGAMOT_PAIRS; do
            /usr/bin/kizurium-translator --bergamot-install "$dep" || warn "Bergamot $dep: повторите установку из настроек"
        done
    fi
    /usr/bin/kizurium-translator --doctor || warn "doctor нашёл проблемы — см. вывод выше"
    ok "Установлено: /usr/bin/kizurium-translator (pacman -Qi kizurium-translator)"
    print_hotkey_hint
    echo "  Обновить: ./install.sh --update; удалить: ./install.sh --uninstall"
    exit 0
}

if [[ "$INSTALL_MODE" == package ]]; then
    install_arch_package
fi

# Defaults if flags left knobs on "ask" (non-interactive partial flags).
if ((INSTALL_OPTS_SET == 0)); then
    [[ "$WITH_SYSTEM" == ask ]] && WITH_SYSTEM=yes
    [[ "$MEIKI" == ask ]] && MEIKI=yes
    [[ "$OFFLINE_PACKS" == ask ]] && OFFLINE_PACKS=no
    [[ "$BERGAMOT" == ask ]] && BERGAMOT=no
    [[ "$PADDLE" == ask ]] && PADDLE=no
fi

step "Установка"
ok "system=$WITH_SYSTEM  meiki=$MEIKI  offline-packs=$OFFLINE_PACKS"

# -------------------------------------------------------------------- system
step "Система"
ok "${PRETTY_NAME:-${ID:-неизвестно}}"
ok "pacman найден"

step "Системные пакеты"
missing=()
for p in "${SYS_PKGS[@]}"; do
    pacman -Qi "$p" >/dev/null 2>&1 || missing+=("$p")
done
if ((${#missing[@]} == 0)); then
    ok "все ${#SYS_PKGS[@]} пакетов уже установлены"
elif [[ "$WITH_SYSTEM" == yes ]]; then
    pacman_install "${missing[@]}" || exit 1
    ok "установлено ${#missing[@]} пакетов"
elif [[ "$WITH_SYSTEM" == no ]]; then
    err "не хватает: ${missing[*]}"
    err "запусти снова и согласись на системные пакеты, или: ./install.sh --system"
    exit 1
else
    echo "  не хватает:"
    printf '    - %s\n' "${missing[@]}"
    if ask_yn "установить ${#missing[@]} пакетов через pacman?" Y; then
        pacman_install "${missing[@]}" || exit 1
        ok "установлено ${#missing[@]} пакетов"
    else
        err "установка прервана: не хватает ${missing[*]}"
        exit 1
    fi
fi

# ----------------------------------------------------------------------- uv
step "Python-окружение"
ensure_uv

gtk_python_usable() {
    "$1" -c 'import sys, gi, cairo; assert sys.version_info >= (3, 11); gi.require_version("Gtk", "4.0"); gi.require_version("Gtk4LayerShell", "1.0")' >/dev/null 2>&1
}

# GTK bindings belong to the distribution's Python ABI. A uv-managed Python
# with --system-site-packages cannot see another interpreter's gi extension.
APP_PYTHON=""
for candidate in /usr/bin/python3 python3 python; do
    candidate="$(command -v -- "$candidate" 2>/dev/null || true)"
    if [[ -n "$candidate" ]] && gtk_python_usable "$candidate"; then
        APP_PYTHON="$candidate"
        break
    fi
done
if [[ -z "$APP_PYTHON" ]]; then
    err "Не найден Python 3.11+ с GTK4, PyGObject, Cairo и gtk4-layer-shell."
    err "Установите системные GTK-привязки (Arch: python-gobject python-cairo gtk4 gtk4-layer-shell)."
    exit 1
fi
PYVER="$("$APP_PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
IFS=. read -r py_major py_minor <<< "$PYVER"
if ((py_major < 3 || (py_major == 3 && py_minor < 11))); then
    err "Python $PYVER не поддерживается; нужен Python 3.11 или новее"
    exit 1
fi
ok "Python $PYVER (совместимость зависимостей проверит uv sync)"

mkdir -p "$STATE_DIR" "$BIN_DIR"

_has_extra() {
    [[ " $EXTRAS " == *" all "* ]] || [[ " $EXTRAS " == *" $1 "* ]]
}

if [[ "$OFFLINE_PACKS" == ask ]]; then
    if _has_extra local-mt; then
        OFFLINE_PACKS=yes
    else
        if ask_yn "Скачать офлайн language packs en→ru / ja→ru (~160 МБ с Hugging Face)?" Y; then
            OFFLINE_PACKS=yes
        else
            OFFLINE_PACKS=no
        fi
        echo "  (локальные модели; онлайн-перевод по умолчанию отдельно — docs/trust.md)"
    fi
fi

if [[ "$MEIKI" == ask ]]; then
    MEIKI=yes
fi

# Defaults: RapidOCR always; local-mt when offline packs; meiki if asked.
if [[ -z "$EXTRAS" ]]; then
    EXTRAS="rapid"
    [[ "$OFFLINE_PACKS" == yes ]] && EXTRAS="$EXTRAS local-mt"
    [[ "$MEIKI" == yes ]] && EXTRAS="$EXTRAS meiki"
else
    [[ "$OFFLINE_PACKS" == yes ]] && ! _has_extra local-mt && EXTRAS="$EXTRAS local-mt"
    [[ "$MEIKI" == yes ]] && ! _has_extra meiki && EXTRAS="$EXTRAS meiki"
fi

if [[ -x "$VENV_DIR/bin/python" ]]; then
    venv_version="$("$VENV_DIR/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
    if [[ "$venv_version" != "$PYVER" ]] ||
       ! grep -qiE '^include-system-site-packages *= *true' "$VENV_DIR/pyvenv.cfg" 2>/dev/null ||
       ! gtk_python_usable "$VENV_DIR/bin/python"; then
        warn "окружение несовместимо с системными GTK-привязками Python $PYVER — пересоздаю"
        rm -rf "$VENV_DIR"
        uv venv --python "$APP_PYTHON" --system-site-packages "$VENV_DIR" >/dev/null
        ok "окружение пересоздано: $VENV_DIR"
    else
        ok "окружение уже есть и совместимо: $VENV_DIR"
    fi
else
    uv venv --python "$APP_PYTHON" --system-site-packages "$VENV_DIR" >/dev/null
    ok "создано окружение $VENV_DIR"
fi

cd "$REPO_DIR"
uv_extra_args=()
for extra in $EXTRAS; do
    uv_extra_args+=(--extra "$extra")
done
if ! UV_PROJECT_ENVIRONMENT="$VENV_DIR" uv sync --python "$APP_PYTHON" --locked --no-editable "${uv_extra_args[@]}"; then
    err "не удалось установить Python-зависимости для Python $PYVER"
    warn "проверь совместимость выбранных extras и повтори установку с нужными флагами"
    exit 1
fi
if ! gtk_python_usable "$VENV_DIR/bin/python"; then
    err "После установки окружение потеряло доступ к системным GTK-привязкам: $VENV_DIR"
    exit 1
fi
ok "пакет установлен${EXTRAS:+ (extras: $EXTRAS)}"

launcher="$(mktemp "$BIN_DIR/.kizurium-translator.XXXXXX")"
{
    printf '#!/usr/bin/env bash\n'
    printf '# Generated by kizurium-translator install.sh\n'
    printf 'if ! %q -c %q >/dev/null 2>&1; then\n' "$VENV_DIR/bin/python" 'import gi, cairo; gi.require_version("Gtk", "4.0"); gi.require_version("Gtk4LayerShell", "1.0")'
    printf '    if [[ -x /usr/bin/kizurium-translator && ! /usr/bin/kizurium-translator -ef "$0" ]]; then\n'
    printf '        exec /usr/bin/kizurium-translator "$@"\n'
    printf '    fi\n'
    printf '    printf "Окружение Kizurium несовместимо с системным GTK. Выполните ./install.sh --update из каталога проекта.\\n" >&2\n'
    printf '    exit 1\n'
    printf 'fi\n'
    printf 'export VIRTUAL_ENV=%q\n' "$VENV_DIR"
    printf 'export PATH=%q:"$PATH"\n' "$VENV_DIR/bin"
    printf '# Preserve the compositor environment for Wayland capture.\n'
    printf 'exec %q "$@"\n' "$VENV_DIR/bin/kizurium-translator"
} > "$launcher"
chmod 755 "$launcher"
mv -f -- "$launcher" "$BIN_DIR/kizurium-translator"
ok "команда: $BIN_DIR/kizurium-translator"

ensure_path

step "Нативные ускорения Rust"
if have cargo; then
    # The app remains usable if this optional build fails. No compiler or
    # prebuilt native code is downloaded by the application at runtime.
    if uv pip install --python "$VENV_DIR/bin/python" "$REPO_DIR/native"; then
        ok "Rust: статистика кадров, связные области и диапазон чернил"
    else
        warn "Rust-модуль не собран; приложение использует Python-реализацию"
    fi
else
    warn "нет cargo — используется Python. Для ускорения установите Rust и повторите --user --update"
fi

if [[ -f "$CONFIG_DIR/config.toml" ]]; then
    ok "конфиг: $CONFIG_DIR/config.toml"
else
    mkdir -p "$CONFIG_DIR"
    cp "$REPO_DIR/config.example.toml" "$CONFIG_DIR/config.toml"
    ok "конфиг создан: $CONFIG_DIR/config.toml"
fi

PACKS_OK=1
if [[ "$OFFLINE_PACKS" == yes ]]; then
    step "Офлайн language packs"
    if ! have aria2c; then
        warn "нет aria2c — один поток, очень медленно. Поставь пакет aria2."
    fi
    if "$BIN_DIR/kizurium-translator" --pack-install all; then
        ok "пакеты en→ru / ja→ru готовы"
    else
        PACKS_OK=0
        warn "не все пакеты скачались (обрыв сети / Ctrl+C) — докачай:"
        echo "    $BIN_DIR/kizurium-translator --pack-install all"
    fi
fi

step "OCR-движки"
if _has_extra rapid; then
    if warm_out="$("$BIN_DIR/kizurium-translator" --ocr-warmup rapid 2>&1)"; then
        ok "RapidOCR: модели на месте"
    else
        printf '%s\n' "$warm_out" | tail -3
        warn "RapidOCR: модели не подготовлены (нет сети?) — докачай: $BIN_DIR/kizurium-translator --ocr-warmup rapid"
    fi
fi
if [[ "$PADDLE" == yes ]]; then
    # PaddlePaddle wheels currently lag the newest CPython ABI. On Python 3.14+
    # keep the app and GTK bindings in their normal venv, and run Paddle in a
    # compatible sidecar interpreter through the persistent OCR worker.
    mkdir -p "$XDG_STATE"
    PADDLE_LOG="$XDG_STATE/paddle-install.log"
    PADDLE_ENV="$VENV_DIR"
    PADDLE_PYTHON="$VENV_DIR/bin/python"
    if (( py_major > 3 || (py_major == 3 && py_minor >= 14) )); then
        PADDLE_ENV="$STATE_DIR/paddle-venv"
        PADDLE_PYTHON="$PADDLE_ENV/bin/python"
        if [[ ! -x "$PADDLE_PYTHON" ]] || ! "$PADDLE_PYTHON" -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 13))' >/dev/null 2>&1; then
            rm -rf -- "$PADDLE_ENV"
            if ! uv venv --python 3.13 "$PADDLE_ENV" >"$PADDLE_LOG" 2>&1; then
                warn "Не удалось подготовить Python 3.13 для PaddleOCR:"
                tail -n 12 "$PADDLE_LOG"
                echo "  подробный лог: $PADDLE_LOG"
                PADDLE_PYTHON=""
            fi
        fi
    fi
    if [[ -n "$PADDLE_PYTHON" ]] && uv pip install \
        --python "$PADDLE_PYTHON" paddleocr paddlepaddle >"$PADDLE_LOG" 2>&1 &&
        "$PADDLE_PYTHON" -c 'import paddle, paddleocr' >>"$PADDLE_LOG" 2>&1; then
        ok "PaddleOCR установлен (Python $("$PADDLE_PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])'))"
        if [[ "$PADDLE_PYTHON" != "$VENV_DIR/bin/python" ]]; then
            echo "  приложение будет вызывать постоянный Paddle worker из $PADDLE_ENV"
        fi
        echo "  модели detection/recognition указываются в Настройки → OCR (автозагрузки нет)"
    else
        warn "PaddleOCR не установлен; причина от uv:"
        tail -n 14 "$PADDLE_LOG"
        echo "  подробный лог: $PADDLE_LOG"
        if (( py_major > 3 || (py_major == 3 && py_minor >= 14) )); then
            echo "  Kizurium остаётся в Python $PYVER; PaddleOCR использует отдельный Python 3.13 worker, если его зависимости удалось установить."
        fi
        echo "  Остальные OCR-движки остаются доступны."
    fi
fi

BERGAMOT_OK=1
if [[ "$BERGAMOT" == yes && -n "$BERGAMOT_PAIRS" ]]; then
    step "Bergamot (локальный перевод)"
    # PyYAML verifies any model; the engine only runs it. The binding has no wheel
    # for this Python, so it gets its own small environment (Python 3.10 via uv).
    uv pip install --python "$VENV_DIR/bin/python" pyyaml >/dev/null 2>&1 || true
    if "$VENV_DIR/bin/python" -c "import bergamot, yaml" 2>/dev/null; then
        ok "движок Bergamot уже установлен"
    elif uv pip install --python "$VENV_DIR/bin/python" --only-binary :all: bergamot >/dev/null 2>&1; then
        ok "движок Bergamot установлен"
    elif "$BIN_DIR/kizurium-translator" --bergamot-runtime-install; then
        ok "движок Bergamot установлен отдельно (свой Python 3.10)"
    else
        BERGAMOT_OK=0
        warn "движок Bergamot не установился: модели будут скачаны и проверены, перевод заработает после:"
        warn "  $BIN_DIR/kizurium-translator --bergamot-runtime-install"
        warn "Рабочая офлайн-замена сейчас: ./install.sh --user --offline-packs (OPUS-MT)"
    fi
    for pair in $BERGAMOT_PAIRS; do
        if "$BIN_DIR/kizurium-translator" --bergamot-install "$pair"; then
            ok "модель $pair проверена"
        else
            # A damaged cached model is dropped and fetched again, once.
            "$BIN_DIR/kizurium-translator" --bergamot-remove "$pair" >/dev/null 2>&1 || true
            if "$BIN_DIR/kizurium-translator" --bergamot-install "$pair"; then
                ok "модель $pair загружена заново"
            else
                BERGAMOT_OK=0
                warn "модель $pair не установлена — повтори: $BIN_DIR/kizurium-translator --bergamot-install $pair"
            fi
        fi
    done
fi

step "Ярлык и Desktop Actions"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
APPS_DIR="$DATA_HOME/applications"
ICON_DIR="$DATA_HOME/icons/hicolor/256x256/apps"
mkdir -p "$APPS_DIR" "$ICON_DIR"
desktop_quote() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//\`/\\\`}"
    value="${value//\$/\\\$}"
    printf '"%s"' "$value"
}

# Desktop Exec has its own quoting rules; sed replacement text is unsafe for
# prefixes containing spaces or characters meaningful to sed/desktop files.
desktop_exec="$(desktop_quote "$BIN_DIR/kizurium-translator")"
desktop_tmp="$(mktemp "$APPS_DIR/.kizurium-translator.desktop.XXXXXX")"
while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
        Exec=kizurium-translator*)
            printf 'Exec=%s%s\n' "$desktop_exec" "${line#Exec=kizurium-translator}"
            ;;
        TryExec=kizurium-translator*)
            printf 'TryExec=%s%s\n' "$desktop_exec" "${line#TryExec=kizurium-translator}"
            ;;
        *) printf '%s\n' "$line" ;;
    esac
done < "$REPO_DIR/assets/kizurium-translator.desktop" > "$desktop_tmp"
chmod 644 "$desktop_tmp"
mv -f -- "$desktop_tmp" "$APPS_DIR/kizurium-translator.desktop"
install -m 644 "$REPO_DIR/assets/icons/kizurium-translator.png" "$ICON_DIR/kizurium-translator.png"
have update-desktop-database && update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
have kbuildsycoca6 && kbuildsycoca6 >/dev/null 2>&1 || true
have gtk-update-icon-cache && gtk-update-icon-cache -q "$DATA_HOME/icons/hicolor" >/dev/null 2>&1 || true
ok "ярлык: $APPS_DIR/kizurium-translator.desktop (действия: Открыть окно, Автоперевод, Настройки …)"

# Store plain, validated data for --update; the loader above never evaluates it.
options_tmp="$(mktemp "$STATE_DIR/.install-options.XXXXXX")"
cat >"$options_tmp" <<EOF
EXTRAS=$EXTRAS
SYSTEM_PACKAGES=$WITH_SYSTEM
OFFLINE_PACKS=$OFFLINE_PACKS
MEIKI=$MEIKI
BERGAMOT_PAIRS=$BERGAMOT_PAIRS
PADDLE=$PADDLE
EOF
chmod 600 "$options_tmp"
mv -f -- "$options_tmp" "$STATE_DIR/install-options"

step "Состояние моделей"
"$BIN_DIR/kizurium-translator" --model-status || true

step "Проверка"
# Doctor over SSH/TTY will complain about WAYLAND_DISPLAY — that must not
# undo a successful package install. Soft-warn; user runs live from Wayland.
if "$BIN_DIR/kizurium-translator" --doctor; then
    ok "doctor: всё критичное на месте"
else
    warn "doctor нашёл проблемы (часто: нет Wayland в этой сессии / SSH)"
    warn "установка при этом завершена — зайди в графическую Wayland-сессию и снова: $BIN_DIR/kizurium-translator --doctor"
fi

print_hotkey_hint

RUN_CMD="kizurium-translator"
PATH_HAS_BIN=1
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) RUN_CMD="$BIN_DIR/kizurium-translator"; PATH_HAS_BIN=0 ;;
esac

# Что на самом деле выигрывает в PATH. Две установки молча делят одно имя
# команды: pacman кладёт в /usr/bin, установщик - в ~/.local/bin, а
# ~/.local/bin обычно стоит раньше. После `pacman -U` + `./install.sh` человек
# запускает старую копию и видит её версию, ничего не поняв. Спросить нельзя -
# ответ один и тот же, но сказать, какой файл победил, можно и нужно.
SHADOWED="$(command -v kizurium-translator 2>/dev/null || true)"
if [[ -n "$SHADOWED" && "$SHADOWED" != "$BIN_DIR/kizurium-translator" ]]; then
    warn "в PATH есть ещё одна копия: $SHADOWED"
    if "$SHADOWED" --version >/dev/null 2>&1; then
        echo "    версия, которую запустит терминал: $("$SHADOWED" --version 2>&1 | tail -1)"
    fi
    echo "    только что поставлено:              $("$BIN_DIR/kizurium-translator" --version 2>&1 | tail -1)"
    echo "    одна из двух установок лишняя:"
    echo "      pacman -Rns kizurium-translator   # убрать системную"
    echo "      rm -f \"$BIN_DIR/kizurium-translator\"  # убрать эту"
fi

if ((PACKS_OK && BERGAMOT_OK)); then
    bold "Готово."
else
    bold "Поставлено частично."
    echo "  приложение работает; недостающие офлайн-модели — докачай командами выше."
fi
cat <<EOF
  выбор области и меню     $RUN_CMD
  toggle live              $RUN_CMD --toggle
  OCR в буфер (drag→copy)  $RUN_CMD --ocr-copy
  окно переводчика         $RUN_CMD --text
  докачать offline packs   $RUN_CMD --pack-install all
  офлайн-пакеты (список)   $RUN_CMD --packs
  остановить live          $RUN_CMD --stop
  удалить                  ./install.sh --uninstall
  или без папки репо       $RUN_CMD --uninstall --purge

Лог: $XDG_STATE/overlay.log
Подробнее: docs/install.md
EOF

# The one thing that made a person think the install had failed: the command is
# in ~/.local/bin, that directory is not on PATH, and "kizurium-translator" then
# answers `command not found`. The paths above are correct either way and a
# reader skims them, so the failure is stated instead of implied.
if ((PATH_HAS_BIN == 0)); then
    cat <<EOF

$(bold "ВНИМАНИЕ")
  $BIN_DIR не в PATH — команда «kizurium-translator» не найдётся из терминала.
  Это не поломка установки: приложение на месте, путь к нему указан выше.

  Прямо сейчас:
    export PATH="$BIN_DIR:\$PATH"

  Навсегда (один раз, для твоего шелла):
$(for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
        [ -f "$rc" ] && printf '    echo %s >> %s\n' \
            "'export PATH=\"$BIN_DIR:\$PATH\"  # kizurium-translator'" "$rc"
      done)
$([ -d "$HOME/.config/fish" ] && printf '    fish_add_path %s  # kizurium-translator\n' "$BIN_DIR")

  Arch вместо этого — pacman, тогда путь уже правильный:
    makepkg -si
EOF
fi
