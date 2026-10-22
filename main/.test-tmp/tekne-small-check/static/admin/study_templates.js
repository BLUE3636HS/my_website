"use strict";
(() => {
    const root = document.querySelector('.template-manager');
    if (!root) return;
    const status = document.getElementById('template-status');
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
    const fields = [];
    let selected = null;
    let dragging = null;
    let saving = false;
    let dirty = false;
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
            dirty = true;
            preview();
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
            dirty = true;
            preview();
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
            dirty = true;
            preview();
        });
    });
    function updateFieldType() {
        const image = byId('field-type').value === 'image';
        byId('image-settings-group').hidden = !image;
        byId('body-settings-title').textContent = image ? 'キャプション設定' : '本文設定';
        byId('body-size-label').textContent = image ? 'キャプション文字サイズ' : '内容文字サイズ';
        byId('body-alignment-label').textContent = image ? 'キャプションの配置' : '本文の配置';
        byId('body-bold-label').textContent = image ? 'キャプションを太字にする' : '本文を太字にする';
        byId('max-length-setting').hidden = image;
        byId('max-length').value = image ? '50' : (byId('max-length').value === '50' ? '0' : byId('max-length').value);
    }
    function settings() {
        return {label: byId('field-label').value.trim(), heading_font_size: Number(byId('heading-size').value),
            body_font_size: Number(byId('body-size').value), hide_heading: byId('hide-heading').checked,
            max_length: Number(byId('max-length').value), required: byId('field-required').checked,
            heading_alignment: byId('heading-alignment').value, body_alignment: byId('body-alignment').value,
            heading_bold: byId('heading-bold').checked, body_bold: byId('body-bold').checked,
            field_type: byId('field-type').value, image_size: byId('image-size').value,
            image_alignment: byId('image-alignment').value};
    }
    function validate() {
        byId('field-label').setCustomValidity(byId('field-label').value.trim() ? '' : '見出しを入力してください。');
        return editor.reportValidity();
    }
    function resetEditor() {
        selected = null; dirty = false; editor.reset();
        setHeadingAlignment('left');
        setBodyAlignment('left');
        setImageAlignment('center');
        updateFieldType();
        byId('field-label').setCustomValidity('');
        byId('editor-title').textContent = '項目を追加';
        byId('field-apply').textContent = '項目を追加';
        byId('field-cancel').textContent = '入力をクリア';
    }
    function edit(field) {
        if (dirty) { status.textContent = '現在の項目を反映するか、入力をクリア／編集を終了してから別の項目を選択してください。'; return; }
        selected = field; dirty = false;
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
        byId('field-required').checked = field.required;
        byId('image-size').value = field.image_size;
        setImageAlignment(field.image_alignment);
        updateFieldType();
        byId('field-label').setCustomValidity('');
        byId('editor-title').textContent = '項目を編集';
        byId('field-apply').textContent = '項目を更新';
        byId('field-cancel').textContent = '編集を終了';
        render(); byId('field-label').focus();
    }
    function preview() {
        const paper = byId('preview-paper'); paper.replaceChildren();
        const id = document.createElement('p'); id.textContent = 'ユーザーID：sample-student'; id.style.fontSize = '11pt'; paper.append(id);
        const items = fields.map(field => field === selected ? settings() : field);
        if (!selected && byId('field-label').value.trim()) items.push(settings());
        items.forEach(field => {
            if (!field.hide_heading) {
                const title = document.createElement('h3'); title.textContent = field.label;
                title.style.fontSize = `${Math.min(36, Math.max(6, field.heading_font_size || 11))}pt`;
                title.style.textAlign = field.heading_alignment;
                title.style.fontWeight = field.heading_bold ? '700' : '400'; paper.append(title);
            }
            if (field.field_type === 'image') {
                const gallery = document.createElement('div');
                gallery.className = `preview-images preview-images-${field.image_size}`;
                gallery.dataset.alignment = field.image_alignment;
                for (let index = 0; index < 3; index++) {
                    const item = document.createElement('div'); item.className = 'preview-image-item';
                    const placeholder = document.createElement('div'); placeholder.className = 'preview-image-placeholder';
                    placeholder.textContent = '画像';
                    const caption = document.createElement('p'); caption.textContent = `画像${index + 1}のキャプション`;
                    caption.style.fontSize = `${Math.min(36, Math.max(6, field.body_font_size || 10))}pt`;
                    caption.style.textAlign = field.body_alignment;
                    caption.style.fontWeight = field.body_bold ? '700' : '400';
                    item.append(placeholder, caption); gallery.append(item);
                }
                paper.append(gallery);
            } else {
                const text = document.createElement('p');
                text.textContent = field.max_length > 0 ? Array.from(sample).slice(0, field.max_length).join('') : sample;
                text.style.fontSize = `${Math.min(36, Math.max(6, field.body_font_size || 10))}pt`;
                text.style.textAlign = field.body_alignment;
                text.style.fontWeight = field.body_bold ? '700' : '400'; paper.append(text);
            }
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
            const handle = action('⋮⋮', `${field.label}の並び替えハンドル。上下ボタンでも移動できます。`, () => {});
            handle.className = 'field-handle'; handle.draggable = true;
            handle.addEventListener('dragstart', event => { dragging = field; event.dataTransfer.setData('text/plain', String(index)); event.dataTransfer.effectAllowed = 'move'; });
            handle.addEventListener('dragend', () => { dragging = null; root.querySelectorAll('.drop-target').forEach(item => item.classList.remove('drop-target')); });
            row.addEventListener('dragover', event => { if (dragging && !saving) { event.preventDefault(); row.classList.add('drop-target'); } });
            row.addEventListener('dragleave', () => row.classList.remove('drop-target'));
            row.addEventListener('drop', event => {
                event.preventDefault(); if (!dragging || saving) return;
                move(dragging, fields.indexOf(field) - fields.indexOf(dragging)); dragging = null;
            });
            const label = document.createElement('strong'); label.textContent = ` ${index + 1}. ${field.label}`;
            const details = document.createElement('p');
            details.textContent = field.field_type === 'image'
                ? `画像 / ${field.required ? '必須' : '任意'} / ${field.image_size === 'small' ? '小（2列）' : '大（1列）'}`
                : `文章 / ${field.required ? '必須' : '任意'} / ${field.max_length ? field.max_length + '文字以内' : '文字数無制限'}`;
            const actions = document.createElement('div'); actions.className = 'field-actions';
            const up = action('↑', `${field.label}を上へ`, () => move(field, -1)); up.disabled = index === 0;
            const down = action('↓', `${field.label}を下へ`, () => move(field, 1)); down.disabled = index === fields.length - 1;
            actions.append(action('編集', `${field.label}を編集`, () => edit(field)), up, down,
                action('削除', `${field.label}を削除`, () => { fields.splice(fields.indexOf(field), 1); if (selected === field) resetEditor(); render(); }));
            row.append(handle, label, details, actions); list.append(row);
        });
        preview();
    }
    editor.addEventListener('input', () => { dirty = true; byId('field-label').setCustomValidity(''); preview(); });
    editor.addEventListener('submit', event => event.preventDefault());
    editor.addEventListener('keydown', event => {
        if (event.key === 'Enter' && event.target.matches('input')) event.preventDefault();
    });
    byId('field-apply').addEventListener('click', () => {
        if (saving || !validate()) return;
        if (selected) Object.assign(selected, settings()); else fields.push(settings());
        resetEditor(); render(); status.textContent = '項目を反映しました。';
    });
    byId('field-cancel').addEventListener('click', () => { resetEditor(); render(); });
    byId('field-type').addEventListener('change', () => { updateFieldType(); preview(); });
    byId('save-template').addEventListener('click', async () => {
        if (saving) return;
        const name = byId('template-name');
        name.setCustomValidity(name.value.trim() ? '' : 'テンプレート名を入力してください。');
        if (!name.reportValidity()) return;
        if (dirty) { status.textContent = '左側の設定を「項目を追加」または「項目を更新」で反映してから保存してください。'; return; }
        if (!fields.length) { status.textContent = '項目を1つ以上追加してください。'; return; }
        saving = true; status.textContent = '保存しています…';
        const controls = [...root.querySelectorAll('input, select, button')].map(control => [control, control.disabled]);
        controls.forEach(([control]) => { control.disabled = true; });
        try {
            await post('/admin/study-templates', {name: name.value.trim(), fields});
            window.location.assign('/admin/study-templates');
        } catch (error) {
            status.textContent = error.message; saving = false;
            controls.forEach(([control, disabled]) => { control.disabled = disabled; });
        }
    });
    byId('template-name').addEventListener('input', () => byId('template-name').setCustomValidity(''));
    render();
})();
