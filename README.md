# Microsoft Rewards Dual-Platform Automation Bot (Phase 5 Full Automation)

Hệ thống bot tự động hóa toàn diện Microsoft Rewards hàng ngày, chạy 100% trên Windows PC bằng Python và Playwright với Microsoft Edge nguyên bản. Bot tích hợp đầy đủ chu trình: **Điểm danh & Chuỗi ngày (Streaks) -> Giải Quests & Hoạt động (Activities/Puzzles/AI) -> Tìm kiếm Desktop & Mobile chống Cooldown -> Sổ cái điểm thưởng & Tự động Sleep**.

---

## ✨ Tính năng chính

### 1. Tự động hóa tìm kiếm kép (Dual-Platform Search)
- **Desktop Search**: Tìm kiếm tự nhiên trên Microsoft Edge nguyên bản (mặc định 30 lượt = 90 điểm). 3 lượt đầu tiên tự động thực hiện qua Sidebar Search (`form=PRSB01`).
- **Mobile Emulation Search**: Giả lập thiết bị Android chuẩn (**Google Pixel 7**) với chuỗi User-Agent Microsoft Edge Android (`EdgA`) chính quy (mặc định 20 lượt = 60 điểm).
- **Cơ chế Cooldown 15 phút**: Chia nhỏ tác vụ thành các batch ngẫu nhiên (2-4 câu/lần), tự động đệm thời gian nghỉ 15 phút theo chuẩn giới hạn điểm của Microsoft Rewards.
- **Edge Browsing Streak**: Trong thời gian chờ Cooldown 15 phút, bot duy trì phiên duyệt web Edge ngầm (`active_edge_browsing_cooldown`) với các thao tác cuộn trang và đọc tin tức Bing News, giúp bạn tích lũy đủ 30 phút duyệt web để nhận thưởng Edge Browsing Streak mà không tốn thêm thời gian.

### 2. Ngụy trang chống phát hiện cấp cao (Ultra Stealth & Humanizer)
- **Đường cong chuột Bézier**: Di chuyển con trỏ chuột theo quỹ đạo cong Bézier bậc 2 tự nhiên, kết hợp vi rung (micro-jitter) và gia tốc/giảm tốc theo định luật Fitts's law thay cho các đường thẳng máy móc.
- **Hành vi gõ phím sinh học**: Gõ từng ký tự với độ trễ biến thiên Gaussian (50-140ms/ký tự).
- **Tương tác gợi ý (Autosuggest)**: Xác suất ~25% nhấp chọn trực tiếp từ danh sách từ khóa gợi ý của Bing.
- **Tỷ lệ nhấp tự nhiên (Organic CTR ~30%)**: Nhấp vào các kết quả tìm kiếm không phải quảng cáo, cuộn trang đọc bài 3-6 giây rồi quay lại, tránh bẫy 0% Click-Through Rate.
- **Cluster Search (~30%)**: Tiếp nối các cụm từ khóa tìm kiếm liên quan (Related Searches) để mô phỏng sự tò mò của người thật.
- **Bảo vệ danh tính trình duyệt**: Tự động vô hiệu hóa `navigator.webdriver`, đồng bộ `navigator.userAgentData`, Client Hints, `hardwareConcurrency`, `deviceMemory` và ngôn ngữ `vi-VN`.
- **Tối ưu phần cứng & Làm mát GPU**: Khống chế khung hình ở mức 15 FPS, tắt GPU VSync giúp GPU nghỉ ngơi mát mẻ (<5W, 0 RPM quạt), bảo vệ phần cứng khi chạy ngầm.
- **Khiên Anti-Ban (Safe Throttle)**: Tự động phát hiện cảnh báo giới hạn tốc độ tìm kiếm bất thường trên Dashboard/kết quả Bing và lập tức kích hoạt chế độ giãn cách an toàn (35-60 giây/câu).

### 3. Đầy đủ hoạt động & Nhiệm vụ thưởng (Full Activities & Quests Suite)
- **Streak Protection**: Tự động kiểm tra và bật khiên bảo vệ chuỗi ngày (Streak Protection) trên tài khoản.
- **Check-in đa nền tảng**: Tự động điểm danh hàng ngày trên cả Desktop và ứng dụng Bing Mobile qua OAuth API chính quy (type 103).
- **Daily Set & More Activities**: Hoàn thành toàn bộ thẻ hoạt động, câu đố trắc nghiệm (Quiz), khảo sát (Polls), bài kiểm tra Supersonic Quiz / Lightspeed Quiz.
- **Hỗ trợ giải đố bằng AI cục bộ (Local AI Solver)**: Tích hợp với **Ollama (Qwen 2.5 3B)** để tự động đọc câu hỏi và chọn đáp án chính xác nhất; tự động giải phóng VRAM sau khi hoàn tất.
- **Mobile Read to Earn (+30 pts/ngày)**: Tự động hoàn thành nhiệm vụ đọc báo kiếm điểm thông qua Bing Mobile App API mà không cần cài máy ảo Android nặng nề.
- **Quét Quests & Punch Cards**: Quét và hoàn thành các thẻ sự kiện lớn tại `/earn`, Punch Cards hàng tháng và mục **"Keep earning"** (đạt tối đa 70/70 điểm).
- **Visual Search Streak**: Tự động sinh ảnh PNG mẫu và thực hiện nhiệm vụ tìm kiếm hình ảnh Visual Search.
- **MSN Daily Gaming Tile & Wallpaper Trivia & Copilot**: Thu thập điểm thưởng MSN Gaming Tile (+10 đến +50 pts), giải câu đố hình nền Wallpaper Trivia, và tương tác Copilot Chat.
- **Đồng bộ điểm tức thì**: Tự động chạm vào biểu tượng đồng xu Rewards (`#id_rh`) sau khi tìm kiếm để ép máy chủ Bing đồng bộ điểm ngay lập tức.

### 4. Hệ thống bảo vệ phần cứng & Vận hành bền bỉ (System Guards)
- **Zero-Browser Daily Lock**: Lưu trạng thái vào `data/daily_state.json`. Nếu trong ngày đã đạt 100% mục tiêu, bot sẽ tự động thoát ngay lập tức mà không khởi động trình duyệt, giúp bảo vệ tài khoản và ổ cứng SSD.
- **SSD Guard & Auto Cache Purge**: Khống chế bộ nhớ đệm Chromium tối đa 10 MB và tự động quét dọn toàn bộ các thư mục rác (`Cache`, `Code Cache`, `GPUCache`, `DawnCache`, `Crashpad`,...) sau mỗi phiên, bảo vệ tuổi thọ ghi (TBW) của ổ cứng M.2 SSD mà vẫn giữ nguyên Cookies/Session đăng nhập.
- **Process Guard**: Tự động phát hiện và đóng các tiến trình `msedge.exe` bị treo từ phiên trước đang giữ khóa thư mục `profiles/`.
- **Network Guard**: Chờ kết nối Internet ổn định tối đa 60 giây khi máy tính vừa thức dậy từ trạng thái Sleep.
- **Smart Auto-Sleep**: Kiểm tra tương tác chuột/bàn phím qua Windows API `GetLastInputInfo`. Nếu người dùng không sử dụng máy (>120 giây), máy tính sẽ tự động vào trạng thái Sleep (S3) sau khi bot cày xong toàn bộ điểm để tiết kiệm điện.

### 5. Sổ cái thống kê điểm số & Dự báo đổi quà (Rewards Ledger)
- Ghi nhật ký chi tiết vào `data/points_ledger.json` và `logs/history.json`.
- Tự động hiển thị thanh tiến độ trực quan và ước tính số ngày còn lại để đổi các mốc quà phổ biến tại Việt Nam:
  - *Thẻ nạp Roblox (100 Robux - 1,500 pts)*
  - *Thẻ nạp điện thoại Viettel / Mobi / Vina 50,000đ (3,400 pts)*
  - *Voucher Shopee / Tiki / Got It 100,000đ (6,500 pts)*
  - *Xbox Game Pass PC 1 Tháng (7,750 pts)*
  - *Voucher Thế Giới Di Động / Điện Máy XANH 200,000đ (13,000 pts)*

---

## 📁 Cấu trúc thư mục

```
ms-rewards-bot/
├── core/
│   ├── __init__.py             # Xuất các hàm chính của toàn bộ hệ thống
│   ├── activities.py           # Dashboard activities, Daily Set, Quizzes, Punch Cards, Streaks
│   ├── ai_solver.py            # Tự động giải đố trắc nghiệm bằng Local AI (Ollama Qwen 2.5)
│   ├── humanizer.py            # Phân phối Gaussian, mô phỏng gõ phím, trễ sinh học
│   ├── logger.py               # Hệ thống logging chuẩn hóa
│   ├── mobile_app_api.py       # Bing Mobile App OAuth API (Check-in, Read to Earn +30 pts)
│   ├── searcher.py             # Chuột cong Bézier, micro-jitter, CTR click, Cluster Search
│   ├── tracker.py              # Đọc điểm Dashboard, ghi log lịch sử và theo dõi session
│   └── trends.py               # Cào Google Trends RSS (VN) & lọc từ khóa đời sống tiếng Việt
├── data/                       # Dữ liệu trạng thái & lịch sử
│   ├── daily_state.json        # Khóa trạng thái hoàn thành trong ngày (Zero-Browser Lock)
│   ├── points_ledger.json      # Sổ cái tổng hợp điểm tích lũy & dự báo đổi quà
│   └── searched_history.json   # Bộ nhớ lịch sử từ khóa đã tìm kiếm (chống trùng lặp tuyệt đối)
├── logs/
│   ├── bot.log                 # Log chi tiết theo thời gian thực
│   └── history.json            # Lịch sử các phiên chạy và biến động điểm
├── profiles/
│   ├── desktop/                # Persistent Browser Context cho Desktop Edge
│   └── mobile/                 # Persistent Browser Context cho Mobile (Pixel 7 Emulation)
├── requirements.txt            # Danh sách thư viện Python phụ thuộc
├── runner.py                   # Bộ điều phối trung tâm thực thi toàn bộ pipeline
├── run.bat                     # Launcher tự phục hồi môi trường (.venv self-healing launcher)
├── run_task.vbs                # Khởi chạy hoàn toàn ngầm không hiện cửa sổ console
├── setup.bat                   # Trình cài đặt tự động 1-click
├── setup.ps1                   # Kịch bản PowerShell cài đặt trọn gói môi trường & lịch trình
├── setup_login.py              # Giao diện đăng nhập tài khoản lần đầu vào profiles
└── schedule_setup.ps1          # Tự động đăng ký Windows Task Scheduler (chạy ngầm mỗi 4 tiếng)
```

---

## 🚀 Cài đặt nhanh

### 1. Yêu cầu hệ thống
- Hệ điều hành: **Windows 10 / 11** (64-bit).
- **Python 3.9+** (đã tick chọn *"Add python.exe to PATH"* khi cài đặt).
- **Microsoft Edge** đã cài sẵn trên máy (mặc định trên Windows).
- Kết nối Internet ổn định.
- *(Tùy chọn)* [Ollama](https://ollama.com/) với mô hình `qwen2.5:3b` nếu muốn bot tự giải các câu hỏi đố vui trắc nghiệm bằng AI cục bộ:
  ```bash
  ollama pull qwen2.5:3b
  ```

---

### 2. Thiết lập trọn gói bằng 1 cú click (Khuyên dùng)

Chỉ cần nhấp đúp vào file:
```cmd
setup.bat
```
Hoặc mở PowerShell tại thư mục dự án và chạy:
```powershell
.\setup.ps1
```
Kịch bản tự động sẽ thực hiện toàn bộ:
1. Kiểm tra Python trên hệ thống.
2. Khởi tạo môi trường ảo `.venv`.
3. Nâng cấp `pip` và cài đặt dependencies từ `requirements.txt`.
4. Cài đặt driver trình duyệt Playwright Edge (`playwright install msedge`).
5. Khởi tạo sẵn các thư mục `logs/`, `profiles/desktop`, `profiles/mobile`.
6. Khởi chạy giao diện `setup_login.py` để bạn đăng nhập tài khoản Microsoft.
7. Đăng ký Windows Task Scheduler để tự động chạy ngầm mỗi ngày.

---

### 3. Cài đặt thủ công qua dòng lệnh

Nếu muốn tự thiết lập từng bước:

```bash
# Di chuyển vào thư mục bot
cd ms-rewards-bot

# Tạo và kích hoạt môi trường ảo
python -m venv .venv
.\.venv\Scripts\activate

# Cài đặt thư viện phụ thuộc
pip install -r requirements.txt

# Cài đặt browser binary cho Microsoft Edge
playwright install msedge
```

---

## 🔑 Đăng nhập tài khoản (Chỉ thực hiện 1 lần đầu)

Chạy script đăng nhập:
```bash
# Menu tương tác
python setup_login.py

# Hoặc tự động mở lần lượt cả Desktop và Mobile
python setup_login.py --both
```

> [!IMPORTANT]
> **Khi cửa sổ trình duyệt Edge mở ra:**
> 1. Đăng nhập tài khoản Microsoft của bạn tại trang `rewards.bing.com`.
> 2. **Bắt buộc tick chọn "Keep me signed in" / "Duy trì đăng nhập"**.
> 3. Sau khi vào được giao diện chính của Microsoft Rewards, hãy **đóng trình duyệt bằng nút X** góc trên bên phải để lưu session vào thư mục `profiles/`.

---

## 💻 Hướng dẫn vận hành

### 1. Chạy hàng ngày qua launcher thông minh (`run.bat`)
File `run.bat` được tích hợp cơ chế **Self-Healing**: nếu phát hiện thư mục `.venv` bị thiếu, hỏng đường dẫn hoặc bị lệch ổ đĩa, nó sẽ tự động dọn dẹp và tái tạo lại môi trường:
```cmd
# Chạy mặc định ở chế độ ngầm (headless), tự động Sleep sau khi hoàn tất:
run.bat

# Chạy có giao diện trình duyệt để theo dõi trực quan:
run.bat --debug

# Chạy không đưa máy vào chế độ Sleep khi hoàn tất:
run.bat --no-sleep
```

### 2. Chạy trực tiếp qua Python CLI
```bash
# Chạy mặc định (headless, 30 Desktop, 20 Mobile, tự động Sleep)
python runner.py

# Chế độ Debug (hiện giao diện trình duyệt, in log chi tiết)
python runner.py --debug

# Tùy chỉnh số lượt tìm kiếm mục tiêu
python runner.py --desktop-target 30 --mobile-target 20 --batch-size 3

# Chạy mà không tự động Sleep máy tính
python runner.py --no-sleep

# Chế độ Daemon chạy nền liên tục (tự kiểm tra sau mỗi 4 tiếng)
python runner.py --daemon --interval 4
```

### 3. Chạy hoàn toàn ngầm không hiện cửa sổ (Silent Launcher)
Chỉ cần nhấp đúp file:
```cmd
run_task.vbs
```
File này sẽ kích hoạt `runner.py` chạy ẩn hoàn toàn trong nền Windows thông qua Windows Script Host, không hề xuất hiện cửa sổ cmd hay làm gián đoạn công việc của bạn.

---

## ⏰ Lên lịch tự động với Windows Task Scheduler

Bạn có thể để máy tính tự động bật dậy cày điểm mỗi ngày mà không cần chạm tay vào:

1. Mở PowerShell với quyền **Run as Administrator**.
2. Di chuyển đến thư mục bot và chạy:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\schedule_setup.ps1
   ```

**Các đặc tính của tác vụ được tạo:**
- **Tên tác vụ**: `MicrosoftRewardsAutoPilot`
- **Tự động lặp lại**: Mỗi **4 tiếng** một lần.
- **Kích hoạt khi đăng nhập**: Chạy ngay khi bạn vừa đăng nhập vào Windows (`AtLogon`).
- **Độ trễ ngẫu nhiên (Random Delay 10 phút)**: Làm lệch các mốc giờ cố định máy móc, ngụy trang tự nhiên với máy chủ Microsoft.
- **Tự động đánh thức máy (`WakeToRun`)**: Đánh thức máy tính từ chế độ Sleep (nếu phần cứng hỗ trợ).
- **Yêu cầu mạng (`RunOnlyIfNetworkAvailable`)**: Chỉ kích hoạt khi có kết nối mạng Internet.

---

## ⚙️ Chi tiết tham số dòng lệnh (`runner.py`)

| Tham số | Mặc định | Mô tả |
| :--- | :---: | :--- |
| `--debug` | `False` | Chạy có giao diện trình duyệt trực quan (`headless=False`), tắt độ trễ ngẫu nhiên ban đầu để kiểm tra nhanh. |
| `--desktop-target` | `30` | Số lượt tìm kiếm Desktop mục tiêu trong ngày. |
| `--mobile-target` | `20` | Số lượt tìm kiếm Mobile mục tiêu trong ngày. |
| `--batch-size` | `3` | Kích thước batch tìm kiếm mỗi đợt trước khi vào khoảng nghỉ Cooldown 15 phút. |
| `--daemon`, `--loop` | `False` | Bật chế độ daemon chạy nền liên tục, tự động ngủ chu kỳ sau khi hoàn thành. |
| `--interval` | `4.0` | Khoảng thời gian ngủ giữa các chu kỳ kiểm tra của chế độ `--daemon` (tính bằng giờ). |
| `--auto-sleep` | `True` | Tự động đưa máy tính vào chế độ Sleep (S3) khi hoàn thành 100% nhiệm vụ (nếu máy đang rảnh). |
| `--no-sleep` | `False` | Vô hiệu hóa tính năng tự động Sleep sau khi hoàn tất. |

---

## 🧠 Cơ chế kỹ thuật chuyên sâu (Technical Architecture)

```
┌────────────────────────────────────────────────────────────────────────┐
│                        MICROSOFT REWARDS BOT                           │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
       ┌───────────────────────────┴───────────────────────────┐
       ▼                                                       ▼
[Zero-Browser Check]                                  [Process Guard]
Kiểm tra data/daily_state.json                        Dọn sạch tiến trình msedge.exe
Đã hoàn thành 100% hôm nay -> THOÁT NGAY              bị treo giữ khóa profiles/
       │ (nếu chưa hoàn thành)
       ▼
[Network Guard]
Kiểm tra kết nối bing.com:443 (chờ tối đa 60s sau khi thức dậy)
       │
       ▼
[BƯỚC 1: DESKTOP CONTEXT]
├── Đọc điểm khởi đầu & Kiểm tra cảnh báo Anti-Ban
├── Đảm bảo bật Streak Protection
├── Thực hiện Daily Set, Quizzes (tích hợp Local AI Solver) & More Activities
├── Hoàn thành Visual Search Streak (tự tạo ảnh PNG)
├── Thực hiện Mobile App API Check-in (OAuth API) & Đọc báo Read to Earn (+30 pts)
├── Quét & giải Quests, Punch Cards và Keep Earning (tối đa 70 pts)
├── Nhận MSN Daily Gaming Tile, Bing Wallpaper Trivia & Chat Copilot
├── Thu thập tất cả phần thưởng khả dụng (Claim Sweep)
└── Kiểm tra số lượt tìm kiếm PC & Mobile thực tế còn thiếu
       │
       ▼
[BƯỚC 1.5: MOBILE CONTEXT CHECK-IN]
└── Điểm danh trên giao diện Bing Mobile & nhận thưởng
       │
       ▼
[BƯỚC 2: CHU TRÌNH TÌM KIẾM CHỐNG COOLDOWN]
┌──► [Desktop Batch 2-4 câu]
│    - Gõ phím tự nhiên + Chuột Bézier + Micro-jitter
│    - Tương tác Autosuggest & Organic CTR Click
│    - Thỉnh thoảng nối chuỗi Cluster Search liên quan
│    - Chạm đồng xu góc #id_rh đồng bộ điểm
│    - Transition Pause (nghỉ 12-30s chuyển đổi thiết bị)
│
├──► [Mobile Batch 2-4 câu]
│    - Giả lập Pixel 7 + EdgA User-Agent
│    - Vuốt màn hình cảm ứng (Touch swipe)
│    - Chạm đồng xu di động ép máy chủ Bing cộng điểm
│
└──► [Active Edge Browsing Cooldown 15 Phút] (nếu chưa đủ điểm)
     - Duy trì phiên lướt tin tức Bing News ngầm
     - Tích lũy 30 phút nhận thưởng Edge Browsing Streak
     - Lặp lại cho tới khi đủ mục tiêu
       │
       ▼
[BƯỚC 3: TỔNG KẾT & QUẢN TRỊ NĂNG LƯỢNG]
├── Đọc điểm số kết thúc & Cập nhật logs/history.json
├── Ghi nhận hoàn thành 100% vào data/daily_state.json
├── Cập nhật sổ cái & Vẽ bảng tiến độ đổi quà (data/points_ledger.json)
├── Dọn dẹp cache rác profiles (SSD Guard)
└── Kiểm tra GetLastInputInfo: Nếu người dùng không hoạt động -> AUTO SLEEP
```

---

## 📊 Nhật ký, Dữ liệu & Sổ cái điểm thưởng

- **`logs/bot.log`**: Toàn bộ nhật ký hoạt động chi tiết từng mili-giây, lưu trữ cả quá trình gõ phím, xử lý thẻ, giải câu đố, nghỉ cooldown.
- **`logs/history.json`**: Ghi lại lịch sử từng phiên chạy (điểm bắt đầu, điểm kết thúc, số điểm kiếm được, số lượt tìm kiếm thành công).
- **`data/points_ledger.json`**: Sổ cái tổng hợp tích lũy điểm theo ngày, dùng để phân tích tốc độ cày điểm trung bình và vẽ biểu đồ dự báo mốc đổi quà tiếp theo.
- **`data/searched_history.json`**: Lưu trữ 200+ từ khóa tìm kiếm gần nhất để đảm bảo không bao giờ tìm kiếm lặp lại một từ khóa trong thời gian ngắn.

---

## 🐛 Khắc phục sự cố thường gặp (Troubleshooting)

### 1. Lỗi "Target page, context or browser has been closed" / Lock Profile
- Nguyên nhân: Có một tiến trình Microsoft Edge chạy ngầm từ trước đang giữ khóa thư mục `profiles/`.
- Cách xử lý: Bot đã tích hợp sẵn **Process Guard** để tự động tắt các tiến trình này. Nếu muốn tắt thủ công:
  ```cmd
  taskkill /F /IM msedge.exe
  ```

### 2. Mất kết nối Internet khi máy tính vừa thức dậy từ chế độ Sleep
- Bot đã tích hợp sẵn **Network Guard**: tự động đệm chờ tối đa 60 giây để card mạng / Wi-Fi kết nối lại ổn định rồi mới mở trình duyệt. Bạn không cần can thiệp.

### 3. Bị Microsoft Rewards cảnh báo giới hạn tìm kiếm (Safe Throttle)
- Khi phát hiện thông báo tốc độ tìm kiếm bất thường, bot sẽ tự động bật **Khiên Anti-Ban** và chuyển sang chế độ **Safe Throttle** (giãn cách 35-60 giây giữa mỗi câu tìm kiếm) để giữ an toàn tuyệt đối cho tài khoản.

### 4. Không đăng ký được Task Scheduler
- Hãy đảm bảo bạn mở PowerShell bằng cách nhấp chuột phải và chọn **"Run as Administrator"** trước khi chạy `.\schedule_setup.ps1`.

---

## ⚠️ Lưu ý quan trọng & Miễn trừ trách nhiệm

1. **Chạy duy nhất một phiên**: Không mở trình duyệt Edge bằng tay trên cùng profile khi bot đang hoạt động để tránh xung đột khóa dữ liệu.
2. **Địa chỉ IP**: Nên sử dụng địa chỉ IP mạng gia đình (Residential IP). Tránh sử dụng VPN / Proxy trung tâm dữ liệu vì dễ bị hệ thống bảo mật Microsoft gắn cờ rủi ro.
3. **Mục đích sử dụng**: Dự án này được phát triển phục vụ mục đích học tập, nghiên cứu tự động hóa trình duyệt và kỹ thuật ngụy trang chống phát hiện với Playwright. Người dùng tự chịu trách nhiệm đối với tài khoản Microsoft của mình.

---

**Chúc bạn tích lũy thật nhiều điểm thưởng Microsoft Rewards mỗi ngày! 🎁**
