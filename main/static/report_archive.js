"use strict";
(() => {
    // アーカイブの削除：「delete」と入力するまで削除ボタンを押せない（サーバーでも確認する）
    const panel = document.getElementById('ra-delete');
    if (panel) {
        const input = document.getElementById('ra-delete-confirm');
        const button = document.getElementById('ra-delete-button');
        const message = document.getElementById('ra-delete-message');
        const word = panel.dataset.word;
        const matches = () => input.value.trim().toLowerCase() === word;
        input.addEventListener('input', () => { button.disabled = !matches(); });
        button.addEventListener('click', async () => {
            if (!matches()) return;
            button.disabled = true; message.textContent = '削除しています…';
            try {
                const response = await fetch(panel.dataset.url, {method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({csrf_token: panel.dataset.csrf, confirm: input.value})});
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '削除できませんでした。');
                window.location.assign(panel.dataset.after);
            } catch (error) { message.textContent = error.message; button.disabled = !matches(); }
        });
    }
    // 提出回の画面：各グループの一番新しい提出を、確認ダイアログのあとにまとめてアーカイブに保存する（super_teacherのみ）
    const saver = document.getElementById('ra-save');
    if (saver) {
        const message = document.getElementById('ra-save-message');
        saver.addEventListener('click', async () => {
            const waiting = Number(saver.dataset.waiting || 0);
            const note = waiting ? `\n\n再提出待ちのグループが${waiting}件あります。その分は、今ある最新の提出が保存されます。` : '';
            if (!window.confirm(`各グループの一番新しい提出${saver.dataset.pending}件を、アーカイブに保存します。保存したものは、先生と生徒の全員が見られます。よろしいですか？${note}`)) return;
            saver.disabled = true; message.textContent = '保存しています…';
            try {
                const response = await fetch('/teacher/api/report-archive', {method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({csrf_token: saver.dataset.csrf, frame_key: Number(saver.dataset.frame), day: saver.dataset.day})});
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '保存できませんでした。');
                message.textContent = `${data.saved}件をアーカイブに保存しました。`;
                window.setTimeout(() => window.location.reload(), 800);
            } catch (error) { message.textContent = error.message; saver.disabled = false; }
        });
    }
})();
