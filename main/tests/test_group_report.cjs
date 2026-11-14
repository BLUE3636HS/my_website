// 成果レポート記入画面：個人スペースはWebSocketを使わずHTTPだけで保存する
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');

class Element {
    constructor(attrs = {}) { this.dataset = attrs.dataset || {}; this.listeners = {}; this.value = ''; this.textContent = ''; this.children = []; this.classList = { toggle() {} }; }
    addEventListener(event, callback) { this.listeners[event] = callback; }
    querySelector(selector) { return this.parts?.[selector] || null; }
    replaceChildren(...items) { this.children = items; }
    append(...items) { this.children.push(...items); }
}

function setup({ personal, fetch, templateKey = 'key-1' }) {
    const input = new Element();
    const field = new Element({ dataset: { key: 'f1' } });
    field.parts = { '.report-value': input };
    const ids = {
        'group-report': new Element({ dataset: { groupId: 'g1', csrf: 'csrf', user: 'a', ...(personal ? { personal: '1' } : {}) } }),
        'report-initial': { textContent: JSON.stringify({ template_key: templateKey, data: { f1: { value: '', images: [] } } }) },
        'report-save-state': new Element(), 'report-connection': personal ? null : new Element(),
        'report-versions': new Element(), 'report-save-version': new Element(), 'report-presence': new Element(),
    };
    const sockets = [];
    const calls = [];
    const alerts = [];
    let reloaded = 0;
    const storage = new Map();
    const context = vm.createContext({
        document: {
            getElementById: id => ids[id], activeElement: null,
            querySelectorAll: selector => selector === '.report-field' ? [field] : [],
            querySelector: () => field, createElement: () => new Element(),
        },
        CSS: { escape: value => value },
        localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
        WebSocket: class { constructor(url) { sockets.push(url); this.readyState = 0; } send() {} },
        location: { protocol: 'http:', host: 'test', reload: () => { reloaded += 1; } },
        alert: message => alerts.push(message),
        fetch: async (path, options = {}) => { calls.push({ path, options }); return fetch(path, options); },
        setTimeout, clearTimeout, Promise, JSON, Map, Set, Number, String, Object, Error,
    });
    context.document.activeElement = input;
    vm.runInContext(fs.readFileSync('static/group_report.js', 'utf8'), context);
    return { input, ids, sockets, calls, alerts, storage, reloaded: () => reloaded };
}

const ok = body => ({ ok: true, status: 200, json: async () => body });
const settle = () => new Promise(resolve => setTimeout(resolve, 0));

test('personal space never opens a WebSocket and can edit immediately', async () => {
    const page = setup({ personal: true, fetch: async () => ok({ state: { template_key: 'key-1' }, versions: [] }) });
    await settle();
    assert.deepEqual(page.sockets, []);
    assert.equal(page.ids['report-save-state'].textContent, '保存済み');
    await page.input.listeners.focus();
    assert.equal(page.input.readOnly, false);
});

test('personal save sends the template key over HTTP', async () => {
    const page = setup({ personal: true, fetch: async (path) => ok(path.endsWith('/save') ? { ok: true } : { state: { template_key: 'key-1' }, versions: [] }) });
    await settle();
    await page.input.listeners.focus();
    page.input.value = '本文';
    page.input.listeners.input();
    await new Promise(resolve => setTimeout(resolve, 700));
    const save = page.calls.find(call => call.path.endsWith('/save'));
    assert.ok(save, 'save request was not sent');
    const body = JSON.parse(save.options.body);
    assert.equal(body.template_key, 'key-1');
    assert.equal(body.data.f1.value, '本文');
    assert.equal(page.ids['report-save-state'].textContent, '保存済み');
});

test('personal save on an old template keeps the draft locally and reloads', async () => {
    const page = setup({ personal: true, fetch: async (path) => path.endsWith('/save')
        ? { ok: false, status: 412, json: async () => ({ detail: '先生がテンプレートを更新しました。' }) }
        : ok({ state: { template_key: 'key-1' }, versions: [] }) });
    await settle();
    await page.input.listeners.focus();
    page.input.value = '書きかけ';
    page.input.listeners.input();
    await new Promise(resolve => setTimeout(resolve, 700));
    assert.equal(page.reloaded(), 1);
    assert.equal(page.alerts.length, 1);
    assert.equal(JSON.parse(page.storage.get('group-report:g1')).f1.value, '書きかけ');
});

test('group report still connects with WebSocket and waits for it before editing', async () => {
    const page = setup({ personal: false, fetch: async () => ok({}) });
    await settle();
    assert.equal(page.sockets.length, 1);
    assert.match(page.sockets[0], /\/ws\/group-reports\/g1$/);
    assert.equal(page.input.readOnly, true);
});
