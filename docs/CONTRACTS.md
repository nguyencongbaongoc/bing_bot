# CONTRACTS — PHASE 9.0 POLYGLOT ARCHITECTURE (`ms-rewards-bot`)

Tài liệu này chốt cứng các giao ước dữ liệu, đường dẫn tài khoản và mã thoát chuẩn giữa 3 tầng:
- **Tầng 1 (Go Kernel Supervisor):** `rewards-supervisor.exe` (`supervisor/`)
- **Tầng 2 (Python API & State Brain):** `core/` + SQLite WAL (`data/rewards.db`)
- **Tầng 3 (TypeScript Browser Worker):** `browser-worker/dist/index.js` (`browser-worker/src/`)

---

## 0. Đường dẫn & Tài nguyên bảo toàn (Trích xuất từ Phase 0)

- **Thư mục Profiles Edge (bảo toàn 100%, không xóa/ghi đè):**
  - Root: `profiles/<account>` (mặc định `default`, trên Windows case-insensitive ánh xạ tới `profiles\Default`)
  - Desktop profile: `profiles/<account>/desktop`
  - Mobile profile: `profiles/<account>/mobile`
- **Mobile OAuth Token Cache:**
  - `default`: `data/mobile_oauth.json`
  - Account khác: `data/mobile_oauth_<account>.json`
  - `CLIENT_ID = "0000000040170455"`, `SCOPE = "service::prod.rewardsplatform.microsoft.com::MBI_SSL"`
- **SQLite State Database (chế độ WAL):**
  - `data/rewards.db` (`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;`)
- **Ảnh cục bộ Visual Search:**
  - Nguồn: `Screenshots/` (các file `.png`/`.jpg`), fallback `assets/visual_search_images/`, `assets/`
  - Ảnh chuẩn hóa JPEG tạm: `data/temp_vs_upload.jpg` (520–640px, quality 82–94)
- **Selectors ổn định đã xác minh:**
  - **Claim xu / Bing Star popup:** `[role="dialog"] button:has-text("Claim points")`, `button:has-text("Claim points")`, `[role="dialog"] button:has-text("Claim")`, `button:has-text("Ready to claim")`, `a:has-text("Ready to claim")`
  - **Streak Protection:** `button[role="switch"][aria-label*="streak protection" i]`, `button[role="switch"][aria-label*="bảo vệ chuỗi" i]`, `div:has-text("Streak protection") button[role="switch"]`
  - **Dashboard /earn & 5 thẻ cốt lõi:** `[role="progressbar"]` (`Bing Search`, `Daily Set`, `Edge Browsing`, `Mobile App`, `Visual Search`), `section#moreactivities` (Keep Earning), `a[href*="/earn/quest/"]` (Universal Quest Resolver)
  - **Search Input (Desktop & Mobile):** `#sb_form_q` (Mobile chỉ dùng gõ trực tiếp `#sb_form_q` + `Enter`, tắt hoàn toàn Autocomplete)
  - **Visual Search:** `#sb_sbip`, `#sbi_b`, `#sb_sbipane`, `#sb_brs`, `label[for="sb_fileinput"]`, `#sb_fileinput`

---

## 1. Contract 1 — `data/batch_plan.json` (Python → TypeScript)

File được Python Planner ghi nguyên tử (write `.tmp` → `fsync` → `os.replace`) trước khi Go khởi chạy TS Browser Worker.

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-11-27T19:40:00+07:00",
  "date_key": "2026-11-27",
  "account": "default",
  "profile_dir": "C:\\Users\\Nguyen Cong Thong\\Desktop\\ms-rewards-bot\\profiles\\default",
  "desktop_profile_dir": "C:\\Users\\Nguyen Cong Thong\\Desktop\\ms-rewards-bot\\profiles\\default\\desktop",
  "mobile_profile_dir": "C:\\Users\\Nguyen Cong Thong\\Desktop\\ms-rewards-bot\\profiles\\default\\mobile",
  "headless": true,
  "exit_on_cooldown": true,
  "has_browser_tasks": true,
  "run_activities": true,
  "run_visual_search": true,
  "visual_search_image_path": "C:\\Users\\Nguyen Cong Thong\\Desktop\\ms-rewards-bot\\data\\temp_vs_upload.jpg",
  "pc_queries": ["query 1", "query 2", "query 3"],
  "mobile_queries": ["query 4", "query 5", "query 6"],
  "ctr_rate": 0.25,
  "dwell_ms_range": [3000, 5000],
  "known_points_before": 5447,
  "known_bing_star_before": 0,
  "attempted_offers": [],
  "attempted_quest_tasks": []
}
```

### Quy tắc bất biến của Contract 1:
- `pc_queries`: tối đa **3** câu; `mobile_queries`: tối đa **3** câu.
- Mọi câu trong `pc_queries` và `mobile_queries` đều phải qua bộ lọc **Jaccard < 0.35** (so với 15 câu gần nhất trong `query_history` và trong cùng batch) và xoay vòng ít nhất `min(len, 3)` chủ đề khác nhau.
- Nếu `has_browser_tasks === false`, Go bỏ qua hoàn toàn bước chạy TS Browser Worker và thoát `0`.

---

## 2. Contract 2 — `data/worker_result.json` (TypeScript → Python)

File luôn được TS Browser Worker ghi nguyên tử trong khối `finally` trước khi thoát (hoặc do Go tạo/cập nhật khi `TerminateJobObject` xảy ra).

```json
{
  "schema_version": "1.0",
  "finished_at": "2026-11-27T19:42:10+07:00",
  "account": "default",
  "status": "ok",
  "activities_done": true,
  "daily_set_done": true,
  "keep_earning_done": true,
  "edge_browsing_done": true,
  "visual_search_done": true,
  "visual_search_attempted": true,
  "pc_completed": 3,
  "mobile_completed": 3,
  "pc_queries_used": ["query 1", "query 2", "query 3"],
  "mobile_queries_used": ["query 4", "query 5", "query 6"],
  "pc_cur": 90,
  "pc_max": 90,
  "mobile_cur": 60,
  "mobile_max": 60,
  "points_before": 5447,
  "points_after": 5465,
  "bing_star_points": 3,
  "cooldown_detected": false,
  "attempted_offers_added": [],
  "attempted_quest_tasks_added": [],
  "errors": []
}
```

### Giá trị hợp lệ của `status`:
- `"ok"`: Hoàn tất các tác vụ trong batch không có lỗi nghiêm trọng.
- `"partial"`: Hoàn thành một phần (ví dụ gặp lỗi ở 1 câu search nhưng vẫn lưu kết quả phần đã xong).
- `"timeout"`: Worker vượt quá giới hạn thời gian và bị Go Job Object tiêu diệt (`exit code 30`).
- `"error"`: Lỗi khởi tạo trình duyệt, mất đăng nhập hoặc lỗi hệ thống.

---

## 3. Contract 3 — Exit Codes chuẩn giữa 3 tầng

| Exit Code | Ý nghĩa | Tầng phát sinh | Hành động xử lý |
| :---: | :--- | :--- | :--- |
| **`0`** | Thành công / Đã hoàn thành hết điểm trong ngày | Go / Python / TS | Kết thúc phiên bình thường |
| **`10`** | Ngoài khung giờ Circadian (`07:00 - 22:30`) | Go Supervisor | Tạm hoãn, không mở Python/Node/Edge |
| **`11`** | Đang có phiên khác giữ Windows Named Mutex | Go Supervisor | Thoát ngay, không đụng profile đang chạy |
| **`20`** | Chạm Cooldown (Stateless Burst thoát sạch có chủ đích) | Python Ingester → Go | Thoát sạch để Task Scheduler chạy lượt sau |
| **`30`** | Worker vượt ngưỡng timeout và đã bị `TerminateJobObject` tiêu diệt | Go Supervisor | Ghi nhận timeout, toàn bộ cây tiến trình đã bị diệt ở kernel |
| **`1`** | Lỗi hệ thống / cấu hình / mất mạng | Go / Python / TS | Ghi log lỗi và kết thúc |
