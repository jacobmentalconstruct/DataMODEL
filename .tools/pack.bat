@echo off
rem Pack this project into <ProjectName>.zip beside the project folder (or: pack.bat D:\out\name.zip).
rem Leaves out this copy's identity, receipts, journal and snapshots; writes unpack launchers beside the zip.
where python >nul 2>nul && (python "%~dp0bin\helpers.py" pack %*) || (py -3 "%~dp0bin\helpers.py" pack %*)
pause
