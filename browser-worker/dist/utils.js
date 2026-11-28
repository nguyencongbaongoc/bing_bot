import { setTimeout as timerSleep } from "node:timers/promises";
import fs from "node:fs";
import path from "node:path";
export async function sleep(ms) {
    const duration = Math.max(0, Math.floor(ms));
    if (duration === 0)
        return;
    await timerSleep(duration);
}
export function randInt(min, max) {
    const lo = Math.ceil(min);
    const hi = Math.floor(max);
    return Math.floor(Math.random() * (hi - lo + 1)) + lo;
}
export function randFloat(min, max) {
    return min + Math.random() * (max - min);
}
export async function withTimeout(promise, ms, label) {
    const controller = new AbortController();
    const signal = AbortSignal.any
        ? AbortSignal.any([controller.signal, AbortSignal.timeout(ms)])
        : AbortSignal.timeout(ms);
    let timeoutHandle;
    const timeoutPromise = new Promise((_, reject) => {
        const onAbort = () => reject(new Error(`Timeout after ${ms}ms (${label})`));
        if (signal.aborted) {
            onAbort();
            return;
        }
        signal.addEventListener("abort", onAbort, { once: true });
        timeoutHandle = setTimeout(() => {
            onAbort();
        }, ms + 25);
    });
    try {
        return await Promise.race([promise, timeoutPromise]);
    }
    finally {
        controller.abort();
        if (timeoutHandle)
            clearTimeout(timeoutHandle);
    }
}
export function atomicWriteJson(filePath, data) {
    const resolved = path.resolve(filePath);
    fs.mkdirSync(path.dirname(resolved), { recursive: true });
    const tmpPath = `${resolved}.tmp`;
    fs.writeFileSync(tmpPath, JSON.stringify(data, null, 2), { encoding: "utf-8" });
    fs.renameSync(tmpPath, resolved);
}
export function normalizeOfferTitle(title) {
    return String(title || "")
        .replace(/\(\+\d+\s*pts?\)/gi, "")
        .replace(/\+\d+\s*pts?\b/gi, "")
        .replace(/\s+/g, " ")
        .trim()
        .toLowerCase();
}
export function isClosedOrCrashError(err) {
    const name = err instanceof Error ? err.name.toLowerCase() : "";
    const msg = (err instanceof Error ? err.message : String(err)).toLowerCase();
    return (name.includes("targetclosed") ||
        msg.includes("targetclosed") ||
        msg.includes("target page, context or browser has been closed") ||
        msg.includes("browser has been closed") ||
        msg.includes("page crashed"));
}
export async function safeCloseContext(ctx, timeoutMs = 5000) {
    if (!ctx)
        return;
    try {
        await withTimeout(ctx.close(), timeoutMs, "safeCloseContext");
    }
    catch {
        // Ignore close errors during teardown; Go Job Object cleans up any stray child
    }
}
export function log(msg) {
    const now = new Date();
    const ts = now.toISOString().replace("T", " ").slice(0, 19);
    console.log(`[${ts}] [TS-Worker] ${msg}`);
}
