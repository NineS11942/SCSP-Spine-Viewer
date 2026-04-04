@echo off
chcp 65001 >nul
title SCSP Spine Viewer - Nuitka Build

echo ==================================================
echo   SCSP Spine Viewer - Nuitka Build
echo ==================================================
echo.

:: Check Nuitka
where nuitka >nul 2>&1
if errorlevel 1 (
    echo [!] Nuitka not found, installing...
    pip install nuitka ordered-set zstandard
    if errorlevel 1 (
        echo [ERROR] Nuitka install failed
        pause
        exit /b 1
    )
)

:: Clean old builds
echo [1/3] Cleaning old builds...
if exist SCSP_Spine_Viewer.dist rmdir /s /q SCSP_Spine_Viewer.dist
if exist SCSP_Spine_Viewer.build rmdir /s /q SCSP_Spine_Viewer.build
if exist SCSP_Spine_Viewer.onefile-build rmdir /s /q SCSP_Spine_Viewer.onefile-build

:: Build
echo [2/3] Nuitka compiling (may take a few minutes)...
python -m nuitka ^
    --onefile ^
    --standalone ^
    --output-filename=SCSP_Spine_Viewer.exe ^
    --include-data-file=index.html=index.html ^
    --include-package=lz4 ^
    --include-package=texture2ddecoder ^
    --include-package=PIL ^
    --include-package=numpy ^
    --include-package=flask ^
    --enable-plugin=numpy ^
    --assume-yes-for-downloads ^
    --windows-console-mode=attach ^
    spine_viewer.py

if errorlevel 1 (
    echo.
    echo [ERROR] Nuitka build failed!
    pause
    exit /b 1
)

:: Cleanup
echo [3/3] Cleaning up...
if exist SCSP_Spine_Viewer.build rmdir /s /q SCSP_Spine_Viewer.build
if exist SCSP_Spine_Viewer.onefile-build rmdir /s /q SCSP_Spine_Viewer.onefile-build

echo.
echo ==================================================
echo   Build complete!
echo   Output: SCSP_Spine_Viewer.exe
echo ==================================================
echo.
pause
