// ワークスペースのフォルダ画面（右側）：2段の一覧・「⋯」メニュー・現在地の表示
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { setup, findAll, ok, settle } = require('./chat_harness.cjs');

const tree = { folders: [
    { id: 'f1', parent_id: null, name: 'データ', path: 'データ' },
    { id: 'f2', parent_id: 'f1', name: '写真', path: 'データ/写真' },
] };
const file = { id: 'file1', folder_id: 'f2', display_name: '九州探究フェス2026_運営マニュアル_最終版.pdf', size_bytes: 5 * 1048576,
    created_at: '2026-11-14T08:40:07+00:00', uploaded_by_login_id: '11111111', uploaded_by_name: '百武蒼太' };
const child = { id: 'f9', parent_id: 'f2', name: '下書き', created_at: '2026-11-13T00:00:00+00:00' };

function server(kind) {
    return async (path, options = {}) => {
        if (path === '/tekne-chat/api/groups') return ok({ groups: [{ id: 'g1', name: '研究', kind, member_count: 2 }] });
        if (path.includes('/messages')) return ok({ messages: [], next_before_id: null });
        if (path.endsWith('/members')) return ok({ members: [] });
        if (path.endsWith('/folder-tree')) return ok(tree);
        if (path.includes('/folders?')) {
            const query = new URLSearchParams(path.split('?')[1]).get('q');
            return ok({ files: [file], folders: query ? [] : [child], current_parent_id: 'f1', folder_path: 'データ/写真' });
        }
        return ok({});
    };
}

async function openFolder(kind, extra) {
    const page = setup(server(kind), extra);
    await settle();
    await page.byId('tc-toggle-files').dispatchAsync('click');
    await settle();
    // 左の木で「写真」を開いた状態にする
    const photo = findAll(page.byId('tc-folder-tree'), (el) => el.textContent === '📁 写真')[0];
    photo.dispatch('click');
    await settle();
    return page;
}
const rows = (page) => findAll(page.byId('tc-file-list'), (el) => el.className?.startsWith('tc-file-row'));
const textOf = (row, cls) => findAll(row, (el) => el.className === cls)[0]?.textContent;
const menuLabels = (page) => page.byId('tc-entry-menu').children.map((item) => item.textContent);

test('rows show the full name on top and size / date / uploader name below', async () => {
    const page = await openFolder('group');
    const [folderRow, fileRow] = rows(page);
    assert.equal(textOf(folderRow, 'tc-file-name'), '下書き');
    assert.match(textOf(folderRow, 'tc-file-meta'), /^フォルダ・/);
    assert.equal(textOf(fileRow, 'tc-file-name'), '九州探究フェス2026_運営マニュアル_最終版.pdf');
    assert.match(textOf(fileRow, 'tc-file-meta'), /^5\.0 MB・.+・百武蒼太$/);
    assert.equal(textOf(fileRow, 'tc-file-location'), undefined, 'no location line while browsing a folder');
});

test('personal space hides the uploader', async () => {
    const page = await openFolder('personal');
    const fileRow = rows(page)[1];
    assert.doesNotMatch(textOf(fileRow, 'tc-file-meta'), /百武蒼太|11111111/);
});

test('breadcrumb shows each parent level and lets you jump back', async () => {
    const page = await openFolder('group');
    const nav = page.byId('tc-folder-path');
    const parts = findAll(nav, () => true).map((el) => el.textContent);
    assert.deepEqual(parts, ['ルート', '＞', 'データ', '＞', '写真']);
    const current = findAll(nav, (el) => el.className === 'tc-breadcrumb-current')[0];
    assert.equal(current.textContent, '写真');
    const link = findAll(nav, (el) => el.className === 'tc-breadcrumb-link' && el.textContent === 'データ')[0];
    link.dispatch('click');
    await settle();
    assert.deepEqual(findAll(page.byId('tc-folder-path'), () => true).map((el) => el.textContent), ['ルート', '＞', 'データ']);
});

test('the ⋯ menu offers the right actions for files and folders, and toggles closed', async () => {
    const page = await openFolder('group');
    const [folderRow, fileRow] = rows(page);
    const fileMore = findAll(fileRow, (el) => el.className === 'tc-file-more')[0];
    fileMore.dispatch('click');
    assert.equal(page.byId('tc-entry-menu').hidden, false);
    assert.deepEqual(menuLabels(page), ['ダウンロード', '名前変更', '移動', '削除']);
    fileMore.dispatch('click');
    assert.equal(page.byId('tc-entry-menu').hidden, true);
    findAll(folderRow, (el) => el.className === 'tc-file-more')[0].dispatch('click');
    assert.deepEqual(menuLabels(page), ['開く', '名前変更', '削除']);
    page.documentDispatch('keydown', { key: 'Escape' });
    assert.equal(page.byId('tc-entry-menu').hidden, true);
});

test('menu actions call the same APIs as before', async () => {
    const page = await openFolder('group', { prompt: () => '新しい名前.pdf', confirm: () => true });
    const fileRow = rows(page)[1];
    const open = () => findAll(fileRow, (el) => el.className === 'tc-file-more')[0].dispatch('click');
    const choose = async (label) => { open(); await page.byId('tc-entry-menu').children.find((i) => i.textContent === label).dispatchAsync('click'); await settle(); };

    await choose('ダウンロード');
    assert.equal(page.location.href, '/tekne-chat/api/files/file1/download');
    await choose('名前変更');
    const rename = page.requests.find((r) => r.method === 'PATCH');
    assert.equal(rename.path, '/tekne-chat/api/files/file1');
    assert.equal(rename.body.get('name'), '新しい名前.pdf');
    await choose('移動');
    assert.equal(page.byId('tc-move-dialog').open, true);
    await choose('削除');
    assert.ok(page.requests.some((r) => r.method === 'DELETE' && r.path === '/tekne-chat/api/files/file1'));
});

test('search results show where each file is and can be cleared', async () => {
    const page = await openFolder('group');
    page.byId('tc-file-search').value = 'マニュアル';
    page.byId('tc-file-search').dispatch('input');
    await new Promise((resolve) => setTimeout(resolve, 260));
    await settle();
    const fileRow = rows(page)[0];
    assert.equal(textOf(fileRow, 'tc-file-location'), '場所：ルート/データ/写真');
    const nav = page.byId('tc-folder-path');
    assert.equal(findAll(nav, (el) => el.className === 'tc-breadcrumb-current')[0].textContent, '「マニュアル」の検索結果');
    findAll(nav, (el) => el.className === 'tc-breadcrumb-clear')[0].dispatch('click');
    await settle();
    assert.equal(page.byId('tc-file-search').value, '');
});
