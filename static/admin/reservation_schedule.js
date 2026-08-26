const schedulePage = document.getElementById("schedule-page");
const today = schedulePage.dataset.today;
let selectedDay = schedulePage.dataset.selectedDay;
let slots = JSON.parse(document.getElementById("schedule-initial-slots").textContent);
let selectedStartIndex = null;
let selectedEndIndex = null;
let dragStartIndex = null;
let isDragging = false;
let requestNumber = 0;

const calendarTitle = document.getElementById("schedule-calendar-title");
const calendarDays = document.getElementById("schedule-calendar-days");
const prevMonth = document.getElementById("schedule-prev-month");
const nextMonth = document.getElementById("schedule-next-month");
const selectedDayLabel = document.getElementById("schedule-selected-day");
const formDay = document.getElementById("schedule-form-day");
const timeline = document.getElementById("schedule-timeline");
const timeScale = document.getElementById("schedule-time-scale");
const loading = document.getElementById("schedule-loading");
const loadError = document.getElementById("schedule-load-error");
const scheduleForm = document.getElementById("schedule-update-form");
const submitButton = document.getElementById("schedule-submit");
const startTimeInput = document.getElementById("schedule-start-time");
const endTimeInput = document.getElementById("schedule-end-time");
const actionInputs = [...scheduleForm.querySelectorAll('input[name="action"]')];
const detailTime = document.getElementById("schedule-detail-time");
const detailOwn = document.getElementById("schedule-detail-own");
const detailCapacity = document.getElementById("schedule-detail-capacity");
const detailReserved = document.getElementById("schedule-detail-reserved");
const detailRemaining = document.getElementById("schedule-detail-remaining");
let calendarCursor = new Date(`${selectedDay}T00:00:00`);
calendarCursor.setDate(1);

const pad = (value) => String(value).padStart(2, "0");
const isoDate = (year, month, day) => `${year}-${pad(month + 1)}-${pad(day)}`;

function renderCalendar() {
    const year = calendarCursor.getFullYear();
    const month = calendarCursor.getMonth();
    const monthKey = `${year}-${pad(month + 1)}`;
    calendarTitle.textContent = `${year}年 ${month + 1}月`;
    calendarDays.innerHTML = "";
    for (let index = 0; index < new Date(year, month, 1).getDay(); index += 1) {
        const blank = document.createElement("span");
        blank.className = "schedule-calendar-blank";
        calendarDays.append(blank);
    }
    const endDay = new Date(year, month + 1, 0).getDate();
    for (let day = 1; day <= endDay; day += 1) {
        const key = isoDate(year, month, day);
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = day;
        button.disabled = key < today;
        button.className = "schedule-calendar-day";
        if (key === selectedDay) {
            button.classList.add("is-selected");
            button.setAttribute("aria-current", "date");
        }
        if (!button.disabled) button.addEventListener("click", () => selectDate(key));
        calendarDays.append(button);
    }
    prevMonth.disabled = monthKey <= today.slice(0, 7);
}

function renderScale() {
    timeScale.innerHTML = "";
    for (let hour = 9; hour <= 22; hour += 1) {
        const label = document.createElement("span");
        label.textContent = `${hour}:00`;
        label.style.left = `${((hour - 9) / 13) * 100}%`;
        timeScale.append(label);
    }
}

function stateLabel(slot) {
    return {available: "空きあり", full: "満席", unset: "未設定", closed: "受付終了"}[slot.state] || slot.state;
}

function clearTimeSelection() {
    selectedStartIndex = null;
    selectedEndIndex = null;
    startTimeInput.value = "";
    endTimeInput.value = "";
    submitButton.disabled = true;
    detailTime.textContent = "時間帯をドラッグして選択してください";
    detailOwn.hidden = true;
    detailCapacity.textContent = "-";
    detailReserved.textContent = "-";
    detailRemaining.textContent = "-";
}

function showSelectedRange() {
    if (selectedStartIndex === null || selectedEndIndex === null) {
        clearTimeSelection();
        return;
    }
    const firstIndex = Math.min(selectedStartIndex, selectedEndIndex);
    const lastIndex = Math.max(selectedStartIndex, selectedEndIndex);
    const selectedSlots = slots.slice(firstIndex, lastIndex + 1);
    const firstSlot = selectedSlots[0];
    const lastSlot = selectedSlots[selectedSlots.length - 1];
    startTimeInput.value = firstSlot.start_time;
    endTimeInput.value = lastSlot.end_time;
    submitButton.disabled = false;
    detailTime.textContent = `${firstSlot.start_time}〜${lastSlot.end_time}`;
    detailOwn.hidden = !selectedSlots.every((slot) => slot.own);
    detailCapacity.textContent = Math.min(...selectedSlots.map((slot) => slot.capacity));
    detailReserved.textContent = Math.max(...selectedSlots.map((slot) => slot.reserved));
    detailRemaining.textContent = Math.min(...selectedSlots.map((slot) => slot.remaining));
    timeline.querySelectorAll(".schedule-timeline-slot").forEach((button, index) => {
        const selected = index >= firstIndex && index <= lastIndex;
        button.classList.toggle("is-range-selected", selected);
        button.classList.toggle("is-range-start", index === firstIndex);
        button.classList.toggle("is-range-end", index === lastIndex);
        button.setAttribute("aria-pressed", String(selected));
    });
    updateAction();
}

function selectRange(startIndex, endIndex) {
    if (!slots[startIndex] || !slots[endIndex]) {
        detailOwn.hidden = true;
        return;
    }
    selectedStartIndex = startIndex;
    selectedEndIndex = endIndex;
    showSelectedRange();
}

function renderTimeline() {
    timeline.innerHTML = "";
    slots.forEach((slot, index) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `schedule-timeline-slot is-${slot.state}${slot.own ? " is-own" : ""}`;
        button.dataset.time = slot.start_time;
        button.dataset.index = String(index);
        button.setAttribute("aria-pressed", "false");
        button.setAttribute("aria-label", `${slot.start_time}から${slot.end_time}、${stateLabel(slot)}、登録枠${slot.capacity}、予約${slot.reserved}、残り${slot.remaining}${slot.own ? "、自分が設定" : ""}`);
        button.title = `${slot.start_time}〜${slot.end_time} ${stateLabel(slot)}`;
        if (slot.own) {
            const marker = document.createElement("span");
            marker.className = "schedule-own-marker";
            marker.setAttribute("aria-hidden", "true");
            button.append(marker);
        }
        button.addEventListener("pointerdown", (event) => {
            if (event.button !== 0) return;
            event.preventDefault();
            isDragging = true;
            dragStartIndex = index;
            selectRange(index, index);
        });
        button.addEventListener("click", (event) => {
            if (event.detail === 0) selectRange(index, index);
        });
        timeline.append(button);
    });
    clearTimeSelection();
}

async function selectDate(day) {
    if (day === selectedDay) return;
    const currentRequest = ++requestNumber;
    loading.hidden = false;
    loadError.hidden = true;
    try {
        const response = await fetch(`/admin/reservation-schedule/availability?day=${encodeURIComponent(day)}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "予約状況を取得できませんでした。");
        if (currentRequest !== requestNumber) return;
        selectedDay = data.day;
        slots = data.slots;
        selectedStartIndex = null;
        selectedEndIndex = null;
        selectedDayLabel.textContent = selectedDay;
        formDay.value = selectedDay;
        window.history.replaceState(null, "", `/admin/reservation-schedule?day=${encodeURIComponent(selectedDay)}`);
        renderCalendar();
        renderTimeline();
    } catch (error) {
        if (currentRequest !== requestNumber) return;
        loadError.textContent = error.message;
        loadError.hidden = false;
    } finally {
        if (currentRequest === requestNumber) loading.hidden = true;
    }
}

function updateAction() {
    const deleting = actionInputs.find((input) => input.checked)?.value === "delete";
    submitButton.textContent = startTimeInput.value
        ? (deleting ? "選択した時間帯を削除" : "選択した時間帯を追加")
        : "時間帯を選択してください";
    submitButton.classList.toggle("is-delete", deleting);
}

prevMonth.addEventListener("click", () => {
    if (!prevMonth.disabled) {
        calendarCursor.setMonth(calendarCursor.getMonth() - 1);
        renderCalendar();
    }
});
nextMonth.addEventListener("click", () => {
    calendarCursor.setMonth(calendarCursor.getMonth() + 1);
    renderCalendar();
});
actionInputs.forEach((input) => input.addEventListener("change", updateAction));
timeline.addEventListener("pointermove", (event) => {
    if (!isDragging || dragStartIndex === null) return;
    const target = document.elementFromPoint(event.clientX, event.clientY)?.closest(".schedule-timeline-slot");
    if (target && timeline.contains(target)) selectRange(dragStartIndex, Number(target.dataset.index));
});
document.addEventListener("pointerup", () => {
    isDragging = false;
    dragStartIndex = null;
});
document.addEventListener("pointercancel", () => {
    isDragging = false;
    dragStartIndex = null;
});
scheduleForm.addEventListener("submit", (event) => {
    if (!startTimeInput.value || !endTimeInput.value) {
        event.preventDefault();
        return;
    }
    const deleting = actionInputs.find((input) => input.checked)?.value === "delete";
    if (deleting && !confirm("選択した時間帯から、自分が登録した枠を削除しますか？")) {
        event.preventDefault();
    }
});

renderCalendar();
renderScale();
renderTimeline();
updateAction();
