(() => {
    const page = document.querySelector(".equipment-reservation");
    const form = document.querySelector("#equipment-reservation-form");
    const message = document.querySelector("#equipment-message");
    const takeoutFields = document.querySelector("#takeout-fields");
    const roomFields = document.querySelector("#room-fields");
    const purposeFields = document.querySelector("#purpose-fields");
    const submitButton = document.querySelector("#equipment-submit");
    const startDay = document.querySelector("#start_day");
    const endDay = document.querySelector("#end_day");
    const useDay = document.querySelector("#use_day");
    const takeoutQuantity = document.querySelector("#takeout-quantity");
    const roomQuantity = document.querySelector("#room-quantity");
    const takeoutAvailability = document.querySelector("#takeout-availability");
    const roomAvailability = document.querySelector("#room-availability");
    const roomTimeMessage = document.querySelector("#room-time-message");
    const roomTimeSlots = document.querySelector("#room-time-slots");
    const selectedStartLabel = document.querySelector("#room-selected-start");
    const selectedEndLabel = document.querySelector("#room-selected-end");
    const state = {equipmentId: "", usageType: "", roomSlots: [], dragging: false, dragStartIndex: null, selectedStart: "", selectedEnd: ""};

    function showMessage(text, type = "error") {
        message.textContent = text;
        message.className = `equipment-message is-${type}`;
        message.hidden = false;
    }
    function clearMessage() { message.textContent = ""; message.hidden = true; }
    function setQuantityOptions(select, available, placeholder) {
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
    }
    function setTakeoutLimits() {
        endDay.min = startDay.value;
        if (!endDay.value || endDay.value < startDay.value) endDay.value = startDay.value;
        const maximum = new Date(`${startDay.value}T00:00:00`);
        maximum.setDate(maximum.getDate() + 6);
        endDay.max = maximum.toISOString().slice(0, 10);
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
    async function updateTakeoutAvailability() {
        if (state.usageType !== "takeout" || !startDay.value || !endDay.value) return;
        takeoutAvailability.textContent = "在庫を確認しています...";
        setQuantityOptions(takeoutQuantity, 0, "確認中です");
        try {
            const params = new URLSearchParams({equipment_id: state.equipmentId, start_day: startDay.value, end_day: endDay.value});
            const data = await fetchJson(`/equipment-availability?${params}`);
            setQuantityOptions(takeoutQuantity, data.available, data.available ? "数量を選択" : "在庫なし");
            takeoutAvailability.textContent = data.available ? `希望期間中の利用可能数: ${data.available}` : "選択した期間は在庫がありません。";
        } catch (error) { takeoutAvailability.textContent = error.message; }
    }
    function resetRoomSelection() {
        state.dragging = false;
        state.dragStartIndex = null;
        state.selectedStart = "";
        state.selectedEnd = "";
        selectedStartLabel.textContent = "未選択";
        selectedEndLabel.textContent = "未選択";
        setQuantityOptions(roomQuantity, 0, "時間帯を選択してください");
        roomAvailability.textContent = "時間帯を選択すると利用可能数を表示します。";
        roomTimeSlots.querySelectorAll(".room-time-slot").forEach(slot => slot.classList.remove("selected", "drag-start"));
    }
    function updateRoomSelection(endIndex) {
        if (state.dragStartIndex === null || endIndex < state.dragStartIndex) return false;
        const selected = state.roomSlots.slice(state.dragStartIndex, endIndex + 1);
        if (!selected.length || selected.some(slot => slot.closed || slot.available_quantity <= 0)) return false;
        roomTimeSlots.querySelectorAll(".room-time-slot").forEach((element, index) => {
            element.classList.toggle("selected", index >= state.dragStartIndex && index <= endIndex);
            element.classList.toggle("drag-start", index === state.dragStartIndex);
        });
        state.selectedStart = selected[0].start_time;
        state.selectedEnd = selected[selected.length - 1].end_time;
        selectedStartLabel.textContent = state.selectedStart;
        selectedEndLabel.textContent = state.selectedEnd;
        const available = Math.min(...selected.map(slot => slot.available_quantity));
        setQuantityOptions(roomQuantity, available, "数量を選択");
        roomAvailability.textContent = `選択時間帯の利用可能数: ${available}`;
        return true;
    }
    function addRoomSlotEvents(element, index) {
        element.addEventListener("mousedown", event => {
            if (event.button !== 0 || element.classList.contains("unavailable")) return;
            event.preventDefault();
            resetRoomSelection();
            state.dragging = true;
            state.dragStartIndex = index;
            updateRoomSelection(index);
            roomTimeMessage.textContent = "終了する枠までドラッグしてください。";
        });
        element.addEventListener("mouseenter", () => { if (state.dragging) updateRoomSelection(index); });
        element.addEventListener("mouseup", event => {
            if (event.button !== 0 || !state.dragging) return;
            if (updateRoomSelection(index)) roomTimeMessage.textContent = "選択した利用時間を確認してください。";
            state.dragging = false;
        });
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
            const period = document.createElement("span");
            period.textContent = `${slot.start_time}～${slot.end_time}`;
            const status = document.createElement("span");
            status.textContent = slot.closed ? "受付終了" : slot.available_quantity ? `残り ${slot.available_quantity}` : "在庫なし";
            element.append(period, status);
            addRoomSlotEvents(element, index);
            roomTimeSlots.append(element);
        });
    }
    async function updateRoomAvailability() {
        if (state.usageType !== "in_room" || !useDay.value) return;
        resetRoomSelection();
        roomTimeSlots.classList.add("is-disabled");
        roomTimeSlots.innerHTML = '<p class="status-loading">空き状況を読み込んでいます...</p>';
        try {
            const params = new URLSearchParams({equipment_id: state.equipmentId, day: useDay.value});
            const data = await fetchJson(`/equipment-room-availability?${params}`);
            state.roomSlots = data.slots;
            renderRoomSlots(data.slots);
            roomTimeMessage.textContent = "開始枠から終了枠までドラッグしてください。";
        } catch (error) {
            state.roomSlots = [];
            roomTimeSlots.innerHTML = "";
            roomTimeMessage.textContent = error.message;
        }
    }
    function switchReservationMode(card) {
        clearMessage();
        state.equipmentId = card.dataset.equipmentId;
        state.usageType = card.dataset.usageType;
        const takeout = state.usageType === "takeout";
        takeoutFields.hidden = !takeout;
        roomFields.hidden = takeout;
        purposeFields.hidden = false;
        submitButton.hidden = false;
        takeoutFields.querySelectorAll("input, select").forEach(control => { control.disabled = !takeout; });
        roomFields.querySelectorAll("input, select").forEach(control => { control.disabled = takeout; });
        if (takeout) { setTakeoutLimits(); updateTakeoutAvailability(); } else { updateRoomAvailability(); }
    }
    form.addEventListener("change", event => {
        if (event.target.name === "equipment_id") switchReservationMode(event.target.closest(".equipment-card"));
    });
    startDay.addEventListener("change", () => { setTakeoutLimits(); updateTakeoutAvailability(); });
    endDay.addEventListener("change", updateTakeoutAvailability);
    useDay.addEventListener("change", updateRoomAvailability);
    document.addEventListener("mouseup", () => { state.dragging = false; });
    form.addEventListener("submit", async event => {
        event.preventDefault();
        clearMessage();
        if (!state.equipmentId) return showMessage("器具を選択してください。");
        if (state.usageType === "in_room" && (!state.selectedStart || !state.selectedEnd)) return showMessage("利用時間を選択してください。");
        const data = new FormData();
        data.append("equipment_id", state.equipmentId);
        data.append("quantity", state.usageType === "takeout" ? takeoutQuantity.value : roomQuantity.value);
        data.append("purpose", document.querySelector("#equipment-purpose").value);
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
            showMessage("予約が完了しました。", "success");
            window.location.href = "/mypage";
        } catch (error) {
            showMessage(error.message);
            if (state.usageType === "takeout") await updateTakeoutAvailability(); else await updateRoomAvailability();
        } finally { submitButton.disabled = false; }
    });
    startDay.min = page.dataset.today;
    endDay.min = page.dataset.today;
    useDay.min = page.dataset.today;
    setTakeoutLimits();
})();
