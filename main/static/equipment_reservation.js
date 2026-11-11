(() => {
    const page = document.querySelector(".equipment-reservation");
    const form = document.querySelector("#equipment-reservation-form");
    const message = document.querySelector("#equipment-message");
    const equipmentFields = document.querySelector("#step-equipment");
    const takeoutFields = document.querySelector("#takeout-fields");
    const roomFields = document.querySelector("#room-fields");
    const purposeFields = document.querySelector("#purpose-fields");
    const submitButton = document.querySelector("#equipment-submit");
    const startDay = document.querySelector("#start_day");
    const endDay = document.querySelector("#end_day");
    const useDay = document.querySelector("#use_day");
    const takeoutItems = document.querySelector("#takeout-items");
    const roomItems = document.querySelector("#room-items");
    const roomTimeMessage = document.querySelector("#room-time-message");
    const roomTimeSlots = document.querySelector("#room-time-slots");
    const selectedStartLabel = document.querySelector("#room-selected-start");
    const selectedEndLabel = document.querySelector("#room-selected-end");
    const selectionStatus = document.querySelector("#equipment-selection-status");
    const clearButton = document.querySelector("#equipment-clear");
    const modeNote = document.querySelector("#equipment-mode-note");
    const cards = Array.from(document.querySelectorAll(".equipment-card"));
    const USAGE_LABELS = {takeout: "持ち出し", in_room: "工作室内"};
    const state = {
        usageType: "",
        selected: [],          // [{id, name}] in the order the student picked them
        quantities: {},        // equipment id -> chosen quantity (string)
        takeoutAvailable: {},  // equipment id -> available count | Error
        roomItemSlots: {},     // equipment id -> slots from the server
        roomSlots: [],         // combined slots across selected items
        dragging: false, dragStartIndex: null, selectedStart: "", selectedEnd: "",
        takeoutRequest: 0, roomRequest: 0
    };

    function showMessage(text, type = "error") {
        message.textContent = text;
        message.className = `equipment-message is-${type}`;
        message.hidden = false;
    }
    function clearMessage() { message.textContent = ""; message.hidden = true; }
    function setQuantityOptions(select, available, placeholder, keep = "") {
        select.innerHTML = "";
        const first = document.createElement("option");
        first.value = "";
        first.textContent = placeholder;
        select.append(first);
        for (let value = 1; value <= available; value += 1) {
            const option = document.createElement("option");
            option.value = String(value);
            option.textContent = String(value);
            select.append(option);
        }
        select.disabled = available <= 0;
        if (keep && Number(keep) <= available) select.value = keep;
    }
    function setTakeoutLimits() {
        endDay.min = startDay.value;
        if (!endDay.value || endDay.value < startDay.value) endDay.value = startDay.value;
        const maximum = new Date(`${startDay.value}T00:00:00`);
        maximum.setDate(maximum.getDate() + 6);
        const year = maximum.getFullYear();
        const month = String(maximum.getMonth() + 1).padStart(2, "0");
        const day = String(maximum.getDate()).padStart(2, "0");
        endDay.max = `${year}-${month}-${day}`;
        if (endDay.value > endDay.max) endDay.value = endDay.max;
    }
    async function fetchJson(url, options) {
        const response = await fetch(url, options);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            const detail = Array.isArray(data.detail)
                ? data.detail.map(item => item.msg).filter(Boolean).join("、")
                : data.detail;
            throw new Error(detail || "処理を完了できませんでした。");
        }
        return data;
    }

    // ---------- 選択中の器具リスト（器具ごとの数量） ----------
    function renderItemRows(container, availability) {
        container.innerHTML = "";
        const heading = document.createElement("p");
        heading.className = "selected-equipment-heading";
        heading.textContent = `器具ごとの数量（${state.selected.length}件）`;
        container.append(heading);
        state.selected.forEach(item => {
            const row = document.createElement("div");
            row.className = "selected-equipment-item";
            if (item.image) {
                const image = document.createElement("img");
                image.className = "selected-equipment-image";
                image.src = item.image;
                image.alt = "";
                row.append(image);
            }
            const info = document.createElement("div");
            info.className = "selected-equipment-info";
            const name = document.createElement("span");
            name.className = "selected-equipment-name";
            name.textContent = item.name;
            const status = document.createElement("span");
            status.className = "selected-equipment-status";
            const label = document.createElement("label");
            label.className = "selected-equipment-quantity";
            label.textContent = "数量";
            const select = document.createElement("select");
            select.dataset.equipmentId = item.id;
            select.setAttribute("aria-label", `${item.name}の数量`);
            const value = availability(item.id);
            if (value === undefined) {
                status.textContent = "在庫を確認しています...";
                setQuantityOptions(select, 0, "確認中です");
            } else if (value === null) {
                status.textContent = "時間帯を選択すると利用可能数を表示します。";
                setQuantityOptions(select, 0, "時間帯を選択");
            } else if (value instanceof Error) {
                status.textContent = value.message;
                status.classList.add("is-error");
                setQuantityOptions(select, 0, "選択できません");
            } else {
                status.textContent = value ? `利用可能数: ${value}` : "在庫がありません";
                if (!value) status.classList.add("is-error");
                setQuantityOptions(select, value, value ? "数量を選択" : "在庫なし", state.quantities[item.id] || "");
                // 利用可能数を超えていた数量は選び直してもらう
                state.quantities[item.id] = select.value;
            }
            label.append(select);
            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "selected-equipment-remove";
            remove.dataset.equipmentId = item.id;
            remove.textContent = "外す";
            remove.setAttribute("aria-label", `${item.name}を選択から外す`);
            info.append(name, status);
            row.append(info, label, remove);
            container.append(row);
        });
    }
    function renderTakeoutItems() {
        renderItemRows(takeoutItems, id => state.takeoutAvailable[id]);
    }
    function selectedRoomRange() {
        if (!state.selectedStart || !state.selectedEnd) return [];
        return state.roomSlots.filter(slot => slot.start_time >= state.selectedStart && slot.end_time <= state.selectedEnd);
    }
    function renderRoomItems() {
        const range = selectedRoomRange();
        renderItemRows(roomItems, id => {
            if (!state.roomItemSlots[id]) return undefined;
            if (!range.length) return null;
            return Math.min(...range.map(slot => slot.byItem[id] ?? 0));
        });
    }

    // ---------- 持ち出し ----------
    async function updateTakeoutAvailability() {
        if (state.usageType !== "takeout" || !startDay.value || !endDay.value || !state.selected.length) return;
        const request = ++state.takeoutRequest;
        state.takeoutAvailable = {};
        renderTakeoutItems();
        const results = await Promise.all(state.selected.map(async item => {
            try {
                const params = new URLSearchParams({equipment_id: item.id, start_day: startDay.value, end_day: endDay.value});
                const data = await fetchJson(`/equipment-availability?${params}`);
                return [item.id, data.available];
            } catch (error) { return [item.id, error]; }
        }));
        if (request !== state.takeoutRequest) return;
        state.takeoutAvailable = Object.fromEntries(results);
        renderTakeoutItems();
    }

    // ---------- 工作室内（時間帯は全器具共通） ----------
    function clearRoomSelection() {
        state.dragging = false;
        state.dragStartIndex = null;
        state.selectedStart = "";
        state.selectedEnd = "";
        selectedStartLabel.textContent = "未選択";
        selectedEndLabel.textContent = "未選択";
        roomTimeSlots.querySelectorAll(".room-time-slot").forEach(slot => slot.classList.remove("selected", "drag-start"));
    }
    function updateRoomSelection(startIndex, endIndex) {
        if (startIndex === null || endIndex < startIndex) return false;
        const selected = state.roomSlots.slice(startIndex, endIndex + 1);
        if (!selected.length || selected.some(slot => slot.closed || slot.available_quantity <= 0)) return false;
        roomTimeSlots.querySelectorAll(".room-time-slot").forEach((element, index) => {
            element.classList.toggle("selected", index >= startIndex && index <= endIndex);
            element.classList.toggle("drag-start", index === startIndex);
        });
        state.selectedStart = selected[0].start_time;
        state.selectedEnd = selected[selected.length - 1].end_time;
        selectedStartLabel.textContent = state.selectedStart;
        selectedEndLabel.textContent = state.selectedEnd;
        renderRoomItems();
        return true;
    }
    function addRoomSlotEvents(element, index) {
        element.addEventListener("mousedown", event => {
            const slot = element.closest(".room-time-slot");
            if (event.button !== 0 || slot.classList.contains("unavailable")) return;
            event.preventDefault();
            clearRoomSelection();
            state.dragging = true;
            state.dragStartIndex = index;
            updateRoomSelection(index, index);
            roomTimeMessage.textContent = "終了する枠までドラッグしてください。";
        });
        element.addEventListener("mouseenter", () => {
            if (!state.dragging) return;
            element.closest(".room-time-slot").classList.add("drag-hover");
            updateRoomSelection(state.dragStartIndex, index);
        });
        element.addEventListener("mouseleave", () => {
            element.closest(".room-time-slot").classList.remove("drag-hover");
        });
        element.addEventListener("mouseup", event => {
            if (event.button !== 0 || !state.dragging) return;
            if (updateRoomSelection(state.dragStartIndex, index)) roomTimeMessage.textContent = "選択した利用時間を確認してください。";
            element.closest(".room-time-slot").classList.remove("drag-hover");
            state.dragging = false;
        });
    }
    function slotStatusText(slot) {
        if (slot.closed) return "受付終了";
        if (slot.available_quantity <= 0) return state.selected.length > 1 ? "在庫不足の器具あり" : "在庫なし";
        return state.selected.length > 1 ? "全器具 空きあり" : `残り ${slot.available_quantity}`;
    }
    function renderRoomSlots(slots) {
        roomTimeSlots.innerHTML = "";
        roomTimeSlots.classList.remove("is-disabled");
        slots.forEach((slot, index) => {
            const element = document.createElement("div");
            element.className = "room-time-slot";
            const unavailable = slot.closed || slot.available_quantity <= 0;
            element.classList.add(unavailable ? "unavailable" : "available");
            if (slot.closed) element.classList.add("closed");
            const time = document.createElement("span");
            time.className = "room-time-label";
            time.textContent = slot.start_time;
            const slotBody = document.createElement("div");
            slotBody.className = "room-slot-body";
            const status = document.createElement("span");
            status.className = "room-slot-status";
            status.textContent = slotStatusText(slot);
            slotBody.append(status);
            element.append(time, slotBody);
            addRoomSlotEvents(slotBody, index);
            roomTimeSlots.append(element);
        });
        const endMarker = document.createElement("div");
        endMarker.className = "room-timeline-end";
        const endTime = document.createElement("span");
        endTime.className = "room-time-label";
        endTime.textContent = slots.length ? slots[slots.length - 1].end_time : "20:30";
        const endLine = document.createElement("span");
        endLine.className = "room-timeline-end-line";
        endMarker.append(endTime, endLine);
        roomTimeSlots.append(endMarker);
    }
    function combineRoomSlots() {
        const lists = state.selected.map(item => [item.id, state.roomItemSlots[item.id]]);
        if (!lists.length || lists.some(([, slots]) => !slots)) return [];
        return lists[0][1].map((base, index) => {
            const byItem = Object.fromEntries(lists.map(([id, slots]) => [id, slots[index].available_quantity]));
            return {
                start_time: base.start_time,
                end_time: base.end_time,
                closed: lists.some(([, slots]) => slots[index].closed),
                available_quantity: Math.min(...Object.values(byItem)),
                byItem
            };
        });
    }
    async function updateRoomAvailability({keepSelection = false} = {}) {
        if (state.usageType !== "in_room" || !useDay.value || !state.selected.length) return;
        const previous = keepSelection ? [state.selectedStart, state.selectedEnd] : ["", ""];
        const request = ++state.roomRequest;
        clearRoomSelection();
        state.roomItemSlots = {};
        state.roomSlots = [];
        renderRoomItems();
        roomTimeSlots.classList.add("is-disabled");
        roomTimeSlots.innerHTML = '<p class="status-loading">空き状況を読み込んでいます...</p>';
        try {
            const results = await Promise.all(state.selected.map(async item => {
                const params = new URLSearchParams({equipment_id: item.id, day: useDay.value});
                const data = await fetchJson(`/equipment-room-availability?${params}`);
                return [item.id, data.slots];
            }));
            if (request !== state.roomRequest) return;
            state.roomItemSlots = Object.fromEntries(results);
            state.roomSlots = combineRoomSlots();
            renderRoomSlots(state.roomSlots);
            roomTimeMessage.textContent = state.selected.length > 1
                ? "開始枠から終了枠までドラッグしてください。選択した器具すべてに空きがある枠だけ選べます。"
                : "開始枠から終了枠までドラッグしてください。";
            if (previous[0]) {
                const startIndex = state.roomSlots.findIndex(slot => slot.start_time === previous[0]);
                const endIndex = state.roomSlots.findIndex(slot => slot.end_time === previous[1]);
                if (startIndex >= 0 && endIndex >= 0 && updateRoomSelection(startIndex, endIndex)) {
                    roomTimeMessage.textContent = "選択した利用時間を確認してください。";
                } else {
                    roomTimeMessage.textContent = "選択していた時間帯に空きのない器具があるため、時間帯を選び直してください。";
                }
            }
            renderRoomItems();
        } catch (error) {
            if (request !== state.roomRequest) return;
            state.roomSlots = [];
            roomTimeSlots.innerHTML = "";
            roomTimeMessage.textContent = error.message;
        }
    }

    // ---------- 器具の選択 ----------
    function selectedPlace() {
        const radio = form.querySelector('input[name="place"]:checked');
        return radio ? radio.value : "";
    }
    // 工作室内: すべての器具 / 持ち出し: 「持ち出し可能」の器具だけ
    function isSelectable(card, place) {
        return place === "in_room" || (place === "takeout" && card.dataset.usageType === "takeout");
    }
    function refreshCards() {
        const place = state.usageType;
        cards.forEach(card => {
            const input = card.querySelector("input");
            const locked = !isSelectable(card, place);
            input.disabled = locked;
            // 選んだ場所で使えない器具（持ち出し時の「工作室内のみ」）はカードごと表示しない
            card.hidden = locked;
        });
        const count = state.selected.length;
        selectionStatus.textContent = count
            ? `${USAGE_LABELS[place]}で使う器具を${count}件選択中`
            : "器具が選択されていません";
        clearButton.hidden = !count;
        modeNote.hidden = place !== "takeout";
    }
    let pickOrder = [];  // equipment ids in the order the student checked them
    function onSelectionChanged() {
        clearMessage();
        const place = selectedPlace();
        // 選んだ場所で使えない器具のチェックは外す（場所の切り替え・戻る操作での復元など）
        cards.forEach(card => {
            if (!isSelectable(card, place)) card.querySelector("input").checked = false;
        });
        const checked = cards.filter(card => card.querySelector("input").checked);
        const checkedIds = checked.map(card => card.dataset.equipmentId);
        pickOrder = pickOrder.filter(id => checkedIds.includes(id));
        checkedIds.forEach(id => { if (!pickOrder.includes(id)) pickOrder.push(id); });
        state.selected = pickOrder.map(id => {
            const card = checked.find(item => item.dataset.equipmentId === id);
            return {id, name: card.dataset.equipmentName, usageType: card.dataset.usageType, image: card.querySelector("img")?.getAttribute("src") || ""};
        });
        const placeChanged = state.usageType !== place;
        state.usageType = place;
        Object.keys(state.quantities).forEach(id => {
            if (!state.selected.some(item => item.id === id)) delete state.quantities[id];
        });
        refreshCards();
        const hasItems = state.selected.length > 0;
        const takeout = place === "takeout" && hasItems;
        const room = place === "in_room" && hasItems;
        equipmentFields.hidden = !place;
        takeoutFields.hidden = !takeout;
        roomFields.hidden = !room;
        purposeFields.hidden = !hasItems;
        submitButton.hidden = !hasItems;
        takeoutFields.querySelectorAll("input").forEach(control => { control.disabled = !takeout; });
        roomFields.querySelectorAll("input").forEach(control => { control.disabled = !room; });
        if (placeChanged) clearRoomSelection();
        if (takeout) { setTakeoutLimits(); updateTakeoutAvailability(); }
        if (room) updateRoomAvailability({keepSelection: !placeChanged});
        if (!hasItems) {
            takeoutItems.innerHTML = "";
            roomItems.innerHTML = "";
            clearRoomSelection();
        }
    }
    function uncheck(id) {
        const card = cards.find(item => item.dataset.equipmentId === id);
        if (card) card.querySelector("input").checked = false;
        onSelectionChanged();
    }

    form.addEventListener("change", event => {
        if (event.target.name === "equipment_id" || event.target.name === "place") onSelectionChanged();
        if (event.target.matches("select[data-equipment-id]")) state.quantities[event.target.dataset.equipmentId] = event.target.value;
    });
    [takeoutItems, roomItems].forEach(container => container.addEventListener("click", event => {
        const button = event.target.closest(".selected-equipment-remove");
        if (button) uncheck(button.dataset.equipmentId);
    }));
    clearButton.addEventListener("click", () => {
        cards.forEach(card => { card.querySelector("input").checked = false; });
        onSelectionChanged();
    });
    startDay.addEventListener("change", () => { setTakeoutLimits(); updateTakeoutAvailability(); });
    endDay.addEventListener("change", updateTakeoutAvailability);
    useDay.addEventListener("change", () => updateRoomAvailability());
    document.addEventListener("mouseup", () => {
        state.dragging = false;
        roomTimeSlots.querySelectorAll(".drag-hover").forEach(slot => slot.classList.remove("drag-hover"));
    });
    form.addEventListener("submit", async event => {
        event.preventDefault();
        clearMessage();
        if (!state.selected.length) return showMessage("器具を選択してください。");
        if (state.usageType === "in_room" && (!state.selectedStart || !state.selectedEnd)) return showMessage("利用時間を選択してください。");
        const missing = state.selected.filter(item => !state.quantities[item.id]);
        if (missing.length) return showMessage(`数量を選択してください: ${missing.map(item => item.name).join("、")}`);
        const purpose = document.querySelector("#equipment-purpose").value;
        if (!purpose.trim()) return showMessage("使用目的を入力してください。");
        const data = new FormData();
        state.selected.forEach(item => {
            data.append("equipment_id", item.id);
            data.append("quantity", state.quantities[item.id]);
        });
        data.append("purpose", purpose);
        data.append("note", document.querySelector("#equipment-note").value);
        let endpoint = "/equipment-reservation";
        if (state.usageType === "takeout") {
            data.append("start_day", startDay.value);
            data.append("end_day", endDay.value);
        } else {
            endpoint = "/equipment-room-reservation";
            data.append("use_day", useDay.value);
            data.append("start_time", state.selectedStart);
            data.append("end_time", state.selectedEnd);
        }
        submitButton.disabled = true;
        try {
            await fetchJson(endpoint, {method: "POST", body: data});
            const count = state.selected.length;
            window.alert(count > 1
                ? `${count}件の器具の予約が確定しました。マイページで確認できます`
                : "予約が確定しました。マイページで確認できます");
            window.location.href = "/mypage";
        } catch (error) {
            showMessage(error.message);
            message.scrollIntoView({behavior: "smooth", block: "center"});
            if (state.usageType === "takeout") await updateTakeoutAvailability();
            else await updateRoomAvailability({keepSelection: true});
        } finally { submitButton.disabled = false; }
    });
    startDay.min = page.dataset.today;
    endDay.min = page.dataset.today;
    useDay.min = page.dataset.today;
    setTakeoutLimits();
    // ブラウザの戻る操作などでチェック状態が復元された場合に備える
    if (selectedPlace() || cards.some(card => card.querySelector("input").checked)) onSelectionChanged();
})();
