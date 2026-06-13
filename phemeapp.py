"""
PhémeApp — v2.1
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

import base64
import html as _html
import csv
import io
import json
import math
import os
import requests
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote as url_quote

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

BREVO_SMTP_LOGIN  = os.environ.get("BREVO_SMTP_LOGIN", "")
BREVO_API_KEY     = os.environ.get("BREVO_API_KEY", "")
BREVO_SENDER      = os.environ.get("BREVO_SENDER", "alerte@phemeapp.ch")
BREVO_SENDER_NAME = "PhémeApp"

# Google Sheet public (lecture seule)
# Format: https://docs.google.com/spreadsheets/d/{ID}/gviz/tq?tqx=out:csv&sheet={SHEET_NAME}
SHEET_ID          = os.environ.get("SHEET_ID", "")
# SEC-01: vérification que les secrets critiques sont définis
def esc(value):
    """SEC-03: Échappe les données externes avant insertion dans le HTML."""
    if value is None:
        return ""
    return _html.escape(str(value), quote=True)


def is_valid_email(email):
    """SEC-02: Validation format email avant tout traitement."""
    import re as _re
    if not email or not isinstance(email, str):
        return False
    return bool(_re.match(r'^[\w.+\-]+@[\w\-]+\.[\w.]+$', email.strip()))


def _check_required_secrets():
    missing = []
    for var in ["BREVO_API_KEY", "BREVO_SMTP_LOGIN", "BREVO_SENDER", "SHEET_ID"]:
        if not os.environ.get(var):
            missing.append(var)
    if missing:
        raise EnvironmentError(f"Variables d'environnement manquantes: {', '.join(missing)}")

HEALTHCHECK_URL   = os.environ.get("HEALTHCHECK_URL", "")   # IDEA-T05: https://hc-ping.com/XXXX
ADMIN_EMAIL       = os.environ.get("ADMIN_EMAIL", "arnaud.mathier@gmail.com")
SHEET_TAB         = "Form Responses 1"

# Périmètre de détection en mètres
PERIMETER_M       = 500

# Fenêtre de recherche : publications des N derniers jours
SEARCH_DAYS       = 30

# Double-check : sites communaux à scraper pour comparer les comptages
# Format : { "NOM_COMMUNE_API": "URL_PAGE_ENQUETES" }
SHEET_HISTORIQUE  = "Historique Alertes"
SHEET_ZONE        = "Zone Elargie"
PERIMETER_LARGE_M = 2000

COMMUNE_BACKUP_URLS = {
    "AIGLE":       "https://www.aigle.ch/enquetes-publiques",
    "MONTREUX":    "https://www.montreux.ch/travaux-et-urbanisme/urbanisme/mises-a-lenquete",
    "PREVERENGES": "https://www.preverenges.ch/informations/enquetes",
    "LAUSANNE":    "https://www.lausanne.ch/officiel/administration/travaux/urbanisme/permis-et-enquetes/mises-a-l-enquete.html",
    "MORGES":      "https://www.morges.ch/urbanisme-constructions/enquetes-publiques",
    "NYON":        "https://www.nyon.ch/fr/vie-quotidienne/construction-urbanisme/mises-a-lenquete",
    "RENENS":      "https://www.renens.ch/urbanisme/mises-a-l-enquete",
}

# Fichiers locaux
NOTIFIED_FILE     = "notified.json"
LOGS_DIR          = "logs"

# URL fiche détaillée canton
CAMAC_BASE_URL    = "https://prestations.vd.ch/pub/actiscamac/101091/5H1IET-7NLEK1/search"
FAO_BASE_URL      = "https://www.faovd.ch/permis-de-construire/"

# ─────────────────────────────────────────────
# INITIALISATION
# ─────────────────────────────────────────────

Path(LOGS_DIR).mkdir(exist_ok=True)

import logging as _logging, sys as _sys

def _setup_logger():
    Path("logs").mkdir(exist_ok=True)
    fmt = _logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    logger = _logging.getLogger("phemeapp")
    logger.setLevel(_logging.DEBUG)
    if not logger.handlers:
        sh = _logging.StreamHandler(_sys.stdout)
        sh.setFormatter(fmt)
        logger.addHandler(sh)
        try:
            fh = _logging.FileHandler("logs/phemeapp.log", encoding="utf-8")
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except Exception:
            pass
    return logger

_logger = _setup_logger()

def log(msg, level="info"):
    """Logging structuré — IDEA-T08."""
    getattr(_logger, level if level in ("debug","info","warning","error","critical") else "info")(msg)


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
        f"/gviz/tq?tqx=out:csv&sheet={url_quote(SHEET_TAB)}"
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


def fetch_enquetes_with_retry(days=SEARCH_DAYS, max_retries=2, delay=30):
    """IDEA-T10: Retry automatique si API CAMAC down."""
    import time as _time
    for attempt in range(max_retries + 1):
        result = fetch_enquetes(days)
        if result:
            return result
        if attempt < max_retries:
            log(f"API CAMAC vide — retry {attempt+1}/{max_retries} dans {delay}s...", "warning")
            _time.sleep(delay)
    log("API CAMAC indisponible après retries — abandon", "error")
    return []


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

# Token GitHub pour lecture/ecriture notified.json dans le repo
GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO    = os.environ.get("GITHUB_REPOSITORY", "Arnaud-Mat/phemeapp")
NOTIFIED_PATH  = "notified.json"

def load_notified():
    """
    Charge notified.json depuis GitHub (si GITHUB_TOKEN disponible)
    ou depuis le fichier local en fallback.
    """
    # Essai lecture depuis GitHub
    if GITHUB_TOKEN:
        try:
            r = requests.get(
                f"https://api.github.com/repos/{GITHUB_REPO}/contents/{NOTIFIED_PATH}",
                headers={"Authorization": f"token {GITHUB_TOKEN}",
                         "Accept": "application/vnd.github.v3+json"},
                timeout=10
            )
            if r.status_code == 200:
                data = r.json()
                content = base64.b64decode(data["content"]).decode("utf-8")
                log("notified.json charge depuis GitHub")
                return json.loads(content)
            elif r.status_code == 404:
                log("notified.json absent sur GitHub - nouveau fichier")
                return {}
        except Exception as e:
            log(f"Erreur lecture notified.json GitHub: {e}")

    # Fallback: fichier local
    if Path(NOTIFIED_FILE).exists():
        with open(NOTIFIED_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_notified(notified):
    """
    Sauvegarde notified.json sur GitHub (si GITHUB_TOKEN disponible)
    ET localement en backup.
    """
    content_str = json.dumps(notified, indent=2, ensure_ascii=False)

    # Sauvegarde locale
    with open(NOTIFIED_FILE, "w", encoding="utf-8") as f:
        f.write(content_str)

    # Sauvegarde sur GitHub
    if GITHUB_TOKEN:
        try:
            encoded = base64.b64encode(content_str.encode("utf-8")).decode()

            # Recuperer le SHA actuel si le fichier existe
            r = requests.get(
                f"https://api.github.com/repos/{GITHUB_REPO}/contents/{NOTIFIED_PATH}",
                headers={"Authorization": f"token {GITHUB_TOKEN}",
                         "Accept": "application/vnd.github.v3+json"},
                timeout=10
            )
            payload = {
                "message": "Update notified.json",
                "content": encoded,
                "committer": {"name": "PhemeApp Bot", "email": "bot@phemeapp.ch"}
            }
            if r.status_code == 200:
                payload["sha"] = r.json()["sha"]

            resp = requests.put(
                f"https://api.github.com/repos/{GITHUB_REPO}/contents/{NOTIFIED_PATH}",
                headers={"Authorization": f"token {GITHUB_TOKEN}",
                         "Accept": "application/vnd.github.v3+json"},
                json=payload,
                timeout=15
            )
            if resp.status_code in [200, 201]:
                log(f"notified.json sauvegarde sur GitHub ({len(notified)} entrees)")
            else:
                log(f"Erreur sauvegarde GitHub: {resp.status_code}")
        except Exception as e:
            log(f"Erreur ecriture notified.json GitHub: {e}")

def already_notified(notified, email, no_camac):
    return f"{email}:{no_camac}" in notified

def already_notified_similar(notified, email, enquete, threshold_days=60):
    """
    IDEA-T13: Déduplication intelligente.
    Vérifie si une alerte similaire (même lieu + commune) a été envoyée
    dans les X derniers jours — pour éviter les doublons sur prolongations.
    """
    lieu    = (enquete.get("lieu") or "").strip().lower()[:30]
    commune = (enquete.get("commune") or "").strip().lower()[:20]
    if not lieu or not commune:
        return False
    cutoff = (datetime.now() - timedelta(days=threshold_days)).isoformat()
    for key, val in notified.items():
        if not key.startswith(f"{email}:"):
            continue
        if not isinstance(val, str) or val < cutoff:
            continue
        # Chercher le contexte stocké avec la clé
        context_key = key + ":ctx"
        ctx = notified.get(context_key, "")
        if lieu in ctx and commune in ctx:
            log(f"  Doublon détecté: {lieu}, {commune} déjà alerté récemment", "warning")
            return True
    return False

def mark_notified_with_context(notified, email, no_camac, enquete):
    """Marque comme notifié en stockant le contexte pour la déduplication."""
    notified[f"{email}:{no_camac}"] = datetime.now().isoformat()
    lieu    = (enquete.get("lieu") or "").strip().lower()[:30]
    commune = (enquete.get("commune") or "").strip().lower()[:20]
    if lieu and commune:
        notified[f"{email}:{no_camac}:ctx"] = f"{lieu}|{commune}"

def mark_notified(notified, email, no_camac):
    notified[f"{email}:{no_camac}"] = datetime.now().isoformat()

def is_new_user(notified, email):
    return f"welcome:{email}" not in notified

def mark_welcome_sent(notified, email):
    notified[f"welcome:{email}"] = datetime.now().isoformat()


# ─────────────────────────────────────────────
# 6. EMAIL DE BIENVENUE
# ─────────────────────────────────────────────


def smtp_send(dest, subject, html):
    """Envoie un email HTML via Brevo SMTP."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{BREVO_SENDER_NAME} <{BREVO_SENDER}>"
    msg["To"]      = dest
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP("smtp-relay.brevo.com", 587) as srv:
        srv.starttls()
        srv.login(BREVO_SMTP_LOGIN, BREVO_API_KEY)
        srv.sendmail(BREVO_SENDER, dest, msg.as_string())


def send_welcome_email(dest_email, dest_nom, adresses):
    prenom = esc(dest_nom.split()[0] if dest_nom else "bonjour")
    unsub_lien = get_unsub_link(dest_email)
    magic_lien = get_magic_link(dest_email)
    adresses_html = "".join([
        f'<tr style="border-bottom:1px solid #eee;"><td style="padding:8px 10px;color:#888;">{esc(a["label"])}</td>'
        f'<td style="padding:8px 10px;">{esc(a["adresse"])}</td></tr>'
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
  <p style="font-size:11px;color:#aaa;border-top:1px solid #eee;padding-top:14px;margin-top:24px;line-height:1.6;">PhémeApp est un service d'information automatisé. Il ne remplace pas une consultation juridique. &nbsp;<a href='{unsub_lien}' style='color:#bbb;font-size:10px'>Se désinscrire</a></p>
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
    except Exception:
        return "date inconnue"

def send_email(dest_email, dest_nom, enquete, adresse, distance_m, profil=''):
    date_fao    = format_date(enquete.get("dateFao", 0))
    try:
        jours_restants = max(0, (datetime.fromtimestamp(enquete.get("dateFao",0)/1000) + timedelta(days=30) - datetime.now()).days)
        urgence = jours_restants <= 7
    except Exception:
        jours_restants = 30
        urgence = False
    # Variables couleur pour le HTML (evite ternaires dans f-strings)
    bg_alerte     = "#fee2e2" if urgence else "#fff8e1"
    border_alerte = "#dc2626" if urgence else "#f59e0b"
    color_alerte  = "#991b1b" if urgence else "#92400e"
    prefix_alerte = "\u26a0\ufe0f URGENT \u2014 " if urgence else "\u23f1 "
    jours_txt     = f"{jours_restants} jour{'s' if jours_restants > 1 else ''}"
    no_camac    = esc(str(enquete.get("noCamac", "?")))
    unsub_lien  = get_unsub_link(dest_email)
    magic_lien   = get_magic_link(dest_email)
    lieu        = esc(enquete.get("lieu", "—"))
    commune     = esc(enquete.get("commune", "—"))
    description = enquete.get("description", "—")
    nature      = enquete.get("natureTravaux", "—")
    fao_lib     = enquete.get("faoLib", "")
    # BUG-007 fix: lien vers site communal si disponible, sinon FAO Vaud
    commune_url = find_commune_enquetes_url(commune.upper()) if commune and commune != "—" else None
    lien        = commune_url if commune_url else FAO_BASE_URL
    msg_profil = ("<p style='font-size:13px;color:#dc2626;background:#fee2e2;padding:10px 14px;border-radius:6px;margin:12px 0'>Locataire: consultez le dossier gratuitement \u00e0 la commune.</p>" if profil and "locataire" in profil.lower() else "<p style='font-size:13px;color:#666'>En cas de doute, contactez votre commune.</p>")
    # IDEA-P15: lien cadastre vaudois (geo.vd.ch)
    lat_enq = enquete.get("lat", "")
    lng_enq = enquete.get("lng", "")
    cadastre_url = f"https://www.geo.vd.ch/?map_x={lng_enq}&map_y={lat_enq}&map_zoom=10" if lat_enq and lng_enq else ""
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
          <strong style="color:{color_alerte}">{prefix_alerte}{jours_txt} pour faire opposition</strong><br>
          <span style="font-size:13px;color:{color_alerte}">Date FAO : <strong>{date_fao}</strong> — d&eacute;lai l&eacute;gal de 30 jours.</span>
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
          PhémeApp est un service d'information automatisé. Il ne remplace pas un avis juridique. &nbsp;&nbsp;<a href='{magic_lien}' style='color:#1a3a5c;font-size:10px;font-weight:500'>Mon espace →</a> &nbsp;<a href='{unsub_lien}' style='color:#bbb;font-size:10px'>Se désinscrire</a>
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


COMMUNES_CACHE_FILE = "communes_cache.json"

def load_communes_cache():
    """Charge le cache des URLs communales."""
    if Path(COMMUNES_CACHE_FILE).exists():
        with open(COMMUNES_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_communes_cache(cache):
    with open(COMMUNES_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)

def get_commune_from_coords(lat, lng):
    """Trouve le nom de la commune depuis des coordonnees GPS via Nominatim.
    IDEA-T09: délai 1s entre appels pour respecter les CGU Nominatim."""
    import time as _time
    _time.sleep(1)  # max 1 req/sec
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lng, "format": "json", "accept-language": "fr"},
            headers={"User-Agent": "PhemeApp/1.0 phemeapp.ch"},
            timeout=10
        )
        addr = r.json().get("address", {})
        commune = addr.get("town") or addr.get("village") or addr.get("city") or addr.get("municipality") or ""
        return commune.upper().strip()
    except Exception as e:
        log(f"  Reverse geocoding impossible: {e}")
        return ""

def find_commune_enquetes_url(commune_name):
    """
    Cherche automatiquement la page des mises a l enquete du site communal.
    Utilise une recherche DuckDuckGo puis verifie les resultats.
    Retourne l URL si trouvee, sinon None.
    """
    # D abord verifier dans COMMUNE_BACKUP_URLS
    if commune_name.upper() in COMMUNE_BACKUP_URLS:
        return COMMUNE_BACKUP_URLS[commune_name.upper()]

    # Puis verifier dans le cache
    cache = load_communes_cache()
    if commune_name.upper() in cache:
        return cache[commune_name.upper()]

    log(f"  Recherche URL enquetes pour commune: {commune_name}")

    # Recherche via l API DuckDuckGo (gratuite, sans cle)
    search_queries = [
        f"{commune_name.lower()} vaud mises enquete publique permis construire",
        f"site:{commune_name.lower()}.ch enquete publique",
    ]

    found_url = None

    for query in search_queries:
        try:
            r = requests.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_redirect": "1", "no_html": "1"},
                headers={"User-Agent": "PhemeApp/1.0 phemeapp.ch"},
                timeout=10
            )
            results = r.json()

            # Verifier les resultats
            related = results.get("RelatedTopics", [])
            abstract_url = results.get("AbstractURL", "")

            candidates = [abstract_url] if abstract_url else []
            for item in related[:5]:
                if isinstance(item, dict) and item.get("FirstURL"):
                    candidates.append(item["FirstURL"])

            # Filtrer pour garder les URLs pertinentes
            commune_lower = commune_name.lower()
            keywords = ["enquete", "permis", "construire", "urbanisme", "construction"]
            for url in candidates:
                url_lower = url.lower()
                if commune_lower in url_lower and any(kw in url_lower for kw in keywords):
                    found_url = url
                    log(f"  URL trouvee pour {commune_name}: {found_url}")
                    break

            if found_url:
                break

        except Exception as e:
            log(f"  Recherche URL {commune_name} echouee: {e}")
            continue

    # Fallback: construire une URL probable et la tester
    if not found_url:
        commune_slug = commune_name.lower().replace(" ", "-").replace("e", "e")
        candidates_urls = [
            f"https://www.{commune_slug}.ch/construction-urbanisme/mises-a-l-enquete",
            f"https://www.{commune_slug}.ch/urbanisme/enquetes-publiques",
            f"https://www.{commune_slug}.ch/travaux-urbanisme/mises-a-lenquete",
            f"https://www.{commune_slug}.ch/enquetes-publiques",
        ]
        for url in candidates_urls:
            try:
                resp = requests.get(url, timeout=8, headers={"User-Agent": "PhemeApp/1.0"}, allow_redirects=True)
                if resp.status_code == 200 and any(kw in resp.text.lower() for kw in ["enquete", "permis de construire"]):
                    found_url = url
                    log(f"  URL fallback validee pour {commune_name}: {found_url}")
                    break
            except Exception:
                continue

    # Sauvegarder dans le cache
    if found_url:
        cache[commune_name.upper()] = found_url
        save_communes_cache(cache)
        log(f"  URL communale mise en cache: {commune_name} -> {found_url}")
    else:
        # Mettre None en cache pour eviter de re-chercher
        cache[commune_name.upper()] = None
        save_communes_cache(cache)
        log(f"  Aucune URL trouvee pour {commune_name} - FAO utilisee par defaut")

    return found_url


def check_commune_backup(commune_name, api_count):
    """
    Double-check sur le site officiel de la commune.
    Cherche automatiquement l URL de la page des enquetes publiques
    si elle n est pas encore connue, puis scrape le contenu.
    Source privilegiee : commune (dossiers complets avec plans).
    """
    # Trouver l URL communale automatiquement
    url = find_commune_enquetes_url(commune_name)

    if not url:
        log(f"  Aucun site communal trouve pour {commune_name} - double-check ignore")
        return

    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "PhemeApp/1.0"})
        r.raise_for_status()
        text = r.text.lower()
        keywords = ["enquete", "enquête", "permis de construire", "mise à l enquete", "camac"]
        hits = sum(text.count(kw) for kw in keywords)

        if hits == 0 and api_count > 0:
            log(f"  ATTENTION {commune_name} : site communal ({url}) semble vide mais CAMAC retourne {api_count} resultats")
        else:
            log(f"  OK double-check {commune_name} : {hits} occurrences sur {url}")
    except Exception as e:
        log(f"  Double-check {commune_name} inaccessible ({url}) : {e}")


# ─────────────────────────────────────────────
# 7. BOUCLE PRINCIPALE
# ─────────────────────────────────────────────

# URL du Apps Script Web App pour ecriture dans le Sheet
# A deployer une fois via Apps Script > Deployer > Nouvelle mise en prod > Web App
# Acces: Tout le monde, executer en tant que: moi
APPS_SCRIPT_WEBAPP_URL = os.environ.get("APPS_SCRIPT_WEBAPP_URL", "")

def append_to_sheet(tab_name, row):
    """
    Ecrit une ligne dans le Google Sheet via deux methodes:
    1. Apps Script Web App (si URL configuree) -> ecriture directe dans le Sheet
    2. Fichier JSONL local (backup toujours actif)
    """
    # Backup local JSONL (toujours)
    Path("logs").mkdir(exist_ok=True)
    log_file = f"logs/sheet_{tab_name.replace(' ', '_').lower()}.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Ecriture dans le Sheet via Apps Script Web App
    if APPS_SCRIPT_WEBAPP_URL:
        try:
            payload = {"tab": tab_name, "row": row}
            resp = requests.post(
                APPS_SCRIPT_WEBAPP_URL,
                json=payload,
                timeout=15,
                headers={"Content-Type": "application/json"}
            )
            if resp.status_code == 200:
                log(f"  Sheet '{tab_name}' mis a jour")
            else:
                log(f"  Sheet '{tab_name}' erreur {resp.status_code}: {resp.text[:100]}")
        except Exception as e:
            log(f"  Sheet '{tab_name}' inaccessible: {e} (JSONL sauvegarde localement)")
    else:
        log(f"  JSONL local: {log_file} (configurer APPS_SCRIPT_WEBAPP_URL pour ecrire dans le Sheet)")

def log_alerte_historique(user, adr, enquete, distance_m):
    """Enregistre une alerte envoyée dans l'historique."""
    date_fao = format_date(enquete.get("dateFao", 0))
    try:
        jours_restants = max(0, (datetime.fromtimestamp(enquete.get("dateFao",0)/1000) + timedelta(days=30) - datetime.now()).days)
    except Exception:
        jours_restants = 30
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
    try:
        jours_restants = max(0, (datetime.fromtimestamp(enquete.get("dateFao",0)/1000) + timedelta(days=30) - datetime.now()).days)
    except Exception:
        jours_restants = 30
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


def load_historique_from_sheet(email, mois):
    """
    Lit l onglet 'Historique Alertes' du Sheet pour un utilisateur et un mois donnés.
    Retourne la liste des alertes envoyées ce mois.
    Colonnes: date_envoi, email, nom, label_adresse, adresse, no_camac,
              lieu, commune, nature_travaux, distance_m, date_fao, lien
    """
    try:
        url = (
            f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
            f"/gviz/tq?tqx=out:csv&sheet={url_quote(SHEET_HISTORIQUE)}"
        )
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        import csv as _csv, io as _io
        reader = _csv.reader(_io.StringIO(r.text))
        rows = list(reader)
        if not rows:
            return []
        # Skip header
        alertes = []
        for row in rows[1:]:
            if len(row) < 10:
                continue
            row_email = row[1].strip()
            row_date  = row[0].strip()  # format ISO: 2026-06-12T...
            row_mois  = row_date[:7]    # 2026-06
            if row_email == email and row_mois == mois:
                alertes.append({
                    "date_envoi":    row[0],
                    "email":         row[1],
                    "nom":           row[2],
                    "label_adresse": row[3],
                    "adresse":       row[4],
                    "no_camac":      row[5],
                    "lieu":          row[6],
                    "commune":       row[7],
                    "nature_travaux":row[8],
                    "distance_m":    row[9],
                    "date_fao":      row[10] if len(row) > 10 else "",
                    "lien":          row[11] if len(row) > 11 else "",
                })
        return alertes
    except Exception as e:
        log(f"  Lecture Historique Sheet impossible: {e}")
        return []


def load_zone_elargie_from_sheet(email, mois):
    """
    Lit l onglet 'Zone Elargie' du Sheet pour un utilisateur et un mois donnés.
    Retourne les publications entre 500m et 2km ce mois.
    """
    try:
        url = (
            f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
            f"/gviz/tq?tqx=out:csv&sheet={url_quote(SHEET_ZONE)}"
        )
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        import csv as _csv, io as _io
        reader = _csv.reader(_io.StringIO(r.text))
        rows = list(reader)
        zone = []
        for row in rows[1:]:
            if len(row) < 10:
                continue
            row_email = row[1].strip()
            row_mois  = row[0].strip()[:7]
            if row_email == email and row_mois == mois:
                zone.append({
                    "lieu":          row[6],
                    "commune":       row[7],
                    "nature_travaux":row[8],
                    "distance_m":    row[9],
                    "date_fao":      row[10] if len(row) > 10 else "",
                    "lien":          row[11] if len(row) > 11 else "",
                })
        return zone
    except Exception as e:
        log(f"  Lecture Zone Elargie Sheet impossible: {e}")
        return []


def send_monthly_confirmation(user, notified):
    """
    IDEA-P03: Email mensuel basé sur l historique réel du Sheet.
    Source: onglet 'Historique Alertes' (alertes envoyées) + 'Zone Elargie' (activité autour).
    Max 1x/mois/utilisateur.
    """
    email = user["email"]
    mois  = datetime.now().strftime("%Y-%m")
    key   = f"monthly:{email}:{mois}"
    if key in notified:
        return
    if not user.get('notif_mensuel', True):
        return  # IDEA-U07: désactivé par l'utilisateur

    prenom = esc(user["nom"].split()[0] if user["nom"] else "bonjour")
    unsub_lien = get_unsub_link(email)

    # Lire l historique réel depuis le Sheet
    alertes_mois = load_historique_from_sheet(email, mois)
    zone_mois    = load_zone_elargie_from_sheet(email, mois)
    nb_alertes   = len(alertes_mois)
    nb_zone      = len(zone_mois)

    couleur_statut = "#dc2626" if nb_alertes > 0 else "#1a7a4a"
    if nb_alertes > 0:
        msg_statut = f"⚠️ {nb_alertes} mise{'s' if nb_alertes > 1 else ''} à l'enquête dans votre périmètre ce mois"
    else:
        msg_statut = "✅ Aucune mise à l'enquête dans votre périmètre ce mois"

    # Tableau des alertes envoyées ce mois
    rows_alertes = ""
    for a in alertes_mois:
        rows_alertes += (
            f"<tr style='border-bottom:1px solid #eee'>"
            f"<td style='padding:7px 8px;color:#dc2626;font-weight:bold'>{a['distance_m']}m</td>"
            f"<td style='padding:7px 8px'>{esc(a['commune'])}</td>"
            f"<td style='padding:7px 8px;font-size:12px;color:#666'>{a['nature_travaux'][:50]}</td>"
            f"<td style='padding:7px 8px;font-size:12px'>{a['date_fao']}</td>"
            f"<td style='padding:7px 8px;font-size:11px;color:#888'>{a['label_adresse']}</td>"
            f"</tr>"
        )

    # Tableau zone élargie (500m–2km)
    rows_zone = ""
    for z in zone_mois[:5]:
        rows_zone += (
            f"<tr style='border-bottom:1px solid #eee'>"
            f"<td style='padding:7px 8px;color:#f59e0b;font-weight:bold'>{z['distance_m']}m</td>"
            f"<td style='padding:7px 8px'>{esc(z['commune'])}</td>"
            f"<td style='padding:7px 8px;font-size:12px;color:#666'>{z['nature_travaux'][:50]}</td>"
            f"<td style='padding:7px 8px;font-size:12px'>{z['date_fao']}</td>"
            f"</tr>"
        )

    # Calculer le nombre total analysé depuis le Sheet
    # (si Sheet vide, on ne peut pas savoir — on indique juste le mois)
    mois_label = datetime.now().strftime("%B %Y")

    html = (
        "<html><body style='font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333'>"
        "<div style='background:#1a3a5c;padding:18px 24px'>"
        "<h1 style='color:white;margin:0;font-size:20px'>PhémeApp</h1>"
        f"<p style='color:#a8c4e0;margin:4px 0 0;font-size:12px'>Rapport mensuel — {mois_label}</p>"
        "</div><div style='padding:24px'>"
        f"<p style='font-size:16px'>Bonjour {prenom},</p>"
        "<p style='font-size:14px;color:#444;line-height:1.7'>"
        "Votre surveillance PhémeApp est <strong style='color:#1a7a4a'>active</strong>. "
        f"Voici le bilan de votre surveillance pour <strong>{mois_label}</strong>.</p>"
        f"<div style='background:#f0fdf4;border-left:3px solid {couleur_statut};padding:14px 18px;margin:16px 0;border-radius:0 6px 6px 0'>"
        f"<strong style='color:{couleur_statut}'>{msg_statut}</strong></div>"
    )

    # Section alertes envoyées
    if alertes_mois:
        html += (
            "<p style='font-size:14px;color:#444;margin-top:16px;font-weight:500'>"
            "📬 Alertes envoyées ce mois :</p>"
            "<table style='width:100%;border-collapse:collapse;font-size:13px'>"
            "<tr style='background:#fee2e2'>"
            "<th style='padding:7px 8px;text-align:left'>Distance</th>"
            "<th style='padding:7px 8px;text-align:left'>Commune</th>"
            "<th style='padding:7px 8px;text-align:left'>Nature</th>"
            "<th style='padding:7px 8px;text-align:left'>FAO</th>"
            "<th style='padding:7px 8px;text-align:left'>Adresse</th></tr>"
            + rows_alertes + "</table>"
        )

    # Section zone élargie
    if zone_mois:
        html += (
            f"<p style='font-size:14px;color:#444;margin-top:20px;font-weight:500'>"
            f"📍 Activité dans un rayon de 2km ({nb_zone} publication{'s' if nb_zone > 1 else ''}) :</p>"
            "<table style='width:100%;border-collapse:collapse;font-size:13px'>"
            "<tr style='background:#fff8e1'>"
            "<th style='padding:7px 8px;text-align:left'>Distance</th>"
            "<th style='padding:7px 8px;text-align:left'>Commune</th>"
            "<th style='padding:7px 8px;text-align:left'>Nature</th>"
            "<th style='padding:7px 8px;text-align:left'>FAO</th></tr>"
            + rows_zone + "</table>"
            "<p style='font-size:11px;color:#888;margin-top:4px'>Ces publications sont hors de votre périmètre de 500m — aucune alerte envoyée.</p>"
        )

    if not alertes_mois and not zone_mois:
        html += "<p style='font-size:14px;color:#666'>Aucune activité détectée dans un rayon de 2km ce mois.</p>"

    html += (
        "<p style='font-size:14px;color:#444;margin-top:20px'>Bien cordialement,<br>"
        "<strong>L'équipe PhémeApp</strong></p>"
        "<p style='font-size:11px;color:#aaa;border-top:1px solid #eee;padding-top:12px;margin-top:20px'>"
        "PhémeApp — service d'information automatisé. Il ne remplace pas une consultation juridique. "
        f"&nbsp;<a href='{unsub_lien}' style='color:#bbb;font-size:10px'>Se désinscrire</a></p>"
        f"{get_tracking_pixel(email, 'monthly')}"
        "</div></body></html>"
    )

    try:
        smtp_send(email, f"PhémeApp — Rapport {mois_label}", html)
        notified[key] = datetime.now().isoformat()
        log(f"  Rapport mensuel envoyé -> {email} ({nb_alertes} alertes, {nb_zone} zone élargie)")
    except Exception as e:
        log(f"  Erreur rapport mensuel {email}: {e}")


def send_rappel_j7(user, notified, enquetes):
    """
    IDEA-P11: Rappel envoyé J-7 avant fin du délai d'opposition (30j depuis FAO).
    Envoyé une seule fois par enquête par utilisateur.
    Seulement pour les enquêtes dans le périmètre de 500m.
    """
    email  = user["email"]
    if not user.get('notif_rappel', True):
        return  # IDEA-U07: rappels désactivés par l'utilisateur
    prenom = esc(user["nom"].split()[0] if user["nom"] else "bonjour")

    for adr in user["adresses"]:
        if not adr.get("lat"):
            continue
        for enquete in enquetes:
            if not enquete.get("lat"):
                continue
            dist = haversine_m(adr["lat"], adr["lng"], enquete["lat"], enquete["lng"])
            if dist > PERIMETER_M:
                continue

            no_camac = enquete.get("noCamac", "?")
            key_rappel = f"rappel7:{email}:{no_camac}"
            if key_rappel in notified:
                continue  # déjà envoyé

            # Calculer les jours restants
            try:
                ts_ms = enquete.get("dateFao", 0)
                date_pub = datetime.fromtimestamp(ts_ms / 1000)
                date_limite = date_pub + timedelta(days=30)
                jours_restants = (date_limite - datetime.now()).days
            except Exception:
                continue

            # Envoyer seulement si entre 5 et 8 jours restants
            if not (5 <= jours_restants <= 8):
                continue

            lieu     = esc(enquete.get("lieu", "--"))
            commune  = esc(enquete.get("commune", "--"))
            nature   = esc(enquete.get("natureTravaux", "--"))
            date_fao = format_date(ts_ms)
            commune_url = find_commune_enquetes_url(commune.upper()) if commune else None
            lien     = commune_url if commune_url else FAO_BASE_URL
            unsub_lien = get_unsub_link(email)

            html = (
                "<html><body style='font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333'>"
                "<div style='background:#dc2626;padding:18px 24px'>"
                "<h1 style='color:white;margin:0;font-size:20px'>⚠️ PhémeApp — Rappel urgent</h1>"
                "<p style='color:#fecaca;margin:4px 0 0;font-size:12px'>Délai d'opposition bientôt expiré</p>"
                "</div><div style='padding:24px'>"
                f"<p style='font-size:16px'>Bonjour {prenom},</p>"
                f"<p style='font-size:14px;color:#444;line-height:1.7'>Il vous reste <strong style='color:#dc2626;font-size:18px'>{jours_restants} jours</strong> pour faire opposition à une mise à l'enquête proche de <em>{adr['label']} — {adr['adresse']}</em>.</p>"
                "<div style='background:#fee2e2;border:2px solid #dc2626;padding:16px 18px;margin:16px 0;border-radius:6px'>"
                f"<p style='margin:0 0 8px;font-size:14px;color:#991b1b;font-weight:bold'>📍 {lieu}, {commune}</p>"
                f"<p style='margin:0 0 4px;font-size:13px;color:#7f1d1d'><strong>Nature :</strong> {nature}</p>"
                f"<p style='margin:0 0 4px;font-size:13px;color:#7f1d1d'><strong>Publié le :</strong> {date_fao}</p>"
                f"<p style='margin:0;font-size:13px;color:#7f1d1d'><strong>Distance :</strong> {round(dist)} m de votre adresse</p>"
                "</div>"
                "<p style='font-size:14px;color:#444;line-height:1.7'>Le délai légal de 30 jours pour déposer une opposition court depuis la date de publication. <strong>Passé ce délai, vous ne pourrez plus vous opposer.</strong></p>"
                f"<div style='text-align:center;margin:24px 0'><a href='{lien}' style='background:#dc2626;color:white;padding:14px 28px;text-decoration:none;border-radius:6px;font-size:15px;font-weight:bold'>Consulter le dossier →</a></div>"
                "<p style='font-size:13px;color:#666'>En cas de doute, n'hésitez pas à contacter votre commune ou un avocat spécialisé en droit public.</p>"
                "<p style='font-size:14px;color:#444'>Bien cordialement,<br><strong>L'équipe PhémeApp</strong></p>"
                f"<p style='font-size:11px;color:#aaa;border-top:1px solid #eee;padding-top:12px;margin-top:20px'>PhémeApp — service d'information automatisé. Il ne remplace pas une consultation juridique. &nbsp;<a href='{unsub_lien}' style='color:#bbb;font-size:10px'>Se désinscrire</a></p>"
                "</div></body></html>"
            )

            try:
                smtp_send(
                    email,
                    f"⚠️ Plus que {jours_restants} jours pour vous opposer — {commune}",
                    html
                )
                notified[key_rappel] = datetime.now().isoformat()
                log(f"  Rappel J-7 envoyé -> {email} (CAMAC {no_camac}, J-{jours_restants})")
            except Exception as e:
                log(f"  Erreur rappel J-7 {email}: {e}", "error")


def send_zone_elargie_newsletter(user, notified):
    """
    IDEA-P02: Newsletter mensuelle dédiée à la zone élargie (500m–2km).
    Complémentaire du rapport mensuel — focus sur l'activité du quartier.
    Max 1x/mois, envoyée seulement si au moins 1 publication en zone élargie.
    """
    email = user["email"]
    mois  = datetime.now().strftime("%Y-%m")
    key   = f"newsletter_zone:{email}:{mois}"
    if key in notified:
        return

    prenom = esc(user["nom"].split()[0] if user["nom"] else "bonjour")
    zone_mois = load_zone_elargie_from_sheet(email, mois)

    if not zone_mois:
        return  # Rien en zone élargie ce mois — pas d'email

    nb = len(zone_mois)
    mois_label = datetime.now().strftime("%B %Y")
    unsub_lien = get_unsub_link(email)
    magic_lien = get_magic_link(email)

    rows_html = ""
    for z in zone_mois[:10]:
        dist = z.get("distance_m", "?")
        commune = z.get("commune", "--")
        nature = (z.get("nature_travaux") or "--")[:60]
        date_fao = z.get("date_fao", "--")
        lien = z.get("lien", FAO_BASE_URL)
        col = "#f59e0b" if int(str(dist).replace("m","") or 2000) <= 1000 else "#888"
        rows_html += (
            f"<tr style='border-bottom:1px solid #eee'>"
            f"<td style='padding:8px;color:{col};font-weight:bold'>{dist}m</td>"
            f"<td style='padding:8px'>{commune}</td>"
            f"<td style='padding:8px;font-size:12px;color:#666'>{nature}</td>"
            f"<td style='padding:8px;font-size:12px'>{date_fao}</td>"
            f"<td style='padding:8px;font-size:12px'><a href='{lien}' style='color:#1a3a5c'>Voir →</a></td>"
            f"</tr>"
        )

    html = (
        "<html><body style='font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333'>"
        "<div style='background:#1a3a5c;padding:18px 24px'>"
        "<h1 style='color:white;margin:0;font-size:20px'>PhémeApp</h1>"
        f"<p style='color:#a8c4e0;margin:4px 0 0;font-size:12px'>Activité du quartier — {mois_label}</p>"
        "</div><div style='padding:24px'>"
        f"<p style='font-size:16px'>Bonjour {prenom},</p>"
        f"<p style='font-size:14px;color:#444;line-height:1.7'>Ce mois, <strong>{nb} mise{'s' if nb > 1 else ''} à l'enquête</strong> ont été publiées dans un rayon de <strong>500m à 2km</strong> autour de vos adresses. Aucune n'était assez proche pour déclencher une alerte directe, mais voici l'activité de votre quartier.</p>"
        "<div style='background:#fff8e1;border-left:3px solid #f59e0b;padding:12px 16px;margin:16px 0;border-radius:0 6px 6px 0'>"
        "<strong style='color:#92400e;font-size:13px'>Ces projets ne concernent pas directement votre périmètre de 500m.</strong>"
        "</div>"
        "<table style='width:100%;border-collapse:collapse;font-size:13px;margin:16px 0'>"
        "<tr style='background:#f0f4f8'>"
        "<th style='padding:8px;text-align:left'>Distance</th>"
        "<th style='padding:8px;text-align:left'>Commune</th>"
        "<th style='padding:8px;text-align:left'>Nature</th>"
        "<th style='padding:8px;text-align:left'>FAO</th>"
        "<th style='padding:8px;text-align:left'>Lien</th></tr>"
        + rows_html + "</table>"
        f"<p style='font-size:13px;color:#888;margin-top:16px'><a href='{magic_lien}' style='color:#1a3a5c;font-weight:500'>Mon espace PhémeApp →</a></p>"
        "<p style='font-size:14px;color:#444;margin-top:20px'>Bien cordialement,<br><strong>L'équipe PhémeApp</strong></p>"
        f"<p style='font-size:11px;color:#aaa;border-top:1px solid #eee;padding-top:12px;margin-top:20px'>PhémeApp — service d'information automatisé. Il ne remplace pas une consultation juridique. &nbsp;<a href='{unsub_lien}' style='color:#bbb;font-size:10px'>Se désinscrire</a></p>"
        "</div></body></html>"
    )

    try:
        smtp_send(email, f"PhémeApp — Activité de votre quartier en {mois_label}", html)
        notified[key] = datetime.now().isoformat()
        log(f"  Newsletter zone élargie envoyée -> {email} ({nb} publications)")
    except Exception as e:
        log(f"  Erreur newsletter zone {email}: {e}", "error")


def send_annual_summary(user, notified):
    """
    IDEA-P20: Email bilan annuel envoyé en janvier.
    Récapitulatif de l'année écoulée: alertes reçues, communes actives, etc.
    """
    # Seulement en janvier
    if datetime.now().month != 1:
        return

    email = user["email"]
    annee = datetime.now().year - 1  # Bilan de l'année précédente
    key   = f"annual:{email}:{annee}"
    if key in notified:
        return

    prenom = esc(user["nom"].split()[0] if user["nom"] else "bonjour")

    # Lire l'historique depuis le Sheet pour l'année écoulée
    try:
        from urllib.parse import quote as _q
        url = (f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
               f"/gviz/tq?tqx=out:csv&sheet={_q(SHEET_HISTORIQUE)}")
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        import csv as _csv, io as _io
        rows = list(_csv.reader(_io.StringIO(resp.text)))[1:]
        alertes_annee = [r for r in rows
                         if len(r) > 1 and r[1].strip().lower() == email.lower()
                         and str(annee) in r[0]]
    except Exception as e:
        log(f"Lecture historique annuel: {e}", "warning")
        alertes_annee = []

    nb_alertes = len(alertes_annee)
    communes_vues = {}
    for row in alertes_annee:
        c = row[7] if len(row) > 7 else "?"
        communes_vues[c] = communes_vues.get(c, 0) + 1
    top_commune = max(communes_vues, key=communes_vues.get) if communes_vues else None

    unsub_lien = get_unsub_link(email)
    magic_lien = get_magic_link(email)

    if nb_alertes == 0:
        msg_bilan = "Aucune mise à l'enquête n'a été détectée dans votre périmètre cette année. Votre surveillance est restée active tout au long de l'année."
        couleur = "#1a7a4a"
        emoji = "✅"
    else:
        msg_bilan = f"Cette année, <strong>{nb_alertes} mise{'s' if nb_alertes > 1 else ''} à l'enquête</strong> ont été détectées dans votre périmètre de surveillance."
        couleur = "#dc2626" if nb_alertes > 3 else "#f59e0b"
        emoji = "⚠️" if nb_alertes > 3 else "📬"

    html = (
        "<html><body style='font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333'>"
        "<div style='background:#1a3a5c;padding:18px 24px'>"
        "<h1 style='color:white;margin:0;font-size:20px'>PhémeApp</h1>"
        f"<p style='color:#a8c4e0;margin:4px 0 0;font-size:12px'>Bilan annuel {annee}</p>"
        "</div><div style='padding:24px'>"
        f"<p style='font-size:16px'>Bonjour {prenom},</p>"
        f"<p style='font-size:14px;color:#444;line-height:1.7'>Voici le bilan de votre surveillance PhémeApp pour l'année <strong>{annee}</strong>.</p>"
        f"<div style='background:#f0fdf4;border-left:3px solid {couleur};padding:16px 18px;margin:16px 0;border-radius:0 6px 6px 0'>"
        f"<p style='margin:0;font-size:14px;color:#0f4a2a'>{emoji} {msg_bilan}</p>"
        + (f"<p style='margin:8px 0 0;font-size:13px;color:#555'>Commune la plus active dans votre périmètre : <strong>{esc(top_commune)}</strong></p>" if top_commune else "")
        + "</div>"
        "<p style='font-size:14px;color:#444;line-height:1.7'>Merci de faire confiance à PhémeApp pour surveiller votre environnement. Votre surveillance continue en {annee+1}.</p>"
        f"<p style='font-size:13px;color:#888;margin-top:16px'><a href='{magic_lien}' style='color:#1a3a5c;font-weight:500'>Mon espace PhémeApp →</a></p>"
        "<p style='font-size:14px;color:#444;margin-top:20px'>Bien cordialement,<br><strong>L'équipe PhémeApp</strong></p>"
        f"<p style='font-size:11px;color:#aaa;border-top:1px solid #eee;padding-top:12px;margin-top:20px'>PhémeApp — service d'information automatisé. Il ne remplace pas une consultation juridique. &nbsp;<a href='{unsub_lien}' style='color:#bbb;font-size:10px'>Se désinscrire</a></p>"
        "</div></body></html>"
    )

    try:
        smtp_send(email, f"PhémeApp — Votre bilan {annee}", html)
        notified[key] = datetime.now().isoformat()
        log(f"  Bilan annuel {annee} envoyé -> {email} ({nb_alertes} alertes)")
    except Exception as e:
        log(f"  Erreur bilan annuel {email}: {e}", "error")


def send_weekly_summary(user, notified, enquetes):
    """
    IDEA-P12: Email chaque lundi si aucune alerte cette semaine.
    Montre les stats de la semaine : X dossiers analysés, aucun dans le périmètre.
    Ne s'envoie pas si une alerte a déjà été envoyée cette semaine.
    """
    # Seulement le lundi
    if datetime.now().weekday() != 0:
        return
    if not user.get('notif_hebdo', True):
        return  # IDEA-U07: désactivé par l'utilisateur

    email = user["email"]
    semaine = datetime.now().strftime("%Y-W%W")
    key_weekly = f"weekly:{email}:{semaine}"

    if key_weekly in notified:
        return

    # Vérifier si une alerte a été envoyée cette semaine
    key_prefix = f"notified:{email}:"
    alerte_cette_semaine = any(
        k.startswith(f"{email}:") and
        v >= (datetime.now() - timedelta(days=7)).isoformat()
        for k, v in notified.items()
        if isinstance(v, str)
    )
    if alerte_cette_semaine:
        return  # Déjà alerté cette semaine, pas de résumé

    prenom = esc(user["nom"].split()[0] if user["nom"] else "bonjour")
    total  = len(enquetes)

    # Stats zone élargie
    nb_zone = 0
    for adr in user["adresses"]:
        if not adr.get("lat"):
            continue
        for e in enquetes:
            if e.get("lat") and e.get("lng"):
                d = haversine_m(adr["lat"], adr["lng"], e["lat"], e["lng"])
                if 500 < d <= 2000:
                    nb_zone += 1

    html = (
        "<html><body style='font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333'>"
        "<div style='background:#1a3a5c;padding:18px 24px'>"
        "<h1 style='color:white;margin:0;font-size:20px'>PhémeApp</h1>"
        f"<p style='color:#a8c4e0;margin:4px 0 0;font-size:12px'>Résumé de la semaine — {datetime.now().strftime('%d %B %Y')}</p>"
        "</div><div style='padding:24px'>"
        f"<p style='font-size:16px'>Bonjour {prenom},</p>"
        "<div style='background:#f0fdf4;border-left:3px solid #1a7a4a;padding:14px 18px;margin:16px 0;border-radius:0 6px 6px 0'>"
        f"<strong style='color:#0f4a2a'>✅ Aucune mise à l'enquête dans votre périmètre cette semaine</strong>"
        "</div>"
        f"<p style='font-size:14px;color:#444;line-height:1.7'>Cette semaine, notre système a analysé <strong>{total} publications</strong> dans le canton de Vaud."
        + (f" <strong>{nb_zone} publication{'s' if nb_zone > 1 else ''}</strong> ont été détectées dans un rayon de 2km autour de vos adresses, mais aucune dans votre périmètre de surveillance de 500m." if nb_zone > 0 else " Aucune publication n'a été détectée dans un rayon de 2km autour de vos adresses.")
        + "</p>"
        "<p style='font-size:13px;color:#888;margin-top:20px'>Votre surveillance reste active. Vous recevrez une alerte dès qu'une mise à l'enquête sera publiée dans votre périmètre.</p>"
        "<p style='font-size:14px;color:#444;margin-top:20px'>Bien cordialement,<br><strong>L'équipe PhémeApp</strong></p>"
        "<p style='font-size:11px;color:#aaa;border-top:1px solid #eee;padding-top:12px;margin-top:20px'>"
        "PhémeApp — service d'information automatisé. Il ne remplace pas une consultation juridique. "
        "&nbsp;<a href='{unsub_lien}' style='color:#bbb;font-size:10px'>Se désinscrire</a></p>"
        "</div></body></html>"
    )

    try:
        smtp_send(email, f"PhémeApp — Semaine du {datetime.now().strftime('%d %B')}: aucune alerte", html)
        notified[key_weekly] = datetime.now().isoformat()
        log(f"  Résumé hebdo envoyé -> {email}")
    except Exception as e:
        log(f"  Erreur résumé hebdo {email}: {e}", "error")


def generate_unsub_token(email):
    """Génère un token unique de désinscription pour l'email donné."""
    import hashlib
    secret = "phemeapp-unsub-2026"
    return hashlib.sha256(f"{email}{secret}".encode()).hexdigest()[:16]

def get_unsub_link(email):
    """
    IDEA-P06: Retourne le lien de désinscription personnalisé.
    MVP: mailto avec token pour vérification manuelle.
    Future: URL vers Apps Script Web App qui supprime automatiquement du Sheet.
    """
    token = generate_unsub_token(email)
    apps_url = APPS_SCRIPT_WEBAPP_URL
    if apps_url:
        # Désinscription automatique via Apps Script Web App
        return f"{apps_url}?action=unsubscribe&email={requests.utils.quote(email)}&token={token}"
    else:
        # Fallback: mailto
        import urllib.parse
        subject = urllib.parse.quote(f"Désinscription PhémeApp [{token}]")
        return f"mailto:alerte@phemeapp.ch?subject={subject}"

def handle_unsubscribe_in_sheet(email):
    """
    IDEA-P06: Marque l'utilisateur comme désinscrit dans le Sheet.
    Appelé par Apps Script Web App quand l'utilisateur clique sur le lien.
    """
    try:
        if not APPS_SCRIPT_WEBAPP_URL:
            return False
        resp = requests.post(
            APPS_SCRIPT_WEBAPP_URL,
            json={"action": "unsubscribe", "email": email},
            timeout=10
        )
        return resp.status_code == 200
    except Exception as e:
        log(f"  Erreur désinscription {email}: {e}", "error")
        return False


MAGIC_LINK_BASE = os.environ.get("MAGIC_LINK_BASE", "https://phemeapp.ch/mon-compte")
MAGIC_LINK_SECRET = os.environ.get("MAGIC_LINK_SECRET", "phemeapp-magic-2026")

def get_tracking_pixel(email, no_camac):
    """
    IDEA-P14: Pixel de tracking 1x1 transparent.
    Si APPS_SCRIPT_WEBAPP_URL configuré → Apps Script log l'ouverture.
    Sinon → pixel transparent générique (ne log rien).
    """
    if APPS_SCRIPT_WEBAPP_URL:
        from urllib.parse import quote as _q
        token = generate_unsub_token(email)
        return (f'<img src="{APPS_SCRIPT_WEBAPP_URL}'
                f'?action=track&email={_q(email)}&camac={no_camac}&token={token}" '
                f'width="1" height="1" style="display:none" alt="">')
    return '<img src="https://phemeapp.ch/pixel.gif" width="1" height="1" style="display:none" alt="">'


def generate_magic_token(email):
    """
    IDEA-U01 + SEC-09: Token magic link HMAC-SHA256 valide 30 jours.
    HMAC résiste aux attaques par extension de longueur vs SHA256 simple.
    """
    import hashlib, hmac
    mois = datetime.now().strftime("%Y-%m")
    raw = f"{email}:{mois}"
    return hmac.new(
        MAGIC_LINK_SECRET.encode("utf-8"),
        raw.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()[:32]

def get_magic_link(email):
    """Retourne l'URL complète de l'espace utilisateur."""
    from urllib.parse import quote as _quote
    token = generate_magic_token(email)
    return f"{MAGIC_LINK_BASE}?token={token}&email={_quote(email)}"

def verify_magic_token(email, token):
    """Vérifie qu'un token est valide pour cet email (mois courant ou précédent)."""
    for delta in [0, -1]:
        from datetime import date
        import calendar
        d = datetime.now()
        # Reculer d'un mois si delta = -1
        if delta == -1:
            if d.month == 1:
                d = d.replace(year=d.year-1, month=12)
            else:
                d = d.replace(month=d.month-1)
        mois = d.strftime("%Y-%m")
        import hashlib, hmac
        raw = f"{email}:{mois}"
        expected = hmac.new(
            MAGIC_LINK_SECRET.encode("utf-8"),
            raw.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()[:32]
        if token == expected:
            return True
    return False


def generate_admin_dashboard(notified, users, enquetes):
    """
    IDEA-T14: Génère une page HTML de dashboard admin.
    Stockée dans logs/dashboard.html — disponible dans GitHub Actions Artifacts.
    Stats: nb utilisateurs, alertes envoyées, communes actives, etc.
    """
    try:
        nb_users    = len(users)
        # IDEA-T15: tendances CAMAC
        types_travaux = {}
        for e in enquetes:
            nature = (e.get("natureTravaux") or "Autre")[:40]
            types_travaux[nature] = types_travaux.get(nature, 0) + 1
        top_types = sorted(types_travaux.items(), key=lambda x: x[1], reverse=True)[:8]
        nb_alertes  = sum(1 for k in notified if ":" in k and not k.startswith("monthly:") and not k.startswith("weekly:") and not k.startswith("rappel7:") and not k.startswith("newsletter_zone:") and not k.endswith(":ctx") and "welcome:" not in k)
        nb_enquetes = len(enquetes)
        communes    = {}
        for e in enquetes:
            c = e.get("commune", "?")
            communes[c] = communes.get(c, 0) + 1
        top_communes = sorted(communes.items(), key=lambda x: x[1], reverse=True)[:10]

        communes_rows = "".join(
            f"<tr><td style='padding:8px'>{esc(c)}</td><td style='padding:8px;text-align:right;font-weight:bold'>{n}</td></tr>"
            for c, n in top_communes
        )

        users_rows = "".join(
            f"<tr><td style='padding:8px;font-size:13px'>{esc(u['email'])}</td>"
            f"<td style='padding:8px;font-size:13px'>{esc(u['nom'])}</td>"
            f"<td style='padding:8px;font-size:13px'>{len(u['adresses'])} adresse(s)</td></tr>"
            for u in users
        )

        html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>PhémeApp — Dashboard Admin</title>
<style>
  body {{ font-family: Arial, sans-serif; background: #f0f4f8; color: #333; padding: 24px; }}
  h1 {{ color: #1a3a5c; }} h2 {{ color: #1a3a5c; font-size: 16px; margin: 24px 0 12px; }}
  .grid {{ display: flex; gap: 16px; flex-wrap: wrap; margin: 20px 0; }}
  .stat {{ background: white; border-radius: 8px; padding: 20px 24px; min-width: 140px; box-shadow: 0 2px 6px rgba(0,0,0,0.08); }}
  .stat-value {{ font-size: 36px; font-weight: bold; color: #1a3a5c; }}
  .stat-label {{ font-size: 13px; color: #888; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 6px rgba(0,0,0,0.08); }}
  th {{ background: #1a3a5c; color: white; padding: 10px 8px; text-align: left; font-size: 13px; }}
  tr:nth-child(even) {{ background: #f9f9f9; }}
  .footer {{ font-size: 11px; color: #aaa; margin-top: 32px; }}
</style>
</head>
<body>
<h1>PhémeApp — Dashboard Admin</h1>
<p style="color:#888;font-size:13px">Généré le {datetime.now().strftime('%d.%m.%Y à %H:%M')} — Run automatique GitHub Actions</p>
<div class="grid">
  <div class="stat"><div class="stat-value">{nb_users}</div><div class="stat-label">Utilisateurs actifs</div></div>
  <div class="stat"><div class="stat-value">{nb_alertes}</div><div class="stat-label">Alertes envoyées (total)</div></div>
  <div class="stat"><div class="stat-value">{nb_enquetes}</div><div class="stat-label">Publications CAMAC (30j)</div></div>
  <div class="stat"><div class="stat-value">{len(communes)}</div><div class="stat-label">Communes actives</div></div>
</div>
<h2>Top communes (30 derniers jours)</h2>
<table><tr><th>Commune</th><th style="text-align:right">Publications</th></tr>{communes_rows}</table>
<h2>Utilisateurs</h2>
<table><tr><th>Email</th><th>Nom</th><th>Adresses</th></tr>{users_rows}</table>
<div class="footer">PhémeApp — Confidentiel. Ne pas partager.</div>
</body></html>"""

        Path("logs").mkdir(exist_ok=True)
        with open("logs/dashboard.html", "w", encoding="utf-8") as f:
            f.write(html)
        log("Dashboard admin généré: logs/dashboard.html")

    except Exception as e:
        log(f"Erreur génération dashboard: {e}", "error")


def check_dossiers_retires(user, notified, enquetes):
    """
    IDEA-P19: Vérifie si un dossier alerté précédemment a disparu de CAMAC.
    Si un noCamac notifié n'est plus dans les enquêtes actuelles ET
    que le délai de 30j n'est pas encore écoulé → notifier le retrait.
    """
    email = user["email"]
    camac_actuels = {str(e.get("noCamac")) for e in enquetes}

    for key, val in list(notified.items()):
        # Format clé: email:noCamac
        if not key.startswith(f"{email}:"):
            continue
        parts = key.split(":")
        if len(parts) != 2:
            continue
        no_camac = parts[1]
        if not no_camac.isdigit():
            continue

        key_retire = f"retire:{email}:{no_camac}"
        if key_retire in notified:
            continue  # Déjà notifié du retrait

        # Vérifier si le dossier était récent (< 35 jours)
        try:
            date_alerte = datetime.fromisoformat(val)
            jours = (datetime.now() - date_alerte).days
            if jours > 35:
                continue  # Trop vieux, normal qu'il soit parti
        except Exception:
            continue

        # Le dossier n'est plus dans CAMAC
        if no_camac not in camac_actuels:
            prenom = user["nom"].split()[0] if user["nom"] else "bonjour"
            unsub_lien = get_unsub_link(email)

            html = (
                "<html><body style='font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333'>"
                "<div style='background:#1a3a5c;padding:18px 24px'>"
                "<h1 style='color:white;margin:0;font-size:20px'>PhémeApp</h1>"
                "<p style='color:#a8c4e0;margin:4px 0 0;font-size:12px'>Mise à jour d'un dossier</p>"
                "</div><div style='padding:24px'>"
                f"<p style='font-size:16px'>Bonjour {prenom},</p>"
                f"<div style='background:#f0fdf4;border-left:3px solid #1a7a4a;padding:14px 18px;margin:16px 0;border-radius:0 6px 6px 0'>"
                f"<strong style='color:#0f4a2a'>✅ Dossier retiré — No CAMAC {no_camac}</strong>"
                f"<p style='margin:8px 0 0;font-size:13px;color:#1a5c35;line-height:1.6'>La mise à l'enquête que nous vous avions signalée (No CAMAC {no_camac}) n'apparaît plus dans les publications officielles. Le projet a probablement été retiré ou clôturé.</p>"
                "</div>"
                "<p style='font-size:14px;color:#444;line-height:1.7'>Si vous avez déposé une opposition ou si vous avez des questions, nous vous recommandons de contacter directement votre commune pour confirmation.</p>"
                "<p style='font-size:14px;color:#444;margin-top:16px'>Bien cordialement,<br><strong>L'équipe PhémeApp</strong></p>"
                f"<p style='font-size:11px;color:#aaa;border-top:1px solid #eee;padding-top:12px;margin-top:20px'>PhémeApp — service d'information automatisé. Il ne remplace pas une consultation juridique. &nbsp;<a href='{unsub_lien}' style='color:#bbb;font-size:10px'>Se désinscrire</a></p>"
                "</div></body></html>"
            )

            try:
                smtp_send(email, f"PhémeApp — Dossier retiré (No {no_camac})", html)
                notified[key_retire] = datetime.now().isoformat()
                log(f"  Retrait dossier notifié -> {email} (CAMAC {no_camac})")
            except Exception as e:
                log(f"  Erreur notification retrait {email}: {e}", "error")


CAMAC_CACHE_TTL_DAYS = 35  # Garder le cache 35 jours (légèrement > délai légal)

def get_cached_camac_ids(notified):
    """IDEA-T03: Retourne les noCamac déjà connus (toutes adresses confondues)."""
    cached = set()
    cutoff = (datetime.now() - timedelta(days=CAMAC_CACHE_TTL_DAYS)).isoformat()
    for key, val in notified.items():
        if ":" not in key or key.startswith("welcome:") or key.startswith("monthly:"):
            continue
        parts = key.split(":")
        if len(parts) >= 2 and parts[-1].isdigit():
            if isinstance(val, str) and val > cutoff:
                cached.add(parts[-1])
    return cached

def is_new_enquete(no_camac, notified):
    """True si cette enquête n'a jamais été vue (pour les stats uniquement — pas de filtre)."""
    return str(no_camac) not in get_cached_camac_ids(notified)


DATA_RETENTION_DAYS = 730  # SEC-07: rétention max 2 ans (nLPD suisse)

def purge_old_notified(notified, max_days=90):
    """
    IDEA-T16 + SEC-07: Supprime les entrées de plus de max_days jours dans notified.
    Garde toutes les clés welcome: et monthly: (pas de date limite).
    Réduit la taille du fichier au fil du temps.
    """
    cutoff = (datetime.now() - timedelta(days=max_days)).isoformat()
    keys_before = len(notified)
    to_delete = []
    for key, val in notified.items():
        # Garder les clés permanentes
        if key.startswith("welcome:") or key.startswith("monthly:") or key.startswith("newsletter_zone:"):
            continue
        # Supprimer les entrées trop anciennes
        if isinstance(val, str) and val < cutoff and not key.endswith(":ctx"):
            to_delete.append(key)
            # Supprimer aussi le contexte associé
            to_delete.append(key + ":ctx")
    for key in to_delete:
        notified.pop(key, None)
    purged = keys_before - len(notified)
    if purged > 0:
        log(f"notified.json: {purged} entrées purgées (>{max_days}j), {len(notified)} restantes")
    return notified


def ping_healthcheck(fail=False):
    """IDEA-T05: Ping healthcheck.io pour monitorer le cron."""
    if not HEALTHCHECK_URL:
        return
    try:
        url = HEALTHCHECK_URL + ("/fail" if fail else "")
        requests.get(url, timeout=5)
        log("Healthcheck ping: OK" + (" (FAIL)" if fail else ""))
    except Exception:
        pass

def send_admin_alert(subject, body):
    """IDEA-T05: Email d alerte a l admin si le script plante."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[PhemeApp ERREUR] {subject}"
        msg["From"]    = f"{BREVO_SENDER_NAME} <{BREVO_SENDER}>"
        msg["To"]      = ADMIN_EMAIL
        msg.attach(MIMEText(f"<pre style='font-family:monospace'>{body}</pre>", "html", "utf-8"))
        with smtplib.SMTP("smtp-relay.brevo.com", 587) as srv:
            srv.starttls()
            srv.login(BREVO_SMTP_LOGIN, BREVO_API_KEY)
            srv.sendmail(BREVO_SENDER, ADMIN_EMAIL, msg.as_string())
        log(f"Admin alert envoyee: {subject}")
    except Exception as e:
        log(f"Impossible d envoyer admin alert: {e}")


def run():
    log("=" * 50)
    log(f"PhémeApp v2.1 — démarrage")
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

    # IDEA-P02: newsletter zone élargie
    for user in users:
        send_zone_elargie_newsletter(user, notified)
    # IDEA-P20: bilan annuel (janvier)
    for user in users:
        send_annual_summary(user, notified)

    # IDEA-P03: rapport mensuel AVANT fetch (basé sur historique Sheet, indépendant API CAMAC)
    log("Rapports mensuels depuis historique Sheet...")
    for user in users:
        send_monthly_confirmation(user, notified)

    log(f"Récupération des mises à l'enquête ({SEARCH_DAYS}j)...")
    enquetes = fetch_enquetes_with_retry()

    if not enquetes:
        log("Aucune mise à l'enquête récupérée — fin.")
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
                # IDEA-T13: vérifier les doublons par similarité (prolongations)
                if already_notified_similar(notified, user["email"], enquete):
                    continue
                dist = haversine_m(adr["lat"], adr["lng"], enquete["lat"], enquete["lng"])
                if dist <= PERIMETER_M:
                    log(f"  MATCH! CAMAC {no_camac} à {round(dist)}m de '{adr['label']}' ({user['email']})")
                    if send_email(user["email"], user["nom"], enquete, adr, dist, user.get("profil", "")):
                        mark_notified_with_context(notified, user["email"], no_camac, enquete)
                        log_alerte_historique(user, adr, enquete, dist)
                        total += 1
                elif PERIMETER_M < dist <= PERIMETER_LARGE_M:
                    zone_key = f"zone:{user['email']}:{no_camac}"
                    if zone_key not in notified:
                        log_zone_elargie(user, adr, enquete, dist)
                        notified[zone_key] = datetime.now().isoformat()

    # IDEA-T16: purger les vieilles entrées notified
    notified = purge_old_notified(notified)

    # IDEA-T14: dashboard admin
    generate_admin_dashboard(notified, users, enquetes)

    save_notified(notified)
    log("=" * 50)
    log(f"PhémeApp — terminé. {total} alerte(s) envoyée(s).")
    log("=" * 50)


if __name__ == "__main__":
    import traceback as _tb, sys as _sys
    try:
        run()
        ping_healthcheck()
    except Exception as _e:
        _err = _tb.format_exc()
        print("=" * 60, flush=True)
        print(f"ERREUR CRITIQUE: {_e}", flush=True)
        print(_err, flush=True)
        print("=" * 60, flush=True)
        try:
            log(f"ERREUR CRITIQUE: {_e}")
            log(_err)
            ping_healthcheck("fail")
            send_admin_alert(f"Erreur critique: {_e}", _err)
        except Exception as _e2:
            print(f"Impossible d envoyer admin alert: {_e2}", flush=True)
        _sys.exit(1)


# ─────────────────────────────────────────────
# HISTORIQUE — GOOGLE SHEET
# ─────────────────────────────────────────────

SHEET_HISTORIQUE  = "Historique Alertes"
SHEET_ZONE        = "Zone Elargie"
PERIMETER_LARGE_M = 2000  # Zone élargie : 500m à 2km

