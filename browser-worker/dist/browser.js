import crypto from "node:crypto";
import fs from "node:fs";
import { chromium, devices } from "patchright";
import { log, withTimeout } from "./utils.js";
const FIRST_PARTY_ALLOWLIST = [
    "bing.com",
    "microsoft.com",
    "live.com",
    "msn.com",
    "bing.net",
    "msecnd.net",
    "microsoftonline.com",
    "azureedge.net",
    "aspnetcdn.com",
    "sfx.ms",
    "office.com",
    "office.net",
    "sharepoint.com",
];
const BLOCKED_AD_DOMAINS = [
    "doubleclick.net",
    "googlesyndication.com",
    "taboola.com",
    "outbrain.com",
];
function hostMatches(hostname, domains) {
    if (!hostname)
        return false;
    const h = hostname.toLowerCase().replace(/^\.+|\.+$/g, "");
    return domains.some((d) => h === d || h.endsWith(`.${d}`));
}
function shouldAbortRoute(urlStr, resourceType) {
    let host = "";
    try {
        host = new URL(urlStr).hostname || "";
    }
    catch {
        host = "";
    }
    if (hostMatches(host, FIRST_PARTY_ALLOWLIST))
        return false;
    if ((resourceType || "").toLowerCase() === "media")
        return true;
    if (hostMatches(host, BLOCKED_AD_DOMAINS))
        return true;
    return false;
}
function buildStealthScript(account, isMobile) {
    const hash = crypto.createHash("sha256").update(account || "default").digest();
    const cores = isMobile ? 8 : [8, 12, 16][hash[0] % 3];
    const mem = isMobile ? 8 : [8, 16][hash[1] % 2];
    const rNoise = ((hash[2] / 255) - 0.5) * 0.0002;
    const gNoise = ((hash[3] / 255) - 0.5) * 0.0002;
    const bNoise = ((hash[4] / 255) - 0.5) * 0.0002;
    const platformVal = isMobile ? "Linux armv8l" : "Win32";
    const uaPlatform = isMobile ? "Android" : "Windows";
    const modelVal = isMobile ? "Pixel 7" : "";
    const platformVer = isMobile ? "14.0.0" : "15.0.0";
    const archVal = isMobile ? "arm" : "x86";
    const mobStr = isMobile ? "true" : "false";
    return `(() => {
    try {
      delete Object.getPrototypeOf(navigator).webdriver;
      Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
      Object.defineProperty(navigator, 'platform', { get: () => '${platformVal}' });
      Object.defineProperty(navigator, 'languages', { get: () => ['vi-VN', 'vi', 'en-US', 'en'] });
      Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => ${cores} });
      Object.defineProperty(navigator, 'deviceMemory', { get: () => ${mem} });
      if (window.outerWidth === 0 || window.outerHeight === 0) {
        Object.defineProperty(window, 'outerWidth', { get: () => window.innerWidth });
        Object.defineProperty(window, 'outerHeight', { get: () => window.innerHeight + 85 });
      }
      const mockUAData = {
        brands: [
          { brand: "Microsoft Edge", version: "131" },
          { brand: "Chromium", version: "131" },
          { brand: "Not_A Brand", version: "24" }
        ],
        mobile: ${mobStr},
        platform: "${uaPlatform}",
        toJSON: function() { return { brands: this.brands, mobile: this.mobile, platform: this.platform }; },
        getHighEntropyValues: function() {
          return Promise.resolve({
            brands: this.brands,
            mobile: ${mobStr},
            platform: "${uaPlatform}",
            platformVersion: "${platformVer}",
            architecture: "${archVal}",
            bitness: "64",
            model: "${modelVal}",
            uaFullVersion: "131.0.2903.86"
          });
        }
      };
      Object.defineProperty(navigator, 'userAgentData', { get: () => mockUAData, configurable: true });
      const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
      HTMLCanvasElement.prototype.toDataURL = function(type, ...args) {
        try {
          const ctx = this.getContext('2d');
          if (ctx && this.width > 0 && this.height > 0) {
            const imgData = ctx.getImageData(0, 0, Math.min(this.width, 10), Math.min(this.height, 10));
            for (let i = 0; i < imgData.data.length; i += 4) {
              imgData.data[i] = Math.min(255, Math.max(0, imgData.data[i] + ${rNoise}));
              imgData.data[i + 1] = Math.min(255, Math.max(0, imgData.data[i + 1] + ${gNoise}));
              imgData.data[i + 2] = Math.min(255, Math.max(0, imgData.data[i + 2] + ${bNoise}));
            }
            ctx.putImageData(imgData, 0, 0);
          }
        } catch {}
        return origToDataURL.apply(this, [type, ...args]);
      };
    } catch {}
  })();`;
}
async function attachStealthAndBlocker(ctx, account, isMobile) {
    await ctx.addInitScript(buildStealthScript(account, isMobile));
    await ctx.route("**/*", (route) => {
        try {
            const req = route.request();
            if (shouldAbortRoute(req.url(), req.resourceType())) {
                void route.abort().catch(() => { });
            }
            else {
                void route.continue().catch(() => { });
            }
        }
        catch {
            void route.continue().catch(() => { });
        }
    });
}
export function buildDesktopArgs(headless) {
    const base = [
        "--disable-gpu-vsync",
        "--frame-rate-limit=15",
        "--blink-settings=imagesEnabled=true",
    ];
    if (headless) {
        return [...base, "--headless=new", "--disable-gpu", "--disable-software-rasterizer"];
    }
    // Headed mode (used for Visual Search) strips GPU disabling flags to prevent Edge renderer crashes
    return [...base, "--start-maximized"];
}
export async function launchDesktopContext(profileDir, headless, account) {
    fs.mkdirSync(profileDir, { recursive: true });
    const args = buildDesktopArgs(headless);
    log(`Launching Desktop Edge context (headless=${headless}, profile=${profileDir})`);
    const context = await withTimeout(chromium.launchPersistentContext(profileDir, {
        channel: "msedge",
        headless,
        args,
        ignoreDefaultArgs: headless ? ["--headless"] : undefined,
        viewport: headless ? { width: 1366, height: 768 } : null,
        locale: "vi-VN",
        timezoneId: "Asia/Ho_Chi_Minh",
        extraHTTPHeaders: {
            "Sec-CH-UA-Platform": '"Windows"',
            "Sec-CH-UA-Mobile": "?0",
        },
    }), 60000, "launchDesktopContext");
    await attachStealthAndBlocker(context, account, false);
    const page = context.pages()[0] ?? (await context.newPage());
    page.setDefaultTimeout(10000);
    return { context, page };
}
export async function launchMobileContext(profileDir, headless, account) {
    fs.mkdirSync(profileDir, { recursive: true });
    const pixel7 = devices["Pixel 7"] || {};
    const ua = pixel7.userAgent && pixel7.userAgent.includes("EdgA")
        ? pixel7.userAgent
        : "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36 EdgA/131.0.0.0";
    const args = [
        "--disable-gpu-vsync",
        "--frame-rate-limit=15",
        "--blink-settings=imagesEnabled=true",
        ...(headless ? ["--headless=new", "--disable-gpu", "--disable-software-rasterizer"] : []),
    ];
    log(`Launching Mobile Edge context (headless=${headless}, profile=${profileDir})`);
    const context = await withTimeout(chromium.launchPersistentContext(profileDir, {
        channel: "msedge",
        headless,
        args,
        ignoreDefaultArgs: headless ? ["--headless"] : undefined,
        userAgent: ua,
        viewport: { width: 412, height: 915 },
        screen: { width: 412, height: 915 },
        deviceScaleFactor: 2.625,
        isMobile: true,
        hasTouch: true,
        locale: "vi-VN",
        timezoneId: "Asia/Ho_Chi_Minh",
        extraHTTPHeaders: {
            "Sec-CH-UA-Platform": '"Android"',
            "Sec-CH-UA-Mobile": "?1",
            "Sec-CH-UA-Model": '"Pixel 7"',
        },
    }), 60000, "launchMobileContext");
    await attachStealthAndBlocker(context, account, true);
    const page = context.pages()[0] ?? (await context.newPage());
    page.setDefaultTimeout(10000);
    return { context, page };
}
