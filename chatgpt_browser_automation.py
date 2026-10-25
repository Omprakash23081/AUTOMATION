"""Compatibility entry point for ChatGPT browser automation."""
import sys

from backend import chatgpt_browser_automation as _module

if __name__ == "__main__":
    _module.main()
else:
    sys.modules[__name__] = _module
