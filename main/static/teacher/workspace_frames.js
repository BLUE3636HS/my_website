(() => {
    // ワークスペースの枠：所属ページ・枠を追加ページ・枠の一覧ページで共通に使う
    const root = document.getElementById("workspace-frames");
    if (!root) return;
    const csrf = root.dataset.csrf;
    const message = document.getElementById("frame-message");

    function showMessage(text, isError) {
        message.textContent = text;
        message.hidden = !text;
        message.classList.toggle("is-error", !!isError);
    }

    async function post(path, body) {
        const response = await fetch(path, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...(body || {}), csrf_token: csrf }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            const detail = Array.isArray(data.detail) ? "入力内容を確認してください。" : data.detail;
            throw new Error(detail || "処理に失敗しました。");
        }
        return data;
    }

    async function run(control, task) {
        if (control) control.disabled = true;
        try { await task(); }
        catch (error) { showMessage(error.message, true); }
        finally { if (control) control.disabled = false; }
    }

    const fields = (form) => ({ name: form.elements.name.value, description: form.elements.description.value });

    // 枠を追加：追加したら一覧へ移る
    document.getElementById("frame-create")?.addEventListener("submit", (event) => {
        event.preventDefault();
        const form = event.currentTarget;
        run(form.querySelector("button[type=submit]"), async () => {
            await post("/teacher/api/workspace-frames", fields(form));
            location.href = "/teacher/workspace-frames/list";
        });
    });

    // 枠の一覧：「編集」で行をフォームに切り替える
    document.querySelectorAll(".frame-row").forEach((row) => {
        const view = row.querySelector(".frame-view");
        const form = row.querySelector(".frame-edit");
        const toggle = (editing) => {
            view.hidden = editing;
            form.hidden = !editing;
            if (editing) form.elements.name.focus();
            else form.reset();
        };
        row.querySelector(".frame-edit-open").addEventListener("click", () => toggle(true));
        row.querySelector(".frame-edit-cancel").addEventListener("click", () => toggle(false));
        form.addEventListener("submit", (event) => {
            event.preventDefault();
            run(form.querySelector("button[type=submit]"), async () => {
                await post(`/teacher/api/workspace-frames/${row.dataset.id}`, fields(form));
                location.reload();
            });
        });
        const remove = row.querySelector(".frame-delete");
        remove.addEventListener("click", () => {
            const count = Number(row.dataset.count || 0);
            const text = count
                ? `枠「${row.dataset.name}」を削除します。所属している ${count} 個のワークスペースは「枠なし」に戻ります（ワークスペースやレポートは消えません）。よろしいですか？`
                : `枠「${row.dataset.name}」を削除します。よろしいですか？`;
            if (!confirm(text)) return;
            run(remove, async () => {
                await post(`/teacher/api/workspace-frames/${row.dataset.id}/delete`);
                location.reload();
            });
        });
    });

    // ワークスペースの所属：選ぶとすぐに保存する
    document.querySelectorAll(".workspace-frame-select").forEach((select) => {
        let current = select.value;
        select.addEventListener("change", () => run(select, async () => {
            try {
                const result = await post(`/teacher/api/workspaces/${encodeURIComponent(select.dataset.group)}/frame`, { frame_id: select.value || null });
                current = select.value;
                showMessage(`枠を「${result.frame_name || "枠なし"}」に変更しました。`, false);
            } catch (error) {
                select.value = current;
                throw error;
            }
        }));
    });
})();

// ワークスペースの所属：メンバー名が欄に入りきらないときだけ、横に流して全員分を見せる
(() => {
    const items = [...document.querySelectorAll(".ws-members")];
    if (!items.length) return;
    function measure() {
        for (const box of items) {
            const track = box.querySelector(".ws-members-track");
            box.classList.remove("is-overflowing");
            const overflow = track.scrollWidth - box.clientWidth;
            if (overflow > 2) {
                box.classList.add("is-overflowing");
                const distance = track.scrollWidth - box.clientWidth;  // 左右の余白を付けた後の幅で測り直す
                box.style.setProperty("--ws-distance", `${-distance}px`);
                box.style.setProperty("--ws-duration", `${Math.max(4, distance / 30 + 3).toFixed(1)}s`);
            }
        }
    }
    let timer = null;
    window.addEventListener("resize", () => { clearTimeout(timer); timer = setTimeout(measure, 150); });
    if (document.fonts?.ready) document.fonts.ready.then(measure); else measure();
    measure();
})();
