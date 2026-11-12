import sys
from patchright.sync_api import sync_playwright

from core.paths import MOBILE_PROFILE, get_edge_executable_path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

with sync_playwright() as p:
    device = dict(p.devices['iPhone 14 Pro'])
    device.pop('default_browser_type', None)
    
    edge_path = get_edge_executable_path()
    launch_kwargs = {
        "user_data_dir": str(MOBILE_PROFILE),
        "headless": True,
        **device,
    }
    
    if edge_path:
        launch_kwargs["executable_path"] = edge_path
    else:
        launch_kwargs["channel"] = "msedge"
    
    ctx = p.chromium.launch_persistent_context(**launch_kwargs)
    page = ctx.new_page()

    url = 'https://www.msn.com/en-us/sports/soccer/kane-wins-2026-ballon-d-or-result-leaked-as-rankings-announced/ar-AA2g7rsk'
    page.goto(url, wait_until='domcontentloaded')
    page.wait_for_timeout(3000)

    # Click continue reading
    page.evaluate('''() => {
        function queryAllDeep(root = document) {
            let els = [];
            const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, null, false);
            let n = walker.nextNode();
            while (n) {
                els.push(n);
                if (n.shadowRoot) els = els.concat(queryAllDeep(n.shadowRoot));
                n = walker.nextNode();
            }
            return els;
        }
        for (const el of queryAllDeep(document)) {
            if ((el.innerText || '').trim() === 'Continue reading') {
                el.click();
                break;
            }
        }
    }''')
    page.wait_for_timeout(1000)

    # Scroll reading for 8 seconds
    for _ in range(8):
        page.mouse.wheel(0, 300)
        page.wait_for_timeout(1000)

    items = page.evaluate('''() => {
        function queryAllDeep(root = document) {
            let els = [];
            const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, null, false);
            let n = walker.nextNode();
            while (n) {
                els.push(n);
                if (n.shadowRoot) els = els.concat(queryAllDeep(n.shadowRoot));
                n = walker.nextNode();
            }
            return els;
        }
        const tray = queryAllDeep(document).find(e => e.tagName.toLowerCase() === 'action-tray');
        if (!tray) return [];
        return queryAllDeep(tray).map(e => ({
            tag: e.tagName.toLowerCase(),
            cls: e.className,
            id: e.id,
            text: (e.innerText || '').trim(),
            aria: e.getAttribute('aria-label') || '',
            rect: e.getBoundingClientRect()
        }));
    }''')

    print(f"Action tray elements ({len(items)}):")
    for it in items:
        print(" ", it)

    ctx.close()

