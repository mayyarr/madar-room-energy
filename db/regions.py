"""
db/regions.py - مرجع واحد لمحافظات الأردن الـ12 بالنظام.

البيانات الأصلية (عدادات لندن، شواحن Caltech) كانت موزّعة على 8 محافظات. لتغطية الـ12،
منقسم أعضاء بعض المحافظات بشكل ثابت (حتمي) على جاراتها المباشرة:
    إربد → إربد / عجلون / جرش      الكرك → الكرك / الطفيلة      العقبة → العقبة / معان
عمّان، الزرقاء، البلقاء، مادبا، المفرق ما بتتغير (سيناريوهات العرض الأساسية مبنية عليها).
"""
REGIONS = ["Amman", "Zarqa", "Irbid", "Aqaba", "Salt", "Madaba", "Karak", "Mafraq",
           "Jerash", "Ajloun", "Tafilah", "Ma'an"]
BASE_REGIONS = REGIONS[:8]

REGION_CENTERS = {
    "Amman": (31.95, 35.93), "Zarqa": (32.07, 36.09), "Irbid": (32.56, 35.85), "Aqaba": (29.53, 35.01),
    "Salt": (32.04, 35.73), "Madaba": (31.72, 35.79), "Karak": (31.18, 35.70), "Mafraq": (32.34, 36.21),
    "Jerash": (32.28, 35.90), "Ajloun": (32.33, 35.75), "Tafilah": (30.84, 35.60), "Ma'an": (30.19, 35.73),
}

REGION_AR = {"Amman": "عمّان", "Zarqa": "الزرقاء", "Irbid": "إربد", "Aqaba": "العقبة", "Salt": "السلط",
             "Madaba": "مادبا", "Karak": "الكرك", "Mafraq": "المفرق", "Jerash": "جرش", "Ajloun": "عجلون",
             "Tafilah": "الطفيلة", "Ma'an": "معان"}

SPLIT = {"Irbid": ["Irbid", "Ajloun", "Jerash"], "Karak": ["Karak", "Tafilah"], "Aqaba": ["Aqaba", "Ma'an"]}


def spread(pairs):
    """pairs: [(id, base_region)] مرتبة. بيرجّع {id: region} بعد تقسيم المحافظات المقسومة."""
    seen, out = {}, {}
    for _id, reg in pairs:
        k = seen.get(reg, 0); seen[reg] = k + 1
        opts = SPLIT.get(reg)
        out[_id] = opts[k % len(opts)] if opts else reg
    return out


def migrate(conn):
    """يطبّق التوزيع على قاعدة بيانات موجودة (بدون إعادة تحميل البيانات)."""
    import math
    rows = conn.execute("SELECT meter_id, region FROM meters WHERE meter_id LIKE 'MAC%' ORDER BY meter_id").fetchall()
    for mid, reg in spread([(r[0], r[1]) for r in rows]).items():
        conn.execute("UPDATE meters SET region=? WHERE meter_id=?", (reg, mid))
    rows = conn.execute("SELECT charger_id, region FROM chargers WHERE charger_id LIKE '1-1-%' ORDER BY charger_id").fetchall()
    for cid, reg in spread([(r[0], r[1]) for r in rows]).items():
        conn.execute("UPDATE chargers SET region=? WHERE charger_id=?", (reg, cid))
    # محطات OCM الحقيقية: إعادة نسب كل محطة لأقرب مركز من الـ12
    def km(a, b, c, d):
        p = math.pi / 180
        x = 0.5 - math.cos((c - a) * p) / 2 + math.cos(a * p) * math.cos(c * p) * (1 - math.cos((d - b) * p)) / 2
        return 12742 * math.asin(math.sqrt(x))
    for sid, lat, lon in conn.execute("SELECT rowid, lat, lon FROM jordan_chargers").fetchall():
        if lat is None or lon is None:
            continue
        n, dist = min(((n, km(lat, lon, c[0], c[1])) for n, c in REGION_CENTERS.items()), key=lambda t: t[1])
        conn.execute("UPDATE jordan_chargers SET region=? WHERE rowid=?", (n if dist <= 30 else "other", sid))
    conn.commit()


if __name__ == "__main__":
    import os, sqlite3, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import config
    c = sqlite3.connect(config.DB_PATH); migrate(c)
    for t in ("meters", "chargers", "jordan_chargers"):
        print(t, dict(c.execute(f"SELECT region, COUNT(*) FROM {t} GROUP BY region").fetchall()))
