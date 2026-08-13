@echo off
REM Build Konclude as a DLL with its C interface enabled and install the result
REM into pykonclude\lib\, where pyproject.toml's [tool.maturin] include picks it
REM up and bundles it into the wheel. Windows counterpart of build-konclude.sh.
REM
REM Invoked by cibuildwheel as `before-all` (see [tool.cibuildwheel.windows] in
REM pyproject.toml). Expects qmake (Qt 5, qtbase) on PATH and an MSVC
REM environment for nmake -- the workflow sets both up before cibuildwheel runs.

setlocal enabledelayedexpansion

for %%i in ("%~dp0..") do set "project_root=%%~fi"
if "%KONCLUDE_DIR%"=="" set "KONCLUDE_DIR=%project_root%\Konclude"
set "dest_dir=%project_root%\pykonclude\lib"

REM The workflow caches pykonclude\lib keyed on the Konclude revision.
if exist "%dest_dir%\Konclude.dll" (
    echo %dest_dir%\Konclude.dll already present -- skipping Konclude build
    exit /b 0
)

if not exist "%KONCLUDE_DIR%\KoncludeCLIB.pro" (
    echo error: no Konclude checkout at %KONCLUDE_DIR% 1>&2
    exit /b 1
)

where qmake >nul 2>&1
if errorlevel 1 (
    echo error: qmake not on PATH -- Qt 5 ^(qtbase^) is required 1>&2
    exit /b 1
)
where nmake >nul 2>&1
if errorlevel 1 (
    echo error: nmake not on PATH -- an MSVC environment is required 1>&2
    exit /b 1
)

REM Konclude needs Qt 5, not Qt 6: it carries patched copies of Qt 5's
REM container internals (Source\Utilities\Container\CQtManagedRestricted*).
for /f "delims=" %%v in ('qmake -query QT_VERSION') do set "qt_version=%%v"
echo --- using Qt %qt_version%
if not "%qt_version:~0,2%"=="5." (
    echo error: Konclude requires Qt 5, found Qt %qt_version% 1>&2
    exit /b 1
)

cd /d "%KONCLUDE_DIR%" || exit /b 1
qmake -o Makefile-clib KoncludeCLIB.pro || exit /b 1
nmake -f Makefile-clib || exit /b 1

if not exist "%dest_dir%" mkdir "%dest_dir%"
copy /y "Release-clib\Konclude.dll" "%dest_dir%\Konclude.dll" || exit /b 1

echo --- installed %dest_dir%\Konclude.dll
endlocal
