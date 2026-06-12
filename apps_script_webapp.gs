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

  MailApp.sendEmail({
    to: email,
    subject: "Votre surveillance Ph\u00e9meApp est active",
    htmlBody: html,
    from: "alerte@phemeapp.ch",
    name: "Ph\u00e9meApp"
  });

  Logger.log("Email bienvenue envoye a " + email);
}

// ─────────────────────────────────────────────
// C) WEB APP - ECRITURE HISTORIQUE DANS SHEET
// ─────────────────────────────────────────────
function doPost(e) {
  try {
    var payload = JSON.parse(e.postData.contents);
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
