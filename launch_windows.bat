@echo off
if not exist .venv\Scripts\pythonw.exe (echo Setup is incomplete. Double-click setup_windows.bat first.& pause & exit /b 1)
set "PYTHONPATH=%~dp0src"
start "EV4 Architect Stage QC" /b .venv\Scripts\pythonw.exe -m ev4_architect_stage_qc
