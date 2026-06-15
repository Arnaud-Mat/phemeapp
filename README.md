# PhémeApp v2.1

> Service d'alerte automatisé pour les mises à l'enquête publiques du canton de Vaud (Suisse)

[![Tests](https://github.com/Arnaud-Mat/phemeapp/actions/workflows/phemeapp.yml/badge.svg)](https://github.com/Arnaud-Mat/phemeapp/actions)

---

## 🎯 Qu'est-ce que PhémeApp ?

PhémeApp surveille chaque matin les publications officielles de mises à l'enquête du canton de Vaud via l'API CAMAC. Dès qu'un projet est déposé dans un périmètre défini autour d'une adresse surveillée, l'utilisateur reçoit une alerte email avec tous les détails et le lien vers le dossier officiel.

**Délai légal : 30 jours pour faire opposition depuis la date FAO.**

---

## 🏗️ Architecture

```
Google Form (inscription)
       ↓
Google Sheet (utilisateurs + historique)
       ↓
phemeapp.py (GitHub Actions, 08h00 CH)
       ↓
API CAMAC (mises à l'enquête VD)
       ↓
Brevo SMTP (emails alertes)
       ↓
Utilisateur (email + espace perso)
```

**Stack :**
- Python 3.12 — script principal (~1800 lignes)
- GitHub Actions — exécution quotidienne (cron 06:00 UTC = 08h00 CH)
- Google Sheets — base de données utilisateurs + historique alertes
- Google Apps Script — Web App (historique, désinscription, magic link)
- Brevo SMTP — envoi emails (alerte@phemeapp.ch)
- Swisstopo API — géocodage adresses suisses
- Nominatim/OSM — reverse geocoding (commune depuis GPS)

---

## 📧 Emails envoyés

| Email | Déclencheur |
|-------|-------------|
| Bienvenue | Nouvelle inscription |
| Alerte | Mise à l'enquête dans le périmètre |
| Rappel J-7 | 5-8 jours avant fin du délai d'opposition |
| Résumé hebdo | Chaque lundi si aucune alerte |
| Rapport mensuel | 1x/mois depuis l'historique Sheet |
| Newsletter zone élargie | 1x/mois si activité 500m-2km |
| Retrait dossier | Si une enquête disparaît de CAMAC dans les 35j |
| Bilan annuel | Janvier — récapitulatif de l'année |

---

## ⚙️ Variables d'environnement (GitHub Secrets)

| Variable | Description | Obligatoire |
|----------|-------------|-------------|
| `BREVO_API_KEY` | Clé API Brevo SMTP | ✅ |
| `BREVO_SMTP_LOGIN` | Login SMTP Brevo | ✅ |
| `BREVO_SENDER` | Email expéditeur (alerte@phemeapp.ch) | ✅ |
| `SHEET_ID` | ID du Google Sheet | ✅ |
| `GH_PAT` | Personal Access Token GitHub (repo + workflow) | ✅ |
| `MAGIC_LINK_SECRET` | Clé HMAC pour les magic links (48 chars) | ✅ |
| `MAGIC_LINK_BASE` | URL base de l'espace utilisateur | ✅ |
| `ADMIN_EMAIL` | Email pour les alertes d'erreur | ✅ |
| `APPS_SCRIPT_WEBAPP_URL` | URL de la Web App Apps Script | ⚡ |
| `HEALTHCHECK_URL` | URL healthcheck.io pour le monitoring | 🔵 |

⚡ = requis pour l'historique Sheet et l'espace utilisateur
🔵 = optionnel

---

## 📊 Google Sheet — Structure

**Onglet "Form Responses 1" (utilisateurs)**

| Colonne | Contenu |
|---------|---------|
| A (0) | Timestamp inscription |
| B (1) | Nom complet |
| C (2) | Email |
| D (3) | Adresse 1 |
| E (4) | Label adresse 1 |
| F (5) | Adresse 2 (optionnel) |
| G (6) | Label adresse 2 |
| H (7) | Profil (Propriétaire/Locataire) |
| I (8) | Téléphone (optionnel) |
| J (9) | Périmètre en mètres (défaut: 500) |
| K (10) | Notif hebdo (oui/non) |
| L (11) | Notif mensuel (oui/non) |
| M (12) | Notif rappel J-7 (oui/non) |

**Onglets supplémentaires :**
- `Historique Alertes` — toutes les alertes envoyées
- `Zone Elargie` — publications 500m-2km

---

## 🔧 Apps Script (apps_script_webapp.gs)

Fonctions principales :

| Fonction | Rôle |
|----------|------|
| `updateForm()` | Met à jour le Google Form (questions profil/téléphone) |
| `onFormSubmit_withTrigger(e)` | Email bienvenue immédiat + déclenche GitHub Actions |
| `doGet(e)` | API REST — lecture données utilisateur (magic link) |
| `doPost(e)` | API REST — écriture historique alertes |
| `verifyMagicToken(email, token)` | Vérification HMAC-SHA256 |
| `getUserDataPaginated(email, token, page, limit)` | Historique paginé |
| `handleUpdateAddresses(email, token, addresses)` | Modification adresses |
| `handleUnsubscribe(email, token)` | Suppression compte |
| `triggerGitHubActions()` | Déclenche un run GitHub Actions |

**Déploiement :**
1. Coller `apps_script_webapp.gs` dans le projet Apps Script
2. Exécuter `updateForm()`
3. Configurer trigger sur `onFormSubmit_withTrigger`
4. Ajouter `GITHUB_TOKEN` dans les propriétés du script
5. Déployer comme Web App (accès : Tout le monde)
6. Copier l'URL → GitHub Secret `APPS_SCRIPT_WEBAPP_URL`

---

## 🔒 Sécurité

- **Secrets** : tous via GitHub Secrets, aucune valeur par défaut dans le code
- **Validation** : email (regex), téléphone (format CH), token HMAC-SHA256
- **Échappement HTML** : `html.escape()` sur toutes les données externes
- **Magic link** : HMAC-SHA256, valide 30 jours, renouvelé chaque mois
- **Permissions GitHub Actions** : minimales par job (read / write séparés)
- **Rétention données** : `DATA_RETENTION_DAYS = 730` (2 ans, nLPD)
- **Audit complet** : voir `🔒 Audit Sécurité` dans Notion

---

## 🧪 Tests

```bash
# Tests rapides (CI)
pytest tests/ -v -m "not slow"

# Tests d'intégration (pipeline complet)
pytest tests/ -m slow
```

**40 tests unitaires + 4 tests d'intégration** couvrant :
- Calcul de distance haversine
- Validation email + token
- Déduplication intelligente
- Rappel J-7 (logique de filtrage)
- Pipeline complet run() avec mocks

---

## 📁 Structure du repo

```
phemeapp/
├── phemeapp.py              # Script principal (~1800 lignes)
├── apps_script_webapp.gs    # Code Google Apps Script
├── mon-compte.html          # Page espace utilisateur (Wix)
├── politique-confidentialite.html  # Politique nLPD (Wix)
├── tests/
│   └── test_phemeapp.py    # 44 tests (40 rapides + 4 @slow)
└── .github/
    └── workflows/
        └── phemeapp.yml     # GitHub Actions (cron 08h00 CH)
```

---

## 📈 Statut

- **Run courant** : GitHub Actions #32 ✅
- **Utilisateurs actifs** : 3
- **Idées implémentées** : 44/49
- **Version** : v2.1
- **Dernier audit sécurité** : 13.06.2026

---

## 📝 Licence

Projet privé — © 2026 Arnaud Mathier, PhémeApp, Canton de Vaud (Suisse)
