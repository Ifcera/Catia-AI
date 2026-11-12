# PyInstaller: .venv\Scripts\python.exe -m PyInstaller --noconfirm --clean CATIA-Asistan.spec
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, copy_metadata

hidden = (collect_submodules("catia_aibot")
          + collect_submodules("mcp", filter=lambda n: not n.startswith("mcp.cli"))
          + collect_submodules("uvicorn") + collect_submodules("keyring")
          + ["win32com.client", "pythoncom", "pywintypes"])
datas = ([("catia_aibot/agent/skill_catia.md", "catia_aibot/agent"),
          ("catia_aibot/ui/assets/icon.ico", "catia_aibot/ui/assets")]
         + collect_data_files("mcp") + copy_metadata("mcp"))

a = Analysis(["scripts/exe_entry.py"], pathex=["."], datas=datas, hiddenimports=hidden,
             excludes=["mcp.cli", "pytest", "tkinter"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="CATIA-Asistan", console=False, upx=False,
          icon="catia_aibot/ui/assets/icon.ico")
coll = COLLECT(exe, a.binaries, a.datas, name="CATIA-Asistan", upx=False)
