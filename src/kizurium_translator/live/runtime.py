"""Настройки разбора кадра и перевода, а не глобальные переменные.

Зачем это отдельным объектом.

Настройки разъехались по модулям при разрезе `live.py` на слои: детектор читает
пороги из одного слоя, перевод - из другого, оверлей - из третьего. У каждого
слоя своя копия, и `configure()` писал в одну из них, а остальные продолжали
видеть исходную. Настройка применялась и не действовала - и это молча, потому
что расхождение значений не падает, а просто меняет порог.

Здесь состояние лежит один раз, в объекте. Слои получают его по ссылке, а не
по отдельной переменной, поэтому правка видна всем сразу и находится в одном
месте.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .. import fonts as fonts_mod
from .. import translate as translate_mod
from ..config import Config
from ..core import scripts, text
from ..core.text import (  # noqa: F401
    tlog,
)
from ..paths import (  # noqa: F401
    Paths,
    default_paths,
)
from ..threads import budget
from ..translation import service, user_glossary

_ALIASES = {
    "INTERVAL": "interval",
    "INTERVAL_SUB": "interval_sub",
    "INTERVAL_UI_STABLE": "interval_ui_stable",
    "CHANGE_MEAN": "change_mean",
    "CHANGE_MEAN_VIDEOISH": "change_mean_videoish",
    "CHANGE_TILE": "change_tile",
    "CHANGE_CENTER": "change_center",
    "OCR_KEY_SIM": "ocr_key_sim",
    "INCREMENTAL": "incremental",
}


@dataclass
class LiveSettings:
    """Пороги разбора кадра, заполняется из `Config` один раз при старте."""

    # Повтор кадра
    interval: float = 0.55
    interval_sub: float = 0.28
    interval_ui_stable: float = 4.5
    stable_check_s: float = 8.0
    video_recheck_s: float = 1.2
    subtitle_empty_recheck_s: float = 1.6
    game_recheck_s: float = 1.4
    slow_cycle_s: float = 1.2
    cycle_duty_cap: float = 2.0

    # Детект изменений
    change_mean: float = 10.0
    change_mean_videoish: float = 20.0
    change_tile: float = 2.15
    change_center: float = 1.85
    ocr_key_sim: float = 0.88

    # Захват и распознавание
    hide_for_capture_s: float = 0.10
    hide_for_sub_s: float = 0.10
    probe_side: int = 320
    max_screenshot_side: int = 4672
    incremental: bool = True
    ocr_engines: tuple[str,...] = ("rapid", "meiki", "tesseract")

    # Перевод
    max_translate_chars: int = 4300
    translate_budget: int = 96
    target_lang: str = "ru"

    # Оверлей
    card_outline: bool = True

    #: Имена, которые раньше были модульными переменными `live.py`.
    #: Оставлены как свойства класса: внешний код и тесты обращаются к ним по
    #: имени, а хранится значение одно - в объекте настроек. Свойство на
    #: уровне класса перехватывает и обращение через экземпляр, и через сам
    #: класс, поэтому обе формы работают и не расходятся.
    def __getattr__(self, name: str) -> object:
        field = _ALIASES.get(name)
        if field is None:
            raise AttributeError(name)
        return getattr(self, field)

    @classmethod
    def from_config(cls, cfg: Config) -> LiveSettings:
        """Значения, которых нет в `Config`, остаются собственными дефолтами.

        Разница между «в `Config`» и «здесь» осознанная: `Config` описывает то,
        что человек задаёт в файле, а этот класс - полный набор порогов
        движка. Поле в обоих местах означало бы два источника правды.
        """
        return cls(
            interval=cfg.interval,
            interval_sub=cfg.interval_sub,
            interval_ui_stable=cfg.interval_ui_stable,
            change_mean=cfg.change_mean,
            change_mean_videoish=cfg.change_mean_videoish,
            change_tile=cfg.change_tile,
            change_center=cfg.change_center,
            ocr_key_sim=cfg.ocr_key_sim,
            hide_for_capture_s=cfg.hide_for_capture_s,
            hide_for_sub_s=cfg.hide_for_sub_s,
            probe_side=cfg.probe_side,
            max_screenshot_side=cfg.max_screenshot_side,
            incremental=cfg.incremental,
            ocr_engines=cfg.ocr_engines,
            max_translate_chars=cfg.max_translate_chars,
            target_lang=cfg.target_lang,
            card_outline=cfg.card_outline,
        )


@dataclass
class TranslationSettings:
    """Состояние переводчика: сам переводчик и пул задаются при настройке."""

    target_lang: str = "ru"
    source_lang: str = "auto"
    use_gtx: bool = True
    allow_slow_translation: bool = False
    offline_only: bool = False
    max_translate_chars: int = 4300
    gtx_cooldown_s: float = 25.0
    disabled: bool = False
    translator: object | None = None
    pool: object | None = None

    @classmethod
    def from_config(cls, cfg: Config) -> TranslationSettings:
        return cls(
            target_lang=cfg.target_lang,
            source_lang=cfg.source_lang,
            use_gtx=cfg.use_gtx,
            allow_slow_translation=cfg.allow_slow_translation,
            offline_only=cfg.offline_only,
            max_translate_chars=cfg.max_translate_chars,
            gtx_cooldown_s=cfg.gtx_cooldown_s,
        )


#: Состояние разбора кадра и состояние перевода - по одному экземпляру на
#: процесс. Лежат здесь, а не в `session.py`, потому что пороги нужны
#: детектору изменений и планировщику, а переводчик - слою перевода.
#: Импорт сессии оттуда утащил бы их вверх и вернул цикл, который только что
#: разобрал.

# `configure`, `translator`, `shutdown` - это `Services` из цепочки
# `Config -> Services -> LiveSession`. Живут здесь, а не в `session.py`,
# потому что к переводчику обращается и сборка карточек, а она ниже
# цикла кадра.

def configure(config: Config, paths: Paths | None = None) -> None:
    """Apply configuration to the engine. Called by the CLI, never at import."""

    # The faces the overlay draws with are registered here, before anything can
    # ask for one. Asked for a name the loader cannot resolve, Pango substitutes
    # silently and the card comes out in whatever the machine had - which is how
    # a layout that fits on one machine overflows on another.
    fonts_mod.ensure_registered()

    # profile DATA may override OCR order / timeouts before settings
    # are copied. Activation also toggles the opt-in game glossary.
    from .. import profile as profile_mod

    requested = profile_mod.resolve_requested_id(config_id=config.profile_id)
    active_prof = profile_mod.activate(requested or None)
    config = profile_mod.apply_to_config(config, active_prof)

    SETTINGS.max_translate_chars = config.max_translate_chars
    SETTINGS.target_lang = config.target_lang
    # Язык назначения — одна запись, в `core.active`. Ниже сессии его читают
    # оттуда, а не из модульной переменной `TARGET_LANG`.
    scripts.set_target_language(config.target_lang)
    from ..core import active

    active.apply_frame(config)
    TRANSLATION.target_lang = config.target_lang
    TRANSLATION.source_lang = config.source_lang
    TRANSLATION.use_gtx = config.use_gtx
    TRANSLATION.allow_slow_translation = config.allow_slow_translation
    TRANSLATION.offline_only = config.offline_only
    TRANSLATION.max_translate_chars = config.max_translate_chars
    TRANSLATION.gtx_cooldown_s = config.gtx_cooldown_s
    SETTINGS.interval = config.interval
    SETTINGS.interval_sub = config.interval_sub
    SETTINGS.interval_ui_stable = config.interval_ui_stable
    SETTINGS.hide_for_capture_s = config.hide_for_capture_s
    SETTINGS.hide_for_sub_s = config.hide_for_sub_s
    SETTINGS.change_mean = config.change_mean
    SETTINGS.change_mean_videoish = config.change_mean_videoish
    SETTINGS.change_tile = config.change_tile
    SETTINGS.change_center = config.change_center
    SETTINGS.ocr_key_sim = config.ocr_key_sim
    SETTINGS.probe_side = config.probe_side
    SETTINGS.max_screenshot_side = config.max_screenshot_side
    SETTINGS.card_outline = config.card_outline
    user_glossary.replace(dict(config.glossary))
    if config.glossary:
        service.GLOSSARY.update(config.glossary)

    SETTINGS.incremental = config.incremental
    SETTINGS.ocr_engines = config.ocr_engines

    text.PATHS = paths or default_paths()
    LOCK = text.PATHS.lock
    PIDFILE = text.PATHS.pid
    text.PATHS.ensure()
    # Once per configure: where data lives, before the frame loop starts.
    from ..paths import emit_storage_banner

    emit_storage_banner(text.PATHS)

    if TRANSLATION.translator is not None:
        TRANSLATION.translator.cache_flush(force=True)
    TRANSLATION.translator = translate_mod.Translator(
        target=config.target_lang,
        source=config.source_lang,
        use_gtx=config.use_gtx,
        allow_slow=config.allow_slow_translation,
        offline_only=config.offline_only,
        max_chars=config.max_translate_chars,
        cooldown_s=config.gtx_cooldown_s,
        cache_path=text.PATHS.translate_cache,
        cache_max_entries=config.cache_max_entries,
        glossary=config.glossary,
        log=tlog,
    )
    TRANSLATION.pool.shutdown(wait=False, cancel_futures=True)
    TRANSLATION.pool = ThreadPoolExecutor(max_workers=budget().translation_executor)


def translator() -> translate_mod.Translator:
    if TRANSLATION.translator is None:
        configure(Config())
    assert TRANSLATION.translator is not None
    return TRANSLATION.translator


def shutdown() -> None:
    """Flush the cache and release the pool. Safe to call more than once."""
    if TRANSLATION.translator is not None:
        try:
            TRANSLATION.translator.cache_flush(force=True)
        except Exception:  # noqa: BLE001
            pass
    TRANSLATION.pool.shutdown(wait=False, cancel_futures=True)
SETTINGS = LiveSettings()
TRANSLATION = TranslationSettings()
