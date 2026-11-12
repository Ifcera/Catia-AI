"""CATIA ana penceresini bulma ve asistan penceresini yanına yaslama (Win32, COM kullanmaz)."""
from __future__ import annotations

import win32api
import win32con
import win32gui
import win32process

from ..catia.dialogs import catia_pids


def find_catia_window() -> int | None:
    pids = catia_pids()
    if not pids:
        return None
    found: list[tuple[int, int]] = []

    def cb(h, _):
        try:
            _, pid = win32process.GetWindowThreadProcessId(h)
            if pid in pids and win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h).startswith("CATIA V5"):
                l, t, r, b = win32gui.GetWindowRect(h)
                found.append(((r - l) * (b - t), h))
        except win32gui.error:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return max(found)[1] if found else None


def dock_beside_catia(own_hwnd: int, width_px: int, side: str = "right") -> str:
    """Asistanı CATIA'nın bulunduğu ekranın sağ (ya da sol) kenarına, tam yükseklikte yerleştirir ve
    CATIA'yı kalan alana sığdırır. Dönüş: kullanıcıya gösterilecek kısa durum metni."""
    cat = find_catia_window()
    if cat is None:
        return "CATIA penceresi bulunamadı."
    mon = win32api.MonitorFromWindow(cat, win32con.MONITOR_DEFAULTTONEAREST)
    wl, wt, wr, wb = win32api.GetMonitorInfo(mon)["Work"]
    height = wb - wt
    if win32gui.IsIconic(cat) or win32gui.GetWindowPlacement(cat)[1] == win32con.SW_SHOWMAXIMIZED:
        win32gui.ShowWindow(cat, win32con.SW_RESTORE)
    if side == "left":
        win32gui.MoveWindow(own_hwnd, wl, wt, width_px, height, True)
        win32gui.MoveWindow(cat, wl + width_px, wt, (wr - wl) - width_px, height, True)
    else:
        win32gui.MoveWindow(own_hwnd, wr - width_px, wt, width_px, height, True)
        win32gui.MoveWindow(cat, wl, wt, (wr - wl) - width_px, height, True)
    return "CATIA'nın yanına yaslandı."
