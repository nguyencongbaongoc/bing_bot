# Graph Report - ms-rewards-bot  (2026-11-12)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 296 nodes · 720 edges · 41 communities (8 shown, 33 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 9 edges (avg confidence: 0.84)
- Token cost: 2,993 input · 443 output

## Graph Freshness
- Built from commit: `e570f34e`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Microsoft Rewards Activity Automation
- Mobile API and Logging
- Anti-Detection Fingerprinting
- Human Behavior Simulation
- Login and Inspection Tools
- Human-Like Search Automation
- AI Quiz Solver
- PNG Image Generation
- OmniRoute AI Gateway
- AI Attendance Dashboard
- AI Attendance Dashboard
- AI Attendance Management
- AI Provider Monitoring
- Gemini API Free Tier Limits
- Gemini API Rate Limit Details
- Gemini API Rate Limits
- Google AI Studio Projects
- Google AI Studio Projects
- Shopping and Budgeting
- Vietnamese Women's Day Page
- UI: AI Attendance - Live Monitoring Line Tool
- UI: AI Attendance - Live Monitoring Line Tool (Single Cam)
- Google Drive File List
- AI Attendance Monitoring
- AI Attendance Monitoring
- Aquaculture Presentation Slides
- Aquaculture Presentation Slides
- AI Attendance Dashboard
- AI Attendance Configuration
- Windows Script Errors
- Microsoft Rewards Search
- Microsoft Rewards Quests
- Bing Search Results
- Rewards Bot Development
- Rewards Bot Errors
- Microsoft Rewards Dashboard
- Microsoft Rewards Status
- Microsoft Rewards Activities
- Microsoft Rewards Points

## God Nodes (most connected - your core abstractions)
1. `run_full_pipeline()` - 48 edges
2. `_log_info()` - 28 edges
3. `_log_warning()` - 23 edges
4. `dismiss_popups()` - 16 edges
5. `solve_mobile_app_promotions()` - 15 edges
6. `_log_debug()` - 15 edges
7. `_log_error()` - 14 edges
8. `solve_keep_earning_activities()` - 13 edges
9. `get_mobile_access_token()` - 13 edges
10. `solve_mobile_app_read_to_earn()` - 13 edges

## Surprising Connections (you probably didn't know these)
- `run_full_pipeline()` --calls--> `solve_mobile_app_daily_checkin()`  [EXTRACTED]
  runner.py → core/mobile_app_api.py
- `run_full_pipeline()` --calls--> `solve_mobile_app_promotions()`  [EXTRACTED]
  runner.py → core/mobile_app_api.py
- `run_full_pipeline()` --calls--> `solve_mobile_app_read_to_earn()`  [EXTRACTED]
  runner.py → core/mobile_app_api.py
- `run_full_pipeline()` --calls--> `extract_current_points()`  [EXTRACTED]
  runner.py → core/tracker.py
- `run_full_pipeline()` --calls--> `finish_session()`  [EXTRACTED]
  runner.py → core/tracker.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **AI Attendance System Interface** — screenshots_screenshot_2026_10_17_142527, screenshots_screenshot_2026_10_18_013451, screenshots_screenshot_2026_10_18_013840, screenshots_screenshot_2026_10_18_013956, screenshots_screenshot_2026_10_18_222537, screenshots_screenshot_2026_10_19_045148 [EXTRACTED 1.00]
- **Google AI Studio Resource Management** — screenshots_screenshot_2026_10_17_221236, screenshots_screenshot_2026_10_17_221259, screenshots_screenshot_2026_10_17_221655, screenshots_screenshot_2026_10_17_221935 [EXTRACTED 1.00]

## Communities (41 total, 33 thin omitted)

### Community 0 - "Microsoft Rewards Activity Automation"
Cohesion: 0.07
Nodes (27): activate_mobile_app_card(), claim_all_available_rewards(), claim_msn_daily_gaming_tile(), detect_unusual_activity_warning(), dismiss_popups(), ensure_streak_protection(), extract_bing_star_bonus(), get_daily_search_progress() (+19 more)

### Community 1 - "Mobile API and Logging"
Cohesion: 0.09
Nodes (26): _activity_awarded_points(), _build_sapphire_headers(), _extract_balance(), _extract_checkin_day(), _extract_promotion_id(), _get_balance(), _get_checkin_promotion(), get_mobile_access_token() (+18 more)

### Community 2 - "Anti-Detection Fingerprinting"
Cohesion: 0.06
Nodes (15): generate_seed_hash(), get_deterministic_hardware_profile(), inject_stealth_fingerprint(), extract_current_points(), apply_stealth_scripts(), cleanup_zombie_browsers(), create_desktop_context(), create_mobile_context() (+7 more)

### Community 3 - "Human Behavior Simulation"
Cohesion: 0.10
Nodes (11): cooldown_sleep(), gaussian_delay(), human_read_delay(), human_scroll_delay(), maybe_click_result(), random_viewport_offset(), typing_delay(), fetch_trending_queries() (+3 more)

### Community 4 - "Login and Inspection Tools"
Cohesion: 0.14
Nodes (8): run_inspection(), apply_stealth_scripts(), get_mobile_device_choice(), launch_desktop_login(), launch_mobile_login(), main(), print_banner(), print_menu()

### Community 5 - "Human-Like Search Automation"
Cohesion: 0.19
Nodes (9): execute_single_search(), human_bezier_move(), human_type_and_search(), _perform_post_search_dwell_and_ctr(), perform_related_cluster_search(), simulate_organic_click(), smooth_scroll(), save_searched_keyword() (+1 more)

### Community 6 - "AI Quiz Solver"
Cohesion: 0.18
Nodes (5): ask_local_ai(), ensure_ollama_service(), solve_quiz_with_ai(), active_edge_browsing_cooldown(), put_pc_to_sleep()

### Community 9 - "OmniRoute AI Gateway"
Cohesion: 0.67
Nodes (3): OmniRoute AI Gateway, OmniRoute AI Gateway log detail showing 502 error, GitHub pull requests for OmniRoute repository

## Knowledge Gaps
- **34 isolated node(s):** `UI: AI Attendance - Live Monitoring View`, `UI: AI Attendance - Dashboard Overview`, `UI: AI Attendance - Debug Console Errors`, `UI: AI Attendance - Dashboard with Recent Records`, `UI: AI Attendance Control Center - ArcFace/NPY Management` (+29 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 148 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **33 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `run_full_pipeline()` connect `Microsoft Rewards Activity Automation` to `Mobile API and Logging`, `Anti-Detection Fingerprinting`, `Human Behavior Simulation`, `Human-Like Search Automation`, `AI Quiz Solver`?**
  _High betweenness centrality (0.045) - this node is a cross-community bridge._
- **What connects `UI: AI Attendance - Live Monitoring View`, `UI: AI Attendance - Dashboard Overview`, `UI: AI Attendance - Debug Console Errors` to the rest of the system?**
  _34 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Microsoft Rewards Activity Automation` be split into smaller, more focused modules?**
  _Cohesion score 0.0744047619047619 - nodes in this community are weakly interconnected._
- **Why does `_log_info()` connect `Mobile API and Logging` to `Microsoft Rewards Activity Automation`, `Anti-Detection Fingerprinting`, `AI Quiz Solver`?**
  _High betweenness centrality (0.020) - this node is a cross-community bridge._
- **Should `Mobile API and Logging` be split into smaller, more focused modules?**
  _Cohesion score 0.09411764705882353 - nodes in this community are weakly interconnected._
- **Why does `solve_mobile_app_daily_checkin()` connect `Mobile API and Logging` to `Microsoft Rewards Activity Automation`, `Anti-Detection Fingerprinting`, `AI Quiz Solver`?**
  _High betweenness centrality (0.017) - this node is a cross-community bridge._
- **Should `Anti-Detection Fingerprinting` be split into smaller, more focused modules?**
  _Cohesion score 0.06262626262626263 - nodes in this community are weakly interconnected._