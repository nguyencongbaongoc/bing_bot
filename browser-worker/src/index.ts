import fs from "node:fs";
import path from "node:path";
import type { BrowserContext, Page } from "patchright";
import { readDailyProgress, runActivitiesFlow } from "./activities.js";
import { launchDesktopContext, launchMobileContext } from "./browser.js";
import { runDesktopSearch, runMobileSearch } from "./search.js";
import type { BatchPlan, WorkerResult } from "./types.js";
import { atomicWriteJson, log, safeCloseContext, sleep } from "./utils.js";
import { runVisualSearch } from "./visual_search.js";

function parseCliArgs(argv: string[]): { planPath: string; outPath: string } {
  let planPath = path.resolve("data", "batch_plan.json");
  let outPath = path.resolve("data", "worker_result.json");
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--plan" && argv[i + 1]) planPath = path.resolve(argv[++i]);
    else if (argv[i] === "--out" && argv[i + 1]) outPath = path.resolve(argv[++i]);
  }
  return { planPath, outPath };
}

async function main(): Promise<void> {
  const { planPath, outPath } = parseCliArgs(process.argv.slice(2));
  if (!fs.existsSync(planPath)) {
    log(`Missing batch plan file: ${planPath}`);
    process.exit(1);
  }

  const plan = JSON.parse(fs.readFileSync(planPath, "utf-8")) as BatchPlan;
  const result: WorkerResult = {
    schema_version: "1.0",
    finished_at: new Date().toISOString(),
    account: plan.account || "default",
    status: "ok",
    activities_done: !plan.run_activities,
    daily_set_done: !plan.run_activities,
    keep_earning_done: !plan.run_activities,
    edge_browsing_done: false,
    visual_search_done: false,
    visual_search_attempted: false,
    pc_completed: 0,
    mobile_completed: 0,
    pc_queries_used: [],
    mobile_queries_used: [],
    pc_cur: -1,
    pc_max: 90,
    mobile_cur: -1,
    mobile_max: 60,
    points_before: plan.known_points_before || 0,
    points_after: plan.known_points_before || 0,
    bing_star_points: plan.known_bing_star_before || 0,
    cooldown_detected: false,
    attempted_offers_added: [],
    attempted_quest_tasks_added: [],
    errors: [],
  };

  let desktopCtx: BrowserContext | null = null;
  let desktopPage: Page | null = null;
  let mobileCtx: BrowserContext | null = null;
  let exitCode = 0;

  try {
    // 1. Activities (headless Desktop)
    if (plan.run_activities) {
      const launched = await launchDesktopContext(plan.desktop_profile_dir, plan.headless, plan.account);
      desktopCtx = launched.context;
      desktopPage = launched.page;
      await runActivitiesFlow(desktopCtx, desktopPage, plan, result);
      if (plan.run_visual_search && !result.visual_search_done) {
        // Close headless desktop context before launching headed Visual Search window on same profile
        await safeCloseContext(desktopCtx, 5000);
        desktopCtx = null;
        desktopPage = null;
      }
    }

    // 2. Visual Search (short-lived headed window, max 1 attempt)
    if (plan.run_visual_search && !result.visual_search_done) {
      await runVisualSearch(plan, result);
    }

    // 3. Desktop Search
    if ((plan.pc_queries || []).length > 0) {
      if (!desktopCtx || !desktopPage || desktopPage.isClosed()) {
        const launched = await launchDesktopContext(plan.desktop_profile_dir, plan.headless, plan.account);
        desktopCtx = launched.context;
        desktopPage = launched.page;
      }
      await runDesktopSearch(desktopCtx, desktopPage, plan, result);
    }

    // Read updated dashboard counters if desktop context is open and searches ran
    if (desktopCtx && desktopPage && !desktopPage.isClosed() && (plan.pc_queries || []).length > 0) {
      const snap = await readDailyProgress(desktopPage, result.points_before);
      if (snap.found_pc) {
        result.pc_cur = snap.pc_cur;
        result.pc_max = snap.pc_max;
      } else {
        result.pc_cur = -1;
      }
      if (snap.found_mobile) {
        result.mobile_cur = snap.mobile_cur;
        result.mobile_max = snap.mobile_max;
      }
      if (snap.balance && snap.balance > 0) {
        result.points_after = snap.balance;
      }
      result.bing_star_points = Math.max(result.bing_star_points, snap.bing_star_points);
      result.edge_browsing_done = result.edge_browsing_done || snap.edge_browsing_done;
      result.visual_search_done = result.visual_search_done || snap.visual_search_done;
    }

    await safeCloseContext(desktopCtx, 5000);
    desktopCtx = null;
    desktopPage = null;

    // 4. Mobile Search (separate profile & mobile emulation, Autocomplete disabled)
    if ((plan.mobile_queries || []).length > 0) {
      if ((plan.pc_queries || []).length > 0) {
        await sleep(3000);
      }
      const mLaunched = await launchMobileContext(plan.mobile_profile_dir, plan.headless, plan.account);
      mobileCtx = mLaunched.context;
      await runMobileSearch(mobileCtx, mLaunched.page, plan, result);
      await safeCloseContext(mobileCtx, 5000);
      mobileCtx = null;
    }

    const totalCompleted = result.pc_completed + result.mobile_completed;
    const totalPlanned = (plan.pc_queries || []).length + (plan.mobile_queries || []).length;
    if (totalPlanned > 0 && totalCompleted < totalPlanned && result.status === "ok") {
      result.status = totalCompleted > 0 ? "partial" : "error";
    }
    if (totalCompleted > 0 && result.points_after <= result.points_before) {
      result.cooldown_detected = true;
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    log(`Fatal worker error: ${msg}`);
    result.errors.push(msg);
    result.status = result.pc_completed + result.mobile_completed > 0 ? "partial" : "error";
    exitCode = result.status === "error" ? 1 : 0;
  } finally {
    await safeCloseContext(desktopCtx, 5000);
    await safeCloseContext(mobileCtx, 5000);
    result.finished_at = new Date().toISOString();
    atomicWriteJson(outPath, result);
    log(`Saved worker_result.json (status=${result.status}, pc=${result.pc_completed}, mobile=${result.mobile_completed})`);
    process.exit(exitCode);
  }
}

void main();
