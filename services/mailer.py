"""
services/mailer.py - إرسال تقرير القرار (PDF) بالإيميل عبر Gmail SMTP

الإعداد (مرة وحدة، متغيرات بيئة — ما بتنكتب بالكود أبداً):
    $env:MADAR_SMTP_USER="madar.alerts@gmail.com"     # الإيميل يلي بيطلع منه الإرسال
    $env:MADAR_SMTP_PASS="xxxx xxxx xxxx xxxx"        # App Password من حساب Google
اختياري:
    $env:MADAR_SMTP_HOST="smtp.gmail.com"   $env:MADAR_SMTP_PORT="465"
"""
import os
import re
import smtplib
import ssl
from email.message import EmailMessage

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_RECIPIENTS = 5


class MailError(Exception):
    pass


def configured():
    return bool(os.environ.get("MADAR_SMTP_USER") and os.environ.get("MADAR_SMTP_PASS"))


def send_report(recipients, subject, html_body, text_body, pdf_bytes, filename):
    # App Password من Google بينسخ ومعه مسافات "غير عادية" (\xa0) بين المجموعات — منشيل كل
    # أنواع المسافات، وإلا smtplib بيفشل بـ 'ascii codec can't encode character'
    user = re.sub(r"\s+", "", (os.environ.get("MADAR_SMTP_USER") or "").replace("\xa0", ""))
    pwd = re.sub(r"\s+", "", (os.environ.get("MADAR_SMTP_PASS") or "").replace("\xa0", ""))
    if not user or not pwd:
        raise MailError("الإرسال مش مفعّل: لازم تحطي MADAR_SMTP_USER و MADAR_SMTP_PASS كمتغيرات بيئة وتعيدي تشغيل السيرفر.")
    recipients = [r.replace("\xa0", "").strip() for r in recipients if r and r.strip()]
    if not recipients:
        raise MailError("اكتبي إيميل واحد على الأقل")
    if len(recipients) > MAX_RECIPIENTS:
        raise MailError(f"أقصى عدد مستلمين {MAX_RECIPIENTS}")
    bad = [r for r in recipients if not EMAIL_RE.match(r)]
    if bad:
        raise MailError("إيميل غير صحيح: " + ", ".join(bad))

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"Madar Control Room <{user}>"
    msg["To"] = ", ".join(recipients)
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=filename)

    host = os.environ.get("MADAR_SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("MADAR_SMTP_PORT", "465"))
    try:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as s:
            s.login(user, pwd)
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise MailError("Gmail رفض تسجيل الدخول — تأكدي إنك مستعملة App Password مش كلمة سر الحساب العادية.")
    except Exception as e:  # noqa: BLE001
        raise MailError(f"فشل الإرسال: {e}")
    return recipients
