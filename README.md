# Microsoft Rewards Dual-Platform Automation Bot

Hệ thống bot tự động hóa tìm kiếm Bing để nhận điểm Microsoft Rewards hàng ngày, chạy 100% trên Windows PC bằng Python và Playwright.

## ✨ Tính năng chính

- **Anti-Bot Telemetry**: Sử dụng Playwright Persistent Context trên Microsoft Edge native + Device Emulation chuẩn (iPhone/Pixel preset)
- **Ngữ nghĩa truy vấn thông minh**: Cào dữ liệu thời sự thực tế từ Google Trends RSS (`geo=VN`) thay vì từ vựng tĩnh
- **Cơ chế Cooldown 15 phút**: Chia nhỏ tác vụ thành batch (3-4 searches/batch), xen kẽ khoảng nghỉ Gaussian trễ sinh học
- **Dual-Platform**: Chạy đồng thời Desktop (Edge) và Mobile (Emulation) để tối đa hóa điểm
- **Silent Deployment**: Chạy ngầm hoàn toàn qua VBScript + Windows Task Scheduler
- **Auto Cache Purge & Storage Optimization**: Giới hạn cache Chromium (tối đa 10 MB) và tự động dọn sạch thư mục cache rác sau mỗi phiên chạy mà vẫn bảo toàn 100% Cookies/Session đăng nhập.

## 📁 Cấu trúc thư mục

```
ms-rewards-bot/
├── core/
│   ├── __init__.py
│   ├── trends.py           # Fetch Google Trends RSS queries
│   └── humanizer.py        # Gaussian delays, typing simulation, human behaviors
├── profiles/
│   ├── desktop/            # Persistent browser context data for Desktop
│   └── mobile/             # Persistent browser context data for Mobile
├── runner.py               # Unified engine executing Desktop & Mobile batches
├── setup_login.py          # Script GUI để đăng nhập tài khoản lần đầu
├── run.bat                 # Portable launcher chạy trực tiếp nhận diện venv tự động
├── run_task.vbs            # Silent VBScript launcher (chạy ẩn không popup cmd)
├── schedule_setup.ps1      # PowerShell script tự động tạo Windows Task Scheduler
├── requirements.txt
└── README.md
```

## 🚀 Cài đặt nhanh

### 1. Yêu cầu hệ thống
- Windows 10/11
- Python 3.9+
- Microsoft Edge đã cài đặt
- Kết nối internet

### 2. Cài đặt dependencies

```bash
cd ms-rewards-bot
pip install -r requirements.txt
playwright install msedge
```

### 3. Đăng nhập tài khoản (Chỉ chạy 1 lần)

Chạy script setup để tạo persistent context cho Desktop và Mobile:

```bash
# Menu tương tác
python setup_login.py

# Hoặc chạy trực tiếp từng nền tảng
python setup_login.py --desktop
python setup_login.py --mobile
python setup_login.py --both
```

**Quan trọng**: Khi trình duyệt mở ra:
1. Đăng nhập tài khoản Microsoft tại `rewards.bing.com`
2. **Bắt buộc tick "Keep me signed in" / "Giữ đăng nhập"**
3. Đóng trình duyệt bằng nút **X** (góc trên) để script lưu session

### 4. Khởi chạy bot (Run & Debug)

- **Chạy trực tiếp bằng `run.bat` (khuyên dùng, tự động nhận diện môi trường ảo):**
  ```cmd
  run.bat
  # Chế độ debug có hiển thị giao diện trình duyệt:
  run.bat --debug
  ```
- **Hoặc chạy thủ công qua lệnh Python:**
  ```bash
  python runner.py --debug
  ```

Chế độ này sẽ hiển thị trình duyệt, in log chi tiết để kiểm tra hành vi.

### 5. Triển khai tự động (Silent)

#### Cách 1: Chạy thủ công ẩn
```bash
wscript run_task.vbs
```

#### Cách 2: Tạo Windows Task Scheduler (Khuyên dùng)

Chạy PowerShell **as Administrator**:

```powershell
# Chạy lúc 08:30 mỗi ngày
.\schedule_setup.ps1

# Hoặc chạy khi đăng nhập Windows
.\schedule_setup.ps1 -RunAtLogon

# Tùy chỉnh giờ và python path
.\schedule_setup.ps1 -RunTime "06:00" -PythonPath "C:\Python311\python.exe"
```

Task sẽ chạy ngầm, không hiện cửa sổ cmd, tiêu thụ tài nguyên tối thiểu.

## ⚙️ Cấu hình nâng cao

### runner.py arguments

| Tham số | Mặc định | Mô tả |
|---------|----------|-------|
| `--desktop` | 30 | Số lượt tìm kiếm Desktop |
| `--mobile` | 20 | Số lượt tìm kiếm Mobile |
| `--batch` | 3 | Kích thước batch (search/batch trước cooldown) |
| `--headless` | True | Chạy ẩn (headless) |
| `--no-headless` | - | Chạy có giao diện |
| `--debug` | False | Bật debug (headless=False, verbose) |
| `--device` | "iPhone 14 Pro" | Device preset mobile |

Ví dụ:
```bash
python runner.py --desktop 30 --mobile 20 --batch 4 --debug
python runner.py --device "Pixel 7"
```

### Tùy chỉnh run_task.vbs

Mở file `run_task.vbs` và chỉnh sửa 2 dòng:
```vbscript
pythonPath = "python.exe"  ' Đường dẫn python.exe (venv hoặc global)
scriptPath = "C:\...\ms-rewards-bot\runner.py"  ' Đường dẫn tuyệt đối đến runner.py
```

### Tùy chỉnh schedule_setup.ps1

Các tham số dòng lệnh:
- `-RunTime "HH:mm"`: Giờ chạy hàng ngày (mặc định 08:30)
- `-RunAtLogon`: Chạy khi đăng nhập Windows thay vì giờ cố định
- `-PythonPath`: Đường dẫn python.exe
- `-ProjectPath`: Thư mục project (mặc định thư mục chứa script)

## 🔧 Cách hoạt động (Technical Details)

### 1. Persistent Context
- Sử dụng `launch_persistent_context` với `channel="msedge"` để dùng Edge thật
- Profile lưu tại `profiles/desktop` và `profiles/mobile`
- Cookie, localStorage, session được giữ lại giữa các lần chạy
- Flag `--disable-blink-features=AutomationControlled` ẩn navigator.webdriver

### 2. Mobile Emulation
- Sử dụng `playwright.devices['iPhone 14 Pro']` (hoặc Pixel 7, etc.)
- Tự động set: viewport, user-agent, isMobile, hasTouch, deviceScaleFactor
- Chạy trên cùng engine Edge nhưng giả lập mobile hoàn hảo

### 3. Google Trends RSS
- Fetch từ `https://trends.google.com/trending/rss?geo=VN`
- Parse XML, unescape HTML entities
- Fallback 30 từ khóa định nghĩa sẵn nếu mất mạng

### 4. Humanizer (Gaussian Delays)
- `gaussian_delay()`: Phân phối Gaussian (mean=9s, std=2.5s) tránh pattern cố định
- `typing_delay()`: 50-140ms/char giả lập gõ phím
- `cooldown_sleep()`: 15 phút + random(30-90s) với countdown console

### 5. Interleaved Pipeline
```
[Desktop Batch 3] → [Mobile Batch 3] → [Cooldown 15m] → [Desktop Batch 3] → [Mobile Batch 3] → ...
```
Đảm bảo tuân thủ rate limit Microsoft Rewards.

### 6. Natural Search Behavior
- Gõ từng ký tự với độ trễ ngẫu nhiên
- Cuộn trang mượt (smooth scroll) 400-800px
- Đọc trang 2-6 giây
- 20% click kết quả organic (không phải ad), đọc 3-5s rồi back

## 📊 Kiểm tra điểm

Sau khi chạy, vào `https://rewards.bing.com` kiểm tra:
- **PC search**: Đạt 30/30 (hoặc số bạn config)
- **Mobile search**: Đạt 20/20 (hoặc số bạn config)
- **Daily set**: Có thể hoàn thành thêm qua trình duyệt thủ công

## 🐛 Troubleshooting

### Lỗi "Profile not found"
```bash
python setup_login.py --both
```

### Lỗi "Executable doesn't exist" (Playwright)
```bash
playwright install msedge
```

### Task Scheduler không chạy
1. Mở Task Scheduler (`taskschd.msc`)
2. Tìm `MicrosoftRewardsAutoPilot`
3. Kiểm tab History (Enable All Tasks History nếu chưa bật)
4. Chạy thử `Start-ScheduledTask -TaskName "MicrosoftRewardsAutoPilot"`

### Python không tìm thấy (trong Task)
Sửa `run_task.vbs`:
```vbscript
pythonPath = "C:\Python311\python.exe"  ' Đường dẫn đầy đủ
```
Hoặc thêm Python vào System PATH.

### Edge bị crash / lock profile
```bash
# Xóa lock file nếu có
del profiles\desktop\SingletonLock
del profiles\mobile\SingletonLock
```

## ⚠️ Lưu ý quan trọng

1. **Tuân thủ ToS**: Microsoft Rewards cho phép tìm kiếm tự nhiên. Bot này mô phỏng hành vi người thật nhưng vẫn có rủi ro tài khoản bị khóa nếu lạm dụng.
2. **Không chạy song song**: Chỉ chạy 1 instance tại một thời điểm (profile lock).
3. **Cập nhật Edge**: Playwright cần Edge version tương thích. Chạy `playwright install msedge` định kỳ.
4. **VPN/Proxy**: Nên dùng IP Việt Namresidential để tránh flag bất thường.
5. **Backup profile**: Copy thư mục `profiles/` để khôi phục nhanh nếu cần cài lại máy.

## 📝 Changelog

### v1.0.0 (2026-10-26)
- Initial release
- Dual-platform (Desktop + Mobile) với Persistent Context
- Google Trends RSS integration (geo=VN)
- Gaussian humanizer + 15min cooldown pipeline
- Silent VBScript launcher + Task Scheduler setup

## 📄 License

MIT License - Sử dụng tự do cho mục đích cá nhân.

---

**Enjoy your Microsoft Rewards points! 🎁**