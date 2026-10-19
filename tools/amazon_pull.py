"""Pull raw Amazon data (Playwright) for guide products that still miss asin/image/price/stars. Writes .github/amazon-raw/results.json.
No matching decisions here: it only records what Amazon shows (top search cards, or the exact /dp/ASIN page)."""
import glob, json, os, random, re, sys, time
from playwright.sync_api import sync_playwright

LIMIT = int(os.environ.get("LIMIT") or 0)
OUT = ".github/amazon-raw/results.json"
os.makedirs(os.path.dirname(OUT), exist_ok=True)

def tasks():
    out = []
    for p in sorted(glob.glob("src/*.json")):
        g = json.load(open(p, encoding="utf-8"))
        if g.get("retired"): continue
        for pr in g.get("products", []):
            a = str(pr.get("asin") or pr.get("image_asin") or "").upper()
            need_link = not a
            need_img = not pr.get("image")
            need_stats = bool(a) and not (pr.get("amazon_price") and pr.get("amazon_rating") and pr.get("amazon_reviews"))
            if need_link or need_img or need_stats:
                out.append({"path": p, "name": pr["name"], "asin": a, "need": [k for k, v in (("link", need_link), ("image", need_img), ("stats", need_stats)) if v]})
    return out

JS_SEARCH = """() => [...document.querySelectorAll('div[data-component-type="s-search-result"][data-asin]')].slice(0,10).map(d => {
  const q = s => d.querySelector(s);
  const rate = q('span[aria-label*="out of 5 stars"]');
  const cnt = q('a[aria-label*="ratings"], span[aria-label*="ratings"]') || q('span.s-underline-text');
  return {asin: d.getAttribute('data-asin'), title: (q('h2') || {}).innerText || '', image: (q('img.s-image') || {}).src || '',
          price: (q('span.a-price span.a-offscreen') || {}).textContent || '', rating: rate ? rate.getAttribute('aria-label') : '',
          reviews: cnt ? (cnt.getAttribute('aria-label') || cnt.textContent) : '', sponsored: /sponsored/i.test(d.innerText.slice(0,200))};
}).filter(x => x.asin)"""
JS_DP = """() => {
  const t = s => (document.querySelector(s) || {});
  const img = t('#landingImage');
  return {title: (t('#productTitle').textContent || '').trim(), image: img.getAttribute ? (img.getAttribute('data-old-hires') || img.src || '') : '',
          price: (t('#corePrice_feature_div span.a-offscreen').textContent || t('span.a-price span.a-offscreen').textContent || ''),
          rating: (t('#acrPopover').title || ''), reviews: (t('#acrCustomerReviewText').textContent || '')};
}"""

def blocked(page):
    try:
        c = page.content()[:200000].lower()
    except Exception:
        return True
    return "enter the characters you see below" in c or "robot check" in page.title().lower() or "api-services-support@amazon.com" in c

def main():
    ts = tasks()
    if LIMIT: ts = ts[:LIMIT]
    print(f"{len(ts)} products to look up", flush=True)
    res, blocks = [], 0
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = br.new_context(locale="en-US", viewport={"width": 1366, "height": 900},
                             user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
        page = ctx.new_page()
        for i, t in enumerate(ts):
            rec = dict(t, cards=[], dp=None, status="ok")
            for attempt in range(2):
                try:
                    if t["asin"]:
                        page.goto(f"https://www.amazon.com/dp/{t['asin']}", timeout=45000, wait_until="domcontentloaded")
                    else:
                        q = re.sub(r"\s+", "+", re.sub(r"[()/]", " ", t["name"]).strip())
                        page.goto(f"https://www.amazon.com/s?k={q}", timeout=45000, wait_until="domcontentloaded")
                    page.wait_for_timeout(1800)
                    if blocked(page):
                        blocks += 1; rec["status"] = "blocked"
                        print(f"[{i}] BLOCKED {t['name'][:40]}", flush=True)
                        time.sleep(90 if attempt == 0 else 5); continue
                    rec["status"] = "ok"
                    if t["asin"]: rec["dp"] = page.evaluate(JS_DP)
                    else: rec["cards"] = page.evaluate(JS_SEARCH)
                    break
                except Exception as e:
                    rec["status"] = "error: " + str(e)[:100]
            n = len(rec["cards"]) or (1 if (rec["dp"] or {}).get("title") else 0)
            print(f"[{i}] {rec['status']} {n} result(s) :: {t['name'][:50]}", flush=True)
            res.append(rec)
            json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
            if blocks >= 6 and all(r["status"] == "blocked" for r in res[-6:]):
                print("too many blocks in a row - stopping", flush=True); break
            time.sleep(random.uniform(6, 12))
        br.close()
    print("done", len(res), "blocked:", blocks)

main()
