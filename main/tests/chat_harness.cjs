// ワークスペース画面（static/tekne_chat/chat.js）をブラウザなしで動かすための簡易DOM
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
    constructor(id = '') {
        this.id = id; this.listeners = {}; this.children = []; this.dataset = {}; this.value = ''; this.textContent = '';
        this.hidden = false; this.disabled = false; this.options = []; this.open = false;
        this.style = { setProperty() {} };
        this.classList = { toggle() {}, add() {}, remove() {}, contains() { return false; } };
    }
    addEventListener(event, callback) { (this.listeners[event] ||= []).push(callback); }
    dispatch(event, payload = {}) { for (const callback of this.listeners[event] || []) callback({ preventDefault() {}, stopPropagation() {}, ...payload }); }
    async dispatchAsync(event, payload = {}) { for (const callback of this.listeners[event] || []) await callback({ preventDefault() {}, stopPropagation() {}, ...payload }); }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this.children = items; this.options = items.filter((item) => item instanceof Option); }
    replaceWith() {}
    add(option) { this.options.push(option); }
    setAttribute(name, value) { this[name] = value; }
    getAttribute(name) { return this[name]; }
    removeAttribute() {}
    querySelector() { return null; }
    querySelectorAll() { return []; }
    contains(node) { return node === this || this.children.some((child) => child instanceof Element && child.contains(node)); }
    closest() { return new Element(); }
    showModal() { this.open = true; }
    close() { this.open = false; this.dispatch('close'); }
    focus() {}
    requestSubmit() {}
}
class Option { constructor(text, value) { this.text = text; this.value = value; } }

function setup(fetchImpl, { prompt = () => null, confirm = () => true } = {}) {
    const ids = new Map();
    const byId = (id) => { if (!ids.has(id)) ids.set(id, new Element(id)); return ids.get(id); };
    const app = byId('tekne-chat-app');
    app.dataset = { csrf: 'csrf', currentUser: 'a', initialGroup: '', initialFile: '', initialFolder: '' };
    byId('tc-group-search').value = '';
    byId('tc-file-search').value = '';
    byId('tc-side-panel').hidden = true;
    byId('tc-entry-menu').hidden = true;
    const requests = [];
    const documentListeners = {};
    const location = { protocol: 'http:', host: 'test', search: '', href: '' };
    const context = vm.createContext({
        document: {
            getElementById: byId, querySelectorAll: () => [], querySelector: () => null, createElement: () => new Element(),
            createTextNode: (t) => t, hidden: false,
            addEventListener: (event, callback) => { (documentListeners[event] ||= []).push(callback); },
        },
        window: { addEventListener() {}, location },
        location,
        WebSocket: class { constructor() { this.readyState = 0; } addEventListener() {} close() {} send() {} },
        FormData: class { constructor() { this.data = new Map(); } set(key, value) { this.data.set(key, value); } },
        URLSearchParams, Option, setTimeout, clearTimeout, setInterval: () => 0, console, prompt, confirm,
        fetch: async (path, options = {}) => {
            requests.push({ path, method: options.method || 'GET', body: options.body?.data });
            return fetchImpl(path, options);
        },
    });
    vm.runInContext(fs.readFileSync('static/tekne_chat/chat.js', 'utf8'), context);
    const documentDispatch = (event, payload = {}) => { for (const callback of documentListeners[event] || []) callback(payload); };
    return { byId, requests, location, documentDispatch };
}

// 子要素を深さ優先でたどって、条件に合う Element を集める
function findAll(node, predicate, out = []) {
    for (const child of node.children || []) {
        if (!(child instanceof Element)) continue;
        if (predicate(child)) out.push(child);
        findAll(child, predicate, out);
    }
    return out;
}

const ok = (body) => ({ ok: true, json: async () => body });
const settle = async (times = 3) => { for (let i = 0; i < times; i += 1) await new Promise((resolve) => setTimeout(resolve, 0)); };

module.exports = { Element, Option, setup, findAll, ok, settle };
