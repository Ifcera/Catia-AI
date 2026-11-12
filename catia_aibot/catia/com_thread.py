"""Tüm COM çağrılarını tek bir STA iş parçacığında çalıştırır.

CATIA COM nesneleri oluşturuldukları iş parçacığına bağlıdır; bu yüzden
oturum (CatiaSession) yalnızca bu iş parçacığından kullanılır. Arayüz bu
sınıfa iş gönderir ve sonucu Future olarak bekler; böylece donmaz.
"""
from __future__ import annotations

import concurrent.futures as cf
from typing import Any, Callable

import pythoncom


def _init_com():
    pythoncom.CoInitialize()


class ComThread:
    def __init__(self):
        self._pool = cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="catia-com", initializer=_init_com)

    def submit(self, fn: Callable[..., Any], *args, **kwargs) -> cf.Future:
        return self._pool.submit(fn, *args, **kwargs)

    def call(self, fn: Callable[..., Any], *args, timeout: float | None = None, **kwargs) -> Any:
        return self.submit(fn, *args, **kwargs).result(timeout=timeout)

    def shutdown(self):
        self._pool.shutdown(wait=False, cancel_futures=True)
