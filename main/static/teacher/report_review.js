(() => {
    const root = document.getElementById("report-review");
    if (!root) return;
    const id = root.dataset.id;
    const csrf = root.dataset.csrf;
    const editable = root.dataset.editable === "1";
    const message = document.getElementById("review-message");
    let dirty = false;

    function showMessage(text, isError) {
        message.textContent = text;
        message.hidden = !text;
        message.classList.toggle("is-error", !!isError);
    }

    async function post(action, body) {
        const response = await fetch(`/teacher/api/report-reviews/${id}/${action}`, {
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

    function comments() {
        const fieldComments = {};
        document.querySelectorAll(".review-field-comment").forEach((input) => {
            fieldComments[input.dataset.key] = input.value;
        });
        return { overall_comment: document.getElementById("review-overall").value, field_comments: fieldComments };
    }

    function reload() {
        dirty = false;
        location.reload();
    }

    function bind(idName, handler) {
        const button = document.getElementById(idName);
        if (!button) return;
        button.addEventListener("click", async () => {
            button.disabled = true;
            try {
                await handler(button);
            } catch (error) {
                showMessage(error.message, true);
            } finally {
                button.disabled = false;
            }
        });
    }

    bind("review-start", async () => {
        await post("lock");
        reload();
    });

    bind("review-force", async (button) => {
        const holder = button.dataset.holder;
        if (!confirm(`${holder} 先生のロックを外します。${holder} 先生がまだ保存していない内容は失われる可能性があります。よろしいですか？`)) return;
        await post("force-unlock");
        reload();
    });

    bind("review-save", async () => {
        const data = await post("save", comments());
        dirty = false;
        document.getElementById("review-editors").textContent = (data.editors || []).join("、") || "まだいません";
        showMessage("保存しました。", false);
    });

    bind("review-finish", async () => {
        await post("save", { ...comments(), finish: true });
        reload();
    });

    bind("review-return", async () => {
        const result = document.querySelector("input[name='review-result']:checked")?.value || "returned";
        const dueDay = document.getElementById("review-due").value;
        if (result === "resubmit" && !dueDay) throw new Error("再提出期限を選んでください。");
        const text = result === "resubmit"
            ? `「要再提出」で返却します（再提出期限：${dueDay} 23:59）。返却後はレビューを変更できません。よろしいですか？`
            : "返却します。返却後はレビューを変更できません。よろしいですか？";
        if (!confirm(text)) return;
        await post("return", { ...comments(), result, due_day: result === "resubmit" ? dueDay : null });
        reload();
    });

    if (editable) {
        document.querySelectorAll(".review-field-comment, #review-overall").forEach((input) => {
            input.addEventListener("input", () => {
                dirty = true;
                showMessage("未保存の変更があります。", false);
            });
        });
        window.addEventListener("beforeunload", (event) => {
            if (dirty) event.preventDefault();
        });
    }
})();

// レビューへの添付ファイル（追加・削除）。追加するときは、書きかけのコメントを先に保存してから、ページを読み込み直す
(() => {
    const root = document.getElementById("report-review");
    if (!root) return;
    const id = root.dataset.id;
    const csrf = root.dataset.csrf;
    const message = document.getElementById("review-message");
    function showMessage(text, isError) {
        if (!message) return;
        message.textContent = text;
        message.hidden = !text;
        message.classList.toggle("is-error", !!isError);
    }
    async function request(url, options) {
        const response = await fetch(url, options);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            const detail = Array.isArray(data.detail) ? "入力内容を確認してください。" : data.detail;
            throw new Error(detail || "処理に失敗しました。");
        }
        return data;
    }
    function saveComments() {
        const fieldComments = {};
        document.querySelectorAll(".review-field-comment").forEach((input) => { fieldComments[input.dataset.key] = input.value; });
        return request(`/teacher/api/report-reviews/${id}/save`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ csrf_token: csrf, overall_comment: document.getElementById("review-overall").value, field_comments: fieldComments }),
        });
    }
    document.getElementById("review-attachment-file")?.addEventListener("change", async (event) => {
        const input = event.target;
        const file = input.files[0];
        if (!file) return;
        try {
            showMessage("添付しています…", false);
            await saveComments();
            const body = new FormData();
            body.set("csrf_token", csrf);
            body.set("file", file);
            await request(`/teacher/api/report-reviews/${id}/attachments`, { method: "POST", body });
            location.reload();
        } catch (error) {
            showMessage(error.message, true);
            input.value = "";
        }
    });
    document.querySelectorAll(".review-attachment-delete").forEach((button) => button.addEventListener("click", async () => {
        if (!confirm("この添付ファイルを削除しますか？")) return;
        button.disabled = true;
        try {
            await saveComments();
            await request(`/teacher/api/report-reviews/${id}/attachments/${button.dataset.id}/delete`, {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ csrf_token: csrf }),
            });
            location.reload();
        } catch (error) {
            showMessage(error.message, true);
            button.disabled = false;
        }
    }));
})();
