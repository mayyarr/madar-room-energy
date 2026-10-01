"""scan_charging.py: يفتّش عن مناطق وتواريخ فيها إشارات شحن حقيقية"""
import pandas as pd
from tools import charging_tools as ct

df, chargers = ct._load()
regions = sorted(set(chargers["region"]))
days = pd.date_range("2020-01-09", df["day"].max(), freq="D")

rows = []
for reg in regions:
    for d in days:
        day = str(d.date())
        if d.weekday() >= 5:
            continue
        shift = ct.detect_charging_load_shift(reg, day)
        new = ct.detect_new_charger_activity(reg, day)
        fail = ct.compare_charger_failure_rate(reg, day)
        score = sum(r.get("strength", 0) for r in (shift, new, fail) if r.get("flagged"))
        if score > 0:
            rows.append((round(score, 2), reg, day, shift.get("ratio"), shift.get("z"),
                         [n["charger_id"] for n in new.get("newly_active_chargers", [])],
                         (fail.get("worst_chargers") or [{}])[0].get("charger_id") if fail.get("flagged") else None))

rows.sort(reverse=True)
print(f"{len(rows)} حالة فيها إشارة. أقوى 15:")
for r in rows[:15]:
    print(f"score={r[0]}  {r[1]:8s} {r[2]}  ratio={r[3]} z={r[4]}  new={r[5]}  failing={r[6]}")