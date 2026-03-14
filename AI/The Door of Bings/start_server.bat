@echo off
chcp 65001 >nul
title 众生之门 - 服务器

cd /d "%~dp0Server"

echo 正在启动众生之门服务器...
echo.

node server.js

pause