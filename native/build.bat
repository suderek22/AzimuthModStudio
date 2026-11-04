@echo off
rem Builds CarPackLoader.dll (packs) and CarPackStudio.dll (wrapper test bench) with MSVC (VS 2022 Build Tools)
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul
cd /d "%~dp0"
cl /nologo /LD /O2 /EHsc /W3 /I"..\native\include" CarPackLoader.cpp /Fe:..\bin\CarPackLoader.dll user32.lib || exit /b 1
cl /nologo /LD /O2 /EHsc /W3 /I"..\native\include" CarPackStudio.cpp /Fe:..\bin\CarPackStudio.dll user32.lib winsqlite3.lib runtimeobject.lib || exit /b 1
