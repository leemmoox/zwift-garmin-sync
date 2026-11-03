# PyInstaller build: pyinstaller zwift-garmin-sync.spec  ->  dist/zwift-garmin-sync(.exe)
from PyInstaller.utils.hooks import collect_all, collect_data_files

# curl_cffi (used by garminconnect) ships native libraries and CA certificates;
# ua_generator ships data files that are read at runtime.
curl_datas, curl_binaries, curl_hidden = collect_all("curl_cffi")

a = Analysis(
    ["main.py"],
    datas=curl_datas + collect_data_files("ua_generator"),
    binaries=curl_binaries,
    hiddenimports=curl_hidden,
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="zwift-garmin-sync",
    console=True,
    upx=False,
)
