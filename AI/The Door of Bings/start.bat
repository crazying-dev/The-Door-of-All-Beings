@echo off
chcp 65001 >nul
title 众生之门 - 游戏启动器

echo ========================================
echo         众生之门 游戏启动器
echo ========================================
echo.

REM 检查Node.js是否安装
where node >nul 2>nul
if %errorlevel% neq 0 (
    echo [错误] 未检测到Node.js，请先安装Node.js
    echo 下载地址: https://nodejs.org/
    pause
    exit /b 1
)

REM 检查MySQL是否运行
echo [1/4] 检查MySQL服务...
mysql --version >nul 2>nul
if %errorlevel% neq 0 (
    echo [警告] 未检测到MySQL命令行工具
    echo 请确保MySQL服务已启动
    echo.
) else (
    echo [成功] MySQL已安装
)

REM 进入服务器目录
cd /d "%~dp0Server"

REM 检查是否已安装依赖
echo [2/4] 检查服务器依赖...
if not exist "node_modules\\" (
    echo 首次运行，正在安装依赖...
    call npm install
    if %errorlevel% neq 0 (
        echo [错误] 依赖安装失败
        pause
        exit /b 1
    )
)

REM 检查环境配置
echo [3/4] 检查环境配置...
if not exist ".env" (
    echo 创建环境配置文件...
    copy .env.example .env
    echo.
    echo [重要] 请编辑 Server\.env 文件，配置数据库连接信息
    echo 然后重新运行此脚本
    echo.
    pause
    exit /b 0
)

REM 启动服务器
echo [4/4] 启动游戏服务器...
echo.
echo ========================================
echo 服务器正在启动...
echo 请在Unity中打开项目运行游戏
echo ========================================
echo.

node server.js

pause