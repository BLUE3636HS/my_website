"use strict";
(() => {
    const root = document.querySelector('.template-manager');
    if (!root) return;
    const status = document.getElementById('template-status');
    const createEndpoint = root.dataset.createEndpoint || '/admin/study-templates';
    const returnUrl = root.dataset.returnUrl || '/admin/study-templates';
    const createSuccessMessage = root.dataset.createSuccessMessage || '';
    const applyMode = root.dataset.applyMode === 'true';
    const sourceTemplateId = root.dataset.sourceTemplateId ? Number(root.dataset.sourceTemplateId) : null;
    const initialFields = JSON.parse(document.getElementById('template-initial-fields')?.textContent || '[]');
    function createStableKey() {
        if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID().replaceAll('-', '');
        return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 18)}`;
    }
    async function post(url, payload) {
        const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({...payload, csrf_token: root.dataset.csrf})});
        const result = await response.json().catch(() => null);
        if (response.redirected || !response.ok || result?.ok !== true) {
            throw new Error(typeof result?.detail === 'string' ? result.detail : '保存できませんでした。入力内容とログイン状態を確認してください。');
        }
        return result;
    }
    root.querySelectorAll('[data-visibility]').forEach(button => {
        button.addEventListener('click', async () => {
            button.disabled = true;
            try {
                const result = await post(`/admin/study-templates/${button.dataset.visibility}/visibility`,
                    {active: button.dataset.active !== 'true'});
                button.dataset.active = String(result.active);
                button.textContent = result.active ? '非公開にする' : '公開する';
                root.querySelector(`[data-state="${button.dataset.visibility}"]`).textContent = result.active ? '公開' : '非公開';
                status.textContent = '公開状態を変更しました。';
            } catch (error) { status.textContent = error.message; }
            finally { button.disabled = false; }
        });
    });
    const editor = document.getElementById('field-editor');
    if (!editor) return;
    const byId = id => document.getElementById(id);
    const fields = Array.isArray(initialFields) ? initialFields : [];
    let selected = null;
    let dragging = null;
    let saving = false;
    let newFieldStableKey = createStableKey();
    const sample = 'ここに生徒が入力した文章が表示されます。';
    function setHeadingAlignment(value) {
        byId('heading-alignment').value = value;
        root.querySelectorAll('[data-heading-alignment]').forEach(button => {
            button.setAttribute('aria-pressed', String(button.dataset.headingAlignment === value));
        });
    }
    root.querySelectorAll('[data-heading-alignment]').forEach(button => {
        button.addEventListener('click', () => {
            if (saving) return;
            setHeadingAlignment(button.dataset.headingAlignment);
            applyEditor();
        });
    });
    function setBodyAlignment(value) {
        byId('body-alignment').value = value;
        root.querySelectorAll('[data-body-alignment]').forEach(button => {
            button.setAttribute('aria-pressed', String(button.dataset.bodyAlignment === value));
        });
    }
    root.querySelectorAll('[data-body-alignment]').forEach(button => {
        button.addEventListener('click', () => {
            if (saving) return;
            setBodyAlignment(button.dataset.bodyAlignment);
            applyEditor();
        });
    });
    function setImageAlignment(value) {
        byId('image-alignment').value = value;
        root.querySelectorAll('[data-image-alignment]').forEach(button => {
            button.setAttribute('aria-pressed', String(button.dataset.imageAlignment === value));
        });
    }
    root.querySelectorAll('[data-image-alignment]').forEach(button => {
        button.addEventListener('click', () => {
            if (saving) return;
            setImageAlignment(button.dataset.imageAlignment);
            applyEditor();
        });
    });
    // 「文字」「画像」の2つのボタン（チェックボックス）と、内部の項目の内容（text／both／image）を連動させる
    function contentTypeFromChecks() {
        const text = byId('content-text').checked, image = byId('content-image').checked;
        return text && image ? 'both' : image ? 'image' : 'text';
    }
    ['content-text', 'content-image'].forEach(id => byId(id).addEventListener('change', event => {
        if (!byId('content-text').checked && !byId('content-image').checked) {
            event.target.checked = true;  // どちらか一方は必ず選ぶ
            status.textContent = '「文字」か「画像」の少なくとも一方を選んでください。';
            return;
        }
        byId('field-type').value = contentTypeFromChecks();
        byId('field-type').dispatchEvent(new Event('change', {bubbles: true}));
    }));
    function updateFieldType() {
        const type = byId('field-type').value;
        byId('content-text').checked = type !== 'image';
        byId('content-image').checked = type !== 'text';
        const image = type === 'image';
        // 文字と画像：本文の設定は画像のキャプションにも使う
        const word = image ? 'キャプション' : type === 'both' ? '本文・キャプション' : '本文';
        byId('image-settings-group').hidden = type === 'text';
        byId('body-settings-title').textContent = `${word}設定`;
        byId('body-size-label').textContent = `${image ? 'キャプション' : word}文字サイズ`;
        byId('body-alignment-label').textContent = `${word}の配置`;
        byId('body-bold-label').textContent = `${word}を太字にする`;
        byId('max-length-setting').hidden = image;
        byId('max-length').value = image ? '50' : (byId('max-length').value === '50' ? '0' : byId('max-length').value);
    }
    function settings() {
        return {stable_key: selected?.stable_key || newFieldStableKey,
            label: byId('field-label').value.trim(), heading_font_size: Number(byId('heading-size').value),
            body_font_size: Number(byId('body-size').value), hide_heading: byId('hide-heading').checked,
            max_length: Number(byId('max-length').value),
            heading_alignment: byId('heading-alignment').value, body_alignment: byId('body-alignment').value,
            heading_bold: byId('heading-bold').checked, body_bold: byId('body-bold').checked,
            field_type: byId('field-type').value, image_size: byId('image-size').value,
            image_alignment: byId('image-alignment').value};
    }
    // 編集欄の内容をすぐに選んでいる項目へ反映する（入力が不正な間は反映しない）
    function applyEditor() {
        if (!selected) return;
        byId('field-label').setCustomValidity(byId('field-label').value.trim() ? '' : '見出しを入力してください。');
        if (editor.checkValidity()) { Object.assign(selected, settings()); render(); } else preview();
    }
    function showEditor(open) {
        byId('editor-body').hidden = !open;
        byId('editor-empty').hidden = open;
    }
    function resetEditor() {
        selected = null; activePart = null; editor.reset();
        newFieldStableKey = createStableKey();
        setHeadingAlignment('left');
        setBodyAlignment('left');
        setImageAlignment('center');
        updateFieldType();
        byId('field-label').setCustomValidity('');
        byId('editor-title').textContent = '項目を編集';
        showEditor(false);
    }
    function edit(field) {
        selected = field; activePart = null;
        byId('field-type').value = field.field_type;
        byId('field-label').value = field.label;
        byId('heading-size').value = field.heading_font_size;
        byId('body-size').value = field.body_font_size;
        byId('hide-heading').checked = field.hide_heading;
        setHeadingAlignment(field.heading_alignment);
        setBodyAlignment(field.body_alignment);
        byId('heading-bold').checked = field.heading_bold;
        byId('body-bold').checked = field.body_bold;
        byId('max-length').value = field.max_length;
        byId('image-size').value = field.image_size;
        setImageAlignment(field.image_alignment);
        updateFieldType();
        byId('field-label').setCustomValidity('');
        byId('editor-title').textContent = `${fields.indexOf(field) + 1}番目の項目を編集`;
        showEditor(true);
        render(); byId('field-label').focus();
    }
    // PDFの先頭に印字する情報（成果レポート設定のみ）。選択ボックスの行を追加・並び替え・削除する
    const headerList = byId('header-item-list');
    let headerOptions = [], headerItems = [];
    try {
        headerOptions = JSON.parse(byId('header-options-data')?.textContent || '[]');
        headerItems = JSON.parse(byId('header-items-data')?.textContent || '[]');
    } catch (error) { headerOptions = []; headerItems = []; }
    const headerLabels = new Map(headerOptions.map(option => [option.key, option.label]));
    headerItems = headerItems.filter(key => headerLabels.has(key));
    const isMemberKey = key => key.startsWith('member_') || key.startsWith('custom:');
    let headerDragging = null;
    // 先頭の情報の文字サイズ・配置・太字（全行共通）
    let headerAlignment = root.querySelector('[data-header-alignment][aria-pressed="true"]')?.dataset.headerAlignment || 'left';
    function headerStyle() {
        const size = Number(byId('header-font-size')?.value);
        return {font_size: Number.isInteger(size) ? Math.min(36, Math.max(6, size)) : 11,
            alignment: headerAlignment, bold: Boolean(byId('header-bold')?.checked)};
    }
    root.querySelectorAll('[data-header-alignment]').forEach(button => {
        button.addEventListener('click', () => {
            headerAlignment = button.dataset.headerAlignment;
            root.querySelectorAll('[data-header-alignment]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
            preview();
        });
    });
    byId('header-font-size')?.addEventListener('input', () => { byId('header-font-size').setCustomValidity(''); preview(); });
    byId('header-bold')?.addEventListener('change', () => preview());
    function renderHeader() {
        if (!headerList) return;
        headerList.replaceChildren();
        headerItems.forEach((key, index) => {
            const row = document.createElement('li'); row.className = 'header-item-row';
            // 項目一覧と同じく、左の⋮⋮をドラッグして並び替え（キーボードでは⋮⋮にフォーカスして↑↓キー）
            const handle = action('⋮⋮', `${headerLabels.get(key)}の並び替えハンドル。ドラッグ、または↑↓キーで移動できます。`, () => {});
            handle.className = 'field-handle'; handle.draggable = true;
            handle.addEventListener('dragstart', event => { headerDragging = index; event.dataTransfer.setData('text/plain', String(index)); event.dataTransfer.effectAllowed = 'move'; });
            handle.addEventListener('dragend', () => { headerDragging = null; headerList.querySelectorAll('.drop-target').forEach(item => item.classList.remove('drop-target')); });
            handle.addEventListener('keydown', event => {
                if (event.key === 'ArrowUp' || event.key === 'ArrowDown') { event.preventDefault(); moveHeader(index, event.key === 'ArrowUp' ? -1 : 1); }
            });
            row.addEventListener('dragover', event => { if (headerDragging !== null && !saving) { event.preventDefault(); row.classList.add('drop-target'); } });
            row.addEventListener('dragleave', () => row.classList.remove('drop-target'));
            row.addEventListener('drop', event => {
                event.preventDefault(); if (headerDragging === null || saving) return;
                const from = headerDragging; headerDragging = null; moveHeader(from, index - from);
            });
            const select = document.createElement('select');
            select.setAttribute('aria-label', `${index + 1}番目に印字する情報`);
            headerOptions.forEach(option => {
                const item = document.createElement('option'); item.value = option.key; item.textContent = option.label;
                item.disabled = option.key !== key && headerItems.includes(option.key);
                select.append(item);
            });
            select.value = key;
            select.addEventListener('change', () => { headerItems[index] = select.value; renderHeader(); preview(); });
            const label = headerLabels.get(key);
            const actions = document.createElement('div'); actions.className = 'field-actions';
            actions.append(action('削除', `${label}を削除`, () => { headerItems.splice(index, 1); renderHeader(); preview(); }));
            row.append(handle, select, actions); headerList.append(row);
        });
        byId('header-item-empty').hidden = headerItems.length > 0;
        byId('header-item-count').textContent = headerItems.length ? `（${headerItems.length}件）` : '（なし）';
        byId('header-item-add').disabled = headerItems.length >= Math.min(20, headerOptions.length);
    }
    function moveHeader(index, offset) {
        const to = index + offset;
        if (to < 0 || to >= headerItems.length) return;
        const [key] = headerItems.splice(index, 1); headerItems.splice(to, 0, key);
        renderHeader(); preview();
        status.textContent = `「${headerLabels.get(key)}」を${to + 1}番目に移動しました。`;
        headerList.children[to]?.querySelector('.field-handle')?.focus();
    }
    byId('header-item-add')?.addEventListener('click', () => {
        const next = headerOptions.find(option => !headerItems.includes(option.key));
        if (!next || headerItems.length >= 20) return;
        headerItems.push(next.key); renderHeader(); preview();
        headerList.lastElementChild?.querySelector('select')?.focus();
    });
    // プレビュー用の仮の値
    const sampleMembers = [
        {member_id: 'sample-student', member_name: '山田 花子', member_furigana: 'やまだ はなこ'},
        {member_id: 'sample-student2', member_name: '佐藤 太郎', member_furigana: 'さとう たろう'},
    ];
    function headerLines() {
        if (!headerList) return ['ユーザーID：sample-student'];
        const values = {saved_at: '2026/11/18 23:59', school_name: root.dataset.sampleSchool || 'サンプル学校',
            group_name: 'サンプルグループ', version: '3', save_kind: '定期保存（2026/11/18）', saved_by: '自動保存'};
        const lines = []; const memberKeys = []; let memberAt = -1;
        headerItems.forEach(key => {
            if (isMemberKey(key)) { if (memberAt < 0) { memberAt = lines.length; lines.push(''); } memberKeys.push(key); }
            else lines.push(`${headerLabels.get(key)}：${values[key] ?? ''}`);
        });
        if (memberAt >= 0) {
            const members = sampleMembers.map(member => '・' + memberKeys.map(key => key.startsWith('custom:')
                ? `${headerLabels.get(key).replace(/^メンバーの/, '')} ○` : member[key]).join('／'));
            lines.splice(memberAt, 1, 'メンバー：', ...members);
        }
        return lines;
    }
    // 編集中の項目のうち、いま触っている設定（heading／body／image）に対応するプレビューの部分を赤く示す
    let activePart = null;
    function preview() {
        const items = fields.map(field => field === selected ? settings() : field);
        const editIndex = selected ? fields.indexOf(selected) : -1;
        drawPreview(byId('preview-paper'), items, {editIndex, part: activePart});
    }
    // PDF簡易プレビューの描画（編集画面とインポートのダイアログで共用）
    function drawPreview(paper, items, highlight = {editIndex: -1, part: null}) {
        paper.replaceChildren();
        const lines = headerLines();
        if (lines.length) {
            const head = document.createElement('p'); head.className = 'preview-header';
            head.textContent = lines.join('\n');
            const style = headerList ? headerStyle() : {font_size: 11, alignment: 'left', bold: false};
            head.style.fontSize = `${style.font_size}pt`; head.style.textAlign = style.alignment;
            head.style.fontWeight = style.bold ? '700' : '400'; paper.append(head);
        }
        const size = (value, fallback) => `${Math.min(36, Math.max(6, value || fallback))}pt`;
        items.forEach((field, fieldIndex) => {
            const box = document.createElement('div'); box.className = 'preview-field';
            const editing = fieldIndex === highlight.editIndex;
            if (editing) box.classList.add('is-editing');
            const mark = (element, part) => { if (editing && highlight.part === part) element.classList.add('preview-highlight'); return element; };
            const hasText = field.field_type !== 'image';
            const hasImages = field.field_type === 'image' || field.field_type === 'both';
            if (!field.hide_heading) {
                const title = document.createElement('h3'); title.textContent = field.label;
                title.style.fontSize = size(field.heading_font_size, 11);
                title.style.textAlign = field.heading_alignment;
                title.style.fontWeight = field.heading_bold ? '700' : '400'; box.append(mark(title, 'heading'));
            }
            if (hasText) {
                const text = document.createElement('p');
                text.textContent = field.max_length > 0 ? Array.from(sample).slice(0, field.max_length).join('') : sample;
                text.style.fontSize = size(field.body_font_size, 10);
                text.style.textAlign = field.body_alignment;
                text.style.fontWeight = field.body_bold ? '700' : '400'; box.append(mark(text, 'body'));
            }
            if (hasImages) {
                const gallery = document.createElement('div');
                gallery.className = `preview-images preview-images-${field.image_size}`;
                gallery.dataset.alignment = field.image_alignment;
                for (let index = 0; index < 3; index++) {
                    const item = document.createElement('div'); item.className = 'preview-image-item';
                    const placeholder = document.createElement('div'); placeholder.className = 'preview-image-placeholder';
                    placeholder.textContent = '画像';
                    const caption = document.createElement('p'); caption.textContent = `画像${index + 1}のキャプション`;
                    caption.style.fontSize = size(field.body_font_size, 10);
                    caption.style.textAlign = field.body_alignment;
                    caption.style.fontWeight = field.body_bold ? '700' : '400';
                    item.append(mark(placeholder, 'image'), mark(caption, 'body')); gallery.append(item);
                }
                box.append(gallery);
            }
            paper.append(box);
        });
    }
    function move(field, offset) {
        const index = fields.indexOf(field); const to = index + offset;
        if (to < 0 || to >= fields.length) return;
        fields.splice(index, 1); fields.splice(to, 0, field); render();
        status.textContent = `「${field.label}」を${to + 1}番目に移動しました。`;
        byId('field-list').children[to].querySelector('.field-handle').focus();
    }
    function action(text, label, callback) {
        const button = document.createElement('button'); button.type = 'button'; button.textContent = text;
        button.setAttribute('aria-label', label); button.addEventListener('click', callback); return button;
    }
    function render() {
        const list = byId('field-list'); list.replaceChildren();
        fields.forEach((field, index) => {
            const row = document.createElement('li'); row.className = `field-row${selected === field ? ' selected' : ''}`;
            const handle = action('⋮⋮', `${field.label}の並び替えハンドル。ドラッグ、または↑↓キーで移動できます。`, () => {});
            handle.className = 'field-handle'; handle.draggable = true;
            handle.addEventListener('keydown', event => {
                if (event.key === 'ArrowUp' || event.key === 'ArrowDown') { event.preventDefault(); move(field, event.key === 'ArrowUp' ? -1 : 1); }
            });
            handle.addEventListener('dragstart', event => { dragging = field; event.dataTransfer.setData('text/plain', String(index)); event.dataTransfer.effectAllowed = 'move'; });
            handle.addEventListener('dragend', () => { dragging = null; root.querySelectorAll('.drop-target').forEach(item => item.classList.remove('drop-target')); });
            row.addEventListener('dragover', event => { if (dragging && !saving) { event.preventDefault(); row.classList.add('drop-target'); } });
            row.addEventListener('dragleave', () => row.classList.remove('drop-target'));
            row.addEventListener('drop', event => {
                event.preventDefault(); if (!dragging || saving) return;
                move(dragging, fields.indexOf(field) - fields.indexOf(dragging)); dragging = null;
            });
            // 1行表示：⋮⋮　種類（T＝文章／P＝画像）　番号と見出し　制限（∞・文字数／画像の大きさ）　✎　×
            const image = field.field_type === 'image';
            const badge = (text, title, className) => {
                const item = document.createElement('span'); item.className = className; item.textContent = text;
                item.title = title; item.setAttribute('aria-label', title); item.setAttribute('role', 'img'); return item;
            };
            const both = field.field_type === 'both';
            const kind = both ? badge('TP', '文字と画像', 'field-kind field-kind-both')
                : badge(image ? 'P' : 'T', image ? '画像だけ' : '文字だけ', `field-kind field-kind-${image ? 'image' : 'text'}`);
            const label = document.createElement('strong'); label.className = 'field-row-label';
            label.textContent = `${index + 1}. ${field.label}`; label.title = field.label;
            const sizeBadge = () => badge(field.image_size === 'small' ? '▢▢' : '▢', field.image_size === 'small' ? '画像：小（2列）' : '画像：大（1列）', 'field-limit');
            const lengthBadge = () => badge(field.max_length ? String(field.max_length) : '∞', field.max_length ? `${field.max_length}文字以内` : '文字数無制限', 'field-limit');
            const limit = document.createElement('span'); limit.className = 'field-limits';
            if (image) limit.append(sizeBadge()); else limit.append(lengthBadge());
            if (both) limit.append(sizeBadge());
            const editButton = action('✎', `${field.label}を編集`, () => edit(field)); editButton.title = '編集';
            const removeButton = action('×', `${field.label}を削除`, () => { fields.splice(fields.indexOf(field), 1); if (selected === field) resetEditor(); render(); });
            removeButton.title = '削除'; removeButton.className = 'field-remove';
            const actions = document.createElement('div'); actions.className = 'field-row-actions';
            actions.append(editButton, removeButton);
            row.append(handle, kind, label, limit, actions); list.append(row);
        });
        if (selected) byId('editor-title').textContent = `${fields.indexOf(selected) + 1}番目の項目を編集`;
        preview();
    }
    editor.addEventListener('input', applyEditor);
    const settingParts = {'heading-settings-group': 'heading', 'body-settings-group': 'body', 'image-settings-group': 'image'};
    const touchPart = event => {
        const group = event.target.closest?.('.field-settings-group');
        const part = group ? settingParts[group.id] || null : null;
        if (part !== activePart) { activePart = part; preview(); }
    };
    editor.addEventListener('focusin', touchPart);
    editor.addEventListener('pointerdown', touchPart);
    editor.addEventListener('submit', event => event.preventDefault());
    editor.addEventListener('keydown', event => {
        if (event.key === 'Enter' && event.target.matches('input')) event.preventDefault();
    });
    byId('field-cancel').addEventListener('click', () => { resetEditor(); render(); });
    byId('field-new')?.addEventListener('click', () => {
        if (saving) return;
        // 仮の見出しの項目を一覧の最後に足し、そのまま編集欄で開く
        const field = {stable_key: createStableKey(), label: `新しい項目${fields.length + 1}`, heading_font_size: 11,
            body_font_size: 10, hide_heading: false, max_length: 0, heading_alignment: 'left', body_alignment: 'left',
            heading_bold: false, body_bold: false, field_type: 'text', image_size: 'small', image_alignment: 'center'};
        fields.push(field); edit(field); byId('field-label').select();
        status.textContent = `「${field.label}」を追加しました。見出しや設定を変更してください。`;
    });
    byId('field-type').addEventListener('change', () => { updateFieldType(); applyEditor(); });
    byId('save-template').addEventListener('click', async () => {
        if (saving) return;
        const name = byId('template-name');
        if (!applyMode) {
            name.setCustomValidity(name.value.trim() ? '' : 'テンプレート名を入力してください。');
            if (!name.reportValidity()) return;
        }
        const headerSize = byId('header-font-size');
        if (headerSize && !headerSize.checkValidity()) {
            headerSize.closest('details')?.setAttribute('open', '');
            headerSize.reportValidity(); return;
        }
        if (selected && !editor.checkValidity()) { editor.reportValidity(); status.textContent = '編集中の項目の入力内容を確認してください。'; return; }
        if (!fields.length) { status.textContent = '項目を1つ以上追加してください。'; return; }
        saving = true; status.textContent = '保存しています…';
        const controls = [...root.querySelectorAll('input, select, button')].map(control => [control, control.disabled]);
        controls.forEach(([control]) => { control.disabled = true; });
        try {
            const payload = {fields};
            if (name) payload.name = name.value.trim();
            if (sourceTemplateId) payload.source_template_id = sourceTemplateId;
            if (headerList) { payload.header_items = headerItems; payload.header_style = headerStyle(); }
            await post(createEndpoint, payload);
            if (applyMode) {
                status.textContent = '成果レポート設定を適用しました。';
                saving = false;
                controls.forEach(([control, disabled]) => { control.disabled = disabled; });
            } else {
                if (createSuccessMessage) window.alert(createSuccessMessage);
                window.location.assign(returnUrl);
            }
        } catch (error) {
            status.textContent = error.message; saving = false;
            controls.forEach(([control, disabled]) => { control.disabled = disabled; });
        }
    });
    byId('template-name')?.addEventListener('input', () => byId('template-name').setCustomValidity(''));
    // 既存テンプレートのインポート：ダイアログで選んで読み込む（置き換えの注意はダイアログ内に表示）
    const importDialog = byId('template-import-dialog');
    let importTemplates = [];
    try { importTemplates = JSON.parse(byId('template-import-data')?.textContent || '[]'); } catch (error) { importTemplates = []; }
    const importRadios = importDialog ? [...importDialog.querySelectorAll('input[name="source_template_id"]')] : [];
    function previewImport() {
        const paper = byId('template-import-preview-paper'); if (!paper) return;
        const index = importRadios.findIndex(radio => radio.checked);
        if (index < 0 || !Array.isArray(importTemplates[index])) { paper.replaceChildren(); return; }
        drawPreview(paper, importTemplates[index]);
    }
    importRadios.forEach(radio => radio.addEventListener('change', previewImport));
    root.querySelector('[data-import-open]')?.addEventListener('click', () => {
        if (saving || !importDialog) return;
        previewImport();
        if (typeof importDialog.showModal === 'function') importDialog.showModal(); else importDialog.setAttribute('open', '');
    });
    importDialog?.querySelector('[data-import-close]')?.addEventListener('click', () => {
        if (typeof importDialog.close === 'function') importDialog.close(); else importDialog.removeAttribute('open');
    });
    renderHeader();
    render();
})();
