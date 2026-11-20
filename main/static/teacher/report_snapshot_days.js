(() => {
    const root = document.getElementById("snapshot-days");
    if (!root) return;
    const today = root.dataset.today;
    const csrf = root.dataset.csrf;
    const frame = root.dataset.frame || "none";
    const grid = document.getElementById("snapshot-grid");
    const monthLabel = document.getElementById("snapshot-month");
    const list = document.getElementById("snapshot-list");
    const message = document.getElementById("snapshot-message");
    const saveButton = document.getElementById("snapshot-save");
    const weekdays = ["日", "月", "火", "水", "木", "金", "土"];

    let saved = JSON.parse(document.getElementById("snapshot-initial").textContent || "[]");
    let selected = new Set();
    // 提出日（明日以降）：日付 → 名前
    let submissions = new Map();
    // 提出日（明日以降）の提出形式：日付 → template / pdf / either
    let formats = new Map();
    const FORMAT_LABELS = { template: "テンプレートで提出", pdf: "PDFで提出", either: "どちらでも提出可" };
    let dirty = false;
    const [ty, tm] = today.split("-").map(Number);
    let viewYear = ty;
    let viewMonth = tm - 1;

    function loadSaved(days) {
        saved = days;
        selected = new Set(days.filter((item) => item.day > today).map((item) => item.day));
        submissions = new Map(days.filter((item) => item.day > today && item.submission).map((item) => [item.day, item.title || ""]));
        formats = new Map(days.filter((item) => item.day > today && item.submission).map((item) => [item.day, item.format || "template"]));
        dirty = false;
    }

    function pad(value) { return String(value).padStart(2, "0"); }
    function iso(year, month, day) { return `${year}-${pad(month + 1)}-${pad(day)}`; }
    function lockedDays() { return new Map(saved.filter((item) => item.day <= today).map((item) => [item.day, item])); }
    function label(day) {
        const [y, m, d] = day.split("-").map(Number);
        return `${y}年${m}月${d}日（${weekdays[new Date(y, m - 1, d).getDay()]}）`;
    }
    function showMessage(text, isError) {
        message.textContent = text;
        message.hidden = !text;
        message.classList.toggle("is-error", !!isError);
    }

    function renderCalendar() {
        monthLabel.textContent = `${viewYear}年${viewMonth + 1}月`;
        grid.replaceChildren();
        const first = new Date(viewYear, viewMonth, 1).getDay();
        const count = new Date(viewYear, viewMonth + 1, 0).getDate();
        const locked = lockedDays();
        for (let i = 0; i < first; i += 1) {
            const blank = document.createElement("span");
            blank.className = "snapshot-day is-blank";
            grid.append(blank);
        }
        for (let d = 1; d <= count; d += 1) {
            const day = iso(viewYear, viewMonth, d);
            const button = document.createElement("button");
            button.type = "button";
            button.className = "snapshot-day";
            button.textContent = d;
            const past = day <= today;
            const lockedItem = locked.get(day);
            if (day === today) button.classList.add("is-today");
            if (past) {
                button.disabled = true;
                if (lockedItem) button.classList.add(lockedItem.processed ? "is-done" : "is-selected");
                if (lockedItem?.submission) button.classList.add("is-submission");
            } else if (selected.has(day)) {
                button.classList.add("is-selected");
                if (submissions.has(day)) button.classList.add("is-submission");
            }
            const isSubmission = lockedItem ? lockedItem.submission : submissions.has(day);
            const state = (lockedItem ? (lockedItem.processed ? "自動保存済み" : "自動保存日") : selected.has(day) ? "自動保存日" : "") + (isSubmission ? "・提出日" : "");
            if (isSubmission) {
                const mark = document.createElement("small");
                mark.className = "snapshot-day-mark";
                mark.textContent = "提出";
                button.append(mark);
            }
            button.setAttribute("aria-label", `${label(day)}${state ? `、${state}` : ""}`);
            if (!past) {
                // クリックするたびに「なし → 自動保存日 → 提出日 → なし」と切り替わる
                button.addEventListener("click", () => {
                    let focusTitle = false;
                    if (!selected.has(day)) selected.add(day);
                    else if (!submissions.has(day)) { submissions.set(day, ""); formats.set(day, "template"); focusTitle = true; }
                    else { selected.delete(day); submissions.delete(day); formats.delete(day); }
                    dirty = true;
                    showMessage("未保存の変更があります。", false);
                    render();
                    if (focusTitle) list.querySelector(`input[data-day="${day}"]`)?.focus();
                });
            }
            grid.append(button);
        }
    }

    function renderList() {
        list.replaceChildren();
        const locked = lockedDays();
        const days = [...new Set([...locked.keys(), ...selected])].sort();
        if (!days.length) {
            const li = document.createElement("li");
            li.textContent = "指定中の日付はありません。";
            list.append(li);
            return;
        }
        for (const day of days) {
            const li = document.createElement("li");
            const item = locked.get(day);
            const text = document.createElement("span");
            text.className = "snapshot-list-day";
            text.textContent = `${label(day)} ${item ? (item.processed ? "自動保存済み" : "自動保存待ち") : "自動保存日（予定）"}`;
            li.append(text);
            if (item) {
                if (item.submission) {
                    const badge = document.createElement("span");
                    badge.className = "snapshot-submission-badge";
                    badge.textContent = `提出日：${item.title}（${FORMAT_LABELS[item.format] || FORMAT_LABELS.template}）`;
                    li.append(badge);
                }
            } else if (submissions.has(day)) {
                // 提出日の名前はここで入力する（提出日にするかどうかはカレンダーで切り替える）
                const badge = document.createElement("span");
                badge.className = "snapshot-submission-badge";
                badge.textContent = "提出日";
                const input = document.createElement("input");
                input.type = "text";
                input.className = "snapshot-submission-title";
                input.dataset.day = day;
                input.maxLength = 50;
                input.placeholder = "提出日の名前（例：中間成果レポート）";
                input.setAttribute("aria-label", `${label(day)}の提出日の名前`);
                input.value = submissions.get(day);
                input.addEventListener("input", () => {
                    submissions.set(day, input.value);
                    dirty = true;
                    showMessage("未保存の変更があります。", false);
                });
                // 提出形式：先生が、テンプレートで提出させるか、PDFで提出させるか、どちらでもよいかを選ぶ
                const select = document.createElement("select");
                select.className = "snapshot-submission-format";
                select.setAttribute("aria-label", `${label(day)}の提出形式`);
                for (const [value, text] of Object.entries(FORMAT_LABELS)) {
                    const option = document.createElement("option");
                    option.value = value;
                    option.textContent = text;
                    select.append(option);
                }
                select.value = formats.get(day) || "template";
                select.addEventListener("change", () => {
                    formats.set(day, select.value);
                    dirty = true;
                    showMessage("未保存の変更があります。", false);
                });
                li.append(badge, input, select);
            }
            list.append(li);
        }
    }

    function render() { renderCalendar(); renderList(); }

    document.getElementById("snapshot-prev").addEventListener("click", () => {
        viewMonth -= 1;
        if (viewMonth < 0) { viewMonth = 11; viewYear -= 1; }
        renderCalendar();
    });
    document.getElementById("snapshot-next").addEventListener("click", () => {
        viewMonth += 1;
        if (viewMonth > 11) { viewMonth = 0; viewYear += 1; }
        renderCalendar();
    });

    saveButton.addEventListener("click", async () => {
        const missing = [...submissions].filter(([, title]) => !title.trim()).map(([day]) => label(day));
        if (missing.length) {
            showMessage(`提出日の名前を入力してください：${missing.join("、")}`, true);
            return;
        }
        saveButton.disabled = true;
        try {
            const response = await fetch("/teacher/school-report/snapshot-days", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    csrf_token: csrf,
                    days: [...selected].sort(),
                    submissions: Object.fromEntries([...submissions].map(([day, title]) => [day, title.trim()])),
                    formats: Object.fromEntries([...submissions.keys()].map((day) => [day, formats.get(day) || "template"])),
                    frame,
                }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                const detail = Array.isArray(data.detail) ? "入力内容を確認してください。" : data.detail;
                throw new Error(detail || "保存できませんでした。");
            }
            // 保存したら、最初の「設定する枠」だけの表示に戻る
            dirty = false;
            location.href = `/teacher/school-report/snapshot-days?saved=${encodeURIComponent(frame)}`;
            return;
        } catch (error) {
            showMessage(error.message, true);
        } finally {
            saveButton.disabled = false;
        }
    });

    // 他の枠から、明日以降の自動保存日・提出日（名前も）をコピーする（保存は「設定を保存」で行う）
    const frames = JSON.parse(document.getElementById("snapshot-frames")?.textContent || "[]");
    document.getElementById("snapshot-copy")?.addEventListener("click", () => {
        const source = frames.find((item) => item.value === document.getElementById("snapshot-copy-source").value);
        if (!source) return;
        const future = source.days.filter((item) => item.day > today);
        if (!future.length) {
            showMessage(`「${source.label}」には明日以降の日付がありません。`, true);
            return;
        }
        if (selected.size && !confirm(`今の明日以降の日付を、「${source.label}」の日付（${future.length}日）に置き換えます。よろしいですか？`)) return;
        selected = new Set(future.map((item) => item.day));
        submissions = new Map(future.filter((item) => item.submission).map((item) => [item.day, item.title || ""]));
        formats = new Map(future.filter((item) => item.submission).map((item) => [item.day, item.format || "template"]));
        dirty = true;
        render();
        showMessage(`「${source.label}」の日付をコピーしました。「設定を保存」を押すと保存されます。`, false);
    });

    window.addEventListener("beforeunload", (event) => {
        if (dirty) event.preventDefault();
    });

    loadSaved(saved);
    render();
})();
