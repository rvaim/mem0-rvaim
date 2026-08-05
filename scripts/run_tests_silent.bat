@echo off
REM Run the mem0-rvaim test suite silently (no console window).
REM Usage: run_tests_silent.bat [pytest args...]
REM Result is written to ..\tmp\test-run.log and echoed at the end.

setlocal
cd /d "%~dp0.."
if not exist "tmp\venv-test\Scripts\pythonw.exe" (
  echo venv not found: run "python -m venv tmp\venv-test" first >&2
  exit /b 1
)
del /q "tmp\test-run.log" 2>nul
start "" /b "tmp\venv-test\Scripts\pythonw.exe" -m pytest tests\ %* > "tmp\test-run.log" 2>&1
echo Tests started in background. Check tmp\test-run.log for results.
