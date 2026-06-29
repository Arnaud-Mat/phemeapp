/**
 * PhemeApp - Apps Script complet
 * 
 * Contient 3 fonctions:
 * 1. updateForm()       - Ajouter questions Form (executer une fois depuis Apps Script)
 * 2. onFormSubmit(e)    - Trigger email bienvenue immediat (configurer comme declencheur)
 * 3. doPost(e)          - Web App pour ecriture historique dans Sheet (deployer comme Web App)
 *
 * SETUP (depuis https://script.google.com - projet existant PhemeApp):
 * 
 * A) Ajouter questions Form:
 *    Executer > updateForm > Autoriser
 *
 * B) Email bienvenue immediat:
 *    Declencheurs (icone horloge) > + Ajouter un declencheur
 *    Fonction: onFormSubmit | Source: Depuis le spreadsheet | Type: A la soumission du formulaire
 *
 * C) Ecriture historique dans Sheet:
 *    Deployer > Nouvelle mise en prod > Type: Application Web
 *    Executer en tant que: Moi | Acces: Tout le monde
 *    Copier l URL et l ajouter dans GitHub Secrets: APPS_SCRIPT_WEBAPP_URL
 */

var SHEET_ID = "1YLK-KV_W7sNraeZdsyttykh1OnYU5aJOhl_NIqwFsJw";
var FORM_ID  = "1UyI_rP33TaBww5WBaitEHuxKj8lTwkruZiSnHD91BEQ";

// ─────────────────────────────────────────────
// A) METTRE A JOUR LE FORMULAIRE
// ─────────────────────────────────────────────
function updateForm() {
  var form  = FormApp.openById(FORM_ID);
  var items = form.getItems();

  // Verifier si les questions existent deja
  var hasProfil = items.some(function(i) { return i.getTitle().indexOf("tes") >= 0 && i.getType() === FormApp.ItemType.MULTIPLE_CHOICE; });
  var hasTel    = items.some(function(i) { return i.getTitle().indexOf("phone") >= 0 || i.getTitle().indexOf("l\u00e9phone") >= 0; });

  var insertPos = items.length - 1; // avant "J accepte les conditions"

  if (!hasProfil) {
    var q1 = form.addMultipleChoiceItem();
    q1.setTitle("Vous \u00eates");
    q1.setChoices([
      q1.createChoice("Propri\u00e9taire"),
      q1.createChoice("Locataire"),
      q1.createChoice("Autre")
    ]);
    q1.setRequired(true);
    form.moveItem(q1.getIndex(), insertPos);
    insertPos++;
    Logger.log("Question Vous etes ajoutee");
  } else {
    Logger.log("Question Vous etes existe deja");
  }

  if (!hasTel) {
    var q2 = form.addTextItem();
    q2.setTitle("Num\u00e9ro de t\u00e9l\u00e9phone (facultatif)");
    q2.setHelpText("Pour \u00eatre contact\u00e9 en cas de besoin.");
    q2.setRequired(false);
    form.moveItem(q2.getIndex(), insertPos);
    Logger.log("Question telephone ajoutee");
  } else {
    Logger.log("Question telephone existe deja");
  }

  Logger.log("Form mis a jour. Total questions: " + form.getItems().length);
}

// ─────────────────────────────────────────────
// B) EMAIL DE BIENVENUE IMMEDIAT (trigger onFormSubmit)
// ─────────────────────────────────────────────
function onFormSubmit(e) {
  var row    = e.values;
  var nom    = row[1] || "";
  var email  = row[2] || "";
  var adr1   = row[3] || "";
  var label1 = row[4] || "Adresse 1";
  var adr2   = row[5] || "";
  var label2 = row[6] || "Adresse 2";

  if (!email || !adr1) {
    Logger.log("Donnees manquantes - email ou adresse vide");
    return;
  }

  var prenom = nom.split(" ")[0] || "bonjour";
  var rowsHtml = "<tr><td style=\'padding:8px 10px;color:#888\'>" + label1 + "</td><td style=\'padding:8px 10px\'>" + adr1 + "</td></tr>";
  if (adr2) rowsHtml += "<tr><td style=\'padding:8px 10px;color:#888\'>" + label2 + "</td><td style=\'padding:8px 10px\'>" + adr2 + "</td></tr>";

  var html = "<html><body style=\'font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333\'>"
    + "<div style=\'background:#1a3a5c;padding:18px 24px\'>"
    + "<h1 style=\'color:white;margin:0;font-size:20px\'>Ph\u00e9meApp</h1>"
    + "<p style=\'color:#a8c4e0;margin:4px 0 0;font-size:12px\'>Surveillance des mises \u00e0 l\'enqu\u00eate \u2014 Canton de Vaud</p>"
    + "</div><div style=\'padding:24px\'>"
    + "<p style=\'font-size:16px\'>Bonjour " + prenom + ",</p>"
    + "<p style=\'font-size:14px;color:#444;line-height:1.7\'>Votre inscription \u00e0 <strong>Ph\u00e9meApp</strong> est confirm\u00e9e. Votre surveillance est maintenant active.</p>"
    + "<div style=\'background:#eaf4ee;border-left:3px solid #1a7a4a;padding:14px 18px;margin:20px 0;border-radius:0 6px 6px 0\'>"
    + "<p style=\'margin:0 0 6px;font-size:14px;color:#0f4a2a;font-weight:500\'>Ce que nous faisons pour vous chaque jour</p>"
    + "<p style=\'margin:0;font-size:13px;color:#1a5c35;line-height:1.7\'>Chaque matin, notre syst\u00e8me v\u00e9rifie si une nouvelle mise \u00e0 l\'enqu\u00eate a \u00e9t\u00e9 d\u00e9pos\u00e9e dans un rayon de <strong>500 m\u00e8tres</strong> autour de vos adresses. Aucune publication ne peut nous \u00e9chapper.</p>"
    + "</div>"
    + "<p style=\'font-size:14px;color:#444\'>Vos adresses surveill\u00e9es :</p>"
    + "<table style=\'width:100%;border-collapse:collapse;font-size:13px;margin:0 0 20px\'>"
    + "<tr style=\'background:#f0f4f8\'><th style=\'padding:8px 10px;text-align:left;color:#555\'>Nom</th><th style=\'padding:8px 10px;text-align:left;color:#555\'>Adresse</th></tr>"
    + rowsHtml + "</table>"
    + "<div style=\'background:#fff8e1;border-left:3px solid #f59e0b;padding:14px 18px;margin:20px 0;border-radius:0 6px 6px 0\'>"
    + "<p style=\'margin:0 0 4px;font-size:13px;color:#92400e;font-weight:500\'>Important \u2014 d\u00e9lai l\u00e9gal de recours</p>"
    + "<p style=\'margin:0;font-size:13px;color:#92400e;line-height:1.6\'>En cas de mise \u00e0 l\'enqu\u00eate \u00e0 proximit\u00e9, vous disposez de <strong>30 jours</strong> \u00e0 compter de la date de publication dans la FAO pour faire opposition.</p>"
    + "</div>"
    + "<p style=\'font-size:14px;color:#444;line-height:1.7\'>Pour modifier vos adresses ou vous d\u00e9sinscrire, r\u00e9pondez simplement \u00e0 cet email.</p>"
    + "<p style=\'font-size:14px;color:#444\'>Bien cordialement,<br><strong>L\'\u00e9quipe Ph\u00e9meApp</strong></p>"
    + "<p style=\'font-size:11px;color:#aaa;border-top:1px solid #eee;padding-top:14px;margin-top:24px\'>Ph\u00e9meApp est en phase de test (beta). Il ne remplace pas une consultation juridique.</p>"
    + "</div></body></html>";

    // Envoi via API Brevo (alerte@phemeapp.ch)
    var brevoPayload = {
      sender: { name: "PhémeApp", email: "alerte@phemeapp.ch" },
      to: [{ email: email, name: nom || "Utilisateur" }],
      subject: "Votre surveillance PhémeApp est active",
      htmlContent: html
    };
    UrlFetchApp.fetch("https://api.brevo.com/v3/smtp/email", {
      method: "post",
      contentType: "application/json",
      headers: { "api-key": "xsmtpsib-c35d132ff59c0a7acd47584a3064fd78986954a2a1ec3cda491e4246b3f96516-MLMfUsjvSEhDzf9F" },
      payload: JSON.stringify(brevoPayload),
      muteHttpExceptions: true
    });

  Logger.log("Email bienvenue envoye a " + email);
}

// ─────────────────────────────────────────────
// C) WEB APP - ECRITURE HISTORIQUE DANS SHEET
// ─────────────────────────────────────────────
function doPost(e) {
  try {
    var payload;

    // Support double mode : JSON (fetch) et form POST (iframe, contournement CORS)
    if (e.postData && e.postData.contents) {
      try {
        payload = JSON.parse(e.postData.contents);
      } catch(parseErr) {
        payload = e.parameter || {};
      }
    } else if (e.parameter && e.parameter.action) {
      payload = e.parameter;
    } else {
      payload = {};
    }

    // Router les actions du formulaire natif
    var action = payload.action || "";
    if (action === "subscribe") {
      return handleSubscribe(payload);
    }

    var tabName = payload.tab;
    var row     = payload.row;

    var ss    = SpreadsheetApp.openById(SHEET_ID);
    var sheet = ss.getSheetByName(tabName);

    if (!sheet) {
      return ContentService
        .createTextOutput(JSON.stringify({error: "Onglet non trouve: " + tabName}))
        .setMimeType(ContentService.MimeType.JSON);
    }

    var values = [];
    if (tabName === "Historique Alertes") {
      values = [row.date_envoi, row.email, row.nom, row.label_adresse, row.adresse,
                row.no_camac, row.lieu, row.commune, row.nature_travaux,
                row.distance_m, row.date_fao, row.lien];
    } else if (tabName === "Zone Elargie") {
      values = [row.date_detection, row.email, row.nom, row.label_adresse, row.adresse,
                row.no_camac, row.lieu, row.commune, row.nature_travaux,
                row.distance_m, row.date_fao, row.lien, row.inclus_newsletter ? "Oui" : "Non"];
    } else {
      values = Object.values(row);
    }

    sheet.appendRow(values);

    return ContentService
      .createTextOutput(JSON.stringify({success: true, tab: tabName, rows: sheet.getLastRow()}))
      .setMimeType(ContentService.MimeType.JSON);

  } catch(err) {
    return ContentService
      .createTextOutput(JSON.stringify({error: err.toString()}))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// Test local depuis Apps Script
function testDoPost() {
  var fakeE = {postData: {contents: JSON.stringify({
    tab: "Historique Alertes",
    row: {date_envoi: new Date().toISOString(), email: "test@test.com", nom: "Test",
          label_adresse: "Maison", adresse: "Route test 1 1000 Lausanne", no_camac: 99999,
          lieu: "Test", commune: "Lausanne", nature_travaux: "Test", distance_m: 100,
          date_fao: "01.01.2026", lien: "https://www.faovd.ch"}
  })}};
  var result = doPost(fakeE);
  Logger.log(result.getContent());
}

// ─────────────────────────────────────────────
// D) ESPACE UTILISATEUR - doGet (lecture données)
// ─────────────────────────────────────────────
function doGet(e) {
  var action = e.parameter.action || "";
  var email  = e.parameter.email  || "";
  var token  = e.parameter.token  || "";

  // Action publique (sans token) — compteur bêta
  if (action === "getUserCount") {
    return getUserCount();
  }

  // Vérifier le token (même logique que Python)
  if (!verifyMagicToken(email, token)) {
    return ContentService
      .createTextOutput(JSON.stringify({error: "Token invalide", valid: false}))
      .setMimeType(ContentService.MimeType.JSON);
  }

  if (action === "getUser") {
    return getUserData(email, token);
  } else if (action === "unsubscribe") {
    return handleUnsubscribe(email, token);
  }

  return ContentService
    .createTextOutput(JSON.stringify({error: "Action inconnue"}))
    .setMimeType(ContentService.MimeType.JSON);
}

function verifyMagicToken(email, token) {
  // Même algo que phemeapp.py : HMAC-SHA256(key=secret, msg=email:mois)[:32]
  var secret = "db842af52df466861c7ce6605a760ad6ddaebf682f302a29"; // sync_secret.yml remplace cette valeur
  var now = new Date();

  for (var delta = 0; delta <= 1; delta++) {
    var d = new Date(now);
    d.setMonth(d.getMonth() - delta);
    var mois = Utilities.formatDate(d, "UTC", "yyyy-MM");
    var raw = email + ":" + mois;

    // HMAC-SHA256 : clé=secret, message=raw
    var secretBytes = Utilities.newBlob(secret).getBytes();
    var rawBytes    = Utilities.newBlob(raw).getBytes();
    var hmacBytes   = Utilities.computeHmacSha256Signature(rawBytes, secretBytes);
    var expected    = hmacBytes.map(function(b) {
      return ("0" + (b & 0xFF).toString(16)).slice(-2);
    }).join("").substring(0, 32);

    if (token === expected) return true;
  }
  return false;
}

function getUserData(email, token) {
  var ss = SpreadsheetApp.openById(SHEET_ID);

  // Lire les adresses depuis Form Responses
  var formSheet = ss.getSheetByName("Form Responses 1");
  var adresses = [];
  if (formSheet) {
    var rows = formSheet.getDataRange().getValues();
    for (var i = 1; i < rows.length; i++) {
      if (rows[i][2] && rows[i][2].toString().toLowerCase() === email.toLowerCase()) {
        var adr1 = rows[i][3] || "";
        var lab1 = rows[i][4] || "Adresse 1";
        var adr2 = rows[i][5] || "";
        var lab2 = rows[i][6] || "Adresse 2";
        if (adr1) adresses.push({label: lab1, adresse: adr1});
        if (adr2) adresses.push({label: lab2, adresse: adr2});
        break;
      }
    }
  }

  // Lire l historique des alertes
  var histSheet = ss.getSheetByName("Historique Alertes");
  var historique = [];
  if (histSheet) {
    var histRows = histSheet.getDataRange().getValues();
    for (var j = 1; j < histRows.length; j++) {
      if (histRows[j][1] && histRows[j][1].toString().toLowerCase() === email.toLowerCase()) {
        historique.push({
          date_envoi:    histRows[j][0] ? histRows[j][0].toString().substring(0, 10) : "",
          commune:       histRows[j][7] || "--",
          nature_travaux:histRows[j][8] || "--",
          distance_m:    histRows[j][9] || 0,
          date_fao:      histRows[j][10] || "--",
          lien:          histRows[j][11] || ""
        });
      }
    }
    historique.reverse(); // Plus récent en premier
    historique = historique.slice(0, 10);
  }

  // Générer le lien désinscription
  var unsubToken = verifyMagicToken(email, token) ? token : "";
  var unsub_url = "mailto:alerte@phemeapp.ch?subject=D%C3%A9sinscription%20Ph%C3%A9meApp";

  var result = {
    valid: true,
    email: email,
    adresses: adresses,
    historique: historique,
    unsub_url: unsub_url
  };

  return ContentService
    .createTextOutput(JSON.stringify(result))
    .setMimeType(ContentService.MimeType.JSON);
}

function handleUnsubscribe(email, token) {
  var ss = SpreadsheetApp.openById(SHEET_ID);
  var sheet = ss.getSheetByName("Form Responses 1");
  if (!sheet) {
    return ContentService.createTextOutput(JSON.stringify({error: "Sheet non trouvé"}))
      .setMimeType(ContentService.MimeType.JSON);
  }

  var rows = sheet.getDataRange().getValues();
  for (var i = rows.length - 1; i >= 1; i--) {
    if (rows[i][2] && rows[i][2].toString().toLowerCase() === email.toLowerCase()) {
      sheet.deleteRow(i + 1);
      Logger.log("Désinscription: " + email + " supprimé ligne " + (i + 1));
      return ContentService
        .createTextOutput(JSON.stringify({success: true, message: "Désinscription effectuée"}))
        .setMimeType(ContentService.MimeType.JSON);
    }
  }

  return ContentService
    .createTextOutput(JSON.stringify({error: "Email non trouvé"}))
    .setMimeType(ContentService.MimeType.JSON);
}


// ─────────────────────────────────────────────
// E) IDEA-T11 : Déclencher GitHub Actions depuis onFormSubmit
// Résout BUG-006 : email de bienvenue immédiat
// ─────────────────────────────────────────────
var GITHUB_TOKEN_GAS  = PropertiesService.getScriptProperties().getProperty("GITHUB_TOKEN") || "";
var GITHUB_REPO_GAS   = "Arnaud-Mat/phemeapp";

function triggerGitHubActions() {
  // Déclencher un workflow_dispatch sur phemeapp.yml
  if (!GITHUB_TOKEN_GAS) {
    Logger.log("GITHUB_TOKEN non configuré dans ScriptProperties");
    return false;
  }
  try {
    var url = "https://api.github.com/repos/" + GITHUB_REPO_GAS + "/actions/workflows/phemeapp.yml/dispatches";
    var options = {
      method: "post",
      headers: {
        "Authorization": "token " + GITHUB_TOKEN_GAS,
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json"
      },
      payload: JSON.stringify({ref: "main"}),
      muteHttpExceptions: true
    };
    var resp = UrlFetchApp.fetch(url, options);
    if (resp.getResponseCode() === 204) {
      Logger.log("GitHub Actions déclenché avec succès");
      return true;
    } else {
      Logger.log("Erreur GitHub Actions: " + resp.getContentText());
      return false;
    }
  } catch(e) {
    Logger.log("Exception triggerGitHubActions: " + e.toString());
    return false;
  }
}

// Mettre à jour onFormSubmit pour déclencher GitHub Actions
// Remplace la version précédente de onFormSubmit
function onFormSubmit_withTrigger(e) {
  // 1. Envoyer l'email de bienvenue immédiatement
  onFormSubmit(e);
  // 2. Déclencher un run GitHub Actions pour vérifier les alertes
  // (permet de ne pas attendre le cron de 8h)
  Utilities.sleep(2000); // Attendre que le Sheet soit mis à jour
  triggerGitHubActions();
  Logger.log("onFormSubmit_withTrigger terminé");
}
// Pour activer: configurer le déclencheur sur onFormSubmit_withTrigger
// au lieu de onFormSubmit
// Et ajouter GITHUB_TOKEN dans ScriptProperties (Extensions > Apps Script > Paramètres du projet)


// ─────────────────────────────────────────────
// F) IDEA-U06 : Historique complet paginé
//    Mise à jour de getUserData pour supporter page + limit
// ─────────────────────────────────────────────
function getUserDataPaginated(email, token, page, limit) {
  page  = parseInt(page)  || 1;
  limit = parseInt(limit) || 10;
  
  var ss = SpreadsheetApp.openById(SHEET_ID);
  
  // Adresses
  var formSheet = ss.getSheetByName("Form Responses 1");
  var adresses = [];
  if (formSheet) {
    var rows = formSheet.getDataRange().getValues();
    for (var i = 1; i < rows.length; i++) {
      if (rows[i][2] && rows[i][2].toString().toLowerCase() === email.toLowerCase()) {
        var adr1 = rows[i][3] || ""; var lab1 = rows[i][4] || "Adresse 1";
        var adr2 = rows[i][5] || ""; var lab2 = rows[i][6] || "Adresse 2";
        if (adr1) adresses.push({label: lab1, adresse: adr1});
        if (adr2) adresses.push({label: lab2, adresse: adr2});
        break;
      }
    }
  }
  
  // Historique avec pagination
  var histSheet = ss.getSheetByName("Historique Alertes");
  var all_historique = [];
  if (histSheet) {
    var histRows = histSheet.getDataRange().getValues();
    for (var j = 1; j < histRows.length; j++) {
      if (histRows[j][1] && histRows[j][1].toString().toLowerCase() === email.toLowerCase()) {
        all_historique.push({
          date_envoi:     histRows[j][0] ? histRows[j][0].toString().substring(0,10) : "",
          commune:        histRows[j][7] || "--",
          nature_travaux: histRows[j][8] || "--",
          distance_m:     histRows[j][9] || 0,
          date_fao:       histRows[j][10] || "--",
          lien:           histRows[j][11] || ""
        });
      }
    }
    all_historique.reverse();
  }
  
  var total   = all_historique.length;
  var pages   = Math.ceil(total / limit);
  var start   = (page - 1) * limit;
  var end     = Math.min(start + limit, total);
  var historique = all_historique.slice(start, end);
  
  return ContentService
    .createTextOutput(JSON.stringify({
      valid: true, email: email, adresses: adresses,
      historique: historique,
      pagination: {page: page, limit: limit, total: total, pages: pages},
      unsub_url: "mailto:alerte@phemeapp.ch?subject=D%C3%A9sinscription%20Ph%C3%A9meApp"
    }))
    .setMimeType(ContentService.MimeType.JSON);
}

// ─────────────────────────────────────────────
// G) IDEA-U03 : Modifier ses adresses depuis Mon compte
// ─────────────────────────────────────────────
function handleUpdateAddresses(email, token, newAddresses) {
  // newAddresses = [{label: "Maison", adresse: "Chemin..."}, ...]
  if (!newAddresses || !Array.isArray(newAddresses)) {
    return ContentService
      .createTextOutput(JSON.stringify({error: "Format adresses invalide"}))
      .setMimeType(ContentService.MimeType.JSON);
  }
  
  var ss = SpreadsheetApp.openById(SHEET_ID);
  var sheet = ss.getSheetByName("Form Responses 1");
  if (!sheet) {
    return ContentService.createTextOutput(JSON.stringify({error: "Sheet non trouvé"}))
      .setMimeType(ContentService.MimeType.JSON);
  }
  
  var rows = sheet.getDataRange().getValues();
  for (var i = rows.length - 1; i >= 1; i--) {
    if (rows[i][2] && rows[i][2].toString().toLowerCase() === email.toLowerCase()) {
      // Mettre à jour les colonnes adresses
      var adr1 = newAddresses[0] ? newAddresses[0].adresse : "";
      var lab1 = newAddresses[0] ? newAddresses[0].label   : "Adresse 1";
      var adr2 = newAddresses[1] ? newAddresses[1].adresse : "";
      var lab2 = newAddresses[1] ? newAddresses[1].label   : "Adresse 2";
      
      // BUG-FORM-08 fix : ne pas écraser adresse2 si non fournie
      sheet.getRange(i + 1, 4).setValue(adr1); // colonne D
      sheet.getRange(i + 1, 5).setValue(lab1); // colonne E
      if (adr2 !== "" || newAddresses.length >= 2) {
        sheet.getRange(i + 1, 6).setValue(adr2); // colonne F — seulement si explicitement modifié
        sheet.getRange(i + 1, 7).setValue(lab2); // colonne G
      }
      
      Logger.log("Adresses mises à jour pour " + email);
      return ContentService
        .createTextOutput(JSON.stringify({success: true, message: "Adresses mises à jour"}))
        .setMimeType(ContentService.MimeType.JSON);
    }
  }
  
  return ContentService
    .createTextOutput(JSON.stringify({error: "Utilisateur non trouvé"}))
    .setMimeType(ContentService.MimeType.JSON);
}

// Mise à jour de doGet pour inclure la pagination
function doGet_v2(e) {
  var action = e.parameter.action || "getUser";
  var email  = e.parameter.email  || "";
  var token  = e.parameter.token  || "";
  var page   = e.parameter.page   || "1";
  var limit  = e.parameter.limit  || "10";
  
  if (!verifyMagicToken(email, token)) {
    return ContentService
      .createTextOutput(JSON.stringify({error: "Token invalide", valid: false}))
      .setMimeType(ContentService.MimeType.JSON);
  }
  
  if (action === "getUser") {
    return getUserDataPaginated(email, token, page, limit);
  } else if (action === "unsubscribe") {
    return handleUnsubscribe(email, token);
  }
  
  return ContentService
    .createTextOutput(JSON.stringify({error: "Action inconnue"}))
    .setMimeType(ContentService.MimeType.JSON);
}

// doPost étendu pour updateAddresses
function doPost_v2(e) {
  try {
    var payload = JSON.parse(e.postData.contents);
    var action  = payload.action || "";
    var email   = payload.email  || "";
    var token   = payload.token  || "";
    
    // Actions qui nécessitent une authentification
    if (action === "updateAddresses") {
      if (!verifyMagicToken(email, token)) {
        return ContentService.createTextOutput(JSON.stringify({error: "Token invalide"}))
          .setMimeType(ContentService.MimeType.JSON);
      }
      return handleUpdateAddresses(email, token, payload.addresses);
    }
    
    // Actions sans token (historique existant)
    return doPost(e);
    
  } catch(err) {
    return ContentService
      .createTextOutput(JSON.stringify({error: err.toString()}))
      .setMimeType(ContentService.MimeType.JSON);
  }
}


// ─────────────────────────────────────────────
// H) FORMULAIRE NATIF — handleSubscribe
// Gère l'inscription depuis index.html (action='subscribe')
// BUG-FORM-02 fix : ce handler était absent
// ─────────────────────────────────────────────
function handleSubscribe(payload) {
  var nom     = (payload.nom     || "").toString().trim().substring(0, 100);
  var email   = (payload.email   || "").toString().trim().toLowerCase();
  var adresse  = (payload.adresse1 || "").toString().trim().substring(0, 200);
  var label    = (payload.label1  || "Adresse principale").toString().trim().substring(0, 50);
  var adresse2 = (payload.adresse2 || "").toString().trim().substring(0, 200);
  var label2   = (payload.label2  || "Adresse 2").toString().trim().substring(0, 50);

  // Validation basique
  if (!email || !adresse) {
    return ContentService
      .createTextOutput(JSON.stringify({error: "Email ou adresse manquant"}))
      .setMimeType(ContentService.MimeType.JSON);
  }
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return ContentService
      .createTextOutput(JSON.stringify({error: "Email invalide"}))
      .setMimeType(ContentService.MimeType.JSON);
  }

  var ss    = SpreadsheetApp.openById(SHEET_ID);
  var sheet = ss.getSheetByName("Form Responses 1");
  if (!sheet) {
    return ContentService
      .createTextOutput(JSON.stringify({error: "Sheet non trouvé"}))
      .setMimeType(ContentService.MimeType.JSON);
  }

  // Vérifier si l'email existe déjà
  var rows = sheet.getDataRange().getValues();
  for (var i = 1; i < rows.length; i++) {
    if (rows[i][2] && rows[i][2].toString().toLowerCase() === email) {
      return ContentService
        .createTextOutput(JSON.stringify({already_exists: true}))
        .setMimeType(ContentService.MimeType.JSON);
    }
  }

  // Vérifier le quota de 100 utilisateurs (seulement pour les inscriptions Vaud)
  var horsVaud = (payload.hors_vaud === '1' || payload.hors_vaud === 1);

  if (!horsVaud) {
    var userCount = rows.length - 1; // sans header
    if (userCount >= 100) {
      return ContentService
        .createTextOutput(JSON.stringify({error: "quota_full", message: "Les 100 places bêta sont complètes."}))
        .setMimeType(ContentService.MimeType.JSON);
    }
  }

  // Écrire dans le bon onglet
  var timestamp = new Date().toISOString();
  if (horsVaud) {
    // Sauvegarder dans l'onglet "Hors Vaud" (créé automatiquement si absent)
    var sheetHV = ss.getSheetByName("Hors Vaud");
    if (!sheetHV) {
      sheetHV = ss.insertSheet("Hors Vaud");
      sheetHV.appendRow(["Timestamp", "Nom", "Email", "Adresse", "Label", "Adresse2", "Label2"]);
    }
    // Vérifier doublon dans Hors Vaud
    var hvRows = sheetHV.getDataRange().getValues();
    for (var j = 1; j < hvRows.length; j++) {
      if (hvRows[j][2] && hvRows[j][2].toString().toLowerCase() === email) {
        return ContentService
          .createTextOutput(JSON.stringify({hors_vaud: true, already_exists: true}))
          .setMimeType(ContentService.MimeType.JSON);
      }
    }
    sheetHV.appendRow([timestamp, nom, email, adresse, label, adresse2, label2]);
    Logger.log("Inscription hors Vaud: " + email);
  } else {
    sheet.appendRow([timestamp, nom, email, adresse, label, adresse2, label2]);
    Logger.log("Nouvelle inscription: " + email);
  }

  // Envoyer l'email de bienvenue
  try {
    var prenom = nom.split(" ")[0] || "bonjour";
    var html = "<html><body style='font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333'>"
      + "<div style='background:#1a3a5c;padding:18px 24px'>"
      + "<h1 style='color:white;margin:0;font-size:20px'>PhémeApp</h1>"
      + "<p style='color:#a8c4e0;margin:4px 0 0;font-size:12px'>Surveillance des mises à l'enquête — Canton de Vaud</p>"
      + "</div><div style='padding:24px'>"
      + "<p style='font-size:16px'>Bonjour " + prenom + ",</p>"
      + "<p style='font-size:14px;color:#444;line-height:1.7'>Votre inscription à <strong>PhémeApp</strong> est confirmée. Votre surveillance est maintenant active.</p>"
      + "<div style='background:#eaf4ee;border-left:3px solid #1a7a4a;padding:14px 18px;margin:20px 0;border-radius:0 6px 6px 0'>"
      + "<p style='margin:0 0 6px;font-size:14px;color:#0f4a2a;font-weight:500'>Ce que nous faisons pour vous chaque jour</p>"
      + "<p style='margin:0;font-size:13px;color:#1a5c35;line-height:1.7'>Chaque matin à 8h, notre système vérifie si une nouvelle mise à l'enquête a été déposée dans un rayon de <strong>500 mètres</strong> autour de votre adresse. Aucune publication ne peut nous échapper.</p>"
      + "</div>"
      + "<p style='font-size:14px;color:#444'>Adresse surveillée : <strong>" + label + " — " + adresse + "</strong></p>"
      + "<div style='background:#fff8e1;border-left:3px solid #f59e0b;padding:14px 18px;margin:20px 0;border-radius:0 6px 6px 0'>"
      + "<p style='margin:0 0 4px;font-size:13px;color:#92400e;font-weight:500'>Important — délai légal de recours</p>"
      + "<p style='margin:0;font-size:13px;color:#92400e;line-height:1.6'>En cas de mise à l'enquête à proximité, vous disposez de <strong>30 jours</strong> à compter de la date de publication dans la FAO pour faire opposition.</p>"
      + "</div>"
      + "<p style='font-size:14px;color:#444'>Bien cordialement,<br><strong>L'équipe PhémeApp</strong></p>"
      + "<p style='font-size:11px;color:#aaa;border-top:1px solid #eee;padding-top:14px;margin-top:24px'>PhémeApp est un service d'information automatisé. Il ne remplace pas une consultation juridique.</p>"
      + "</div></body></html>";
    // Email selon le canton
    var emailSubject, emailHtml;
    if (horsVaud) {
      emailSubject = "PhémeApp arrive bientôt dans votre canton";
      emailHtml = "<html><body style='font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333'>"
        + "<div style='background:#1a3a5c;padding:18px 24px'>"
        + "<h1 style='color:white;margin:0;font-size:20px'>PhémeApp</h1>"
        + "<p style='color:#a8c4e0;margin:4px 0 0;font-size:12px'>Surveillance des mises à l'enquête</p>"
        + "</div><div style='padding:24px'>"
        + "<p style='font-size:16px'>Bonjour " + prenom + ",</p>"
        + "<p style='font-size:14px;color:#444;line-height:1.7'>Merci de votre intérêt pour <strong>PhémeApp</strong>&nbsp;!</p>"
        + "<div style='background:#fff8e1;border-left:3px solid #f59e0b;padding:14px 18px;margin:20px 0;border-radius:0 6px 6px 0'>"
        + "<p style='margin:0 0 6px;font-size:14px;color:#92400e;font-weight:500'>Votre canton n'est pas encore couvert</p>"
        + "<p style='margin:0;font-size:13px;color:#92400e;line-height:1.7'>PhémeApp surveille actuellement uniquement le <strong>canton de Vaud</strong>. Nous avons bien enregistré votre adresse (<strong>" + adresse + "</strong>) et nous vous contacterons dès que nous lancerons la surveillance dans votre région.</p>"
        + "</div>"
        + "<p style='font-size:14px;color:#444;line-height:1.7'>En attendant, si vous avez des questions, n'hésitez pas à nous écrire à <a href='mailto:alerte@phemeapp.ch'>alerte@phemeapp.ch</a>.</p>"
        + "<p style='font-size:14px;color:#444'>Bien cordialement,<br><strong>L'équipe PhémeApp</strong></p>"
        + "<p style='font-size:11px;color:#aaa;border-top:1px solid #eee;padding-top:14px;margin-top:24px'>PhémeApp — service d'information automatisé sur les mises à l'enquête publiques.</p>"
        + "</div></body></html>";
    } else {
      emailSubject = "Votre surveillance PhémeApp est active";
      emailHtml = html;
    }

    // Envoi via API Brevo
    var brevoPayload = {
      sender: { name: "PhémeApp", email: "alerte@phemeapp.ch" },
      to: [{ email: email, name: nom || "Utilisateur" }],
      subject: emailSubject,
      htmlContent: emailHtml
    };
    UrlFetchApp.fetch("https://api.brevo.com/v3/smtp/email", {
      method: "post",
      contentType: "application/json",
      headers: {
        "api-key": "xsmtpsib-c35d132ff59c0a7acd47584a3064fd78986954a2a1ec3cda491e4246b3f96516-MLMfUsjvSEhDzf9F"
      },
      payload: JSON.stringify(brevoPayload),
      muteHttpExceptions: true
    });
    Logger.log("Email bienvenue envoyé à " + email);
  } catch(mailErr) {
    Logger.log("Erreur email bienvenue: " + mailErr.toString());
    // Ne pas bloquer l'inscription si l'email échoue
  }

  return ContentService
    .createTextOutput(JSON.stringify(horsVaud ? {hors_vaud: true} : {success: true}))
    .setMimeType(ContentService.MimeType.JSON);
}

// getUserCount — endpoint public pour le compteur bêta dans la landing page
// BUG-FORM-01 fix : cet endpoint était absent
function getUserCount() {
  var ss    = SpreadsheetApp.openById(SHEET_ID);
  var sheet = ss.getSheetByName("Form Responses 1");
  if (!sheet) {
    return ContentService
      .createTextOutput(JSON.stringify({count: 0, max: 100}))
      .setMimeType(ContentService.MimeType.JSON);
  }
  var count = Math.max(0, sheet.getLastRow() - 1); // sans header
  return ContentService
    .createTextOutput(JSON.stringify({count: count, max: 100, remaining: Math.max(0, 100 - count)}))
    .setMimeType(ContentService.MimeType.JSON);
}

// ─────────────────────────────────────────────
// SETUP — Créer le trigger sur le formulaire
// Exécuter UNE SEULE FOIS pour lier le script au formulaire
// ─────────────────────────────────────────────
function createFormTrigger() {
  // Supprimer les triggers existants sur ce formulaire pour éviter les doublons
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === 'onFormSubmit_withTrigger') {
      ScriptApp.deleteTrigger(triggers[i]);
      Logger.log('Ancien trigger supprimé');
    }
  }
  
  // Créer le nouveau trigger lié au formulaire PhémeApp
  ScriptApp.newTrigger('onFormSubmit_withTrigger')
    .forForm('1UyI_rP33TaBww5WBaitEHuxKj8lTwkruZiSnHD91BEQ')
    .onFormSubmit()
    .create();
  
  Logger.log('✅ Trigger créé: onFormSubmit_withTrigger → Form PhémeApp');
  Logger.log('Vérifiez dans Déclencheurs: événement doit être "À la soumission du formulaire"');
}


// Appelé par le workflow CI pour synchroniser le secret HMAC
function setMagicLinkSecret(secret) {
  PropertiesService.getScriptProperties().setProperty("MAGIC_LINK_SECRET", secret);
  Logger.log("MAGIC_LINK_SECRET mis à jour");
}
