<div align="center">

<a href="README.md"><img src="https://img.shields.io/badge/ЯЗЫК-русский-cba6f7?style=for-the-badge&logo=readme&logoColor=white" alt="Русский"></a>
<a href="README.en.md"><img src="https://img.shields.io/badge/lang-English-b4befe?style=for-the-badge&logo=readme&logoColor=white" alt="English"></a>

<br>

<h3>Kizurium Translator</h3>

<br>

<a href="#-скриншоты-"><kbd>&nbsp;&nbsp;<br>SCREENSHOTS<br>&nbsp;&nbsp;</kbd></a>&ensp;&ensp;
<a href="#-установка-"><kbd>&nbsp;&nbsp;<br>INSTALL<br>&nbsp;&nbsp;</kbd></a>&ensp;&ensp;
<a href="docs/trust.md"><kbd>&nbsp;&nbsp;<br>PRIVACY<br>&nbsp;&nbsp;</kbd></a>&ensp;&ensp;
<a href="#-документация-"><kbd>&nbsp;&nbsp;<br>DOCS<br>&nbsp;&nbsp;</kbd></a>&ensp;&ensp;
<a href="https://github.com/Kizuchann/kizurium-translator/issues"><kbd>&nbsp;&nbsp;<br>ISSUES<br>&nbsp;&nbsp;</kbd></a>

<br><br>

<img src="https://img.shields.io/github/stars/Kizuchann/kizurium-translator?style=for-the-badge&color=cba6f7&label=stars" alt="stars">
<img src="https://img.shields.io/github/forks/Kizuchann/kizurium-translator?style=for-the-badge&color=b4befe&label=forks" alt="forks">
<img src="https://img.shields.io/badge/license-AGPL--3.0-cba6f7?style=for-the-badge" alt="license">
<img src="https://img.shields.io/badge/platform-Wayland%20%7C%20Linux-b4befe?style=for-the-badge" alt="platform">

</div>

## • описание •

Kizurium — переводчик текста на экране и OCR-инструмент для Wayland.

Выделяешь область, Kizurium распознаёт текст, переводит его и рисует перевод
поверх оригинала. Окно не перехватывается: оверлей живёт отдельным слоем.

Три режима:

- **OCR** — распознать текст выбранной области.
- **Перевод** — перевести текст между языками, исходный и целевой задаются конфигом.
- **Живой перевод** — постоянно распознавать меняющийся текст и показывать
  перевод оверлеем Wayland.

Рассчитан на Wayland. Поддерживает Arch-based дистрибутивы и Nix/NixOS, есть
ручная установка.

## • установка •

**Arch** — `pacman` ставит и снимает:

```bash
git clone https://github.com/Kizuchann/kizurium-translator.git
cd kizurium-translator
makepkg -si
```

`makepkg -si` ставит tesseract. RapidOCR в репозиториях Arch нет — только в
PyPI (27 МБ плюс 21 МБ onnxruntime), поэтому пакет их не тянет: pacman не умеет
ставить из pip, а вшивать wheels в сборку — значит отнимать у неё воспроизводимость.
Нужен RapidOCR — ставь через `./install.sh`, он ставит и его.

**Любой дистрибутив** — установщик кладёт команду в `~/.local/bin`:

```bash
git clone https://github.com/Kizuchann/kizurium-translator.git
cd kizurium-translator
./install.sh
```

**NixOS / Nix** — flakes включены в репозиторий:

```bash
nix profile add github:Kizuchann/kizurium-translator
# или в configuration.nix
#   inputs.kizurium.url = "github:Kizuchann/kizurium-translator";
```

<details>
<summary>Зависимости</summary>

```bash
Pillow  numpy  pytesseract  requests          # pip
python-gobject  python-cairo  gtk4  gtk4-layer-shell   # дистрибутив
tesseract{,-data-eng,-data-jpn,-data-rus}  grim  slurp  quickshell
wl-clipboard  aria2  libnotify
```

</details>

Запуск: выделить область мышкой и получить перевод поверх текста.

```bash
kizurium-translator --toggle
```

## • скриншоты •

<div align="center">

| перевод | оригинал |
|:--:|:--:|
| <img src="assets/screenshots/menu-ru.png" width="49%"> | <img src="assets/screenshots/menu-en.png" width="49%"> |

</div>

<div align="center">

| перевод | оригинал |
|:--:|:--:|
| <img src="assets/screenshots/dialogue-ru.png" width="49%"> | <img src="assets/screenshots/dialogue-en.png" width="49%"> |

</div>

## • установка •

```bash
./install.sh
kizurium-translator --toggle
```

Полная инструкция — [`docs/install.md`](docs/install.md).

<details>
<summary>Зависимости</summary>

```bash
Pillow  numpy  pytesseract  requests          # pip
python-gobject  python-cairo  gtk4  gtk4-layer-shell   # дистрибутив
tesseract{,-data-eng,-data-jpn,-data-rus}  grim  slurp  quickshell
wl-clipboard  aria2  libnotify
```

</details>

<details>
<summary>Команды</summary>

| | |
|---|---|
| `--toggle` | выбрать область мышкой и запустить |
| `--live -g 0,0 1920x1080` | живой перевод области |
| `--ocr-copy` | область → текст в буфер обмена |
| `--text` | окно переводчика текста |
| `--stop` | остановить |
| `--status` | состояние сессии |
| `--doctor` | проверка окружения |
| `--profile ID` | игровой профиль (opt-in) |
| `--offline-only` | запретить сетевые бэкенды |
| `--pack-install ID` | поставить офлайн-пак |

</details>

## • приватность •

Подробнее — [`docs/trust.md`](docs/trust.md).

Снимки экрана никуда не уходят. В сеть уходит только текст, который
переводится, и только если выбран сетевой бэкенд. API-ключей у приложения
нет, телеметрии нет.

## • словари •

Свой словарь важнее модели перевода.

```bash
kizurium-translator --import-dictionary ~/my-dict.tsv
```

Приоритет: ваш → профиль → пак игры → общий. Игровые термины лежат в
opt-in паках, включаются через `--profile`. Без профиля — универсальный режим.

## • документация •

| | |
|---|---|
| [`docs/install.md`](docs/install.md) | установка, горячие клавиши |
| [`docs/trust.md`](docs/trust.md) | что уходит в сеть |
| [`docs/layout-families.md`](docs/layout-families.md) | как отличить реплику от кнопки |
| [`docs/external-dictionaries.md`](docs/external-dictionaries.md) | форматы словарей |
| [`docs/language-packs.md`](docs/language-packs.md) | офлайн-паки |
| [`docs/licenses.md`](docs/licenses.md) | лицензии |

```bash
uv run pytest -q          # 1935 тестов
uv run ruff check src/
```

## • известные ограничения •

- Пол говорящего — по форме `Имя: реплика`. Без имени в кадре не определяется.
- Иероглифы на боссах — профилем можно пометить зону «не текст», автоматически нет.
- Наклон и жирность меряются по чернилам; ниже 34px чернил наклон не меряется.

---

<div align="center">

**AGPL-3.0-or-later** · [LICENSE](LICENSE) · [NOTICE](NOTICE)

<sub>Скриншоты — из игр их правообладателей, как демонстрация работы переводчика.</sub>

</div>