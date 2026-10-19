# Zoe Picks: how to write or fix a guide (read this FIRST)

Any AI or person asked to "write / fix / improve a guide" follows this file. The reference quality bar is `src/best-coffee-makers-2026.json`
(and `src/best-laptops-for-college-students-2026.json`). Open one of them before writing.

## 1. Where things live
- One guide = one file `src/<slug>.json`. Edit ONLY that file. Never edit `g/`, `guides/`, `index.html` or `sitemap.xml` by hand:
  the "Render guides" workflow rebuilds them within ~2 minutes of every push that touches `src/**`.
- Bots commit to guide files all day (images, prices). So: read the file fresh, change only your fields, and when saving
  through the GitHub API re-read the sha and retry on a 409 conflict. Never overwrite a whole file from an old copy.
- Do not run workflows, bots or the fill job unless the owner asked for it.
- Never paste or store tokens in files, commits or chat logs.

## 2. Guide fields
Guide level: `slug, title (under ~65 chars, "(2026)" style), category, date ("Oct 18, 2026"), summary, method, assumptions,
verdict, intro (list of 1-2 paragraphs), voices (optional), tips, faq, products`.
Categories: home_kitchen, electronics, laptops, mobiles, monitors, accessories, beauty_health, fitness_outdoor, fashion,
toys_kids, tools_auto, pets, office_books, gaming, smart_home, furniture_decor, hobby_music, other.

Per product: `name` (keep it stable: bots match on it), `short`, `badge` ("Best Overall", "Best Budget" ...), `why` (list, 2 short
paragraphs), `desc`, `benefit`, `specs` (up to 6), `pros` (3-4), `cons` (2-4), `best_for`, `skip_if`, `link`.
`link` must be exactly `https://www.amazon.com/s?k=<Product+Name>&tag=zoepicks00-20` (the renderer rejects anything else).

DO NOT write or change these (the Amazon bot fills them): `asin, image, image_asin, amazon_price, amazon_price_asof,
amazon_listing, amazon_rating, amazon_reviews, amazon_rating_asof, price_asof, fill_tries, fill_last, fill_failed`.
`price` only when a source states the price: `"$599 (was $699) at Store"` or `"$599 at Store"`. Otherwise leave it out.

## 3. Hard rules (anti-invention)
1. Facts only from sources you actually read (hands-on review sites, official spec pages, Amazon listing). Not from memory.
2. Every number (specs, hours, lb, warranty years, ratings, review counts) must appear in a source or in the product's own
   Amazon fields. No number found = leave it out.
3. No price inside `desc`, `benefit`, `verdict`, `intro`, `faq`. Prices change; the page shows them from the bot.
4. No hype: never "best ever", "amazing", "perfect", "students love it", "everyone buys this". No fake urgency or FOMO.
   A deal line is allowed only if a source states it.
5. Every pick needs an honest drawback. A guide where nothing has a catch will not be trusted.
6. Simple English, short sentences. No claims of testing we did not do (say "PCWorld measured", not "we tested").
7. Do not mention a Black Friday / sale unless the guide is a deal guide or a source states it. The site's BF date is Nov 27.
8. Rating counts under ~50 reviews are not used as proof.

## 4. How to write each part
**`desc`** (1-2 sentences): what the product is and has, from facts. Include one thing it does NOT have. No opinion.

**`benefit`** ("Why buy it", 3-4 sentences, this order):
1. `Picture ...` one short real scene of the buyer this pick suits using it.
2. The real gain, from source facts (battery, weight, warranty, saves money/time).
3. Why it wins against the OTHER picks in this guide, using only supportable comparisons ("the only 16-inch laptop in this guide",
   "the longest battery in this guide").
4. `The catch:` one honest drawback taken from the cons or owner complaints.

**`skip_if`**: the buyer who should not choose it, and the reason from a listed con.
**`best_for`**: one line, who it is for.
**`verdict`** (top of the page, decisive, never "it depends"):
`{"pick": <exact product name>, "line": "who should buy it and why (1-2 sentences, ends with its main catch)",
  "also": [["Longest battery","<exact product name>"], ... up to 4]}`
**`intro`**: paragraph 1 = a hook built from a real fact in the guide (a surprising number or a common mistake). Paragraph 2 is optional
(price/season note, no invented deals).
**`tips`**: `{"title": "4 mistakes ...", "items": [[bold lead, one-sentence explanation], ...]}`, each item backed by guide facts.
**`faq`**: `[[question, answer], ...]` 4-5 real buyer questions ("best for most people?", "longest battery?", "is X worth it?").
**`voices`** (optional): only real reviewer notes that exist in a source, shown as `{name, src, like, catch}`. Skip if you have no source.
**Counts**: 3-5 picks per guide for complex products (fewer, clearly different picks convert better than a long list).

## 5. Why this structure (what the research says)
Trust beats hype. Balanced pros and cons, proof of real data, a decisive top pick shown first, and a few clearly different
options convert better than long lists and enthusiastic copy. Mobile first: short lines, the answer before the detail.

## 6. Checklist before you save
- [ ] Opened the reference guide and followed its shape.
- [ ] Every number traced to a source. No price in prose.
- [ ] Each pick has `desc`, `benefit` (4 parts), `skip_if`, honest cons.
- [ ] `verdict.pick` and every `also` name equals a product `name` exactly.
- [ ] No bot-filled fields touched; `link` format correct; JSON valid.
- [ ] Saved only `src/<slug>.json`, with a fresh sha. Waited for the render workflow and checked the live page.
- [ ] Told the owner in 5 lines what changed, and what could not be verified.

## 7. Starter prompt (owner pastes this into any new chat / account)
> You work on Zoe Picks (repo zoepicks/zoepicks.github.io, public). Before writing or fixing any guide, read
> `plan/GUIDE_RULES.md` in that repo and follow it exactly, then open `src/best-coffee-makers-2026.json` as the quality reference.
> Do not run bots or workflows. Edit only the one guide file I name. Show me what you wrote when done.
