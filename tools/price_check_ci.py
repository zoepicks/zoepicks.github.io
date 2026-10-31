"""CI price check: opens each ASIN on amazon.com with real headless Chromium (Playwright) and records what Amazon itself shows.
Used to verify a Deal Reel's price/discount against the live page. Writes price_check_result.json (+ a screenshot of the price block per ASIN).
Usage: python tools/price_check_ci.py B09W9G8WCC,B09DPKMYHT      (or env ASINS)"""
import json, os, re, sys, time
from datetime import datetime, timezone


def _money(txt):
    m = re.search(r"\$\s*([\d,]+(?:\.\d{1,2})?)", txt or "")
    return float(m.group(1).replace(",", "")) if m else None


def check(page, asin, shot_dir):
    out = {"asin": asin, "url": f"https://www.amazon.com/dp/{asin}", "checked_at": datetime.now(timezone.utc).isoformat()}
    try:
        page.goto(out["url"], wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3500)
    except Exception as e:
        out["error"] = f"goto failed: {type(e).__name__}: {e}"[:200]
        return out
    body = page.content()
    out["final_url"] = page.url
    out["robot_check"] = bool(re.search(r"Enter the characters you see below|Type the characters|api-services-support@amazon|/errors/validateCaptcha", body))
    out["page_title"] = (page.title() or "")[:160]
    if out["robot_check"]:
        out["error"] = "Amazon robot/captcha page - no price read"
        return out

    def txt(sel):
        try:
            el = page.query_selector(sel)
            return (el.inner_text() or "").strip() if el else ""
        except Exception:
            return ""

    def attr_text(sel):
        try:
            el = page.query_selector(sel)
            return (el.text_content() or "").strip() if el else ""
        except Exception:
            return ""

    out["title"] = txt("#productTitle")[:200]
    # price actually payable (buy box) - same element the scanner anchors on
    pay = ""
    for sel in ("#corePriceDisplay_desktop_feature_div .priceToPay .a-offscreen", "#corePrice_feature_div .priceToPay .a-offscreen",
                ".priceToPay .a-offscreen", "#apex_desktop .priceToPay .a-offscreen"):
        pay = attr_text(sel)
        if pay:
            break
    if not pay:     # visible digits fallback
        whole = txt(".priceToPay .a-price-whole").replace("\n", "").replace(",", "").strip(".")
        frac = txt(".priceToPay .a-price-fraction")
        if whole:
            pay = f"${whole}.{frac or '00'}"
    out["price_text"] = pay
    out["price"] = _money(pay)
    lst = ""
    for sel in ("#corePriceDisplay_desktop_feature_div .basisPrice .a-offscreen", ".basisPrice .a-offscreen",
                "#corePrice_feature_div .a-price.a-text-price .a-offscreen", "#listPrice", ".a-price.a-text-price[data-a-strike='true'] .a-offscreen"):
        lst = attr_text(sel)
        if lst:
            break
    out["list_price_text"] = lst
    out["list_price"] = _money(lst)
    sav = txt(".savingsPercentage") or txt("#corePriceDisplay_desktop_feature_div .savingPriceOverride")
    out["savings_text"] = sav
    if out["price"] and out["list_price"] and out["list_price"] > out["price"]:
        out["computed_discount_pct"] = round((1 - out["price"] / out["list_price"]) * 100, 1)
    out["availability"] = txt("#availability")[:120]
    out["seller"] = txt("#merchant-info")[:160] or txt("#sellerProfileTriggerId")[:120]
    out["deal_badge"] = txt("#dealBadge_feature_div")[:80] or txt(".dealBadge")[:80]
    out["coupon"] = txt("#couponBadgeRegularVpc")[:80] or txt("#couponTextpct")[:80]
    out["other_buying_options"] = txt("#buybox-see-all-buying-choices")[:80]
    try:
        blk = page.query_selector("#corePriceDisplay_desktop_feature_div") or page.query_selector("#corePrice_feature_div") or page.query_selector("#apex_desktop")
        if blk:
            os.makedirs(shot_dir, exist_ok=True)
            blk.screenshot(path=os.path.join(shot_dir, f"{asin}.png"))
            out["screenshot"] = f"{asin}.png"
    except Exception as e:
        out["screenshot_error"] = str(e)[:100]
    if not out["price"]:
        out["note"] = "price block not found (variant picker / unavailable / new markup) - see page_title + availability"
    return out


def main():
    asins = [a.strip() for a in (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ASINS", "")).replace(" ", ",").split(",") if a.strip()]
    res = {"started": datetime.now(timezone.utc).isoformat(), "results": []}
    shot_dir = "price_check_shots"
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = br.new_context(locale="en-US", timezone_id="America/New_York", viewport={"width": 1366, "height": 900},
                                 user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
            page = ctx.new_page()
            for a in asins:
                res["results"].append(check(page, a, shot_dir))
                time.sleep(2.5)
            br.close()
    except Exception as e:
        res["fatal"] = f"{type(e).__name__}: {e}"[:300]
    res["finished"] = datetime.now(timezone.utc).isoformat()
    json.dump(res, open("price_check_result.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
