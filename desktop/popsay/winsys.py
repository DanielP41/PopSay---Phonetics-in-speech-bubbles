"""Integración con Windows vía ctypes (sin dependencias extra).

- Atajo global (RegisterHotKey).
- Gancho de mouse de bajo nivel para detectar selecciones (arrastre o doble clic).
- Copiar la selección de la app activa (Ctrl+C simulado) restaurando el portapapeles.
- Instancia única, inicio con Windows, tema del sistema, app en primer plano.

En otros sistemas todo degrada a no-op para poder desarrollar y testear.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)
IS_WIN = sys.platform == "win32"

CONSOLE_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "mintty", "PuTTY", "VirtualConsoleClass"}

if IS_WIN:
    import ctypes
    from ctypes import wintypes as W

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    ULONG_PTR = ctypes.c_size_t
    LRESULT = ctypes.c_ssize_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD), ("dwFlags", W.DWORD),
                    ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD), ("time", W.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", W.DWORD), ("wParamL", W.WORD), ("wParamH", W.WORD)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", W.DWORD), ("u", _INPUTUNION)]

    class MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("pt", W.POINT), ("mouseData", W.DWORD), ("flags", W.DWORD), ("time", W.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, W.WPARAM, W.LPARAM)

    user32.SendInput.argtypes = [W.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = W.UINT
    user32.OpenClipboard.argtypes = [W.HWND]
    user32.OpenClipboard.restype = W.BOOL
    user32.CloseClipboard.restype = W.BOOL
    user32.EmptyClipboard.restype = W.BOOL
    user32.GetClipboardData.argtypes = [W.UINT]
    user32.GetClipboardData.restype = W.HANDLE
    user32.SetClipboardData.argtypes = [W.UINT, W.HANDLE]
    user32.SetClipboardData.restype = W.HANDLE
    user32.IsClipboardFormatAvailable.argtypes = [W.UINT]
    user32.IsClipboardFormatAvailable.restype = W.BOOL
    user32.CountClipboardFormats.restype = ctypes.c_int
    user32.GetClipboardSequenceNumber.restype = W.DWORD
    user32.GetForegroundWindow.restype = W.HWND
    user32.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
    user32.GetWindowThreadProcessId.restype = W.DWORD
    user32.WindowFromPoint.argtypes = [W.POINT]
    user32.WindowFromPoint.restype = W.HWND
    user32.GetAncestor.argtypes = [W.HWND, W.UINT]
    user32.GetAncestor.restype = W.HWND
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.RegisterHotKey.argtypes = [W.HWND, ctypes.c_int, W.UINT, W.UINT]
    user32.RegisterHotKey.restype = W.BOOL
    user32.UnregisterHotKey.argtypes = [W.HWND, ctypes.c_int]
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, W.HINSTANCE, W.DWORD]
    user32.SetWindowsHookExW.restype = W.HHOOK
    user32.CallNextHookEx.argtypes = [W.HHOOK, ctypes.c_int, W.WPARAM, W.LPARAM]
    user32.CallNextHookEx.restype = LRESULT
    user32.UnhookWindowsHookEx.argtypes = [W.HHOOK]
    user32.GetMessageW.argtypes = [ctypes.POINTER(W.MSG), W.HWND, W.UINT, W.UINT]
    user32.PostThreadMessageW.argtypes = [W.DWORD, W.UINT, W.WPARAM, W.LPARAM]
    user32.GetDoubleClickTime.restype = W.UINT
    kernel32.GlobalAlloc.argtypes = [W.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = W.HGLOBAL
    kernel32.GlobalLock.argtypes = [W.HGLOBAL]
    kernel32.GlobalLock.restype = W.LPVOID
    kernel32.GlobalUnlock.argtypes = [W.HGLOBAL]
    kernel32.GlobalFree.argtypes = [W.HGLOBAL]
    kernel32.GetModuleHandleW.argtypes = [W.LPCWSTR]
    kernel32.GetModuleHandleW.restype = W.HMODULE
    kernel32.CreateMutexW.argtypes = [W.LPVOID, W.BOOL, W.LPCWSTR]
    kernel32.CreateMutexW.restype = W.HANDLE
    kernel32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel32.OpenProcess.restype = W.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
    kernel32.CloseHandle.argtypes = [W.HANDLE]

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    WH_MOUSE_LL = 14
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN, WM_MBUTTONDOWN = 0x0201, 0x0202, 0x0204, 0x0207
    GA_ROOT = 2
    MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
    VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN, VK_C = 0x11, 0x10, 0x12, 0x5B, 0x5C, 0x43
    ERROR_ALREADY_EXISTS = 183
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


_VK_NAMES = {f"F{i}": 0x6F + i for i in range(1, 25)} | {"SPACE": 0x20, "INSERT": 0x2D, "HOME": 0x24, "END": 0x23}


def parse_hotkey(spec: str) -> tuple[int, int]:
    """'Alt+Shift+P' -> (modificadores, código de tecla virtual)."""
    mods, vk = 0, None
    for part in [p.strip().upper() for p in spec.split("+") if p.strip()]:
        if part in ("ALT",):
            mods |= 0x1
        elif part in ("CTRL", "CONTROL"):
            mods |= 0x2
        elif part == "SHIFT":
            mods |= 0x4
        elif part in ("WIN", "SUPER"):
            mods |= 0x8
        elif len(part) == 1 and part.isalnum():
            vk = ord(part)
        elif part in _VK_NAMES:
            vk = _VK_NAMES[part]
        else:
            raise ValueError(f"Tecla desconocida en el atajo: {part}")
    if vk is None or mods == 0:
        raise ValueError("El atajo necesita al menos un modificador y una tecla, p. ej. Alt+Shift+P")
    return mods, vk


def foreground_info() -> tuple[str, str]:
    """(clase de ventana, nombre del .exe) de la app en primer plano."""
    if not IS_WIN:
        return "", ""
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    pid = W.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if h:
        size = W.DWORD(1024)
        path = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, path, ctypes.byref(size)):
            exe = os.path.basename(path.value)
        kernel32.CloseHandle(h)
    return buf.value, exe


def window_belongs_to_us_at(x: int, y: int) -> bool:
    if not IS_WIN:
        return False
    hwnd = user32.WindowFromPoint(W.POINT(x, y))
    root = user32.GetAncestor(hwnd, GA_ROOT) if hwnd else None
    if not root:
        return False
    pid = W.DWORD()
    user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
    return pid.value == os.getpid()


def _open_clipboard(retries: int = 10) -> bool:
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def get_clipboard_text() -> str | None:
    if not IS_WIN:
        return None
    if not _open_clipboard():
        return None
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = kernel32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p) if p else None
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str) -> bool:
    if not IS_WIN:
        return False
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    if not _open_clipboard():
        return False
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not h:
            return False
        p = kernel32.GlobalLock(h)
        ctypes.memmove(p, data, size)
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)
            return False
        return True  # el sistema es dueño de h a partir de acá
    finally:
        user32.CloseClipboard()


def _clipboard_is_text_only() -> bool:
    """True si el portapapeles está vacío o sólo tiene texto (así podemos restaurarlo sin pérdidas)."""
    if not _open_clipboard():
        return False
    try:
        n = user32.CountClipboardFormats()
        return n == 0 or bool(user32.IsClipboardFormatAvailable(CF_UNICODETEXT)) and n <= 4
    finally:
        user32.CloseClipboard()


def _send_ctrl_c() -> None:
    seq = [(VK_CONTROL, 0), (VK_C, 0), (VK_C, KEYEVENTF_KEYUP), (VK_CONTROL, KEYEVENTF_KEYUP)]
    arr = (INPUT * len(seq))()
    for i, (vk, flags) in enumerate(seq):
        arr[i].type = INPUT_KEYBOARD
        arr[i].ki = KEYBDINPUT(vk, 0, flags, 0, 0)
    user32.SendInput(len(seq), arr, ctypes.sizeof(INPUT))


def _wait_modifiers_released(timeout: float = 1.5) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in (VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN)):
            return
        time.sleep(0.015)


def copy_selection(timeout: float = 0.6) -> tuple[str | None, str]:
    """Copia la selección de la app activa. Devuelve (texto, origen).

    origen: 'selection' | 'clipboard' (terminal: no mandamos Ctrl+C) | 'none'
    """
    if not IS_WIN:
        return None, "none"
    cls, _exe = foreground_info()
    if cls in CONSOLE_CLASSES:
        # En una terminal Ctrl+C interrumpe el programa: usamos lo que ya esté copiado.
        return get_clipboard_text(), "clipboard"

    _wait_modifiers_released()
    restorable = _clipboard_is_text_only()
    previous = get_clipboard_text() if restorable else None
    before = user32.GetClipboardSequenceNumber()
    _send_ctrl_c()

    end = time.monotonic() + timeout
    while time.monotonic() < end and user32.GetClipboardSequenceNumber() == before:
        time.sleep(0.015)
    if user32.GetClipboardSequenceNumber() == before:
        return None, "none"  # no había nada seleccionado
    time.sleep(0.03)  # algunas apps escriben el portapapeles en varios pasos
    text = get_clipboard_text()
    if restorable:
        if previous is not None:
            set_clipboard_text(previous)
        elif _open_clipboard():
            user32.EmptyClipboard()
            user32.CloseClipboard()
    return text, "selection"


class InputWatcher(threading.Thread):
    """Hilo con su propio bucle de mensajes: atajo global y gancho de mouse.

    Callbacks (se llaman desde este hilo; la UI debe reenviarlos al hilo de Qt):
      on_hotkey()
      on_selection_gesture(x, y)   arrastre o doble clic terminado
      on_mouse_down(x, y)          cualquier clic (para cerrar burbujas)
    """

    HOTKEY_ID = 0xF0B1

    def __init__(self, hotkey: str, on_hotkey: Callable[[], None],
                 on_selection_gesture: Callable[[int, int], None], on_mouse_down: Callable[[int, int], None]):
        super().__init__(name="input-watcher", daemon=True)
        self.hotkey = hotkey
        self.on_hotkey = on_hotkey
        self.on_selection_gesture = on_selection_gesture
        self.on_mouse_down = on_mouse_down
        self.mouse_enabled = True
        self.hotkey_ok = threading.Event()
        self.hotkey_error: str | None = None
        self._tid = 0
        self._down: tuple[int, int, float] | None = None
        self._last_up: tuple[int, int, float] | None = None
        self._proc = None

    def run(self) -> None:
        if not IS_WIN:
            self.hotkey_error = "Sólo Windows"
            self.hotkey_ok.set()
            return
        self._tid = kernel32.GetCurrentThreadId()
        try:
            mods, vk = parse_hotkey(self.hotkey)
            if user32.RegisterHotKey(None, self.HOTKEY_ID, mods | MOD_NOREPEAT, vk):
                self.hotkey_error = None
            else:
                self.hotkey_error = f"{self.hotkey} ya lo usa otra aplicación"
        except ValueError as e:
            self.hotkey_error = str(e)
        self.hotkey_ok.set()

        self._proc = HOOKPROC(self._mouse_proc)
        hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        if not hook:
            log.warning("No pude instalar el gancho de mouse (error %s)", ctypes.get_last_error())

        msg = W.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam == self.HOTKEY_ID:
                self._safe(self.on_hotkey)
        if hook:
            user32.UnhookWindowsHookEx(hook)
        user32.UnregisterHotKey(None, self.HOTKEY_ID)

    def _safe(self, fn, *args) -> None:
        try:
            fn(*args)
        except Exception:
            log.exception("Error en callback de entrada")

    def _mouse_proc(self, code, wparam, lparam):
        # Debe volver rápido: Windows desactiva ganchos lentos.
        if code >= 0:
            info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            x, y, now = info.pt.x, info.pt.y, time.monotonic()
            if wparam in (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN):
                self._safe(self.on_mouse_down, x, y)
                if wparam == WM_LBUTTONDOWN:
                    self._down = (x, y, now)
            elif wparam == WM_LBUTTONUP and self._down and self.mouse_enabled:
                dx, dy, t0 = self._down
                dragged = abs(x - dx) + abs(y - dy) >= 12 and now - t0 > 0.12
                dbl_ms = user32.GetDoubleClickTime() / 1000
                double = bool(self._last_up and now - self._last_up[2] <= dbl_ms
                              and abs(x - self._last_up[0]) + abs(y - self._last_up[1]) <= 6)
                self._last_up = (x, y, now)
                if dragged or double:
                    self._safe(self.on_selection_gesture, x, y)
                self._down = None
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def stop(self) -> None:
        if IS_WIN and self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)


_mutex = None


def acquire_single_instance(name: str = "PopSay.SingleInstance") -> bool:
    global _mutex
    if not IS_WIN:
        return True
    _mutex = kernel32.CreateMutexW(None, False, name)
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def system_prefers_dark() -> bool:
    if not IS_WIN:
        return True
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return True


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def set_start_with_windows(enabled: bool, command: str) -> bool:
    """Registra la ruta ACTUAL del ejecutable (si movés el pendrive, volvé a activarlo)."""
    if not IS_WIN:
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            try:  # entrada de cuando la app se llamaba "Fonética Burbuja"
                winreg.DeleteValue(k, "FoneticaBurbuja")
            except FileNotFoundError:
                pass
            if enabled:
                winreg.SetValueEx(k, "PopSay", 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(k, "PopSay")
                except FileNotFoundError:
                    pass
        return True
    except OSError as e:
        log.warning("No pude cambiar el inicio automático: %s", e)
        return False
