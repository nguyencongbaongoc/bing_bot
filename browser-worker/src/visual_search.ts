import fs from "node:fs";
import path from "node:path";
import type { BrowserContext, Locator, Page, Response } from "patchright";
import { claimAllAvailableRewards, dismissPopups, readDailyProgress } from "./activities.js";
import { launchDesktopContext } from "./browser.js";
import type { BatchPlan, WorkerResult } from "./types.js";
import { log, safeCloseContext, sleep } from "./utils.js";

const VSSTREAK_URL = "https://www.bing.com/?features=vsstreak,vstooltip&form=ML2XES";
const VS_RESULT_URL_RE = /(bcid=|\/search\?q=|iss=sbi)/i;
const VS_KBLOB_REQ_RE = /(\/images\/kblob|sbiupload)/i;
const VS_TIER3_ACTION =
  "https://www.bing.com/images/search?view=detailv2&iss=sbi&form=SBIHMP&sbisrc=ImgPicker";

function isValidVsResultUrl(url: string): boolean {
  const lower = (url || "").toLowerCase();
  if (!lower) return false;
  if (lower.includes("explore.microsoft.com") || lower.includes("form=sbirdi") || lower.includes("form=imvnxt")) {
    return false;
  }
  if (lower.includes("features=vsstreak") && !lower.includes("bcid=")) return false;
  if (lower.includes("/images/search")) {
    return lower.includes("bcid=") || lower.includes("iss=sbi");
  }
  return VS_RESULT_URL_RE.test(lower);
}

async function openVisualSearchSurface(
  context: BrowserContext,
  earnPage: Page
): Promise<Page> {
  if (!earnPage.url().toLowerCase().includes("rewards.bing.com/earn")) {
    await earnPage.goto("https://rewards.bing.com/earn", { waitUntil: "domcontentloaded", timeout: 15000 }).catch(() => {});
    await sleep(1500);
    await dismissPopups(earnPage);
  }

  let activePage = earnPage;
  const pagesBefore = context.pages().length;

  const cardSelectors = [
    'button:has-text("Visual Search"), button:has-text("Tìm kiếm trực quan")',
    '[aria-label*="Visual Search" i], [aria-label*="Tìm kiếm trực quan" i]',
    'a[href*="visualsearch"], a[href*="visual-search"]',
  ];
  for (const sel of cardSelectors) {
    const loc = earnPage.locator(sel).first();
    if (await loc.isVisible({ timeout: 800 }).catch(() => false)) {
      await loc.click({ timeout: 3000 }).catch(() => {});
      await sleep(1200);
      break;
    }
  }

  if (context.pages().length === pagesBefore && activePage.url().toLowerCase().includes("rewards.bing.com")) {
    const ctaSels = [
      '[role="dialog"] a[href*="vsstreak"]',
      'a[href*="features=vsstreak"]',
      '[role="dialog"] a[href*="bing.com"]',
      '[role="dialog"] button:has-text("Search now")',
      '[role="dialog"] a:has-text("Search now")',
      '[role="dialog"] button:has-text("Tìm kiếm ngay")',
      '[role="dialog"] a:has-text("Tìm kiếm ngay")',
    ];
    for (const sel of ctaSels) {
      const loc = earnPage.locator(sel).first();
      if (await loc.isVisible({ timeout: 700 }).catch(() => false)) {
        await loc.click({ timeout: 3000 }).catch(() => {});
        await sleep(1200);
        break;
      }
    }
  }

  const pagesAfter = context.pages();
  if (pagesAfter.length > pagesBefore) {
    activePage = pagesAfter[pagesAfter.length - 1];
    await activePage
      .waitForURL(
        (u) => {
          const h = u.hostname.toLowerCase();
          return h.includes("bing.com") && !h.includes("rewards.bing.com");
        },
        { timeout: 10000 }
      )
      .catch(() => {});
    await activePage.waitForLoadState("domcontentloaded", { timeout: 10000 }).catch(() => {});
  }

  const cur = activePage.url().toLowerCase();
  const onTracked =
    cur.includes("bing.com") &&
    !cur.includes("rewards.bing.com") &&
    !cur.includes("explore.microsoft.com") &&
    !cur.includes("form=sbirdi");

  if (!onTracked) {
    activePage = await context.newPage();
    await activePage.goto(VSSTREAK_URL, { waitUntil: "domcontentloaded", timeout: 15000 }).catch(() => {});
  }

  await sleep(1200);
  await dismissPopups(activePage);
  log(`[Visual Search] Active surface URL: ${activePage.url()}`);

  const uploadPaneSelectors = ["#sb_sbipane", "#sbi_b", 'input[type="file"]'];
  const isUploadPaneOpen = async (): Promise<boolean> => {
    for (const sel of uploadPaneSelectors) {
      if (await activePage.locator(sel).first().isVisible({ timeout: 500 }).catch(() => false)) {
        return true;
      }
    }
    return false;
  };

  // Allow Bing auto-open animation (from features=vsstreak,vstooltip) to settle first
  await activePage
    .waitForSelector(uploadPaneSelectors.join(", "), { state: "visible", timeout: 2000 })
    .catch(() => {});

  if (await isUploadPaneOpen()) {
    log("[Visual Search] Upload pane is already open; skipping #sb_sbip click.");
  } else {
    const camBtn = activePage.locator('#sb_sbip, .b_icon[aria-label*="image" i], #sb_sbi').first();
    if ((await camBtn.count()) > 0) {
      log("[Visual Search] Upload pane closed -> clicking #sb_sbip to open it.");
      await camBtn.click({ timeout: 5000 }).catch(() => camBtn.click({ force: true, timeout: 3000 }).catch(() => {}));
      await activePage
        .waitForSelector(uploadPaneSelectors.join(", "), { state: "visible", timeout: 4000 })
        .catch(() => {});
      await sleep(800);
    }
  }

  return activePage;
}

async function findVisualSearchFileInput(activePage: Page): Promise<{ locator: Locator; selector: string } | null> {
  const inputSelectors = [
    '#sb_sbipane input[type="file"]',
    "#sb_fileinput",
    'input[type="file"][accept*="image"]',
    '#sbi_b input[type="file"]',
    'input[type="file"]',
  ];
  for (const sel of inputSelectors) {
    const loc = activePage.locator(sel).first();
    if ((await loc.count().catch(() => 0)) > 0) {
      return { locator: loc, selector: sel };
    }
  }
  return null;
}

/**
 * Event-driven Tier 1 upload:
 * 1. Triggers native FileChooser directly on <input type="file"> (fallback: #sb_brtext FileChooser, then setInputFiles).
 * 2. Logs POST /images/kblob or sbiupload HTTP status; if 200 OK, waits up to 15000ms for bcid=/search redirect.
 * 3. Confirms Tier 1 ONLY when browser URL genuinely redirects to bcid= / /search?q= / iss=sbi.
 */
async function tryTier1WithEventListeners(
  context: BrowserContext,
  activePage: Page,
  imagePath: string
): Promise<{ triggered: boolean; signalSource: string; resultPage: Page }> {
  const controller = new AbortController();
  let openedResultTab: Page | null = null;
  let kblobStatus200 = false;
  let resolveKblob200: () => void = () => {};
  const kblob200Promise = new Promise<void>((resolve) => {
    resolveKblob200 = resolve;
  });

  const onResponse = (resp: Response) => {
    try {
      const req = resp.request();
      const respUrl = resp.url();
      if (req.method() === "POST" && VS_KBLOB_REQ_RE.test(respUrl)) {
        const status = resp.status();
        log(`[Visual Search] Network POST ${respUrl.slice(0, 120)} -> HTTP ${status} (page=${activePage.url()})`);
        if (status === 200) {
          kblobStatus200 = true;
          resolveKblob200();
        }
      }
    } catch {
      // ignore
    }
  };
  activePage.on("response", onResponse);

  let finishNewTab: (pg: Page | null) => void = () => {};
  const newTabPromise = new Promise<Page | null>((resolve) => {
    let settled = false;
    const finish = (pg: Page | null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      context.off("page", onPage);
      resolve(pg);
    };
    finishNewTab = finish;
    const timer = setTimeout(() => finish(null), 25000);
    const onPage = (pg: Page) => {
      void pg
        .waitForURL(VS_RESULT_URL_RE, { timeout: 15000 })
        .then(() => {
          if (isValidVsResultUrl(pg.url())) {
            openedResultTab = pg;
            finish(pg);
          }
        })
        .catch(() => {});
    };
    context.on("page", onPage);
    controller.signal.addEventListener("abort", () => finish(null), { once: true });
  });

  try {
    let uploadSent = false;
    const fileInputMatch = await findVisualSearchFileInput(activePage);

    // 1. Primary: trigger native FileChooser directly on <input type="file">
    if (fileInputMatch) {
      try {
        const [chooser] = await Promise.all([
          activePage.waitForEvent("filechooser", { timeout: 4000 }),
          fileInputMatch.locator.click({ force: true }),
        ]);
        await chooser.setFiles(imagePath);
        uploadSent = true;
        log(`[Visual Search] Tier 1 sent via FileChooser on file input (${fileInputMatch.selector}).`);
      } catch {
        log(
          `[Visual Search] Direct FileChooser click on ${fileInputMatch.selector} did not open; trying browse button...`
        );
      }
    }

    // 2. Secondary FileChooser via #sb_brtext inside #sb_sbipane
    if (!uploadSent) {
      const browseSelectors = [
        "#sb_sbipane #sb_brtext",
        "#sb_brtext",
        "#sb_brs",
        'label[for="sb_fileinput"]',
        'span:has-text("Browse")',
        'span:has-text("Duyệt")',
      ];
      for (const sel of browseSelectors) {
        const btn = activePage.locator(sel).first();
        if (await btn.isVisible({ timeout: 800 }).catch(() => false)) {
          try {
            const [chooser] = await Promise.all([
              activePage.waitForEvent("filechooser", { timeout: 3500 }),
              btn.click({ timeout: 3500 }),
            ]);
            await chooser.setFiles(imagePath);
            uploadSent = true;
            log(`[Visual Search] Tier 1 sent via FileChooser (${sel}).`);
          } catch {
            log(`[Visual Search] FileChooser did not open on ${sel}; falling back to setInputFiles.`);
          }
          break;
        }
      }
    }

    // 3. Fallback: setInputFiles + dispatch 'change' event
    if (!uploadSent && fileInputMatch) {
      try {
        await fileInputMatch.locator.setInputFiles(imagePath);
        await fileInputMatch.locator.dispatchEvent("change").catch(() => {});
        uploadSent = true;
        log(`[Visual Search] Tier 1 fallback sent via setInputFiles + change (${fileInputMatch.selector}).`);
      } catch {
        uploadSent = false;
      }
    }

    if (!uploadSent) {
      controller.abort();
      return { triggered: false, signalSource: "", resultPage: activePage };
    }

    if (isValidVsResultUrl(activePage.url())) {
      controller.abort();
      return { triggered: true, signalSource: "url-immediate", resultPage: activePage };
    }
    if (openedResultTab && isValidVsResultUrl((openedResultTab as Page).url())) {
      controller.abort();
      return { triggered: true, signalSource: "new-tab-immediate", resultPage: openedResultTab };
    }

    // Wait up to 8000ms initially; if kblob returns 200 OK, wait up to 15000ms for bcid= redirect
    const initialUrlPromise = activePage
      .waitForURL(VS_RESULT_URL_RE, { timeout: 8000 })
      .then(() => (isValidVsResultUrl(activePage.url()) ? { source: "url-navigation", page: activePage } : null))
      .catch(() => null);

    let winner = await Promise.race([
      initialUrlPromise.then((v) => (v ? v : new Promise<{ source: string; page: Page } | null>(() => {}))),
      newTabPromise.then((pg) =>
        pg ? { source: "new-tab-url", page: pg } : new Promise<{ source: string; page: Page } | null>(() => {})
      ),
      sleep(8000).then(() => null),
    ]);

    if (!winner && !isValidVsResultUrl(activePage.url())) {
      if (!kblobStatus200) {
        // Give kblob a tiny window only if still not settled, otherwise kblob200Promise won't delay beyond 8s
        await Promise.race([kblob200Promise, sleep(200)]);
      }
      if (kblobStatus200) {
        log("[Visual Search] kblob 200 OK confirmed -> waiting up to 15000ms for bcid= redirect...");
        const extendedUrlPromise = activePage
          .waitForURL(VS_RESULT_URL_RE, { timeout: 15000 })
          .then(() =>
            isValidVsResultUrl(activePage.url()) ? { source: "url-after-kblob-200", page: activePage } : null
          )
          .catch(() => null);

        winner = await Promise.race([
          extendedUrlPromise.then((v) => (v ? v : new Promise<{ source: string; page: Page } | null>(() => {}))),
          newTabPromise.then((pg) =>
            pg ? { source: "new-tab-after-kblob-200", page: pg } : new Promise<{ source: string; page: Page } | null>(() => {})
          ),
          sleep(15000).then(() => null),
        ]);
      }
    }

    controller.abort();
    finishNewTab(null);

    if (winner && isValidVsResultUrl(winner.page.url())) {
      log(`[Visual Search] Tier 1 URL redirect confirmed (${winner.source}): ${winner.page.url().slice(0, 120)}`);
      return { triggered: true, signalSource: winner.source, resultPage: winner.page };
    }

    if (isValidVsResultUrl(activePage.url())) {
      return { triggered: true, signalSource: "url-post-race", resultPage: activePage };
    }

    return { triggered: false, signalSource: "", resultPage: activePage };
  } finally {
    controller.abort();
    activePage.off("response", onResponse);
  }
}

async function tryTier2DataTransfer(activePage: Page, imagePath: string): Promise<boolean> {
  try {
    const b64 = fs.readFileSync(imagePath).toString("base64");
    const filename = path.basename(imagePath);
    log("[Visual Search] Tier 1 did not redirect to bcid=/search result -> trying Tier 2 DataTransfer...");
    const dispatched = await activePage.evaluate(
      ([rawB64, fname]) => {
        const target = document.querySelector("#sbi_dropzone, #sbi_b, #sb_sbipane");
        if (!target) return false;
        const bin = atob(rawB64);
        const arr = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
        const file = new File([arr], fname, { type: "image/jpeg" });
        const dt = new DataTransfer();
        dt.items.add(file);
        for (const evName of ["dragenter", "dragover", "drop"]) {
          target.dispatchEvent(new DragEvent(evName, { bubbles: true, cancelable: true, dataTransfer: dt }));
        }
        return true;
      },
      [b64, filename]
    );
    if (!dispatched) return false;
    await activePage.waitForURL(VS_RESULT_URL_RE, { timeout: 8000 }).catch(() => {});
    return isValidVsResultUrl(activePage.url());
  } catch {
    return false;
  }
}

async function tryTier3ImageBinFormPost(activePage: Page, imagePath: string): Promise<boolean> {
  try {
    const b64 = fs.readFileSync(imagePath).toString("base64");
    log(
      `[Visual Search] Tier 2 did not redirect -> trying Tier 3 native Form POST imageBin (${b64.length} base64 chars)...`
    );
    if (!activePage.url().toLowerCase().includes("bing.com")) {
      await activePage.goto(VSSTREAK_URL, { waitUntil: "domcontentloaded", timeout: 12000 }).catch(() => {});
    }

    await activePage.evaluate(
      ([rawB64, actionUrl]) => {
        const form = document.createElement("form");
        form.method = "POST";
        form.enctype = "multipart/form-data";
        form.action = actionUrl;

        const cbir = document.createElement("input");
        cbir.type = "hidden";
        cbir.name = "cbir";
        cbir.value = "sbi";
        form.appendChild(cbir);

        const imageBin = document.createElement("input");
        imageBin.type = "hidden";
        imageBin.name = "imageBin";
        imageBin.value = rawB64;
        form.appendChild(imageBin);

        document.body.appendChild(form);
        form.submit();
      },
      [b64, VS_TIER3_ACTION]
    );

    await activePage.waitForURL(VS_RESULT_URL_RE, { timeout: 12000 }).catch(() => {});
    await activePage.waitForLoadState("domcontentloaded", { timeout: 8000 }).catch(() => {});
    const ok = isValidVsResultUrl(activePage.url());
    log(`[Visual Search] Tier 3 Form POST result: ok=${ok}, url=${activePage.url().slice(0, 120)}`);
    return ok;
  } catch (err) {
    log(`[Visual Search] Tier 3 Form POST error: ${err instanceof Error ? err.message : String(err)}`);
    return false;
  }
}

async function verifyVisualSearchOnEarn(
  page: Page,
  pointsBefore: number
): Promise<{ verified: boolean; balance: number | null }> {
  const afterSnap = await readDailyProgress(page, pointsBefore);
  if (!page.url().toLowerCase().includes("rewards.bing.com/earn")) {
    await page.goto("https://rewards.bing.com/earn", { waitUntil: "domcontentloaded", timeout: 15000 }).catch(() => {});
    await sleep(2000);
    await dismissPopups(page);
  }

  const cardStatus = await page
    .evaluate(() => {
      const bars = Array.from(document.querySelectorAll('[role="progressbar"]'));
      for (const bar of bars) {
        const label = (bar.getAttribute("aria-label") || "").toLowerCase();
        if (label.includes("visual search") || label.includes("trực quan")) {
          const cur = parseFloat(bar.getAttribute("aria-valuenow") || "0");
          const max = parseFloat(bar.getAttribute("aria-valuemax") || "0");
          const vt = (bar.getAttribute("aria-valuetext") || "").trim();
          if (max > 0 && cur < max) return "incomplete" as const;
          if ((max > 0 && cur >= max) || vt === "100%") return "done" as const;
        }
      }
      const candidates = Array.from(
        document.querySelectorAll("a, button, [role='button'], div[class*='card'], section > div")
      );
      for (const el of candidates) {
        const txt = ((el as HTMLElement).innerText || "").trim();
        const lower = txt.toLowerCase();
        if (!lower.includes("visual search") && !lower.includes("trực quan")) continue;
        if (txt.length > 300) continue;
        if (/\b0\s*\/\s*1\b/.test(txt)) return "incomplete" as const;
        if (/\b1\s*\/\s*1\b/.test(txt)) return "done" as const;
      }
      return "unknown" as const;
    })
    .catch(() => "unknown" as const);

  const verified =
    cardStatus === "done" || (cardStatus !== "incomplete" && Boolean(afterSnap.visual_search_done));
  return { verified, balance: afterSnap.balance };
}

export async function runVisualSearch(plan: BatchPlan, result: WorkerResult): Promise<void> {
  const imagePath = plan.visual_search_image_path;
  if (!imagePath || !fs.existsSync(imagePath)) {
    log("[Visual Search] Missing visual_search_image_path; skipping.");
    return;
  }

  let ctx: BrowserContext | null = null;
  try {
    // Visual Search runs in a short-lived headed window (headless=false, GPU flags stripped)
    const launched = await launchDesktopContext(plan.desktop_profile_dir, false, plan.account);
    ctx = launched.context;
    const page = launched.page;

    const initialSnap = await readDailyProgress(page, result.points_before);
    if (initialSnap.visual_search_done) {
      result.visual_search_done = true;
      log("[Visual Search] Already marked done (1/1) on /earn; skipping.");
      return;
    }

    // Single attempt per supervisor invocation so we never burn 3/3 in one run
    result.visual_search_attempted = true;
    const activePage = await openVisualSearchSurface(ctx, page);

    const tier1 = await tryTier1WithEventListeners(ctx, activePage, imagePath);
    let resultPage = tier1.resultPage;
    let confirmed = tier1.triggered;
    if (!confirmed) {
      confirmed = await tryTier2DataTransfer(activePage, imagePath);
      resultPage = activePage;
    }
    if (!confirmed) {
      confirmed = await tryTier3ImageBinFormPost(activePage, imagePath);
      resultPage = activePage;
    }

    if (confirmed) {
      await resultPage.waitForLoadState("domcontentloaded", { timeout: 8000 }).catch(() => {});
      // Dwell 7s on result page for Bing telemetry beacon
      const rc = resultPage.locator(".irsikc a, #vs_insights a, .richImgLnk, .iusc").first();
      if (await rc.isVisible({ timeout: 1000 }).catch(() => false)) {
        await rc.click({ timeout: 3000 }).catch(() => {});
      }
      await sleep(3500);
      await resultPage.mouse.wheel(0, 300).catch(() => {});
      await sleep(3500);

      for (const tab of ctx.pages()) {
        if (tab !== page && !tab.isClosed()) {
          await tab.close().catch(() => {});
        }
      }
      await sleep(4000);
      let { verified, balance } = await verifyVisualSearchOnEarn(page, result.points_before);
      if (!verified) {
        log("[Visual Search] Card still not 1/1 on first /earn check; waiting 5s and rechecking...");
        await sleep(5000);
        const recheck = await verifyVisualSearchOnEarn(page, result.points_before);
        verified = recheck.verified;
        if (recheck.balance && recheck.balance > 0) {
          balance = recheck.balance;
        }
      }
      if (verified) {
        await claimAllAvailableRewards(page);
      }
      result.visual_search_done = verified;
      if (balance && balance > 0) {
        result.points_after = Math.max(result.points_after, balance);
      }
      log(
        `[Visual Search] Result URL confirmed; /earn verification -> vs_done=${result.visual_search_done}`
      );
    } else {
      result.visual_search_done = false;
      log("[Visual Search] Upload did not redirect to bcid=/search result within timeout (vs_done=false).");
    }
  } catch (err) {
    const msg = `Visual Search error: ${err instanceof Error ? err.message : String(err)}`;
    log(`[Visual Search] ${msg}`);
    result.errors.push(msg);
  } finally {
    await safeCloseContext(ctx, 5000);
  }
}
