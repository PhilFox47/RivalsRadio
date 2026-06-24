@echo off
REM One-click Windows build for RivalsRadio.
REM Double-click this file (or run it) to produce dist\RivalsRadio.exe

echo === RivalsRadio build ===

REM Create / reuse a local virtual environment.
if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv
)
call .venv\Scripts\activate.bat

echo Installing dependencies...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt pyinstaller

echo Building executable...
pyinstaller --noconfirm RivalsRadio.spec

if exist dist\RivalsRadio.exe (
    echo.
    echo Done!  Your app is at:  dist\RivalsRadio.exe
    echo You can move that .exe anywhere and double-click to run.
) else (
    echo.
    echo Build failed - see the output above for errors.
)
pause
