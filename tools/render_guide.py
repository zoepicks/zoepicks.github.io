"""Render one buying guide (static HTML, so X link previews work) + update guides/index.json.
Usage: python tools/render_guide.py guide.json [BASE_URL]\n       python tools/render_guide.py --all BASE_URL   (re-render all guides; run after changing assets/og.png — og:image ?v= is auto-set from the image hash)
guide.json: {slug,title,category,summary,method,date,assumptions?,products:[{name,badge?,price?,price_asof?,specs[],pros[],cons[],best_for,link,image?}]}
Only fields that are present get rendered — nothing is invented."""
import json, sys, html, re, pathlib, hashlib, datetime
ROOT = pathlib.Path(__file__).resolve().parent.parent
CATS = {"home_kitchen": "Home & Kitchen", "electronics": "Electronics", "laptops": "Laptops", "mobiles": "Mobiles", "monitors": "Monitors", "accessories": "Accessories", "beauty_health": "Beauty & Health",
        "fitness_outdoor": "Fitness & Outdoor", "fashion": "Fashion", "toys_kids": "Toys & Kids", "tools_auto": "Tools & Auto", "pets": "Pets",
        "office_books": "Office & Books", "gaming": "Gaming", "smart_home": "Smart Home & TV", "furniture_decor": "Furniture & Decor",
        "hobby_music": "Hobby & Music", "other": "More"}
EMOJI = {"home_kitchen":"🍳","electronics":"🎧","laptops":"💻","mobiles":"📱","monitors":"🖥️","accessories":"🖱️","beauty_health":"🧴","fitness_outdoor":"🏃","fashion":"👟","toys_kids":"🧸","tools_auto":"🔧","pets":"🐶","office_books":"📚","gaming":"🎮","smart_home":"🏠","furniture_decor":"🛋️","hobby_music":"🎸","other":"✨"}
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

SHOW_AMAZON_PRICE = True      # switch off to go back to hiding the bot's scraped Amazon prices
AMZ_PRICE_MAX_AGE = 1         # days (US-Eastern date, same clock as the bot); the bot re-checks every product daily before its window, an older price is hidden

def amz_price(p):
    """(price string, as-of date) of the bot's own Amazon price check, only when it is well-formed and fresh; else None."""
    if not SHOW_AMAZON_PRICE: return None
    raw, asof = str(p.get("amazon_price") or "").strip(), str(p.get("amazon_price_asof") or "").strip()
    if not re.match(r"^\$\d[\d,]*(\.\d{1,2})?$", raw): return None
    try:
        try:
            from zoneinfo import ZoneInfo
            today = datetime.datetime.now(ZoneInfo("America/New_York")).date()
        except Exception:                                   # no tzdata on the runner: fixed UTC-5 is close enough, never crash the build
            today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-5))).date()
        age = (today - datetime.datetime.strptime(asof, "%b %d, %Y").date()).days
    except ValueError: return None
    return (raw, asof) if 0 <= age <= AMZ_PRICE_MAX_AGE else None

def amz_price_html(p):
    a = amz_price(p)
    return f'<div class="dr-pr"><b>{e(a[0])}</b></div>' if a else ""

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
    a = amz_price(p)
    if a: return f'<div class="pl"><span class="now">{e(a[0])}</span></div>'
    return '<div class="sub">No deal price listed yet. Check the current price on Amazon.</div>'

AFF_TAG = "zoepicks00-20"

def buy_link(p):
    """Direct product page (add to cart / buy now) when the bot has stored the product's ASIN, else the saved search link."""
    a = str(p.get("asin") or p.get("image_asin") or "").strip().upper()
    return f"https://www.amazon.com/dp/{a}?tag={AFF_TAG}" if re.match(r"^[A-Z0-9]{10}$", a) else p.get("link", "")

def is_ready(p):
    """True only when the bot filled BOTH a valid ASIN and an image. Unfilled products must never link to an Amazon search page."""
    a = str(p.get("asin") or p.get("image_asin") or "").strip().upper()
    return bool(re.match(r"^[A-Z0-9]{10}$", a)) and bool(p.get("image"))

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
            f'<b>{r:g}</b><span class="rn">({n:,})</span></div>')

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

def tab_keys(d, p):
    """Tabs a product belongs to: tabs it is tagged with + any tab with a 'max' price the product's own price text fits under."""
    ks = [k for k in (p.get("tabs") or []) if k]
    q = parse_price(p.get("price")) or {}
    try: now = float(re.sub(r"[^\d.]", "", q.get("now") or "")) if q.get("now") else None
    except ValueError: now = None
    for t in d.get("tabs") or []:
        if t["key"] not in ks and t.get("max") and now is not None and now <= float(t["max"]): ks.append(t["key"])
    return ks

TABS_JS = r"""(function(){var b=document.querySelectorAll('.tabs button'),L=document.querySelector('.dr-list');if(!b.length||!L)return;
function go(t){var n=0;b.forEach(function(x){x.classList.toggle('on',x.dataset.t===t)});
L.querySelectorAll('.dr').forEach(function(a){var h=!!t&&(' '+(a.dataset.tabs||'')+' ').indexOf(' '+t+' ')<0;a.hidden=h;
if(!h){var g=a.querySelector('.dr-tag');if(g)g.textContent=g.textContent.replace(/^#\d+/,'#'+(++n))}})}
b.forEach(function(x){x.onclick=function(){go(x.dataset.t);try{history.replaceState(null,'','#'+(x.dataset.t||'all'))}catch(e){}}});
var h=(location.hash||'').slice(1);if(h&&[].some.call(b,function(x){return x.dataset.t===h}))go(h)})();"""

MIN_TAB_PAGE = 3   # a tab with >= 3 picks also gets its own indexable URL: /g/<guide>/<tab>/ (real page, own title/H1/canonical, not a JS filter)

def tab_pages(d):
    """[(tab, products)] for every tab of this guide that is big enough to be its own page. Drafts/retired guides have none."""
    if d.get("draft") or d.get("retired"): return []
    P = visible_products(d["products"]); out = []
    for t in d.get("tabs") or []:
        if not re.match(r"^[a-z0-9-]{2,40}$", str(t.get("key", ""))): continue
        ps = [p for p in P if t["key"] in tab_keys(d, p)]
        if len(ps) >= MIN_TAB_PAGE: out.append((t, ps))
    return out

def tab_title(d, t):
    """Own H1/title of a tab page. A tab may carry an explicit 'title'; else 'Under $N' swaps the price in the parent title, anything else
    becomes 'Best X: Label (year)'."""
    if t.get("title"): return t["title"]
    base = d["title"]; lab = t["label"]
    ym = re.search(r"\s*\((\d{4})\)\s*$", base); yr = f" ({ym.group(1)})" if ym else ""
    if str(t["key"]).startswith("under-"):
        stem = re.split(r"(?i)\s+(?:by\s+budget\b|under\s+\$)", base[:ym.start()] if ym else base)[0].strip()
        return f"{stem} {lab}{yr}"
    m = re.search(r"\s*\((\d{4})\)\s*$", base)
    return f"{base[:m.start()]}: {lab} ({m.group(1)})" if m else f"{base}: {lab}"

def tabs_bar(d, items):
    """One guide, several views: 'Top picks' (all) + one chip per tab that has >= 1 product. Chips only exist when there is something to show."""
    ts = [(t, sum(1 for _, p, *_ in items if t["key"] in tab_keys(d, p))) for t in d.get("tabs") or []]
    ts = [(t, n) for t, n in ts if n]
    if not ts: return ""
    chips = '<button class="on" data-t="">Top picks</button>' + "".join(f'<button data-t="{e(t["key"])}">{e(t["label"])}<i>{n}</i></button>' for t, n in ts)
    pg = [t for t, _ in tab_pages(d)] if d.get("_tabpages", True) else []
    own = ('<nav class="tl" style="font-size:13px;margin:6px 0 10px;opacity:.85">Also as its own page: ' + " · ".join(f'<a href="{e(t["key"])}/">{e(t["label"])}</a>' for t in pg) + "</nav>") if pg else ""
    return f'<div class="tabs" role="tablist">{chips}</div>{own}'

def deal_rows(d):
    """Compact deal list for theme=deal guides: image + -% badge + price/was + one button per row, details collapsed.
    Biggest discount first; products with no deal price go last. Button says 'Buy on Amazon' only when the bot's own recent
    Amazon check shows the deal holds there; otherwise 'Check on Amazon' (the deal price may be from another store)."""
    items = []
    for i, p in enumerate(d["products"]):
        link = buy_link(p)
        if not re.match(r"^https://www\.amazon\.com/.*tag=zoepicks00-20", link):
            raise ValueError(f"product {i+1}: link must be an amazon.com URL with tag=zoepicks00-20")
        items.append((i, p, link, parse_price(p.get("price")) or {}))
    dealpage = d.get("theme") == "deal"
    if dealpage: items.sort(key=lambda t: -(t[3].get("pct") or 0))
    reordered, soon_shown = False, False
    if not dealpage:       # picks whose Amazon link is not filled yet go last (original order kept inside each group); the daily rebuild restores the full ranking once the bot fills them
        rd = [t for t in items if is_ready(t[1])]; nr = [t for t in items if not is_ready(t[1])]
        if rd and nr: items, reordered = rd + nr, True
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
            price = amz_price_html(p) or '<div class="dr-pr np">No deal price yet</div><div class="dr-sv">Check the current price on Amazon</div>'
        else:
            price = amz_price_html(p) + (f'<div class="dr-bf">{e(p["best_for"])}</div>' if p.get("best_for") else "")
        hold = amazon_holds(p) or (not dealpage and not q.get("store"))
        btn = (f'<a class="dr-btn{" ok" if hold else ""}" href="{e(link)}" rel="sponsored nofollow noopener" target="_blank">{"Buy on Amazon" if hold else "Check on Amazon"} &#8599;</a>'
               if is_ready(p) else '<span class="dr-btn off" role="link" aria-disabled="true">Link coming soon</span>')
        det = ""
        if p.get("best_for") and (dealpage or q.get("now") or q.get("raw")): det += f'<p><b>Best for:</b> {e(p["best_for"])}</p>'
        if p.get("why"):
            ws = p["why"] if isinstance(p["why"], list) else [p["why"]]
            det += "".join(f"<p>{e(x)}</p>" for x in ws[:1])
        if p.get("pros") or p.get("cons"): det += f'<div class="pc"><div class="pro"><b>Pros</b>{ul(p.get("pros", []))}</div><div class="con"><b>Cons</b>{ul(p.get("cons", []))}</div></div>'
        if p.get("skip_if"): det += f'<p class="skip"><b>Skip it if:</b> {e(p["skip_if"])}</p>'
        if p.get("deal_note"): det += f'<p class="deal"><b>Deal tip:</b> {e(p["deal_note"])}</p>'
        tag = (f'<small class="dr-tag">{e(p["badge"])}</small>' if p.get("badge") else "") if dealpage else (f'<small class="dr-tag">' + ("" if reordered else f"#{i+1}") + ((("" if reordered else " · ") + e(p["badge"])) if p.get("badge") else "") + '</small>')
        if reordered and not soon_shown and not is_ready(p):
            soon_shown = True; out += '<p class="dr-soon">Amazon links for the picks below are being added</p>'
        out += (f'<article class="dr{" off" if not is_ready(p) else ""}" id="p{i+1}" data-tabs="{e(" ".join(tab_keys(d, p)))}" style="--c:{c}"><div class="dr-t"><div class="dr-im"><span class="dr-ph">{EMOJI.get(d["category"], "🛒")}</span>{img}{off}</div>'
                f'<div class="dr-b">{tag}<h3>{e(p["name"])}</h3>{rating_block(p)}{price}{btn}</div></div>'
                + (f'<p class="dr-desc">{e(p["desc"])}</p>' if p.get("desc") else "")
                + (f'<p class="dr-why"><b>Why buy it:</b> {e(p["benefit"])}</p>' if p.get("benefit") else "")
                + (f'<details class="dr-d"><summary>Pros, cons &amp; who should skip it</summary>{det}</details>' if det else "") + "</article>")
    bar = tabs_bar(d, items)
    return bar + f'<div class="dr-list">{out}</div>' + (f'<script>{TABS_JS}</script>' if bar else "")   # script AFTER the list it filters

def deal_strip(d):
    pcts = [(parse_price(p.get("price")) or {}).get("pct", 0) for p in d["products"]]
    asof = next((p["price_asof"] for p in d["products"] if p.get("price_asof")), d.get("date", ""))
    top = f"Up to {max(pcts)}% off" if any(pcts) else "Deals"
    return f'<p class="dstrip"><b>&#128293; {top}</b> · prices as of {e(asof)} · may change, check before buying</p>'

def card(i, p):
    link = buy_link(p)
    if not re.match(r"^https://www\.amazon\.com/.*tag=zoepicks00-20", link):
        raise ValueError(f"product {i+1}: link must be an amazon.com URL with tag=zoepicks00-20")
    c = PALETTE[i % len(PALETTE)]
    h = f'<article class="card{" off" if not is_ready(p) else ""}" id="p{i+1}" style="--c:{c}">'
    if p.get("badge"): h += f'<span class="rank">#{i+1} · {e(p["badge"])}</span>'
    h += '<div class="top">'
    if p.get("image"): h += f'<img class="thumb" src="{e(p["image"])}" alt="{e(p["name"])}" loading="lazy" decoding="async" width="108" height="108" referrerpolicy="no-referrer" onerror="this.style.display=\'none\'">'
    h += f'<div class="meta"><h3>{e(p["name"])}</h3>{rating_block(p)}{price_block(p)}'
    # scraped Amazon price is internal only (bot uses it to verify deals); never shown publicly
    h += "</div></div>"
    if p.get("best_for"): h += f'<div class="best"><b>Best for:</b> {e(p["best_for"])}</div>'
    h += (f'<a class="btn" href="{e(link)}" rel="sponsored nofollow noopener" target="_blank">Buy on Amazon &rarr;</a>'
          if is_ready(p) else '<span class="btn off" role="link" aria-disabled="true">Link coming soon</span>')
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
            f'<link rel="canonical" href="{(base + "/category.html?c=" + e(cat)) if base else url}"><link rel="icon" type="image/png" href="../../assets/logo-64.png"><link rel="stylesheet" href="../../assets/style.css"></head><body>'
            f'<main class="wrap"><div class="g-head"><h1>This offer has ended</h1><p class="sum">The deals in "{e(d["title"])}" are over. '
            f'<a href="{url}">See current {name} guides →</a></p></div></main><script>location.replace("{url}")</script></body></html>')

def visible_products(P):
    """Auto-hide: a product the bot gave up on (fill_failed, still no ASIN/image) disappears from the public page, as long as at
    least 3 products remain. Ranks renumber by position. When the bot later fills it, fill_failed resets and it comes back."""
    gone = [p for p in P if p.get("fill_failed") and not is_ready(p)]
    return [p for p in P if p not in gone] if gone and len(P) - len(gone) >= 3 else P

def meta_desc(txt, limit=158):
    """Meta/OG description: full text if short enough, else cut at a word boundary (Google truncates ~155-160 chars anyway)."""
    t = " ".join((txt or "").split())
    if len(t) <= limit:
        return t
    cut = t[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-\u2013\u2014.")
    while cut.rsplit(" ", 1)[-1].lower() in ("and", "or", "the", "a", "an", "of", "with", "to", "for", "in", "on", "at", "by", "but", "as", "from") and " " in cut:
        cut = cut.rsplit(" ", 1)[0].rstrip(" ,;:-\u2013\u2014.")           # never end on a dangling word
    return cut + "\u2026"


def ld_json(d, base, url=None, parent=None):
    """Article + BreadcrumbList structured data (only when a canonical base URL is known)."""
    if not base: return ""
    url = url or f"{base}/g/{d['slug']}/"
    try: iso = datetime.datetime.strptime(d.get("date", ""), "%b %d, %Y").date().isoformat()
    except Exception: iso = None
    art = {"@type": "Article", "headline": d["title"][:110], "description": d["summary"], "mainEntityOfPage": url,
           "author": {"@type": "Organization", "name": "Zoe Picks", "url": base + "/about.html"},
           "publisher": {"@type": "Organization", "name": "Zoe Picks", "logo": {"@type": "ImageObject", "url": base + "/assets/logo-512.png"}}}
    if iso: art["datePublished"] = iso; art["dateModified"] = iso
    bc = {"@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Zoe Picks", "item": base + "/"},
        {"@type": "ListItem", "position": 2, "name": CATS.get(d["category"], "Guide"), "item": f"{base}/category.html?c={d['category']}"},
        {"@type": "ListItem", "position": 3, "name": parent["title"] if parent else d["title"], "item": f"{base}/g/{(parent or d)['slug']}/"}]
        + ([{"@type": "ListItem", "position": 4, "name": d["title"], "item": url}] if parent else [])}
    return '<script type="application/ld+json">' + json.dumps({"@context": "https://schema.org", "@graph": [art, bc]}, ensure_ascii=False).replace("</", "<\\/") + "</script>"

def offers_html(d):
    """Compact 'Deals today' strip for THIS guide's product type: one small label + a horizontally scrollable row of chips
    (product name + discount pill), read client-side from guides/offers.json. Stays hidden when there is no fresh verified
    discount for this type. Shows ONLY name + discount; all other detail is in the picks below."""
    js = r"""(function(){var S=%S%,C=%C%,A=%A%;
fetch("../../guides/offers.json",{cache:"no-store"}).then(function(r){return r.json()}).then(function(j){
var T=(j&&j.types)||{},fh=((j&&j.fresh_h)||36)*3600,n=Date.now()/1000,best=null,bl=0,s="-"+S+"-";
var order=[C].concat(A||[]).concat(Object.keys(T));
order.forEach(function(c,i){if(order.indexOf(c)!==i)return;(T[c]||[]).forEach(function(t){if(s.indexOf("-"+t.key+"-")>-1&&t.key.length>bl){best=t;bl=t.key.length}})});
if(!best)return;
var um=/-under-(\d+)-/.exec(s),cap=um?+um[1]:0,seen={},L=(best.items||[]).filter(function(x){if(cap&&x.now&&parseFloat(x.now)>cap)return false;if(seen[x.asin]||!(n-x.t<fh)||!/^https:\/\/www\.amazon\.com\//.test(x.link||""))return false;seen[x.asin]=1;return true}).slice(0,3);
if(!L.length)return;
var E=function(v){return String(v==null?"":v).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})};
var chip=function(x){var im=(x.img||"").indexOf("https://")===0?'<img class="dl-i" src="'+E(x.img)+'" alt="" loading="lazy" referrerpolicy="no-referrer">':'<span class="dl-i"></span>';var pr=(x.now&&x.was)?'<span class="dl-p"><strong>$'+E(x.now)+'</strong><s>$'+E(x.was)+'</s></span>':'';var dd=x.deal?'<span class="dl-d">'+E(x.deal)+'</span>':'';return '<a class="dl-c" href="'+E(x.link)+'" rel="sponsored nofollow noopener" target="_blank">'+im+'<span class="dl-b"><span class="dl-t">'+E(x.name)+'</span>'+pr+dd+'</span><b>-'+E(x.pct)+'%%</b></a>'};
var z=document.getElementById("zo");z.innerHTML='<div class="dl-h"><i></i>Deals today \u00b7 '+E(best.label)+'</div><div class="dl-r">'+L.map(chip).join("")+'</div><p class="dl-n">Prices change fast. Confirm on Amazon before buying.</p>';z.hidden=false;
}).catch(function(){})})();"""
    also = d.get("also") or []
    also = [also] if isinstance(also, str) else list(also)
    js = js.replace("%S%", json.dumps(d["slug"])).replace("%C%", json.dumps(d["category"])).replace("%A%", json.dumps(also)).replace("%%", "%")
    return '<section id="zo" class="dl" hidden></section><script>' + js.replace("</", "<\\/") + "</script>"

def render(d, base=""):
    d = dict(d, products=visible_products(d["products"]))
    t = (ROOT / "templates/guide.html").read_text()
    method = d["method"] + (" Assumptions: " + d["assumptions"] if d.get("assumptions") else "")
    rep = {"OGV": og_version(), "TITLE": e(d["title"]), "SUMMARY": e(d["summary"]), "META": e(meta_desc(d["summary"])), "CATKEY": e(d["category"]), "CATNAME": e(CATS.get(d["category"], "Guide")),
           "DATE": e(d["date"]), "METHOD": e(method), "MORE": more_html(d), "CARDS": "\n".join(card(i, p) for i, p in enumerate(d["products"])), "INTRO": intro_html(d), "COMPARE": compare_html(d), "JUMP": jump_html(d), "VOICES": voices_html(d), "COUNTDOWN": countdown_html(d), "HEADCLASS": "deal-head" if d.get("theme") == "deal" else "", "TIPS": tips_html(d), "FAQ": faq_html(d),
           "OFFERS": offers_html(d),   # top-3 discounts of THIS guide's product type live on its own guide page (not on the category page)
           "LD": ld_json(d, base), "OGURL": (f'<meta property="og:url" content="{e(base)}/g/{e(d["slug"])}/"><link rel="canonical" href="{e(base)}/g/{e(d["slug"])}/">') if base else ""}
    # compact rows for every guide (mobile first); deal guides also get the deals strip and biggest-discount-first order
    rep.update({"CARDS": deal_rows(d), "JUMP": "", "COMPARE": "", "VOICES": "", "MORE": "", "FAQ": faq_html(d) + more_html(d)})
    if d.get("theme") == "deal": rep["INTRO"] = deal_strip(d) if is_live(d) else intro_html(d)
    if d.get("draft"): rep["OGURL"] += '<meta name="robots" content="noindex">'   # unfinished guide: reachable by link only, hidden from search
    for k, v in rep.items(): t = t.replace("{{" + k + "}}", v)
    return t

def render_tab(d, t, ps, base=""):
    """One tab of a big guide as its own page (own URL, title, H1, canonical, breadcrumb) - the picks come from the parent guide."""
    title = tab_title(d, t); key = t["key"]; url = f"{base}/g/{d['slug']}/{key}/"
    sib = [(x, n) for x, n in tab_pages(d) if x["key"] != key]
    nav = ('<nav class="tl" style="font-size:14px;margin:4px 0 12px">&larr; <a href="../">' + e(d["title"]) + "</a>"
           + "".join(f' · <a href="../{e(x["key"])}/">{e(x["label"])}</a>' for x, _ in sib) + "</nav>")
    d2 = dict(d, title=title, products=ps, tabs=[], _tabpages=False, also=d.get("also"),
              summary=t.get("summary") or f"{t['label']} picks from our full guide: {d['summary']}")
    html = render(d2, base)
    html = html.replace('<div class="dr-list">', nav + '<div class="dr-list">', 1)
    if base:
        html = re.sub(r'<meta property="og:url" content="[^"]*"><link rel="canonical" href="[^"]*">',
                      lambda m: f'<meta property="og:url" content="{e(url)}"><link rel="canonical" href="{e(url)}">', html, 1)
        html = re.sub(r'<script type="application/ld\+json">.*?</script>', lambda m: ld_json(d2, base, url, d), html, 1, flags=re.S)
    html = html.replace('"../../', '"../../../')
    # "More guides" cards are written as ../<slug>/ (one level up from /g/<guide>/); a tab page sits one level deeper, so they need ../../
    html = re.sub(r'<section class="more">.*?</section>',
                  lambda m: m.group(0).replace('href="../', 'href="../../'), html, 1, flags=re.S)
    return html

def site_files(base):
    """sitemap.xml + static guide links on the homepage (so crawlers / no-JS see them)."""
    L = [g for g in json.loads((ROOT / "guides/index.json").read_text()) if is_live(g)]
    def _lm(g):                                    # <lastmod> from the guide's own date (never faked); empty if unknown
        try: return datetime.datetime.strptime(g.get("date", ""), "%b %d, %Y").strftime("%Y-%m-%d")
        except ValueError: return ""
    urls = [(f"{base}/", "")] + [(f"{base}/{pg}.html", "") for pg in ("about", "contact", "privacy", "terms", "disclosure")] + [(f"{base}/g/{g['slug']}/", _lm(g)) for g in L]
    for g in L:                                   # tab pages of the live big guides
        sf = ROOT / "src" / f"{g['slug']}.json"
        if sf.exists():
            urls += [(f"{base}/g/{g['slug']}/{t['key']}/", _lm(g)) for t, _ in tab_pages(json.load(open(sf)))]
    (ROOT / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"<url><loc>{u}</loc>" + (f"<lastmod>{lm}</lastmod>" if lm else "") + "</url>\n" for u, lm in urls) + "</urlset>\n")
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
    for f in sorted((ROOT / "src").glob("*.json")):
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
    for f in sorted((ROOT / "src").glob("*.json")):
        d = json.load(open(f)); P = visible_products(d.get("products") or [])
        m = {"n": len(P)}
        img = next((x["image"] for x in P if is_ready(x)), None)
        if img: m["img"] = img
        if P: m["top"] = P[0].get("short") or P[0].get("name")
        off = max([(parse_price(x.get("price")) or {}).get("pct", 0) for x in P] or [0])
        if off: m["off"] = off
        meta[d["slug"]] = m
    deals = []   # verified "was/now" prices from live deal guides only (store + as-of date come from the guide's own price text)
    for f in sorted((ROOT / "src").glob("*.json")):
        d = json.load(open(f))
        if not d.get("expires") or d.get("retired") or d.get("draft") or not is_live(d): continue
        for x in visible_products(d.get("products") or []):
            q = parse_price(x.get("price"))
            if not q or not q.get("pct"): continue
            deals.append({"cat": d["category"], **({"also": d["also"]} if d.get("also") else {}), "guide": d["slug"], "name": x.get("short") or x.get("name"),
                          "ver": amazon_holds(x), **({"link": buy_link(x)} if is_ready(x) else {}),
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
    for f in sorted((ROOT / "src").glob("*.json")):
        d = json.load(open(f))
        if d.get("draft") and not d.get("retired") and d["slug"] not in live:
            soon.append({"slug": d["slug"], "title": d["title"], "category": d["category"], **({"also": d["also"]} if d.get("also") else {}), "soon": True})
    soon.sort(key=lambda g: (pr.get(g["slug"], 999), g["slug"]))
    new = [g for g in L if not g.get("soon")] + soon
    if new != L: ix.write_text(json.dumps(new, indent=1))

def sync_live():
    """Add every built (non-draft, non-retired) src/*.json guide that is missing from guides/index.json as a live row.
    The collector only runs rebuild_all, which used to never add new guides, so built guides were orphaned (page existed, not listed)."""
    ix = ROOT / "guides/index.json"; L = json.loads(ix.read_text())
    have = {g["slug"] for g in L if not g.get("soon")}
    add = []
    for f in sorted((ROOT / "src").glob("*.json")):
        d = json.load(open(f))
        if d.get("draft") or d.get("retired") or d["slug"] in have: continue
        add.append({"slug": d["slug"], "title": d["title"], "category": d["category"], "date": d.get("date", ""), **({"also": d["also"]} if d.get("also") else {}), **({"expires": d["expires"]} if d.get("expires") else {})})
    if not add: return
    def _dt(g):
        try: return datetime.datetime.strptime(g["date"], "%b %d, %Y")
        except Exception: return datetime.datetime.min
    add.sort(key=_dt, reverse=True)
    new = {g["slug"] for g in add}
    ix.write_text(json.dumps(add + [g for g in L if g["slug"] not in new], indent=1))

def rebuild_all(base):
    """Re-render every guide from src/*.json (keeps guides/index.json order). Run after changing assets/og.png."""
    sync_live()
    sync_index_expiry()
    sync_soon()
    sync_meta()
    for f in sorted((ROOT / "src").glob("*.json")):
        d = json.load(open(f)); out = ROOT / "g" / d["slug"]; out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text(retired_html(d, base) if d.get("retired") else render(d, base)); print("ok", out / "index.html")
        for t, ps in tab_pages(d):
            (out / t["key"]).mkdir(exist_ok=True); (out / t["key"] / "index.html").write_text(render_tab(d, t, ps, base)); print("ok", out / t["key"] / "index.html")
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
