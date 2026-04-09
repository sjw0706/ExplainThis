# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_dir = Path(os.getcwd()).resolve()
src_file = project_dir / "src" / "main.py"
third_party_dir = project_dir / "third_party"
assets_dir = project_dir / "assets"

datas = []

if third_party_dir.exists():
    datas.append((str(third_party_dir), "third_party"))

if assets_dir.exists():
    datas.append((str(assets_dir), "assets"))

block_cipher = None

a = Analysis(
    [str(src_file)],
    pathex=[str(project_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "pytesseract",
        "PIL",
        "requests",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ExplainThis",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(project_dir / "assets" / "app.ico") if (project_dir / "assets" / "app.ico").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ExplainThis",
)