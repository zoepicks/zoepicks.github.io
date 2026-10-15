"""Render one buying guide (static HTML, so X link previews work) + update guides/index.json.
Usage: python tools/render_guide.py guide.json [BASE_URL]
guide.json: {slug,title,category,summary,method,date,assumptions?,products:[{name,badge?,price?,price_asof?,specs[],pros[],cons[],best_for,link,image?}]}
Only fields that are present get rendered — nothing is invented."""
import json, sys, html, re, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
CATS = {"home_kitchen": "Home & Kitchen", "electronics": "Electronics", "computers_phones": "Computers & Phones", "beauty_health": "Beauty & Health",
        "fitness_outdoor": "Fitness & Outdoor", "fashion": "Fashion", "toys_kids": "Toys & Kids", "tools_auto": "Tools & Auto", "pets": "Pets",
        "office_books": "Office & Books", "gaming": "Gaming", "smart_home": "Smart Home & TV", "furniture_decor": "Furniture & Decor",
        "hobby_music": "Hobby & Music", "other": "More"}
e = lambda s: html.escape(str(s), quote=True)
ul = lambda xs: "<ul>" + "".join(f"<li>{e(x)}</li>" for x in xs) + "</ul>"

def card(i, p):
    if not re.match(r"^https://www\.amazon\.com/.*tag=iamcartigo-20", p.get("link", "")):
        raise ValueError(f"product {i+1}: link must be an amazon.com URL with tag=iamcartigo-20")
    h = '<article class="card">' + (f'<span class="rank">{e(p["badge"])}</span>' if p.get("badge") else "") + f'<h3>{e(p["name"])}</h3>'
    if p.get("price"): h += f'<div class="price">{e(p["price"])}' + (f' · as of {e(p["price_asof"])}' if p.get("price_asof") else "") + ' — may have changed</div>'
    if p.get("image"): h += f'<img src="{e(p["image"])}" alt="{e(p["name"])}" loading="lazy">'
    if p.get("specs"): h += '<ul class="specs">' + "".join(f"<li>{e(s)}</li>" for s in p["specs"]) + "</ul>"
    if p.get("pros") or p.get("cons"): h += f'<div class="pc"><div class="pro"><b>Pros</b>{ul(p.get("pros", []))}</div><div class="con"><b>Cons</b>{ul(p.get("cons", []))}</div></div>'
    if p.get("best_for"): h += f'<div class="best"><b>Best for:</b> {e(p["best_for"])}</div>'
    return h + f'<a class="btn" href="{e(p["link"])}" rel="sponsored nofollow noopener" target="_blank">Check price on Amazon <small>(affiliate link)</small></a></article>'

def render(d, base=""):
    t = (ROOT / "templates/guide.html").read_text()
    method = d["method"] + (" Assumptions: " + d["assumptions"] if d.get("assumptions") else "")
    rep = {"TITLE": e(d["title"]), "SUMMARY": e(d["summary"]), "CATKEY": e(d["category"]), "CATNAME": e(CATS.get(d["category"], "Guide")),
           "DATE": e(d["date"]), "METHOD": e(method), "CARDS": "\n".join(card(i, p) for i, p in enumerate(d["products"])),
           "OGURL": f'<meta property="og:url" content="{e(base)}/g/{e(d["slug"])}/">' if base else ""}
    for k, v in rep.items(): t = t.replace("{{" + k + "}}", v)
    return t

if __name__ == "__main__":
    d = json.load(open(sys.argv[1])); base = sys.argv[2].rstrip("/") if len(sys.argv) > 2 else ""
    if not re.match(r"^[a-z0-9-]{3,80}$", d["slug"]): raise SystemExit("bad slug")
    out = ROOT / "g" / d["slug"]; out.mkdir(parents=True, exist_ok=True); (out / "index.html").write_text(render(d, base))
    ix = ROOT / "guides/index.json"; L = [x for x in json.loads(ix.read_text()) if x["slug"] != d["slug"]]
    L.insert(0, {"slug": d["slug"], "title": d["title"], "category": d["category"], "date": d["date"]}); ix.write_text(json.dumps(L[:500], indent=1))
    print("ok", out / "index.html")
