@echo off
chcp 65001 >nul
rem Baglanti Birlestirici - arayuzu baslat
cd /d "%~dp0"

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "arayuz.py"
    exit /b 0
)

where python >nul 2>nul
if %errorlevel%==0 (
    start "" python "arayuz.py"
    exit /b 0
)

echo Python bulunamadi. https://www.python.org/downloads/ adresinden kurabilirsin.
pause
