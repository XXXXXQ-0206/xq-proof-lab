@echo off
setlocal
cd /d "%~dp0..\external\pikafish-official-2026-01-02"
if not exist "Windows\pikafish-avx512.exe" (
  echo Missing frozen Pikafish baseline. Run scripts\bootstrap_pikafish_baseline.ps1 first. 1>&2
  exit /b 1
)
if not exist "pikafish.nnue" (
  echo Missing frozen Pikafish NNUE. Run scripts\bootstrap_pikafish_baseline.ps1 first. 1>&2
  exit /b 1
)
Windows\pikafish-avx512.exe
