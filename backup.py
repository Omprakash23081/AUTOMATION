"""Compatibility import for Cutroom backup helpers."""
import sys

from backend import backup as _module

if __name__ == "__main__":
    raise SystemExit(_module.main())
else:
    sys.modules[__name__] = _module
