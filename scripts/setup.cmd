@echo off
rem Windows entry point for setup.ps1: runs it whatever the PowerShell
rem execution policy is, from cmd or PowerShell.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
exit /b %ERRORLEVEL%
