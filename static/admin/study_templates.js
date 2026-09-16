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
    const sample = 'ここに生徒が入力した文章が表示されます。\n研究の内容や結果を、文章でまとめます。';
    function settings() {
        return {label: byId('field-label').value.trim(), heading_font_size: Number(byId('heading-size').value),
            body_font_size: Number(byId('body-size').value), hide_heading: byId('hide-heading').checked,
            max_length: Number(byId('max-length').value), required: byId('field-required').checked};
    }
    function validate() {
        byId('field-label').setCustomValidity(byId('field-label').value.trim() ? '' : '見出しを入力してください。');
        return editor.reportValidity();
    }
    function resetEditor() {
        selected = null; dirty = false; editor.reset();
        byId('field-label').setCustomValidity('');
        byId('editor-title').textContent = '項目を追加';
        byId('field-apply').textContent = '項目を追加';
        byId('field-cancel').hidden = true;
    }
    function edit(field) {
        if (dirty) { status.textContent = '現在の項目を反映するか、「編集を終了」してから別の項目を選択してください。'; return; }
        selected = field; dirty = false;
        byId('field-label').value = field.label;
        byId('heading-size').value = field.heading_font_size;
        byId('body-size').value = field.body_font_size;
        byId('hide-heading').checked = field.hide_heading;
        byId('max-length').value = field.max_length;
        byId('field-required').checked = field.required;
        byId('field-label').setCustomValidity('');
        byId('editor-title').textContent = '項目を編集';
        byId('field-apply').textContent = '項目を更新';
        byId('field-cancel').hidden = false;
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
                title.style.fontSize = `${Math.min(36, Math.max(6, field.heading_font_size || 13))}pt`; paper.append(title);
            }
            const text = document.createElement('p');
            text.textContent = field.max_length > 0 ? Array.from(sample).slice(0, field.max_length).join('') : sample;
            text.style.fontSize = `${Math.min(36, Math.max(6, field.body_font_size || 11))}pt`; paper.append(text);
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
            const details = document.createElement('p'); details.textContent = `${field.required ? '必須' : '任意'} / ${field.max_length ? field.max_length + '文字以内' : '文字数無制限'}`;
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
    editor.addEventListener('submit', event => {
        event.preventDefault(); if (saving || !validate()) return;
        if (selected) Object.assign(selected, settings()); else fields.push(settings());
        resetEditor(); render(); status.textContent = '項目を反映しました。';
    });
    byId('field-cancel').addEventListener('click', () => { resetEditor(); render(); });
    byId('save-template').addEventListener('click', async () => {
        if (saving) return;
        const name = byId('template-name');
        name.setCustomValidity(name.value.trim() ? '' : 'テンプレート名を入力してください。');
        if (!name.reportValidity()) return;
        if (dirty) { status.textContent = '左側の設定を「項目を追加」または「項目を更新」で反映してから保存してください。'; return; }
        if (!fields.length) { status.textContent = '項目を1つ以上追加してください。'; return; }
        saving = true; status.textContent = '保存しています…';
        const controls = [...root.querySelectorAll('input, button')].map(control => [control, control.disabled]);
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
