@echo off
rem Double-click to start the music-ears vinyl player. Keep this window open while you use it.
chcp 65001 >nul
cd /d "%~dp0"
python music-ears serve
echo.
pause
