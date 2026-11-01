# Офлайн-перевод и языковые паки

Kizurium умеет переводить без сетевого backend, если установлен runtime **CTranslate2** (`local-mt`) и для нужной языковой пары есть локальный **CT2 pack**.

Готовые паки устанавливаются из каталога проекта. Вручную скачивать и конвертировать OPUS-MT в CT2 для обычного использования не требуется.

---

## Быстрая установка

После клонирования репозитория запустите:

```bash
./install.sh
```

Обычный `./install.sh` в Arch-режиме ставит пакет и **не** добавляет CT2-паки:
установщик предлагает только Bergamot. В Nix-ветке можно скачать модели, но
runtime `ctranslate2` всё равно не входит в пакет. Чтобы включить офлайн-перевод
в Arch, используйте пользовательский режим (`--user`) и выберите паки в списке
либо запустите установку без вопросов:

```bash
./install.sh --user --yes --offline-packs
```

В Arch-режиме флаг `--offline-packs` требует `--user`: он добавляет extra
`local-mt` (runtime CTranslate2) и скачивает модели. Без `--user` команда
`--pack-install` только скачивает модели, но не ставит runtime; для локального
перевода используйте `./install.sh --user --offline-packs`.

Модели паков можно скачать и позже (runtime `local-mt` при этом всё равно нужен):

```bash
kizurium-translator --pack-install all
```

Посмотреть, что уже установлено:

```bash
kizurium-translator --packs
```

---

## Где хранятся модели

Модели находятся в пользовательском каталоге данных Kizurium:

```text
$XDG_DATA_HOME/kizurium-translator/models/<id>/
```

Если `XDG_DATA_HOME` не задан, используется стандартный пользовательский каталог данных.

Каждый pack хранится в собственном каталоге, например:

```text
models/opus-mt-en-ru/
models/opus-mt-ja-ru/
```

---

## Что входит в каталог паков

Первый набор локальных моделей использует **pair-specific OPUS-MT** для конкретных направлений перевода.

Сейчас в каталоге два пака:

```text
opus-mt-en-ru
opus-mt-ja-ru
```

Это означает, что для каждой пары используется отдельная модель, а не одна универсальная модель на все языки.

Посмотреть текущий каталог и состояние паков:

```bash
kizurium-translator --packs
```

Для каждого слота будет показано одно из трёх состояний:

```text
Installed (<version>)
Download (one command)
Download (local --from)
```

`Installed` означает, что файлы модели уже лежат в каталоге данных. Наличие
runtime CTranslate2 проверяют `--model-status` и `--doctor`: модели и runtime
устанавливаются отдельно. Если pack уже установлен, повторно скачивать его не
нужно.

---

## Строгий офлайн-режим

Чтобы Kizurium вообще не использовал сетевые переводчики, включите:

```toml
# ~/.config/kizurium-translator/config.toml

[translation]
offline_only = true
```

или запускайте отдельную команду с:

```bash
kizurium-translator --offline-only ...
```

В этом режиме сетевые backend'ы не используются. В частности, не создаются HTTP-сессии для удалённого перевода и не происходит скрытого перехода на сеть, если локального перевода не хватает.

При этом продолжают работать локальные данные Kizurium:

- установленные CT2 packs — если установлен runtime `local-mt`;
- словари;
- translation memory;
- кэш.

### Что происходит, если нужного pack нет

Kizurium не будет молча уходить в интернет.

При отсутствии нужной локальной модели:

```text
language pack not installed
```

и перевод для этого запроса не возвращается.

Это удобно для действительно автономного режима: можно заранее поставить нужные модели и быть уверенным, что после запуска приложение не начнёт отправлять текст в сеть.

---

## Установка конкретного pack

Установить все доступные паки:

```bash
kizurium-translator --pack-install all
```

Для отдельного pack можно указать его ID:

```bash
kizurium-translator --pack-install opus-mt-en-ru
```

или:

```bash
kizurium-translator --pack-install opus-mt-ja-ru
```

После установки снова проверьте состояние:

```bash
kizurium-translator --packs
```

---

## Обычный пользовательский путь

Для большинства пользователей достаточно следующей последовательности:

```bash
git clone https://github.com/Kizuchann/kizurium-translator.git
cd kizurium-translator
./install.sh
```

Позже, когда понадобится локальный перевод, помните, что `--pack-install all`
скачивает только модели. Для самого перевода нужен runtime `local-mt` — в Arch:
`./install.sh --user --offline-packs` (или Bergamot).

Проверить установку:

```bash
kizurium-translator --packs
```

Включить строгий офлайн-режим:

```bash
kizurium-translator --offline-only
```

Никакой ручной конвертации моделей в CT2 для этого сценария не требуется.

---

## Если у вас уже есть CT2-модель

Ручной режим нужен в основном разработчикам или тем, у кого уже есть собственный локальный CT2-каталог.

Установите optional dependency:

```bash
pip install 'kizurium-translator[local-mt]'
```

Затем передайте каталог модели:

```bash
kizurium-translator \
  --pack-install opus-mt-en-ru \
  --from /path/to/ct2-model
```

Здесь `/path/to/ct2-model` — уже готовый каталог CTranslate2.

Kizurium не требует от вас самостоятельно выполнять конвертацию OPUS-MT → CT2, если используется официальный каталог паков.

---

## Bergamot

Bergamot — второй локальный движок. Его runtime и модели устанавливаются отдельно
от CT2-паков и не входят в `--pack-install`.

```bash
kizurium-translator --model-status                  # состояние runtime, моделей и паков
kizurium-translator --bergamot-runtime-install      # собственный Python 3.10 и wheel bergamot (нужны uv и сеть)
kizurium-translator --bergamot-install en-ru        # модель из репозитория Mozilla, проверка sha256
kizurium-translator --bergamot-remove en-ru         # удалить модель из кэша Kizurium
kizurium-translator --bergamot-runtime-remove       # удалить окружение runtime, модели остаются
```

Чтобы использовать Bergamot как провайдер:

```toml
[translation]
provider = "bergamot"
```

Файлы моделей Firefox команды не трогают. Если Bergamot выбран, а runtime или модель
не установлены, строка не переводится; онлайн-провайдер вместо него не подставляется.

Полный список сетевых загрузок: [`trust.md`](trust.md).

---

## OPUS-MT и лицензии

Каталог локальных паков использует модели **OPUS-MT**.

Для используемых OPUS-MT моделей применяется лицензия **CC-BY-4.0**.

Подробности по лицензиям компонентов:

[`licenses.md`](licenses.md)

Инвентарь лицензий:

[`licenses-inventory.toml`](licenses-inventory.toml)

---

## Почему не NLLB

**NLLB не является catalog pack в первом релизе Kizurium.**

Основной локальный путь сейчас построен вокруг pair-specific **OPUS-MT** моделей.

Это сделано специально: Kizurium не пытается включить одну большую универсальную модель в стандартный набор паков, а использует отдельные модели для конкретных языковых направлений.

Поэтому список доступных локальных переводов определяется именно каталогом Kizurium.

---

## Полезные команды

```bash
# Показать состояние паков
kizurium-translator --packs

# Установить все паки
kizurium-translator --pack-install all

# Установить один pack
kizurium-translator --pack-install opus-mt-en-ru

# Запретить сетевые backend'ы
kizurium-translator --offline-only

# Состояние runtime, моделей и паков
kizurium-translator --model-status

# Проверить окружение
kizurium-translator --doctor
```

---

## Связанные разделы

Установка и настройка:

[`install.md`](install.md)

Приватность и сетевые запросы:

[`trust.md`](trust.md)

Лицензии:

[`licenses.md`](licenses.md)
