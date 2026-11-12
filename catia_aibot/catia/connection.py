"""Çalışan CATIA V5 örneğine resmî COM Automation API üzerinden bağlanma.

Geç bağlama (late binding / dynamic dispatch) kullanılır: R20'de makecache
(gencache) tür kütüphanesi üretmek gerekmez ve sürüm farklarına daha dayanıklıdır.
"""
from __future__ import annotations

from dataclasses import dataclass

import pythoncom
import pywintypes
import win32com.client
import win32com.client.dynamic

PROG_ID = "CATIA.Application"


class CatiaNotRunning(RuntimeError):
    pass


@dataclass
class CatiaInfo:
    name: str
    full_name: str
    version: int | None
    release: int | None
    service_pack: int | None
    os: str | None


def connect(start_if_needed: bool = False):
    """Açık CATIA'ya bağlanır. COM çağrıları yapılacak her iş parçacığında
    önce pythoncom.CoInitialize() çağrılmış olmalı."""
    try:
        unk = pythoncom.GetActiveObject(PROG_ID)
        disp = unk.QueryInterface(pythoncom.IID_IDispatch)
        return win32com.client.dynamic.Dispatch(disp)
    except pywintypes.com_error:
        if not start_if_needed:
            raise CatiaNotRunning(
                "CATIA çalışmıyor ya da COM'a kayıtlı değil. CATIA V5'i açıp tekrar deneyin."
            ) from None
    return win32com.client.dynamic.Dispatch(PROG_ID)


def _safe(fn):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - sürüm farkı olan özellikler
        return None


def read_info(catia) -> CatiaInfo:
    sc = _safe(lambda: catia.SystemConfiguration)
    return CatiaInfo(
        name=catia.Name,
        full_name=_safe(lambda: catia.FullName) or "",
        version=_safe(lambda: sc.Version) if sc else None,
        release=_safe(lambda: sc.Release) if sc else None,
        service_pack=_safe(lambda: sc.ServicePack) if sc else None,
        os=_safe(lambda: sc.OperatingSystem) if sc else None,
    )
