@echo off
rem Puts this folder on Nuke's plugin path, so Nuke shows the JKrender menu.
rem It adds one line to %USERPROFILE%\.nuke\init.py; delete that line to uninstall.
setlocal
set "HERE=%~dp0"
set "HERE=%HERE:~0,-1%"
set "NUKEDIR=%USERPROFILE%\.nuke"
set "INIT=%NUKEDIR%\init.py"
if not exist "%NUKEDIR%" mkdir "%NUKEDIR%"
if not exist "%INIT%" goto add
findstr /l /c:"%HERE%" "%INIT%" >nul 2>&1
if not errorlevel 1 goto already

:add
>>"%INIT%" echo.
>>"%INIT%" echo import nuke
>>"%INIT%" echo nuke.pluginAddPath(r"%HERE%")  # JKrender
echo JKrender was added to %INIT%
goto done

:already
echo JKrender is already in %INIT%

:done
echo.
echo Restart Nuke. Select a Write and choose JKrender ^> Render Selected Write(s), or press Ctrl+Alt+R.
echo.
pause
