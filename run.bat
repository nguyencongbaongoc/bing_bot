@echo off
title Microsoft Rewards Bot
chcp 65001 >nul 2>&1

:: Chuyển thư mục làm việc về đúng thư mục chứa file .bat
cd /d "%~dp0"

:: ==============================================================
:: 1. KIỂM TRA TÍNH TOÀN VẸN CỦA .VENV (SELF-HEALING CHECK)
:: ==============================================================
if not exist "%~dp0.venv\Scripts\python.exe" goto :SETUP_VENV

:: Chạy thử một lệnh Python ngầm để kiểm tra xem venv có bị hỏng đường dẫn gốc không
"%~dp0.venv\Scripts\python.exe" -c "exit(0)" >nul 2>&1
if not errorlevel 1 (
    goto :RUN_BOT
)

echo.
echo ============================================================
echo [CẢNH BÁO] Thư mục .venv đã bị hỏng hoặc bị lệch đường dẫn gốc
echo           (do vừa di chuyển ổ đĩa hoặc copy từ máy khác)!
echo           Đang tự động dọn dẹp và thiết lập lại .venv mới...
echo ============================================================
echo.
rmdir /s /q "%~dp0.venv" >nul 2>&1

:: ==============================================================
:: 2. KHỞI TẠO .VENV MỚI (Chỉ chạy khi chưa có hoặc venv bị hỏng)
:: ==============================================================
:SETUP_VENV
echo ============================================================
echo [SETUP] Đang thiết lập môi trường ảo .venv cho máy hiện tại...
echo ============================================================
echo.

:: Kiểm tra Python hệ thống
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Máy chưa cài đặt Python hoặc chưa tick "Add Python to PATH"!
    echo         Vui lòng cài Python từ python.org rồi thử lại.
    pause
    exit /b 1
)

echo [1/3] Đang khởi tạo môi trường .venv...
python -m venv "%~dp0.venv"
if errorlevel 1 (
    echo [ERROR] Không thể tạo thư mục .venv!
    pause
    exit /b 1
)

echo [2/3] Đang cài đặt thư viện cần thiết...
"%~dp0.venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
if exist "%~dp0requirements.txt" (
    "%~dp0.venv\Scripts\pip.exe" install -r "%~dp0requirements.txt"
) else (
    "%~dp0.venv\Scripts\pip.exe" install playwright requests psutil
)

echo [3/3] Đang cài đặt trình duyệt Playwright...
"%~dp0.venv\Scripts\python.exe" -m playwright install

echo.
echo ============================================================
echo [THÀNH CÔNG] Môi trường .venv đã sẵn sàng! Bắt đầu chạy bot...
echo ============================================================
echo.

:: ==============================================================
:: 3. CHẠY BOT
:: ==============================================================
:RUN_BOT
set "PY_BIN=%~dp0.venv\Scripts\python.exe"

"%PY_BIN%" runner.py %*

if errorlevel 1 (
    echo.
    echo [ERROR] Bot kết thúc với mã lỗi %ERRORLEVEL%!
    pause
)
