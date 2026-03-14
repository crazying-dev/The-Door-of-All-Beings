@echo off
chcp 65001 >nul
title 众生之门 - 一键启动

echo ========================================
echo         众生之门 一键启动
echo ========================================
echo.

REM 检查Node.js
where node >nul 2>nul
if %errorlevel% neq 0 (
    echo [错误] 未检测到Node.js
    echo 请先安装Node.js: https://nodejs.org/
    pause
    exit /b 1
)

REM 启动服务器（后台运行）
cd /d "%~dp0Server"

if not exist "node_modules\" (
    echo 首次运行，正在安装服务器依赖...
    call npm install
)

if not exist ".env" (
    echo 创建环境配置文件...
    copy .env.example .env
    echo.
    echo [重要] 请先配置 Server\.env 文件中的数据库信息
    echo 配置完成后重新运行此脚本
    pause
    exit /b 0
)

echo 正在启动游戏服务器...
start /min cmd /c "node server.js"

REM 等待服务器启动
timeout /t 3 /nobreak >nul

echo.
echo 服务器已启动！
echo.
echo 接下来启动Unity项目...
echo.

REM 启动Unity
cd /d "%~dp0"

REM 查找Unity
for /f "delims=" %%i in ('dir /b /s "%PROGRAMFILES%\Unity\Editor\Unity.exe" 2^>nul ^| findstr /i "Unity.exe$"') do (
    set UNITY_PATH=%%i
    goto :start_unity
)

for /f "delims=" %%i in ('dir /b /s "%PROGRAMFILES(X86)%\Unity\Editor\Unity.exe" 2^>nul ^| findstr /i "Unity.exe$"') do (
    set UNITY_PATH=%%i
    goto :start_unity
)

echo [警告] 未找到Unity，请手动打开Unity项目
echo 项目路径: %~dp0
goto :end

:start_unity
echo 正在启动Unity...
start "" "%UNITY_PATH%" -projectPath "%~dp0"

:end
echo.
echo ========================================
echo 游戏启动完成！
echo 服务器: http://localhost:7777
echo ========================================
echo.
echo 按任意键关闭此窗口（服务器将继续运行）
pause >nul