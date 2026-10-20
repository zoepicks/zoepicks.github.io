"""Pull raw Amazon data (Playwright) for guide products that still miss asin/image/price/stars. Writes .github/amazon-raw/results.json.
No matching decisions here: it only records what Amazon shows (top search cards, or the exact /dp/ASIN page)."""
import glob, json, os, random, re, sys, time
from playwright.sync_api import sync_playwright

LIMIT = int(os.environ.get("LIMIT") or 0)
MODE = os.environ.get("MODE") or "all"
PICKS = MODE == "picks"
DEEP = MODE in ("deep", "queries")
OUT = ".github/amazon-raw/results-%s.json" % MODE if MODE in ("picks", "deep", "queries") else ".github/amazon-raw/results.json"

def variants(name, path):
    n = name.replace("\u2011", "-").replace("\u2013", "-").replace("\u2019", "'")
    n = re.sub(r"\(sold as[^)]*\)", "", n, flags=re.I)
    n = re.sub(r"\bTemplate Vector\b", "", n, flags=re.I)
    base = re.sub(r"\s+", " ", re.sub(r"\(([^)]*)\)", r" \1 ", n)).strip()
    cat = re.sub(r"^best-|-20\d\d$", "", os.path.basename(path)[:-5]).replace("-", " ")
    cat1 = cat.split()[-1] if cat else ""
    toks = base.replace("/", " ").split()
    model = [w for w in toks if re.search(r"\d", w)]
    v = [base]
    if "/" in base: v.append(base.split("/")[0].strip())
    if cat1 and cat1.lower().rstrip("s") not in base.lower(): v.append(base + " " + cat)
    if toks and model: v.append(toks[0] + " " + " ".join(model[:3]))
    out = []
    for x in v:
        if x and x not in out: out.append(x)
    return out[:3]
os.makedirs(os.path.dirname(OUT), exist_ok=True)

def tasks():
    if PICKS: return json.load(open(".github/amazon-raw/picks.json", encoding="utf-8"))
    if MODE == "queries": return json.load(open(".github/amazon-raw/queries.json", encoding="utf-8"))
    if DEEP:
        out = []
        for p in sorted(glob.glob("src/*.json")):
            for pr in json.load(open(p, encoding="utf-8")).get("products", []):
                if not (pr.get("asin") or pr.get("image_asin")):
                    out.append({"path": p, "name": pr["name"], "asin": "", "need": ["link"], "queries": variants(pr["name"], p)})
        return out
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
  const h2 = q('h2'), im = q('img.s-image');
  const title = [h2 && h2.getAttribute('aria-label'), im && im.alt, h2 && h2.innerText].filter(Boolean).sort((a,b) => b.length - a.length)[0] || '';
  const rate = q('span.a-icon-alt');
  const cnt = q('a[aria-label*="rating"], span[aria-label*="rating"]') || q('span.s-underline-text');
  return {asin: d.getAttribute('data-asin'), title: title, image: im ? im.src : '',
          price: (q('span.a-price span.a-offscreen') || {}).textContent || '', rating: rate ? rate.textContent : '',
          reviews: cnt ? (cnt.getAttribute('aria-label') || cnt.textContent) : '', sponsored: /sponsored/i.test(d.innerText.slice(0,200))};
}).filter(x => x.asin)"""
JS_DP = """() => {
  const t = s => (document.querySelector(s) || {});
  const img = t('#landingImage');
  const rows = [...document.querySelectorAll('#productDetails_techSpec_section_1 tr, #productDetails_detailBullets_sections1 tr, #prodDetails tr')].map(r => r.innerText.replace(/\\s+/g, ' ').trim()).filter(x => x && x.length < 160).slice(0, 25);
  const bl = [...document.querySelectorAll('#feature-bullets li')].map(x => x.innerText.replace(/\\s+/g, ' ').trim()).filter(Boolean).slice(0, 8);
  return {title: (t('#productTitle').textContent || '').trim(), image: img.getAttribute ? (img.getAttribute('data-old-hires') || img.src || '') : '',
          price: (t('#corePrice_feature_div span.a-offscreen').textContent || t('span.a-price span.a-offscreen').textContent || ''),
          rating: (t('#acrPopover').title || ''), reviews: (t('#acrCustomerReviewText').textContent || ''),
          brand: (t('#bylineInfo').textContent || '').trim(), bullets: bl, details: rows, avail: (t('#availability').innerText || '').trim().slice(0, 80)};
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
            if DEEP:
                seen = {}
                for qi, qtext in enumerate(t["queries"]):
                    for attempt in range(2):
                        try:
                            q = re.sub(r"\s+", "+", re.sub(r"[()/]", " ", qtext).strip())
                            page.goto(f"https://www.amazon.com/s?k={q}", timeout=45000, wait_until="domcontentloaded")
                            page.wait_for_timeout(1800)
                            if blocked(page):
                                blocks += 1; rec["status"] = "blocked"; time.sleep(90 if attempt == 0 else 5); continue
                            rec["status"] = "ok"
                            for c in page.evaluate(JS_SEARCH)[:8]:
                                if c["asin"] not in seen: seen[c["asin"]] = dict(c, q=qi)
                            break
                        except Exception as e:
                            rec["status"] = "error: " + str(e)[:100]
                    time.sleep(random.uniform(3, 6))
                rec["cards"] = list(seen.values())
            else:
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
