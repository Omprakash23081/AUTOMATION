from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _windows_dpapi(data: bytes, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("The local agent credential store is supported only on Windows.")

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    source_buffer = ctypes.create_string_buffer(data)
    source = _DataBlob(
        len(data),
        ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    target = _DataBlob()
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR) if decrypt else wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    operation.restype = wintypes.BOOL
    if decrypt:
        success = operation(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target))
    else:
        success = operation(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target))
    if not success:
        raise OSError(ctypes.get_last_error(), "Windows could not access the protected local-agent credential.")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        kernel32.LocalFree.restype = wintypes.HLOCAL
        kernel32.LocalFree(target.pbData)


class CredentialStore:
    def __init__(self, path: Path | None = None):
        local_app_data = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        self.path = path or local_app_data / "Cutroom" / "agent-token.dpapi"

    @staticmethod
    def _protect(token: str) -> bytes:
        return _windows_dpapi(token.encode("utf-8"), decrypt=False)

    @staticmethod
    def _unprotect(data: bytes) -> str:
        return _windows_dpapi(data, decrypt=True).decode("utf-8")

    def save(self, token: str) -> None:
        if not token or len(token) > 256:
            raise ValueError("The local-agent token is invalid.")
        encrypted = self._protect(token)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temporary.write_bytes(encrypted)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def load(self) -> str | None:
        if not self.path.exists():
            return None
        token = self._unprotect(self.path.read_bytes())
        if not token or len(token) > 256:
            raise RuntimeError("The saved local-agent credential is invalid; pair this computer again.")
        return token

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)
