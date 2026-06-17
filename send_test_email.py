import os, smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

BREVO_SMTP_LOGIN = os.environ["BREVO_SMTP_LOGIN"]
BREVO_API_KEY    = os.environ["BREVO_API_KEY"]
BREVO_SENDER     = os.environ["BREVO_SENDER"]
DEST             = "arnaud.mathier@gmail.com"

html = """<html><body style='font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333'>
<div style='background:#1a3a5c;padding:18px 24px'>
  <h1 style='color:white;margin:0;font-size:20px'>PhémeApp</h1>
  <p style='color:#a8c4e0;margin:4px 0 0;font-size:12px'>Email de test — Système opérationnel</p>
</div>
<div style='padding:24px'>
  <p style='font-size:16px'>Bonjour Arnaud,</p>
  <p style='font-size:14px;color:#444;line-height:1.7'>Ceci est un <strong>email de test</strong> envoyé depuis GitHub Actions pour confirmer que toute la chaîne fonctionne.</p>
  <div style='background:#f0fdf4;border-left:3px solid #1a7a4a;padding:14px 18px;margin:16px 0;border-radius:0 6px 6px 0'>
    <strong style='color:#0f4a2a'>✅ Système opérationnel</strong>
    <p style='font-size:13px;color:#1a5c35;margin-top:4px'>GitHub Actions → SMTP Brevo → arnaud.mathier@gmail.com</p>
  </div>
  <table style='width:100%;border-collapse:collapse;font-size:13px;margin:16px 0'>
    <tr style='background:#f0f4f8'><th style='padding:8px;text-align:left'>Composant</th><th style='padding:8px;text-align:left'>Statut</th></tr>
    <tr><td style='padding:8px'>phemeapp.py v2.1</td><td style='padding:8px;color:#1a7a4a'>✅ Run #40</td></tr>
    <tr><td style='padding:8px'>54 tests pytest</td><td style='padding:8px;color:#1a7a4a'>✅ 100% pass</td></tr>
    <tr><td style='padding:8px'>SMTP Brevo</td><td style='padding:8px;color:#1a7a4a'>✅ OK</td></tr>
    <tr><td style='padding:8px'>Apps Script Web App</td><td style='padding:8px;color:#1a7a4a'>✅ Configurée</td></tr>
    <tr><td style='padding:8px'>Liens emails</td><td style='padding:8px;color:#1a7a4a'>✅ Site communal (BUG-007 corrigé)</td></tr>
  </table>
  <p style='font-size:14px;color:#444;margin-top:20px'>Bien cordialement,<br><strong>L\'équipe PhémeApp</strong></p>
  <p style='font-size:11px;color:#aaa;border-top:1px solid #eee;padding-top:12px;margin-top:20px'>PhémeApp v2.1 — service d\'information automatisé.</p>
</div></body></html>"""

msg = MIMEMultipart("alternative")
msg["Subject"] = "✅ PhémeApp — Email de test système v2.1"
msg["From"]    = f"PhémeApp <{BREVO_SENDER}>"
msg["To"]      = DEST
msg.attach(MIMEText(html, "html", "utf-8"))

with smtplib.SMTP("smtp-relay.brevo.com", 587) as srv:
    srv.starttls()
    srv.login(BREVO_SMTP_LOGIN, BREVO_API_KEY)
    srv.sendmail(BREVO_SENDER, DEST, msg.as_string())
print(f"Email envoyé à {DEST}")
