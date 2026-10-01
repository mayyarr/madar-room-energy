"""
seed_ocm.py  -  يجيب محطات الشحن الحقيقية بالأردن من Open Charge Map
ويخزّنها بجدول jordan_chargers + نسخة كاش بملف (عشان الديمو ما يعتمد على الإنترنت).

  python db/seed_ocm.py            <- يستعمل الكاش إذا موجود، وإلا بيجيب من النت
  python db/seed_ocm.py --refresh  <- بيجيب من النت ويجدد الكاش
"""
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from db.database import get_conn  # noqa: E402

API_URL = "https://api.openchargemap.io/v3/poi"
CACHE_FILE = os.path.join(ROOT, "data", "cache", "ocm_jordan.json")

# مراكز تقريبية للمناطق الثمانية (لنسب كل محطة لأقرب منطقة)
from db.regions import REGION_CENTERS  # noqa: E402  (الـ12 محافظة)
MAX_KM = 30   # أبعد من هيك = "other"


def km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (0.5 - math.cos((lat2 - lat1) * p) / 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2)
    return 12742 * math.asin(math.sqrt(a))


def nearest_region(lat, lon):
    if lat is None or lon is None:
        return "other"
    name, d = min(((n, km(lat, lon, c[0], c[1])) for n, c in REGION_CENTERS.items()), key=lambda x: x[1])
    return name if d <= MAX_KM else "other"


def fetch_from_api():
    import requests
    key = os.environ.get("OCM_API_KEY")
    if not key:
        print("متغير OCM_API_KEY مش موجود. ضيفيه كمتغير بيئة وافتحي التيرمنال من جديد.")
        return None
    r = requests.get(API_URL, headers={"X-API-Key": key}, timeout=60,
                     params={"output": "json", "countrycode": "JO", "maxresults": 1000, "verbose": "false"})
    if r.status_code != 200:
        print("فشل الطلب. الكود:", r.status_code)
        print(r.text[:300])
        return None
    return r.json()


def load_pois(refresh=False):
    if os.path.exists(CACHE_FILE) and not refresh:
        with open(CACHE_FILE, encoding="utf-8") as f:
            print("قرأت من الكاش:", CACHE_FILE)
            return json.load(f)
    data = fetch_from_api()
    if data is None:
        return None
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print("جبت من الإنترنت وحفظت الكاش:", CACHE_FILE)
    return data


def title_of(obj):
    return (obj or {}).get("Title")


def parse(poi):
    a = poi.get("AddressInfo") or {}
    conns = poi.get("Connections") or []
    powers = [c.get("PowerKW") for c in conns if isinstance(c, dict) and c.get("PowerKW")]
    lat, lon = a.get("Latitude"), a.get("Longitude")
    return (
        poi.get("ID"), a.get("Title"), a.get("AddressLine1"), a.get("Town"), lat, lon,
        poi.get("NumberOfPoints"), max(powers) if powers else None,
        title_of(poi.get("OperatorInfo")), title_of(poi.get("UsageType")), title_of(poi.get("StatusType")),
        nearest_region(lat, lon),
    )


def create_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jordan_chargers (
            ocm_id       INTEGER PRIMARY KEY,
            title        TEXT,
            address      TEXT,
            town         TEXT,
            lat          REAL,
            lon          REAL,
            num_points   INTEGER,
            max_power_kw REAL,
            operator     TEXT,
            usage_type   TEXT,
            status       TEXT,
            region       TEXT,
            source       TEXT NOT NULL DEFAULT 'Open Charge Map'
        )
    """)
    conn.commit()


def seed(refresh=False):
    pois = load_pois(refresh)
    if pois is None:
        return
    rows = [parse(p) for p in pois if p.get("ID") is not None]
    conn = get_conn()
    create_table(conn)
    conn.execute("DELETE FROM jordan_chargers")
    conn.executemany(
        "INSERT OR REPLACE INTO jordan_chargers (ocm_id,title,address,town,lat,lon,num_points,max_power_kw,"
        "operator,usage_type,status,region) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM jordan_chargers").fetchone()[0]
    per = conn.execute("SELECT region, COUNT(*) FROM jordan_chargers GROUP BY region ORDER BY 2 DESC").fetchall()
    sample = conn.execute("SELECT title, town, max_power_kw FROM jordan_chargers LIMIT 3").fetchall()
    conn.close()
    print(f"تم: {n} محطة شحن حقيقية بالأردن")
    print("لكل منطقة:", {r[0]: r[1] for r in per})
    print("عينة:", [tuple(s) for s in sample])


if __name__ == "__main__":
    seed(refresh="--refresh" in sys.argv)