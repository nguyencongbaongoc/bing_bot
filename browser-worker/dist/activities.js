import { log, normalizeOfferTitle, randInt, sleep, withTimeout } from "./utils.js";
const REWARDS_URL = "https://rewards.bing.com";
const REWARDS_EARN_URL = "https://rewards.bing.com/earn";
const REWARDS_DASHBOARD_URL = "https://rewards.bing.com/dashboard";
async function isVisibleSafe(loc, timeoutMs = 600) {
    try {
        if ((await loc.count()) === 0)
            return false;
        return await loc.first().isVisible({ timeout: timeoutMs });
    }
    catch {
        return false;
    }
}
export async function dismissPopups(page) {
    const selectors = [
        "#bnp_btn_reject",
        "#bnp_btn_accept",
        'button:has-text("Reject")',
        'button:has-text("Accept")',
        'button:has-text("Maybe later")',
        'button:has-text("Để sau")',
        'button:has-text("Not now")',
        'button:has-text("No thanks")',
        'button:has-text("Skip")',
        'button[aria-label="Close"]',
        'button[title="Close"]',
    ];
    for (const sel of selectors) {
        try {
            const btn = page.locator(sel).first();
            if (await btn.isVisible({ timeout: 350 })) {
                await btn.click({ force: true, timeout: 1000 });
                await sleep(250);
            }
        }
        catch {
            // ignore
        }
    }
    try {
        await page.keyboard.press("Escape");
    }
    catch {
        // ignore
    }
}
export function sanitizeScrapedPoints(scraped, lastKnownPoints) {
    if (scraped === null || scraped === undefined || !Number.isFinite(scraped))
        return null;
    const val = Math.trunc(scraped);
    if (val <= 0)
        return null;
    if (lastKnownPoints && lastKnownPoints > 500 && val < lastKnownPoints * 0.5) {
        return null;
    }
    return val;
}
export async function extractCurrentPoints(page, lastKnownPoints) {
    try {
        await page
            .waitForSelector("#id_rc, #flyout_id_rc, .b_id_rc, #rh_meter, [id*='reward_points'], span[class*='pointsValue'], header, #b_header", { timeout: 3000 })
            .catch(() => { });
    }
    catch {
        // ignore
    }
    const selectors = [
        "#id_rc",
        "#flyout_id_rc",
        ".b_id_rc",
        "#rh_meter",
        "#id_rh",
        "[id*='reward_points']",
        "span[class*='pointsValue']",
        "#flyout-points",
        "[data-bi-id*='rewards'] .points",
        "a[aria-label*='Rewards' i]",
        "#b_header [aria-label*='Rewards' i]",
        "button[aria-label*='points' i]",
        "header button:has(img[src*='Medals'])",
        "header button:has(img[alt='Member'])",
    ];
    for (const sel of selectors) {
        try {
            const elem = page.locator(sel).first();
            if ((await elem.count()) === 0)
                continue;
            const visible = await elem.isVisible({ timeout: 400 }).catch(() => false);
            let text = (visible ? await elem.innerText() : await elem.textContent()) || "";
            text = text.trim();
            if (!text) {
                text = ((await elem.getAttribute("aria-label")) || (await elem.getAttribute("title")) || "").trim();
            }
            if (!text)
                continue;
            const clean = text.replace(/[,.]/g, "");
            const matches = clean.match(/\d+/g);
            if (matches && matches.length > 0) {
                const accepted = sanitizeScrapedPoints(parseInt(matches[0], 10), lastKnownPoints);
                if (accepted !== null)
                    return accepted;
            }
        }
        catch {
            // continue
        }
    }
    try {
        const rawText = await page.locator("header, #b_header, [role='banner']").first().innerText({ timeout: 1500 });
        const re = /(?:rewards|points|pts|điểm)\s*[:-]?\s*(\d[\d,.]{0,7})|(\d[\d,.]{0,7})\s*(?:points|pts|điểm)/gi;
        for (const m of rawText.matchAll(re)) {
            const tok = (m[1] || m[2] || "").replace(/[,.]/g, "");
            if (/^\d+$/.test(tok)) {
                const accepted = sanitizeScrapedPoints(parseInt(tok, 10), lastKnownPoints);
                if (accepted !== null)
                    return accepted;
            }
        }
    }
    catch {
        // ignore
    }
    try {
        const items = await page.locator("mee-rewards-user-status-item").all();
        for (const item of items) {
            const txt = await item.innerText().catch(() => "");
            if (txt.toLowerCase().includes("available") || txt.toLowerCase().includes("khả dụng")) {
                const m = txt.replace(/[,.]/g, "").match(/\b\d{2,6}\b/);
                if (m) {
                    const accepted = sanitizeScrapedPoints(parseInt(m[0], 10), lastKnownPoints);
                    if (accepted !== null)
                        return accepted;
                }
            }
        }
    }
    catch {
        // ignore
    }
    return null;
}
export async function claimAllAvailableRewards(page) {
    let claimed = 0;
    try {
        await page
            .evaluate(() => {
            const dialogs = document.querySelectorAll('[role="dialog"], .overflow-auto, .scrollbar-panel, div.fixed.inset-0');
            dialogs.forEach((d) => {
                d.scrollTop = d.scrollHeight;
            });
        })
            .catch(() => { });
        await sleep(400);
        const modalSelectors = [
            'div.fixed.inset-0 button:has-text("Claim points")',
            '[role="dialog"] button:has-text("Claim points")',
            'button:has-text("Claim points")',
            'span:text-is("Claim points")',
            '[role="dialog"] button:has-text("Claim")',
            'button:has-text("Claim")',
        ];
        for (const sel of modalSelectors) {
            const loc = page.locator(sel).first();
            if ((await loc.count()) > 0 && (await loc.isVisible({ timeout: 800 }).catch(() => false))) {
                await loc.click({ force: true, timeout: 3000 }).catch(() => { });
                claimed++;
                log(`[Coin] Claimed popup with selector: ${sel}`);
                await sleep(1200);
            }
        }
        const readyButtons = page.locator('button:has-text("Ready to claim"), a:has-text("Ready to claim"), ' +
            '[aria-label*="Ready to claim" i], [aria-label*="Star bonus" i] button, ' +
            'div:has-text("Bing Star bonus") button:has-text("Claim"), ' +
            'div:has-text("Star bonus") button:has-text("Claim")');
        const count = Math.min(await readyButtons.count(), 10);
        for (let i = 0; i < count; i++) {
            try {
                const btn = readyButtons.nth(i);
                if (!(await btn.isVisible({ timeout: 800 })))
                    continue;
                await btn.scrollIntoViewIfNeeded({ timeout: 1500 }).catch(() => { });
                await btn.click({ timeout: 2500 });
                claimed++;
                await sleep(1200);
                await page
                    .evaluate(() => {
                    const dialogs = document.querySelectorAll('[role="dialog"], .overflow-auto, .scrollbar-panel, div.fixed.inset-0');
                    dialogs.forEach((d) => {
                        d.scrollTop = d.scrollHeight;
                    });
                })
                    .catch(() => { });
                await sleep(400);
                const confirmBtn = page
                    .locator('button:has-text("Claim points"), [role="dialog"] button:has-text("Claim"), ' +
                    '[role="dialog"] button:has-text("Claim points"), button:has-text("Claim")')
                    .first();
                if (await isVisibleSafe(confirmBtn, 1500)) {
                    await confirmBtn.click({ force: true, timeout: 2500 }).catch(() => { });
                    log("[Coin] Confirmed 'Claim points' in popup.");
                    await sleep(1200);
                }
                await page.keyboard.press("Escape").catch(() => { });
                await sleep(400);
            }
            catch {
                // continue
            }
        }
    }
    catch {
        // ignore
    }
    return claimed;
}
export async function claimDashboardPendingPoints(page) {
    try {
        await page.goto(REWARDS_DASHBOARD_URL, { waitUntil: "domcontentloaded", timeout: 15000 }).catch(() => { });
        await dismissPopups(page);
        await sleep(1800);
        const cardSelectors = [
            'a:has-text("Ready to claim")',
            'button:has-text("Ready to claim")',
            '[role="button"]:has-text("Ready to claim")',
            'a:has-text("Sẵn sàng nhận")',
            'button:has-text("Sẵn sàng nhận")',
            '[aria-label*="Ready to claim" i]',
        ];
        for (const sel of cardSelectors) {
            const card = page.locator(sel).first();
            if (await isVisibleSafe(card, 600)) {
                await card.click({ timeout: 2500 }).catch(() => { });
                await sleep(1400);
                break;
            }
        }
        const noPointsBlocked = await isVisibleSafe(page.locator(':has-text("No points to claim right now"), :has-text("0 points to claim")').first(), 350);
        if (noPointsBlocked)
            return false;
        const btn = page
            .locator('button:has-text("Claim points"), [role="dialog"] button:has-text("Claim"), button:has-text("Nhận điểm")')
            .first();
        if (await isVisibleSafe(btn, 800)) {
            const enabled = await btn.isEnabled().catch(() => false);
            if (enabled) {
                await btn.click({ timeout: 2500 });
                await sleep(2200);
                log("[Final Claim] Claimed pending points from Dashboard drawer.");
                return true;
            }
        }
    }
    catch {
        // ignore
    }
    return false;
}
export async function ensureStreakProtection(page) {
    const selectors = [
        'button[role="switch"][aria-label*="streak protection" i]',
        'button[role="switch"][aria-label*="bảo vệ chuỗi" i]',
        'div:has-text("Streak protection") button[role="switch"]',
        'div:has-text("Bảo vệ chuỗi") button[role="switch"]',
        'button[role="switch"]',
    ];
    for (const sel of selectors) {
        try {
            const btns = page.locator(sel);
            const cnt = await btns.count();
            for (let i = 0; i < cnt; i++) {
                const btn = btns.nth(i);
                if (!(await btn.isVisible({ timeout: 600 }).catch(() => false)))
                    continue;
                if (sel === 'button[role="switch"]') {
                    const nearStreak = await btn
                        .locator('xpath=./ancestor::*[contains(., "Streak") or contains(., "streak") or contains(., "chuỗi")]')
                        .count();
                    if (nearStreak === 0)
                        continue;
                }
                const checked = await btn.getAttribute("aria-checked");
                const inner = ((await btn.innerText().catch(() => "")) || "").toLowerCase();
                if (checked === "false" || inner.includes("off")) {
                    await btn.click({ force: true, timeout: 2000 });
                    await sleep(800);
                    const confirm = page.locator('button:has-text("Turn on"), button:has-text("Bật"), button:has-text("Confirm")').first();
                    if (await isVisibleSafe(confirm, 1200)) {
                        await confirm.click({ force: true, timeout: 2000 }).catch(() => { });
                        await sleep(800);
                    }
                    log("[Streak Protection] Enabled.");
                    return true;
                }
                if (checked === "true" || inner.includes("on")) {
                    return true;
                }
            }
        }
        catch {
            // continue
        }
    }
    return false;
}
export async function extractBingStarBonus(page) {
    try {
        const val = await page.evaluate(() => {
            const parseNum = (s) => parseInt(String(s || "0").replace(/[,.]/g, ""), 10) || 0;
            const bars = document.querySelectorAll('[role="progressbar"]');
            for (const bar of bars) {
                const label = (bar.getAttribute("aria-label") || "").toLowerCase();
                const vt = (bar.getAttribute("aria-valuetext") || "").toLowerCase();
                if (label.includes("star") || vt.includes("star")) {
                    return Math.round(parseFloat(bar.getAttribute("aria-valuenow") || "0")) || 0;
                }
            }
            const bodyText = document.body?.innerText || "";
            const m = bodyText.match(/(?:Bing\s*Star\s*bonus|Star\s*bonus|Bing\s*Star)[\s\S]{0,200}?(\d[\d,.]*)\s*(?:\/|of|trên)\s*(\d[\d,.]*)/i);
            if (m)
                return parseNum(m[1]);
            return 0;
        });
        return Math.max(0, Number(val) || 0);
    }
    catch {
        return 0;
    }
}
export async function readDailyProgress(page, lastKnownPoints) {
    const snap = {
        found: false,
        found_pc: false,
        found_mobile: false,
        pc_cur: -1,
        pc_max: 90,
        mobile_cur: -1,
        mobile_max: 60,
        balance: null,
        bing_star_points: 0,
        daily_set_done: false,
        edge_browsing_done: false,
        visual_search_done: false,
        bing_search_done: false,
        all_maxed: false,
        scrape_error: false,
    };
    const userInfoHolder = { data: null };
    const onResponse = async (resp) => {
        try {
            if (!resp.url().toLowerCase().includes("/api/getuserinfo"))
                return;
            if (resp.status() >= 400)
                return;
            const json = (await resp.json());
            if (json && typeof json === "object")
                userInfoHolder.data = json;
        }
        catch {
            // ignore
        }
    };
    page.on("response", onResponse);
    const attemptUrls = [REWARDS_EARN_URL, `${REWARDS_URL}/`];
    try {
        for (let attempt = 0; attempt < attemptUrls.length; attempt++) {
            const targetUrl = attemptUrls[attempt];
            try {
                await page.goto(targetUrl, { waitUntil: "commit", timeout: 20000 });
                // Wait for Microsoft hidden SSO redirect to settle after navigation commit
                await sleep(2000);
                await page
                    .waitForSelector('[role="progressbar"], section#moreactivities, mee-rewards-user-status-item, #id_rc', {
                    timeout: 12000,
                })
                    .catch(() => { });
                await sleep(1500);
                await dismissPopups(page);
                const acts = await page.evaluate(() => {
                    const out = {
                        bingSearch: false,
                        dailySet: false,
                        edgeBrowsing: false,
                        visualSearch: false,
                    };
                    const bars = document.querySelectorAll('[role="progressbar"]');
                    bars.forEach((bar) => {
                        const label = (bar.getAttribute("aria-label") || "").toLowerCase();
                        const cur = parseFloat(bar.getAttribute("aria-valuenow") || "0");
                        const max = parseFloat(bar.getAttribute("aria-valuemax") || "0");
                        const vt = (bar.getAttribute("aria-valuetext") || "").trim();
                        const done = (max > 0 && cur >= max) || vt === "100%";
                        if (label.includes("bing") && !label.includes("star"))
                            out.bingSearch = done;
                        else if (label.includes("daily set") || label.includes("bộ hằng ngày"))
                            out.dailySet = done;
                        else if (label.includes("edge"))
                            out.edgeBrowsing = done;
                        else if (label.includes("visual search") || label.includes("trực quan"))
                            out.visualSearch = done;
                    });
                    return out;
                });
                snap.bing_search_done = acts.bingSearch;
                snap.daily_set_done = acts.dailySet;
                snap.edge_browsing_done = acts.edgeBrowsing;
                snap.visual_search_done = acts.visualSearch;
                let bodyText = await page.innerText("body").catch(() => "");
                const hasBreakdown = bodyText.includes("Desktop Bing search") ||
                    bodyText.includes("Mobile Bing search") ||
                    bodyText.includes("Tìm kiếm trên máy tính") ||
                    bodyText.includes("Tìm kiếm trên điện thoại");
                if (!hasBreakdown) {
                    const clicked = await page
                        .evaluate(() => {
                        const btns = Array.from(document.querySelectorAll('button, div[role="button"], a'));
                        for (const b of btns) {
                            const t = (b.innerText || "").toLowerCase();
                            if (t.includes("points breakdown") || t.includes("chi tiết điểm")) {
                                b.click();
                                return true;
                            }
                        }
                        return false;
                    })
                        .catch(() => false);
                    if (clicked) {
                        await sleep(1400);
                        bodyText = await page.innerText("body").catch(() => "");
                    }
                }
                const pcMatch = bodyText.match(/(?:Desktop Bing search|Tìm kiếm trên máy tính|Tìm kiếm trên PC|PC search|Desktop search|PC Bing search)[\s\S]{0,100}?(\d+)\s*\/\s*(\d+)/i);
                if (pcMatch) {
                    snap.pc_cur = parseInt(pcMatch[1], 10);
                    snap.pc_max = parseInt(pcMatch[2], 10) || 90;
                    snap.found_pc = true;
                }
                const mobMatch = bodyText.match(/(?:Mobile Bing search|Tìm kiếm trên điện thoại|Tìm kiếm trên thiết bị di động|Tìm kiếm trên di động|Mobile search|Mobile App search)[\s\S]{0,100}?(\d+)\s*\/\s*(\d+)/i);
                if (mobMatch) {
                    snap.mobile_cur = parseInt(mobMatch[1], 10);
                    snap.mobile_max = parseInt(mobMatch[2], 10) || 60;
                    snap.found_mobile = true;
                }
                if (userInfoHolder.data) {
                    const root = userInfoHolder.data.dashboard || userInfoHolder.data;
                    const us = root.userStatus || root;
                    const avail = us.availablePoints ?? root.availablePoints ?? us.balance;
                    if (typeof avail === "number" && snap.balance === null) {
                        snap.balance = Math.trunc(avail);
                    }
                }
                if (snap.balance === null) {
                    snap.balance = await extractCurrentPoints(page, lastKnownPoints);
                }
                snap.bing_star_points = Math.max(snap.bing_star_points, await extractBingStarBonus(page));
                snap.found = snap.found_pc || snap.found_mobile || snap.balance !== null;
                snap.all_maxed =
                    snap.found_pc &&
                        snap.found_mobile &&
                        snap.pc_cur >= snap.pc_max &&
                        snap.mobile_cur >= snap.mobile_max;
                await page.keyboard.press("Escape").catch(() => { });
                if (snap.found_pc || snap.found_mobile) {
                    snap.scrape_error = false;
                    break;
                }
                if (attempt === 0) {
                    log("[Dashboard] /earn did not expose search counters; retrying fallback https://rewards.bing.com/...");
                    await sleep(1000);
                }
                else {
                    snap.scrape_error = true;
                }
            }
            catch (err) {
                const msg = err instanceof Error ? err.message : String(err);
                const isAborted = msg.toUpperCase().includes("ERR_ABORTED");
                log(`[Dashboard] Error reading ${targetUrl} (attempt ${attempt + 1}/2): ${msg}`);
                if (attempt === 0 && (isAborted || !snap.found_pc)) {
                    log("[Dashboard] Retrying progress read with fallback https://rewards.bing.com/...");
                    await sleep(1500);
                    continue;
                }
                snap.scrape_error = true;
            }
        }
    }
    finally {
        page.off("response", onResponse);
    }
    if (!snap.found_pc)
        snap.pc_cur = -1;
    if (!snap.found_mobile)
        snap.mobile_cur = -1;
    return snap;
}
export async function solveInteractiveTab(subTab, cardTitle = "") {
    try {
        await subTab.waitForLoadState("domcontentloaded", { timeout: 6000 }).catch(() => { });
        await sleep(1000);
        const startBtn = subTab
            .locator('#rqStartQuiz, input[value*="Start" i], button:has-text("Start playing"), button:has-text("Start quiz")')
            .first();
        if (await isVisibleSafe(startBtn, 800)) {
            await startBtn.click({ force: true, timeout: 2000 }).catch(() => { });
            await sleep(1200);
        }
        const quizSels = [".btOption", "#QuestionPane", "input[type='radio']", ".rqOption", ".wk_question", "#rqQuestion"];
        let isQuiz = false;
        for (const sel of quizSels) {
            if (await isVisibleSafe(subTab.locator(sel).first(), 600)) {
                isQuiz = true;
                break;
            }
        }
        if (isQuiz) {
            const optSels = [".btOption", "#QuestionPane input", "#QuestionPane label", "input[type='radio']", ".rqOption"];
            for (const osel of optSels) {
                const opts = await subTab.locator(osel).all();
                const visibleOpts = [];
                for (const o of opts) {
                    if (await o.isVisible({ timeout: 250 }).catch(() => false))
                        visibleOpts.push(o);
                }
                if (visibleOpts.length > 0) {
                    const chosen = visibleOpts[randInt(0, visibleOpts.length - 1)];
                    await chosen.click({ force: true, timeout: 2000 }).catch(() => { });
                    break;
                }
            }
            await sleep(randInt(3000, 4500));
            return;
        }
        const searchInput = subTab.locator("#sb_form_q, textarea[name='q'], input[name='q']").first();
        const curUrl = subTab.url().toLowerCase();
        if ((curUrl.includes("bing.com") && !curUrl.includes("/search?q=")) && (await isVisibleSafe(searchInput, 800))) {
            const cleanQ = normalizeOfferTitle(cardTitle);
            const q = cleanQ.length >= 3 && !cleanQ.startsWith("card ") ? cleanQ : "trending news today";
            await searchInput.click({ force: true, timeout: 2000 }).catch(() => { });
            await searchInput.fill(q).catch(() => { });
            await subTab.keyboard.press("Enter").catch(() => { });
            await subTab.waitForLoadState("domcontentloaded", { timeout: 8000 }).catch(() => { });
        }
        await subTab.mouse.wheel(0, randInt(250, 450)).catch(() => { });
        await sleep(randInt(3000, 4200));
    }
    catch {
        // ignore
    }
}
async function runDailySet(context, page, attemptedOffers, addedOffers) {
    let consecutiveTimeouts = 0;
    try {
        await page.goto(REWARDS_URL, { waitUntil: "commit", timeout: 15000 });
        await sleep(2500);
        await dismissPopups(page);
        const cardSel = 'div[data-rac] a[href*="bing.com"], div[class*="card"] a[href*="bing.com"], ' +
            'div[id*="daily-set"] [role="button"], div[id*="daily-set"] a, ' +
            'div[data-bi-area="DailySet"] .c-card, .daily-sets .c-card, mee-rewards-daily-set-item .c-card';
        const cards = page.locator(cardSel);
        const total = Math.min(await cards.count(), 12);
        log(`[Daily Set] Found ${total} candidate cards.`);
        for (let i = 0; i < total; i++) {
            if (consecutiveTimeouts >= 2) {
                log("[Daily Set] Skipping remaining cards after 2 consecutive timeouts.");
                break;
            }
            const card = cards.nth(i);
            const title = (((await card.innerText({ timeout: 1000 }).catch(() => "")) || "").split("\n")[0] || `Card ${i + 1}`)
                .slice(0, 60)
                .trim();
            const normTitle = normalizeOfferTitle(title);
            if (normTitle && attemptedOffers.has(normTitle))
                continue;
            const done = await card
                .locator('svg.size-2, .mee-icon-SkypeCircleCheck, .mee-icon-CheckMark, div[class*="completed"]')
                .first()
                .isVisible({ timeout: 250 })
                .catch(() => false);
            if (done)
                continue;
            try {
                await withTimeout((async () => {
                    await page.keyboard.press("Escape").catch(() => { });
                    await card.scrollIntoViewIfNeeded({ timeout: 1500 }).catch(() => { });
                    const pagesBefore = context.pages().length;
                    await card.click({ force: true, timeout: 3000 }).catch(async () => {
                        await card.evaluate((el) => el.click());
                    });
                    await sleep(1800);
                    const pagesAfter = context.pages();
                    if (pagesAfter.length > pagesBefore) {
                        const subTab = pagesAfter[pagesAfter.length - 1];
                        try {
                            await solveInteractiveTab(subTab, title);
                        }
                        finally {
                            await subTab.close().catch(() => { });
                        }
                    }
                    else {
                        await sleep(1500);
                    }
                })(), 20000, `daily-set-${i}`);
                consecutiveTimeouts = 0;
                if (normTitle) {
                    attemptedOffers.add(normTitle);
                    addedOffers.push(normTitle);
                }
            }
            catch {
                consecutiveTimeouts++;
            }
        }
        return true;
    }
    catch (err) {
        log(`[Daily Set] Error: ${err instanceof Error ? err.message : String(err)}`);
        return false;
    }
}
async function solveKeepEarning(context, page, attemptedOffers, addedOffers) {
    let consecutiveTimeouts = 0;
    try {
        await page.goto(REWARDS_EARN_URL, { waitUntil: "commit", timeout: 15000 });
        await sleep(2200);
        await dismissPopups(page);
        const sec = page.locator("section#moreactivities, section[id*='moreactivities'], section:has-text('Keep earning')").first();
        if (!(await isVisibleSafe(sec, 4000)))
            return true;
        await sec.scrollIntoViewIfNeeded({ timeout: 2000 }).catch(() => { });
        const expandBtn = sec.locator("button[aria-expanded='false'], [role='button'][aria-expanded='false']").first();
        if (await isVisibleSafe(expandBtn, 1200)) {
            await expandBtn.click({ force: true, timeout: 2000 }).catch(() => { });
            await sleep(1000);
        }
        for (let attempt = 0; attempt < 10; attempt++) {
            if (consecutiveTimeouts >= 2) {
                log("[Keep Earning] Skipping remaining cards after 2 consecutive timeouts.");
                break;
            }
            const skipArr = Array.from(attemptedOffers);
            const nextCard = await page.evaluate((skipList) => {
                const s = document.querySelector("section#moreactivities") ||
                    document.querySelector("section[id*='moreactivities']") ||
                    Array.from(document.querySelectorAll("section")).find((el) => (el.innerText || "").toLowerCase().includes("keep earning"));
                if (!s)
                    return null;
                const skipSet = new Set(skipList.map((x) => x.toLowerCase()));
                document
                    .querySelectorAll("[data-bot-keep-earning-target]")
                    .forEach((el) => el.removeAttribute("data-bot-keep-earning-target"));
                const raw = Array.from(s.querySelectorAll("a, [role='button'], [role='link']"));
                for (const el of raw) {
                    const text = (el.innerText || "").trim();
                    const lower = text.toLowerCase();
                    const href = (el.href || "").trim();
                    const title = (text.split("\n")[0] || "").trim();
                    const norm = title
                        .replace(/\(\+\d+\s*pts?\)/gi, "")
                        .replace(/\+\d+\s*pts?\b/gi, "")
                        .replace(/\s+/g, " ")
                        .trim()
                        .toLowerCase();
                    if (!title || (norm && skipSet.has(norm)))
                        continue;
                    const isDone = lower.includes("completed") ||
                        lower.includes("hoàn thành") ||
                        !!el.querySelector("[class*='check' i], [class*='statusSuccess' i], svg[class*='success' i]");
                    if (isDone)
                        continue;
                    if (href.startsWith("microsoft-edge:") || lower.includes("refer and earn"))
                        continue;
                    const hasPts = /\+\d+/.test(text);
                    const isTarget = href.includes("PUBL=RewardsDO") ||
                        href.includes("form=ML2") ||
                        (href.includes("bing.com") && (href.includes("search") || href.includes("spotlight")));
                    if (hasPts || isTarget) {
                        el.setAttribute("data-bot-keep-earning-target", "active");
                        return { title, norm, href };
                    }
                }
                return null;
            }, skipArr);
            if (!nextCard)
                break;
            if (nextCard.norm) {
                attemptedOffers.add(nextCard.norm);
                addedOffers.push(nextCard.norm);
            }
            try {
                await withTimeout((async () => {
                    const loc = page.locator('[data-bot-keep-earning-target="active"]').first();
                    await loc.scrollIntoViewIfNeeded({ timeout: 1500 }).catch(() => { });
                    const pagesBefore = context.pages().length;
                    await loc.click({ force: true, timeout: 2500 }).catch(async () => {
                        if (nextCard.href && nextCard.href.startsWith("http")) {
                            const t = await context.newPage();
                            await t.goto(nextCard.href, { waitUntil: "commit", timeout: 12000 }).catch(() => { });
                        }
                    });
                    await sleep(1600);
                    const pagesAfter = context.pages();
                    if (pagesAfter.length > pagesBefore) {
                        const subTab = pagesAfter[pagesAfter.length - 1];
                        try {
                            await solveInteractiveTab(subTab, nextCard.title);
                        }
                        finally {
                            await subTab.close().catch(() => { });
                        }
                    }
                })(), 20000, `keep-earning-${nextCard.title}`);
                consecutiveTimeouts = 0;
            }
            catch {
                consecutiveTimeouts++;
            }
            if (!page.url().includes("/earn")) {
                await page.goto(REWARDS_EARN_URL, { waitUntil: "commit", timeout: 12000 }).catch(() => { });
                await sleep(1200);
            }
            await claimAllAvailableRewards(page);
        }
        return true;
    }
    catch (err) {
        log(`[Keep Earning] Error: ${err instanceof Error ? err.message : String(err)}`);
        return false;
    }
}
async function solveUniversalEarnQuests(context, page, attemptedQuests, addedQuests) {
    try {
        await page.goto(REWARDS_EARN_URL, { waitUntil: "commit", timeout: 15000 });
        await sleep(2000);
        const questLinks = await page.locator('a[href*="/earn/quest/"]').all();
        const urls = [];
        for (const el of questLinks) {
            const href = (await el.getAttribute("href").catch(() => "")) || "";
            if (!href.includes("/earn/quest/"))
                continue;
            const txt = (await el.innerText().catch(() => "")) || "";
            const m = txt.match(/(\d+)\s*\/\s*(\d+)\s*tasks/i);
            if (m && parseInt(m[1], 10) >= parseInt(m[2], 10) && parseInt(m[2], 10) > 0)
                continue;
            const full = new URL(href, REWARDS_EARN_URL);
            const canon = `${full.origin}${full.pathname.replace(/\/+$/, "")}`;
            if (!urls.includes(canon))
                urls.push(canon);
        }
        for (const qUrl of urls.slice(0, 4)) {
            await page.goto(qUrl, { waitUntil: "commit", timeout: 15000 }).catch(() => { });
            await sleep(1800);
            await dismissPopups(page);
            const markedCount = await page
                .evaluate(() => {
                document.querySelectorAll("[data-bot-quest-target]").forEach((e) => e.removeAttribute("data-bot-quest-target"));
                const candidates = Array.from(document.querySelectorAll('a, button, [role="button"], [data-react-aria-pressable="true"]'));
                const ignore = ["feedback", "terms", "dashboard", "earn", "redeem", "privacy", "accept", "reject", "cookies"];
                let idx = 0;
                for (const el of candidates) {
                    const r = el.getBoundingClientRect();
                    if (r.width < 10 || r.height < 10)
                        continue;
                    if (el.disabled || el.getAttribute("aria-disabled") === "true")
                        continue;
                    const txt = (el.innerText || el.getAttribute("aria-label") || "").trim().toLowerCase();
                    if (!txt || ignore.some((ig) => txt.includes(ig)))
                        continue;
                    if (el.querySelector("button, a"))
                        continue;
                    if (el.closest("header, nav, footer"))
                        continue;
                    el.setAttribute("data-bot-quest-target", String(idx++));
                }
                return Math.min(idx, 6);
            })
                .catch(() => 0);
            for (let t = 0; t < markedCount; t++) {
                const target = page.locator(`[data-bot-quest-target="${t}"]`).first();
                if (!(await isVisibleSafe(target, 1000)))
                    continue;
                const label = ((await target.innerText().catch(() => "")) || `task-${t}`).trim().slice(0, 40).toLowerCase();
                const key = `${qUrl}::${label}`;
                if (attemptedQuests.has(key))
                    continue;
                attemptedQuests.add(key);
                addedQuests.push(key);
                const pagesBefore = context.pages().length;
                await target.click({ force: true, timeout: 2500 }).catch(() => { });
                await sleep(1500);
                await page.keyboard.press("Escape").catch(() => { });
                const pagesAfter = context.pages();
                if (pagesAfter.length > pagesBefore) {
                    const subTab = pagesAfter[pagesAfter.length - 1];
                    try {
                        await solveInteractiveTab(subTab, label);
                    }
                    finally {
                        await subTab.close().catch(() => { });
                    }
                }
            }
        }
    }
    catch (err) {
        log(`[Quest Resolver] Error: ${err instanceof Error ? err.message : String(err)}`);
    }
}
export async function runActivitiesFlow(context, page, plan, result) {
    log("[Activities] Starting Dashboard Activities flow...");
    const attemptedOffers = new Set((plan.attempted_offers || []).map((x) => normalizeOfferTitle(x)));
    const attemptedQuests = new Set(plan.attempted_quest_tasks || []);
    const beforeSnap = await readDailyProgress(page, plan.known_points_before);
    if (beforeSnap.balance && beforeSnap.balance > 0 && result.points_before <= 0) {
        result.points_before = beforeSnap.balance;
    }
    result.bing_star_points = Math.max(result.bing_star_points, beforeSnap.bing_star_points);
    result.pc_cur = beforeSnap.found_pc ? beforeSnap.pc_cur : result.pc_cur;
    result.pc_max = beforeSnap.found_pc ? beforeSnap.pc_max : result.pc_max;
    result.mobile_cur = beforeSnap.found_mobile ? beforeSnap.mobile_cur : result.mobile_cur;
    result.mobile_max = beforeSnap.found_mobile ? beforeSnap.mobile_max : result.mobile_max;
    await ensureStreakProtection(page);
    await claimAllAvailableRewards(page);
    result.daily_set_done = await runDailySet(context, page, attemptedOffers, result.attempted_offers_added);
    await solveUniversalEarnQuests(context, page, attemptedQuests, result.attempted_quest_tasks_added);
    result.keep_earning_done = await solveKeepEarning(context, page, attemptedOffers, result.attempted_offers_added);
    await claimDashboardPendingPoints(page);
    const afterSnap = await readDailyProgress(page, result.points_before);
    result.edge_browsing_done = afterSnap.edge_browsing_done;
    result.visual_search_done = result.visual_search_done || afterSnap.visual_search_done;
    result.bing_star_points = Math.max(result.bing_star_points, afterSnap.bing_star_points);
    if (afterSnap.found_pc) {
        result.pc_cur = afterSnap.pc_cur;
        result.pc_max = afterSnap.pc_max;
    }
    if (afterSnap.found_mobile) {
        result.mobile_cur = afterSnap.mobile_cur;
        result.mobile_max = afterSnap.mobile_max;
    }
    if (afterSnap.balance && afterSnap.balance > 0) {
        result.points_after = afterSnap.balance;
    }
    result.activities_done = Boolean((afterSnap.daily_set_done || result.daily_set_done) && result.keep_earning_done);
    log(`[Activities] Done. daily_set=${result.daily_set_done}, keep_earning=${result.keep_earning_done}, ` +
        `vs_done=${result.visual_search_done}, points=${result.points_after}`);
}
