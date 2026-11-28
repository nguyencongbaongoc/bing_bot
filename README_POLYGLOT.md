# `ms-rewards-bot` — Phase 9.0 Polyglot Architecture

Kiến trúc **3 tầng tách biệt (Native Windows, không Docker)** thay thế hoàn toàn mô hình đơn tiến trình Python cũ:

1. **Tầng 1 — Go Kernel Supervisor (`supervisor/` → `rewards-supervisor.exe`):**
   - **Windows Named Mutex** (`Global\MSRewardsBot_<account>` / `Local\...`): loại bỏ vĩnh viễn lỗi file `.lock` / `pid` ma.
   - **Windows Job Object** (`TerminateJobObject` + `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`): giam toàn bộ tiến trình con/cháu (`python.exe`, `node.exe`, `msedge.exe`, `crashpad`) và tiêu diệt sạch ở tầng kernel khi vượt ngưỡng timeout (`120s` / `150s` / `30s`).
   - **Circadian Guard** (`07:00 - 22:30`), **Startup Jitter** (`15-30s`), **Pre-run Orphan Guard**, **Network Guard** (`www.bing.com:443`).
2. **Tầng 2 — Python API & State Brain (`core/` + `data/rewards.db`):**
   - Thuần HTTP (`requests` + `asyncio.sleep`), **không** import trình duyệt.
   - SQLite chế độ `WAL` (`data/rewards.db`): lưu `daily_progress`, `query_history`, `session_logs`; giữ `bing_star_points` đơn điệu (`MAX(old, new)`).
   - **Query Engine**: lọc `Jaccard >= 0.35` và trùng chủ đề gốc so với 15 câu gần nhất, ép xoay vòng tối thiểu 3 chủ đề/batch 3 câu.
   - **Stateless Burst** (`exit_on_cooldown = True` mặc định): mỗi phiên tối đa 3 câu PC + 3 câu Mobile, xong thoát sạch để Task Scheduler chạy nhịp kế tiếp.
3. **Tầng 3 — TypeScript Browser Worker (`browser-worker/` → `browser-worker/dist/index.js`):**
   - Node + `patchright` (`channel: "msedge"`), tái sử dụng `profiles/<account>/{desktop,mobile}`.
   - Cấm tuyệt đối `waitForTimeout`; mọi delay dùng `node:timers/promises` + `AbortSignal.timeout`.
   - **Mobile Search**: tắt hoàn toàn Mode 2 Autocomplete, chỉ focus `#sb_form_q` → gõ → `Enter`.
   - **Visual Search**: chạy cửa sổ ngắn hạn (`headless=false`, strip GPU flags), lắng nghe song song URL/request trong 7s ở Tier 1 để **cấm** chạy đè Tier 2/3 khi Tier 1 đã gửi thành công.

---

## Lệnh build lại từ source

### 1. Build Go Kernel Supervisor (`rewards-supervisor.exe`)
```powershell
cd supervisor
go test -v ./...
go build -o ..\rewards-supervisor.exe .
```

### 2. Build TypeScript Browser Worker (`browser-worker/dist/`)
```powershell
cd browser-worker
npm install
npm run build
```

### 3. Kiểm tra Python Core & Unit Tests
```powershell
.\.venv\Scripts\python.exe -m py_compile core\*.py
python -m pytest tests\test_query_engine.py -v
```

---

## Lệnh chạy thủ công

- **Chạy chuẩn (theo Circadian Guard + Startup Jitter):**
  ```powershell
  .\rewards-supervisor.exe --account default
  ```
- **Chạy ngay ngoài khung giờ `07:00 - 22:30` và bỏ qua Jitter:**
  ```powershell
  .\rewards-supervisor.exe --account default --force-hours --skip-jitter
  ```
- **Dry-run (chỉ chạy Tầng 1 + Tầng 2 sinh `data/batch_plan.json`, không mở trình duyệt):**
  ```powershell
  .\rewards-supervisor.exe --account default --force-hours --skip-jitter --dry-run
  ```
- **Tắt Stateless Burst (không thoát mã 20 khi chạm Cooldown):**
  ```powershell
  .\rewards-supervisor.exe --account default --no-exit-on-cooldown
  ```

---

## Cập nhật Windows Task Scheduler

Chạy PowerShell với quyền **Administrator**:
```powershell
powershell -ExecutionPolicy Bypass -File "C:\Users\Nguyen Cong Thong\Desktop\ms-rewards-bot\scripts\register_task.ps1"
```

---

## Mã thoát chuẩn (Contract 3)

| Mã thoát | Ý nghĩa |
| :---: | :--- |
| `0` | Thành công / Đã hoàn tất điểm trong ngày |
| `10` | Ngoài khung giờ Circadian (`07:00 - 22:30`) |
| `11` | Đang có phiên khác giữ Windows Named Mutex |
| `20` | Chạm Cooldown (Stateless Burst thoát có chủ đích) |
| `30` | Worker vượt ngưỡng timeout và đã bị `TerminateJobObject` tiêu diệt |
| `1` | Lỗi hệ thống / cấu hình / mất mạng |
