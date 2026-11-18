@echo off
rem Runs "python -m live_clipper <mode>" (watch or serve) and starts it again if it ever exits.
rem Output goes to live_clipper_data\<mode>.log. Started at logon by the scheduled tasks.
cd /d "%~dp0.."
rem serve listens on the Wi-Fi too, so a phone can open it (the token in keys.env gates it)
set "EXTRA="
if "%1"=="serve" set "EXTRA=--lan"
:again
python -m live_clipper %1 %EXTRA% >> live_clipper_data\%1.log 2>&1
timeout /t 30 /nobreak >nul
goto again
