// Optional browser smoke test for dynamic study image inputs.
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async () => {
    const browser = await chromium.launch({headless: true,
        ...(process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {})});
    try {
        const page = await browser.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        let posted = '';
        const template = [{id: 2, name: '画像テンプレート', fields: [
            {id: 20, label: '本文', field_type: 'text', required: false, max_length: 0},
            {id: 21, label: '実験写真', field_type: 'image', required: true, max_length: 50,
             image_size: 'small', image_alignment: 'center'}]}];
        const html = `<!doctype html><form id="study-form" action="/addform">
          <input name="csrf_token" value="csrf"><input name="submission_token" value="token">
          <input name="name" required value="研究"><textarea name="introduce" required>紹介</textarea>
          <label><input type="radio" name="registration_type" value="pdf">PDF</label>
          <label><input type="radio" name="registration_type" value="template" checked>template</label>
          <div id="pdf-panel"><input type="file" id="pdf_file" name="pdf"></div>
          <div id="template-panel"><select id="template-id" name="template_id"><option value="2">画像</option></select><div id="template-fields"></div></div>
          <p id="submission-status"></p><button id="add_btn">送信</button></form>
          <script type="application/json" id="study-templates">${JSON.stringify(template)}</script>`;
        await page.route('http://study.test/**', async route => {
            if (route.request().method() === 'POST') {
                posted = route.request().postDataBuffer().toString('utf8');
                return route.fulfill({contentType: 'application/json', body: JSON.stringify({ok: true, id: 1})});
            }
            return route.fulfill({contentType: 'text/html', body: html});
        });
        await page.goto('http://study.test/addform');
        await page.addScriptTag({path: 'static/addform.js'});
        const picker = page.locator('.study-image-field input[type=file]');
        const tinyPng = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');
        await picker.setInputFiles([
            {name: 'one.png', mimeType: 'image/png', buffer: tinyPng},
            {name: 'two.png', mimeType: 'image/png', buffer: tinyPng}]);
        assert.equal(await page.locator('.study-image-card').count(), 2);
        await page.locator('.study-image-card input[type=text]').nth(0).fill('最初');
        await page.locator('.study-image-card input[type=text]').nth(1).fill('次');
        await page.getByRole('button', {name: '↑ 上へ'}).nth(1).click();
        assert.equal(await page.locator('.study-image-card input[type=text]').nth(0).inputValue(), '次');
        await page.locator('#add_btn').click();
        await page.getByText('研究成果を追加しました。', {exact: false}).waitFor();
        assert.ok(posted.includes('image_21'));
        assert.ok(posted.indexOf('two.png') < posted.indexOf('one.png'));
        assert.ok(posted.indexOf('次') < posted.indexOf('最初'));
        assert.deepEqual(errors, []);
        console.log('Study image form: selection, captions, reorder, and ordered multipart submission passed.');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
