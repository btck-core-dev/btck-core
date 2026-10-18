"""Restrict local wallet files to the current OS account before writing data."""

import os
from pathlib import Path
import re


def is_private_path(path: Path) -> bool:
    """Check OS access controls without silently changing an imported file."""
    if os.name != "nt":
        return not (path.stat().st_mode & 0o077)
    import ctypes
    from ctypes import wintypes
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                          ctypes.c_void_p, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p)]
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p]
    token = wintypes.HANDLE()
    sid_text, sddl = wintypes.LPWSTR(), wintypes.LPWSTR()
    security = ctypes.c_void_p()
    try:
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise ctypes.WinError(ctypes.get_last_error())
        result = advapi.GetNamedSecurityInfoW(str(path.resolve()), 1, 4,
                                              None, None, None, None, ctypes.byref(security))
        if result:
            raise ctypes.WinError(result)
        if not advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            security, 1, 4, ctypes.byref(sddl), None
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        entries = re.findall(r"\(([^()]*)\)", sddl.value)
        if not entries:
            return False
        allowed = {sid_text.value, "SY", "BA", "S-1-5-18", "S-1-5-32-544"}
        for entry in entries:
            fields = entry.split(";")
            if len(fields) != 6 or fields[0] not in {"A", "D"}:
                return False
            if fields[0] == "A" and fields[5] not in allowed:
                return False
        return True
    finally:
        for allocation in (sid_text, sddl):
            if allocation:
                kernel.LocalFree(ctypes.cast(allocation, ctypes.c_void_p))
        if security:
            kernel.LocalFree(security)
        if token:
            kernel.CloseHandle(token)


def restrict_private_path(path: Path, *, directory=False, descriptor=None) -> None:
    if os.name != "nt":
        if descriptor is not None:
            os.fchmod(descriptor, 0o700 if directory else 0o600)
        else:
            path.chmod(0o700 if directory else 0o600)
        return

    # Windows chmod only changes the read-only flag. Use a protected NTFS DACL
    # instead: the current user and SYSTEM, without inherited broad grants.
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                          ctypes.c_void_p, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p,
                                             ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD)]
    advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                       ctypes.c_void_p]
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    sid_text = wintypes.LPWSTR()
    security = ctypes.c_void_p()
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise ctypes.WinError(ctypes.get_last_error())
        inherit = "OICI" if directory else ""
        sddl = f"D:P(A;{inherit};FA;;;SY)(A;{inherit};FA;;;{sid_text.value})"
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(security), None
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if not advapi.SetFileSecurityW(str(path.resolve()), 0x80000004, security):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        if security:
            kernel.LocalFree(security)
        if sid_text:
            kernel.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))
        kernel.CloseHandle(token)
