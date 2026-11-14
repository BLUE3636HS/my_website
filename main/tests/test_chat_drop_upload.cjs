// ワークスペースのメッセージ画面にファイルをドロップ → 保存先フォルダを選んで保存する
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { Element, setup: baseSetup, ok, settle } = require('./chat_harness.cjs');

// アップロードの送信だけを取り出しやすくする
function setup(fetchImpl) {
    const page = baseSetup(fetchImpl);
    return { ...page, get uploads() {
        return page.requests.filter((r) => r.method === 'POST' && r.path.endsWith('/files'))
            .map((r) => ({ path: r.path, folder: r.body.get('folder_id'), file: r.body.get('file') }));
    } };
}
const groups = { groups: [{ id: 'g1', name: '植物の研究', kind: 'personal', member_count: 1 }] };
const tree = { folders: [
    { id: 'f1', parent_id: null, name: 'データ', path: 'データ' },
    { id: 'f2', parent_id: 'f1', name: '写真', path: 'データ/写真' },
    { id: 'f3', parent_id: null, name: 'pdf保存場所', path: 'pdf保存場所' },
] };

// 木の中の要素を、深さ（tc-tree-row の入れ子の数）と一緒に並べる
function treeItems(node, depth = 0, out = [], hidden = false) {
    for (const child of node.children || []) {
        if (!(child instanceof Element)) continue;
        if (child.className === 'tc-tree-item' || child.className === 'tc-tree-item tc-tree-root') out.push({ text: child.textContent, depth, selected: child['aria-selected'], el: child, hiddenParent: hidden });
        else if (child.className === 'tc-tree-toggle') out.push({ toggle: true, text: child.textContent, el: child });
        else treeItems(child, child['role'] === 'group' ? depth + 1 : depth, out, hidden || child.hidden);
    }
    return out;
}
const labels = (page) => treeItems(page.byId('tc-drop-tree')).filter((i) => !i.toggle).map((i) => `${'  '.repeat(i.depth)}${i.text}`);
const click = (page, text) => treeItems(page.byId('tc-drop-tree')).find((i) => !i.toggle && i.text === text).el.dispatch('click');

function server(failName = null) {
    return async (path, options = {}) => {
        if (path === '/tekne-chat/api/groups') return ok(groups);
        if (path.includes('/messages')) return ok({ messages: [], next_before_id: null });
        if (path.endsWith('/folder-tree')) return ok(tree);
        if (path.endsWith('/files') && options.method === 'POST') {
            const file = options.body.data.get('file');
            if (file.name === failName) return { ok: false, json: async () => ({ detail: '容量が大きすぎます。' }) };
            return ok({ message: { id: Math.floor(Math.random() * 1e6) + 1, type: 'file_upload', file_name: file.name } });
        }
        return ok({});
    };
}

function dropEvent(files, { folder = false } = {}) {
    const items = files.map((file) => ({ kind: 'file', getAsFile: () => file, webkitGetAsEntry: () => ({ isDirectory: false }) }));
    if (folder) items.push({ kind: 'file', getAsFile: () => ({ name: 'dir', size: 0 }), webkitGetAsEntry: () => ({ isDirectory: true }) });
    return { preventDefault() {}, dataTransfer: { types: ['Files'], items, files, dropEffect: '' } };
}

async function openWorkspace(fetchImpl) {
    const page = setup(fetchImpl);
    await settle(); await settle(); await settle();
    return page;
}

test('dropping files opens the save dialog with every folder as a choice', async () => {
    const page = await openWorkspace(server());
    const conversation = page.byId('tc-conversation');
    conversation.dispatch('dragenter', dropEvent([]));
    assert.equal(page.byId('tc-drop-overlay').hidden, false);
    conversation.dispatch('drop', dropEvent([{ name: 'a.pdf', size: 2048 }, { name: 'b.png', size: 3 * 1048576 }], { folder: true }));
    await settle(); await settle();
    assert.equal(page.byId('tc-drop-overlay').hidden, true);
    assert.equal(page.byId('tc-drop-dialog').open, true);
    // 親子関係が分かる木の形で、子フォルダは親の下に1段下げて並ぶ
    assert.deepEqual(labels(page), ['📁 ルート', '📁 データ', '  📁 写真', '📁 pdf保存場所']);
    assert.equal(treeItems(page.byId('tc-drop-tree'))[0]['selected'], 'true');
    assert.equal(page.byId('tc-drop-selected').textContent, 'ルート');
    assert.equal(page.byId('tc-drop-files').children.length, 2);
    assert.match(page.byId('tc-notice').textContent, /フォルダはそのまま保存できません/);
    assert.equal(page.uploads.length, 0, 'nothing is saved before choosing a folder');
});

test('saving uploads each dropped file to the chosen folder', async () => {
    const page = await openWorkspace(server());
    page.byId('tc-conversation').dispatch('drop', dropEvent([{ name: 'a.pdf', size: 10 }, { name: 'b.pdf', size: 10 }]));
    await settle(); await settle();
    click(page, '📁 写真');
    assert.equal(page.byId('tc-drop-selected').textContent, 'ルート/データ/写真');
    assert.equal(treeItems(page.byId('tc-drop-tree')).find((i) => i.text === '📁 写真').selected, 'true');
    await page.byId('tc-drop-form').dispatchAsync('submit', { preventDefault() {} });
    assert.deepEqual(page.uploads.map((u) => [u.file.name, u.folder]), [['a.pdf', 'f2'], ['b.pdf', 'f2']]);
    assert.equal(page.byId('tc-drop-dialog').open, false);
});

test('a failed file keeps the dialog open and names the file', async () => {
    const page = await openWorkspace(server('big.zip'));
    page.byId('tc-conversation').dispatch('drop', dropEvent([{ name: 'ok.pdf', size: 10 }, { name: 'big.zip', size: 10 }]));
    await settle(); await settle();
    await page.byId('tc-drop-form').dispatchAsync('submit', { preventDefault() {} });
    assert.equal(page.uploads.length, 2);
    assert.equal(page.byId('tc-drop-dialog').open, true);
    assert.match(page.byId('tc-drop-status').textContent, /big\.zip：容量が大きすぎます。/);
});

test('parent folders can be collapsed in the save dialog', async () => {
    const page = await openWorkspace(server());
    page.byId('tc-conversation').dispatch('drop', dropEvent([{ name: 'a.pdf', size: 10 }]));
    await settle(); await settle();
    const toggle = treeItems(page.byId('tc-drop-tree')).find((i) => i.toggle && i.text === '▾');
    toggle.el.dispatch('click');
    const child = treeItems(page.byId('tc-drop-tree')).find((i) => i.text === '📁 写真');
    assert.equal(child.hiddenParent, true);
});

test('dragging text (not files) does nothing', async () => {
    const page = await openWorkspace(server());
    page.byId('tc-conversation').dispatch('drop', { preventDefault() { throw new Error('should not prevent'); }, dataTransfer: { types: ['text/plain'], items: [], files: [] } });
    assert.equal(page.byId('tc-drop-dialog').open, false);
});
