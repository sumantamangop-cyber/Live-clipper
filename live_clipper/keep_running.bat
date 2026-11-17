@echo off
rem Runs "python -m live_clipper <mode>" (watch or serve) and starts it again if it ever exits.
rem Output goes to live_clipper_data\<mode>.log. Started at logon by the scheduled tasks.
cd /d "%~dp0.."
:again
python -m live_clipper %1 >> live_clipper_data\%1.log 2>&1
timeout /t 30 /nobreak >nul
goto again
