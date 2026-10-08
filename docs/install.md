# Установка

Kizurium рассчитан на **Wayland** и работает с композиторами **Hyprland, Niri, MangoWC и Sway**.

Поддерживаемые способы установки:

- **Arch Linux / Arch-based** — через `./install.sh` или `makepkg`.
- **Nix / NixOS** — через Flakes.
- **Другие дистрибутивы** — вручную, если доступны необходимые Wayland/GTK-зависимости.

> Для большинства пользователей на Arch достаточно выполнить `./install.sh`.

---

## Быстрый старт

```bash
git clone https://github.com/Kizuchann/kizurium-translator.git
cd kizurium-translator
./install.sh
```

После установки:

```bash
kizurium-translator --toggle
```

Выберите область экрана — Kizurium распознает текст и покажет перевод поверх него.

Проверить окружение:

```bash
kizurium-translator --doctor
```

---

## Arch Linux

### Установка

Рекомендуемый способ:

```bash
git clone https://github.com/Kizuchann/kizurium-translator.git
cd kizurium-translator
./install.sh
```

Установщик открывает меню с основными действиями:

- установка;
- удаление;
- полное удаление (`purge`);
- очистка кэша;
- диагностика (`doctor`).

В меню установки можно выбрать дополнительные компоненты с помощью `↑` / `↓`, цифр, `Space` и `Enter`.

По умолчанию устанавливаются основные пакеты и **Meiki**. Офлайн-паки можно установить позже; они занимают примерно **160 МБ**.

Чтобы не останавливать установку вопросами:

```bash
./install.sh --yes
```

### Офлайн-паки

Установить все доступные паки после основной установки:

```bash
kizurium-translator --pack-install all
```

---

## Удаление

Удалить приложение через установленную команду:

```bash
kizurium-translator --uninstall
```

Удаление спросит, нужно ли удалять пользовательские данные.

Полностью удалить конфигурацию, кэш, логи и офлайн-модели:

```bash
kizurium-translator --uninstall --purge
```

Только очистить кэш переводов и логи, оставив приложение установленным:

```bash
./install.sh --clean-cache
```

Те же действия доступны через установленную команду:

```bash
kizurium-translator --clean-cache
```

### Репозиторий после установки можно удалить

`./install.sh` не запускает Kizurium из каталога клона.

Команда устанавливается в пользовательское окружение, поэтому после установки репозиторий можно:

- переименовать;
- перенести;
- удалить.

Установленная команда продолжит работать.

Чтобы обновить приложение из нового каталога:

```bash
./install.sh
```

---

## Где находится команда

По умолчанию команда устанавливается в:

```text
~/.local/bin/kizurium-translator
```

Если `kizurium-translator` не находится после установки, запустите новый shell или проверьте путь напрямую:

```bash
~/.local/bin/kizurium-translator --doctor
```

Если `~/.local/bin` не находится в `PATH`, добавьте его в конфигурацию вашего shell.

---

## Установка через `makepkg`

Для пользователей, которым нужен непосредственно Arch-пакет:

```bash
git clone https://github.com/Kizuchann/kizurium-translator.git
cd kizurium-translator
makepkg -si
```

Удаление установленного пакета:

```bash
sudo pacman -Rns kizurium-translator
```

---

## Nix / NixOS

В репозитории уже есть Flake.

### Проверить окружение

```bash
nix run .# -- --doctor
```

### Установить профиль

```bash
nix profile add .#kizurium-translator
```

### Подключить репозиторий как Flake input

```nix
{
  inputs.kizurium.url = "github:Kizuchann/kizurium-translator";

  # ...
}
```

### NixOS module

Kizurium предоставляет NixOS module:

```nix
{
  inputs.kizurium.url = "github:Kizuchann/kizurium-translator";

  outputs = { nixpkgs, kizurium, ... }: {
    nixosConfigurations.myhost = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";

      modules = [
        kizurium.nixosModules.default

        {
          programs.kizurium-translator.enable = true;
        }
      ];
    };
  };
}
```

Модуль добавляет необходимые зависимости в `systemPackages`, включая компоненты для:

```text
grim
slurp
wl-clipboard
tesseract
quickshell
gtk4
gtk4-layer-shell
aria2
```

### Использовать другую версию или собственный пакет

Пакет можно переопределить:

```nix
{
  programs.kizurium-translator = {
    enable = true;
    package = pkgs.callPackage ./path/to/package.nix { };
  };
}
```

После установки офлайн-паки можно добавить отдельно:

```bash
kizurium-translator --pack-install all
```

---

## Зависимости

Kizurium использует системные Wayland/GTK-компоненты и Python-зависимости.

Основные зависимости:

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

На Arch рекомендуется использовать `./install.sh`, чтобы не устанавливать их по одному вручную.

---

## Горячие клавиши

Kizurium не навязывает глобальные сочетания клавиш: их удобно назначить средствами самого композитора.

### Hyprland — `hyprland.conf`

```ini
bind = SUPER_SHIFT, T, exec, kizurium-translator --toggle
```

### Hyprland — Lua-конфигурации

```lua
hl.bind(
    "SUPER + SHIFT + T",
    hl.dsp.exec_cmd("kizurium-translator --toggle")
)
```

### Niri

```kdl
Mod+Shift+T {
    spawn "kizurium-translator" "--toggle"
}
```

### MangoWC

```text
bind=SUPER+SHIFT,t,spawn,kizurium-translator --toggle
```

### Sway

```text
bindsym $mod+Shift+t exec kizurium-translator --toggle
```

Чтобы получить подсказку именно для текущего окружения:

```bash
kizurium-translator --doctor
```

---

## Несколько мониторов

Для выбора области Kizurium использует экранный селектор.

При необходимости область можно задать вручную:

```bash
kizurium-translator --region "X,Y WxH"
```

Или выбрать конкретный выход:

```bash
kizurium-translator --output
```

Конкретный синтаксис зависит от текущего режима запуска и конфигурации.

---

## Живой перевод

Запустить Live Translation для области:

```bash
kizurium-translator --live -g 0,0 1920x1080
```

Где:

```text
-g 0,0 1920x1080
│  │   │
│  │   └─ размер области: 1920×1080
│  └───── координата X/Y начала области: 0,0
└──────── геометрия области
```

Для обычного одноразового перевода вместо этого используйте:

```bash
kizurium-translator --toggle
```

---

## Офлайн-режим

Чтобы запретить использование сетевых translation backends:

```bash
kizurium-translator --offline-only
```

Это удобно, когда нужен полностью локальный сценарий работы.

Подробнее о том, какие данные могут покидать систему:

[`trust.md`](trust.md)

---

## Ручная установка на других дистрибутивах

Если для вашего дистрибутива нет готового пакета, установите системные зависимости самостоятельно, затем создайте окружение проекта:

```bash
uv venv --system-site-packages
uv sync --extra rapid --extra local-mt
```

После этого убедитесь, что доступны:

```bash
grim --version
slurp --version
tesseract --version
```

И проверьте Kizurium:

```bash
kizurium-translator --doctor
```

Результат `--doctor` поможет определить, какой компонент отсутствует или не настроен.

---

## Если что-то не работает

### `uv: command not found`

На Arch:

```bash
sudo pacman -S uv
```

Либо разрешите установщику добавить отсутствующий компонент.

### `kizurium-translator: command not found`

Откройте новый shell и проверьте:

```bash
~/.local/bin/kizurium-translator --doctor
```

Если эта команда работает, добавьте `~/.local/bin` в `PATH`.

### `No module named 'gi'`

Повторно запустите установку:

```bash
./install.sh
```

Для ручной установки убедитесь, что установлены `python-gobject` и связанные GTK-зависимости.

### Quickshell не найден

Установите его средствами вашего дистрибутива.

На Arch:

```bash
sudo pacman -S quickshell
```

Для режимов, которым он не требуется, можно использовать ручной `--region` или `--output`.

### Wayland не найден

Kizurium предназначен для Wayland-сессии.

Проверьте окружение:

```bash
echo "$XDG_SESSION_TYPE"
```

Ожидаемое значение:

```text
wayland
```

### Диагностика

При любой неоднозначной проблеме начните с:

```bash
kizurium-translator --doctor
```

---

## Что ставит установщик

`./install.sh` предназначен не только для первого запуска: через него можно управлять установкой и обслуживанием Kizurium.

```text
install
├─ основные пакеты
├─ Meiki
└─ offline packs (опционально)

maintenance
├─ uninstall
├─ purge
└─ clean-cache

diagnostics
└─ doctor
```

Офлайн-паки можно не устанавливать сразу и добавить позже:

```bash
kizurium-translator --pack-install all
```

---

## Дополнительно

Приватность и сетевые запросы:

[`trust.md`](trust.md)

Основной README:

[`../README.md`](../README.md)

Исходный код и Issues:

[GitHub](https://github.com/Kizuchann/kizurium-translator)
