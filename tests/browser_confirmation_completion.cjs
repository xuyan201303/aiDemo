/* Normal-browser acceptance against explicitly injected synthetic 18772 data.
 * GET snapshots are evidence only. All business mutations use visible controls.
 * No SDK/AI, upload, test-injection HTTP route, or formal 8765 traffic is allowed.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const origin = 'http://127.0.0.1:18772';
const output = process.env.SANZO_CONFIRMATION_BROWSER_OUTPUT;
assert(output?.startsWith('/Volumes/SANZO_TEST/private/tmp/confirmation-completion-20260930/browser/'));
fs.mkdirSync(output, { recursive: true });
const results = [], requests = [], errors = [];
let page, browser;
const save = (name, value) => fs.writeFileSync(path.join(output, name), JSON.stringify(value, null, 2));
async function shot(name) { await page.screenshot({ path: path.join(output, name + '.png'), fullPage: true }); }
async function snap(id) { const r = await page.request.get(origin + '/api/cases/' + id); assert.equal(r.status(), 200); return r.json(); }
function member(snapshot, id) { return snapshot.comparison.rows.flatMap(r => r.member_items).find(i => i.item_id === id); }
async function openCase(id) {
  await page.goto(origin + '/cases/' + id, { waitUntil: 'networkidle' });
  await page.locator('#case-detail-view').waitFor({ state: 'visible' });
}
async function rowFor(id, targetPage = page) {
  const snapshot = await (await targetPage.request.get(origin + '/api/cases/' + id.replace(/-item-\d+$/, ''))).json();
  const index = snapshot.comparison.rows.findIndex(r => r.item_ids.includes(id));
  assert(index >= 0, id);
  await targetPage.locator('.row-confirm-button').nth(index).click();
  await targetPage.locator('#edit-dialog').waitFor({ state: 'visible' });
  return targetPage.locator(`[data-member-id="${id}"]`);
}
async function openCondition(id, field='equipment', targetPage=page) {
  const card = await rowFor(id, targetPage);
  await card.getByRole('button', { name: '適用条件を修正', exact: true }).click();
  await targetPage.locator('#condition-field').selectOption(field);
}
async function preview(button, name) {
  await page.locator(button).click();
  await page.locator('#preview-dialog').waitFor({state:'visible'});
  await page.locator('#preview-frame').waitFor({state:'visible'});
  await page.waitForTimeout(350);
  await shot(name);
  await page.locator('#preview-dialog .close-dialog').first().click();
}
async function submit(button, endpointSuffix, targetPage=page) {
  const wait = targetPage.waitForResponse(r => r.request().method()==='POST' && r.url().endsWith(endpointSuffix));
  await targetPage.locator(button).click();
  const response = await wait;
  const body = await response.json();
  assert.equal(response.status(), 200, JSON.stringify(body));
  await targetPage.locator('#edit-dialog').waitFor({state:'hidden'});
  await targetPage.waitForLoadState('networkidle');
  return body;
}
async function confirmCondition(id, field, tag, {change, previewSource=false}={}) {
  await openCondition(id, field);
  if (change) { await page.locator('#condition-intent').selectOption('change'); await page.locator('#condition-value').fill(change); }
  await page.locator('#condition-evidence').fill(`合成原件 Synthetic 第3行、${field} と対応する費目・単価を目視確認。実SDK/AI出力ではありません。`);
  if (previewSource) await preview('#condition-preview', tag+'-original');
  if (tag) await shot(tag+'-operation');
  const response = await submit('#condition-save', change ? '/conditions' : '/quote-confirmation');
  if (tag) { save(tag+'-save.json', response); await shot(tag+'-saved'); }
  return response;
}
async function confirmSource(id, tag, showPreview=true) {
  const card = await rowFor(id);
  await card.getByRole('button', {name:'原文の照合を記録', exact:true}).click();
  if (showPreview) await preview('#source-review-preview', tag+'-original');
  await page.locator('#source-review-evidence').fill(`合成原件の第1ページ／Synthetic第3行。${id} に対応する原文費目、表示単価とUSDを照合。ページ表示のみで自動行定位を主張しません。`);
  await page.locator('#source-review-ack').check();
  await shot(tag+'-operation');
  const response = await submit('#source-review-save', '/quote-confirmation');
  save(tag+'-save.json', response); await shot(tag+'-saved');
  return response;
}
async function checkpoint(id, tag, draft) {
  await page.reload({waitUntil:'networkidle'});
  const snapshot=await snap(id); assert.equal(snapshot.export_draft, draft, id+' draft');
  save(tag+'-snapshot.json', snapshot); await shot(tag+'-refreshed');
  const download = page.waitForEvent('download'); await page.locator('#export-button').click();
  const xlsx=await download; await xlsx.saveAs(path.join(output, tag+'.xlsx'));
  await page.locator('#sidebar-cases').click();
  await page.locator('#case-list-view').waitFor({state:'visible'});
  const status=await page.locator(`[data-case-id="${id}"] .case-status`).innerText();
  assert.equal(status, draft ? '確認必要' : '確認済み', id+' list status');
  await shot(tag+'-list');
  save(tag+'-checkpoint.json',{draft:snapshot.export_draft,caseStatus:status,rows:snapshot.comparison.rows.map(r=>({name:r.display_name,status:r.confirmation_status,items:r.item_ids})),excel:xlsx.suggestedFilename()});
  await openCase(id); return snapshot;
}
async function caseEvidence(id, fn) {
  await openCase(id); const before=await snap(id); save(id+'-before.json',before);await shot(id+'-before');
  await fn(before); results.push({case_id:id,status:'PASS'});save('browser-results.json',{status:'RUNNING',results});
}

(async()=>{
 browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_HEADLESS_SHELL});
 const context=await browser.newContext({viewport:{width:1600,height:1100},acceptDownloads:true});
 await context.route('**/*',async route=>{
   const request=route.request();const url=new URL(request.url());
   if(url.origin!==origin)throw Error('Unexpected origin '+url.origin);
   requests.push({method:request.method(),path:url.pathname});
   if(/\/process|\/documents$/.test(url.pathname)&&request.method()!=='GET')throw Error('Prohibited processing/upload');
   await route.continue();
 });
 page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',e=>errors.push(e.message));
 await openCase('cc-current');await shot('00-screenshot-persistence-check');assert(fs.statSync(path.join(output,'00-screenshot-persistence-check.png')).size>1000);

 await caseEvidence('cc-current', async before=>{
   const id='cc-current-item-1';assert(member(before,id).review_facts.review_reasons.includes('適用条件未確認'));
   for(const field of ['equipment','billing_unit','charge_scope','extra_terms'])await confirmCondition(id,field,'01-'+field,{previewSource:field==='equipment'});
   const after=await checkpoint('cc-current','01-current-complete',false);
   assert.deepEqual(after.items[0].observation,before.items[0].observation);assert.deepEqual(after.items[0].condition_overrides,{});
   assert.equal(after.items[0].current_memory_version,before.items[0].current_memory_version);
   assert.equal(member(after,id).review_facts.condition_confirmations.equipment,true);
 });
 await caseEvidence('cc-correct',async before=>{
   const id='cc-correct-item-1';await confirmSource(id,'02-source-before-change');
   await confirmCondition(id,'equipment','02-correction',{change:'40FT',previewSource:true});
   let after=await snap('cc-correct');assert.equal(after.items.find(i=>i.item_id===id).effective_context.equipment,'40FT');
   const sibling=after.items.find(i=>i.item_id.endsWith('-2')),oldSibling=before.items.find(i=>i.item_id.endsWith('-2'));
   for(const key of ['observation','source_ref','current_price','currency','version','current_fee_id'])assert.deepEqual(sibling[key],oldSibling[key]);
   await checkpoint('cc-correct','02-after-condition-before-classification',true);
   await rowFor(id);await submit('#save-correction','/confirmation');
   const stale=await checkpoint('cc-correct','02-classified-source-recheck-required',true);
   assert.equal(member(stale,id).quote_confirmations.source.valid,false);
   await confirmSource(id,'02-source-after-change');
   after=await checkpoint('cc-correct','02-future-reminder-complete',false);
   assert(member(after,id).review_facts.learning_notices.length>0);assert.equal(member(after,id).learning_eligible,false);
   assert.equal(after.comparison.rows.length,2);
 });
 await caseEvidence('cc-source',async before=>{
   assert.equal(before.comparison.rows[0].item_ids.length,2);
   await confirmSource('cc-source-item-1','03-first-source');
   let after=await checkpoint('cc-source','03-only-first-confirmed',true);
   assert.equal(member(after,'cc-source-item-1').review_facts.review_required,false);
   assert.equal(member(after,'cc-source-item-2').review_facts.review_required,true);
   await confirmSource('cc-source-item-2','03-second-source');after=await checkpoint('cc-source','03-source-complete',false);
   for(const item of after.items){assert.equal(item.source_ref.locator_status,'PAGE_ONLY');assert.equal(item.observation.source_ready,false);}
 });
 await caseEvidence('cc-missing',async()=>{
   await openCondition('cc-missing-item-1');await page.locator('#condition-evidence').fill('原文に必要な機材がなく、この値を確認できません。');await page.locator('#condition-save').click();
   assert.match(await page.locator('#condition-error').innerText(),/入力|未取得|確認/);await shot('04-missing-condition-blocked');
   await page.locator('#condition-dialog .close-dialog').first().click();await page.locator('#edit-dialog .close-dialog').first().click();
   const after=await checkpoint('cc-missing','04-missing-remains-pending',true);assert(member(after,'cc-missing-item-1').review_facts.review_reasons.includes('適用条件未確認'));
 });
 for(const [id,text]of[['cc-conflict','競合'],['cc-revoked','撤回']])await caseEvidence(id,async()=>{
   await confirmSource(id+'-item-1','05-'+id);
   const after=await checkpoint(id,'05-'+id+'-blocked',true);assert(member(after,id+'-item-1').review_facts.review_reasons.some(r=>r.includes(text)));
 });
 await caseEvidence('cc-profile',async()=>{
   await confirmSource('cc-profile-item-1','06-profile-source');
   const after=await checkpoint('cc-profile','06-profile-complete',false);
   assert.equal(after.items[0].observation.forwarder_id,null);assert.equal(after.items[0].observation.context.transport_mode,null);
   assert(member(after,'cc-profile-item-1').review_facts.metadata_notices.length>0);
   assert.equal(after.tasks[0].state,'REVIEW_REQUIRED');assert.equal(after.document_reviews['cc-profile-doc-1'].current_state,'AVAILABLE');
 });
 await caseEvidence('cc-coverage',async()=>{
   const id='cc-coverage-item-1';for(const field of ['equipment','billing_unit','charge_scope','extra_terms'])await confirmCondition(id,field,'07-'+field);
   await confirmSource(id,'07-source');await checkpoint('cc-coverage','07-before-document-coverage',true);
   await page.getByRole('button',{name:'資料全体の明細を確認',exact:true}).click();await page.locator('#document-review-count').fill('1');
   await page.locator('#document-review-evidence').fill('合成PDF第1ページの明細は1件です。原文費目・条件・1900 USD と比較表1件を全件照合しました。');await page.locator('#document-review-ack').check();
   await shot('07-document-operation');const wait=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/review'));
   await page.locator('#document-review-save').click();const response=await wait;assert.equal(response.status(),200,await response.text());
   await page.locator('#document-review-dialog').waitFor({state:'hidden'});await page.waitForLoadState('networkidle');await shot('07-document-saved');
   const after=await checkpoint('cc-coverage','07-coverage-complete',false);assert.equal(after.tasks[0].state,'REVIEW_REQUIRED');
   assert.equal(after.document_reviews['cc-coverage-doc-1'].coverage_confirmation.valid,true);
 });
 await caseEvidence('cc-unknown-document',async()=>{
   const after=await checkpoint('cc-unknown-document','08-unknown-document-pending',true);
   assert.equal(after.document_reviews['cc-unknown-document-doc-1'].coverage_confirmable,false);
   assert.match(after.document_reviews['cc-unknown-document-doc-1'].coverage_unavailable_reason,/SOURCE_LINE_NOT_EXTRACTED/);
 });
 await caseEvidence('cc-ai',async()=>{
   const after=await checkpoint('cc-ai','09-ai-still-provisional',true);assert.equal(after.items[0].current_fee_id,null);
   assert.match(await page.locator('.compare-table').innerText(),/仮|未確認|確認必要/);
 });
 await caseEvidence('cc-stale',async()=>{
   await openCondition('cc-stale-item-1');await page.locator('#condition-evidence').fill('先に開いた画面・合成原件の機材40FTを確認');
   const other=await context.newPage();await other.goto(origin+'/cases/cc-stale',{waitUntil:'networkidle'});await openCondition('cc-stale-item-1','equipment',other);await other.locator('#condition-evidence').fill('もう一つの画面で原件40FTを確認');
   await submit('#condition-save','/quote-confirmation',other);await other.close();
   const wait=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/quote-confirmation'));await page.locator('#condition-save').click();assert.equal((await wait).status(),409);
   assert.match(await page.locator('#condition-error').innerText(),/更新|最新/);await shot('10-stale-version-blocked');
   await openCase('cc-stale');await checkpoint('cc-stale','10-stale-unchanged',false);
 });
 await caseEvidence('cc-retry',async before=>{
   await openCondition('cc-retry-item-1');await page.locator('#condition-evidence').fill('合成原件40FTを確認。応答喪失後に同一保存を再送する検証。');
   let originalBody;await page.route('**/quote-confirmation',async route=>{originalBody=route.request().postDataJSON();await route.fetch();await route.abort('failed');},{times:1});
   await page.locator('#condition-save').click();await page.locator('#condition-error').waitFor({state:'visible'});await shot('11-lost-response-retry');
   const response=await submit('#condition-save','/quote-confirmation');assert(originalBody.idempotency_key);save('11-retry-response.json',response);
   const after=await checkpoint('cc-retry','11-retry-complete',false);assert.equal(after.items[0].version,before.items[0].version+1);
 });
 assert.deepEqual(errors,[]);save('browser-network.json',requests);save('browser-results.json',{status:'PASS',origin,fixture:'INJECTED_SYNTHETIC_NOT_REAL_SDK_OR_AI',results,pageErrors:errors,sdkCalls:0,aiCalls:0});
 await browser.close();console.log(JSON.stringify({status:'PASS',scenarios:results.length,output},null,2));
})().catch(async error=>{save('browser-results.json',{status:'FAIL',results,error:error.stack,pageErrors:errors});save('browser-network.json',requests);if(page)await shot('FAILURE').catch(()=>{});if(browser)await browser.close();console.error(error);process.exit(1);});
