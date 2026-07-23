@echo off
cd /d "%~dp0"
where uv >nul 2>nul || (echo uv is required. Install uv, then double-click this file again.& pause & exit /b 1)
uv sync --locked || (echo Setup failed.& pause & exit /b 1)
echo Setup complete. Double-click launch_windows.bat to start EV4 Architect Stage QC.
pause
