@echo off
rem Build RivalsRadio.exe locally (no tooling needed beyond Python 3.11+).
python -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm RivalsRadio.spec
echo Done - dist\RivalsRadio.exe
pause
