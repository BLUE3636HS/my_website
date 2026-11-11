(() => {
    const root = document.getElementById("snapshot-days");
    if (!root) return;
    const today = root.dataset.today;
    const csrf = root.dataset.csrf;
    const grid = document.getElementById("snapshot-grid");
    const monthLabel = document.getElementById("snapshot-month");
    const list = document.getElementById("snapshot-list");
    const message = document.getElementById("snapshot-message");
    const saveButton = document.getElementById("snapshot-save");
    const weekdays = ["日", "月", "火", "水", "木", "金", "土"];

    let saved = JSON.parse(document.getElementById("snapshot-initial").textContent || "[]");
    let selected = new Set();
    let dirty = false;
    const [ty, tm] = today.split("-").map(Number);
    let viewYear = ty;
    let viewMonth = tm - 1;

    function loadSaved(days) {
        saved = days;
        selected = new Set(days.filter((item) => item.day > today).map((item) => item.day));
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
            } else if (selected.has(day)) {
                button.classList.add("is-selected");
            }
            const state = lockedItem ? (lockedItem.processed ? "自動保存済み" : "自動保存日") : selected.has(day) ? "自動保存日" : "";
            button.setAttribute("aria-label", `${label(day)}${state ? `、${state}` : ""}`);
            button.setAttribute("aria-pressed", !past && selected.has(day) ? "true" : "false");
            if (!past) {
                button.addEventListener("click", () => {
                    if (selected.has(day)) selected.delete(day);
                    else selected.add(day);
                    dirty = true;
                    showMessage("未保存の変更があります。", false);
                    render();
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
            li.textContent = `${label(day)} ${item ? (item.processed ? "自動保存済み" : "自動保存待ち") : "予定"}`;
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
        saveButton.disabled = true;
        try {
            const response = await fetch("/teacher/school-report/snapshot-days", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ csrf_token: csrf, days: [...selected].sort() }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                const detail = Array.isArray(data.detail) ? "入力内容を確認してください。" : data.detail;
                throw new Error(detail || "保存できませんでした。");
            }
            loadSaved(data.days || []);
            render();
            showMessage("自動保存日を保存しました。", false);
        } catch (error) {
            showMessage(error.message, true);
        } finally {
            saveButton.disabled = false;
        }
    });

    window.addEventListener("beforeunload", (event) => {
        if (dirty) event.preventDefault();
    });

    loadSaved(saved);
    render();
})();
