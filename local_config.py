"""Compatibility import for Cutroom local configuration helpers."""
import sys

from backend import local_config as _module

sys.modules[__name__] = _module
