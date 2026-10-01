"""
services/report_intake.py - استقبال تقارير PDF (زر "+" جنب خانة السيناريو اليدوي)

المسار:
1. نقرأ النص من ملف الـ PDF (pypdf) ونطبّعه (NFKC — بيرجّع أشكال الحروف العربية
   المعروضة لحروف عادية).
2. نداء واحد للموديل دوره "استخراج بس، مش تشخيص": شو المعرّفات (عداد/شاحن/وحدة)،
   المنطقة، التاريخ أو الدورة، والأرقام والأعراض المذكورة حرفياً بالتقرير.
   ممنوع يستنتج سبب — التشخيص شغل الوكلاء الثلاثة وجولة التحدي.
3. الكود (مش الموديل) بيبني نص الحادثة من الحقول المستخرجة، وبيسلّمه لنفس مسار
   التحقيق العادي. لو الاستخراج ناقص، بنكمّل بالموجود (الوكلاء بيحكوا "الدليل غير كافي").

يحتاج:  pip install pypdf
"""
import io
import json
import re
import unicodedata

from groq import Groq

import config

from db.regions import REGIONS
MAX_CHARS = 9000

EXTRACT_PROMPT = """You read a field/operations report about Jordan's power grid (mixed Arabic and English;
the Arabic text may have scrambled word order because it was extracted from a PDF).

Your ONLY job is to EXTRACT facts that are written in the report. Do NOT diagnose, do NOT guess
the cause, do NOT add any explanation that is not written in the report. Another system will
investigate the cause later.

Return ONLY a JSON object, no markdown, with exactly these keys (use null when not in the report):
{
  "title": "short English title of what was reported (symptom, not cause), max 10 words",
  "region": one of ["Amman","Zarqa","Irbid","Aqaba","Salt","Madaba","Karak","Mafraq","Jerash","Ajloun","Tafilah","Ma'an"] or null,
  "report_date": "YYYY-MM-DD" or null,
  "as_of_day": "YYYY-MM-DD the last day of the observation period" or null,
  "meter_ids": ["meter IDs exactly as written"],
  "charger_ids": ["charger IDs exactly as written"],
  "unit_id": integer plant/turbine unit number or null,
  "as_of_cycle": integer operating cycle number or null,
  "kwh_per_day_before": number or null,
  "kwh_per_day_after": number or null,
  "symptoms": ["each observed fact from the report as one short English sentence, with its numbers"]
}
"""


class ReportError(Exception):
    pass


def extract_text(pdf_bytes):
    try:
        from pypdf import PdfReader
    except ImportError as e:  # noqa: F841
        raise ReportError("مكتبة pypdf مش منزّلة. شغّلي: pip install pypdf")
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception as e:  # noqa: BLE001
        raise ReportError(f"ما قدرت أقرأ ملف الـ PDF: {e}")
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) < 40:
        raise ReportError("الملف ما فيه نص مقروء (ممكن يكون صورة ممسوحة ضوئياً)")
    return text[:MAX_CHARS]


def extract_fields(text):
    client = Groq(api_key=config.GROQ_API_KEY)
    resp = client.chat.completions.create(
        model=config.MODEL, temperature=0.0,
        messages=[{"role": "system", "content": EXTRACT_PROMPT},
                  {"role": "user", "content": "REPORT TEXT:\n" + text}],
    )
    raw = (resp.choices[0].message.content or "").strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ReportError("الموديل ما رجّع استخراج مفهوم من التقرير")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        raise ReportError("استخراج التقرير رجع بصيغة غلط")
    if data.get("region") not in REGIONS:
        data["region"] = None
    return data


def build_event(fields):
    """الكود بيبني نص الحادثة من الحقول — نفس الشكل يلي بيفهمه الوكلاء بالسيناريوهات الثابتة."""
    parts = ["Uploaded field report."]
    region = fields.get("region")
    meters = [m for m in (fields.get("meter_ids") or []) if m]
    before, after = fields.get("kwh_per_day_before"), fields.get("kwh_per_day_after")
    day = fields.get("as_of_day") or fields.get("report_date")

    for mid in meters[:2]:
        s = f"Meter {mid}" + (f" in {region} region" if region else "")
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            verb = "rose" if after > before else "dropped"
            s += (f" {verb} from {before:g} to {after:g} kWh/day"
                  f" (change of {after - before:+.1f} kWh/day)")
        parts.append(s + ".")
    for cid in (fields.get("charger_ids") or [])[:3]:
        parts.append(f"Charger {cid}" + (f" in {region} region" if region else "") + " is mentioned.")
    if fields.get("unit_id") is not None:
        parts.append(f"Plant unit {int(fields['unit_id'])} is involved.")
    if region and not meters and fields.get("unit_id") is None:
        parts.append(f"Region: {region}.")
    for s in (fields.get("symptoms") or [])[:8]:
        parts.append(str(s).strip().rstrip(".") + ".")
    if fields.get("as_of_cycle") is not None:
        parts.append(f"Investigate the unit as of cycle {int(fields['as_of_cycle'])}.")
    elif day:
        parts.append(f"Investigate as of {day}.")
    else:
        parts.append("Investigate the cause.")
    return " ".join(parts)


def process(pdf_bytes):
    text = extract_text(pdf_bytes)
    fields = extract_fields(text)
    return {"fields": fields, "event_text": build_event(fields),
            "title": "📄 " + (fields.get("title") or "تقرير مرفوع"), "region": fields.get("region")}
