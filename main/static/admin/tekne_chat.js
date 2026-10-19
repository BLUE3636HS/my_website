(() => {
    const app = document.getElementById("tc-admin-app"); if (!app) return;
    const csrf = app.dataset.csrf; const detail = document.getElementById("tc-admin-detail");
    let currentGroup = null; let detailState = null;
    function form() { const data = new FormData(); data.set("csrf", csrf); return data; }
    async function api(path, options = {}) {
        const response = await fetch(path, { credentials: "same-origin", ...options });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.detail || "操作に失敗しました。"); return payload;
    }
    function node(tag, text, className) { const element = document.createElement(tag); if (text !== undefined) element.textContent = text; if (className) element.className = className; return element; }
    function action(label, run, danger = false) { const button = node("button", label); if (danger) button.className = "tc-admin-danger"; button.addEventListener("click", run); return button; }
    function row(primary, secondary, actions = []) {
        const element = node("div", undefined, "tc-admin-row"); const main = node("div", undefined, "tc-admin-row-main");
        main.append(node("strong", primary)); if (secondary) main.append(node("small", secondary));
        const controls = node("div", undefined, "tc-admin-row-actions"); actions.forEach((item) => controls.append(item)); element.append(main, controls); return element;
    }
    async function openGroup(groupId, options = {}) {
        currentGroup = groupId; detailState = { messages: [], files: [], nextMessage: null, nextFile: null };
        detail.replaceChildren(node("p", "読み込み中…", "tc-admin-empty"));
        try { const data = await api(`/admin/tekne-chat/api/groups/${encodeURIComponent(groupId)}?limit=50`); render(data); }
        catch (error) { detail.replaceChildren(node("p", error.message)); }
    }
    function render(data, append = false, kind = "") {
        if (append && kind === "messages") {
            detailState.messages = [...detailState.messages, ...data.messages];
            detailState.nextMessage = data.next_message_before_id;
        } else if (append && kind === "files") {
            detailState.files = [...detailState.files, ...data.files];
            detailState.nextFile = data.next_file_before_id;
        } else {
            detailState.messages = data.messages; detailState.files = data.files;
            detailState.nextMessage = data.next_message_before_id; detailState.nextFile = data.next_file_before_id;
        }
        detail.replaceChildren();
        const group = data.group; const heading = node("h2", `${group.name} — ${group.school_name}`);
        const rename = action("グループ名変更", async () => {
            const name = prompt("新しいグループ名", group.name); if (!name) return;
            const body = form(); body.set("name", name);
            try { await api(`/admin/tekne-chat/api/groups/${group.id}`, { method: "PATCH", body }); await openGroup(group.id); location.reload(); }
            catch (error) { alert(error.message); }
        });
        detail.append(heading, row("グループ操作", "すべての変更は監査ログに記録されます。", [rename]));
        detail.append(node("h3", `メンバー（${data.members.length}）`));
        const members = node("div", undefined, "tc-admin-section");
        for (const member of data.members) {
            const controls = [];
            if (!member.left_at) controls.push(action("強制退出", async () => {
                if (!confirm("このメンバーをグループから退出させますか？")) return;
                try { await api(`/admin/tekne-chat/api/groups/${group.id}/members/${member.student_id}/remove`, { method: "POST", body: form() }); await openGroup(group.id); }
                catch (error) { alert(error.message); }
            }, true));
            members.append(row(member.login_id || "削除済みユーザー", member.left_at ? `退出: ${member.left_at}` : `参加: ${member.joined_at}`, controls));
        }
        detail.append(members);
        detail.append(node("h3", "メッセージ"));
        const messages = node("div", undefined, "tc-admin-section");
        for (const message of detailState.messages) {
            const text = message.deleted ? "メッセージが削除されました" : message.type === "file_upload" ? `${message.sender}がファイルをアップロードしました: ${message.file_name} (${message.folder_path})` : message.body;
            const controls = message.deleted ? [] : [action("削除", async () => {
                if (!confirm("このメッセージを削除しますか？")) return;
                try { await api(`/admin/tekne-chat/api/messages/${message.id}`, { method: "DELETE", body: form() }); await openGroup(group.id); }
                catch (error) { alert(error.message); }
            }, true)];
            messages.append(row(`${message.sender} · ${new Date(message.created_at).toLocaleString("ja-JP")}`, text, controls));
        }
        detail.append(messages);
        if (detailState.nextMessage) { const more = action("過去のメッセージを読み込む", async () => {
            try { const older = await api(`/admin/tekne-chat/api/groups/${group.id}?message_before_id=${detailState.nextMessage}&limit=50`); render(older, true, "messages"); }
            catch (error) { alert(error.message); }
        }); more.classList.add("tc-admin-more"); detail.append(more); }
        detail.append(node("h3", "共有ファイル"));
        const files = node("div", undefined, "tc-admin-section");
        for (const file of detailState.files) {
            const controls = [action("ダウンロード", () => { location.href = `/admin/tekne-chat/api/files/${file.id}/download`; }), action("削除", async () => {
                if (!confirm(`「${file.display_name}」を削除しますか？`)) return;
                try { await api(`/admin/tekne-chat/api/files/${file.id}`, { method: "DELETE", body: form() }); await openGroup(group.id); }
                catch (error) { alert(error.message); }
            }, true)];
            files.append(row(file.display_name, `${file.size_bytes} bytes · ${file.created_at}`, controls));
        }
        detail.append(files);
        if (detailState.nextFile) { const more = action("ファイルをさらに表示", async () => {
            try { const older = await api(`/admin/tekne-chat/api/groups/${group.id}?file_before_id=${detailState.nextFile}&limit=50`); render(older, true, "files"); }
            catch (error) { alert(error.message); }
        }); more.classList.add("tc-admin-more"); detail.append(more); }
        detail.append(node("h3", "フォルダ"));
        const folders = node("div", undefined, "tc-admin-section");
        for (const folder of data.folders) folders.append(row(folder.name, `作成: ${folder.created_at}`, [action("削除", async () => {
            if (!confirm(`「${folder.name}」と中のファイルを削除しますか？`)) return;
            try { await api(`/admin/tekne-chat/api/folders/${folder.id}`, { method: "DELETE", body: form() }); await openGroup(group.id); }
            catch (error) { alert(error.message); }
        }, true)]));
        detail.append(folders);
    }
    document.querySelectorAll(".tc-admin-group").forEach((button) => button.addEventListener("click", () => openGroup(button.dataset.groupId)));
})();
