# 인도네시아 영업뉴스 수집기. LLM/토큰 미사용: RSS + 키워드 규칙 + 무료 기계번역.
import json, re, time, hashlib, os, urllib.parse
import datetime as dt
import feedparser

OUT, CACHE = "data/news.json", "data/cache.json"
KEEP_DAYS, MAX_TR, USD_IDR = 14, 1500, 16500

Q_ID = ["pabrik baru", "peletakan batu pertama pabrik", "investasi pabrik kawasan industri",
        "smelter", "MoU investasi pabrik", "izin usaha industri", "relokasi pabrik",
        "ekspor impor", "bea masuk tarif", "logistik pelabuhan", "peraturan impor Kemendag",
        "penjualan ritel konsumen"]
Q_EN = ["Indonesia factory groundbreaking", "Indonesia new plant investment",
        "Indonesia industrial estate", "Indonesia tariff exports", "Indonesia port logistics",
        "Indonesia import regulation"]

# 우선순위 순서대로 첫 매칭 적용
TOPICS = [
 ("invest", r"pabrik|smelter|kawasan industri|investasi|groundbreaking|peletakan|relokasi|mou\b|factory|plant\b|industrial (park|estate|zone)|investment"),
 ("tariff", r"bea masuk|tarif|anti-?dumping|safeguard|tariff|duties|duty"),
 ("logistics", r"logistik|pelabuhan|kontainer|kargo|pelayaran|freight|port\b|ports\b|shipping|cargo|logistics"),
 ("law", r"peraturan|permendag|regulasi|undang-undang|kebijakan|aturan|regulation|decree|\blaw\b|policy"),
 ("trade", r"ekspor|impor|export|import"),
 ("consumer", r"ritel|penjualan|konsumen|e-?commerce|retail|sales|consumer"),
]
STAGES = [
 ("open", r"beroperasi|diresmikan|peresmian|operasional|resmi beroperasi|inaugurat|commission|begins operation|opens?\b|starts? operation"),
 ("build", r"peletakan batu pertama|groundbreaking|mulai dibangun|pembangunan dimulai|ground-?breaking|breaks? ground|construction (begins|starts)|start of construction"),
 ("permit", r"izin|perizinan|amdal|lisensi|permit|licen[sc]e|approval|approved"),
 ("plan", r"\bmou\b|rencana|berencana|akan bangun|bakal|kesepakatan|plans?\b|to build|agreement|proposed"),
]
REGIONS = [
 ("kalimantan", r"kalimantan|kaltim|kaltara|\bikn\b|nusantara capital|balikpapan|samarinda|pontianak|banjarmasin"),
 ("sulawesi", r"sulawesi|sulteng|morowali|makassar|manado|kendari|konawe|palu\b"),
 ("sumatra", r"sumatera|sumatra|aceh|medan|riau|batam|palembang|lampung|jambi|padang|bengkulu"),
 ("other", r"\bbali\b|\bntt\b|\bntb\b|nusa tenggara|maluku|halmahera|papua|lombok|weda bay"),
 ("java", r"jakarta|\bjawa\b|java\b|banten|bekasi|karawang|cikarang|surabaya|bandung|semarang|gresik|cilegon|tangerang|subang|batang|yogyakarta|purwakarta|bogor|depok|jabar|jatim|jateng"),
]
SECTORS = [("식품·농수산", r"pangan|makanan|food|palm oil|sawit|seafood|agri"),
           ("니켈·배터리", r"nikel|nickel|baterai|battery|ev\b|smelter"),
           ("전자·반도체", r"elektronik|semikonduktor|semiconductor|electronic"),
           ("섬유·의류", r"tekstil|garmen|textile|garment|footwear|sepatu"),
           ("자동차", r"otomotif|mobil|automotive|vehicle|\bcar\b"),
           ("산업단지", r"kawasan industri|industrial (park|estate|zone)|\bkek\b|sez\b")]

# 인도(India) 관련 기사 제외. 인도네시아(Indonesia/Indo)는 해당 없음. 제외 단어를 늘리려면 INDIA 패턴에 추가하세요.
INDIA = re.compile(r"\bindia\b|\bindians?\b(?!\s+ocean)|new delhi|mumbai|bengaluru|bangalore|hyderabad|chennai|kolkata|"
                   r"gujarat|maharashtra|\brupees?\b|\bsensex\b", re.I)
def is_india(title, src=""):
    return bool(INDIA.search(f"{title} {src}"))

def first(rules, text):
    for k, p in rules:
        if re.search(p, text, re.I): return k
    return None

def num(s, lang):
    s = s.strip(".,")
    s = s.replace(".", "").replace(",", ".") if lang == "id" else s.replace(",", "")
    try: return float(s)
    except ValueError: return None

def amount_usd(text, lang):
    m = re.search(r"(?:US\$|USD|\$)\s?([\d.,]+)\s?(miliar|billion|bn|juta|million)\b", text, re.I)
    if m and (n := num(m.group(1), lang)):
        return n * (1e9 if m.group(2).lower() in ("miliar", "billion", "bn") else 1e6)
    m = re.search(r"Rp\.?\s?([\d.,]+)\s?(triliun|miliar|juta|trillion|billion)", text, re.I)
    if m and (n := num(m.group(1), lang)):
        mult = {"triliun": 1e12, "trillion": 1e12, "miliar": 1e9, "billion": 1e9, "juta": 1e6}[m.group(2).lower()]
        return n * mult / USD_IDR
    return None

def norm(t): return re.sub(r"[^a-z0-9가-힣 ]", "", t.lower())
def toks(t): return {w for w in norm(t).split() if len(w) > 3}

def load(p, default):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return default

def fetch(q, lang):
    hl, ceid = ("id", "ID:id") if lang == "id" else ("en-ID", "ID:en")
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(q + ' when:3d')}&hl={hl}&gl=ID&ceid={ceid}"
    return feedparser.parse(url).entries

cache, ntr = load(CACHE, {}), 0
ARGOS = None
def setup_argos():
    # 무료 오프라인 번역 모델(Argos) 설치: 인니어->영어, 영어->한국어 (외부 번역 서버 호출 없음)
    global ARGOS
    try:
        import argostranslate.package as ap, argostranslate.translate as at
        have = {(p.from_code, p.to_code) for p in ap.get_installed_packages()}
        need = [x for x in (("id", "en"), ("en", "ko")) if x not in have]
        if need:
            ap.update_package_index()
            for pkg in ap.get_available_packages():
                if (pkg.from_code, pkg.to_code) in need:
                    ap.install_from_path(pkg.download())
        ARGOS = at
    except Exception as e:
        print("argos setup error:", repr(e)[:200])

def tr(text, src, tgt):
    global ntr
    key = hashlib.md5(f"{src}{tgt}{text}".encode()).hexdigest()
    if cache.get(key): return cache[key]
    if ARGOS is None or ntr >= MAX_TR: return ""
    try:
        out = ARGOS.translate(text, src, tgt).strip()
        ntr += 1
        if out: cache[key] = out
        return out
    except Exception as e:
        print("translate error:", repr(e)[:150])
        return ""

old = load(OUT, {"items": []})["items"]
now = dt.datetime.now(dt.timezone.utc)
cut = (now - dt.timedelta(days=KEEP_DAYS)).isoformat()
items = {i["id"]: i for i in old if i["published"] >= cut and not is_india(i["title"], i.get("source", ""))}

for lang, qs in (("id", Q_ID), ("en", Q_EN)):
    for q in qs:
        for e in fetch(q, lang):
            src = (e.get("source") or {}).get("title", "")
            title = e.title.rsplit(" - ", 1)[0].strip() if src and e.title.endswith(src) else e.title.strip()
            iid = hashlib.md5(norm(title).encode()).hexdigest()[:12]
            if iid in items or not e.get("published_parsed") or "vietnam" in src.lower() or is_india(title, src): continue
            pub = dt.datetime(*e.published_parsed[:6], tzinfo=dt.timezone.utc).isoformat()
            if pub < cut: continue
            text = title + " " + e.get("summary", "")
            topic = first(TOPICS, text) or "industry"
            stage = first(STAGES, text) if topic == "invest" else None
            amt = amount_usd(title, lang)
            items[iid] = {"id": iid, "title": title, "url": e.link, "source": src, "published": pub,
                          "lang": lang, "topic": topic, "stage": stage, "region": first(REGIONS, text),
                          "sector": first(SECTORS, text), "usd": amt}

# 유사 제목 중복 제거(같은 프로젝트의 여러 매체 기사)
out, seen = [], []
for it in sorted(items.values(), key=lambda x: x["published"], reverse=True):
    t = toks(it["title"])
    if t and any(it["topic"] == o["topic"] and len(t & s) / len(t | s) >= 0.6 for o, s in seen): continue
    out.append(it); seen.append((it, t))

need = [i for i in out if not i.get("title_ko") or not i.get("title_en")]
if need: setup_argos()
for it in need:
    if it["lang"] == "id":
        if not it.get("title_en"): it["title_en"] = tr(it["title"], "id", "en")
        if not it.get("title_ko") and it["title_en"]: it["title_ko"] = tr(it["title_en"], "en", "ko")
    else:
        it["title_en"] = it["title"]
        if not it.get("title_ko"): it["title_ko"] = tr(it["title"], "en", "ko")

os.makedirs("data", exist_ok=True)
json.dump({"updated": now.isoformat(), "items": out}, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
print(f"items={len(out)} translated={ntr}")
