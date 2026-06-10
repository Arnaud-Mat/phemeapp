# PhemeApp

Service de surveillance des mises a l enquete publique — Canton de Vaud.

## Fonctionnement
- Detection automatique via API CAMAC officielle
- Alertes email via Brevo SMTP
- Perimetre : 500m autour des adresses surveillees
- Frequence : tous les jours a 7h (GitHub Actions)

## Configuration
Les secrets sont dans GitHub Secrets :
- `BREVO_API_KEY` : cle SMTP Brevo
- `BREVO_SMTP_LOGIN` : login SMTP Brevo

