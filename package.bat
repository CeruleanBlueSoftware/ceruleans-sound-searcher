@echo off
setlocal
title Packaging Cerulean's Sound Searcher
pushd "%~dp0"
set "VERSION=0.4.1"
set "DIST=%~dp0dist\sound_searcher.dist"
set "OUTPUT=%~dp0output"
set "ZIP=%OUTPUT%\ceruleans sound searcher.zip"
set "STAGING=%OUTPUT%\zip staging"
set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
set "LOG=%~dp0package log.txt"
echo Packaging Cerulean's Sound Searcher %VERSION%, %date% %time% > "%LOG%"

if not exist "%DIST%\Cerulean's Sound Searcher.exe" (
	echo There's no build to package. Run sound.bat first.
	goto :error
)
if not exist "%ISCC%" (
	echo Inno Setup 6 wasn't found at "%ISCC%".
	goto :error
)
if not exist "%OUTPUT%" mkdir "%OUTPUT%"

echo Step 1 of 2: making the installer. Details go in "package log.txt".
"%ISCC%" /Q "/DMyAppVersion=%VERSION%" "%~dp0sound_searcher.iss" >> "%LOG%" 2>&1
if errorlevel 1 goto :error

echo Step 2 of 2: making the zip.
if exist "%STAGING%" rmdir /s /q "%STAGING%"
robocopy "%DIST%" "%STAGING%\Cerulean's Sound Searcher" /E /NFL /NDL /NJH /NJS /NP >> "%LOG%" 2>&1
if errorlevel 8 goto :error
if exist "%ZIP%" del "%ZIP%"
powershell -NoProfile -Command "Compress-Archive -Path '%STAGING%\*' -DestinationPath '%ZIP%' -CompressionLevel Optimal" >> "%LOG%" 2>&1
if errorlevel 1 goto :error
rmdir /s /q "%STAGING%"

echo Packaging completed successfully. The installer and the zip are in the output folder.
echo Packaging completed successfully. >> "%LOG%"
popd
pause
exit /b 0

:error
echo Packaging failed. The reason is near the end of "package log.txt".
echo Packaging failed. >> "%LOG%"
popd
pause
exit /b 1
