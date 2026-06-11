#!/usr/bin/env python3
"""
Script pour ajouter les questions manquantes au Google Form PhemeApp.
Utilise l API Google Forms v1 avec OAuth.

Usage:
  pip3 install google-auth-oauthlib google-auth-httplib2 google-api-python-client
  python3 update_form.py
"""

import json
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
import os

FORM_ID = "1UyI_rP33TaBww5WBaitEHuxKj8lTwkruZiSnHD91BEQ"
SCOPES = ["https://www.googleapis.com/auth/forms.body"]

def get_service():
    """Authentification OAuth interactif"""
    flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
    creds = flow.run_local_server(port=0)
    return build("forms", "v1", credentials=creds)

def update_form():
    service = get_service()
    
    # Recuperer la structure actuelle du form
    form = service.forms().get(formId=FORM_ID).execute()
    items = form.get("items", [])
    print(f"Form actuel: {len(items)} questions")
    for i, item in enumerate(items):
        print(f"  {i}: {item.get('title', '?')} (id: {item.get('itemId')})")
    
    # Ajouter les nouvelles questions
    # 1. Question: Vous etes (proprietaire / locataire) - obligatoire
    # 2. Question: Numero de telephone - facultatif
    requests_body = {
        "requests": [
            {
                "createItem": {
                    "item": {
                        "title": "Vous êtes",
                        "questionItem": {
                            "question": {
                                "required": True,
                                "choiceQuestion": {
                                    "type": "RADIO",
                                    "options": [
                                        {"value": "Propriétaire"},
                                        {"value": "Locataire"},
                                        {"value": "Autre"}
                                    ]
                                }
                            }
                        }
                    },
                    "location": {"index": len(items) - 1}
                }
            },
            {
                "createItem": {
                    "item": {
                        "title": "Numéro de téléphone (facultatif)",
                        "description": "Pour être contacté en cas de besoin.",
                        "questionItem": {
                            "question": {
                                "required": False,
                                "textQuestion": {
                                    "paragraph": False
                                }
                            }
                        }
                    },
                    "location": {"index": len(items)}
                }
            }
        ]
    }
    
    result = service.forms().batchUpdate(
        formId=FORM_ID,
        body=requests_body
    ).execute()
    
    print("\nQuestions ajoutees avec succes!")
    print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    update_form()
