/* Isolated 18770 browser acceptance. The test server uses only synthetic data. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const origin = 'http://127.0.0.1:18770';
const caseId = 'synthetic-route-case';
const output = process.env.SANZO_ROUTE_SCREENSHOTS;
if (!output || !path.isAbsolute(output)) throw new Error('Absolute SANZO_ROUTE_SCREENSHOTS required');
fs.mkdirSync(output, { recursive: true });

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROME_HEADLESS_SHELL,
    args: ['--no-sandbox', '--disable-gpu'],
  });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, acceptDownloads: true });
  page.setDefaultTimeout(10000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const currentPath = () => new URL(page.url()).pathname;
  const cases = page.locator('#case-list-view');
  const detail = page.locator('#case-detail-view');
  const sidebar = page.locator('#sidebar-cases');

  await page.goto(origin + '/', { waitUntil: 'networkidle' });
  assert.equal(currentPath(), '/cases');
  await cases.waitFor({ state: 'visible' });
  assert.equal(await sidebar.getAttribute('aria-current'), 'page');
  await page.screenshot({ path: path.join(output, '01_cases_with_sidebar.png') });

  await page.locator('#case-search').fill('合成見積');
  assert.equal(await page.locator('#case-list .case-list-entry').count(), 1);
  await page.locator('#case-search').fill('');
  await page.locator(`[data-case-id="${caseId}"]`).click();
  await detail.waitFor({ state: 'visible' });
  assert.equal(currentPath(), '/cases/' + caseId);
  assert.equal(await sidebar.getAttribute('aria-current'), 'page');
  assert.equal(await page.locator('.compare-table thead th').count(), 4);
  const right = await page.locator('.compare-table thead th').last().boundingBox();
  assert(right && right.x + right.width <= 1920, 'three quote columns must fit at 1920px');
  await page.screenshot({ path: path.join(output, '02_case_detail_with_sidebar.png') });

  await page.reload({ waitUntil: 'networkidle' });
  await detail.waitFor({ state: 'visible' });
  assert.equal(currentPath(), '/cases/' + caseId);
  await page.screenshot({ path: path.join(output, '03_direct_url_refresh.png') });

  await page.goBack({ waitUntil: 'networkidle' });
  await cases.waitFor({ state: 'visible' });
  assert.equal(currentPath(), '/cases');
  await page.goForward({ waitUntil: 'networkidle' });
  await detail.waitFor({ state: 'visible' });
  assert.equal(currentPath(), '/cases/' + caseId);
  await page.locator('#case-list-button').click();
  await cases.waitFor({ state: 'visible' });
  assert.equal(currentPath(), '/cases');
  await page.goBack({ waitUntil: 'networkidle' });
  await detail.waitFor({ state: 'visible' });
  assert.equal(currentPath(), '/cases/' + caseId);
  await sidebar.click();
  await cases.waitFor({ state: 'visible' });
  assert.equal(currentPath(), '/cases');

  const directList = await browser.newPage();
  await directList.goto(origin + '/cases', { waitUntil: 'networkidle' });
  await directList.locator('#case-list-view').waitFor({ state: 'visible' });
  assert.equal(new URL(directList.url()).pathname, '/cases');
  const directCase = await browser.newPage();
  await directCase.goto(origin + '/cases/' + caseId, { waitUntil: 'networkidle' });
  await directCase.locator('#case-detail-view').waitFor({ state: 'visible' });
  assert.equal(new URL(directCase.url()).pathname, '/cases/' + caseId);
  await directCase.locator('.compare-table .row-confirm-button').first().click();
  await directCase.locator('#edit-dialog').waitFor({ state: 'visible' });
  await directCase.locator('#edit-members .row-member').first().getByRole('button', { name: 'この明細を修正' }).click();
  await directCase.locator('#member-correction-dialog').waitFor({ state: 'visible' });
  await directCase.locator('#member-correction-dialog .close-dialog').first().click();
  await directCase.locator('#edit-members .row-member').first().getByRole('button', { name: '適用条件を修正' }).click();
  await directCase.locator('#condition-dialog').waitFor({ state: 'visible' });
  await directCase.locator('#condition-dialog .close-dialog').first().click();
  await directCase.locator('#edit-dialog .close-dialog').first().click();
  await directCase.locator('.document-card').first().getByRole('button', { name: /原文をプレビュー/ }).click();
  await directCase.locator('#preview-dialog').waitFor({ state: 'visible' });
  await directCase.locator('#preview-dialog .close-dialog').first().click();
  const downloadPromise = directCase.waitForEvent('download');
  await directCase.locator('#export-button').click();
  assert.match((await downloadPromise).suggestedFilename(), /\.xlsx$/);
  assert(await directCase.locator('#files').isVisible());

  for (const legacy of ['/#case=' + caseId, '/cases#case=' + caseId]) {
    await page.goto(origin + legacy, { waitUntil: 'networkidle' });
    await detail.waitFor({ state: 'visible' });
    assert.equal(currentPath(), '/cases/' + caseId);
    assert.equal(new URL(page.url()).hash, '');
  }
  await page.goto(origin + '/cases/not-existing-id', { waitUntil: 'networkidle' });
  await cases.waitFor({ state: 'visible' });
  assert.equal(currentPath(), '/cases');
  assert.match(await page.locator('#notice').innerText(), /案件が見つかりません/);

  await page.locator('#new-case-button').click();
  await page.locator('#case-name').fill('合成・新規URL検証');
  await page.getByRole('button', { name: '案件を作成' }).click();
  await detail.waitFor({ state: 'visible' });
  assert.match(currentPath(), /^\/cases\/[0-9a-f-]{36}$/);
  assert.equal(new URL(page.url()).hash, '');
  await page.reload({ waitUntil: 'networkidle' });
  await detail.waitFor({ state: 'visible' });

  const layouts = [];
  await page.goto(origin + '/cases/' + caseId, { waitUntil: 'networkidle' });
  for (const [width, height] of [[1440, 900], [1280, 800]]) {
    await page.setViewportSize({ width, height });
    await detail.waitFor({ state: 'visible' });
    const metrics = await page.evaluate(() => ({
      viewport: window.innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      sidebar: document.querySelector('.sidebar').getBoundingClientRect().width,
      mainLeft: document.querySelector('.app-content').getBoundingClientRect().left,
    }));
    assert(metrics.documentWidth <= metrics.viewport, 'page must not overflow horizontally');
    assert(metrics.mainLeft >= metrics.sidebar, 'sidebar must not cover main content');
    layouts.push({ width, height, ...metrics });
    await page.screenshot({ path: path.join(output, `04_detail_${width}.png`) });
  }
  assert.deepEqual(errors, []);
  await browser.close();
  console.log(JSON.stringify({ status: 'PASS', origin, routes: 10, screenshots: 5, readOnlyWorkflowSmoke: ['row', 'single-item', 'condition', 'source-preview', 'excel-download', 'file-upload-ui'], layouts, pageErrors: errors }, null, 2));
})().catch(error => { console.error(error); process.exit(1); });
