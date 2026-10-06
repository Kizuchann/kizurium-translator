"""Маркер для тестов, которым нужен настоящий хост.

Зачем маркер, а не «они и так падают в песочнице».

Nix-сборка идёт в песочнице, где нет ни `/bin`, ни установленных шрифтов. Часть
тестов проверяет честно именно их: зовёт `/bin/true`, меряет глифы системным
шрифтом, поднимает процессы селектора. В песочнице они падают не из-за кода, а
из-за отсутствующей предпосылки, и без соглашения это неотличимо от настоящей
поломки - сборка просто красная, и разбираться приходится заново каждый раз.

Поэтому предпосылка названа явно:

    uv run pytest -q                   # хост: всё, включая needs_host, проходит
    nix flake check                    # песочница: needs_host пропускается

Пропуск проверяет наличие, а не флажок: на машине, где `/bin/true` есть, тест
выполняется, даже если маркер стоит.
"""

from __future__ import annotations

import os
import shutil

import pytest

#: Что песочница nix не даёт и что нужно этой части тестов.
_HOST_PRECONDITION = "/bin/true"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"needs_host: requires {_HOST_PRECONDITION} and installed system fonts, "
        "which a nix build sandbox has neither of",
    )


def _host_is_complete() -> bool:
    return os.path.isfile(_HOST_PRECONDITION) and shutil.which("true") is not None


@pytest.fixture(autouse=True)
def _needs_host_precondition(request: pytest.FixtureRequest) -> None:
    if "needs_host" not in request.keywords:
        return
    if not _host_is_complete():
        pytest.skip(
            f"нет {_HOST_PRECONDITION} - песочница сборки без корня POSIX"
        )
