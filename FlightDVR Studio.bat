@echo off
rem Launch FlightDVR Studio without a console window hanging around.
cd /d "%~dp0"
start "" pythonw -m flightdvr
