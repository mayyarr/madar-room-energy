"""
services/grid_graph.py - Graph reasoning فعلي على شبكة الأردن (networkx)

بدل وصف نصي للأثر الجغرافي، منبني غراف حقيقي ومنشغّل عليه خوارزميات:
  - dijkstra / shortest_path : كيف بينتقل أثر العطل من موقعه لباقي المحافظات، وبأي ترتيب
  - connected_components     : لو شلنا الأصل المعطّل (أو محطة التوزيع بمحافظته) شو بينفصل عن الشبكة
  - betweenness_centrality   : أهم نقطة بالشبكة، وترتيب الأصل المعطّل بين كل العقد

العقد:
  - 12 محافظة (مراكز حقيقية)، والتجاور بينها محسوب من حدودها الإدارية الفعلية
    (static/geo/jordan_governorates.json) — مش مكتوب يدوي.
  - 4 محطات توليد بمواقعها الحقيقية التقريبية + وحدات التوليد التابعة إلها.
  - محطات الشحن العامة الحقيقية من Open Charge Map (jordan_chargers) — محافظتها
    محسوبة بـ point-in-polygon على الحدود الحقيقية.
  - العدادات/الشواحن يلي بتظهر بالحادثة (موقع تقريبي داخل محافظتها).
الأضلاع: تجاور إداري (ربط شبكي بين المحافظات) + تغذية (محطة ← محافظة) + تفريع (أصل ← محافظته).
الوزن = المسافة الحقيقية بالكيلومتر.

تنبيه صادق: المواقع والمسافات والحدود حقيقية، بس "مين موصول بمين" نموذج طوبولوجي
تقريبي — بيانات شبكة النقل الأردنية الفعلية مش منشورة. نفس الخوارزميات بتشتغل
على الطوبولوجيا الحقيقية لو توفرت، بدون تغيير بالكود.
"""
import hashlib
import json
import math
import os
import re
import threading

import networkx as nx

from db.database import get_conn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEO_FILE = os.path.join(ROOT, "static", "geo", "jordan_governorates.json")

# المحافظات الـ12: الكود (ISO 3166-2:JO) ← الاسم العربي الرسمي، المركز الإداري وإحداثياته
GOVS = {
    "JO-AM": {"ar": "العاصمة (عمّان)", "en": "Amman", "seat": "عمّان", "lat": 31.9539, "lon": 35.9106},
    "JO-IR": {"ar": "إربد", "en": "Irbid", "seat": "إربد", "lat": 32.5556, "lon": 35.8500},
    "JO-AZ": {"ar": "الزرقاء", "en": "Zarqa", "seat": "الزرقاء", "lat": 32.0728, "lon": 36.0880},
    "JO-BA": {"ar": "البلقاء", "en": "Balqa", "seat": "السلط", "lat": 32.0392, "lon": 35.7272},
    "JO-MD": {"ar": "مادبا", "en": "Madaba", "seat": "مادبا", "lat": 31.7160, "lon": 35.7939},
    "JO-KA": {"ar": "الكرك", "en": "Karak", "seat": "الكرك", "lat": 31.1853, "lon": 35.7048},
    "JO-AT": {"ar": "الطفيلة", "en": "Tafilah", "seat": "الطفيلة", "lat": 30.8375, "lon": 35.6042},
    "JO-MN": {"ar": "معان", "en": "Ma'an", "seat": "معان", "lat": 30.1962, "lon": 35.7341},
    "JO-AQ": {"ar": "العقبة", "en": "Aqaba", "seat": "العقبة", "lat": 29.5321, "lon": 35.0063},
    "JO-MA": {"ar": "المفرق", "en": "Mafraq", "seat": "المفرق", "lat": 32.3434, "lon": 36.2080},
    "JO-JA": {"ar": "جرش", "en": "Jerash", "seat": "جرش", "lat": 32.2808, "lon": 35.8993},
    "JO-AJ": {"ar": "عجلون", "en": "Ajloun", "seat": "عجلون", "lat": 32.3326, "lon": 35.7517},
}
# مناطق المشروع (8) ← المحافظة الرسمية. "Salt" = مركز محافظة البلقاء.
REGION_TO_GOV = {"Amman": "JO-AM", "Zarqa": "JO-AZ", "Irbid": "JO-IR", "Aqaba": "JO-AQ",
                 "Salt": "JO-BA", "Madaba": "JO-MD", "Karak": "JO-KA", "Mafraq": "JO-MA",
                 "Jerash": "JO-JA", "Ajloun": "JO-AJ", "Tafilah": "JO-AT", "Ma'an": "JO-MN"}

# محطات التوليد (مواقع تقريبية للمحطات الحقيقية)
PLANTS = {
    "PLANT-AQABA":  {"ar": "محطة العقبة الحرارية", "gov": "JO-AQ", "lat": 29.3700, "lon": 34.9860,
                     "site_match": "العقبة"},
    "PLANT-HUSSEIN": {"ar": "محطة الحسين الحرارية", "gov": "JO-AZ", "lat": 32.1180, "lon": 36.0930,
                      "site_match": "الحسين"},
    "PLANT-SAMRA":  {"ar": "محطة السمرا لتوليد الكهرباء", "gov": "JO-AZ", "lat": 32.1540, "lon": 36.1700,
                     "site_match": "السمرا"},
    "PLANT-QATRANA": {"ar": "محطة القطرانة لتوليد الكهرباء", "gov": "JO-KA", "lat": 31.2280, "lon": 36.0550,
                      "site_match": "القطرانة"},
}
TYPE_AR = {"gov": "محافظة", "plant": "محطة توليد", "unit": "وحدة توليد", "station": "محطة شحن عامة",
           "meter": "عداد", "charger": "شاحن"}

_lock = threading.Lock()
_cache = {}


def km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (0.5 - math.cos((lat2 - lat1) * p) / 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2)
    return 12742 * math.asin(math.sqrt(a))


# ---------------- الحدود الحقيقية: تجاور + point-in-polygon ----------------

def _polys():
    with open(GEO_FILE, encoding="utf-8") as f:
        feats = json.load(f)["features"]
    out = {}
    for ft in feats:
        g = ft["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        out[ft["properties"]["id"]] = polys
    return out


def _in_ring(lon, lat, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def gov_of_point(lat, lon):
    polys = _cache.get("polys")
    for code, ps in polys.items():
        for poly in ps:
            if _in_ring(lon, lat, poly[0]) and not any(_in_ring(lon, lat, h) for h in poly[1:]):
                return code
    # خارج كل الحدود (مثلاً نقطة على الحد) ← أقرب مركز محافظة
    return min(GOVS, key=lambda c: km(lat, lon, GOVS[c]["lat"], GOVS[c]["lon"]))


def _adjacency(polys, tol_km=3.0):
    verts = {c: [pt for poly in ps for ring in poly for pt in ring] for c, ps in polys.items()}
    codes = list(verts)
    adj = []
    for i, a in enumerate(codes):
        for b in codes[i + 1:]:
            best = min(math.hypot((p[0] - q[0]) * 96, (p[1] - q[1]) * 111)
                       for p in verts[a][::2] for q in verts[b][::2])
            if best < tol_km:
                adj.append((a, b))
    return adj


def _jitter(key, lat, lon, max_km=9):
    """موقع تقريبي ثابت (مش عشوائي كل مرة) لأصل ما إله إحداثيات بالداتا."""
    h = hashlib.md5(key.encode()).digest()
    ang = h[0] / 255 * 2 * math.pi
    r = (2 + h[1] / 255 * (max_km - 2)) / 111
    return lat + r * math.sin(ang), lon + r * math.cos(ang) / math.cos(lat * math.pi / 180)


# ---------------- بناء الغراف الأساسي ----------------

def base_graph():
    with _lock:
        if "G" in _cache:
            return _cache["G"]
        polys = _polys()
        _cache["polys"] = polys
        G = nx.Graph()
        for c, g in GOVS.items():
            G.add_node(c, type="gov", label=g["ar"], lat=g["lat"], lon=g["lon"], gov=c)
        for a, b in _adjacency(polys):
            G.add_edge(a, b, kind="interconnect",
                       weight=round(km(GOVS[a]["lat"], GOVS[a]["lon"], GOVS[b]["lat"], GOVS[b]["lon"]), 1))
        for pid, p in PLANTS.items():
            G.add_node(pid, type="plant", label=p["ar"], lat=p["lat"], lon=p["lon"], gov=p["gov"])
            # كل محطة بتغذي محافظتها + أقرب محافظتين غيرها (خطوط نقل)
            near = sorted(GOVS, key=lambda c: km(p["lat"], p["lon"], GOVS[c]["lat"], GOVS[c]["lon"]))
            feeds = [p["gov"]] + [c for c in near if c != p["gov"]][:2]
            for c in feeds:
                G.add_edge(pid, c, kind="supply",
                           weight=round(km(p["lat"], p["lon"], GOVS[c]["lat"], GOVS[c]["lon"]), 1))
        conn = get_conn()
        try:
            for r in conn.execute("SELECT ocm_id, title, town, lat, lon, max_power_kw FROM jordan_chargers "
                                  "WHERE lat IS NOT NULL AND lon IS NOT NULL"):
                gov = gov_of_point(r["lat"], r["lon"])
                nid = f"OCM-{r['ocm_id']}"
                G.add_node(nid, type="station", label=r["title"] or "محطة شحن", lat=r["lat"], lon=r["lon"],
                           gov=gov, kw=r["max_power_kw"])
                G.add_edge(nid, gov, kind="feeder",
                           weight=round(km(r["lat"], r["lon"], GOVS[gov]["lat"], GOVS[gov]["lon"]), 1))
        except Exception:  # noqa: BLE001
            pass
        conn.close()
        _cache["G"] = G
        _cache["bc"] = nx.betweenness_centrality(G, weight="weight", normalized=True)
        return G


# ---------------- تحديد الأصل المعطّل من الحادثة ----------------

def _incident_asset(incident, trace):
    text = f"{incident.get('description') or ''} {incident.get('title') or ''}"
    winner = incident.get("winner_agent")
    # شاحن مذكور بأدلة الوكيل الفائز (مثلاً JO-KRK-CH03) أدق من المنطقة
    ev_text = ""
    pinned = []   # الشاحن يلي الأدوات نفسها حددته (مش أي شاحن انذكر بالسياق)
    for e in trace:
        if e.get("event_type") != "tool_result":
            continue
        try:
            pl = json.loads(e.get("payload") or "{}") if isinstance(e.get("payload"), str) else (e.get("payload") or {})
        except ValueError:
            continue
        if winner and pl.get("agent") not in (winner, None):
            continue
        r = pl.get("result") or {}
        if r.get("flagged"):
            if r.get("best_matching_charger"):
                pinned.append(r["best_matching_charger"])
            for w in (r.get("worst_chargers") or [])[:1]:
                pinned.append(w.get("charger_id"))
            for w in (r.get("newly_active_chargers") or [])[:1]:
                pinned.append(w.get("charger_id"))
        ev_text += " " + json.dumps(r)
    conn = get_conn()
    try:
        u = re.search(r"\bunit\s*(\d+)", text, re.I)
        if u:
            row = conn.execute("SELECT unit_id, site FROM plant_units WHERE unit_id = ?", (int(u.group(1)),)).fetchone()
            if row:
                plant = next((k for k, p in PLANTS.items() if p["site_match"] in (row["site"] or "")), None)
                return {"id": f"UNIT-{row['unit_id']}", "type": "unit", "label": f"وحدة التوليد {row['unit_id']}",
                        "site": row["site"], "plant": plant}
        m = re.search(r"\b(JOM\d{6}|MAC\d{6})\b", text)
        charger_ids = [c for c in pinned if c] + re.findall(r"\b(JO-[A-Z]{3}-CH\d{2}|\d-\d-\d{2,3}-\d{2,4})\b", text)
        if winner == "Charging Agent" and charger_ids:
            cid = charger_ids[0]
            row = conn.execute("SELECT region FROM chargers WHERE charger_id = ?", (cid,)).fetchone()
            if row:
                return {"id": f"CHG-{cid}", "type": "charger", "label": f"الشاحن {cid}", "region": row["region"]}
        if m:
            row = conn.execute("SELECT region FROM meters WHERE meter_id = ?", (m.group(1),)).fetchone()
            if row:
                return {"id": f"MTR-{m.group(1)}", "type": "meter", "label": f"العداد {m.group(1)}",
                        "region": row["region"]}
        if charger_ids:
            row = conn.execute("SELECT region FROM chargers WHERE charger_id = ?", (charger_ids[0],)).fetchone()
            if row:
                return {"id": f"CHG-{charger_ids[0]}", "type": "charger", "label": f"الشاحن {charger_ids[0]}",
                        "region": row["region"]}
    finally:
        conn.close()
    if incident.get("region") in REGION_TO_GOV:
        return {"id": REGION_TO_GOV[incident["region"]], "type": "gov",
                "label": GOVS[REGION_TO_GOV[incident["region"]]]["ar"]}
    return None


# ---------------- التحليل ----------------

DOMAIN_WEIGHT = {"unit": 1.0, "plant": 1.0, "gov": 0.8, "station": 0.55, "charger": 0.55, "meter": 0.45}
# مدى الانتشار (كم على الغراف): عطل توليد بيوصل أبعد بكتير من عطل عداد منزلي
REACH_KM = {"unit": 320.0, "plant": 320.0, "gov": 180.0, "station": 110.0, "charger": 110.0, "meter": 80.0}


def analyze_incident(incident, trace):
    G0 = base_graph()
    bc0 = _cache["bc"]
    G = G0.copy()
    asset = _incident_asset(incident, trace)
    if not asset:
        return {"error": "ما قدرت أحدد موقع الخلل من الحادثة (ما في منطقة أو معرّف معروف)."}

    # نضيف الأصل المعطّل كعقدة حقيقية بالغراف (لو مش موجود)
    fid = asset["id"]
    if asset["type"] == "unit":
        pid = asset.get("plant") or "PLANT-AQABA"
        p = G.nodes[pid]
        G.add_node(fid, type="unit", label=asset["label"], lat=p["lat"] + 0.012, lon=p["lon"] + 0.012, gov=p["gov"])
        G.add_edge(fid, pid, kind="unit", weight=0.5)
    elif asset["type"] in ("meter", "charger"):
        gov = REGION_TO_GOV.get(asset.get("region"), "JO-AM")
        lat, lon = _jitter(fid, GOVS[gov]["lat"], GOVS[gov]["lon"])
        G.add_node(fid, type=asset["type"], label=asset["label"], lat=lat, lon=lon, gov=gov, approx=True)
        G.add_edge(fid, gov, kind="feeder", weight=round(km(lat, lon, GOVS[gov]["lat"], GOVS[gov]["lon"]), 1))
    f = G.nodes[fid]
    home = f["gov"]

    bc = nx.betweenness_centrality(G, weight="weight", normalized=True) if fid not in bc0 else bc0
    ranked = sorted(bc.items(), key=lambda kv: kv[1], reverse=True)
    rank_of = {n: i + 1 for i, (n, _) in enumerate(ranked)}

    # 1) shortest paths: انتشار الأثر من الأصل المعطّل لكل محافظة
    dist, paths = nx.single_source_dijkstra(G, fid, weight="weight")
    conf = incident.get("final_confidence")
    sev = (conf if isinstance(conf, (int, float)) and incident.get("status") == "resolved" else 0.4)
    sev = max(0.25, min(1.0, sev)) * DOMAIN_WEIGHT.get(asset["type"], 0.6)
    bc_max = max((bc.get(c, 0) for c in GOVS), default=1) or 1
    affected = []
    for c in GOVS:
        if c not in dist:
            continue
        d = dist[c]
        # الأثر بيتناقص أُسّياً مع المسافة على الغراف (مش المسافة الهوائية)، وبيزيد شوي
        # للمحافظات المركزية (betweenness عالي) لأنها بتنقل الحمل لغيرها
        impact = sev * math.exp(-d / REACH_KM.get(asset["type"], 120.0)) * (0.75 + 0.25 * bc.get(c, 0) / bc_max)
        # محافظة الخلل نفسها دايماً الأعلى تأثراً
        impact = sev if c == home else min(impact, sev * 0.95)
        impact = round(min(1.0, impact), 3)
        if impact >= 0.10 or c == home:
            path = paths[c]
            affected.append({
                "gov": c, "ar": GOVS[c]["ar"], "seat": GOVS[c]["seat"], "dist_km": round(d, 1),
                "hops": len(path) - 1, "impact": impact,
                "path": [{"id": n, "label": G.nodes[n].get("label"), "lat": G.nodes[n]["lat"], "lon": G.nodes[n]["lon"]}
                         for n in path],
            })
    affected.sort(key=lambda a: -a["impact"])

    # 2) connected_components: شو بينفصل لو الأصل تعطّل، ولو تصاعد لمحطة توزيع المحافظة
    def cut(nodes):
        H = G.copy()
        H.remove_nodes_from(nodes)
        comps = sorted(nx.connected_components(H), key=len, reverse=True)
        cut_off = [n for comp in comps[1:] for n in comp]
        by_type = {}
        for n in cut_off:
            t = H.nodes[n]["type"]
            by_type[TYPE_AR.get(t, t)] = by_type.get(TYPE_AR.get(t, t), 0) + 1
        return {"components": len(comps), "isolated": len(cut_off), "by_type": by_type,
                "isolated_nodes": [{"id": n, "label": H.nodes[n].get("label"), "lat": H.nodes[n]["lat"],
                                    "lon": H.nodes[n]["lon"], "type": H.nodes[n]["type"]} for n in cut_off[:300]]}
    direct = cut([fid])
    escalation_hub = home if fid != home else None
    escalation = cut([fid, home]) if escalation_hub else direct
    # أي محافظات بتصير أبعد (أو بتنقطع) عن مصادر التوليد لو سقطت محطة توزيع المحافظة
    H = G.copy()
    H.remove_nodes_from([fid, home] if escalation_hub else [fid])
    plant_reach = {}
    for c in GOVS:
        if c in H:
            ds = [nx.shortest_path_length(H, c, p, weight="weight") for p in PLANTS if p in H and nx.has_path(H, c, p)]
            plant_reach[c] = round(min(ds), 1) if ds else None
    base_reach = {c: round(min(nx.shortest_path_length(G0, c, p, weight="weight") for p in PLANTS), 1) for c in GOVS}
    rerouted = [{"gov": c, "ar": GOVS[c]["ar"], "before_km": base_reach[c], "after_km": plant_reach[c]}
                for c in plant_reach if plant_reach[c] is None or plant_reach[c] > base_reach[c] + 0.5]

    top = [{"id": n, "label": G.nodes[n].get("label"), "type": TYPE_AR.get(G.nodes[n]["type"]),
            "value": round(v, 4)} for n, v in ranked[:5]]

    return {
        "incident": {"id": incident.get("id"), "title": incident.get("title"), "status": incident.get("status"),
                     "winner": incident.get("winner_agent"), "confidence": incident.get("final_confidence")},
        "failure": {"id": fid, "type": asset["type"], "type_ar": TYPE_AR.get(asset["type"]), "label": f.get("label"),
                    "lat": f["lat"], "lon": f["lon"], "gov": home, "gov_ar": GOVS[home]["ar"],
                    "seat": GOVS[home]["seat"], "approx_location": bool(f.get("approx")),
                    "centrality": round(bc.get(fid, 0), 4), "centrality_rank": rank_of.get(fid),
                    "nodes_total": G.number_of_nodes()},
        "severity": round(sev, 3),
        "affected": affected,
        "components": {"direct": direct, "escalation": escalation,
                       "escalation_hub": GOVS[home]["ar"] if escalation_hub else None},
        "rerouted": rerouted,
        "centrality_top": top,
        "graph": {"nodes": G.number_of_nodes(), "edges": G.number_of_edges()},
        "method": ("networkx: single_source_dijkstra (انتشار الأثر) · connected_components (العزل) · "
                   "betweenness_centrality (الأهمية). الحدود والتجاور والمسافات حقيقية؛ الربط الشبكي نموذج تقريبي."),
    }


def network_layer():
    """الشبكة الأساسية للرسم: المحافظات، المحطات، محطات الشحن، والأضلاع بينها."""
    G = base_graph()
    bc = _cache["bc"]
    nodes = [{"id": n, "type": d["type"], "label": d.get("label"), "lat": d["lat"], "lon": d["lon"],
              "gov": d.get("gov"), "bc": round(bc.get(n, 0), 4)} for n, d in G.nodes(data=True)]
    edges = [{"a": a, "b": b, "kind": d.get("kind"), "km": d.get("weight")} for a, b, d in G.edges(data=True)]
    return {"nodes": nodes, "edges": edges, "govs": {c: {"ar": g["ar"], "seat": g["seat"]} for c, g in GOVS.items()}}
