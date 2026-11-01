# Сеть и данные

Kizurium не содержит встроенной телеметрии и аналитики, не содержит API-ключей и
не отправляет снимки экрана ни одному онлайн-провайдеру. Наружу уходит только
текст распознанных строк и только туда, куда его направляет выбранный backend.

Анонимности Kizurium не обещает: любой сетевой сервис видит IP-адрес и метаданные
запроса, а провайдер перевода получает текст.

## Что остаётся на машине

- Захват области, предобработка и OCR (RapidOCR, Tesseract, Meiki, PaddleOCR)
  выполняются локально.
- Локальный перевод выполняют CTranslate2 с OPUS-MT паками и Bergamot. Для них
  нужны установленные модели и runtime.
- Кэш переводов (SQLite), translation memory, словари, конфигурация и логи
  лежат в каталогах XDG:
  `~/.config/kizurium-translator`, `~/.cache`, `~/.local/share`, `~/.local/state`.
- Установщик не меняет конфигурацию композитора; он печатает пример бинда.

## Что уходит в сеть

| Когда | Куда | Что передаётся |
|---|---|---|
| Перевод при `provider = "google"` (по умолчанию, `use_gtx = true`) | `translate.googleapis.com`, неофициальный endpoint gtx, без ключа | текст строки |
| `provider = "yandex"` | `translate.api.cloud.yandex.net` | текст строки и ключ API |
| `provider = "deepl"` | `api.deepl.com` или `api-free.deepl.com` | текст строки и ключ API |
| `provider = "azure"` | `api.cognitive.microsofttranslator.com` или endpoint из настроек | текст строки, ключ API и регион |
| `provider = "custom"` | адрес, который задал пользователь | текст строки |
| `allow_slow_translation = true` | MyMemory и Google через `deep-translator` | текст строки |
| Установка и обновление офлайн-паков (`--pack-install`) | `huggingface.co` или `hf-mirror.com` (`HF_ENDPOINT`) | запрос файла модели |
| Установка моделей Bergamot (`--bergamot-install`) | каталог `translatelocally.com`, файлы `data.statmt.org`, Remote Settings Mozilla (`firefox.settings.services.mozilla.com`) | запрос каталога и файла модели |
| Подготовка runtime Bergamot (`--bergamot-runtime-install`) | PyPI через `uv` | запрос пакета |
| Установка через `install.sh` | зеркала pacman, PyPI, `files.pythonhosted.org` (RapidOCR, ONNX Runtime в `PKGBUILD`) | запрос пакетов |

Перед отправкой текст проходит локальную подготовку: имена собственные и пути
заменяются метками, а знаки препинания сохраняются отдельным скелетом. Провайдер
получает уже эту подготовленную строку. Условия хранения и обработки данных
определяет сам провайдер, а не Kizurium.

Строки из локального кэша и из локального пака сетевой запрос не вызывают.

## Режимы

### Обычный

По умолчанию перевод идёт через Google gtx. Сначала проверяются кэш, словарь и
локальный CT2-пак, если он установлен. Онлайн-провайдер вызывается только для
строк, которых нет в кэше и в локальных источниках.

При 429 gtx уходит в паузу на `gtx_cooldown_s` секунд.

### Строгий офлайн

```bash
kizurium-translator --offline-only
```

или в конфигурации:

```toml
[translation]
offline_only = true
```

В этом режиме HTTP-клиенты для онлайн-провайдеров не создаются, даже если
`use_gtx = true`. Если локальной модели для пары нет, строка не переводится и в
журнал пишется `language pack not installed`. Переход на сеть не происходит.

Bergamot в строгом офлайн-режиме допустим: он работает локально, если установлены
runtime и модель.

Строгий режим не отменяет разовые загрузки: установка паков, моделей и runtime
по-прежнему требует сети. Эти загрузки выполняются только командами, которые вы
запускаете явно.

## Ключи API

Ключи провайдеров хранятся в Secret Service или KWallet через `keyring`.
Установщик не сохраняет ключи в `config.toml`. Если ни одна из этих служб не
доступна, Kizurium не сохраняет ключ и выводит ошибку. В этом случае задайте
переменную окружения `KIZURIUM_<PROVIDER>_API_KEY`, например
`KIZURIUM_DEEPL_API_KEY`.

## Удаление и очистка

```bash
./install.sh --uninstall                 # интерактивно спросит про конфиг, кэш и модели
./install.sh --uninstall --purge         # конфиг, кэш, логи и офлайн-модели
./install.sh --clean-cache               # кэш переводов и логи, приложение остаётся
kizurium-translator --uninstall --purge  # если папки репозитория уже нет
```

`--clean-cache` доступен только в `install.sh`. Установленная команда
`kizurium-translator` его не принимает.

Системные пакеты (grim, tesseract и другие) остаются: их мог поставить не только
этот проект.

Установка: [install.md](install.md). Офлайн-паки и Bergamot:
[language-packs.md](language-packs.md).
