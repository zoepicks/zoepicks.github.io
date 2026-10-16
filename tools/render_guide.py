"""Render one buying guide (static HTML, so X link previews work) + update guides/index.json.
Usage: python tools/render_guide.py guide.json [BASE_URL]\n       python tools/render_guide.py --all BASE_URL   (re-render all guides; run after changing assets/og.png — og:image ?v= is auto-set from the image hash)
guide.json: {slug,title,category,summary,method,date,assumptions?,products:[{name,badge?,price?,price_asof?,specs[],pros[],cons[],best_for,link,image?}]}
Only fields that are present get rendered — nothing is invented."""
import json, sys, html, re, pathlib, hashlib, datetime
ROOT = pathlib.Path(__file__).resolve().parent.parent
CATS = {"home_kitchen": "Home & Kitchen", "electronics": "Electronics", "computers_phones": "Laptops", "mobiles": "Mobiles", "monitors": "Monitors", "beauty_health": "Beauty & Health",
        "fitness_outdoor": "Fitness & Outdoor", "fashion": "Fashion", "toys_kids": "Toys & Kids", "tools_auto": "Tools & Auto", "pets": "Pets",
        "office_books": "Office & Books", "gaming": "Gaming", "smart_home": "Smart Home & TV", "furniture_decor": "Furniture & Decor",
        "hobby_music": "Hobby & Music", "other": "More"}
EMOJI = {"home_kitchen":"🍳","electronics":"🎧","computers_phones":"💻","mobiles":"📱","monitors":"🖥️","beauty_health":"🧴","fitness_outdoor":"🏃","fashion":"👟","toys_kids":"🧸","tools_auto":"🔧","pets":"🐶","office_books":"📚","gaming":"🎮","smart_home":"🏠","furniture_decor":"🛋️","hobby_music":"🎸","other":"✨"}
def is_live(g):
    """A guide with an 'expires' date (YYYY-MM-DD) stops being live ON that date (UTC). No 'expires' = evergreen = always live."""
    if g.get("retired") or g.get("soon"): return False      # "soon" = coming-soon header row, no article yet
    ex = g.get("expires")
    return not ex or datetime.date.today().isoformat() < ex

def og_version():
    f = ROOT / "assets/og.png"
    return hashlib.md5(f.read_bytes()).hexdigest()[:8] if f.exists() else "0"
e = lambda s: html.escape(str(s), quote=True)
ul = lambda xs: "<ul>" + "".join(f"<li>{e(x)}</li>" for x in xs) + "</ul>"

PALETTE = ["#6c5ce7", "#0284c7", "#c2410c", "#059669", "#c026d3", "#0d9488", "#7c3aed", "#475569"]

def parse_price(txt):
    """'$599 (was $699) at Apple' -> now / was / store / pct / save. Only uses what the text says; pct and save are plain arithmetic."""
    if not txt: return None
    m = re.match(r"^\s*(\$[\d,]+(?:\.\d+)?)\s*(?:\(\s*was\s*(\$[\d,]+(?:\.\d+)?)\s*\))?\s*(?:at\s+(.+?))?\s*$", txt)
    if not m: return {"raw": txt}
    num = lambda x: float(x.replace("$", "").replace(",", "")) if x else None
    now, was = num(m.group(1)), num(m.group(2))
    r = {"now": m.group(1), "was": m.group(2), "store": m.group(3)}
    if now and was and was > now:
        diff = round(was - now, 2)
        r["pct"] = round(diff / was * 100)
        r["save"] = "$" + (f"{diff:,.0f}" if diff == int(diff) else f"{diff:,.2f}")
    return r

def price_block(p):
    pr = parse_price(p.get("price"))
    if pr and "now" in pr:
        h = f'<div class="pl"><span class="now">{e(pr["now"])}</span>'
        if pr.get("was"): h += f'<s class="was">{e(pr["was"])}</s>'
        if pr.get("pct"): h += f'<span class="off">-{pr["pct"]}% OFF</span>'
        bits = []
        if pr.get("save"): bits.append(f'<b class="save">Save {e(pr["save"])}</b> vs list price')
        if pr.get("store"): bits.append(f'at {e(pr["store"])}')
        if p.get("price_asof"): bits.append(f'as of {e(p["price_asof"])}')
        bits.append("may have changed")
        return h + '</div><div class="sub">' + " · ".join(bits) + "</div>"
    if p.get("price"):
        return f'<div class="sub">{e(p["price"])}' + (f' · as of {e(p["price_asof"])}' if p.get("price_asof") else "") + " — may have changed</div>"
    return '<div class="sub">No deal price listed yet. Check the current price on Amazon.</div>'

AFF_TAG = "iamcartigo-20"

def buy_link(p):
    """Direct product page (add to cart / buy now) when the bot has stored the product's ASIN, else the saved search link."""
    a = str(p.get("asin") or p.get("image_asin") or "").strip().upper()
    return f"https://www.amazon.com/dp/{a}?tag={AFF_TAG}" if re.match(r"^[A-Z0-9]{10}$", a) else p.get("link", "")

def _rating(p):
    """(rating float, count int) only when the bot stored a sane Amazon rating + count; else None."""
    try:
        r = float(p.get("amazon_rating")); n = int(p.get("amazon_reviews"))
    except (TypeError, ValueError): return None
    return (r, n) if 1 <= r <= 5 and n > 0 else None

def _k(n):
    return f"{n/1000:.1f}".rstrip("0").rstrip(".") + "K" if n >= 1000 else str(n)

def rating_block(p):
    rt = _rating(p)
    if not rt: return ""
    r, n = rt
    asof = f' title="Amazon rating as of {e(p["amazon_rating_asof"])}"' if p.get("amazon_rating_asof") else ""
    return (f'<div class="rt"{asof}><span class="stars" style="--p:{r/5*100:.0f}%" aria-hidden="true">&#9733;&#9733;&#9733;&#9733;&#9733;</span>'
            f'<b>{r:g}</b><span class="rn">({n:,} ratings on Amazon)</span></div>')

def voices_html(d):
    """Auto-gliding cards: what the expert reviewers said (paraphrased, attributed) - praise AND the catch."""
    v = d.get("voices")
    if not v: return ""
    cards = ""
    for i, x in enumerate(v):
        c = PALETTE[i % len(PALETTE)]
        cards += (f'<article class="vc" style="--c:{c}"><b class="n">{e(x["name"])}</b><small>{e(x["src"])}</small>'
                  f'<p class="l">{e(x["like"])}</p><p class="c">{e(x["catch"])}</p></article>')
    return f'<section class="vr"><h2 class="cmp">What reviewers say <small class="tap">expert reviews, summarized</small></h2><div class="vrail">{cards}</div></section>'

def deal_rows(d):
    """Compact deal list for theme=deal guides: image + -% badge + price/was + one button per row, details collapsed.
    Biggest discount first; products with no deal price go last. Button says 'Buy on Amazon' only when the bot's own recent
    Amazon check shows the deal holds there; otherwise 'Check on Amazon' (the deal price may be from another store)."""
    items = []
    for i, p in enumerate(d["products"]):
        link = buy_link(p)
        if not re.match(r"^https://www\.amazon\.com/.*tag=iamcartigo-20", link):
            raise ValueError(f"product {i+1}: link must be an amazon.com URL with tag=iamcartigo-20")
        items.append((i, p, link, parse_price(p.get("price")) or {}))
    dealpage = d.get("theme") == "deal"
    if dealpage: items.sort(key=lambda t: -(t[3].get("pct") or 0))
    out = ""
    for n, (i, p, link, q) in enumerate(items):
        c = PALETTE[i % len(PALETTE)]
        img = (f'<img src="{e(p["image"])}" alt="{e(p["name"])}" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.style.display=\'none\'">' if p.get("image") else "")
        off = f'<span class="dr-off">-{q["pct"]}%</span>' if q.get("pct") else ""
        if q.get("now"):
            was = f'<s>{e(q["was"])}</s>' if q.get("was") else ""
            meta = " · ".join(x for x in ((f'Save {e(q["save"])}' if q.get("save") else ""), (f'at {e(q["store"])}' if q.get("store") else ""), e(p.get("price_asof", ""))) if x)
            price = f'<div class="dr-pr"><b>{e(q["now"])}</b>{was}</div><div class="dr-sv">{meta}</div>'
        elif q.get("raw"):
            price = f'<div class="dr-pr"><b>{e(q["raw"])}</b></div><div class="dr-sv">{("as of " + e(p["price_asof"]) + " · ") if p.get("price_asof") else ""}may have changed</div>'
        elif dealpage:
            price = '<div class="dr-pr np">No deal price yet</div><div class="dr-sv">Check the current price on Amazon</div>'
        else:
            price = f'<div class="dr-bf">{e(p["best_for"])}</div>' if p.get("best_for") else ""
        hold = amazon_holds(p) or (not dealpage and not q.get("store"))
        btn = f'<a class="dr-btn{" ok" if hold else ""}" href="{e(link)}" rel="sponsored nofollow noopener" target="_blank">{"Buy on Amazon" if hold else "Check on Amazon"} &#8599;</a>'
        det = ""
        if p.get("best_for") and (dealpage or q.get("now") or q.get("raw")): det += f'<p><b>Best for:</b> {e(p["best_for"])}</p>'
        if p.get("why"):
            ws = p["why"] if isinstance(p["why"], list) else [p["why"]]
            det += "".join(f"<p>{e(x)}</p>" for x in ws[:1])
        if p.get("pros") or p.get("cons"): det += f'<div class="pc"><div class="pro"><b>Pros</b>{ul(p.get("pros", []))}</div><div class="con"><b>Cons</b>{ul(p.get("cons", []))}</div></div>'
        if p.get("skip_if"): det += f'<p class="skip"><b>Skip it if:</b> {e(p["skip_if"])}</p>'
        if p.get("deal_note"): det += f'<p class="deal"><b>Deal tip:</b> {e(p["deal_note"])}</p>'
        tag = (f'<small class="dr-tag">{e(p["badge"])}</small>' if p.get("badge") else "") if dealpage else (f'<small class="dr-tag">#{i+1}' + (f' · {e(p["badge"])}' if p.get("badge") else "") + '</small>')
        out += (f'<article class="dr" id="p{i+1}" style="--c:{c}"><div class="dr-t"><div class="dr-im"><span class="dr-ph">{EMOJI.get(d["category"], "🛒")}</span>{img}{off}</div>'
                f'<div class="dr-b">{tag}<h3>{e(p["name"])}</h3>{rating_block(p)}{price}{btn}</div></div>'
                + (f'<details class="dr-d"><summary>Why it is worth it</summary>{det}</details>' if det else "") + "</article>")
    return f'<div class="dr-list">{out}</div>'

def deal_strip(d):
    pcts = [(parse_price(p.get("price")) or {}).get("pct", 0) for p in d["products"]]
    asof = next((p["price_asof"] for p in d["products"] if p.get("price_asof")), d.get("date", ""))
    top = f"Up to {max(pcts)}% off" if any(pcts) else "Deals"
    return f'<p class="dstrip"><b>&#128293; {top}</b> · prices as of {e(asof)} · may change, check before buying</p>'

def card(i, p):
    link = buy_link(p)
    if not re.match(r"^https://www\.amazon\.com/.*tag=iamcartigo-20", link):
        raise ValueError(f"product {i+1}: link must be an amazon.com URL with tag=iamcartigo-20")
    c = PALETTE[i % len(PALETTE)]
    h = f'<article class="card" id="p{i+1}" style="--c:{c}">'
    if p.get("badge"): h += f'<span class="rank">#{i+1} · {e(p["badge"])}</span>'
    h += '<div class="top">'
    if p.get("image"): h += f'<img class="thumb" src="{e(p["image"])}" alt="{e(p["name"])}" loading="lazy" decoding="async" width="108" height="108" referrerpolicy="no-referrer" onerror="this.style.display=\'none\'">'
    h += f'<div class="meta"><h3>{e(p["name"])}</h3>{rating_block(p)}{price_block(p)}'
    # scraped Amazon price is internal only (bot uses it to verify deals); never shown publicly
    h += "</div></div>"
    if p.get("best_for"): h += f'<div class="best"><b>Best for:</b> {e(p["best_for"])}</div>'
    h += f'<a class="btn" href="{e(link)}" rel="sponsored nofollow noopener" target="_blank">Buy here &rarr;</a>'
    d = ""
    if p.get("why"):
        paras = p["why"] if isinstance(p["why"], list) else [p["why"]]
        d += '<div class="why"><b>Why we picked it</b>' + "".join(f"<p>{e(x)}</p>" for x in paras) + "</div>"
    if p.get("specs"): d += '<b class="lbl">Key specs</b><ul class="specs">' + "".join(f"<li>{e(s)}</li>" for s in p["specs"]) + "</ul>"
    if p.get("pros") or p.get("cons"): d += f'<div class="pc"><div class="pro"><b>Pros</b>{ul(p.get("pros", []))}</div><div class="con"><b>Cons</b>{ul(p.get("cons", []))}</div></div>'
    if p.get("skip_if"): d += f'<div class="skip"><b>Skip it if:</b> {e(p["skip_if"])}</div>'
    if p.get("deal_note"): d += f'<div class="deal"><b>Deal tip:</b> {e(p["deal_note"])}</div>'
    if d: h += f'<details class="more-d"><summary>Full review, specs, pros &amp; cons</summary>{d}</details>'
    return h + "</article>"

def intro_html(d):
    """Short intro. 'intro' = while the offer is live, optional 'intro_ended' = after it ends (so no stale 'grab it now' text).
    Both are rendered; the inline script in the template swaps them by the viewer's clock, the daily rebuild swaps them in the HTML."""
    pcts = [pr["pct"] for pr in (parse_price(p.get("price")) for p in d.get("products", [])) if pr and pr.get("pct")]
    hook = f"Up to {max(pcts)}% off" if pcts else "Big savings"      # {deal_hook}: real number from the guide's own prices
    def sec(xs, cls, hidden):
        xs = xs if isinstance(xs, list) else [xs]
        return f'<section class="{cls}"{" hidden" if hidden else ""}>' + "".join(f"<p>{e(x.replace('{deal_hook}', hook))}</p>" for x in xs) + "</section>"
    live, end = d.get("intro"), d.get("intro_ended")
    if not live and not end: return ""
    if not end: return sec(live, "intro intro-live", False)
    on = is_live(d)
    return (sec(live, "intro intro-live", not on) if live else "") + sec(end, "intro intro-end", on)

def jump_html(d):
    """Sticky-ish scrollable chips #1..#N under the header so a buyer can jump straight to a pick."""
    if len(d["products"]) < 3: return ""
    chips = "".join(f'<a href="#p{i+1}"><b>{i+1}</b> {e(p.get("short") or p["name"])}</a>' for i, p in enumerate(d["products"]))
    return f'<div class="jump" role="navigation" aria-label="Jump to a pick">{chips}</div>'

def compare_html(d):
    if not d.get("compare"): return ""
    rows = ""
    for i, p in enumerate(d["products"]):
        pr = parse_price(p.get("price"))
        if pr and "now" in pr:
            price = f'<b>{e(pr["now"])}</b>' + (f'<s>{e(pr["was"])}</s>' if pr.get("was") else "")
            off = f'<span class="off">-{pr["pct"]}%</span>' if pr.get("pct") else "-"
        else:
            price, off = "See price", "-"
        rt = _rating(p)
        rtx = f'<span class="rt sm"><span class="st1">&#9733;</span>{rt[0]:g} ({_k(rt[1])})</span>' if rt else ""
        rows += f'<tr><td><a href="#p{i+1}">{e(p.get("short") or p["name"])}</a>{rtx}<small class="pk">{e(p.get("badge", ""))}</small></td><td class="pr">{price}</td><td>{off}</td></tr>'
    return f'<h2 class="cmp">Quick comparison <small class="tap">tap a name</small></h2><div class="tbl"><table><thead><tr><th>Laptop</th><th>Deal</th><th>Off</th></tr></thead><tbody>{rows}</tbody></table></div>'

def countdown_html(d):
    """Black Friday style banner. 3 states: counting down -> deals live -> ended. Static HTML is the fallback; a small inline
    script in the template flips the state by the viewer's clock, so it stays correct even between daily rebuilds."""
    t, ex = d.get("countdown_to"), d.get("expires")
    if not t and not ex: return ""
    if not is_live(d):
        return f'<div class="cd ended"><b>This offer has ended</b><span>Prices below were from {e(d.get("date", ""))}. Check the current price on Amazon.</span></div>'
    return (f'<div class="cd" data-to="{e(t or "")}" data-exp="{e(ex or "")}" data-before="{e(d.get("countdown_label", "Deal day in"))}" data-live="{e(d.get("live_label", "Deals are live now"))}">'
            f'<b>{e(d.get("countdown_label", "Deal day in"))}</b><span>{e(t or ex)}</span></div>')

def tips_html(d):
    t = d.get("tips")
    if not t: return ""
    return f'<h2>{e(t.get("title", "Buying tips"))}</h2><ol class="tips">' + "".join(f"<li><b>{e(a)}</b> {e(b)}</li>" for a, b in t["items"]) + "</ol>"

def faq_html(d):
    q = d.get("faq")
    if not q: return ""
    return "<h2>FAQ</h2>" + "".join(f'<details class="faq"><summary>{e(a)}</summary><p>{e(b)}</p></details>' for a, b in q)

def more_html(d):
    """Horizontal 'more guides' carousel (static HTML). Same category first, then newest."""
    try: L = json.loads((ROOT / "guides/index.json").read_text())
    except Exception: L = []
    L = [g for g in L if g["slug"] != d["slug"] and is_live(g)]
    L.sort(key=lambda g: g["category"] != d["category"])   # stable: keeps newest-first inside each group
    if not L: return ""
    cards = "".join(f'<a href="../{e(g["slug"])}/"><small>{e(CATS.get(g["category"], "Guide"))} · {e(g.get("date", ""))}</small><b>{e(g["title"])}</b><span>See top 5 picks →</span></a>' for g in L[:10])
    return f'<section class="more"><h2>More guides</h2><div class="rail">{cards}</div></section>'

def retired_html(d, base=""):
    """Replaces a retired offer page: redirects to the category list (so old X links never 404), noindex, no stale prices."""
    cat = d.get("category", "other"); url = f"../../category.html?c={e(cat)}"; name = e(CATS.get(cat, "Guides"))
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{e(d["title"])} (ended) — Zoe Picks</title><meta name="robots" content="noindex,follow"><meta http-equiv="refresh" content="0;url={url}">'
            f'<link rel="canonical" href="{(base + "/category.html?c=" + e(cat)) if base else url}"><link rel="stylesheet" href="../../assets/style.css"></head><body>'
            f'<main class="wrap"><div class="g-head"><h1>This offer has ended</h1><p class="sum">The deals in "{e(d["title"])}" are over. '
            f'<a href="{url}">See current {name} guides →</a></p></div></main><script>location.replace("{url}")</script></body></html>')

def render(d, base=""):
    t = (ROOT / "templates/guide.html").read_text()
    method = d["method"] + (" Assumptions: " + d["assumptions"] if d.get("assumptions") else "")
    rep = {"OGV": og_version(), "TITLE": e(d["title"]), "SUMMARY": e(d["summary"]), "CATKEY": e(d["category"]), "CATNAME": e(CATS.get(d["category"], "Guide")),
           "DATE": e(d["date"]), "METHOD": e(method), "MORE": more_html(d), "CARDS": "\n".join(card(i, p) for i, p in enumerate(d["products"])), "INTRO": intro_html(d), "COMPARE": compare_html(d), "JUMP": jump_html(d), "VOICES": voices_html(d), "COUNTDOWN": countdown_html(d), "HEADCLASS": "deal-head" if d.get("theme") == "deal" else "", "TIPS": tips_html(d), "FAQ": faq_html(d),
           "OGURL": (f'<meta property="og:url" content="{e(base)}/g/{e(d["slug"])}/"><link rel="canonical" href="{e(base)}/g/{e(d["slug"])}/">') if base else ""}
    # compact rows for every guide (mobile first); deal guides also get the deals strip and biggest-discount-first order
    rep.update({"CARDS": deal_rows(d), "JUMP": "", "COMPARE": "", "VOICES": "", "MORE": "", "FAQ": faq_html(d) + more_html(d)})
    if d.get("theme") == "deal": rep["INTRO"] = deal_strip(d) if is_live(d) else intro_html(d)
    if d.get("draft"): rep["OGURL"] += '<meta name="robots" content="noindex">'   # unfinished guide: reachable by link only, hidden from search
    for k, v in rep.items(): t = t.replace("{{" + k + "}}", v)
    return t

def site_files(base):
    """sitemap.xml + static guide links on the homepage (so crawlers / no-JS see them)."""
    L = [g for g in json.loads((ROOT / "guides/index.json").read_text()) if is_live(g)]
    urls = [f"{base}/"] + [f"{base}/g/{g['slug']}/" for g in L]
    (ROOT / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"<url><loc>{u}</loc></url>\n" for u in urls) + "</urlset>\n")
    (ROOT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n")
    ix = ROOT / "index.html"; h = ix.read_text()
    rows = "".join(f'<a class="item" href="g/{g["slug"]}/"><span><b>{e(g["title"])}</b><br><small>{e(CATS.get(g["category"], "Guide"))}</small></span><small>{e(g.get("date", ""))}</small></a>' for g in L[:8])
    h = re.sub(r"<!--GUIDES-->.*?<!--/GUIDES-->", lambda m: "<!--GUIDES-->" + rows + "<!--/GUIDES-->", h, flags=re.S)
    cats = "".join(f'<a class="cat" href="category.html?c={k}"><i>{EMOJI[k]}</i><b>{e(v)}</b></a>' for k, v in CATS.items())
    h = re.sub(r"<!--CATS-->.*?<!--/CATS-->", lambda m: "<!--CATS-->" + cats + "<!--/CATS-->", h, flags=re.S)
    h = re.sub(r"assets/og\.png\?v=[0-9a-zA-Z]+", "assets/og.png?v=" + og_version(), h)
    ix.write_text(h)

def sync_index_expiry():
    """Copy each guide's 'expires' from src/*.json into guides/index.json (the bot and the site read it there)."""
    ix = ROOT / "guides/index.json"; L = json.loads(ix.read_text()); ex = {}; gone = set(); al = {}
    for f in (ROOT / "src").glob("*.json"):
        d = json.load(open(f)); ex[d["slug"]] = d.get("expires"); al[d["slug"]] = d.get("also")
        if d.get("retired"): gone.add(d["slug"])
    ch = False
    if gone:
        n = len(L); L = [g for g in L if g["slug"] not in gone]; ch = len(L) != n
    for g in L:
        if g["slug"] in al and not g.get("soon"):      # "also": extra categories this guide is listed under (one page, shown in both)
            if al[g["slug"]] and g.get("also") != al[g["slug"]]: g["also"] = al[g["slug"]]; ch = True
            elif not al[g["slug"]] and "also" in g: del g["also"]; ch = True
        if g["slug"] in ex:
            if ex[g["slug"]] and g.get("expires") != ex[g["slug"]]: g["expires"] = ex[g["slug"]]; ch = True
            elif not ex[g["slug"]] and "expires" in g: del g["expires"]; ch = True
    if ch: ix.write_text(json.dumps(L, indent=1))


def _money(x):
    m = re.search(r"\d[\d,]*\.?\d*", str(x or ""))
    try: return float(m.group(0).replace(",", "")) if m else None
    except ValueError: return None

def amazon_holds(p, max_age=3, tol=0.03):
    """True when the bot's own Amazon check is recent (<= max_age days) and Amazon's price is at or under the guide's deal price (+3%).
    Same rule as the bot's offer lifecycle. The scraped Amazon price itself is never shown in the ticker."""
    deal, amz = _money(str(p.get("price") or "").split("(")[0]), _money(p.get("amazon_price"))
    if not deal or not amz or not p.get("amazon_price_asof"): return False
    try: age = (datetime.date.today() - datetime.datetime.strptime(p["amazon_price_asof"], "%b %d, %Y").date()).days
    except ValueError: return False
    return age <= max_age and amz <= deal * (1 + tol)

def sync_meta():
    """Add card data to guides/index.json for the category page: n (picks), img (first product image), top (#1 pick),
    off (biggest 'was' discount % found in the guide's own price text). Only uses what src/*.json already says."""
    ix = ROOT / "guides/index.json"; L = json.loads(ix.read_text()); meta = {}
    for f in (ROOT / "src").glob("*.json"):
        d = json.load(open(f)); P = d.get("products") or []
        m = {"n": len(P)}
        img = next((x["image"] for x in P if x.get("image")), None)
        if img: m["img"] = img
        if P: m["top"] = P[0].get("short") or P[0].get("name")
        off = max([(parse_price(x.get("price")) or {}).get("pct", 0) for x in P] or [0])
        if off: m["off"] = off
        meta[d["slug"]] = m
    deals = []   # verified "was/now" prices from live deal guides only (store + as-of date come from the guide's own price text)
    for f in (ROOT / "src").glob("*.json"):
        d = json.load(open(f))
        if not d.get("expires") or d.get("retired") or d.get("draft") or not is_live(d): continue
        for x in d.get("products") or []:
            q = parse_price(x.get("price"))
            if not q or not q.get("pct"): continue
            deals.append({"cat": d["category"], **({"also": d["also"]} if d.get("also") else {}), "guide": d["slug"], "name": x.get("short") or x.get("name"),
                          "ver": amazon_holds(x), **({"link": x["link"]} if x.get("link") else {}),
                          "now": q["now"], "was": q["was"], "pct": q["pct"], "save": q["save"], **({"store": q["store"]} if q.get("store") else {}),
                          **({"img": x["image"]} if x.get("image") else {}), **({"asof": x["price_asof"]} if x.get("price_asof") else {})})
    deals.sort(key=lambda z: -z["pct"])
    dj = ROOT / "guides/deals.json"; dt = json.dumps(deals, indent=1)
    if not dj.exists() or dj.read_text() != dt: dj.write_text(dt)
    ch = False
    for g in L:
        if g.get("soon") or g["slug"] not in meta: continue
        m = meta[g["slug"]]
        for k in ("n", "img", "top", "off"):
            if m.get(k) and g.get(k) != m[k]: g[k] = m[k]; ch = True
            elif not m.get(k) and k in g: del g[k]; ch = True
    if ch: ix.write_text(json.dumps(L, indent=1))

def sync_soon():
    """Keep a 'Coming soon' header row in guides/index.json for every draft guide (src/*.json with "draft": true) so the
    category page lists all planned topics. Rows follow plan priority; a row disappears once the draft becomes a real guide."""
    ix = ROOT / "guides/index.json"; L = json.loads(ix.read_text())
    try: pr = {t["slug"]: t.get("priority", 999) for t in json.loads((ROOT / "plan/topics.json").read_text())}
    except Exception: pr = {}
    live = {g["slug"] for g in L if not g.get("soon")}
    soon = []
    for f in (ROOT / "src").glob("*.json"):
        d = json.load(open(f))
        if d.get("draft") and not d.get("retired") and d["slug"] not in live:
            soon.append({"slug": d["slug"], "title": d["title"], "category": d["category"], **({"also": d["also"]} if d.get("also") else {}), "soon": True})
    soon.sort(key=lambda g: (pr.get(g["slug"], 999), g["slug"]))
    new = [g for g in L if not g.get("soon")] + soon
    if new != L: ix.write_text(json.dumps(new, indent=1))

def rebuild_all(base):
    """Re-render every guide from src/*.json (keeps guides/index.json order). Run after changing assets/og.png."""
    sync_index_expiry()
    sync_soon()
    sync_meta()
    for f in sorted((ROOT / "src").glob("*.json")):
        d = json.load(open(f)); out = ROOT / "g" / d["slug"]; out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text(retired_html(d, base) if d.get("retired") else render(d, base)); print("ok", out / "index.html")
    site_files(base)

if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--all":
        rebuild_all(sys.argv[2].rstrip("/")); raise SystemExit
    d = json.load(open(sys.argv[1])); base = sys.argv[2].rstrip("/") if len(sys.argv) > 2 else ""
    if not re.match(r"^[a-z0-9-]{3,80}$", d["slug"]): raise SystemExit("bad slug")
    out = ROOT / "g" / d["slug"]; out.mkdir(parents=True, exist_ok=True); (out / "index.html").write_text(render(d, base))
    ix = ROOT / "guides/index.json"; L = [x for x in json.loads(ix.read_text()) if x["slug"] != d["slug"]]
    if d.get("draft"): print("draft: not added to the live index (shows as Coming soon)"); ix.write_text(json.dumps(L, indent=1)); print("ok", out / "index.html"); (rebuild_all(base) if base else None); raise SystemExit
    L.insert(0, {"slug": d["slug"], "title": d["title"], "category": d["category"], "date": d["date"], **({"also": d["also"]} if d.get("also") else {}), **({"expires": d["expires"]} if d.get("expires") else {})}); ix.write_text(json.dumps(L[:500], indent=1))
    print("ok", out / "index.html")
    if base: rebuild_all(base)
