@echo off
REM Serial Monitor 启动脚本
REM 用法: start.bat "项目路径" "步骤"
REM 示例: start.bat "D:\项目" "s3"

cd /d "%~dp0"

echo 正在启动 Serial Monitor & AI Assistant (S5)...
echo.

REM 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误: 未找到Python，请先安装Python
    pause
    exit /b 1
)

REM 检查pyserial
python -c "import serial" >nul 2>&1
if errorlevel 1 (
    echo 正在安装依赖...
    pip install pyserial
)

REM 启动 - 传入项目路径和步骤
echo 启动中...
if "%1"=="" (
    python serial_monitor.py
) else (
    python serial_monitor.py --project "%1" --step "%2"
)

pause
