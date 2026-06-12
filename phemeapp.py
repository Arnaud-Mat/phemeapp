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
import os
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
    try:
        jours_restants = max(0, (datetime.fromtimestamp(enquete.get("dateFao",0)/1000) + timedelta(days=30) - datetime.now()).days)
        urgence = jours_restants <= 7
    except:
        jours_restants = 30
        urgence = False
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
          <strong style="color:{'#991b1b' if urgence else '#92400e'}">{'⚠️ URGENT — ' if urgence else '⏱ '}{jours_restants} jour{'s' if jours_restants > 1 else ''} pour faire opposition</strong><br>
          <span style="font-size:13px;color:{'#991b1b' if urgence else '#92400e'}">Date FAO : <strong>{date_fao}</strong> — d&eacute;lai l&eacute;gal de 30 jours.</span>
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
          PhémeApp est un service d'information automatisé. Il ne remplace pas un avis juridique. &nbsp;<a href='mailto:alerte@phemeapp.ch?subject=D%C3%A9sinscription%20Ph%C3%A9meApp' style='color:#bbb;font-size:10px'>Se désinscrire</a>
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
    """Trouve le nom de la commune depuis des coordonnees GPS via Nominatim."""
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
            except:
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
    except:
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
    except:
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


def send_monthly_confirmation(user, notified, enquetes):
    """
    IDEA-P03: Email mensuel confirmant que la surveillance est active.
    Montre les publications les plus proches ce mois. Max 1x/mois/utilisateur.
    """
    email = user["email"]
    mois  = datetime.now().strftime("%Y-%m")
    key   = f"monthly:{email}:{mois}"
    if key in notified:
        return

    prenom = user["nom"].split()[0] if user["nom"] else "bonjour"
    total  = len(enquetes)

    # 5 publications les plus proches toutes adresses confondues
    proches = []
    for adr in user["adresses"]:
        if not adr.get("lat"):
            continue
        for e in enquetes:
            if e.get("lat") and e.get("lng"):
                d = haversine_m(adr["lat"], adr["lng"], e["lat"], e["lng"])
                if d <= 2000:
                    proches.append((d, e, adr["label"]))
    proches.sort(key=lambda x: x[0])
    proches = proches[:5]

    alerte_500 = sum(1 for d, _, _ in proches if d <= 500)
    couleur_header = "#dc2626" if alerte_500 else "#1a7a4a"
    msg_statut = (
        f"⚠️ {alerte_500} publication(s) dans votre périmètre ce mois"
        if alerte_500 else
        "✅ Aucune mise à l'enquête dans votre périmètre ce mois"
    )

    rows_html = ""
    for dist, e, label in proches:
        commune  = e.get("commune", "--")
        nature   = (e.get("natureTravaux") or "--")[:50]
        date_fao = format_date(e.get("dateFao", 0))
        col      = "#dc2626" if dist <= 500 else "#f59e0b" if dist <= 1000 else "#888"
        rows_html += (
            f"<tr style='border-bottom:1px solid #eee'>"
            f"<td style='padding:7px 8px;color:{col};font-weight:bold'>{round(dist)}m</td>"
            f"<td style='padding:7px 8px'>{commune}</td>"
            f"<td style='padding:7px 8px;font-size:12px;color:#666'>{nature}</td>"
            f"<td style='padding:7px 8px;font-size:12px'>{date_fao}</td>"
            f"<td style='padding:7px 8px;font-size:11px;color:#888'>{label}</td>"
            f"</tr>"
        )

    html = (
        "<html><body style='font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333'>"
        "<div style='background:#1a3a5c;padding:18px 24px'>"
        "<h1 style='color:white;margin:0;font-size:20px'>PhémeApp</h1>"
        f"<p style='color:#a8c4e0;margin:4px 0 0;font-size:12px'>Rapport mensuel — {datetime.now().strftime('%B %Y')}</p>"
        "</div><div style='padding:24px'>"
        f"<p style='font-size:16px'>Bonjour {prenom},</p>"
        f"<p style='font-size:14px;color:#444;line-height:1.7'>Votre surveillance PhémeApp est <strong style='color:#1a7a4a'>active</strong>. "
        f"Ce mois, notre système a analysé <strong>{total} mises à l'enquête</strong> dans le canton de Vaud.</p>"
        f"<div style='background:#f0fdf4;border-left:3px solid {couleur_header};padding:14px 18px;margin:16px 0;border-radius:0 6px 6px 0'>"
        f"<strong style='color:{couleur_header}'>{msg_statut}</strong></div>"
    )

    if proches:
        html += (
            "<p style='font-size:14px;color:#444;margin-top:16px'>Publications les plus proches ce mois :</p>"
            "<table style='width:100%;border-collapse:collapse;font-size:13px'>"
            "<tr style='background:#f0f4f8'>"
            "<th style='padding:7px 8px;text-align:left'>Distance</th>"
            "<th style='padding:7px 8px;text-align:left'>Commune</th>"
            "<th style='padding:7px 8px;text-align:left'>Nature</th>"
            "<th style='padding:7px 8px;text-align:left'>FAO</th>"
            "<th style='padding:7px 8px;text-align:left'>Adresse</th></tr>"
            + rows_html + "</table>"
        )
    else:
        html += "<p style='font-size:14px;color:#666'>Aucune publication détectée dans un rayon de 2km.</p>"

    html += (
        "<p style='font-size:14px;color:#444;margin-top:20px'>Bien cordialement,<br>"
        "<strong>L'équipe PhémeApp</strong></p>"
        "<p style='font-size:11px;color:#aaa;border-top:1px solid #eee;padding-top:12px;margin-top:20px'>"
        "PhémeApp — service d'information automatisé. Il ne remplace pas une consultation juridique. &nbsp;&nbsp;<a href='mailto:alerte@phemeapp.ch?subject=D%C3%A9sinscription%20Ph%C3%A9meApp' style='color:#bbb;font-size:10px'>Se désinscrire</a></p>"
        "</div></body></html>"
    )

    try:
        smtp_send(email, f"PhémeApp — Rapport {datetime.now().strftime('%B %Y')}", html)
        notified[key] = datetime.now().isoformat()
        log(f"  Rapport mensuel envoyé -> {email}")
    except Exception as e:
        log(f"  Erreur rapport mensuel {email}: {e}")


HEALTHCHECK_URL = os.environ.get("HEALTHCHECK_URL", "")
ADMIN_EMAIL     = os.environ.get("ADMIN_EMAIL", "arnaud.mathier@gmail.com")

def ping_healthcheck(fail=False):
    """IDEA-T05: Ping healthcheck.io pour monitorer le cron."""
    if not HEALTHCHECK_URL:
        return
    try:
        url = HEALTHCHECK_URL + ("/fail" if fail else "")
        requests.get(url, timeout=5)
        log("Healthcheck ping: OK" + (" (FAIL)" if fail else ""))
    except:
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

    # IDEA-P03: rapport mensuel
    log("Rapports mensuels...")
    for user in users:
        send_monthly_confirmation(user, notified, enquetes)

    save_notified(notified)
    log("=" * 50)
    log(f"PhémeApp — terminé. {total} alerte(s) envoyée(s).")
    log("=" * 50)


if __name__ == "__main__":
    try:
        run()
        ping_healthcheck()  # succes
    except Exception as e:
        import traceback
        err = traceback.format_exc()
        log(f"ERREUR CRITIQUE: {e}")
        log(err)
        ping_healthcheck("fail")
        send_admin_alert(f"Erreur critique: {e}", err)
        raise


# ─────────────────────────────────────────────
# HISTORIQUE — GOOGLE SHEET
# ─────────────────────────────────────────────

SHEET_HISTORIQUE  = "Historique Alertes"
SHEET_ZONE        = "Zone Elargie"
PERIMETER_LARGE_M = 2000  # Zone élargie : 500m à 2km

