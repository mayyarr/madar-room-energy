import os

# مفتاح Groq يُقرأ من متغير البيئة، لا نكتبه في الكود أبداً
GROQ_API_KEY = (os.environ.get("GROQ_API_KEY") or "").strip()

# الموديل الذي يشغّل الوكلاء
MODEL = "openai/gpt-oss-120b"
# اسم ملف قاعدة البيانات
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "madar.db")

# إذا كان الفرق بين ثقة فرضيتين أقل من 10% نعتبرها تعادل ونفتح نقاشاً
TIE_THRESHOLD = 0.10