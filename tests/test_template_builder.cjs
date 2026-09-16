// Optional browser smoke test. Uses Playwright and a locally installed Chrome.
// Set PLAYWRIGHT_MODULE and CHROME_PATH if they are not available by default.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async () => {
    const browser = await chromium.launch({headless: true,
        ...(process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {})});
    try {
        const page = await browser.newPage({viewport: {width: 1500, height: 1000}});
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        let payload;
        let fail = true;
        let requests = 0;
        const html = fs.readFileSync('templates/admin/study_template_new.html', 'utf8')
            .replace(/{%[\s\S]*?%}/g, '').replace(/{{ csrf_token }}/g, 'token')
            .replace(/<script[\s\S]*?<\/script>/g, '').replace(/<link[^>]*>/g, '');
        await page.route('http://template.test/**', async route => {
            if (route.request().method() === 'POST') {
                requests++; payload = route.request().postDataJSON();
                await new Promise(resolve => setTimeout(resolve, 80));
                return route.fulfill({status: fail ? 500 : 201, contentType: 'application/json',
                    body: JSON.stringify(fail ? {detail: '保存エラー'} : {ok: true, id: 2})});
            }
            return route.fulfill({contentType: 'text/html', body: html});
        });
        await page.goto('http://template.test/admin/study-templates/new');
        await page.addStyleTag({path: 'static/admin/study_templates.css'});
        await page.addScriptTag({path: 'static/admin/study_templates.js'});
        await page.locator('#template-name').fill('確認テンプレート');
        await page.locator('#field-label').fill('テーマ');
        await page.locator('#heading-size').fill('24');
        await page.locator('#body-size').fill('18');
        await page.locator('#max-length').fill('100');
        await page.locator('#field-required').check();
        await page.locator('#field-apply').click();
        await page.locator('#field-label').fill('背景');
        await page.locator('#hide-heading').check();
        await page.locator('#field-apply').click();
        assert.equal(await page.locator('#field-list > li').count(), 2);
        assert.equal(await page.locator('#preview-paper h3').count(), 1);
        await page.getByRole('button', {name: '背景を上へ', exact: true}).click();
        assert.match(await page.locator('#field-list > li').first().innerText(), /背景/);
        await page.getByRole('button', {name: '背景を編集', exact: true}).click();
        await page.locator('#field-label').fill('変更した背景');
        await page.locator('#field-apply').click();
        // HTML drag handle: reorder back to the original order.
        await page.locator('.field-handle').first().dragTo(page.locator('.field-row').nth(1));
        assert.match(await page.locator('#field-list > li').first().innerText(), /テーマ/);
        await page.locator('#save-template').click();
        await page.getByText('保存エラー', {exact: true}).waitFor();
        assert.equal(await page.locator('#field-list > li').count(), 2);
        assert.equal(await page.locator('#template-name').inputValue(), '確認テンプレート');
        assert.equal(payload.fields[0].heading_font_size, 24);
        assert.equal(payload.fields[1].hide_heading, true);
        await page.setViewportSize({width: 500, height: 900});
        assert.equal(await page.locator('.template-builder').evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length), 1);
        fail = false;
        await page.locator('#save-template').evaluate(button => { button.click(); button.click(); });
        await page.waitForURL('http://template.test/admin/study-templates');
        assert.equal(requests, 2);
        assert.deepEqual(errors, []);
        console.log('Template builder: add, edit, keyboard reorder, drag, preview, failure retention, responsive layout and duplicate-click guard passed.');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
