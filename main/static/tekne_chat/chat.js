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
        reconnectTimer: null, folderId: null, folderTree: [], expandedFolders: new Set(), searchTimer: null,
        lastId: 0, historyBeforeId: null, fileRequestId: 0, selectedEntry: null, panel: "members", loading: false, movingFileId: null, sending: false };

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
    const isPersonal = (group) => group?.kind === "personal";
    function setEnabled(enabled) {
        for (const id of ["tc-message-input", "tc-send-message", "tc-rename-group", "tc-toggle-members", "tc-toggle-files", "tc-new-folder", "tc-upload-file", "tc-leave-group", "tc-selected-download", "tc-selected-rename", "tc-selected-move", "tc-selected-delete"]) $(id).disabled = !enabled;
    }
    function groupIcon(group) {
        const avatar = document.createElement("div");
        avatar.className = "tc-avatar";
        if (group.icon_key) {
            const image = document.createElement("img");
            image.alt = "";
            image.src = `/tekne-chat/api/groups/${encodeURIComponent(group.id)}/icon?v=${encodeURIComponent(group.icon_key)}`;
            avatar.append(image);
        } else avatar.textContent = (group.name || "T").slice(0, 1);
        return avatar;
    }
    function renderGroups() {
        const query = $("tc-group-search").value.trim().toLocaleLowerCase();
        groupsEl.replaceChildren();
        const visible = state.groups.filter((group) => !query || group.name.toLocaleLowerCase().includes(query));
        if (!visible.length) {
            const empty = document.createElement("p"); empty.className = "tc-empty"; empty.textContent = "個人スペース・グループはありません"; groupsEl.append(empty); return;
        }
        // 個人スペースを上、グループを下に分けて表示する
        const sections = [["個人スペース", visible.filter(isPersonal)], ["グループ", visible.filter((group) => !isPersonal(group))]];
        for (const [heading, groups] of sections) {
            if (!groups.length) continue;
            const label = document.createElement("h2"); label.className = "tc-group-section"; label.textContent = heading; groupsEl.append(label);
            for (const group of groups) {
                const button = document.createElement("button"); button.type = "button"; button.className = "tc-group-item";
                button.setAttribute("aria-current", state.group?.id === group.id ? "true" : "false");
                const copy = document.createElement("div"); copy.className = "tc-group-copy";
                const title = document.createElement("strong"); title.textContent = group.name;
                const last = document.createElement("span"); last.textContent = group.last_message || (isPersonal(group) ? "個人スペース" : `${group.member_count}人`);
                copy.append(title, last); button.append(groupIcon(group), copy);
                button.addEventListener("click", () => openGroup(group.id)); groupsEl.append(button);
            }
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
        if (!ordered.length) { const p = document.createElement("p"); p.className = "tc-empty"; p.textContent = isPersonal(state.group) ? "メモやファイルを残しておけます" : "最初のメッセージを送ってみましょう"; messagesEl.append(p); }
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
            if (payload.type === "message.created") { addMessage(payload.message, { scroll: true }); scheduleGroupRefresh(); }
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
    // 更新があったら一覧を取り直して、新しい順に並べ直す（連続した更新はまとめて1回にする）
    function scheduleGroupRefresh() {
        clearTimeout(state.groupRefreshTimer);
        state.groupRefreshTimer = setTimeout(() => refreshGroups().catch(() => {}), 400);
    }
    async function refreshGroups() {
        const result = await api("/tekne-chat/api/groups");
        state.groups = result.groups;
        renderGroups();
        if (state.group) {
            const refreshed = state.groups.find((group) => group.id === state.group.id);
            if (!refreshed) {
                state.socket?.close(); state.group = null; state.selectedEntry = null; renderSelectionActions(); setEnabled(false); $("tc-current-name").textContent = "グループを選択";
                if ($("tc-report-link")) $("tc-report-link").hidden = true;
                messagesEl.replaceChildren();
            } else {
                state.group = refreshed;
                applyKind(refreshed);
                $("tc-current-name").textContent = refreshed.name;
                const avatar = groupIcon(refreshed); avatar.id = "tc-current-icon"; $("tc-current-icon").replaceWith(avatar);
            }
        }
    }
    async function openGroup(groupId, options = {}) {
        clearError();
        state.socket?.close(); clearTimeout(state.reconnectTimer);
        state.fileRequestId += 1;
        state.folderId = null; state.folderTree = []; state.expandedFolders.clear(); state.selectedEntry = null;
        state.group = state.groups.find((group) => group.id === groupId) || { id: groupId, name: "読み込み中" };
        const group = state.group;
        const reportLink = $("tc-report-link"); if (reportLink) { reportLink.href = `/tekne-chat/groups/${encodeURIComponent(groupId)}/report`; reportLink.hidden = false; }
        $("tc-current-name").textContent = group.name;
        const currentIcon = groupIcon(group); currentIcon.id = "tc-current-icon";
        $("tc-current-icon").replaceWith(currentIcon);
        $("tc-current-members").textContent = "";
        setEnabled(true); applyKind(group); renderSelectionActions(); renderGroups();
        state.messages.clear(); state.lastId = 0; state.historyBeforeId = null; messagesEl.replaceChildren();
        await loadMessages({ replace: true });
        if (state.group?.id !== groupId) return;
        connectSocket(groupId);
        await loadMembers();
        if (options.fileId) await openFileTarget(options.fileId);
        else if (options.folderId) { state.folderId = options.folderId; await openPanel("files"); }
        else if (!sidePanel.hidden && state.panel === "files") await loadFiles();
    }
    // 個人スペースではメンバー関係の操作を出さず、チャット欄を自分用メモとして使う
    function applyKind(group) {
        const personal = isPersonal(group);
        $("tc-toggle-members").hidden = personal;
        $("tc-toggle-members").disabled = personal || !group;
        $("tc-leave-group").hidden = personal;
        $("tc-toggle-files").setAttribute("aria-label", personal ? "フォルダ" : "共有フォルダ");
        $("tc-toggle-files").title = personal ? "フォルダ" : "共有フォルダ";
        $("tc-rename-group").setAttribute("aria-label", personal ? "個人スペース設定" : "グループ設定");
        $("tc-rename-group").title = personal ? "個人スペース設定" : "グループ設定";
        $("tc-message-input").placeholder = personal
            ? "メモを入力（Enterで保存、Shift+Enterで改行）\nファイルはこの画面にドロップすると保存できます"
            : "メッセージを入力（Enterで送信、Shift+Enterで改行）\nファイルはこの画面にドロップすると保存できます";
        $("tc-send-message").textContent = personal ? "保存" : "送信";
        $("tc-file-search").placeholder = personal ? "個人スペース内を検索" : "グループ内を検索";
        if (personal) {
            $("tc-current-members").textContent = "個人スペース";
            if (!sidePanel.hidden && state.panel === "members") { sidePanel.hidden = true; app.classList.remove("tc-show-files"); }
        }
    }
    async function loadMembers() {
        if (!state.group || isPersonal(state.group)) return;
        const result = await api(`/tekne-chat/api/groups/${encodeURIComponent(state.group.id)}/members`);
        state.members = result.members;
        $("tc-current-members").textContent = `${state.members.length}人`;
        const list = $("tc-member-list"); list.replaceChildren();
        for (const member of state.members) {
            const row = document.createElement("div"); row.className = "tc-panel-row";
            const name = document.createElement("span"); name.textContent = member.login_id || "削除済みユーザー"; row.append(name); list.append(row);
        }
    }
    function folderMap() { return new Map(state.folderTree.map((folder) => [String(folder.id), folder])); }
    function expandFolderAncestors(folderId) {
        const folders = folderMap();
        let folder = folders.get(String(folderId));
        while (folder?.parent_id) {
            state.expandedFolders.add(String(folder.parent_id));
            folder = folders.get(String(folder.parent_id));
        }
    }
    function renderFolderTree() {
        const tree = $("tc-folder-tree");
        tree.replaceChildren();
        const childrenByParent = new Map();
        for (const folder of state.folderTree) {
            const key = folder.parent_id ? String(folder.parent_id) : "";
            if (!childrenByParent.has(key)) childrenByParent.set(key, []);
            childrenByParent.get(key).push(folder);
        }
        const root = document.createElement("button");
        root.type = "button"; root.className = "tc-tree-item tc-tree-root"; root.setAttribute("role", "treeitem");
        root.setAttribute("aria-selected", state.folderId ? "false" : "true"); root.textContent = "📁 ルート";
        root.addEventListener("click", () => selectFolder(null).catch(showError)); tree.append(root);

        function appendChildren(parent, parentId = "", depth = 0) {
            for (const folder of childrenByParent.get(parentId) || []) {
                const id = String(folder.id);
                const row = document.createElement("div"); row.className = "tc-tree-row"; row.style.setProperty("--tc-tree-depth", depth);
                const children = childrenByParent.get(id) || [];
                const toggle = document.createElement("button"); toggle.type = "button"; toggle.className = "tc-tree-toggle";
                toggle.textContent = children.length ? (state.expandedFolders.has(id) ? "▾" : "▸") : "";
                toggle.disabled = !children.length; toggle.tabIndex = -1; toggle.setAttribute("aria-label", `${folder.name}を${state.expandedFolders.has(id) ? "折りたたむ" : "展開"}`);
                toggle.addEventListener("click", () => { if (state.expandedFolders.has(id)) state.expandedFolders.delete(id); else state.expandedFolders.add(id); renderFolderTree(); });
                const select = document.createElement("button"); select.type = "button"; select.className = "tc-tree-item";
                select.setAttribute("role", "treeitem"); select.setAttribute("aria-selected", String(state.folderId) === id ? "true" : "false");
                if (children.length) select.setAttribute("aria-expanded", String(state.expandedFolders.has(id)));
                select.textContent = `📁 ${folder.name}`; select.addEventListener("click", () => selectFolder(id).catch(showError));
                row.append(toggle, select); parent.append(row);
                if (children.length) {
                    const group = document.createElement("div"); group.setAttribute("role", "group"); group.hidden = !state.expandedFolders.has(id);
                    appendChildren(group, id, depth + 1); parent.append(group);
                }
            }
        }
        appendChildren(tree);
    }
    async function loadFolderTree() {
        if (!state.group) return;
        const groupId = state.group.id;
        const result = await api(`/tekne-chat/api/groups/${encodeURIComponent(groupId)}/folder-tree`);
        if (state.group?.id !== groupId) return;
        state.folderTree = result.folders;
        const existing = new Set(state.folderTree.map((folder) => String(folder.id)));
        state.expandedFolders = new Set([...state.expandedFolders].filter((id) => existing.has(id)));
        if (state.folderId && !existing.has(String(state.folderId))) state.folderId = null;
        if (state.folderId) expandFolderAncestors(state.folderId);
        renderFolderTree();
    }
    async function selectFolder(folderId) {
        state.folderId = folderId;
        $("tc-file-search").value = "";
        if (folderId) expandFolderAncestors(folderId);
        renderFolderTree();
        await loadFiles();
    }
    function formatDate(value) {
        if (!value) return "—";
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? value : date.toLocaleString("ja-JP");
    }
    function formatSize(value) {
        const size = Number(value);
        if (!Number.isFinite(size)) return "—";
        if (size < 1024) return `${size} B`;
        if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
        if (size < 1024 * 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
        return `${(size / (1024 * 1024 * 1024)).toFixed(1)} GB`;
    }
    function renderSelectionActions() {
        const selected = state.selectedEntry;
        const hasEntry = Boolean(selected);
        $("tc-selection-status").textContent = selected ? `選択中: ${selected.item.name || selected.item.display_name}` : "項目を選択してください";
        $("tc-selected-download").disabled = !hasEntry || selected.kind !== "file";
        $("tc-selected-rename").disabled = !hasEntry;
        $("tc-selected-move").disabled = !hasEntry || selected.kind !== "file";
        $("tc-selected-delete").disabled = !hasEntry;
    }
    function setSelectedEntry(kind, item) {
        state.selectedEntry = kind && item ? { kind, item } : null;
        renderSelectionActions();
        for (const row of document.querySelectorAll(".tc-file-row")) {
            const selected = state.selectedEntry && String(state.selectedEntry.item.id) === row.dataset.id && state.selectedEntry.kind === row.dataset.kind;
            row.classList.toggle("tc-selected", Boolean(selected));
            row.setAttribute("aria-pressed", String(Boolean(selected)));
        }
    }
    function createEntryRow({ item, kind, icon, uploader = "—", createdAt = "", size = "—", path = "", onOpen }) {
        const row = document.createElement("div"); row.className = "tc-file-row";
        row.dataset.id = String(item.id); row.dataset.kind = kind;
        row.setAttribute("role", "button"); row.tabIndex = 0;
        row.setAttribute("aria-label", `${item.name || item.display_name}。クリックで選択、ダブルクリックで開く`);
        const selected = state.selectedEntry?.kind === kind && String(state.selectedEntry.item.id) === String(item.id);
        row.classList.toggle("tc-selected", selected); row.setAttribute("aria-pressed", String(selected));
        row.addEventListener("click", () => setSelectedEntry(kind, item));
        row.addEventListener("dblclick", () => Promise.resolve(onOpen?.()).catch(showError));
        row.addEventListener("keydown", (event) => {
            if (event.key === " ") { event.preventDefault(); setSelectedEntry(kind, item); }
            if (event.key === "Enter") { event.preventDefault(); Promise.resolve(onOpen?.()).catch(showError); }
        });
        const title = document.createElement("div"); title.className = "tc-file-entry-title";
        const name = item.name || item.display_name;
        const entryName = document.createElement("span"); entryName.className = "tc-file-entry-link";
        entryName.textContent = `${icon} ${name}`; entryName.title = path || name; title.append(entryName);
        if (path) { const location = document.createElement("small"); location.textContent = path; title.append(location); }
        const uploaderCell = document.createElement("span"); uploaderCell.className = "tc-file-cell"; uploaderCell.dataset.label = "投稿者"; uploaderCell.textContent = uploader || "—";
        const dateCell = document.createElement("span"); dateCell.className = "tc-file-cell"; dateCell.dataset.label = "登録日時"; dateCell.textContent = formatDate(createdAt);
        const sizeCell = document.createElement("span"); sizeCell.className = "tc-file-cell"; sizeCell.dataset.label = "サイズ"; sizeCell.textContent = size;
        row.append(title, uploaderCell, dateCell, sizeCell);
        return row;
    }
    async function loadFiles() {
        if (!state.group) return;
        const groupId = state.group.id;
        if (!state.folderTree.length) await loadFolderTree();
        if (state.group?.id !== groupId) return;
        const requestId = ++state.fileRequestId;
        const params = new URLSearchParams();
        const query = $("tc-file-search").value.trim();
        if (query) params.set("q", query); else if (state.folderId) params.set("folder_id", state.folderId);
        const result = await api(`/tekne-chat/api/groups/${encodeURIComponent(groupId)}/folders?${params}`);
        if (state.group?.id !== groupId || requestId !== state.fileRequestId) return;
        if (state.selectedEntry) {
            const selectedId = String(state.selectedEntry.item.id);
            const collection = state.selectedEntry.kind === "file" ? result.files : result.folders;
            if (!collection.some((item) => String(item.id) === selectedId)) setSelectedEntry(null, null);
        }
        $("tc-folder-path").textContent = query ? `検索結果: ${query}` : (result.folder_path || "ルート");
        const list = $("tc-file-list"); list.replaceChildren();
        for (const folder of result.folders) {
            const path = folderMap().get(String(folder.id))?.path || folder.name;
            list.append(createEntryRow({ item: folder, kind: "folder", name: folder.name, icon: "📁", createdAt: folder.created_at, path, onOpen: () => selectFolder(folder.id) }));
        }
        for (const file of result.files) {
            const path = file.folder_id ? folderMap().get(String(file.folder_id))?.path || "" : "ルート";
            const open = () => { window.location.href = `/tekne-chat/api/files/${encodeURIComponent(file.id)}/download`; };
            list.append(createEntryRow({ item: file, kind: "file", icon: "📄", uploader: file.uploaded_by_login_id || "削除済みユーザー", createdAt: file.created_at, size: formatSize(file.size_bytes), path, onOpen: open }));
        }
        if (!result.files.length && !result.folders.length) { const empty = document.createElement("p"); empty.className = "tc-empty"; empty.textContent = query ? "検索結果がありません" : "項目はありません"; list.append(empty); }
    }
    async function openFileTarget(fileId) {
        try {
            const file = await api(`/tekne-chat/api/files/${encodeURIComponent(fileId)}`);
            state.folderId = file.folder_id;
            await openPanel("files");
        } catch (error) { showError(error); }
    }
    async function refreshCurrentData() {
        await refreshGroups();
        if (state.group) {
            await loadMembers().catch(showError);
            if (state.panel === "files") { await loadFolderTree().catch(showError); await loadFiles().catch(showError); }
        }
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
    const newKind = () => document.querySelector('input[name="tc-new-kind"]:checked')?.value || "personal";
    function updateCreateLabel() {
        $("tc-new-group-name-label").textContent = newKind() === "personal" ? "個人スペース名" : "グループ名";
        $("tc-new-group-name").placeholder = newKind() === "personal" ? "例：植物の成長の研究" : "例：1班 植物チーム";
    }
    document.querySelectorAll('input[name="tc-new-kind"]').forEach((input) => input.addEventListener("change", updateCreateLabel));
    $("tc-create-group").addEventListener("click", () => { updateCreateLabel(); $("tc-create-dialog").showModal(); });
    $("tc-create-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
            const result = await api("/tekne-chat/api/groups", { method: "POST", body: form({ name: $("tc-new-group-name").value, kind: newKind() }) });
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
            scheduleGroupRefresh();
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
        const personal = isPersonal(state.group);
        $("tc-settings-title").textContent = personal ? "個人スペース設定" : "グループ設定";
        $("tc-edit-group-name-label").textContent = personal ? "個人スペース名" : "グループ名";
        $("tc-group-icon-label").textContent = personal ? "アイコン（任意）" : "グループアイコン（任意）";
        $("tc-edit-group-name").value = state.group.name;
        $("tc-group-icon-file").value = ""; $("tc-remove-icon").checked = false;
        $("tc-remove-icon-row").hidden = !state.group.icon_key;
        showIconPreview(state.group);
        $("tc-icon-dialog").showModal();
    });
    function showIconPreview(group, file = null) {
        const preview = $("tc-icon-preview"); preview.replaceChildren();
        if (file) {
            const image = document.createElement("img"); image.alt = "";
            const url = URL.createObjectURL(file); image.src = url;
            image.addEventListener("load", () => URL.revokeObjectURL(url), { once: true });
            preview.append(image);
        } else if (group.icon_key && !$("tc-remove-icon").checked) preview.append(...groupIcon(group).childNodes);
        else preview.textContent = (group.name || "T").slice(0, 1);
    }
    $("tc-group-icon-file").addEventListener("change", () => {
        const file = $("tc-group-icon-file").files[0];
        if (file) $("tc-remove-icon").checked = false;
        if (state.group) showIconPreview(state.group, file || null);
    });
    $("tc-remove-icon").addEventListener("change", () => {
        if ($("tc-remove-icon").checked) $("tc-group-icon-file").value = "";
        if (state.group) showIconPreview(state.group);
    });
    $("tc-icon-form").addEventListener("submit", async (event) => {
        event.preventDefault(); if (!state.group) return;
        try {
            await api(`/tekne-chat/api/groups/${state.group.id}`, { method: "PATCH", body: form({ name: $("tc-edit-group-name").value }) });
            const upload = new FormData(); upload.set("csrf", csrf);
            const icon = $("tc-group-icon-file").files[0]; if (icon) upload.set("file", icon);
            else if ($("tc-remove-icon").checked) upload.set("remove_icon", "true");
            await api(`/tekne-chat/api/groups/${state.group.id}/icon`, { method: "POST", body: upload });
            $("tc-icon-dialog").close(); $("tc-group-icon-file").value = ""; await refreshCurrentData(); await openGroup(state.group.id);
        } catch (error) { showError(error); }
    });
    function openPanel(panel) {
        state.panel = panel; sidePanel.hidden = false;
        const members = panel === "members";
        $("tc-panel-title").textContent = members ? "メンバー" : (isPersonal(state.group) ? "フォルダ" : "共有フォルダ");
        $("tc-close-panel").setAttribute("aria-label", members ? "閉じる" : "チャットに戻る");
        app.classList.toggle("tc-show-files", !members);
        $("tc-members-panel").hidden = !members; $("tc-files-panel").hidden = members;
        return members ? loadMembers().catch(showError) : loadFiles().catch(showError);
    }
    $("tc-toggle-members").addEventListener("click", () => openPanel("members"));
    $("tc-toggle-files").addEventListener("click", () => openPanel("files"));
    $("tc-close-panel").addEventListener("click", () => { sidePanel.hidden = true; app.classList.remove("tc-show-files"); });
    $("tc-selected-download").addEventListener("click", () => {
        const selected = state.selectedEntry;
        if (selected?.kind === "file") window.location.href = `/tekne-chat/api/files/${encodeURIComponent(selected.item.id)}/download`;
    });
    $("tc-selected-rename").addEventListener("click", async () => {
        const selected = state.selectedEntry;
        if (!selected) return;
        const currentName = selected.item.name || selected.item.display_name;
        const name = prompt("新しい名前", currentName);
        if (!name) return;
        try {
            const path = selected.kind === "folder" ? `/tekne-chat/api/folders/${selected.item.id}` : `/tekne-chat/api/files/${selected.item.id}`;
            await api(path, { method: "PATCH", body: form({ name }) });
            if (selected.kind === "folder") { selected.item.name = name; await loadFolderTree(); }
            else selected.item.display_name = name;
            renderSelectionActions(); await loadFiles();
        } catch (error) { showError(error); }
    });
    $("tc-selected-move").addEventListener("click", async () => {
        const selected = state.selectedEntry;
        if (selected?.kind !== "file") return;
        try {
            const tree = await api(`/tekne-chat/api/groups/${encodeURIComponent(state.group.id)}/folder-tree`);
            const target = $("tc-move-target"); target.replaceChildren(new Option("ルート", ""));
            for (const folder of tree.folders) target.add(new Option(folder.path, folder.id));
            target.value = selected.item.folder_id || ""; state.movingFileId = selected.item.id; $("tc-move-dialog").showModal();
        } catch (error) { showError(error); }
    });
    $("tc-selected-delete").addEventListener("click", async () => {
        const selected = state.selectedEntry;
        if (!selected) return;
        const isFolder = selected.kind === "folder";
        const name = selected.item.name || selected.item.display_name;
        const promptText = isFolder ? `「${name}」と中のファイルを削除しますか？` : `「${name}」を削除しますか？`;
        if (!confirm(promptText)) return;
        try {
            const path = isFolder ? `/tekne-chat/api/folders/${selected.item.id}` : `/tekne-chat/api/files/${selected.item.id}`;
            await api(path, { method: "DELETE", body: form() });
            setSelectedEntry(null, null);
            if (isFolder) await loadFolderTree();
            await loadFiles();
        } catch (error) { showError(error); }
    });
    $("tc-move-form").addEventListener("submit", async (event) => {
        event.preventDefault(); if (!state.movingFileId) return;
        try { await api(`/tekne-chat/api/files/${state.movingFileId}/move`, { method: "POST", body: form({ folder_id: $("tc-move-target").value }) }); $("tc-move-dialog").close(); state.movingFileId = null; await loadFiles(); }
        catch (error) { showError(error); }
    });
    // メッセージ画面にファイルをドロップしたら、保存先フォルダを選んで保存する
    const conversation = $("tc-conversation");
    let dragDepth = 0;
    const hasFiles = (event) => [...(event.dataTransfer?.types || [])].includes("Files");
    function showDropOverlay(show) { $("tc-drop-overlay").hidden = !show; }
    conversation.addEventListener("dragenter", (event) => {
        if (!hasFiles(event) || !state.group) return;
        event.preventDefault(); dragDepth += 1; showDropOverlay(true);
    });
    conversation.addEventListener("dragover", (event) => {
        if (!hasFiles(event) || !state.group) return;
        event.preventDefault(); event.dataTransfer.dropEffect = "copy";
    });
    conversation.addEventListener("dragleave", (event) => {
        if (!hasFiles(event)) return;
        dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) showDropOverlay(false);
    });
    conversation.addEventListener("drop", (event) => {
        if (!hasFiles(event)) return;
        event.preventDefault(); dragDepth = 0; showDropOverlay(false);
        if (!state.group) return;
        // フォルダそのものは保存できないので除く（items と files の並びは一致しないことがあるので items から作る）
        const fileItems = [...(event.dataTransfer.items || [])].filter((item) => item.kind === "file");
        let folders = 0;
        let files = [];
        if (fileItems.length) {
            for (const item of fileItems) {
                if (item.webkitGetAsEntry?.()?.isDirectory) { folders += 1; continue; }
                const file = item.getAsFile(); if (file) files.push(file);
            }
        } else files = [...event.dataTransfer.files];
        if (folders) showError(new Error("フォルダはそのまま保存できません。中のファイルを選んでドロップしてください。"));
        if (files.length) openDropDialog(files).catch(showError);
    });
    function formatDropSize(bytes) { return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)}MB` : `${Math.max(1, Math.round(bytes / 1024))}KB`; }
    async function openDropDialog(files) {
        state.droppedFiles = files;
        const list = $("tc-drop-files"); list.replaceChildren();
        for (const file of files) {
            const item = document.createElement("li");
            const name = document.createElement("span"); name.textContent = file.name;
            const size = document.createElement("span"); size.textContent = formatDropSize(file.size);
            item.append(name, size); list.append(item);
        }
        $("tc-drop-space").textContent = `「${state.group.name}」`;
        $("tc-drop-status").textContent = "";
        $("tc-drop-save").disabled = false;
        const tree = await api(`/tekne-chat/api/groups/${encodeURIComponent(state.group.id)}/folder-tree`);
        state.dropFolders = tree.folders;
        // 親子関係が一目で分かるように、最初はすべて開いておく
        state.dropExpanded = new Set(tree.folders.map((folder) => String(folder.id)));
        // フォルダ画面で開いているフォルダがあれば、そこを最初に選んでおく
        state.dropTarget = tree.folders.some((folder) => String(folder.id) === String(state.folderId)) ? String(state.folderId) : "";
        renderDropTree();
        $("tc-drop-dialog").showModal();
    }
    // 保存先フォルダを、サイドのフォルダ画面と同じ木の形で選ぶ
    function renderDropTree() {
        const tree = $("tc-drop-tree"); tree.replaceChildren();
        const folders = state.dropFolders || [];
        const childrenByParent = new Map();
        for (const folder of folders) {
            const key = folder.parent_id ? String(folder.parent_id) : "";
            if (!childrenByParent.has(key)) childrenByParent.set(key, []);
            childrenByParent.get(key).push(folder);
        }
        const choose = (id) => { state.dropTarget = id; renderDropTree(); };
        const root = document.createElement("button");
        root.type = "button"; root.className = "tc-tree-item tc-tree-root"; root.setAttribute("role", "treeitem");
        root.setAttribute("aria-selected", state.dropTarget ? "false" : "true"); root.textContent = "📁 ルート";
        root.addEventListener("click", () => choose("")); tree.append(root);
        function appendChildren(parent, parentId = "", depth = 0) {
            for (const folder of childrenByParent.get(parentId) || []) {
                const id = String(folder.id);
                const children = childrenByParent.get(id) || [];
                const open = state.dropExpanded.has(id);
                const row = document.createElement("div"); row.className = "tc-tree-row"; row.style.setProperty?.("--tc-tree-depth", depth);
                const toggle = document.createElement("button"); toggle.type = "button"; toggle.className = "tc-tree-toggle";
                toggle.textContent = children.length ? (open ? "▾" : "▸") : ""; toggle.disabled = !children.length; toggle.tabIndex = -1;
                toggle.setAttribute("aria-label", `${folder.name}を${open ? "折りたたむ" : "展開"}`);
                toggle.addEventListener("click", () => { if (open) state.dropExpanded.delete(id); else state.dropExpanded.add(id); renderDropTree(); });
                const select = document.createElement("button"); select.type = "button"; select.className = "tc-tree-item";
                select.setAttribute("role", "treeitem"); select.setAttribute("aria-selected", state.dropTarget === id ? "true" : "false");
                if (children.length) select.setAttribute("aria-expanded", String(open));
                select.textContent = `📁 ${folder.name}`; select.addEventListener("click", () => choose(id));
                row.append(toggle, select); parent.append(row);
                if (children.length) {
                    const group = document.createElement("div"); group.setAttribute("role", "group"); group.hidden = !open;
                    appendChildren(group, id, depth + 1); parent.append(group);
                }
            }
        }
        appendChildren(tree);
        const chosen = folders.find((folder) => String(folder.id) === state.dropTarget);
        $("tc-drop-selected").textContent = chosen ? `ルート/${chosen.path}` : "ルート";
    }
    $("tc-drop-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const files = state.droppedFiles || []; if (!files.length || !state.group) return;
        const groupId = state.group.id, folderId = state.dropTarget || "";
        $("tc-drop-save").disabled = true; $("tc-drop-cancel").disabled = true;
        const failed = [];
        for (const [index, file] of files.entries()) {
            $("tc-drop-status").textContent = `保存中… (${index + 1}/${files.length}) ${file.name}`;
            const data = form({ folder_id: folderId }); data.set("file", file);
            try {
                const result = await api(`/tekne-chat/api/groups/${encodeURIComponent(groupId)}/files`, { method: "POST", body: data });
                if (state.group?.id === groupId) addMessage(result.message, { scroll: true });
            } catch (error) { failed.push(`${file.name}：${error.message}`); }
        }
        $("tc-drop-cancel").disabled = false;
        if (failed.length) {
            $("tc-drop-status").textContent = `保存できなかったファイルがあります。${failed.join(" ／ ")}`;
            $("tc-drop-save").disabled = true;
        } else {
            $("tc-drop-dialog").close(); clearError();
        }
        state.droppedFiles = [];
        scheduleGroupRefresh();
        if (!sidePanel.hidden && state.panel === "files") await loadFiles().catch(showError);
    });
    $("tc-drop-dialog").addEventListener("close", () => { state.droppedFiles = []; });
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
        try { await api(`/tekne-chat/api/groups/${state.group.id}/members/me`, { method: "DELETE", body: form() }); state.socket?.close(); state.group = null; setSelectedEntry(null, null); messagesEl.replaceChildren(); setEnabled(false); $("tc-current-name").textContent = "グループを選択"; await refreshGroups(); }
        catch (error) { showError(error); }
    });
    $("tc-new-folder").addEventListener("click", () => {
        if (!state.group) return;
        $("tc-new-folder-name").value = "";
        $("tc-folder-create-dialog").showModal();
        $("tc-new-folder-name").focus();
    });
    $("tc-folder-create-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const name = $("tc-new-folder-name").value.trim(); if (!name || !state.group) return;
        try {
            await api(`/tekne-chat/api/groups/${state.group.id}/folders`, { method: "POST", body: form({ name, parent_id: state.folderId || "" }) });
            $("tc-folder-create-dialog").close();
            if (state.folderId) state.expandedFolders.add(String(state.folderId));
            await loadFolderTree(); await loadFiles();
        }
        catch (error) { showError(error); }
    });
    $("tc-up-folder").addEventListener("click", async () => {
        if (!state.folderId || !state.group) return;
        try { const data = await api(`/tekne-chat/api/groups/${state.group.id}/folders?folder_id=${encodeURIComponent(state.folderId)}`); await selectFolder(data.current_parent_id || null); }
        catch (error) { showError(error); }
    });
    $("tc-file-search").addEventListener("input", () => { clearTimeout(state.searchTimer); state.searchTimer = setTimeout(() => loadFiles().catch(showError), 220); });
    $("tc-upload-file").addEventListener("change", async (event) => {
        const file = event.target.files[0]; if (!file || !state.group) return;
        const data = form({ folder_id: state.folderId || "" }); data.set("file", file);
        try { await api(`/tekne-chat/api/groups/${state.group.id}/files`, { method: "POST", body: data }); clearError(); await loadFiles(); scheduleGroupRefresh(); }
        catch (error) { showError(error); }
        finally { event.target.value = ""; }
    });

    // 他のグループでの更新（レポート記入など）も一覧の並び順に反映する
    window.addEventListener("focus", scheduleGroupRefresh);
    setInterval(() => { if (!document.hidden) scheduleGroupRefresh(); }, 60000);
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
