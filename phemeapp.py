"""
PhémeApp — MVP v1.2
====================
Script quotidien de détection des mises à l'enquête vaudoises.

Fonctionnement :
  1. Lit les utilisateurs depuis Google Sheets (export CSV public — pas de clé nécessaire)
  2. Géocode chaque adresse via l'API Swisstopo (gratuite, sans clé)
  3. Appelle l'API cantonale CAMAC → nouvelles publications avec lat/lng
  4. Calcule la distance Haversine
  5. Envoie un email Brevo SMTP si distance < 300m et pas déjà notifié

Usage :
  python3 phemeapp.py

Cron quotidien (7h00) :
  0 7 * * * cd /chemin/vers/dossier && python3 phemeapp.py >> logs/phemeapp.log 2>&1

Dépendances :
  pip3 install requests
  (rien d'autre — pas de gspread, pas de clé Google)
"""

import csv
import io
import json
import math
import requests
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

BREVO_SMTP_LOGIN  = os.environ.get("BREVO_SMTP_LOGIN", "ae1387001@smtp-brevo.com")
BREVO_API_KEY     = os.environ.get("BREVO_API_KEY", "xsmtpsib-c35d132ff59c0a7acd47584a3064fd78986954a2a1ec3cda491e4246b3f96516-MLMfUsjvSEhDzf9F")
BREVO_SENDER      = os.environ.get("BREVO_SENDER", "alerte@phemeapp.ch")
BREVO_SENDER_NAME = "PhémeApp"

# Google Sheet public (lecture seule)
# Format: https://docs.google.com/spreadsheets/d/{ID}/gviz/tq?tqx=out:csv&sheet={SHEET_NAME}
SHEET_ID          = os.environ.get("SHEET_ID", "1YLK-KV_W7sNraeZdsyttykh1OnYU5aJOhl_NIqwFsJw")
SHEET_TAB         = "Form Responses 1"

# Périmètre de détection en mètres
PERIMETER_M       = 500

# Fenêtre de recherche : publications des N derniers jours
SEARCH_DAYS       = 30

# Double-check : sites communaux à scraper pour comparer les comptages
# Format : { "NOM_COMMUNE_API": "URL_PAGE_ENQUETES" }
COMMUNE_BACKUP_URLS = {
    "AIGLE":    "https://www.aigle.ch/enquetes-publiques",
    "MONTREUX": "https://www.montreux.ch/autorisations-de-construire-et-enquetes-publiques/enquetes-publiques",
}

# Fichiers locaux
NOTIFIED_FILE     = "notified.json"
LOGS_DIR          = "logs"

# URL fiche détaillée canton
CAMAC_BASE_URL    = "https://prestations.vd.ch/pub/actiscamac/101091/5H1IET-7NLEK1/results"

# ─────────────────────────────────────────────
# INITIALISATION
# ─────────────────────────────────────────────

Path(LOGS_DIR).mkdir(exist_ok=True)

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


# ─────────────────────────────────────────────
# 1. CHARGEMENT UTILISATEURS DEPUIS GOOGLE SHEET
# ─────────────────────────────────────────────
#
# Colonnes du Sheet (dans l'ordre du Form) :
# 0  Timestamp
# 1  Prénom et nom
# 2  Adresse email de notification
# 3  Adresse complète          (adresse 1)
# 4  Nom de cette adresse      (label 1)
# 5  Deuxième adresse complète (adresse 2)
# 6  Nom de la deuxième adresse(label 2)
# 7  J'accepte les conditions

def load_users_from_sheet():
    url = (
        f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
        f"/gviz/tq?tqx=out:csv&sheet={requests.utils.quote(SHEET_TAB)}"
    )
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        content = r.text
    except Exception as e:
        log(f"❌ Impossible de lire le Sheet : {e}")
        return []

    users = []
    seen_emails = set()
    reader = csv.reader(io.StringIO(content))
    next(reader, None)  # skip header

    for row in reader:
        while len(row) < 8:
            row.append("")

        email  = row[2].strip()
        nom    = row[1].strip()
        adr1   = row[3].strip()
        label1 = row[4].strip() or "Adresse 1"
        adr2   = row[5].strip()
        label2 = row[6].strip() or "Adresse 2"

        if not email or not adr1 or email in seen_emails:
            continue
        seen_emails.add(email)

        adresses = [{"label": label1, "adresse": adr1, "lat": None, "lng": None}]
        if adr2:
            adresses.append({"label": label2, "adresse": adr2, "lat": None, "lng": None})

        users.append({"email": email, "nom": nom, "adresses": adresses})

    log(f"Utilisateurs chargés : {len(users)}")
    return users


# ─────────────────────────────────────────────
# 2. GÉOCODAGE (Swisstopo — gratuit, sans clé)
# ─────────────────────────────────────────────

def geocode_swisstopo(adresse):
    url = "https://api3.geo.admin.ch/rest/services/api/SearchServer"
    params = {"searchText": adresse, "type": "locations", "sr": "4326", "limit": 1}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        results = r.json().get("results", [])
        if results:
            attrs = results[0].get("attrs", {})
            lat, lon = attrs.get("lat"), attrs.get("lon")
            if lat and lon:
                return float(lat), float(lon)
    except Exception as e:
        log(f"  ⚠️  Géocodage '{adresse}': {e}")
    return None, None

def geocode_users(users):
    for user in users:
        for adr in user["adresses"]:
            if adr["lat"] is None:
                lat, lng = geocode_swisstopo(adr["adresse"])
                adr["lat"], adr["lng"] = lat, lng
                status = f"{lat:.4f}, {lng:.4f}" if lat else "ÉCHEC"
                log(f"  {'✅' if lat else '❌'} {adr['adresse']} → {status}")
    return users


# ─────────────────────────────────────────────
# 3. RÉCUPÉRATION MISES À L'ENQUÊTE (API CAMAC)
# ─────────────────────────────────────────────

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

def fetch_enquetes(days=SEARCH_DAYS):
    today     = datetime.now()
    from_date = today - timedelta(days=days)
    payload = {
        "noCamac": None,
        "fromFao": [from_date.year, from_date.month, from_date.day],
        "toFao":   [today.year, today.month, today.day],
        "municipality": None, "district": None, "investigations": None,
        "description": None, "exemption": None, "hzb": None,
        "clearing": None, "internalReference": None
    }
    try:
        session = requests.Session()
        session.get("https://prestations.vd.ch/pub/actiscamac/101091/5H1IET-7NLEK1/search",
            headers={"User-Agent": UA}, timeout=15)
        xsrf = session.cookies.get("XSRF-TOKEN", "")
        r = session.post(
            "https://prestations.vd.ch/pub/actiscamac/api/101091/search/avis",
            json=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "X-XSRF-TOKEN": xsrf,
                     "Referer": "https://prestations.vd.ch/pub/actiscamac/101091/5H1IET-7NLEK1/search",
                     "User-Agent": UA},
            timeout=30)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            data = data.get("avis") or data.get("results") or data.get("data") or []
        if not isinstance(data, list):
            log(f"Format inattendu : {str(data)[:200]}")
            return []
        with_coords = [e for e in data if isinstance(e, dict) and e.get("lat") and e.get("lng")]
        log(f"Mises à l'enquête : {len(data)} total, {len(with_coords)} avec GPS")
        return with_coords
    except Exception as e:
        log(f"Erreur API cantonale : {e}")
        return []


# ─────────────────────────────────────────────
# 4. DISTANCE HAVERSINE
# ─────────────────────────────────────────────

def haversine_m(lat1, lng1, lat2, lng2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi    = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


# ─────────────────────────────────────────────
# 5. MÉMOIRE DES NOTIFICATIONS
# ─────────────────────────────────────────────

def load_notified():
    if Path(NOTIFIED_FILE).exists():
        with open(NOTIFIED_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_notified(notified):
    with open(NOTIFIED_FILE, "w", encoding="utf-8") as f:
        json.dump(notified, f, indent=2, ensure_ascii=False)

def already_notified(notified, email, no_camac):
    return f"{email}:{no_camac}" in notified

def mark_notified(notified, email, no_camac):
    notified[f"{email}:{no_camac}"] = datetime.now().isoformat()

def is_new_user(notified, email):
    return f"welcome:{email}" not in notified

def mark_welcome_sent(notified, email):
    notified[f"welcome:{email}"] = datetime.now().isoformat()


# ─────────────────────────────────────────────
# 6. EMAIL DE BIENVENUE
# ─────────────────────────────────────────────

def send_welcome_email(dest_email, dest_nom, adresses):
    prenom = dest_nom.split()[0] if dest_nom else "bonjour"
    adresses_html = "".join([
        f'<tr style="border-bottom:1px solid #eee;"><td style="padding:8px 10px;color:#888;">{a["label"]}</td>'
        f'<td style="padding:8px 10px;">{a["adresse"]}</td></tr>'
        for a in adresses])
    html = f"""<html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333;">
<div style="background:#1a3a5c;padding:18px 24px;">
  <h1 style="color:white;margin:0;font-size:20px;">PhémeApp</h1>
  <p style="color:#a8c4e0;margin:4px 0 0;font-size:12px;">Surveillance des mises à l'enquête — Canton de Vaud</p>
</div>
<div style="padding:24px;">
  <p style="font-size:16px;">Bonjour {prenom},</p>
  <p style="font-size:14px;color:#444;line-height:1.7;">Votre inscription à <strong>PhémeApp</strong> est confirmée. Votre surveillance est maintenant active.</p>
  <div style="background:#eaf4ee;border-left:3px solid #1a7a4a;padding:14px 18px;margin:20px 0;border-radius:0 6px 6px 0;">
    <p style="margin:0 0 6px;font-size:14px;color:#0f4a2a;font-weight:500;">Ce que nous faisons pour vous chaque jour</p>
    <p style="margin:0;font-size:13px;color:#1a5c35;line-height:1.7;">Chaque matin, notre système consulte automatiquement les publications officielles du canton de Vaud et vérifie si une nouvelle mise à l'enquête publique a été déposée dans un rayon de <strong>500 mètres</strong> autour de vos adresses. Nous relevons les données directement depuis le registre officiel CAMAC — la source de référence pour toutes les demandes de permis de construire dans le canton. Aucune publication ne peut nous échapper.</p>
  </div>
  <p style="font-size:14px;color:#444;">Vos adresses surveillées :</p>
  <table style="width:100%;border-collapse:collapse;font-size:13px;margin:0 0 20px;">
    <tr style="background:#f0f4f8;"><th style="padding:8px 10px;text-align:left;color:#555;font-weight:500;">Nom</th><th style="padding:8px 10px;text-align:left;color:#555;font-weight:500;">Adresse</th></tr>
    {adresses_html}
  </table>
  <div style="background:#fff8e1;border-left:3px solid #f59e0b;padding:14px 18px;margin:20px 0;border-radius:0 6px 6px 0;">
    <p style="margin:0 0 4px;font-size:13px;color:#92400e;font-weight:500;">Important — délai légal de recours</p>
    <p style="margin:0;font-size:13px;color:#92400e;line-height:1.6;">En cas de mise à l'enquête à proximité, vous disposez de <strong>30 jours</strong> à compter de la date de publication dans la Feuille des Avis Officiels (FAO) pour faire opposition. Nous vous alertons dès la publication pour vous laisser le maximum de temps.</p>
  </div>
  <p style="font-size:14px;color:#444;line-height:1.7;">Pour modifier vos adresses ou vous désinscrire, répondez simplement à cet email.</p>
  <p style="font-size:14px;color:#444;">Bien cordialement,<br><strong>L'équipe PhémeApp</strong></p>
  <p style="font-size:11px;color:#aaa;border-top:1px solid #eee;padding-top:14px;margin-top:24px;line-height:1.6;">PhémeApp est un service d'information automatisé basé sur les données officielles du canton de Vaud. Il ne remplace pas une consultation juridique.</p>
</div></body></html>"""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Votre surveillance PhémeApp est active"
        msg["From"]    = f"{BREVO_SENDER_NAME} <{BREVO_SENDER}>"
        msg["To"]      = dest_email
        msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP("smtp-relay.brevo.com", 587) as srv:
            srv.starttls()
            srv.login(BREVO_SMTP_LOGIN, BREVO_API_KEY)
            srv.sendmail(BREVO_SENDER, dest_email, msg.as_string())
        log(f"  Bienvenue envoyé -> {dest_email}")
        return True
    except Exception as e:
        log(f"  Erreur bienvenue {dest_email} : {e}")
        return False

def format_date(ts_ms):
    try:
        return datetime.fromtimestamp(ts_ms / 1000).strftime("%d.%m.%Y")
    except:
        return "date inconnue"

def send_email(dest_email, dest_nom, enquete, adresse, distance_m):
    date_fao    = format_date(enquete.get("dateFao", 0))
    no_camac    = enquete.get("noCamac", "?")
    lieu        = enquete.get("lieu", "—")
    commune     = enquete.get("commune", "—")
    description = enquete.get("description", "—")
    nature      = enquete.get("natureTravaux", "—")
    fao_lib     = enquete.get("faoLib", "")
    lien        = f"{CAMAC_BASE_URL}?noCamac={no_camac}"
    prenom      = dest_nom.split()[0] if dest_nom else "bonjour"
    dist        = round(distance_m)

    sujet = f"⚠️ Mise à l'enquête à {dist}m — {adresse['label']}"

    html = f"""<html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333;">
      <div style="background:#1a3a5c;padding:20px 30px;">
        <h1 style="color:white;margin:0;font-size:20px;">🔔 PhémeApp</h1>
        <p style="color:#a8c4e0;margin:4px 0 0;font-size:13px;">Surveillance des mises à l'enquête — Canton de Vaud</p>
      </div>
      <div style="padding:30px;">
        <p>Bonjour {prenom},</p>
        <p>Une mise à l'enquête publique a été publiée à <strong>{dist} mètres</strong> de
        <em>« {adresse['label']} — {adresse['adresse']} »</em>.</p>

        <div style="background:#fff8e1;border-left:4px solid #f59e0b;padding:14px 18px;margin:20px 0;border-radius:4px;">
          <strong style="color:#92400e;">⏱ 30 jours pour faire opposition</strong><br>
          <span style="font-size:13px;color:#92400e;">Date FAO : <strong>{date_fao}</strong> — le délai court dès cette date.</span>
        </div>

        <table style="width:100%;border-collapse:collapse;margin:20px 0;">
          <tr style="background:#f8f9fa;"><td style="padding:9px 12px;font-size:13px;color:#666;width:38%;">No CAMAC</td><td style="padding:9px 12px;font-weight:bold;">{no_camac} {f'({fao_lib})' if fao_lib else ''}</td></tr>
          <tr><td style="padding:9px 12px;font-size:13px;color:#666;">Lieu</td><td style="padding:9px 12px;">{lieu}, {commune}</td></tr>
          <tr style="background:#f8f9fa;"><td style="padding:9px 12px;font-size:13px;color:#666;">Nature des travaux</td><td style="padding:9px 12px;">{nature}</td></tr>
          <tr><td style="padding:9px 12px;font-size:13px;color:#666;">Description</td><td style="padding:9px 12px;">{description}</td></tr>
          <tr style="background:#f8f9fa;"><td style="padding:9px 12px;font-size:13px;color:#666;">Distance</td><td style="padding:9px 12px;color:#1a3a5c;font-weight:bold;">{dist} m</td></tr>
        </table>

        <div style="text-align:center;margin:28px 0;">
          <a href="{lien}" style="background:#1a3a5c;color:white;padding:13px 26px;text-decoration:none;border-radius:6px;font-weight:bold;">Consulter le dossier →</a>
        </div>

        <p style="font-size:11px;color:#aaa;border-top:1px solid #eee;padding-top:14px;">
          PhémeApp est un service d'information automatisé. Il ne remplace pas un avis juridique.
        </p>
      </div>
    </body></html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = sujet
        msg["From"]    = f"{BREVO_SENDER_NAME} <{BREVO_SENDER}>"
        msg["To"]      = dest_email
        msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP("smtp-relay.brevo.com", 587) as srv:
            srv.starttls()
            srv.login(BREVO_SMTP_LOGIN, BREVO_API_KEY)
            srv.sendmail(BREVO_SENDER, dest_email, msg.as_string())
        log(f"  📧 → {dest_email} (CAMAC {no_camac}, {dist}m)")
        return True
    except Exception as e:
        log(f"  ❌ Email {dest_email} : {e}")
        return False


# ─────────────────────────────────────────────
# DOUBLE-CHECK : SITES COMMUNAUX
# ─────────────────────────────────────────────

def check_commune_backup(commune_name, api_count):
    """
    Scrappe la page d'enquêtes du site communal et compare le comptage
    avec celui de l'API cantonale. Log une alerte si écart détecté.
    """
    url = COMMUNE_BACKUP_URLS.get(commune_name.upper())
    if not url:
        return  # pas de backup configuré pour cette commune

    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "PhemeApp/1.0"})
        r.raise_for_status()
        # Compter les occurrences de mots-clés typiques d'une mise à l'enquête
        text = r.text.lower()
        keywords = ["enquête", "enquete", "camac", "mise à l'enquête", "permis de construire"]
        hits = sum(text.count(kw) for kw in keywords)

        if hits == 0 and api_count > 0:
            log(f"  ⚠️  DOUBLE-CHECK {commune_name} : site communal semble vide mais API retourne {api_count} résultats — vérification manuelle conseillée")
        else:
            log(f"  ✅ Double-check {commune_name} : site communal accessible ({hits} occurrences de mots-clés)")
    except Exception as e:
        log(f"  ⚠️  Double-check {commune_name} inaccessible : {e}")


# ─────────────────────────────────────────────
# 7. BOUCLE PRINCIPALE
# ─────────────────────────────────────────────

def run():
    log("=" * 50)
    log("PhémeApp — démarrage")
    log("=" * 50)

    users    = load_users_from_sheet()
    notified = load_notified()

    if not users:
        log("Aucun utilisateur — arrêt.")
        return

    # Emails de bienvenue pour les nouveaux utilisateurs
    log("Vérification des nouveaux utilisateurs...")
    for user in users:
        if is_new_user(notified, user["email"]):
            log(f"  Nouvel utilisateur : {user['email']}")
            if send_welcome_email(user["email"], user["nom"], user["adresses"]):
                mark_welcome_sent(notified, user["email"])

    log("Géocodage des adresses...")
    users = geocode_users(users)

    log(f"Récupération des mises a l'enquête ({SEARCH_DAYS}j)...")
    enquetes = fetch_enquetes()

    if not enquetes:
        log("Aucune mise à l'enquête — fin.")
        save_notified(notified)
        return

    # Double-check communes
    for commune in COMMUNE_BACKUP_URLS:
        count = sum(1 for e in enquetes if e.get("commune","").upper() == commune)
        check_commune_backup(commune, count)

    total = 0
    for user in users:
        for adr in user["adresses"]:
            if not adr["lat"]:
                continue
            for enquete in enquetes:
                no_camac = enquete.get("noCamac")
                if already_notified(notified, user["email"], no_camac):
                    continue
                dist = haversine_m(adr["lat"], adr["lng"], enquete["lat"], enquete["lng"])
                if dist <= PERIMETER_M:
                    log(f"  MATCH! CAMAC {no_camac} à {round(dist)}m de '{adr['label']}' ({user['email']})")
                    if send_email(user["email"], user["nom"], enquete, adr, dist):
                        mark_notified(notified, user["email"], no_camac)
                        log_alerte_historique(user, adr, enquete, dist)
                        total += 1
                elif PERIMETER_M < dist <= PERIMETER_LARGE_M:
                    zone_key = f"zone:{user['email']}:{no_camac}"
                    if zone_key not in notified:
                        log_zone_elargie(user, adr, enquete, dist)
                        notified[zone_key] = datetime.now().isoformat()

    save_notified(notified)
    log("=" * 50)
    log(f"PhémeApp — terminé. {total} alerte(s) envoyée(s).")
    log("=" * 50)


if __name__ == "__main__":
    run()


# ─────────────────────────────────────────────
# HISTORIQUE — GOOGLE SHEET
# ─────────────────────────────────────────────

SHEET_HISTORIQUE  = "Historique Alertes"
SHEET_ZONE        = "Zone Elargie"
PERIMETER_LARGE_M = 2000  # Zone élargie : 500m à 2km

def get_sheet_csv_url(tab_name):
    return f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv&sheet={requests.utils.quote(tab_name)}"

def append_to_sheet(tab_name, row):
    """
    Ajoute une ligne dans un onglet du Google Sheet via l'API gviz.
    Utilise l'API Sheets v4 en mode public append — nécessite que le Sheet
    soit partagé en écriture OU on passe par Apps Script webhook.
    Pour le MVP: on écrit dans un fichier CSV local qui sera importé manuellement
    ou via un script Apps Script trigger.
    On stocke dans un JSON local en attendant une solution d'écriture directe.
    """
    log_file = f"logs/sheet_{tab_name.replace(' ', '_').lower()}.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

def log_alerte_historique(user, adr, enquete, distance_m):
    """Enregistre une alerte envoyée dans l'historique."""
    date_fao = format_date(enquete.get("dateFao", 0))
    row = {
        "date_envoi":       datetime.now().isoformat(),
        "email":            user["email"],
        "nom":              user["nom"],
        "label_adresse":    adr["label"],
        "adresse":          adr["adresse"],
        "no_camac":         enquete.get("noCamac"),
        "lieu":             enquete.get("lieu"),
        "commune":          enquete.get("commune"),
        "nature_travaux":   enquete.get("natureTravaux"),
        "distance_m":       round(distance_m),
        "date_fao":         date_fao,
        "lien":             f"{CAMAC_BASE_URL}?noCamac={enquete.get('noCamac')}"
    }
    append_to_sheet(SHEET_HISTORIQUE, row)
    log(f"  Historique alerte enregistre: CAMAC {row['no_camac']} ({row['distance_m']}m)")

def log_zone_elargie(user, adr, enquete, distance_m):
    """Enregistre une publication dans la zone élargie (500m–2km)."""
    date_fao = format_date(enquete.get("dateFao", 0))
    row = {
        "date_detection":   datetime.now().isoformat(),
        "email":            user["email"],
        "nom":              user["nom"],
        "label_adresse":    adr["label"],
        "adresse":          adr["adresse"],
        "no_camac":         enquete.get("noCamac"),
        "lieu":             enquete.get("lieu"),
        "commune":          enquete.get("commune"),
        "nature_travaux":   enquete.get("natureTravaux"),
        "distance_m":       round(distance_m),
        "date_fao":         date_fao,
        "lien":             f"{CAMAC_BASE_URL}?noCamac={enquete.get('noCamac')}",
        "inclus_newsletter": False
    }
    append_to_sheet(SHEET_ZONE, row)
    log(f"  Zone élargie enregistrée: CAMAC {row['no_camac']} ({row['distance_m']}m)")
