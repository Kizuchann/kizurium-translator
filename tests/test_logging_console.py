"""Console logging must actually reach the terminal.

``RotatingFileHandler`` subclasses ``StreamHandler``, so a naive
``isinstance(..., StreamHandler)`` check used to skip attaching stderr and
left live looking silent while overlay.log filled up.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from kizurium_translator import logging_setup


def test_get_logger_attaches_stderr_alongside_file(tmp_path, capsys):
    log = tmp_path / "overlay.log"
    # Fresh name so a prior test's handlers do not interfere.
    name = f"kizurium-test-overlay-{id(log)}"
    logger = logging_setup.get_logger(name, log, to_stderr=True)
    logger.info("hello-terminal")
    # Second call must keep the console handler (early-return path).
    logger2 = logging_setup.get_logger(name, log, to_stderr=True)
    assert logger2 is logger
    console = [
        h
        for h in logger.handlers
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
    ]
    assert len(console) == 1
    assert console[0].stream is sys.stderr
    assert "hello-terminal" in log.read_text(encoding="utf-8")
    err = capsys.readouterr().err
    assert "hello-terminal" in err
    # Cleanup so other tests do not inherit handlers.
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    logging.Logger.manager.loggerDict.pop(name, None)
