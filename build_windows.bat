@echo off
REM Build standalone Windows binaries of Batch LEAPP with PyInstaller:
REM   - dist\Batch LEAPP.exe   the double-clickable GUI (bundles Python + Tk)
REM   - dist\batch-leapp.exe   the single-file command-line binary
REM
REM Requirements:  Python 3 (with tkinter, included in the python.org installer)
REM                and PyInstaller:   py -m pip install pyinstaller
REM Usage:         build_windows.bat        (both)
REM                build_windows.bat gui
REM                build_windows.bat cli
REM
REM The .exe files are UNSIGNED. SmartScreen may warn on first run on another PC
REM ("Windows protected your PC" -> More info -> Run anyway). Build is for this
REM machine's architecture (typically x64).

setlocal
cd /d "%~dp0"
set WHAT=%1
if "%WHAT%"=="" set WHAT=all

py -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo PyInstaller is not installed. Run:  py -m pip install pyinstaller
    exit /b 1
)

if "%WHAT%"=="all" goto gui
if "%WHAT%"=="gui" goto gui
goto cli

:gui
py -m PyInstaller --noconfirm --windowed --clean ^
    --name "Batch LEAPP" ^
    --icon leapps.ico ^
    batch_leapp_gui.py
if "%WHAT%"=="gui" goto done

:cli
py -m PyInstaller --noconfirm --onefile --clean ^
    --name batch-leapp ^
    --icon leapps.ico ^
    batch_leapp.py

:done
echo.
echo Built into dist\  (Batch LEAPP.exe and/or batch-leapp.exe)
endlocal
