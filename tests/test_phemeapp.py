"""
PhémeApp — Tests unitaires (IDEA-T07)
pytest tests/test_phemeapp.py
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
import sys, os

# Configurer les variables d'environnement avant l'import
os.environ.setdefault("SHEET_ID", "test_sheet_id")
os.environ.setdefault("BREVO_SMTP_LOGIN", "test@test.com")
os.environ.setdefault("BREVO_API_KEY", "test_key")
os.environ.setdefault("BREVO_SENDER", "test@phemeapp.ch")
os.environ.setdefault("GITHUB_TOKEN", "test_token")
os.environ.setdefault("GITHUB_REPOSITORY", "test/repo")


# ─────────────────────────────────────────────
# IMPORT DU MODULE AVEC MOCKS
# ─────────────────────────────────────────────

@pytest.fixture(autouse=True)
def mock_network(monkeypatch):
    """Mock tous les appels réseau pour les tests unitaires."""
    import phemeapp
    monkeypatch.setattr(phemeapp, "smtp_send", lambda *a, **kw: None)
    monkeypatch.setattr(phemeapp, "save_notified", lambda n: None)
    monkeypatch.setattr(phemeapp, "ping_healthcheck", lambda *a, **kw: None)
    monkeypatch.setattr(phemeapp, "send_admin_alert", lambda *a, **kw: None)


# ─────────────────────────────────────────────
# TESTS haversine_m
# ─────────────────────────────────────────────

def test_haversine_meme_point():
    """Distance entre un point et lui-même = 0."""
    import phemeapp
    assert phemeapp.haversine_m(46.5126, 6.5299, 46.5126, 6.5299) == pytest.approx(0, abs=1)

def test_haversine_preverenges_lausanne():
    """Distance Préverenges → Lausanne ≈ 8km."""
    import phemeapp
    d = phemeapp.haversine_m(46.5126, 6.5299, 46.5197, 6.6323)
    assert 7000 < d < 9000, f"Distance inattendue: {d}m"

def test_haversine_500m():
    """Test avec deux points à environ 500m."""
    import phemeapp
    # ~500m vers le nord depuis Préverenges
    d = phemeapp.haversine_m(46.5126, 6.5299, 46.5171, 6.5299)
    assert 450 < d < 550, f"Distance inattendue: {d}m"


# ─────────────────────────────────────────────
# TESTS format_date
# ─────────────────────────────────────────────

def test_format_date_timestamp_valide():
    """Timestamp valide → date formatée."""
    import phemeapp
    ts = int(datetime(2026, 6, 1).timestamp() * 1000)
    result = phemeapp.format_date(ts)
    assert "2026" in result
    assert "06" in result or "6" in result

def test_format_date_zero():
    """Timestamp 0 → chaîne non vide."""
    import phemeapp
    result = phemeapp.format_date(0)
    assert isinstance(result, str)
    assert len(result) > 0

def test_format_date_recent():
    """Timestamp récent → date d'aujourd'hui."""
    import phemeapp
    ts = int(datetime.now().timestamp() * 1000)
    result = phemeapp.format_date(ts)
    assert str(datetime.now().year) in result


# ─────────────────────────────────────────────
# TESTS generate_unsub_token
# ─────────────────────────────────────────────

def test_unsub_token_longueur():
    """Token de désinscription = 16 caractères."""
    import phemeapp
    token = phemeapp.generate_unsub_token("test@test.com")
    assert len(token) == 16

def test_unsub_token_deterministe():
    """Même email → même token."""
    import phemeapp
    t1 = phemeapp.generate_unsub_token("arnaud@test.com")
    t2 = phemeapp.generate_unsub_token("arnaud@test.com")
    assert t1 == t2

def test_unsub_token_unique():
    """Emails différents → tokens différents."""
    import phemeapp
    t1 = phemeapp.generate_unsub_token("alice@test.com")
    t2 = phemeapp.generate_unsub_token("bob@test.com")
    assert t1 != t2


# ─────────────────────────────────────────────
# TESTS already_notified / mark_notified
# ─────────────────────────────────────────────

def test_already_notified_vide():
    """Dictionnaire vide → pas encore notifié."""
    import phemeapp
    assert not phemeapp.already_notified({}, "test@test.com", 12345)

def test_already_notified_apres_mark():
    """Après mark_notified, already_notified retourne True."""
    import phemeapp
    n = {}
    phemeapp.mark_notified(n, "test@test.com", 12345)
    assert phemeapp.already_notified(n, "test@test.com", 12345)

def test_already_notified_autre_camac():
    """CAMAC différent → pas notifié."""
    import phemeapp
    n = {}
    phemeapp.mark_notified(n, "test@test.com", 12345)
    assert not phemeapp.already_notified(n, "test@test.com", 99999)

def test_is_new_user():
    """is_new_user: vrai si pas de clé welcome."""
    import phemeapp
    n = {}
    assert phemeapp.is_new_user(n, "test@test.com")
    phemeapp.mark_welcome_sent(n, "test@test.com")
    assert not phemeapp.is_new_user(n, "test@test.com")


# ─────────────────────────────────────────────
# TESTS send_rappel_j7 (logique de filtrage)
# ─────────────────────────────────────────────

def test_rappel_j7_pas_envoye_si_hors_perimetre():
    """Pas de rappel si l'enquête est à plus de 500m."""
    import phemeapp

    emails_envoyes = []
    with patch.object(phemeapp, "smtp_send", side_effect=lambda d,s,h: emails_envoyes.append(d)):
        user = {
            "email": "test@test.com",
            "nom": "Test",
            "adresses": [{"label": "Maison", "adresse": "Test", "lat": 46.5126, "lng": 6.5299}]
        }
        # Enquête à 1000m (hors périmètre 500m)
        ts = int((datetime.now() - timedelta(days=25)).timestamp() * 1000)
        enquete = {"noCamac": 1, "lat": 46.5216, "lng": 6.5299, "dateFao": ts,
                   "lieu": "Test", "commune": "TestVille", "natureTravaux": "Test"}
        phemeapp.send_rappel_j7(user, {}, [enquete])

    assert len(emails_envoyes) == 0

def test_rappel_j7_envoye_si_j7():
    """Rappel envoyé si enquête à <500m et J-7."""
    import phemeapp

    emails_envoyes = []
    with patch.object(phemeapp, "smtp_send", side_effect=lambda d,s,h: emails_envoyes.append(d)):
        with patch.object(phemeapp, "find_commune_enquetes_url", return_value=None):
            user = {
                "email": "test@test.com",
                "nom": "Test User",
                "adresses": [{"label": "Maison", "adresse": "Test", "lat": 46.5126, "lng": 6.5299}]
            }
            # Enquête à ~100m, publiée il y a 23 jours (J-7)
            ts = int((datetime.now() - timedelta(days=23)).timestamp() * 1000)
            enquete = {"noCamac": 42, "lat": 46.5130, "lng": 6.5299, "dateFao": ts,
                       "lieu": "Test", "commune": "TestVille", "natureTravaux": "Test"}
            phemeapp.send_rappel_j7(user, {}, [enquete])

    assert len(emails_envoyes) == 1
    assert "test@test.com" in emails_envoyes

def test_rappel_j7_pas_doublon():
    """Rappel pas envoyé deux fois pour le même dossier."""
    import phemeapp
    emails_envoyes = []
    with patch.object(phemeapp, "smtp_send", side_effect=lambda d,s,h: emails_envoyes.append(d)):
        with patch.object(phemeapp, "find_commune_enquetes_url", return_value=None):
            user = {
                "email": "test@test.com",
                "nom": "Test",
                "adresses": [{"label": "Maison", "adresse": "Test", "lat": 46.5126, "lng": 6.5299}]
            }
            ts = int((datetime.now() - timedelta(days=23)).timestamp() * 1000)
            enquete = {"noCamac": 42, "lat": 46.5130, "lng": 6.5299, "dateFao": ts,
                       "lieu": "Test", "commune": "TestVille", "natureTravaux": "Test"}
            notified = {}
            phemeapp.send_rappel_j7(user, notified, [enquete])
            phemeapp.send_rappel_j7(user, notified, [enquete])  # 2ème fois

    assert len(emails_envoyes) == 1  # envoyé une seule fois


# ─────────────────────────────────────────────
# TESTS IDEA-T13 : Déduplication intelligente
# ─────────────────────────────────────────────

def test_already_notified_similar_vide():
    """Dictionnaire vide → pas de doublon."""
    import phemeapp
    enquete = {"lieu": "Chemin du Test", "commune": "Testville", "noCamac": 1}
    assert not phemeapp.already_notified_similar({}, "test@test.com", enquete)

def test_already_notified_similar_detecte():
    """Même lieu + commune récent → doublon détecté."""
    import phemeapp
    from datetime import datetime
    notified = {
        "test@test.com:99999": datetime.now().isoformat(),
        "test@test.com:99999:ctx": "chemin du test|testville"
    }
    enquete = {"lieu": "Chemin du Test", "commune": "Testville", "noCamac": 11111}
    assert phemeapp.already_notified_similar(notified, "test@test.com", enquete)

def test_already_notified_similar_commune_differente():
    """Même lieu mais commune différente → pas de doublon."""
    import phemeapp
    from datetime import datetime
    notified = {
        "test@test.com:99999": datetime.now().isoformat(),
        "test@test.com:99999:ctx": "chemin du test|autrevillage"
    }
    enquete = {"lieu": "Chemin du Test", "commune": "Testville", "noCamac": 11111}
    assert not phemeapp.already_notified_similar(notified, "test@test.com", enquete)

# ─────────────────────────────────────────────
# TESTS IDEA-U01 : Magic link
# ─────────────────────────────────────────────

def test_magic_token_longueur():
    """Token magic link = 32 caractères."""
    import phemeapp
    token = phemeapp.generate_magic_token("test@test.com")
    assert len(token) == 32

def test_magic_token_deterministe():
    """Même email → même token ce mois."""
    import phemeapp
    t1 = phemeapp.generate_magic_token("arnaud@test.com")
    t2 = phemeapp.generate_magic_token("arnaud@test.com")
    assert t1 == t2

def test_verify_magic_token_valide():
    """Token du mois courant est valide."""
    import phemeapp
    email = "test@test.com"
    token = phemeapp.generate_magic_token(email)
    assert phemeapp.verify_magic_token(email, token)

def test_verify_magic_token_invalide():
    """Token aléatoire est invalide."""
    import phemeapp
    assert not phemeapp.verify_magic_token("test@test.com", "token_invalide_xxxx")

def test_get_magic_link_format():
    """Magic link contient le token et l'email."""
    import phemeapp
    email = "arnaud@test.com"
    link = phemeapp.get_magic_link(email)
    assert "token=" in link
    assert "email=" in link

# ─────────────────────────────────────────────
# TESTS IDEA-T03 : fetch_enquetes_with_retry
# ─────────────────────────────────────────────

def test_fetch_retry_retourne_resultat():
    """fetch_enquetes_with_retry retourne le résultat si API OK."""
    import phemeapp
    from unittest.mock import patch
    with patch.object(phemeapp, "fetch_enquetes", return_value=[{"noCamac": 1}]):
        result = phemeapp.fetch_enquetes_with_retry()
    assert len(result) == 1

def test_fetch_retry_retente_si_vide():
    """Retry si premier appel retourne vide."""
    import phemeapp
    from unittest.mock import patch
    calls = []
    def mock_fetch(days=30):
        calls.append(1)
        return [] if len(calls) < 2 else [{"noCamac": 1}]
    with patch.object(phemeapp, "fetch_enquetes", side_effect=mock_fetch):
        result = phemeapp.fetch_enquetes_with_retry(delay=0)
    assert len(result) == 1
    assert len(calls) == 2


# ─────────────────────────────────────────────
# TESTS IDEA-T16 : Purge notified.json
# ─────────────────────────────────────────────

def test_purge_old_notified_vide():
    """Dictionnaire vide reste vide après purge."""
    import phemeapp
    result = phemeapp.purge_old_notified({})
    assert result == {}

def test_purge_old_notified_garde_recents():
    """Les entrées récentes ne sont pas supprimées."""
    import phemeapp
    from datetime import datetime
    notified = {"test@test.com:12345": datetime.now().isoformat()}
    result = phemeapp.purge_old_notified(notified, max_days=90)
    assert "test@test.com:12345" in result

def test_purge_old_notified_supprime_anciens():
    """Les entrées de plus de 90 jours sont supprimées."""
    import phemeapp
    from datetime import datetime, timedelta
    old_date = (datetime.now() - timedelta(days=100)).isoformat()
    notified = {"test@test.com:99999": old_date}
    result = phemeapp.purge_old_notified(notified, max_days=90)
    assert "test@test.com:99999" not in result

def test_purge_garde_welcome():
    """Les clés welcome: sont toujours gardées."""
    import phemeapp
    from datetime import datetime, timedelta
    old_date = (datetime.now() - timedelta(days=200)).isoformat()
    notified = {
        "welcome:test@test.com": old_date,
        "test@test.com:12345": old_date
    }
    result = phemeapp.purge_old_notified(notified, max_days=90)
    assert "welcome:test@test.com" in result
    assert "test@test.com:12345" not in result

# ─────────────────────────────────────────────
# TESTS IDEA-T03 : Cache CAMAC
# ─────────────────────────────────────────────

def test_get_cached_camac_ids_vide():
    """Dictionnaire vide → aucun ID en cache."""
    import phemeapp
    assert len(phemeapp.get_cached_camac_ids({})) == 0

def test_get_cached_camac_ids_recent():
    """Entrée récente → ID en cache."""
    import phemeapp
    from datetime import datetime
    notified = {"test@test.com:42": datetime.now().isoformat()}
    ids = phemeapp.get_cached_camac_ids(notified)
    assert "42" in ids

def test_is_new_enquete_inconnu():
    """noCamac inconnu → nouvelle enquête."""
    import phemeapp
    assert phemeapp.is_new_enquete(99999, {})

def test_is_new_enquete_connu():
    """noCamac déjà vu → pas nouvelle."""
    import phemeapp
    from datetime import datetime
    notified = {"test@test.com:42": datetime.now().isoformat()}
    assert not phemeapp.is_new_enquete(42, notified)

# ─────────────────────────────────────────────
# TESTS IDEA-P19 : Dossiers retirés
# ─────────────────────────────────────────────

def test_check_dossiers_retires_pas_de_retrait():
    """Dossier encore présent dans CAMAC → pas de notification."""
    import phemeapp
    from datetime import datetime
    emails_envoyes = []
    from unittest.mock import patch
    with patch.object(phemeapp, "smtp_send", side_effect=lambda d,s,h: emails_envoyes.append(d)):
        user = {"email": "test@test.com", "nom": "Test", "adresses": []}
        notified = {"test@test.com:42": datetime.now().isoformat()}
        enquetes = [{"noCamac": 42, "lat": 46.5, "lng": 6.5}]
        phemeapp.check_dossiers_retires(user, notified, enquetes)
    assert len(emails_envoyes) == 0  # Dossier encore là → pas de notification

def test_check_dossiers_retires_notifie_retrait():
    """Dossier disparu récemment → notification envoyée."""
    import phemeapp
    from datetime import datetime, timedelta
    emails_envoyes = []
    from unittest.mock import patch
    with patch.object(phemeapp, "smtp_send", side_effect=lambda d,s,h: emails_envoyes.append(d)):
        user = {"email": "test@test.com", "nom": "Test", "adresses": []}
        # Dossier alerté il y a 10 jours
        notified = {"test@test.com:42": (datetime.now() - timedelta(days=10)).isoformat()}
        enquetes = []  # Dossier 42 n'est plus là
        phemeapp.check_dossiers_retires(user, notified, enquetes)
    assert len(emails_envoyes) == 1


# ─────────────────────────────────────────────
# TESTS IDEA-P10 : Segmentation profil
# ─────────────────────────────────────────────

def test_send_email_accepte_profil():
    """send_email accepte un paramètre profil=Locataire sans lever d'exception."""
    import phemeapp
    from unittest.mock import patch
    # Vérifier que la signature accepte profil sans KeyError ni TypeError
    sent = []
    def mock_smtp(dest, subj, html):
        sent.append(html)
    with patch.object(phemeapp, "smtp_send", mock_smtp):
        with patch.object(phemeapp, "find_commune_enquetes_url", return_value=None):
            enquete = {"noCamac": 1, "dateFao": 0, "lieu": "Test", "commune": "TestVille",
                       "description": "Test", "natureTravaux": "Test", "lat": 46.5, "lng": 6.5}
            adr = {"label": "Maison", "adresse": "Test 1 1000 Lausanne", "lat": 46.5, "lng": 6.5}
            try:
                phemeapp.send_email("test@test.com", "Test User", enquete, adr, 200, "Locataire")
                called = True
            except Exception:
                called = False
    assert called, "send_email doit s'exécuter sans exception avec profil=Locataire"

def test_send_email_message_proprietaire():
    """send_email s'exécute sans exception avec profil=Propriétaire."""
    import phemeapp
    from unittest.mock import patch
    def mock_smtp(dest, subj, html): pass
    with patch.object(phemeapp, "smtp_send", mock_smtp):
        with patch.object(phemeapp, "find_commune_enquetes_url", return_value=None):
            enquete = {"noCamac": 1, "dateFao": 0, "lieu": "Test", "commune": "TestVille",
                       "description": "Test", "natureTravaux": "Test", "lat": 46.5, "lng": 6.5}
            adr = {"label": "Maison", "adresse": "Test 1 1000 Lausanne", "lat": 46.5, "lng": 6.5}
            try:
                phemeapp.send_email("test@test.com", "Test", enquete, adr, 200, "Propriétaire")
                ok = True
            except Exception as e:
                ok = False
    assert ok

# ─────────────────────────────────────────────
# TESTS IDEA-U07 : Préférences notification
# ─────────────────────────────────────────────

def test_weekly_desactive_si_preference():
    """Résumé hebdo non envoyé si notif_hebdo=False (retourne immédiatement si pas lundi ou désactivé)."""
    import phemeapp
    from unittest.mock import patch
    emails = []
    # notif_hebdo=False doit empêcher l'envoi quel que soit le jour
    with patch.object(phemeapp, "smtp_send", side_effect=lambda d,s,h: emails.append(d)):
        user = {"email": "test@test.com", "nom": "Test",
                "adresses": [{"lat": 46.5, "lng": 6.5}],
                "notif_hebdo": False}
        # Même si on est lundi, notif désactivée → pas d'email
        phemeapp.send_weekly_summary(user, {}, [])
    assert len(emails) == 0, "Pas d'email si notif_hebdo=False"

def test_rappel_desactive_si_preference():
    """Rappel J-7 non envoyé si notif_rappel=False."""
    import phemeapp
    from unittest.mock import patch
    from datetime import datetime, timedelta
    emails = []
    with patch.object(phemeapp, "smtp_send", side_effect=lambda d,s,h: emails.append(d)):
        user = {"email": "test@test.com", "nom": "Test",
                "adresses": [{"lat": 46.5126, "lng": 6.5299}],
                "notif_rappel": False}
        ts = int((datetime.now() - timedelta(days=23)).timestamp() * 1000)
        enquete = {"noCamac": 42, "lat": 46.5130, "lng": 6.5299, "dateFao": ts,
                   "lieu": "Test", "commune": "TestVille", "natureTravaux": "Test"}
        phemeapp.send_rappel_j7(user, {}, [enquete])
    assert len(emails) == 0


# ═══════════════════════════════════════════════════════════════
# IDEA-T17 : TESTS D'INTÉGRATION — Pipeline complet
# ═══════════════════════════════════════════════════════════════

def _make_mock_context(phemeapp, smtp_mock, users_mock, notified_mock, enquetes_mock):
    """Contexte mock complet pour les tests d'intégration."""
    from unittest.mock import patch
    return [
        patch.object(phemeapp, "smtp_send", smtp_mock),
        patch.object(phemeapp, "load_users_from_sheet", return_value=users_mock),
        patch.object(phemeapp, "load_notified", return_value=notified_mock),
        patch.object(phemeapp, "save_notified", lambda n: None),
        patch.object(phemeapp, "geocode_swisstopo", return_value=(46.5126, 6.5299)),
        patch.object(phemeapp, "get_commune_from_coords", return_value="TESTVILLE"),
        patch.object(phemeapp, "find_commune_enquetes_url", return_value=None),
        patch.object(phemeapp, "fetch_enquetes_with_retry", return_value=enquetes_mock),
        patch.object(phemeapp, "load_historique_from_sheet", return_value=[]),
        patch.object(phemeapp, "load_zone_elargie_from_sheet", return_value=[]),
        patch.object(phemeapp, "check_commune_backup", lambda *a: None),
        patch.object(phemeapp, "log_alerte_historique", lambda *a: None),
        patch.object(phemeapp, "log_zone_elargie", lambda *a: None),
        patch.object(phemeapp, "ping_healthcheck", lambda *a, **kw: None),
        patch.object(phemeapp, "generate_admin_dashboard", lambda *a: None),
        patch.object(phemeapp, "get_unsub_link", return_value="mailto:test"),
        patch.object(phemeapp, "get_magic_link", return_value="https://test"),
    ]

def _run_with_mocks(phemeapp, smtp_mock, users_mock, notified_mock, enquetes_mock):
    """Lance run() avec tous les mocks nécessaires."""
    from contextlib import ExitStack
    contexts = _make_mock_context(phemeapp, smtp_mock, users_mock, notified_mock, enquetes_mock)
    with ExitStack() as stack:
        for ctx in contexts:
            stack.enter_context(ctx)
        phemeapp.run()


def test_integration_nouvel_utilisateur_recoit_bienvenue():
    """Pipeline: nouvel utilisateur → email de bienvenue envoyé."""
    import phemeapp
    emails = []
    def mock_smtp(dest, subj, html): emails.append({"dest": dest, "subj": subj})

    users_mock = [{"email": "new@test.com", "nom": "Nouveau",
        "adresses": [{"label": "Maison", "adresse": "Test", "lat": None, "lng": None}],
        "profil": "", "telephone": "", "perimetre": "500",
        "notif_hebdo": True, "notif_mensuel": True, "notif_rappel": True}]

    _run_with_mocks(phemeapp, mock_smtp, users_mock, {}, [])
    bienvenus = [e for e in emails if "active" in e["subj"].lower() or "surveillance" in e["subj"].lower() or "PhémeApp" in e["subj"]]
    assert len(bienvenus) >= 1, f"Email bienvenue attendu, reçu: {[e['subj'] for e in emails]}"


def test_integration_alerte_envoyee_si_match():
    """Pipeline: enquête dans périmètre → alerte envoyée."""
    import phemeapp
    from datetime import datetime
    emails = []
    def mock_smtp(dest, subj, html): emails.append({"dest": dest, "subj": subj})

    users_mock = [{"email": "test@test.com", "nom": "Test",
        "adresses": [{"label": "Maison", "adresse": "Test", "lat": 46.5126, "lng": 6.5299}],
        "profil": "Propriétaire", "telephone": "", "perimetre": "500",
        "notif_hebdo": True, "notif_mensuel": True, "notif_rappel": True}]

    ts = int(datetime.now().timestamp() * 1000)
    enquetes_mock = [{"noCamac": 249553, "lat": 46.5145, "lng": 6.5330, "dateFao": ts,
        "lieu": "Test", "commune": "TestVille", "description": "Test",
        "natureTravaux": "Construction villa", "faoLib": ""}]

    notified_mock = {"welcome:test@test.com": datetime.now().isoformat()}
    _run_with_mocks(phemeapp, mock_smtp, users_mock, notified_mock, enquetes_mock)

    alertes = [e for e in emails if "249553" in e["subj"] or "TestVille" in e["subj"] or "enquête" in e["subj"].lower()]
    assert len(alertes) >= 1, f"Alerte attendue, emails: {[e['subj'] for e in emails]}"


def test_integration_pas_dalerte_hors_perimetre():
    """Pipeline: enquête hors périmètre → aucune alerte."""
    import phemeapp
    from datetime import datetime
    emails = []
    def mock_smtp(dest, subj, html): emails.append(subj)

    users_mock = [{"email": "test@test.com", "nom": "Test",
        "adresses": [{"label": "Maison", "adresse": "Test", "lat": 46.5126, "lng": 6.5299}],
        "profil": "", "telephone": "", "perimetre": "500",
        "notif_hebdo": True, "notif_mensuel": True, "notif_rappel": True}]

    ts = int(datetime.now().timestamp() * 1000)
    enquetes_mock = [{"noCamac": 99999, "lat": 46.5600, "lng": 6.5299,
        "dateFao": ts, "lieu": "Loin", "commune": "Lausanne",
        "description": "Test", "natureTravaux": "Test", "faoLib": ""}]

    notified_mock = {"welcome:test@test.com": datetime.now().isoformat()}
    _run_with_mocks(phemeapp, mock_smtp, users_mock, notified_mock, enquetes_mock)

    alertes = [s for s in emails if "99999" in str(s) or ("enquête" in str(s).lower() and "TestVille" not in str(s))]
    assert len(alertes) == 0, f"Aucune alerte hors périmètre, reçu: {alertes}"


def test_integration_pas_doublon_alerte():
    """Pipeline: enquête déjà notifiée → pas de doublon."""
    import phemeapp
    from datetime import datetime
    emails = []
    def mock_smtp(dest, subj, html): emails.append(subj)

    users_mock = [{"email": "test@test.com", "nom": "Test",
        "adresses": [{"label": "Maison", "adresse": "Test", "lat": 46.5126, "lng": 6.5299}],
        "profil": "", "telephone": "", "perimetre": "500",
        "notif_hebdo": True, "notif_mensuel": True, "notif_rappel": True}]

    ts = int(datetime.now().timestamp() * 1000)
    enquetes_mock = [{"noCamac": 12345, "lat": 46.5130, "lng": 6.5299,
        "dateFao": ts, "lieu": "Test", "commune": "TestVille",
        "description": "Test", "natureTravaux": "Test", "faoLib": ""}]

    notified_mock = {
        "welcome:test@test.com": datetime.now().isoformat(),
        "test@test.com:12345": datetime.now().isoformat()
    }
    _run_with_mocks(phemeapp, mock_smtp, users_mock, notified_mock, enquetes_mock)

    alertes = [s for s in emails if "12345" in str(s)]
    assert len(alertes) == 0, f"Doublon détecté: {alertes}"
