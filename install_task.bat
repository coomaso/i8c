@echo off
chcp 65001 >nul
title i8 待办监测 - Windows 计划任务安装

echo ============================================
echo   i8 工作流待办监测 - 计划任务安装脚本
echo ============================================
echo.

:: 获取当前目录
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

:: Python 路径（使用系统 Python 3）
where python3 >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "PYTHON=python3"
) else (
    where python >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        set "PYTHON=python"
    ) else (
        echo [错误] 未找到 Python，请先安装 Python 3。
        pause
        exit /b 1
    )
)

echo Python 路径: %PYTHON%
echo 脚本目录: %SCRIPT_DIR%
echo.

:: 安装依赖
echo [1/3] 安装依赖包...
%PYTHON% -m pip install requests pycryptodome schedule -q
if %ERRORLEVEL% NEQ 0 (
    echo [警告] 依赖安装可能未完成，请手动执行:
    echo   pip install requests pycryptodome schedule
) else (
    echo [OK] 依赖安装完成
)
echo.

:: 测试连接
echo [2/3] 测试 i8 系统连接...
%PYTHON% "%SCRIPT_DIR%\i8_workflow_monitor.py" 2>&1 | findstr "登录成功\|登录失败"
if %ERRORLEVEL% NEQ 0 (
    echo [警告] 登录测试可能需要手动验证
) else (
    echo [OK] 系统连接正常
)
echo.

:: 安装计划任务
echo [3/3] 安装 Windows 计划任务...

set "TASK_NAME=i8WorkflowMonitor"

:: 删除已有任务（如存在）
schtasks /query /tn "%TASK_NAME%" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    schtasks /delete /tn "%TASK_NAME%" /f >nul
    echo   已删除旧任务
)

:: 创建新任务 - 每 5 分钟执行一次
schtasks /create ^
    /tn "%TASK_NAME%" ^
    /tr "\"%PYTHON%\" \"%SCRIPT_DIR%\i8_workflow_monitor.py\"" ^
    /sc minute ^
    /mo 5 ^
    /ru "%USERNAME%" ^
    /f

if %ERRORLEVEL% EQU 0 (
    echo [OK] 计划任务安装成功！
    echo.
    echo 任务名称: %TASK_NAME%
    echo 执行频率: 每 5 分钟
    echo 执行用户: %USERNAME%
    echo 下次执行: 即将在 5 分钟内触发
) else (
    echo [错误] 计划任务安装失败，请以管理员身份运行此脚本。
)
echo.

echo ============================================
echo   安装完成！请确保 config.ini 中的
echo   企业微信 Webhook 地址已正确配置。
echo ============================================
pause
