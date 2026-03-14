@echo off
chcp 65001 >nul
title 众生之门 - Unity项目

echo ========================================
echo       众生之门 Unity项目启动器
echo ========================================
echo.

REM 设置Unity项目路径
set PROJECT_PATH=%~dp0
set PROJECT_PATH=%PROJECT_PATH:~0,-1%

echo 项目路径: %PROJECT_PATH%
echo.

REM 检查Unity是否安装
echo 正在查找Unity安装...

REM 尝试常见的Unity安装路径
set UNITY_PATH=

REM Unity Hub方式
if exist "%PROGRAMFILES%\Unity Hub\Unity Hub.exe" (
    echo 检测到Unity Hub
    echo 请在Unity Hub中打开项目: %PROJECT_PATH%
    start "" "%PROGRAMFILES%\Unity Hub\Unity Hub.exe"
    goto :end
)

REM 直接查找Unity.exe
for /f "delims=" %%i in ('dir /b /s "%PROGRAMFILES%\Unity\Editor\Unity.exe" 2^>nul ^| findstr /i "Unity.exe$"') do (
    set UNITY_PATH=%%i
    goto :found
)

for /f "delims=" %%i in ('dir /b /s "%PROGRAMFILES(X86)%\Unity\Editor\Unity.exe" 2^>nul ^| findstr /i "Unity.exe$"') do (
    set UNITY_PATH=%%i
    goto :found
)

for /f "delims=" %%i in ('dir /b /s "%LOCALAPPDATA%\Unity\Editor\Unity.exe" 2^>nul ^| findstr /i "Unity.exe$"') do (
    set UNITY_PATH=%%i
    goto :found
)

:found
if defined UNITY_PATH (
    echo 找到Unity: %UNITY_PATH%
    echo.
    echo 正在启动Unity项目...
    start "" "%UNITY_PATH%" -projectPath "%PROJECT_PATH%"
    goto :end
)

echo [错误] 未找到Unity安装
echo.
echo 请先安装Unity Editor (推荐版本: 2022.3.21f1)
echo 下载地址: https://unity.com/download
echo.
echo 安装后，在Unity Hub中打开项目: %PROJECT_PATH%

:end
echo.
pause