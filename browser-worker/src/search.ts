import type { BrowserContext, Page } from "patchright";
import { dismissPopups, extractCurrentPoints } from "./activities.js";
import type { BatchPlan, WorkerResult } from "./types.js";
import { isClosedOrCrashError, log, randFloat, randInt, sleep, withTimeout } from "./utils.js";

const BING_HOME = "https://www.bing.com/";

async function humanType(page: Page, text: string): Promise<void> {
  for (const ch of text) {
    await page.keyboard.type(ch, { delay: randInt(35, 75) });
    if (ch === " ") {
      await sleep(randInt(110, 210));
    }
  }
}

async function performPostSearchDwellAndCtr(
  context: BrowserContext,
  page: Page,
  ctrRate: number,
  dwellRange: [number, number]
): Promise<void> {
  try {
    await page.mouse.wheel(0, randInt(240, 480)).catch(() => {});
    await sleep(randInt(700, 1200));

    if (Math.random() < ctrRate) {
      const links = page.locator("#b_results h2 a, #b_results .b_algo a");
      const count = await links.count().catch(() => 0);
      if (count > 0) {
        const target = links.nth(randInt(0, Math.min(2, count - 1)));
        if (await target.isVisible({ timeout: 700 }).catch(() => false)) {
          const pagesBefore = context.pages().length;
          const serpUrl = page.url();
          await target.click({ timeout: 3000 }).catch(() => {});
          const dwellMs = randInt(dwellRange[0], dwellRange[1]);
          await sleep(dwellMs);
          const pagesAfter = context.pages();
          if (pagesAfter.length > pagesBefore) {
            const tab = pagesAfter[pagesAfter.length - 1];
            await tab.mouse.wheel(0, randInt(200, 400)).catch(() => {});
            await sleep(800);
            await tab.close().catch(() => {});
          } else if (page.url() !== serpUrl) {
            await page.goto(serpUrl, { waitUntil: "commit", timeout: 10000 }).catch(() => {});
            await sleep(700);
          }
        }
      }
    }
  } catch {
    // ignore CTR failures
  }
}

async function waitForSerpReady(page: Page, timeoutMs: number): Promise<void> {
  const resultsLoc = page.locator("#b_results");
  await Promise.race([
    resultsLoc.waitFor({ state: "visible", timeout: timeoutMs }),
    page.waitForURL(/bing\.com\/search\?/i, { timeout: timeoutMs }),
  ]).catch(() => {});
}

async function executeDesktopQuery(
  context: BrowserContext,
  page: Page,
  query: string,
  plan: BatchPlan
): Promise<void> {
  const curUrl = page.url().toLowerCase();
  if (!curUrl.includes("bing.com") || curUrl.includes("rewards.bing.com")) {
    await page.goto(BING_HOME, { waitUntil: "commit", timeout: 15000 });
    await sleep(1000);
  }
  await dismissPopups(page);

  const searchBox = page.locator("#sb_form_q, textarea[name='q'], input[name='q']").first();
  await searchBox.waitFor({ state: "visible", timeout: 8000 });
  await searchBox.click({ timeout: 3000 });
  await sleep(randInt(150, 350));
  await page.keyboard.press("Control+A");
  await page.keyboard.press("Backspace");
  await searchBox.fill("").catch(() => {});

  await humanType(page, query);
  await sleep(randInt(350, 700));
  await page.keyboard.press("Enter");
  await waitForSerpReady(page, 20000);
  await sleep(randInt(1200, 2000));

  await performPostSearchDwellAndCtr(context, page, plan.ctr_rate, plan.dwell_ms_range);
}

/**
 * Mobile Search (Fix #2):
 * Mode 2 (Autocomplete) is COMPLETELY DISABLED.
 * Only direct focus #sb_form_q -> clear -> type query -> Enter -> wait for SERP (#b_results / URL).
 */
async function executeMobileQuery(
  context: BrowserContext,
  page: Page,
  query: string,
  plan: BatchPlan
): Promise<void> {
  const curUrl = page.url().toLowerCase();
  if (!curUrl.includes("bing.com") || curUrl.includes("rewards.bing.com")) {
    await page.goto(BING_HOME, { waitUntil: "commit", timeout: 15000 });
    await sleep(1000);
  }
  await dismissPopups(page);

  const searchBox = page.locator("#sb_form_q").first();
  await searchBox.waitFor({ state: "visible", timeout: 8000 });
  await searchBox.click({ timeout: 3000 });
  await sleep(randInt(150, 300));
  await page.keyboard.press("Control+A");
  await page.keyboard.press("Backspace");
  await searchBox.fill("").catch(() => {});

  await humanType(page, query);
  await sleep(randInt(300, 600));
  await page.keyboard.press("Enter");

  await waitForSerpReady(page, 25000);
  await sleep(randInt(1200, 1800));

  await performPostSearchDwellAndCtr(context, page, plan.ctr_rate, plan.dwell_ms_range);
}

export async function runDesktopSearch(
  context: BrowserContext,
  page: Page,
  plan: BatchPlan,
  result: WorkerResult
): Promise<void> {
  const queries = (plan.pc_queries || []).slice(0, 3);
  if (queries.length === 0) return;

  log(`[PC Search] Running ${queries.length} queries...`);
  for (let i = 0; i < queries.length; i++) {
    const q = queries[i];
    log(`[PC Search ${i + 1}/${queries.length}] "${q}"`);
    try {
      await withTimeout(executeDesktopQuery(context, page, q, plan), 30000, `pc-query-${i + 1}`);
      result.pc_completed++;
      result.pc_queries_used.push(q);
      if (i < queries.length - 1) {
        await sleep(randFloat(3500, 5500));
      }
    } catch (err) {
      const msg = `PC query '${q}' failed: ${err instanceof Error ? err.message : String(err)}`;
      log(`[PC Search] ${msg}`);
      result.errors.push(msg);
      if (isClosedOrCrashError(err)) {
        result.status = "partial";
        break;
      }
    }
  }

  const pts = await extractCurrentPoints(page, result.points_before).catch(() => null);
  if (pts && pts > 0) {
    result.points_after = Math.max(result.points_after, pts);
  }
}

export async function runMobileSearch(
  context: BrowserContext,
  page: Page,
  plan: BatchPlan,
  result: WorkerResult
): Promise<void> {
  const queries = (plan.mobile_queries || []).slice(0, 3);
  if (queries.length === 0) return;

  log(`[Mobile Search] Running ${queries.length} queries (Autocomplete DISABLED, direct #sb_form_q + Enter)...`);
  for (let i = 0; i < queries.length; i++) {
    const q = queries[i];
    log(`[Mobile Search ${i + 1}/${queries.length}] "${q}"`);
    try {
      await withTimeout(executeMobileQuery(context, page, q, plan), 30000, `mobile-query-${i + 1}`);
      result.mobile_completed++;
      result.mobile_queries_used.push(q);
      if (i < queries.length - 1) {
        await sleep(randFloat(3500, 5500));
      }
    } catch (err) {
      const msg = `Mobile query '${q}' failed: ${err instanceof Error ? err.message : String(err)}`;
      log(`[Mobile Search] ${msg}`);
      result.errors.push(msg);
      if (isClosedOrCrashError(err)) {
        result.status = "partial";
        break;
      }
    }
  }

  const pts = await extractCurrentPoints(page, result.points_before).catch(() => null);
  if (pts && pts > 0) {
    result.points_after = Math.max(result.points_after, pts);
  }
}
