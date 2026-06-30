"""Async email jo'natish — aiosmtplib orqali.

Lokalda test uchun MailHog (Docker Compose ichida) ishlatiladi:
  SMTP_HOST=mailhog, SMTP_PORT=1025

Productionda real SMTP (Gmail, Sendgrid, Mailgun va h.k.):
  SMTP_HOST=smtp.gmail.com, SMTP_PORT=587, SMTP_TLS=true

Xavfsizlik:
  - SMTP hisob ma'lumotlari .env da saqlanadi.
  - Email yuborishdagi xatolar exception tashlamaydi — faqat log'lanadi.
    (Asosiy so'rovni bloklamamasin uchun background task sifatida ishlatiladi.)
"""

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


async def _send_email(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str | None = None,
) -> bool:
    """Asosiy email jo'natish funksiyasi.

    Qaytaradi: True — muvaffaqiyatli, False — xato.
    Exception tashlamaydi.
    """
    if not settings.email_enabled:
        logger.info("[email] Email o'chirilgan, jo'natilmadi: %s", to_email)
        return False

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    message["To"] = to_email

    if text_body:
        message.attach(MIMEText(text_body, "plain", "utf-8"))
    message.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        smtp = aiosmtplib.SMTP(
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            use_tls=settings.smtp_ssl,
            start_tls=settings.smtp_tls,
            timeout=10,
        )
        await smtp.connect()
        if settings.smtp_user and settings.smtp_password:
            await smtp.login(settings.smtp_user, settings.smtp_password)
        await smtp.send_message(message)
        await smtp.quit()
        logger.info("[email] Jo'natildi: %s -> %s", subject, to_email)
        return True
    except Exception as exc:
        logger.error("[email] Xato: %s -> %s: %s", subject, to_email, exc)
        return False


async def send_verification_email(to_email: str, token: str) -> bool:
    """Email tasdiqlash xatini yuboradi.

    Token — URL'ga qo'shiladi: /auth/verify-email?token=<token>
    """
    verify_url = f"{settings.base_url}/api/v1/auth/verify-email?token={token}"

    subject = "Email manzilingizni tasdiqlang"
    html_body = f"""
<!DOCTYPE html>
<html lang="uz">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Email tasdiqlash</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
             background:#f5f5f5; margin:0; padding:40px 0;">
  <div style="max-width:520px; margin:0 auto; background:#fff;
              border-radius:12px; overflow:hidden;
              box-shadow:0 4px 24px rgba(0,0,0,0.08);">
    <!-- Header -->
    <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding:36px 40px; text-align:center;">
      <h1 style="color:#fff; margin:0; font-size:24px; font-weight:700;">
        🔗 URL Shortener
      </h1>
      <p style="color:rgba(255,255,255,0.85); margin:8px 0 0; font-size:14px;">
        Email manzilingizni tasdiqlang
      </p>
    </div>
    <!-- Body -->
    <div style="padding:36px 40px;">
      <p style="color:#374151; font-size:16px; line-height:1.6; margin:0 0 24px;">
        Salom! Ro'yxatdan o'tganingiz uchun rahmat. Hisobingizni faollashtirish
        uchun quyidagi tugmani bosing:
      </p>
      <div style="text-align:center; margin:32px 0;">
        <a href="{verify_url}"
           style="display:inline-block; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                  color:#fff; text-decoration:none; padding:14px 32px;
                  border-radius:8px; font-size:16px; font-weight:600;
                  letter-spacing:0.3px;">
          ✅ Email ni tasdiqlash
        </a>
      </div>
      <p style="color:#6b7280; font-size:13px; line-height:1.5; margin:24px 0 0;">
        Agar tugma ishlamasa, ushbu havolani brauzeringizga nusxalang:<br>
        <a href="{verify_url}" style="color:#667eea; word-break:break-all;">
          {verify_url}
        </a>
      </p>
      <p style="color:#9ca3af; font-size:12px; margin:20px 0 0; padding-top:16px;
                border-top:1px solid #f3f4f6;">
        Agar siz ro'yxatdan o'tmagan bo'lsangiz, bu xatni e'tiborsiz qoldiring.
        Havola 24 soat davomida amal qiladi.
      </p>
    </div>
  </div>
</body>
</html>
"""
    text_body = (
        f"Email manzilingizni tasdiqlash uchun ushbu havolaga o'ting:\n{verify_url}\n\n"
        "Agar siz ro'yxatdan o'tmagan bo'lsangiz, bu xatni e'tiborsiz qoldiring."
    )
    return await _send_email(to_email, subject, html_body, text_body)
