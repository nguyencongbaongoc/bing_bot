export interface BatchPlan {
  schema_version: string;
  generated_at: string;
  date_key: string;
  account: string;
  profile_dir: string;
  desktop_profile_dir: string;
  mobile_profile_dir: string;
  headless: boolean;
  exit_on_cooldown: boolean;
  has_browser_tasks: boolean;
  run_activities: boolean;
  run_visual_search: boolean;
  visual_search_image_path: string | null;
  pc_queries: string[];
  mobile_queries: string[];
  ctr_rate: number;
  dwell_ms_range: [number, number];
  known_points_before: number;
  known_bing_star_before: number;
  attempted_offers: string[];
  attempted_quest_tasks: string[];
}

export type WorkerStatus = "ok" | "partial" | "timeout" | "error";

export interface WorkerResult {
  schema_version: string;
  finished_at: string;
  account: string;
  status: WorkerStatus;
  activities_done: boolean;
  daily_set_done: boolean;
  keep_earning_done: boolean;
  edge_browsing_done: boolean;
  visual_search_done: boolean;
  visual_search_attempted: boolean;
  pc_completed: number;
  mobile_completed: number;
  pc_queries_used: string[];
  mobile_queries_used: string[];
  pc_cur: number;
  pc_max: number;
  mobile_cur: number;
  mobile_max: number;
  points_before: number;
  points_after: number;
  bing_star_points: number;
  cooldown_detected: boolean;
  attempted_offers_added: string[];
  attempted_quest_tasks_added: string[];
  errors: string[];
}

export interface DailyProgressSnapshot {
  found: boolean;
  found_pc: boolean;
  found_mobile: boolean;
  pc_cur: number;
  pc_max: number;
  mobile_cur: number;
  mobile_max: number;
  balance: number | null;
  bing_star_points: number;
  daily_set_done: boolean;
  edge_browsing_done: boolean;
  visual_search_done: boolean;
  bing_search_done: boolean;
  all_maxed: boolean;
  scrape_error: boolean;
}
