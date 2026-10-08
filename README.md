<div align="center">

<a href="README.md"><img src="https://img.shields.io/badge/ЯЗЫК-русский-cba6f7?style=for-the-badge&logo=readme&logoColor=white" alt="Русский"></a>
<a href="README.en.md"><img src="https://img.shields.io/badge/lang-English-b4befe?style=for-the-badge&logo=readme&logoColor=white" alt="English"></a>

<br><br>

# Kizurium Translator

**Wayland-native OCR, screen translation and real-time translation overlay for Linux**

<p>
  Designed for <strong>Wayland</strong>, with support for <strong>Hyprland</strong>,
  <strong>Arch Linux</strong> / Arch-based systems and <strong>Nix/NixOS</strong>.
</p>

<br>

<a href="#-скриншоты-"><kbd>&nbsp;&nbsp;<br>СКРИНШОТЫ<br>&nbsp;&nbsp;</kbd></a>&ensp;
<a href="#-быстрый-старт-"><kbd>&nbsp;&nbsp;<br>ЗАПУСК<br>&nbsp;&nbsp;</kbd></a>&ensp;
<a href="#-возможности-"><kbd>&nbsp;&nbsp;<br>ВОЗМОЖНОСТИ<br>&nbsp;&nbsp;</kbd></a>&ensp;
<a href="docs/trust.md"><kbd>&nbsp;&nbsp;<br>ПРИВАТНОСТЬ<br>&nbsp;&nbsp;</kbd></a>&ensp;
<a href="#-документация-"><kbd>&nbsp;&nbsp;<br>ДОКУМЕНТАЦИЯ<br>&nbsp;&nbsp;</kbd></a>&ensp;
<a href="https://github.com/Kizuchann/kizurium-translator/issues"><kbd>&nbsp;&nbsp;<br>ISSUES<br>&nbsp;&nbsp;</kbd></a>

<br><br>

<img src="https://img.shields.io/github/stars/Kizuchann/kizurium-translator?style=for-the-badge&color=cba6f7&label=stars" alt="stars">
<img src="https://img.shields.io/github/forks/Kizuchann/kizurium-translator?style=for-the-badge&color=b4befe&label=forks" alt="forks">
<img src="https://img.shields.io/badge/license-AGPL--3.0-cba6f7?style=for-the-badge" alt="license">
<img src="https://img.shields.io/badge/platform-Wayland%20%7C%20Linux-b4befe?style=for-the-badge" alt="platform">

</div>

<br>

## • что такое Kizurium •

**Kizurium Translator** — экранный OCR и переводчик для Linux на **Wayland**.

Выбираешь область экрана — Kizurium распознаёт текст, переводит его и показывает результат поверх исходного содержимого. Перевод выводится отдельным **Wayland overlay**, поэтому приложению под ним не нужно отдавать управление окном.

Для постоянного перевода есть **Live Translation**: Kizurium периодически анализирует указанную область экрана, отслеживает изменившийся текст и обновляет перевод поверх него.

Проект рассчитан на обычную работу в Wayland-средах и отдельно поддерживает **Hyprland**. Для установки предусмотрены **Arch / Arch-based системы** и **Nix/NixOS**.

## • возможности •

### OCR

Распознаёт текст в выбранной области экрана и возвращает исходный текст.

### Перевод

Распознаёт текст, переводит его между заданными языками и отображает результат поверх оригинала.

### Live Translation

Непрерывно распознаёт меняющийся текст в выбранной области и обновляет перевод без необходимости заново делать скриншот вручную.

### Игры и визуальные новеллы

Подходит для диалогов, интерфейсов, субтитров и другого текста, который появляется прямо на экране. Игровые профили и словари подключаются отдельно и не требуются для базового режима.

### Локальная работа

Для офлайн-сценариев предусмотрены локальные OCR-компоненты и режим `--offline-only`.

---

## • скриншоты •

<div align="center">

### Меню

<img src="assets/screenshots/menu-ru.png" width="48%" alt="Kizurium — меню, перевод">
&nbsp;
<img src="assets/screenshots/menu-en.png" width="48%" alt="Kizurium — меню, оригинал">

</div>

<br>

<div align="center">

### Диалог

<img src="assets/screenshots/dialogue-ru.png" width="48%" alt="Kizurium — диалог, перевод">
&nbsp;
<img src="assets/screenshots/dialogue-en.png" width="48%" alt="Kizurium — диалог, оригинал">

</div>

<br>

<div align="center">

**Перевод отображается непосредственно поверх содержимого экрана.**

</div>

---

## • быстрый старт •

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

### Ручная установка

```bash
git clone https://github.com/Kizuchann/kizurium-translator.git
cd kizurium-translator
./install.sh
```

Полная инструкция:

[`docs/install.md`](docs/install.md)

### Первый запуск

Выделить область экрана и запустить перевод:

```bash
kizurium-translator --toggle
```

Запустить живой перевод области:

```bash
kizurium-translator --live -g 0,0 1920x1080
```

---

## • команды •

| Команда | Что делает |
|---|---|
| `--toggle` | выбрать область мышкой и запустить перевод |
| `--live -g 0,0 1920x1080` | живой перевод указанной области |
| `--ocr-copy` | распознать область и скопировать текст в буфер обмена |
| `--text` | открыть текстовый переводчик |
| `--stop` | остановить текущую сессию |
| `--status` | показать состояние сессии |
| `--doctor` | проверить окружение и зависимости |
| `--profile ID` | включить игровой профиль |
| `--offline-only` | запретить сетевые бэкенды |
| `--pack-install ID` | установить офлайн-пак |

---

## • установка и зависимости •

Arch-пакет включает необходимые компоненты для OCR и работы Kizurium. В ручной установке используются системные зависимости Wayland/GTK и Python-пакеты проекта.

<details>
<summary>Основные зависимости</summary>

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

## • приватность •

Kizurium не использует телеметрию и не содержит встроенных API-ключей.

**Снимки экрана не отправляются в сеть сами по себе.**

При использовании сетевого переводчика наружу уходит только текст, выбранный для перевода, и только при соответствующей настройке backend.

Подробнее:

[`docs/trust.md`](docs/trust.md)

Для локального режима:

```bash
kizurium-translator --offline-only
```

---

## • словари и профили •

Собственный словарь имеет приоритет над профилем, игровым пакетом и общим словарём.

Импорт:

```bash
kizurium-translator --import-dictionary ~/my-dict.tsv
```

Порядок приоритета:

```text
ваш словарь
    ↓
профиль
    ↓
игровой пакет
    ↓
общий словарь
```

Игровые термины подключаются через `--profile` и остаются **opt-in**.

---

## • документация •

| Документ | Содержание |
|---|---|
| [`docs/install.md`](docs/install.md) | установка, запуск и горячие клавиши |
| [`docs/trust.md`](docs/trust.md) | приватность и сетевые запросы |
| [`docs/layout-families.md`](docs/layout-families.md) | определение структуры текста и реплик |
| [`docs/external-dictionaries.md`](docs/external-dictionaries.md) | форматы внешних словарей |
| [`docs/language-packs.md`](docs/language-packs.md) | офлайн-паки |
| [`docs/licenses.md`](docs/licenses.md) | лицензии компонентов |

---

## • разработка •

Запустить тесты:

```bash
uv run pytest -q
```

Проверить код:

```bash
uv run ruff check src/
```

---

## • известные ограничения •

- Пол говорящего определяется по форме `Имя: реплика`. Если имени нет в кадре, автоматически определить его нельзя.
- Текст внутри сложных элементов интерфейса может быть ошибочно распознан. Профилем можно пометить такие области как `не текст`.
- Наклон и жирность оцениваются по структуре чернил; для текста с высотой менее 34 px измерение наклона не выполняется.

---

<div align="center">

**Kizurium Translator** · **Wayland** · **Hyprland** · **Linux** · **Arch Linux** · **Nix/NixOS** · **OCR** · **Screen Translation** · **Live Translation**

<br><br>

**AGPL-3.0-or-later** · [LICENSE](LICENSE) · [NOTICE](NOTICE)

<br>

<sub>Скриншоты — из игр их правообладателей и используются только для демонстрации работы переводчика.</sub>

</div>
