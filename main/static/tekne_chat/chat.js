(() => {
    const app = document.getElementById("tekne-chat-app");
    if (!app) return;
    const csrf = app.dataset.csrf;
    const currentUser = app.dataset.currentUser;
    const $ = (id) => document.getElementById(id);
    const groupsEl = $("tc-group-list");
    const messagesEl = $("tc-messages");
    const sidePanel = $("tc-side-panel");
    const notice = $("tc-notice");
    const state = { groups: [], group: null, members: [], messages: new Map(), socket: null, retry: 0,
        reconnectTimer: null, folderId: null, searchTimer: null, lastId: 0, historyBeforeId: null, panel: "members", loading: false, movingFileId: null, sending: false };

    function api(path, options = {}) {
        return fetch(path, { credentials: "same-origin", ...options }).then(async (response) => {
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(payload.detail || "処理に失敗しました。");
            return payload;
        });
    }
    function form(fields = {}) {
        const data = new FormData();
        data.set("csrf", csrf);
        for (const [key, value] of Object.entries(fields)) data.set(key, value ?? "");
        return data;
    }
    function showError(error) { notice.textContent = error?.message || "処理に失敗しました。"; }
    function clearError() { notice.textContent = ""; }
    function newEventId() { return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`; }
    function setEnabled(enabled) {
        for (const id of ["tc-message-input", "tc-send-message", "tc-rename-group", "tc-toggle-members", "tc-toggle-files", "tc-new-folder", "tc-upload-file", "tc-leave-group"]) $(id).disabled = !enabled;
    }
    function groupIcon(group) {
        const avatar = document.createElement("div");
        avatar.className = "tc-avatar";
        if (group.icon_key) {
            const image = document.createElement("img");
            image.alt = "";
            image.src = `/tekne-chat/api/groups/${encodeURIComponent(group.id)}/icon`;
            avatar.append(image);
        } else avatar.textContent = (group.name || "T").slice(0, 1);
        return avatar;
    }
    function renderGroups() {
        const query = $("tc-group-search").value.trim().toLocaleLowerCase();
        groupsEl.replaceChildren();
        const visible = state.groups.filter((group) => !query || group.name.toLocaleLowerCase().includes(query));
        if (!visible.length) {
            const empty = document.createElement("p"); empty.className = "tc-empty"; empty.textContent = "グループはありません"; groupsEl.append(empty); return;
        }
        for (const group of visible) {
            const button = document.createElement("button"); button.type = "button"; button.className = "tc-group-item";
            button.setAttribute("aria-current", state.group?.id === group.id ? "true" : "false");
            const copy = document.createElement("div"); copy.className = "tc-group-copy";
            const title = document.createElement("strong"); title.textContent = group.name;
            const last = document.createElement("span"); last.textContent = group.last_message || `${group.member_count}人`;
            copy.append(title, last); button.append(groupIcon(group), copy);
            button.addEventListener("click", () => openGroup(group.id)); groupsEl.append(button);
        }
    }
    function linkify(container, text) {
        const pattern = /https?:\/\/[^\s<>"']+/gi;
        let cursor = 0;
        for (const match of text.matchAll(pattern)) {
            const index = match.index;
            if (index > cursor) container.append(document.createTextNode(text.slice(cursor, index)));
            let url = match[0];
            let suffix = "";
            while (/[).,!?、。]$/.test(url)) { suffix = url.slice(-1) + suffix; url = url.slice(0, -1); }
            const anchor = document.createElement("a"); anchor.href = url; anchor.textContent = url;
            anchor.target = "_blank"; anchor.rel = "noopener noreferrer nofollow";
            container.append(anchor, document.createTextNode(suffix)); cursor = index + match[0].length;
        }
        if (cursor < text.length) container.append(document.createTextNode(text.slice(cursor)));
    }
    function addMessage(message, { scroll = false } = {}) {
        if (!message || !message.id || state.messages.has(String(message.id))) return;
        state.messages.set(String(message.id), message);
        state.lastId = Math.max(state.lastId, Number(message.id));
        renderMessages(scroll);
    }
    function updateMessage(message) {
        if (!message) return;
        state.messages.set(String(message.id), message);
        state.lastId = Math.max(state.lastId, Number(message.id));
        renderMessages(false);
    }
    function renderMessages(scroll) {
        const wasNearBottom = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 90;
        messagesEl.replaceChildren();
        const ordered = [...state.messages.values()].sort((a, b) => a.id - b.id);
        if (!ordered.length) { const p = document.createElement("p"); p.className = "tc-empty"; p.textContent = "最初のメッセージを送ってみましょう"; messagesEl.append(p); }
        for (const message of ordered) {
            const card = document.createElement("article");
            if (message.type === "file_upload") {
                card.className = "tc-system-message";
                if (message.deleted) {
                    card.textContent = "メッセージが削除されました";
                    messagesEl.append(card);
                    continue;
                }
                const title = document.createElement("strong"); title.textContent = `${message.sender}がファイルをアップロードしました`;
                const filename = document.createElement("span"); filename.textContent = message.file_name || "ファイル";
                const folder = document.createElement("span"); folder.textContent = `保存先: ${message.folder_path || "ルート"}`;
                const link = document.createElement("a"); link.href = message.file_url || `/tekne-chat?group_id=${encodeURIComponent(state.group.id)}&file_id=${encodeURIComponent(message.file_id)}`;
                link.textContent = "ファイルを確認";
                card.append(title, filename, folder, link);
                if (message.sender === currentUser) {
                    const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "通知を削除";
                    remove.addEventListener("click", () => deleteMessage(message.id));
                    card.append(remove);
                }
                messagesEl.append(card); continue;
            }
            card.className = `tc-message${message.sender === currentUser ? " tc-own" : ""}`;
            const meta = document.createElement("div"); meta.className = "tc-message-meta";
            const sender = document.createElement("span"); sender.textContent = message.sender || "削除済みユーザー";
            const time = document.createElement("time"); time.textContent = new Date(message.created_at).toLocaleString("ja-JP", { dateStyle: "short", timeStyle: "short" });
            meta.append(sender, time);
            if (message.edited_at && !message.deleted) { const edited = document.createElement("span"); edited.textContent = "編集済み"; meta.append(edited); }
            const bubble = document.createElement("div"); bubble.className = "tc-message-bubble";
            if (message.deleted) bubble.textContent = "メッセージが削除されました";
            else linkify(bubble, message.body || "");
            card.append(meta, bubble);
            if (!message.deleted && message.sender === currentUser) {
                const actions = document.createElement("div"); actions.className = "tc-message-actions";
                const edit = document.createElement("button"); edit.type = "button"; edit.textContent = "編集";
                edit.addEventListener("click", () => editMessage(message));
                const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "削除";
                remove.addEventListener("click", () => deleteMessage(message.id)); actions.append(edit, remove); card.append(actions);
            }
            messagesEl.append(card);
        }
        if (scroll || wasNearBottom) messagesEl.scrollTop = messagesEl.scrollHeight;
    }
    async function loadMessages({ beforeId = null, afterId = null, replace = false } = {}) {
        if (!state.group || state.loading) return;
        state.loading = true;
        try {
            const params = new URLSearchParams({ limit: "50" });
            if (beforeId) params.set("before_id", String(beforeId));
            if (afterId) params.set("after_id", String(afterId));
            const result = await api(`/tekne-chat/api/groups/${encodeURIComponent(state.group.id)}/messages?${params}`);
            if (replace) { state.messages.clear(); state.lastId = 0; }
            for (const message of result.messages) addMessage(message);
            if (!afterId) state.historyBeforeId = result.next_before_id || null;
            $("tc-load-older").hidden = !state.historyBeforeId;
            renderMessages(replace);
            return result;
        } catch (error) { showError(error); }
        finally { state.loading = false; }
    }
    async function catchUp() {
        if (!state.group) return;
        let cursor = state.lastId;
        while (true) {
            const result = await loadMessages({ afterId: cursor });
            if (!result?.messages?.length) break;
            const newest = Math.max(...result.messages.map((message) => Number(message.id)));
            if (newest <= cursor || result.messages.length < 50) break;
            cursor = newest;
        }
    }
    function connectSocket(groupId) {
        clearTimeout(state.reconnectTimer);
        state.socket?.close();
        const scheme = location.protocol === "https:" ? "wss:" : "ws:";
        const socket = new WebSocket(`${scheme}//${location.host}/ws/tekne-chat/${encodeURIComponent(groupId)}`);
        state.socket = socket;
        socket.addEventListener("open", () => { state.retry = 0; catchUp(); });
        socket.addEventListener("message", (event) => {
            let payload; try { payload = JSON.parse(event.data); } catch { return; }
            if (payload.type === "message.created") addMessage(payload.message, { scroll: true });
            else if (payload.type === "message.updated") updateMessage(payload.message);
            else if (["files.updated", "members.updated", "group.updated"].includes(payload.type)) refreshCurrentData();
            else if (payload.type === "member.left") refreshGroups();
            else if (payload.type === "error") showError(new Error(payload.message));
        });
        socket.addEventListener("close", () => {
            if (state.socket !== socket || !state.group || state.group.id !== groupId) return;
            const wait = Math.min(30000, 600 * (2 ** Math.min(state.retry++, 6)));
            state.reconnectTimer = setTimeout(() => { if (state.group?.id === groupId) connectSocket(groupId); }, wait);
        });
    }
    async function refreshGroups() {
        const result = await api("/tekne-chat/api/groups");
        state.groups = result.groups;
        renderGroups();
        if (state.group) {
            const refreshed = state.groups.find((group) => group.id === state.group.id);
            if (!refreshed) {
                state.socket?.close(); state.group = null; setEnabled(false); $("tc-current-name").textContent = "グループを選択";
                messagesEl.replaceChildren();
            } else {
                state.group = refreshed;
                $("tc-current-name").textContent = refreshed.name;
                const avatar = groupIcon(refreshed); avatar.id = "tc-current-icon"; $("tc-current-icon").replaceWith(avatar);
            }
        }
    }
    async function openGroup(groupId, options = {}) {
        clearError();
        state.socket?.close(); clearTimeout(state.reconnectTimer);
        state.group = state.groups.find((group) => group.id === groupId) || { id: groupId, name: "読み込み中" };
        const group = state.group;
        $("tc-current-name").textContent = group.name;
        const currentIcon = groupIcon(group); currentIcon.id = "tc-current-icon";
        $("tc-current-icon").replaceWith(currentIcon);
        $("tc-current-members").textContent = "";
        setEnabled(true); renderGroups();
        state.messages.clear(); state.lastId = 0; state.historyBeforeId = null; messagesEl.replaceChildren();
        await loadMessages({ replace: true });
        if (state.group?.id !== groupId) return;
        connectSocket(groupId);
        await loadMembers();
        if (options.fileId) await openFileTarget(options.fileId);
        else if (options.folderId) { state.folderId = options.folderId; await loadFiles(); }
        else if (!sidePanel.hidden && state.panel === "files") await loadFiles();
    }
    async function loadMembers() {
        if (!state.group) return;
        const result = await api(`/tekne-chat/api/groups/${encodeURIComponent(state.group.id)}/members`);
        state.members = result.members;
        $("tc-current-members").textContent = `${state.members.length}人`;
        const list = $("tc-member-list"); list.replaceChildren();
        for (const member of state.members) {
            const row = document.createElement("div"); row.className = "tc-panel-row";
            const name = document.createElement("span"); name.textContent = member.login_id || "削除済みユーザー"; row.append(name); list.append(row);
        }
    }
    async function loadFiles() {
        if (!state.group) return;
        const params = new URLSearchParams();
        const query = $("tc-file-search").value.trim();
        if (query) params.set("q", query); else if (state.folderId) params.set("folder_id", state.folderId);
        const result = await api(`/tekne-chat/api/groups/${encodeURIComponent(state.group.id)}/folders?${params}`);
        $("tc-folder-path").textContent = result.folder_path;
        const list = $("tc-file-list"); list.replaceChildren();
        for (const folder of result.folders) {
            const row = document.createElement("div"); row.className = "tc-panel-row";
            const name = document.createElement("button"); name.className = "tc-file-row-name"; name.textContent = `📁 ${folder.name}`;
            name.addEventListener("click", () => { state.folderId = folder.id; $("tc-file-search").value = ""; loadFiles(); });
            const rename = document.createElement("button"); rename.textContent = "名前変更";
            rename.addEventListener("click", async () => { const value = prompt("新しいフォルダ名", folder.name); if (!value) return; try { await api(`/tekne-chat/api/folders/${folder.id}`, { method: "PATCH", body: form({ name: value }) }); await loadFiles(); } catch (error) { showError(error); } });
            const remove = document.createElement("button"); remove.textContent = "削除";
            remove.addEventListener("click", async () => { if (!confirm(`「${folder.name}」と中のファイルを削除しますか？`)) return; try { await api(`/tekne-chat/api/folders/${folder.id}`, { method: "DELETE", body: form() }); await loadFiles(); } catch (error) { showError(error); } });
            row.append(name, rename, remove); list.append(row);
        }
        for (const file of result.files) {
            const row = document.createElement("div"); row.className = "tc-panel-row";
            const download = document.createElement("a"); download.className = "tc-file-row-name"; download.textContent = `📄 ${file.display_name}`;
            download.href = `/tekne-chat/api/files/${encodeURIComponent(file.id)}/download`;
            const rename = document.createElement("button"); rename.textContent = "名前変更";
            rename.addEventListener("click", async () => { const value = prompt("新しいファイル名", file.display_name); if (!value) return; try { await api(`/tekne-chat/api/files/${file.id}`, { method: "PATCH", body: form({ name: value }) }); await loadFiles(); } catch (error) { showError(error); } });
            const move = document.createElement("button"); move.textContent = "移動";
            move.addEventListener("click", async () => {
                try {
                    const tree = await api(`/tekne-chat/api/groups/${state.group.id}/folder-tree`);
                    const select = $("tc-move-target"); select.replaceChildren(new Option("ルート", ""));
                    for (const folder of tree.folders) select.add(new Option(folder.path, folder.id));
                    select.value = file.folder_id || ""; state.movingFileId = file.id; $("tc-move-dialog").showModal();
                } catch (error) { showError(error); }
            });
            const remove = document.createElement("button"); remove.textContent = "削除";
            remove.addEventListener("click", async () => { if (!confirm(`「${file.display_name}」を削除しますか？`)) return; try { await api(`/tekne-chat/api/files/${file.id}`, { method: "DELETE", body: form() }); await loadFiles(); } catch (error) { showError(error); } });
            row.append(download, rename, move, remove); list.append(row);
        }
        if (!result.files.length && !result.folders.length) { const empty = document.createElement("p"); empty.className = "tc-empty"; empty.textContent = "項目はありません"; list.append(empty); }
    }
    async function openFileTarget(fileId) {
        try {
            const file = await api(`/tekne-chat/api/files/${encodeURIComponent(fileId)}`);
            state.folderId = file.folder_id;
            sidePanel.hidden = false; state.panel = "files"; $("tc-panel-title").textContent = "共有フォルダ";
            $("tc-members-panel").hidden = true; $("tc-files-panel").hidden = false;
            await loadFiles();
            const link = document.createElement("a"); link.href = `/tekne-chat/api/files/${encodeURIComponent(file.id)}/download`; link.textContent = `${file.display_name}をダウンロード`;
            link.className = "tc-panel-row"; $("tc-file-list").prepend(link);
        } catch (error) { showError(error); }
    }
    async function refreshCurrentData() {
        await refreshGroups();
        if (state.group) { await loadMembers().catch(showError); if (state.panel === "files") await loadFiles().catch(showError); }
    }
    async function editMessage(message) {
        const body = prompt("メッセージを編集", message.body); if (body === null) return;
        try { const result = await api(`/tekne-chat/api/messages/${message.id}`, { method: "PATCH", body: form({ body }) }); updateMessage(result.message); clearError(); }
        catch (error) { showError(error); }
    }
    async function deleteMessage(id) {
        if (!confirm("このメッセージを削除しますか？")) return;
        try { const result = await api(`/tekne-chat/api/messages/${id}`, { method: "DELETE", body: form() }); updateMessage(result.message); clearError(); }
        catch (error) { showError(error); }
    }

    $("tc-group-search").addEventListener("input", renderGroups);
    $("tc-create-group").addEventListener("click", () => $("tc-create-dialog").showModal());
    $("tc-create-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
            const result = await api("/tekne-chat/api/groups", { method: "POST", body: form({ name: $("tc-new-group-name").value }) });
            $("tc-create-dialog").close(); $("tc-new-group-name").value = ""; await refreshGroups(); await openGroup(result.id);
        } catch (error) { showError(error); }
    });
    document.querySelectorAll(".tc-dialog-cancel").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));
    $("tc-message-form").addEventListener("submit", async (event) => {
        event.preventDefault(); if (!state.group || state.sending) return;
        const body = $("tc-message-input").value;
        if (!body.trim()) return;
        state.sending = true; $("tc-send-message").disabled = true;
        const clientEventId = newEventId();
        const payload = { type: "message.create", body, client_event_id: clientEventId };
        try {
            if (state.socket?.readyState === WebSocket.OPEN) state.socket.send(JSON.stringify(payload));
            else {
                const result = await api(`/tekne-chat/api/groups/${state.group.id}/messages`, { method: "POST", body: form({ body, client_event_id: clientEventId }) });
                addMessage(result.message, { scroll: true });
            }
            $("tc-message-input").value = "";
        } catch (error) { showError(error); }
        finally {
            state.sending = false;
            $("tc-send-message").disabled = !state.group;
        }
    });
    $("tc-load-older").addEventListener("click", async () => {
        if (!state.historyBeforeId || state.loading) return;
        const previousHeight = messagesEl.scrollHeight;
        await loadMessages({ beforeId: state.historyBeforeId });
        messagesEl.scrollTop += messagesEl.scrollHeight - previousHeight;
    });
    $("tc-message-input").addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("tc-message-form").requestSubmit(); }
    });
    $("tc-rename-group").addEventListener("click", () => {
        if (!state.group) return;
        $("tc-edit-group-name").value = state.group.name;
        $("tc-icon-dialog").showModal();
    });
    $("tc-icon-form").addEventListener("submit", async (event) => {
        event.preventDefault(); if (!state.group) return;
        try {
            await api(`/tekne-chat/api/groups/${state.group.id}`, { method: "PATCH", body: form({ name: $("tc-edit-group-name").value }) });
            const upload = new FormData(); upload.set("csrf", csrf);
            const icon = $("tc-group-icon-file").files[0]; if (icon) upload.set("file", icon);
            await api(`/tekne-chat/api/groups/${state.group.id}/icon`, { method: "POST", body: upload });
            $("tc-icon-dialog").close(); $("tc-group-icon-file").value = ""; await refreshCurrentData(); await openGroup(state.group.id);
        } catch (error) { showError(error); }
    });
    function openPanel(panel) {
        state.panel = panel; sidePanel.hidden = false;
        const members = panel === "members";
        $("tc-panel-title").textContent = members ? "メンバー" : "共有フォルダ";
        $("tc-members-panel").hidden = !members; $("tc-files-panel").hidden = members;
        if (members) loadMembers().catch(showError); else loadFiles().catch(showError);
    }
    $("tc-toggle-members").addEventListener("click", () => openPanel("members"));
    $("tc-toggle-files").addEventListener("click", () => openPanel("files"));
    $("tc-close-panel").addEventListener("click", () => { sidePanel.hidden = true; });
    $("tc-move-form").addEventListener("submit", async (event) => {
        event.preventDefault(); if (!state.movingFileId) return;
        try { await api(`/tekne-chat/api/files/${state.movingFileId}/move`, { method: "POST", body: form({ folder_id: $("tc-move-target").value }) }); $("tc-move-dialog").close(); state.movingFileId = null; await loadFiles(); }
        catch (error) { showError(error); }
    });
    $("tc-student-search").addEventListener("input", () => {
        clearTimeout(state.searchTimer);
        state.searchTimer = setTimeout(async () => {
            const q = $("tc-student-search").value.trim(); const results = $("tc-student-results"); results.replaceChildren();
            if (q.length < 2) return;
            try {
                const data = await api(`/tekne-chat/api/students?q=${encodeURIComponent(q)}`);
                for (const student of data.students) {
                    const row = document.createElement("div"); row.className = "tc-panel-row";
                    const name = document.createElement("span"); name.textContent = student.login_id;
                    const invite = document.createElement("button"); invite.textContent = "招待";
                    invite.addEventListener("click", async () => { try { await api(`/tekne-chat/api/groups/${state.group.id}/members`, { method: "POST", body: form({ student_id: student.login_id }) }); await loadMembers(); row.remove(); } catch (error) { showError(error); } });
                    row.append(name, invite); results.append(row);
                }
            } catch (error) { showError(error); }
        }, 250);
    });
    $("tc-leave-group").addEventListener("click", async () => {
        if (!state.group || !confirm("このグループを退出しますか？")) return;
        try { await api(`/tekne-chat/api/groups/${state.group.id}/members/me`, { method: "DELETE", body: form() }); state.socket?.close(); state.group = null; messagesEl.replaceChildren(); setEnabled(false); $("tc-current-name").textContent = "グループを選択"; await refreshGroups(); }
        catch (error) { showError(error); }
    });
    $("tc-new-folder").addEventListener("click", async () => {
        const name = prompt("フォルダ名"); if (!name || !state.group) return;
        try { await api(`/tekne-chat/api/groups/${state.group.id}/folders`, { method: "POST", body: form({ name, parent_id: state.folderId || "" }) }); await loadFiles(); }
        catch (error) { showError(error); }
    });
    $("tc-up-folder").addEventListener("click", async () => {
        if (!state.folderId || !state.group) return;
        try { const data = await api(`/tekne-chat/api/groups/${state.group.id}/folders?folder_id=${encodeURIComponent(state.folderId)}`); state.folderId = data.current_parent_id || null; await loadFiles(); }
        catch (error) { showError(error); }
    });
    $("tc-file-search").addEventListener("input", () => { clearTimeout(state.searchTimer); state.searchTimer = setTimeout(() => loadFiles().catch(showError), 220); });
    $("tc-upload-file").addEventListener("change", async (event) => {
        const file = event.target.files[0]; if (!file || !state.group) return;
        const data = form({ folder_id: state.folderId || "" }); data.set("file", file);
        try { await api(`/tekne-chat/api/groups/${state.group.id}/files`, { method: "POST", body: data }); clearError(); await loadFiles(); }
        catch (error) { showError(error); }
        finally { event.target.value = ""; }
    });

    (async () => {
        try {
            await refreshGroups();
            const fileId = app.dataset.initialFile;
            const fileGroup = fileId ? new URLSearchParams(location.search).get("group_id") : null;
            const selected = app.dataset.initialGroup || fileGroup || state.groups[0]?.id;
            if (selected) await openGroup(selected, { fileId, folderId: app.dataset.initialFolder });
        } catch (error) { showError(error); }
    })();
})();
