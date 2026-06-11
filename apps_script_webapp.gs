/**
 * PhemeApp - Web App pour ecriture dans Google Sheet
 * 
 * DEPLOIEMENT (une seule fois depuis Apps Script) :
 * 1. Extensions > Apps Script
 * 2. Remplacer le code par ce fichier
 * 3. Deployer > Nouvelle mise en prod
 *    - Type: Application Web
 *    - Executer en tant que: Moi (arnaud.mathier@gmail.com)
 *    - Acces: Tout le monde
 * 4. Copier l URL generee
 * 5. Dans GitHub > Settings > Secrets > ajouter APPS_SCRIPT_WEBAPP_URL = URL copiee
 */

var SHEET_ID = "1YLK-KV_W7sNraeZdsyttykh1OnYU5aJOhl_NIqwFsJw";

function doPost(e) {
  try {
    var payload = JSON.parse(e.postData.contents);
    var tabName = payload.tab;
    var row = payload.row;
    
    var ss = SpreadsheetApp.openById(SHEET_ID);
    var sheet = ss.getSheetByName(tabName);
    
    if (!sheet) {
      return ContentService
        .createTextOutput(JSON.stringify({error: "Onglet non trouve: " + tabName}))
        .setMimeType(ContentService.MimeType.JSON);
    }
    
    // Construire la ligne selon l onglet
    var values = [];
    if (tabName === "Historique Alertes") {
      values = [
        row.date_envoi,
        row.email,
        row.nom,
        row.label_adresse,
        row.adresse,
        row.no_camac,
        row.lieu,
        row.commune,
        row.nature_travaux,
        row.distance_m,
        row.date_fao,
        row.lien
      ];
    } else if (tabName === "Zone Elargie") {
      values = [
        row.date_detection,
        row.email,
        row.nom,
        row.label_adresse,
        row.adresse,
        row.no_camac,
        row.lieu,
        row.commune,
        row.nature_travaux,
        row.distance_m,
        row.date_fao,
        row.lien,
        row.inclus_newsletter ? "Oui" : "Non"
      ];
    } else {
      // Format generique: toutes les valeurs du dict
      values = Object.values(row);
    }
    
    sheet.appendRow(values);
    
    return ContentService
      .createTextOutput(JSON.stringify({
        success: true,
        tab: tabName,
        rows: sheet.getLastRow()
      }))
      .setMimeType(ContentService.MimeType.JSON);
      
  } catch(err) {
    return ContentService
      .createTextOutput(JSON.stringify({error: err.toString()}))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// Test depuis Apps Script (Executer > testWebApp)
function testWebApp() {
  var testRow = {
    date_envoi: new Date().toISOString(),
    email: "test@test.com",
    nom: "Test",
    label_adresse: "Maison",
    adresse: "Route d Adversan 15 1832 Villard sur Chamby",
    no_camac: 249553,
    lieu: "Chemin du Grebe 8",
    commune: "Preverenges",
    nature_travaux: "Test",
    distance_m: 270,
    date_fao: "05.06.2026",
    lien: "https://www.faovd.ch"
  };
  
  var ss = SpreadsheetApp.openById(SHEET_ID);
  var sheet = ss.getSheetByName("Historique Alertes");
  sheet.appendRow(Object.values(testRow));
  Logger.log("Test OK - ligne ajoutee dans Historique Alertes");
}
