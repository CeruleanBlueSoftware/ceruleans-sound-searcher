@echo off
setlocal
title Building Cerulean's Sound Searcher
pushd "%~dp0"
set "PYTHON=%~dp0.win\Scripts\python.exe"
set "DIST=%~dp0dist\sound_searcher.dist"
set "PACKAGES=%~dp0.win\Lib\site-packages"
set "LOG=%~dp0build log.txt"
echo Build of Cerulean's Sound Searcher, %date% %time% > "%LOG%"

echo Step 1 of 3: compiling with Nuitka. This takes a while. Details go in "build log.txt".
"%PYTHON%" -m nuitka --mode=standalone --windows-console-mode=disable --remove-output --assume-yes-for-downloads ^
 "--output-dir=%~dp0dist" "--output-filename=Cerulean's Sound Searcher.exe" ^
 "--windows-icon-from-ico=%~dp0ceruleanBlue.ico" "--company-name=Cerulean Blue Software" ^
 "--product-name=Cerulean's Sound Searcher" --file-version=0.4.1 --product-version=0.4.1 ^
 --include-package=accessible_output2 --include-package=_soundfile_data ^
 --include-module=sound_index --include-module=settings --include-module=clap_onnx --include-module=model_download ^
 --nofollow-import-to=torch --nofollow-import-to=transformers --nofollow-import-to=onnx --nofollow-import-to=onnxscript ^
 --nofollow-import-to=onnxruntime.transformers --nofollow-import-to=onnxruntime.tools --nofollow-import-to=onnxruntime.quantization ^
 "--include-data-files=%~dp0audioset_labels.txt=audioset_labels.txt" ^
 "%~dp0sound_searcher.py" >> "%LOG%" 2>&1
if errorlevel 1 goto :error

echo Step 2 of 3: copying the screen reader, sound file and ONNX Runtime libraries.
robocopy "%PACKAGES%\accessible_output2\lib" "%DIST%\accessible_output2\lib" *.dll /NFL /NDL /NJH /NJS /NP >> "%LOG%" 2>&1
if errorlevel 8 goto :error
robocopy "%PACKAGES%\_soundfile_data" "%DIST%\_soundfile_data" *.dll /NFL /NDL /NJH /NJS /NP >> "%LOG%" 2>&1
if errorlevel 8 goto :error
robocopy "%PACKAGES%\onnxruntime\capi" "%DIST%\onnxruntime\capi" *.dll /NFL /NDL /NJH /NJS /NP >> "%LOG%" 2>&1
if errorlevel 8 goto :error

echo Step 3 of 3: copying the read me, change log and licence.
copy /y "%~dp0README.md" "%DIST%\" >> "%LOG%" 2>&1
copy /y "%~dp0CHANGELOG.md" "%DIST%\" >> "%LOG%" 2>&1
copy /y "%~dp0LICENSE" "%DIST%\LICENSE.txt" >> "%LOG%" 2>&1

echo Build completed successfully. The program is in dist\sound_searcher.dist.
echo Build completed successfully. >> "%LOG%"
popd
pause
exit /b 0

:error
set BUILD_EXIT_CODE=%ERRORLEVEL%
echo Build failed with exit code %BUILD_EXIT_CODE%. The reason is near the end of "build log.txt".
echo Build failed with exit code %BUILD_EXIT_CODE%. >> "%LOG%"
popd
pause
exit /b %BUILD_EXIT_CODE%
