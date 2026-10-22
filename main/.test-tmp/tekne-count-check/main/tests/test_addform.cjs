// Small DOM harness: exercise generated fields and async submission without a browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');

class Element {
    constructor(tag = '') { this.tag = tag; this.children = []; this.listeners = {}; this.value = ''; }
    append(...items) { this.children.push(...items); }
    replaceChildren() { this.children = []; }
    querySelectorAll(tag) { return this.children.filter(item => item.tag === tag); }
    addEventListener(event, callback) { this.listeners[event] = callback; }
    setAttribute() {}
    removeAttribute() {}
    setCustomValidity(message) { this.validationMessage = message; }
}

function setup(fetch) {
    const ids = Object.fromEntries(['study-form', 'add_btn', 'submission-status', 'study-templates',
        'template-id', 'template-fields', 'pdf-panel', 'pdf_file', 'template-panel'].map(id => [id, new Element()]));
    ids['study-templates'].textContent = JSON.stringify([
        { id: 1, fields: Array.from({ length: 9 }, (_, i) => ({ id: i + 1, label: `項目${i}`, required: false })) },
        { id: 2, fields: Array.from({ length: 20 }, (_, i) => ({ id: i + 20, label: `別項目${i}`, required: i === 0, max_length: i === 0 ? 5 : 0 })) }
    ]);
    ids['template-id'].value = '1';
    const form = ids['study-form'];
    form.elements = { registration_type: { value: 'pdf' } };
    form.action = '/addform';
    form.reportValidity = () => true;
    const radios = [new Element(), new Element()];
    form.querySelectorAll = () => radios;
    const context = vm.createContext({
        document: { getElementById: id => ids[id], createElement: tag => new Element(tag), createTextNode: text => text },
        FormData: class {}, fetch
    });
    vm.runInContext(fs.readFileSync('static/addform.js', 'utf8'), context);
    return { ids, radios, form, submit: () => form.listeners.submit({ preventDefault() {} }) };
}

test('PDF/template switching and arbitrary DB field counts', () => {
    const { ids, radios, form } = setup();
    assert.equal(ids['template-fields'].children.length, 18);
    assert.equal(ids['template-panel'].hidden, true);
    assert.equal(ids['pdf_file'].disabled, false);
    form.elements.registration_type.value = 'template';
    radios[1].listeners.change();
    assert.equal(ids['pdf-panel'].hidden, true);
    assert.equal(ids['pdf_file'].disabled, true);
    assert.equal(ids['template-id'].disabled, false);
    ids['template-id'].value = '2';
    ids['template-id'].listeners.change();
    const inputs = ids['template-fields'].querySelectorAll('textarea');
    assert.equal(inputs.length, 20);
    assert.equal(inputs[0].required, true);
    assert.equal(inputs[1].required, false);
});

test('field limit counts codepoints and normalizes CRLF', () => {
    const {ids} = setup();
    ids['template-id'].value = '2';
    ids['template-id'].listeners.change();
    const input = ids['template-fields'].querySelectorAll('textarea')[0];
    input.value = 'あ😀\r\n b'; input.listeners.input();
    assert.equal(input.validationMessage, '');
    input.value += 'c'; input.listeners.input();
    assert.notEqual(input.validationMessage, '');
});

for (const mode of ['pdf', 'template']) {
    test(`${mode}: success is shown only after response; double submit blocked`, async () => {
        let resolve;
        let calls = 0;
        const { ids, form, submit } = setup(() => { calls++; return new Promise(done => { resolve = done; }); });
        form.elements.registration_type.value = mode;
        const pending = submit();
        assert.equal(ids.add_btn.disabled, true);
        assert.equal(ids['submission-status'].textContent, '登録しています…');
        await submit();
        assert.equal(calls, 1);
        resolve({ ok: true, json: async () => ({ ok: true }) });
        await pending;
        assert.equal(ids['submission-status'].textContent, '研究成果を追加しました。');
        assert.equal(ids.add_btn.disabled, true);
        await submit();
        assert.equal(calls, 1);
    });
}

test('server failure allows retry and never displays success', async () => {
    const { ids, submit } = setup(async () => ({ ok: false, json: async () => ({ detail: '登録失敗' }) }));
    await submit();
    assert.equal(ids['submission-status'].textContent, '登録失敗');
    assert.equal(ids.add_btn.disabled, false);
});

test('login redirect or network failure never displays success', async () => {
    for (const fetch of [async () => ({ ok: true, redirected: true, json: async () => { throw new Error(); } }),
                         async () => { throw new Error('通信失敗'); }]) {
        const { ids, submit } = setup(fetch);
        await submit();
        assert.notEqual(ids['submission-status'].textContent, '研究成果を追加しました。');
        assert.equal(ids.add_btn.disabled, false);
    }
});
