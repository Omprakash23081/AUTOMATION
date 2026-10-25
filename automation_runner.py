"""Compatibility entry point for the Cutroom automation runner."""
import sys

from backend import automation_runner as _module

if __name__ == "__main__":
    _module.main()
else:
    sys.modules[__name__] = _module
