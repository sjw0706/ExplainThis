@echo off
setlocal

cd /d "%~dp0"

echo [1/5] Cleaning previous build artifacts...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [2/5] Creating virtual environment if missing...
if not exist .venv (
    python -m venv .venv
)

echo [3/5] Activating virtual environment...
call .venv\Scripts\activate.bat

echo [4/5] Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller

echo [5/5] Building executable...
pyinstaller --noconfirm ExplainThis.spec

echo.
echo Build complete.
echo Output folder: dist\ExplainThis
echo.

pause