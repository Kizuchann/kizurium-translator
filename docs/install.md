# Установка

Wayland, Arch (pacman) или NixOS. Зависимости: grim, gtk4-layer-shell,
quickshell, tesseract и остальное из списка в README — их ставит `./install.sh`.

Композиторы: Hyprland, Niri, MangoWC, Sway.

## Arch

```bash
git clone https://github.com/Kizuchann/kizurium-translator
cd kizurium-translator
./install.sh
```

`./install.sh` — меню: установка, удаление, purge, чистка кэша, doctor.
В установке галочки (↑↓ / цифры / Space / Enter): пакеты, Meiki, offline packs.
По умолчанию пакеты + Meiki; offline (~160 МБ) выкл. Докачать packs:
`kizurium-translator --pack-install all`.

Без вопросов: `./install.sh --yes`

Команда: `~/.local/bin/kizurium-translator` (скрипт подскажет, если каталога
нет в PATH).

```bash
./install.sh --uninstall                 # спросит про конфиг/кэш/модели
./install.sh --uninstall --purge         # конфиг + кэш + логи + offline-модели
./install.sh --clean-cache               # только кэш переводов и логи (приложение остаётся)

# если папку репозитория уже снесли — та же чистка через установленную команду:
kizurium-translator --uninstall
kizurium-translator --uninstall --purge
```

После `./install.sh` переводчик живёт в `~/.local/…`, не в папке клона: её можно
переименовать, перенести или удалить — команда продолжит работать. Обновление
кода: снова `./install.sh` из нового места.

## Nix

```bash
nix run .# -- --doctor
nix profile install .#kizurium-translator
# или ./install.sh
```

Модуль: `programs.kizurium-translator.enable` в `flake.nix`.

Packs после установки: `kizurium-translator --pack-install all`.

## Горячие клавиши

| Композитор | Пример |
|---|---|
| Hyprland (`hyprland.lua`) | `hl.bind("SUPER + SHIFT + T", hl.dsp.exec_cmd("kizurium-translator --toggle"))` |
| Hyprland (`hyprland.conf`) | `bind = SUPER_SHIFT, T, exec, kizurium-translator --toggle` |
| Niri | `Mod+Shift+T { spawn "kizurium-translator" "--toggle"; }` |
| MangoWC | `bind=SUPER+SHIFT,t,spawn,kizurium-translator --toggle` |
| Sway | `bindsym $mod+Shift+t exec kizurium-translator --toggle` |

`kizurium-translator --doctor` подставляет пример под текущий композитор.

Несколько мониторов: селектор, `--region "X,Y WxH"` или `--output`.

## Если что-то не так

| Симптом | Действие |
|---|---|
| нет `uv` | `pacman -S uv` или согласие в install |
| `command not found` | новый шелл или `~/.local/bin/kizurium-translator` |
| `No module named 'gi'` | снова `./install.sh` |
| нет quickshell | `pacman -S quickshell` или `--region` / `--output` |
| нет Wayland | запуск из графической сессии |

Другие дистрибутивы: зависимости вручную, затем
`uv venv --system-site-packages && uv sync --extra rapid --extra local-mt`.

Сеть и данные: [trust.md](trust.md).
