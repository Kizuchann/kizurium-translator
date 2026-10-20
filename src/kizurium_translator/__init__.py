"""Kizurium Translator — live screen translation for Wayland.

Standalone app: does not read or modify compositor or Quickshell configs.
"""

from pathlib import Path as _Path
import sys as _sys

# The Arch package keeps Python wheels missing from some derivative repos in a
# private directory. Append after system site-packages so distro modules win
# when installed, while the package remains self-contained on CachyOS.
_vendor = _Path(__file__).with_name("_vendor")
if _vendor.is_dir() and str(_vendor) not in _sys.path:
    _sys.path.append(str(_vendor))
del _Path, _sys, _vendor

__version__ = "1.3.5"

APP_ID = "ru.kizurium.translator"

__all__ = ["__version__", "APP_ID"]
