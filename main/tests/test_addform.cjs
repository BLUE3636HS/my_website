// Small DOM harness: exercise the PDF submission form without a browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');

class Element {
    constructor(tag = '') { this.tag = tag; this.children = []; this.listeners = {}; this.value = ''; }
    append(...items) { this.children.push(...items); }
    addEventListener(event, callback) { this.listeners[event] = callback; }
    setAttribute() {}
    removeAttribute() {}
}

function setup(fetch) {
    const ids = Object.fromEntries(['study-form', 'add_btn', 'submission-status',
        'group-submission-panel', 'chat-group-id'].map(id => [id, new Element()]));
    const form = ids['study-form'];
    form.action = '/addform';
    form.reportValidity = () => true;
    let submissionKind = 'individual';
    form.querySelector = () => ({ value: submissionKind });
    const sentBodies = [];
    const context = vm.createContext({
        document: { getElementById: id => ids[id], createElement: tag => new Element(tag), createTextNode: text => text },
        FormData: class { constructor(source) { this.source = source; sentBodies.push(this); } },
        fetch
    });
    vm.runInContext(fs.readFileSync('static/addform.js', 'utf8'), context);
    const changeKind = kind => {
        submissionKind = kind;
        form.listeners.change({ target: { matches: selector => selector === '[name="submission_kind"]' } });
    };
    return { ids, form, sentBodies, changeKind, submit: () => form.listeners.submit({ preventDefault() {} }) };
}

test('template mode is gone from the script and page', () => {
    const script = fs.readFileSync('static/addform.js', 'utf8');
    const html = fs.readFileSync('templates/addform.html', 'utf8');
    for (const source of [script, html]) {
        assert.doesNotMatch(source, /template-fields|template-panel|study-templates|registration_type/);
    }
    assert.match(html, /<h1>PDFを提出<\/h1>/);
    assert.match(html, /accept="\.pdf,application\/pdf" required/);
});

test('group selector is shown and required only for group submission', () => {
    const { ids, changeKind } = setup();
    assert.equal(ids['group-submission-panel'].hidden, true);
    assert.equal(ids['chat-group-id'].disabled, true);
    assert.equal(ids['chat-group-id'].required, false);
    changeKind('group');
    assert.equal(ids['group-submission-panel'].hidden, false);
    assert.equal(ids['chat-group-id'].disabled, false);
    assert.equal(ids['chat-group-id'].required, true);
    changeKind('individual');
    assert.equal(ids['group-submission-panel'].hidden, true);
    assert.equal(ids['chat-group-id'].required, false);
});

test('success is shown only after response; double submit blocked', async () => {
    let resolve;
    let calls = 0;
    const { ids, form, sentBodies, submit } = setup(() => { calls++; return new Promise(done => { resolve = done; }); });
    const pending = submit();
    assert.equal(ids.add_btn.disabled, true);
    assert.equal(ids['submission-status'].textContent, '提出しています…');
    await submit();
    assert.equal(calls, 1);
    assert.equal(sentBodies.length, 1);
    assert.equal(sentBodies[0].source, form);
    resolve({ ok: true, json: async () => ({ ok: true }) });
    await pending;
    assert.equal(ids['submission-status'].textContent, 'PDFを提出しました。');
    assert.equal(ids.add_btn.textContent, '提出済み');
    assert.equal(ids.add_btn.disabled, true);
    await submit();
    assert.equal(calls, 1);
});

test('server failure allows retry and never displays success', async () => {
    const { ids, submit } = setup(async () => ({ ok: false, json: async () => ({ detail: 'PDFファイルを選択してください。' }) }));
    await submit();
    assert.equal(ids['submission-status'].textContent, 'PDFファイルを選択してください。');
    assert.equal(ids.add_btn.disabled, false);
});

test('login redirect or network failure never displays success', async () => {
    for (const fetch of [async () => ({ ok: true, redirected: true, json: async () => { throw new Error(); } }),
                         async () => { throw new Error('通信失敗'); }]) {
        const { ids, submit } = setup(fetch);
        await submit();
        assert.notEqual(ids['submission-status'].textContent, 'PDFを提出しました。');
        assert.equal(ids.add_btn.disabled, false);
    }
});
