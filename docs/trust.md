# Сеть и данные

## Локально

- Снимок выбранной области остаётся в памяти процесса.
- OCR выполняется на машине.
- Кэш, словари, модели и логи — в каталогах XDG
  (`~/.config`, `~/.cache`, `~/.local/share`, `~/.local/state`).
- Установщик не меняет конфиги композитора; в конце печатает пример бинда.

## Сеть

При промахе кэша распознанный текст отправляется на
`translate.googleapis.com` (gtx, без API-ключа).

При `allow_slow_translation = true` возможен MyMemory.

Language packs скачиваются с Hugging Face при установке или по
`--pack-install`.

Аналитики и телеметрии нет.

## Локальный перевод без онлайна

```toml
[translation]
offline_only = true
use_gtx = false
```

Нужны packs: `kizurium-translator --pack-install all`.

## Удаление

```bash
./install.sh --uninstall                 # спросит про конфиг/кэш/модели
./install.sh --uninstall --purge         # конфиг + кэш + логи + offline-модели
./install.sh --clean-cache               # только кэш переводов и логи
kizurium-translator --uninstall --purge  # если папки репозитория уже нет
```

Системные пакеты (grim, tesseract, …) остаются — их мог поставить не только
этот проект.

Установка: [install.md](install.md).
