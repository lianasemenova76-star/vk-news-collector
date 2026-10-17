/** Google Apps Script: deploy as Web App (execute as Me; access Anyone).
 * Set script property ARCHIVE_TOKEN to a long random token.
 * Share neither deployment URL nor token publicly.
 */
const SHEET_ID = "1TwOkF5gU_9Rv4DSEszEYZ5Cfu5qgw4Etq5kiYpAozis";
const TAB = "Найденные посты";
function doPost(e) {
  let lock = LockService.getScriptLock();
  if (!lock.tryLock(25000)) return output({ok:false,error:"busy"});
  try {
    let data = JSON.parse(e.postData.contents);
    const secret = PropertiesService.getScriptProperties().getProperty("ARCHIVE_TOKEN");
    if (!secret || data.token !== secret) return output({ok:false,error:"unauthorized"});
    const props = PropertiesService.getScriptProperties();
    if (data.action === "status") return output({ok:true,offset:Number(props.getProperty("NEXT_OFFSET")||0)});
    if (data.action !== "append" || !Array.isArray(data.records) || data.records.length>100) return output({ok:false,error:"invalid batch"});
    const sheet = SpreadsheetApp.openById(SHEET_ID).getSheetByName(TAB);
    const last = sheet.getLastRow();
    const existing = last>1 ? new Set(sheet.getRange(2,5,last-1,1).getDisplayValues().flat()) : new Set();
    const rows = [];
    data.records.forEach(r => {
      if (typeof r.id !== "string" || existing.has(r.id)) return;
      existing.add(r.id);
      rows.push([safe(r.date),safe(r.terms),safe(r.text),safe(r.url),safe(r.id),safe(r.forms)]);
    });
    if (rows.length) sheet.getRange(last+1,1,rows.length,6).setValues(rows);
    const next = Number(data.next_offset);
    if (Number.isSafeInteger(next) && next>=0) props.setProperty("NEXT_OFFSET",String(Math.max(next,Number(props.getProperty("NEXT_OFFSET")||0))));
    return output({ok:true,added:rows.length,offset:Number(props.getProperty("NEXT_OFFSET")||0)});
  } catch(err) { return output({ok:false,error:String(err)}); }
  finally { lock.releaseLock(); }
}
function safe(v) {
  const s = String(v ?? "");
  return /^[=+@\-]/.test(s) ? "'"+s : s;
}
function output(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
