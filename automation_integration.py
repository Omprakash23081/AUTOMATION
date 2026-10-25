"""Compatibility import for the Cutroom automation API."""
import sys

from backend import automation_integration as _module

sys.modules[__name__] = _module
