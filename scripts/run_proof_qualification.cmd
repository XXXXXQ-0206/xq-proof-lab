@echo off
setlocal
cd /d "%~dp0.."
echo This historical qualification launcher is disabled during static closeout. 1>&2
echo Its 64-position corpus does not pin the proof-store SHA required by the runner. 1>&2
echo See docs\MINIMAL_STATIC_CLOSURE.md before rebuilding a qualified corpus. 1>&2
exit /b 2
