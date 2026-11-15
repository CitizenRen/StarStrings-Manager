@echo off
rem Builds "StarStrings Manager.exe" into ..\builds as a single file.
setlocal
cd /d "%~dp0"
set ROOT=%~dp0..

python -m pip install -q -r requirements.txt pyinstaller || goto :fail
python make_icons.py || goto :fail

python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "StarStrings Manager" ^
  --icon "%ROOT%\assets\generated\app-icon.ico" ^
  --add-data "%ROOT%\assets\generated;assets\generated" ^
  --collect-data customtkinter ^
  --exclude-module numpy --exclude-module psutil --exclude-module pymupdf --exclude-module fitz ^
  --exclude-module charset_normalizer --exclude-module setuptools --exclude-module pkg_resources ^
  --distpath "%ROOT%\builds" ^
  --workpath "%~dp0_work" ^
  --specpath "%~dp0_work" ^
  app.py || goto :fail

rmdir /s /q "%~dp0_work" 2>nul
echo.
echo Built: %ROOT%\builds\StarStrings Manager.exe
exit /b 0

:fail
echo Build failed.
exit /b 1
