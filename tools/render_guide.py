"""Render one buying guide (static HTML, so X link previews work) + update guides/index.json.
Usage: python tools/render_guide.py guide.json [BASE_URL]\n       python tools/render_guide.py --all BASE_URL   (re-render all guides; run after changing assets/og.png — og:image ?v= is auto-set from the image hash)
guide.json: {slug,title,category,summary,method,date,assumptions?,products:[{name,badge?,price?,price_asof?,specs[],pros[],cons[],best_for,link,image?}]}
Only fields that are present get rendered — nothing is invented."""
import json, sys, html, re, pathlib, hashlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
CATS = {"home_kitchen": "Home & Kitchen", "electronics": "Electronics", "computers_phones": "Computers & Phones", "beauty_health": "Beauty & Health",
        "fitness_outdoor": "Fitness & Outdoor", "fashion": "Fashion", "toys_kids": "Toys & Kids", "tools_auto": "Tools & Auto", "pets": "Pets",
        "office_books": "Office & Books", "gaming": "Gaming", "smart_home": "Smart Home & TV", "furniture_decor": "Furniture & Decor",
        "hobby_music": "Hobby & Music", "other": "More"}
EMOJI = {"home_kitchen":"🍳","electronics":"🎧","computers_phones":"💻","beauty_health":"🧴","fitness_outdoor":"🏃","fashion":"👟","toys_kids":"🧸","tools_auto":"🔧","pets":"🐶","office_books":"📚","gaming":"🎮","smart_home":"🏠","furniture_decor":"🛋️","hobby_music":"🎸","other":"✨"}
def og_version():
    f = ROOT / "assets/og.png"
    return hashlib.md5(f.read_bytes()).hexdigest()[:8] if f.exists() else "0"
e = lambda s: html.escape(str(s), quote=True)
ul = lambda xs: "<ul>" + "".join(f"<li>{e(x)}</li>" for x in xs) + "</ul>"

def card(i, p):
    if not re.match(r"^https://www\.amazon\.com/.*tag=iamcartigo-20", p.get("link", "")):
        raise ValueError(f"product {i+1}: link must be an amazon.com URL with tag=iamcartigo-20")
    h = f'<article class="card" id="p{i+1}">' + (f'<span class="rank">{e(p["badge"])}</span>' if p.get("badge") else "") + f'<h3>{e(p["name"])}</h3>'
    if p.get("price"): h += f'<div class="price">{e(p["price"])}' + (f' · as of {e(p["price_asof"])}' if p.get("price_asof") else "") + ' — may have changed</div>'
    if p.get("image"): h += f'<img src="{e(p["image"])}" alt="{e(p["name"])}" loading="lazy">'
    if p.get("why"):
        paras = p["why"] if isinstance(p["why"], list) else [p["why"]]
        h += '<div class="why"><b>Why we picked it</b>' + "".join(f"<p>{e(x)}</p>" for x in paras) + "</div>"
    if p.get("specs"): h += '<b class="lbl">Key specs</b><ul class="specs">' + "".join(f"<li>{e(s)}</li>" for s in p["specs"]) + "</ul>"
    if p.get("pros") or p.get("cons"): h += f'<div class="pc"><div class="pro"><b>Pros</b>{ul(p.get("pros", []))}</div><div class="con"><b>Cons</b>{ul(p.get("cons", []))}</div></div>'
    if p.get("best_for"): h += f'<div class="best"><b>Best for:</b> {e(p["best_for"])}</div>'
    if p.get("skip_if"): h += f'<div class="skip"><b>Skip it if:</b> {e(p["skip_if"])}</div>'
    if p.get("deal_note"): h += f'<div class="deal"><b>Deal tip:</b> {e(p["deal_note"])}</div>'
    return h + f'<a class="btn" href="{e(p["link"])}" rel="sponsored nofollow noopener" target="_blank">Check price on Amazon <small>(affiliate link)</small></a></article>'

def intro_html(d):
    xs = d.get("intro")
    if not xs: return ""
    xs = xs if isinstance(xs, list) else [xs]
    return '<section class="intro">' + "".join(f"<p>{e(x)}</p>" for x in xs) + "</section>"

def compare_html(d):
    if not d.get("compare"): return ""
    rows = "".join(f'<tr><td><a href="#p{i+1}">{e(p["name"])}</a></td><td>{e(p.get("badge", ""))}</td><td>{e(p.get("price", "Check price"))}</td></tr>' for i, p in enumerate(d["products"]))
    return f'<h2>Quick comparison</h2><div class="tbl"><table><thead><tr><th>Laptop</th><th>Pick</th><th>Price seen</th></tr></thead><tbody>{rows}</tbody></table></div>'

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
    L = [g for g in L if g["slug"] != d["slug"]]
    L.sort(key=lambda g: g["category"] != d["category"])   # stable: keeps newest-first inside each group
    if not L: return ""
    cards = "".join(f'<a href="../{e(g["slug"])}/"><small>{e(CATS.get(g["category"], "Guide"))} · {e(g.get("date", ""))}</small><b>{e(g["title"])}</b><span>See top 5 picks →</span></a>' for g in L[:10])
    return f'<section class="more"><h2>More guides you might like</h2><div class="rail">{cards}</div></section>'

def render(d, base=""):
    t = (ROOT / "templates/guide.html").read_text()
    method = d["method"] + (" Assumptions: " + d["assumptions"] if d.get("assumptions") else "")
    rep = {"OGV": og_version(), "TITLE": e(d["title"]), "SUMMARY": e(d["summary"]), "CATKEY": e(d["category"]), "CATNAME": e(CATS.get(d["category"], "Guide")),
           "DATE": e(d["date"]), "METHOD": e(method), "MORE": more_html(d), "CARDS": "\n".join(card(i, p) for i, p in enumerate(d["products"])), "INTRO": intro_html(d), "COMPARE": compare_html(d), "TIPS": tips_html(d), "FAQ": faq_html(d),
           "OGURL": (f'<meta property="og:url" content="{e(base)}/g/{e(d["slug"])}/"><link rel="canonical" href="{e(base)}/g/{e(d["slug"])}/">') if base else ""}
    for k, v in rep.items(): t = t.replace("{{" + k + "}}", v)
    return t

def site_files(base):
    """sitemap.xml + static guide links on the homepage (so crawlers / no-JS see them)."""
    L = json.loads((ROOT / "guides/index.json").read_text())
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

def rebuild_all(base):
    """Re-render every guide from src/*.json (keeps guides/index.json order). Run after changing assets/og.png."""
    for f in sorted((ROOT / "src").glob("*.json")):
        d = json.load(open(f)); out = ROOT / "g" / d["slug"]; out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text(render(d, base)); print("ok", out / "index.html")
    site_files(base)

if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--all":
        rebuild_all(sys.argv[2].rstrip("/")); raise SystemExit
    d = json.load(open(sys.argv[1])); base = sys.argv[2].rstrip("/") if len(sys.argv) > 2 else ""
    if not re.match(r"^[a-z0-9-]{3,80}$", d["slug"]): raise SystemExit("bad slug")
    out = ROOT / "g" / d["slug"]; out.mkdir(parents=True, exist_ok=True); (out / "index.html").write_text(render(d, base))
    ix = ROOT / "guides/index.json"; L = [x for x in json.loads(ix.read_text()) if x["slug"] != d["slug"]]
    L.insert(0, {"slug": d["slug"], "title": d["title"], "category": d["category"], "date": d["date"]}); ix.write_text(json.dumps(L[:500], indent=1))
    print("ok", out / "index.html")
    if base: rebuild_all(base)
