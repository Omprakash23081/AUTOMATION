"""Compatibility entry point for the Cutroom Flask backend."""
import sys

from backend import server as _server

if __name__ == "__main__":
    _server.main()
else:
    sys.modules[__name__] = _server
