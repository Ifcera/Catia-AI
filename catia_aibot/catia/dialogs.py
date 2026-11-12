"""CATIA'nın açtığı modal diyalogları bulur ve kapatır.

Bir COM çağrısı sürerken CATIA bir uyarı/hata kutusu açarsa çağrı bu kutu
kapanana kadar bloke olur. DialogGuard, çağrı süresince ayrı bir iş
parçacığında CNEXT.exe'ye ait standart diyalog pencerelerini (#32770) arar,
metnini kaydeder ve kapatır. Bu iş parçacığı COM kullanmaz (yalnız Win32).
"""
from __future__ import annotations

import csv
import io
import subprocess
import threading
import time

import win32con
import win32gui
import win32process


def catia_pids() -> set[int]:
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq CNEXT.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout
    return {int(r[1]) for r in csv.reader(io.StringIO(out)) if len(r) > 1 and r[1].isdigit()}


def _window_texts(hwnd) -> list[str]:
    texts: list[str] = []

    def cb(child, _):
        try:
            cls = win32gui.GetClassName(child)
            if cls in ("Static", "Edit", "RichEdit20W", "RICHEDIT50W"):
                t = win32gui.GetWindowText(child).strip()
                if t:
                    texts.append(t)
        except win32gui.error:
            pass
        return True

    try:
        win32gui.EnumChildWindows(hwnd, cb, None)
    except win32gui.error:
        pass
    return texts


def find_dialogs(pids: set[int]) -> list[dict]:
    found: list[dict] = []

    def cb(hwnd, _):
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid in pids and win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == "#32770":
                found.append({"hwnd": hwnd, "title": win32gui.GetWindowText(hwnd), "text": _window_texts(hwnd)})
        except win32gui.error:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return found


def dismiss(hwnd) -> None:
    # Önce "OK/Tamam" (IDOK), sonra kapat. Evet/Hayır sorularında IDCANCEL güvenli taraftır.
    try:
        win32gui.PostMessage(hwnd, win32con.WM_COMMAND, win32con.IDCANCEL, 0)
        time.sleep(0.1)
        if win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
            win32gui.PostMessage(hwnd, win32con.WM_COMMAND, win32con.IDOK, 0)
            time.sleep(0.1)
        if win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    except win32gui.error:
        pass


class DialogGuard:
    """with DialogGuard() as g: ...COM çağrısı...;  g.dialogs -> kapatılan diyaloglar."""

    def __init__(self, pids: set[int] | None = None, grace: float = 1.5, interval: float = 0.3, auto_dismiss: bool = True):
        self.pids = pids
        self.grace = grace
        self.interval = interval
        self.auto_dismiss = auto_dismiss
        self.dialogs: list[dict] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._preexisting: set[int] = set()

    def __enter__(self):
        if self.pids is None:
            self.pids = catia_pids()
        # Çağrıdan önce zaten açık olan diyaloglara dokunma (kullanıcınındır).
        self._preexisting = {d["hwnd"] for d in find_dialogs(self.pids)}
        self._thread = threading.Thread(target=self._run, daemon=True, name="catia-dialog-guard")
        self._thread.start()
        return self

    def _run(self):
        start = time.monotonic()
        while not self._stop.wait(self.interval):
            if time.monotonic() - start < self.grace:
                continue
            self._sweep()

    def _sweep(self):
        for d in find_dialogs(self.pids):
            if d["hwnd"] in self._preexisting or any(x["hwnd"] == d["hwnd"] for x in self.dialogs):
                continue
            self.dialogs.append(d)
            if self.auto_dismiss:
                dismiss(d["hwnd"])

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        # Çağrı bittikten sonra kalan yeni diyalogları da topla.
        self._sweep()
        return False

    def summary(self) -> list[str]:
        return [f"{d['title']}: {' | '.join(d['text'])}".strip(": ") for d in self.dialogs]
