@echo off
chcp 65001 >nul
title SCSP Spine Viewer - PyInstaller Build

echo ==================================================
echo   SCSP Spine Viewer - PyInstaller Build
echo ==================================================
echo.

:: Check PyInstaller
where pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [!] PyInstaller not found, installing...
    pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] PyInstaller install failed
        pause
        exit /b 1
    )
)

:: Clean old builds
echo [1/3] Cleaning old builds...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist *.spec del /f /q *.spec

:: Build
echo [2/3] PyInstaller packaging...
pyinstaller ^
    --onefile ^
    --name SCSP_Spine_Viewer ^
    --add-data "index.html;." ^
    --add-data "spine_test.html;." ^
    --add-data "static;static" ^
    --hidden-import=v1 ^
    --hidden-import=v1.scsp_common ^
    --hidden-import=v1.scsp_v2 ^
    --hidden-import=v1.scsp_v3 ^
    --hidden-import=v1.scsp2json ^
    --hidden-import=v0 ^
    --hidden-import=v0.scsp_v3_parser ^
    --hidden-import=v0.model_extractor ^
    --hidden-import=v0.png_to_sct ^
    --hidden-import=v0.pack_repacker ^
    --hidden-import=lz4 ^
    --hidden-import=lz4.block ^
    --hidden-import=texture2ddecoder ^
    --hidden-import=PIL ^
    --hidden-import=numpy ^
    --hidden-import=flask ^
    --hidden-import=customtkinter ^
    --collect-all customtkinter ^
    --console ^
    spine_viewer.py

if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller build failed!
    pause
    exit /b 1
)

:: Move exe to root
echo [3/3] Moving output...
move /Y dist\SCSP_Spine_Viewer.exe SCSP_Spine_Viewer.exe

:: Cleanup
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist *.spec del /f /q *.spec

echo.
echo ==================================================
echo   Build complete!
echo   Output: SCSP_Spine_Viewer.exe
echo ==================================================
echo.
pause
