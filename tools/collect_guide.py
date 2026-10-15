"""Zoe Picks collector: topic -> best sources -> grounded guide JSON -> render.

Runs unattended (GitHub Actions), so it keeps working after any chat/window is closed.
Usage:
  python tools/collect_guide.py                  # next todo topic by priority
  python tools/collect_guide.py --slug best-macbooks-2026
  python tools/collect_guide.py --dry            # collect + build, print result, write nothing
Env: GROQ_API_KEY (or OPENROUTER_API_KEY) for writing; TAVILY_API_KEY optional (best page discovery).

Anti-invention rules (enforced in code, not just in the prompt):
  * a product is kept only if its name appears in the text of >= MIN_SOURCES different sites
  * a price is kept only if its digits appear in a fetched source
  * deal_note / FOMO lines are kept only if the sentence exists in a source
  * fewer than MIN_PRODUCTS survivors -> guide stays a draft ("Coming soon"), nothing is published
"""
import json, os, re, sys, time, pathlib, datetime, subprocess, urllib.parse
import requests
from bs4 import BeautifulSoup

ROOT = pathlib.Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0 (compatible; ZoePicksBot/1.0; +https://zoepicks.github.io)"}
AFF = "iamcartigo-20"
MIN_SOURCES, MIN_PRODUCTS, MAX_PRODUCTS = 2, 3, 6

# Best review/roundup sources per category (hands-on testing sites first), + deal sources for FOMO.
SOURCES = {
    "computers_phones": ["pcworld.com", "tomshardware.com", "tomsguide.com", "notebookcheck.net", "rtings.com", "techradar.com"],
    "mobiles":          ["gsmarena.com", "tomsguide.com", "pcmag.com", "androidauthority.com", "techradar.com", "rtings.com"],
    "monitors":         ["rtings.com", "tomshardware.com", "pcworld.com", "pcmag.com", "techradar.com"],
    "gaming":           ["tomshardware.com", "rtings.com", "pcgamer.com", "ign.com", "techradar.com", "pcworld.com"],
    "electronics":      ["rtings.com", "tomsguide.com", "cnet.com", "pcmag.com", "techradar.com", "theverge.com"],
    "smart_home":       ["rtings.com", "tomsguide.com", "cnet.com", "theverge.com", "techradar.com"],
    "home_kitchen":     ["nytimes.com/wirecutter", "seriouseats.com", "consumerreports.org", "tomsguide.com", "cnet.com", "goodhousekeeping.com"],
    "_default":         ["nytimes.com/wirecutter", "rtings.com", "cnet.com", "pcmag.com", "tomsguide.com", "consumerreports.org"],
}
DEAL_SOURCES = ["techradar.com", "tomsguide.com", "cnet.com", "pcworld.com"]
DEAL_SUBS = {"computers_phones": ["laptopdeals", "buildapcsales"], "monitors": ["buildapcsales"], "gaming": ["buildapcsales", "GameDeals"], "mobiles": ["phonedeals"]}


def log(*a): print("[collect]", *a, flush=True)


# ---------- discovery ----------
def _tavily(q, domain):
    r = requests.post("https://api.tavily.com/search", json={"api_key": os.environ["TAVILY_API_KEY"], "query": q, "include_domains": [domain],
                      "max_results": 2, "search_depth": "basic"}, timeout=30)
    return [x["url"] for x in r.json().get("results", [])]


def _ddg(q, domain):
    r = requests.get("https://html.duckduckgo.com/html/", params={"q": f"{q} site:{domain}"}, headers=UA, timeout=30)
    out = []
    for a in BeautifulSoup(r.text, "lxml").select("a.result__a"):
        u = urllib.parse.parse_qs(urllib.parse.urlparse(a.get("href", "")).query).get("uddg", [a.get("href", "")])[0]
        if domain.split("/")[0] in u: out.append(u)
    return out[:2]


def _tavily_multi(q, domains):
    """ONE Tavily call per topic (free plan = 1000 searches/month): all review domains at once, 1 best URL per domain."""
    r = requests.post("https://api.tavily.com/search", json={"api_key": os.environ["TAVILY_API_KEY"], "query": q, "include_domains": [d.split("/")[0] for d in domains],
                      "max_results": 12, "search_depth": "basic"}, timeout=40)
    r.raise_for_status()
    seen, out = set(), []
    for x in r.json().get("results", []):
        host = next((d.split("/")[0] for d in domains if d.split("/")[0] in x["url"]), None)
        if host and host not in seen: seen.add(host); out.append((host, x["url"]))
    return out


def discover(topic, domains):
    q = re.sub(r"\(\d{4}\)", "", topic["title"]).strip() + " review tested"
    if os.environ.get("TAVILY_API_KEY"):
        try:
            pages = _tavily_multi(q, domains)
            if pages: return pages
        except Exception as e: log("tavily multi fail", e)
    pages = []
    for d in domains:
        try: urls = _tavily(q, d) if os.environ.get("TAVILY_API_KEY") else _ddg(q, d)
        except Exception as e: log("discover fail", d, e); urls = []
        pages += [(d.split("/")[0], u) for u in urls[:1]]
        time.sleep(1)
    return pages


def fetch_text(url, limit=9000):
    try:
        r = requests.get(url, headers=UA, timeout=30)
        if r.status_code != 200: return ""
        s = BeautifulSoup(r.text, "lxml")
        for t in s(["script", "style", "nav", "footer", "aside", "form", "noscript"]): t.decompose()
        txt = re.sub(r"\s+", " ", " ".join(p.get_text(" ", strip=True) for p in s.select("h1,h2,h3,p,li")))
        return txt[:limit]
    except Exception as e:
        log("fetch fail", url, e); return ""


def collect_deals(topic):
    """FOMO raw material: Reddit deal threads (public JSON) + deal-roundup pages. Best effort, may be empty."""
    out = []
    for sub in DEAL_SUBS.get(topic["category"], []):
        try:
            q = re.sub(r"\(\d{4}\)|best", "", topic["title"], flags=re.I).strip()
            j = requests.get(f"https://www.reddit.com/r/{sub}/search.json", params={"q": q, "restrict_sr": 1, "sort": "new", "t": "week", "limit": 8}, headers=UA, timeout=30).json()
            out += [("reddit.com", c["data"]["title"]) for c in j.get("data", {}).get("children", [])]
        except Exception as e: log("reddit fail", sub, e)
    return out


# ---------- LLM ----------
_rot = [0]


def llm(prompt, system):
    """GROQ_API_KEY may hold several keys separated by commas: they are rotated and, on 429/any error, the next one is tried."""
    gk = [k.strip() for k in (os.environ.get("GROQ_API_KEY") or "").split(",") if k.strip()]
    if gk:
        i = _rot[0] % len(gk); gk = gk[i:] + gk[:i]; _rot[0] += 1
    keys = [("https://api.groq.com/openai/v1/chat/completions", k, "llama-3.3-70b-versatile") for k in gk]
    keys.append(("https://openrouter.ai/api/v1/chat/completions", os.environ.get("OPENROUTER_API_KEY"), "meta-llama/llama-3.3-70b-instruct:free"))
    for url, key, model in keys:
        if not key: continue
        try:
            r = requests.post(url, headers={"Authorization": f"Bearer {key}"}, timeout=120, json={
                "model": model, "temperature": 0.2, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]})
            if r.status_code == 200: return json.loads(r.json()["choices"][0]["message"]["content"])
            log("llm", model, r.status_code, r.text[:200])
        except Exception as e: log("llm err", e)
    raise SystemExit("no LLM key worked (set GROQ_API_KEY or OPENROUTER_API_KEY)")


SYSTEM = ("You write buying guides from supplied source excerpts ONLY. Never use outside knowledge. Never invent specs, prices, scores or quotes. "
          "If a detail is not in the excerpts, omit it. Reply with one JSON object.")


def build(topic, pages, texts, deals):
    src_block = "\n\n".join(f"[{i}] ({dom}) {txt}" for i, ((dom, _), txt) in enumerate(zip(pages, texts)) if txt)
    deal_block = "\n".join(f"- ({d}) {t}" for d, t in deals[:15]) or "(none)"
    prompt = f"""Topic: {topic['title']}
Pick {MIN_PRODUCTS}-{MAX_PRODUCTS} products that the sources below recommend. Prefer products recommended by MORE THAN ONE source.
Return JSON: {{"summary": "one sentence", "method": "how picks were made, naming the sites used", "assumptions": "price caveats",
"products": [{{"name": "full product name", "short": "short name", "badge": "e.g. Best Overall", "why": ["2 short paragraphs, facts from sources"],
"specs": ["up to 6 specs, exactly as stated"], "pros": ["3-4"], "cons": ["2-4"], "best_for": "who", "skip_if": "when to skip",
"price": "only if a source states a price, format '$599 (was $699) at Store' or '$599 at Store', else null",
"deal_note": "ONLY a short sentence taken from the deal lines or sources about a discount / limited-time offer, else null",
"sources": [indices of the sources that mention this product]}}]}}

SOURCES:
{src_block}

DEAL LINES (reddit/roundups, may be empty):
{deal_block}"""
    return llm(prompt, SYSTEM)


# ---------- grounding gate ----------
def mentioned(name, text):
    toks = [t for t in re.findall(r"[A-Za-z0-9]+", name.lower()) if len(t) > 1][:3]
    t = text.lower()
    return bool(toks) and all(x in t for x in toks)


def ground(g, pages, texts):
    keep = []
    for p in g.get("products", []):
        doms = {pages[i][0] for i, t in enumerate(texts) if t and mentioned(p.get("name", ""), t)}
        if len(doms) < MIN_SOURCES:
            log("drop (only %d sources):" % len(doms), p.get("name")); continue
        alltxt = " ".join(texts)
        pr = p.get("price")
        if pr and not all(n in alltxt for n in re.findall(r"\$([\d,]+)", pr)): log("price not in sources, removed:", pr); p["price"] = None
        dn = p.get("deal_note")
        if dn and not any(w in alltxt.lower() for w in re.findall(r"[a-z0-9$]{5,}", dn.lower())[:3]): p["deal_note"] = None
        p.pop("sources", None)
        p = {k: v for k, v in p.items() if v not in (None, "", [])}
        p["link"] = "https://www.amazon.com/s?k=" + urllib.parse.quote_plus(p["name"]) + "&tag=" + AFF
        if p.get("price"): p["price_asof"] = datetime.date.today().strftime("%b %-d, %Y")
        keep.append(p)
    return keep[:MAX_PRODUCTS]


# ---------- plan / io ----------
def pick_topic(slug=None, skip=()):
    plan = json.loads((ROOT / "plan/topics.json").read_text())
    c = [t for t in plan if (t["slug"] == slug if slug else t.get("status") == "todo" and t["slug"] not in skip)]
    if not c: raise SystemExit("no matching/todo topic")
    c.sort(key=lambda t: (t.get("priority", 999), not t.get("high_ticket", False)))
    return plan, c[0]


def run_topic(plan, topic, dry):
    log("topic:", topic["title"])
    domains = SOURCES.get(topic["category"], SOURCES["_default"])
    pages = discover(topic, domains)
    texts = [fetch_text(u) for _, u in pages]
    ok = sum(bool(t) for t in texts)
    log(f"{ok}/{len(pages)} source pages fetched")
    if ok < MIN_SOURCES: raise SystemExit("not enough sources fetched; topic left as todo")
    g = build(topic, pages, texts, collect_deals(topic))
    products = ground(g, pages, texts)
    today = datetime.date.today().strftime("%b %-d, %Y")
    out = {"slug": topic["slug"], "title": topic["title"], "category": topic["category"], "date": today,
           "summary": g.get("summary", ""), "method": g.get("method", ""), "assumptions": g.get("assumptions", ""), "products": products}
    if any(k in topic["slug"] for k in ("black-friday", "deals")):
        out.update({"countdown_to": "2026-11-27", "countdown_label": "Black Friday in", "expires": "2026-12-01", "live_label": "Black Friday deals are live", "theme": "deal"})
    if len(products) < MIN_PRODUCTS:
        log(f"only {len(products)} well-sourced products -> staying draft")
        out["draft"] = True; out["summary"] = "We're still testing and researching this guide. Picks will be added soon."; out["products"] = []
    if dry: print(json.dumps(out, indent=1)); return
    (ROOT / "src" / f"{topic['slug']}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    for t in plan:
        if t["slug"] == topic["slug"] and not out.get("draft"): t["status"] = "done"
        elif t["slug"] == topic["slug"]: t["last_try"] = today
    (ROOT / "plan/topics.json").write_text(json.dumps(plan, indent=1))
    subprocess.run([sys.executable, str(ROOT / "tools/render_guide.py"), "--all", "https://zoepicks.github.io"], check=True)
    log("done:", topic["slug"], "draft" if out.get("draft") else f"{len(products)} products")


def _arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main():
    """--count N builds up to N guides in one run (a failed topic is skipped for this run, not retried in a loop);
    --gap S pauses S seconds between guides (free-tier LLM rate limits). Stops after 3 failures in a row."""
    dry = "--dry" in sys.argv
    slug = _arg("--slug")
    count = max(1, int(_arg("--count", 1)))
    gap = int(_arg("--gap", 45))
    tried, made, fails_in_row = set(), 0, 0
    for i in range(count):
        try:
            plan, topic = pick_topic(slug, tried)
        except SystemExit as e:
            log("stop:", e); break
        tried.add(topic["slug"])
        try:
            run_topic(plan, topic, dry)
            made += 1; fails_in_row = 0
        except (Exception, SystemExit) as e:
            fails_in_row += 1
            log(f"FAILED {topic['slug']}: {e}")
            if count == 1: raise
            if fails_in_row >= 3: log("3 failures in a row -> stopping this run"); break
        if slug or i == count - 1: break
        time.sleep(gap)
    log(f"run finished: {made} guide(s) built, {len(tried)} topic(s) tried")
    if made == 0 and count > 1: sys.exit(1)


if __name__ == "__main__":
    main()
