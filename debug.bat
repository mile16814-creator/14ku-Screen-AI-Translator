@echo off
REM 调试模式快速启动脚本
REM 启用调试模式后，OCR处理过程中的图像会保存到 "调试模式图像" 目录

echo ========================================
echo 屏幕翻译工具 - 调试模式启动
echo ========================================
echo.

REM 设置调试模式环境变量
set SCREEN_TRANSLATOR_DEBUG=1

REM 显示调试信息
echo [调试模式] 已启用
echo [调试信息] OCR图像将保存到: %CD%\调试模式图像
echo.

REM 切换到脚本所在目录
cd /d "%~dp0"

REM 启动主程序
echo 正在启动程序...
echo.

python main.py

REM 如果程序异常退出，暂停以便查看错误信息
if errorlevel 1 (
    echo.
    echo ========================================
    echo 程序异常退出，错误代码: %errorlevel%
    echo ========================================
    pause
)

