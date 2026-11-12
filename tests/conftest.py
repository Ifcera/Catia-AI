from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _catia_running() -> bool:
    import pythoncom
    import pywintypes

    pythoncom.CoInitialize()
    try:
        pythoncom.GetActiveObject("CATIA.Application")
        return True
    except pywintypes.com_error:
        return False


@pytest.fixture(scope="session")
def tools(tmp_path_factory):
    if not _catia_running():
        pytest.skip("CATIA açık değil")
    from catia_aibot.tools import CatiaTools

    t = CatiaTools(workspace=tmp_path_factory.mktemp("ws"))
    yield t
    t.close()


@pytest.fixture
def msg(tools, request):
    """Her test kendi mesaj grubunda çalışır; sonunda grup geri alınır (açılan belgeler kapanır)."""
    tools.begin_message(request.node.name)
    yield tools
    tools.end_message()
    res = tools.undo_last_message()
    assert not res["errors"], res
