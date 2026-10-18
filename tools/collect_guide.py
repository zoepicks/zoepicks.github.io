"""Zoe Picks collector: topic -> best sources -> grounded guide JSON -> render.

Runs unattended (GitHub Actions), so it keeps working after any chat/window is closed.
Usage:
  python tools/collect_guide.py                  # next todo topic by priority
  python tools/collect_guide.py --slug best-macbooks-2026
  python tools/collect_guide.py --dry            # collect + build, print result, write nothing
Env: GROQ_API_KEY (or OPENROUTER_API_KEY) for writing; TAVILY_API_KEY optional (best page discovery).
Fallbacks: Tavily -> DuckDuckGo (requests) -> Playwright browser search (DDG, Bing); blocked pages are re-fetched in Playwright.

Anti-invention rules (enforced in code, not just in the prompt):
  * a product is kept only if its name appears in the text of >= MIN_SOURCES different sites
  * a price is kept only if its digits appear in a fetched source
  * deal_note / FOMO lines are kept only if the sentence exists in a source
  * fewer than MIN_PRODUCTS survivors -> guide stays a draft ("Coming soon"), nothing is published
"""
import json, os, re, sys, time, pathlib, datetime, subprocess, urllib.parse, atexit, base64
import requests
from bs4 import BeautifulSoup

ROOT = pathlib.Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0 (compatible; ZoePicksBot/1.0; +https://zoepicks.github.io)"}
AFF = "iamcartigo-20"
MIN_SOURCES, MIN_PRODUCTS, MAX_PRODUCTS = 2, 3, 5   # publish floor 3, cap 5
ASK_MIN = 4   # the LLM is asked for 4-5 best picks per guide (the 'best 4-5' under the Top-3 discounts)
# Groq free tier = 8000 tokens/min AND per request, counted as prompt + max_tokens. Every call is kept under TOKEN_BUDGET so it can
# never hit the per-minute cap or answer 413. Tokens are estimated at CHARS_PER_TOKEN (3.0 = deliberately pessimistic; real text is ~4).
TOKEN_BUDGET = 7000
MAX_OUT_TOKENS = 3400
CHARS_PER_TOKEN = 3.0
DEAL_LINES_MAX, DEAL_CHARS_MAX = 10, 900
MIN_TEXT = 400  # fetched page text shorter than this = blocked/empty -> retry in a real browser
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# Best review/roundup sources per category (hands-on testing sites first), + deal sources for FOMO.
SOURCES = {
    "laptops": ["pcworld.com", "tomshardware.com", "tomsguide.com", "notebookcheck.net", "rtings.com", "techradar.com"],
    "mobiles":          ["gsmarena.com", "tomsguide.com", "pcmag.com", "androidauthority.com", "techradar.com", "rtings.com"],
    "monitors":         ["rtings.com", "tomshardware.com", "pcworld.com", "pcmag.com", "techradar.com"],
    "gaming":           ["tomshardware.com", "rtings.com", "pcgamer.com", "ign.com", "techradar.com", "pcworld.com"],
    "electronics":      ["rtings.com", "tomsguide.com", "cnet.com", "pcmag.com", "techradar.com", "theverge.com"],
    "smart_home":       ["rtings.com", "tomsguide.com", "cnet.com", "theverge.com", "techradar.com"],
    "home_kitchen":     ["nytimes.com/wirecutter", "seriouseats.com", "consumerreports.org", "tomsguide.com", "cnet.com", "goodhousekeeping.com"],
    "_default":         ["nytimes.com/wirecutter", "rtings.com", "cnet.com", "pcmag.com", "tomsguide.com", "consumerreports.org"],
}
DEAL_SOURCES = ["techradar.com", "tomsguide.com", "cnet.com", "pcworld.com"]
DEAL_SUBS = {"laptops": ["laptopdeals", "buildapcsales"], "monitors": ["buildapcsales"], "gaming": ["buildapcsales", "GameDeals"], "mobiles": ["phonedeals"]}


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


# ---------- browser fallback (Playwright, optional) ----------
_pw = {}


def _browser():
    """Lazy single headless Chromium for the whole run. Returns None if Playwright is not installed."""
    if "ctx" in _pw: return _pw["ctx"]
    _pw["ctx"] = None
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        br = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        _pw.update(pw=pw, br=br, ctx=br.new_context(user_agent=BROWSER_UA, locale="en-US", viewport={"width": 1366, "height": 900}))
        atexit.register(_browser_close)
    except Exception as e:
        log("playwright unavailable:", e)
    return _pw["ctx"]


def _browser_close():
    for k in ("br", "pw"):
        try: _pw[k].close() if k == "br" else _pw[k].stop()
        except Exception: pass


def _browser_html(url, wait_ms=2500):
    ctx = _browser()
    if not ctx: return ""
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(wait_ms)
        return page.content()
    except Exception as e:
        log("browser fail", url, e); return ""
    finally:
        try: page.close()
        except Exception: pass


def _unwrap(href):
    """Search engines wrap result links: DuckDuckGo (uddg=) and Bing (ck/a?u=a1<base64>)."""
    q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
    if "uddg" in q: return q["uddg"][0]
    if "bing.com/ck/a" in href and "u" in q:
        u = q["u"][0]
        if u.startswith("a1"):
            try: return base64.urlsafe_b64decode(u[2:] + "=" * (-len(u[2:]) % 4)).decode()
            except Exception: pass
    return href


def _pick_per_domain(urls, domains):
    seen, out = set(), []
    for u in urls:
        host = next((d.split("/")[0] for d in domains if d.split("/")[0] in u), None)
        if host and host not in seen and u.startswith("http"): seen.add(host); out.append((host, u))
    return out


def _browser_search(q, domains):
    """ONE site:-OR query in a real browser (DuckDuckGo, then Bing); first URL per review domain."""
    sites = " OR ".join("site:" + d for d in domains)
    qq = urllib.parse.quote_plus(f"{q} ({sites})")
    engines = [("https://html.duckduckgo.com/html/?q=" + qq, "a.result__a"),
               ("https://www.bing.com/search?q=" + qq, "li.b_algo h2 a")]
    for url, sel in engines:
        html = _browser_html(url, wait_ms=1500)
        urls = [_unwrap(a.get("href", "")) for a in BeautifulSoup(html, "lxml").select(sel)] if html else []
        pages = _pick_per_domain(urls, domains)
        log("browser search", url.split("/")[2], "->", len(pages), "pages")
        if pages: return pages
    return []


def _kw(topic):
    """Search keywords come from the slug (stable product words), not the display title - titles are now varied
    ('Top Air Fryers for Fast Weeknight Dinners') and would make noisy search queries."""
    return re.sub(r"\s+", " ", re.sub(r"^best-|-?\d{4}$", "", topic["slug"]).replace("-", " ")).strip()


def discover(topic, domains):
    q = _kw(topic) + " review tested"
    # 1) Tavily: ONE call per topic. (No per-domain Tavily retry any more - it only burns quota when the key/plan is the problem.)
    if os.environ.get("TAVILY_API_KEY"):
        try:
            pages = _tavily_multi(q, domains)
            if pages: log("tavily ->", len(pages), "pages"); return pages
            log("tavily returned 0 pages")
        except Exception as e: log("tavily multi fail", e)
    # 2) plain DuckDuckGo over requests (often blocked on GitHub runners)
    pages = []
    for d in domains:
        try: urls = _ddg(q, d)
        except Exception as e: log("ddg fail", d, e); urls = []
        pages += [(d.split("/")[0], u) for u in urls[:1]]
        time.sleep(1)
    if pages: log("ddg ->", len(pages), "pages"); return pages
    # 3) real browser search
    log("no pages from Tavily/DDG -> browser search fallback")
    return _browser_search(q, domains)


def _extract(html, limit):
    s = BeautifulSoup(html, "lxml")
    for t in s(["script", "style", "nav", "footer", "aside", "form", "noscript"]): t.decompose()
    txt = re.sub(r"\s+", " ", " ".join(p.get_text(" ", strip=True) for p in s.select("h1,h2,h3,p,li")))
    return txt[:limit]


def fetch_text(url, limit=9000):
    txt = ""
    try:
        r = requests.get(url, headers=UA, timeout=30)
        if r.status_code == 200: txt = _extract(r.text, limit)
        else: log("http", r.status_code, url)
    except Exception as e: log("fetch fail", url, e)
    if len(txt) < MIN_TEXT:  # blocked (403/captcha/JS-only) -> try a real browser
        html = _browser_html(url)
        if html:
            b = _extract(html, limit)
            log("browser fetch", len(b), "chars:", url)
            if len(b) > len(txt): txt = b
    return txt


def collect_deals(topic):
    """FOMO raw material: Reddit deal threads (public JSON) + deal-roundup pages. Best effort, may be empty."""
    out = []
    for sub in DEAL_SUBS.get(topic["category"], []):
        try:
            q = _kw(topic)
            j = requests.get(f"https://www.reddit.com/r/{sub}/search.json", params={"q": q, "restrict_sr": 1, "sort": "new", "t": "week", "limit": 8}, headers=UA, timeout=30).json()
            out += [("reddit.com", c["data"]["title"]) for c in j.get("data", {}).get("children", [])]
        except Exception as e: log("reddit fail", sub, e)
    return out


# ---------- LLM ----------
_rot = [0]
_dead = set()   # (key tail, model) pairs that answered 404/403 "no access" - never retried in this run
# NOTE (2026-10-17): Groq retired llama-3.3-70b-versatile + llama-3.1-8b-instant (16 Aug 2026) and qwen3-32b + llama-4-scout (17 Jul 2026);
# they answer 404 model_not_found. Only the IDs below are live. Groq limits are per ORGANIZATION per model, so extra keys from the same
# org add no capacity - extra MODELS do (each has its own 200k TPD / 8k TPM on the free tier).
# Groq models to try per key, in order. A key that cannot use one model (404 model_not_found) falls through to the next;
# Groq also rate-limits per model, so a 429 on one model just moves on to the next one. Override with env GROQ_MODELS.
GROQ_MODELS = [m.strip() for m in (os.environ.get("GROQ_MODELS") or
               "openai/gpt-oss-120b,openai/gpt-oss-20b,qwen/qwen3.6-27b,qwen/qwen3.8-27b").split(",") if m.strip()]


def _parse_json(txt):
    txt = re.sub(r"<think>.*?</think>", "", txt or "", flags=re.S).strip()
    try: return json.loads(txt)
    except Exception:
        m = re.search(r"\{.*\}", txt, re.S)
        return json.loads(m.group(0))


_diag = {"daily": {}, "toolarge": {}, "minute": {}, "noaccess": {}, "ok": set()}   # "keytail|model" -> "used/limit" text, for the end-of-run summary (per MODEL: Groq limits are per model)


def _nums(body):
    """Groq limit errors say 'Limit 200000, Used 199100, Requested 5400' -> compact text."""
    m = re.search(r"Limit (\d+), Used (\d+), Requested (\d+)", body or "")
    return f"limit {int(m.group(1)):,} used {int(m.group(2)):,} requested {int(m.group(3)):,}" if m else ""


class LLMDown(SystemExit):
    """Every key/model is out of DAILY tokens - nothing more can be written today, stop the whole run (not a topic fault)."""


def _retry_secs(txt):
    """Groq 429 text says 'Please try again in 7m12.5s' / '850ms' / '12.3s' -> seconds (None if absent)."""
    m = re.search(r"try again in ([0-9hms.]+)", txt or "")
    if not m: return None
    tot = 0.0
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", m.group(1)):
        tot += float(num) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return tot or None


def est_tokens(*parts):
    return int(sum(len(p or "") for p in parts) / CHARS_PER_TOKEN) + 1


def llm(prompt, system, max_out=MAX_OUT_TOKENS):
    """GROQ_API_KEY may hold several keys separated by commas: they are rotated; for each key every model in GROQ_MODELS is tried.
    403/404 = no access -> that (key, model) is dead for the run. 429 per-minute -> wait the time Groq asks (up to 90 s) and go round again;
    429 per-DAY -> dead for the run. 413 / transient 400 -> skip for this call only. OpenRouter is the last fallback."""
    gk = [k.strip() for k in (os.environ.get("GROQ_API_KEY") or "").split(",") if k.strip()]
    if gk:
        i = _rot[0] % len(gk); gk = gk[i:] + gk[:i]; _rot[0] += 1
    keys = [("https://api.groq.com/openai/v1/chat/completions", k, m) for k in gk for m in GROQ_MODELS]
    keys.append(("https://openrouter.ai/api/v1/chat/completions", os.environ.get("OPENROUTER_API_KEY"), "meta-llama/llama-3.3-70b-instruct:free"))
    for rnd in range(3):
        waits = []
        for url, key, model in keys:
            if not key or (key[-6:], model) in _dead: continue
            try:
                payload = {"model": model, "temperature": 0.2, "response_format": {"type": "json_object"}, "max_tokens": max_out,
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
                if model.startswith("openai/gpt-oss"): payload["reasoning_effort"] = "low"   # far fewer hidden reasoning tokens per guide
                r = requests.post(url, headers={"Authorization": f"Bearer {key}"}, timeout=120, json=payload)
                if r.status_code == 200:
                    _diag["ok"].add(f"{key[-4:]}|{model}")
                    try: return _parse_json(r.json()["choices"][0]["message"]["content"])
                    except Exception as e: log("llm bad json", model, e); continue
                body = r.text[:400].replace("\n", " ")
                if r.status_code == 429:
                    if "per day" in body or "TPD" in body:
                        _dead.add((key[-6:], model)); _diag["daily"][f"{key[-4:]}|{model}"] = _nums(body)
                        log(f"llm key ...{key[-4:]} {model} DAILY limit {_nums(body)} -> skipped for this run")
                    else:
                        waits.append(_retry_secs(body) or 30); _diag["minute"][f"{key[-4:]}|{model}"] = _nums(body)
                        log(f"llm key ...{key[-4:]} {model} per-minute limit {_nums(body)} -> will wait")
                    continue
                if r.status_code == 413:
                    _diag["toolarge"][f"{key[-4:]}|{model}"] = _nums(body)
                    log(f"llm key ...{key[-4:]} {model} REQUEST TOO LARGE {_nums(body)}")
                    continue
                if r.status_code in (403, 404):
                    _dead.add((key[-6:], model)); _diag["noaccess"][f"{key[-4:]}|{model}"] = f"{r.status_code} {body[:90]}"
                elif r.status_code == 400:
                    _diag["noaccess"].setdefault(f"{key[-4:]}|{model}", f"400 (skipped this call) {body[:90]}")
                log("llm", model, r.status_code, body[:160])
            except Exception as e: log("llm err", model, e)
        if not any(k[1] and (k[1][-6:], k[2]) not in _dead for k in keys):
            raise LLMDown(f"no usable LLM combo left: {len(_diag['daily'])} DAILY-limit + {len(_diag['noaccess'])} no-access(404/403) of {len(gk) * len(GROQ_MODELS)} key x model - stopping the run")
        if not waits or rnd == 2: break
        w = min(waits)
        if w > 90: log(f"rate limit resets in {w:.0f}s - too long, giving up on this topic"); break
        log(f"rate-limited, waiting {w + 1:.0f}s then trying again"); time.sleep(w + 1)
    raise SystemExit("no LLM key worked (set GROQ_API_KEY or OPENROUTER_API_KEY; see log for per-model errors)")


SYSTEM = ("You write buying guides from supplied source excerpts ONLY. Never use outside knowledge. Never invent specs, prices, scores or quotes. "
          "If a detail is not in the excerpts, omit it. Reply with one JSON object.")


def build(topic, pages, texts, deals):
    # deal lines: capped so they cannot eat the budget
    deal_block = "\n".join(f"- ({d}) {t}" for d, t in deals[:DEAL_LINES_MAX])[:DEAL_CHARS_MAX] or "(none)"
    template = f"""Topic: {topic['title']}
Pick {ASK_MIN}-{MAX_PRODUCTS} products that the sources below recommend, best first. Prefer products recommended by MORE THAN ONE source.
Return JSON: {{"summary": "one sentence", "method": "how picks were made, naming the sites used", "assumptions": "price caveats",
"products": [{{"name": "full product name", "short": "short name", "badge": "e.g. Best Overall", "why": ["2 short paragraphs, facts from sources"],
"specs": ["up to 6 specs, exactly as stated"], "pros": ["3-4"], "cons": ["2-4"], "best_for": "who", "skip_if": "when to skip",
"price": "only if a source states a price, format '$599 (was $699) at Store' or '$599 at Store', else null",
"deal_note": "ONLY a short sentence taken from the deal lines or sources about a discount / limited-time offer, else null",
"sources": [indices of the sources that mention this product]}}]}}

SOURCES:
{{SRC}}

DEAL LINES (reddit/roundups, may be empty):
{deal_block}"""
    live = [(i, dom, txt) for i, ((dom, _), txt) in enumerate(zip(pages, texts)) if txt]
    # chars left for source text = (input token budget) * chars/token - system - template - per-source label overhead
    input_chars = int((TOKEN_BUDGET - MAX_OUT_TOKENS) * CHARS_PER_TOKEN)
    room = input_chars - len(SYSTEM) - len(template) - 40 * len(live)
    per_src = max(600, room // max(1, len(live)))
    src_block = "\n\n".join(f"[{i}] ({dom}) {txt[:per_src]}" for i, dom, txt in live)
    prompt = template.replace("{SRC}", src_block)
    log(f"llm request: {len(live)} sources x {per_src} chars, ~{est_tokens(SYSTEM, prompt)} in + {MAX_OUT_TOKENS} out tokens (budget {TOKEN_BUDGET})")
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
    def _parent_ready(t):      # a tab-topic can only be added to a parent guide that has real picks (not a 'coming soon' draft)
        if not t.get("parent"): return True
        pf = ROOT / "src" / f"{t['parent']}.json"
        try: d = json.loads(pf.read_text(encoding="utf-8"))
        except Exception: return False
        return not d.get("draft") and bool(d.get("products"))
    c = [t for t in plan if (t["slug"] == slug if slug else t.get("status") == "todo" and t["slug"] not in skip and _parent_ready(t))]
    if not c: raise SystemExit("no matching/todo topic")
    c.sort(key=lambda t: (1 if t.get("last_try") else 0, t.get("priority", 999), not t.get("high_ticket", False)))
    return plan, c[0]


def _nm(s): return re.sub(r"[^a-z0-9]+", "", str(s).lower())

MIN_TAB_PRODUCTS = 2   # a tab (variant view of a big guide) is worth showing from 2 picks

def merge_into_parent(plan, topic, products, today, dry):
    """One product = one big guide. A topic with {parent, tab, tab_label?, tab_max?} (e.g. 'wireless earbuds under 100') adds its picks
    to the PARENT guide as a tab instead of becoming a second page: same product picked again just gets the tab tag, new ones are appended."""
    pf = ROOT / "src" / f"{topic['parent']}.json"
    if not pf.exists(): raise SystemExit(f"parent guide {topic['parent']} missing; topic left as todo")
    if len(products) < MIN_TAB_PRODUCTS:
        log(f"only {len(products)} well-sourced products for tab {topic['tab']} -> staying todo")
        for t in plan:
            if t["slug"] == topic["slug"]: t["last_try"] = today
        if not dry: (ROOT / "plan/topics.json").write_text(json.dumps(plan, indent=1))
        return
    par = json.loads(pf.read_text(encoding="utf-8")); tab = topic["tab"]
    if par.get("draft") or not par.get("products"): raise SystemExit(f"parent guide {topic['parent']} not built yet; topic left as todo")
    have = {_nm(p.get("name")): p for p in par.get("products", [])}; new = 0
    for p in products:
        q = have.get(_nm(p.get("name")))
        if q is not None:
            if tab not in q.setdefault("tabs", []): q["tabs"].append(tab)
        else:
            p = dict(p, tabs=[tab]); par.setdefault("products", []).append(p); have[_nm(p.get("name"))] = p; new += 1
    tabs = par.setdefault("tabs", [])
    if not any(t["key"] == tab for t in tabs):
        t = {"key": tab, "label": topic.get("tab_label") or tab.replace("-", " ").title()}
        if topic.get("tab_max"): t["max"] = topic["tab_max"]
        tabs.append(t)
    par["date"] = today
    if dry: print(json.dumps(par, indent=1, ensure_ascii=False)); return
    pf.write_text(json.dumps(par, indent=1, ensure_ascii=False), encoding="utf-8")
    for t in plan:
        if t["slug"] == topic["slug"]: t["status"] = "done"
    (ROOT / "plan/topics.json").write_text(json.dumps(plan, indent=1))
    subprocess.run([sys.executable, str(ROOT / "tools/render_guide.py"), "--all", "https://zoepicks.github.io"], check=True)
    log(f"done: {topic['slug']} -> tab '{tab}' of {topic['parent']} (+{new} new, {len(products) - new} already there)")


# ---------- backup sources (a blocked site, e.g. Wirecutter 403, must not leave a guide with 2 pages and 0 well-sourced picks) ----------
TARGET_PAGES = 4          # try to have this many readable source pages before asking the LLM
BACKUP_DOMAINS = ["cnet.com", "pcmag.com", "tomsguide.com", "techradar.com", "theverge.com", "wired.com", "engadget.com", "rtings.com",
                  "consumerreports.org", "goodhousekeeping.com", "bestproducts.com", "thespruceeats.com", "gq.com", "esquire.com", "allure.com"]


# Shops / social / reference sites are never review sources, whatever the search engine ranks first.
NOT_REVIEW_HOSTS = ("amazon.", "walmart.", "target.", "ebay.", "bestbuy.", "homedepot.", "lowes.", "etsy.", "aliexpress.", "wayfair.", "costco.",
                    "pinterest.", "facebook.", "instagram.", "tiktok.", "youtube.", "youtu.be", "reddit.", "quora.", "wikipedia.", "twitter.", "x.com",
                    "linkedin.", "zoepicks.", "temu.", "shein.")


def _host(url):
    h = urllib.parse.urlparse(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def _reviewish(url):
    """A real article page on a non-shop site (not a bare homepage)."""
    if not url.startswith("http"): return False
    h = _host(url)
    return bool(h) and not any(b in h for b in NOT_REVIEW_HOSTS) and len(urllib.parse.urlparse(url).path.strip("/")) > 3


def discover_open(topic, exclude=()):
    """Find the BEST review sites for THIS product on the open web (not a fixed list): Tavily (relevance ranked) -> DuckDuckGo -> real browser.
    Returns [(host, url)], one URL per host, skipping shops/social and the hosts in `exclude`."""
    q = _kw(topic) + " best review tested"
    urls = []
    if os.environ.get("TAVILY_API_KEY"):
        try:
            r = requests.post("https://api.tavily.com/search", json={"api_key": os.environ["TAVILY_API_KEY"], "query": q, "max_results": 15,
                              "search_depth": "basic", "exclude_domains": [h.strip(".") for h in NOT_REVIEW_HOSTS if "." in h.strip(".")]}, timeout=40)
            r.raise_for_status()
            urls = [x["url"] for x in r.json().get("results", [])]
            log("open web (tavily) ->", len(urls), "results")
        except Exception as e:
            log("open web tavily fail", e)
    if not urls:
        try:
            r = requests.get("https://html.duckduckgo.com/html/", params={"q": q}, headers=UA, timeout=30)
            urls = [_unwrap(a.get("href", "")) for a in BeautifulSoup(r.text, "lxml").select("a.result__a")]
            log("open web (ddg) ->", len(urls), "results")
        except Exception as e:
            log("open web ddg fail", e)
    if not urls:
        for eng, sel in (("https://www.bing.com/search?q=" + urllib.parse.quote_plus(q), "li.b_algo h2 a"),):
            html = _browser_html(eng, wait_ms=1500)
            urls = [_unwrap(a.get("href", "")) for a in BeautifulSoup(html, "lxml").select(sel)] if html else []
            log("open web (browser) ->", len(urls), "results")
    seen, out = {_host("https://" + e) if "/" not in e else _host(e) for e in exclude}, []
    for u in urls:
        if _reviewish(u) and _host(u) not in seen:
            seen.add(_host(u)); out.append((_host(u), u))
    return out


def top_up_sources(topic, domains, pages, texts, want=TARGET_PAGES, max_new=3):
    """Add readable pages until `want` pages have text: FIRST the best review sites for this very product (open-web search),
    then the generic backup list. Returns how many readable pages were added."""
    have = lambda: sum(bool(t) for t in texts)
    if have() >= want:
        return 0
    tried = {d for d, _ in pages}
    cands = []
    try:
        cands += discover_open(topic, tried)
    except Exception as e:
        log("open web search fail", e)
    backup = [d for d in BACKUP_DOMAINS if d.split("/")[0] not in tried and d not in domains]
    if backup and len(cands) < max_new + 2:
        try:
            cands += discover(topic, backup)
        except Exception as e:
            log("backup sources fail", e)
    added = 0
    for dom, url in cands:
        if have() >= want or added >= max_new:
            break
        if dom in tried:
            continue
        tried.add(dom)
        t = fetch_text(url)
        pages.append((dom, url)); texts.append(t)
        if t:
            added += 1
    log(f"extra sources: +{added} readable page(s) -> {have()} total ({', '.join(d for d, _ in pages[-max(added, 1):])})")
    return added


def run_topic(plan, topic, dry):
    log("topic:", topic["title"])
    domains = SOURCES.get(topic["category"], SOURCES["_default"])
    pages = discover(topic, domains)
    texts = [fetch_text(u) for _, u in pages]
    ok = sum(bool(t) for t in texts)
    log(f"{ok}/{len(pages)} source pages fetched")
    top_up_sources(topic, domains, pages, texts)                       # fewer than TARGET_PAGES readable -> add backup sites
    ok = sum(bool(t) for t in texts)
    if ok < MIN_SOURCES: raise SystemExit("not enough sources fetched; topic left as todo")
    deals = collect_deals(topic)
    g = build(topic, pages, texts, deals)
    products = ground(g, pages, texts)
    if len(products) < MIN_PRODUCTS and top_up_sources(topic, domains, pages, texts, want=ok + 2, max_new=2):   # would stay a draft -> one retry with more sources
        log("retrying with extra sources before giving up")
        g = build(topic, pages, texts, deals)
        products = ground(g, pages, texts)
    today = datetime.date.today().strftime("%b %-d, %Y")
    if topic.get("parent"): return merge_into_parent(plan, topic, products, today, dry)
    _old = ROOT / "src" / f"{topic['slug']}.json"
    _tabs = json.loads(_old.read_text(encoding="utf-8")).get("tabs") if _old.exists() else None   # keep the tab spec of a draft parent
    out = {"slug": topic["slug"], "title": topic["title"], "category": topic["category"], "date": today,
           "summary": g.get("summary", ""), "method": g.get("method", ""), "assumptions": g.get("assumptions", ""), "products": products}
    if _tabs: out["tabs"] = _tabs
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


def _mark_tried(slug):
    """A topic that failed for its own reasons (sources blocked, bad output) goes to the back of the queue instead of blocking it."""
    try:
        plan = json.loads((ROOT / "plan/topics.json").read_text())
        for t in plan:
            if t["slug"] == slug: t["last_try"] = datetime.date.today().strftime("%b %-d, %Y")
        (ROOT / "plan/topics.json").write_text(json.dumps(plan, indent=1))
    except Exception as e: log("could not mark tried:", e)


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
        except LLMDown as e:
            log("STOP:", e); break
        except (Exception, SystemExit) as e:
            fails_in_row += 1
            log(f"FAILED {topic['slug']}: {e}")
            if not dry and "no LLM key worked" not in str(e): _mark_tried(topic["slug"])
            if count == 1: raise
            if fails_in_row >= 3: log("3 failures in a row -> stopping this run"); break
        if slug or i == count - 1: break
        time.sleep(gap)
    keys = [k.strip() for k in (os.environ.get("GROQ_API_KEY") or "").split(",") if k.strip()]
    tails = lambda d: sorted({k.split("|")[0] for k in d}) or 'none'
    log(f"llm summary: {len(keys)} key(s) | daily-limit: {tails(_diag['daily'])} | too-large: {tails(_diag['toolarge'])} | per-minute: {tails(_diag['minute'])}")   # old format, kept for older dashboards
    # new per-key+model report for the dashboard (a key is only 'dead' if EVERY model on it is out - Groq limits are per model)
    log("llm report: " + json.dumps({"keys": len(keys), "models": len(GROQ_MODELS), "daily": _diag["daily"], "toolarge": _diag["toolarge"],
                                      "minute": _diag["minute"], "noaccess": _diag["noaccess"], "ok": sorted(_diag["ok"])}))
    log(f"run finished: {made} guide(s) built, {len(tried)} topic(s) tried")
    if made == 0 and count > 1: sys.exit(1)


if __name__ == "__main__":
    main()
