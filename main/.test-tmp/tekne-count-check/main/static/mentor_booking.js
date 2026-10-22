(() => {
const page = document.getElementById("reservation-page");
const today = page.dataset.today;
const initialDay = page.dataset.initialDay;
const csrfToken = page.dataset.csrfToken;
const calendarDays = document.getElementById("calendar-days");
const monthTitle = document.getElementById("month-title");
const prevMonthButton = document.getElementById("prev-month");
const nextMonthButton = document.getElementById("next-month");
const selectedDate = document.getElementById("selected-date");
const reservationStatus = document.getElementById("reservation-status");
const timeMessage = document.getElementById("time-message");
const selectedStart = document.getElementById("selected-start");
const selectedEnd = document.getElementById("selected-end");
const purpose = document.getElementById("purpose");
const submitButton = document.getElementById("reservation-submit");
const message = document.getElementById("reservation-message");
const endpoint = location.pathname.replace(/\/$/, "");
const meetingType = () => document.querySelector('[name="meeting_type"]:checked').value;
let timeRequest = 0;
let submitting = false;
let selectedDay = "";
let unavailableTimes = new Set();
let availableDays = new Set();
let isDragging = false;
let dragStartTime = null;
let calendarRequest = 0;
let cursor = new Date(`${initialDay || today}T00:00:00`);
cursor.setDate(1);

const pad = (value) => String(value).padStart(2, "0");
const isoDate = (year, month, day) => `${year}-${pad(month + 1)}-${pad(day)}`;
const timeToMinutes = (value) => {
    const [hour, minute] = value.split(":").map(Number);
    return hour * 60 + minute;
};
const minutesToTime = (value) => `${pad(Math.floor(value / 60))}:${pad(value % 60)}`;

function showMessage(text, type = "error") {
    message.textContent = text;
    message.className = `reservation-message is-${type}`;
    message.hidden = false;
}
function clearMessage() {
    message.hidden = true;
    message.textContent = "";
}

async function renderCalendar() {
    const requestId = ++calendarRequest;
    const year = cursor.getFullYear();
    const month = cursor.getMonth();
    const monthKey = `${year}-${pad(month + 1)}`;
    monthTitle.textContent = `${year}年 ${month + 1}月`;
    calendarDays.innerHTML = '<span class="status-loading">読込中...</span>';
    try {
        const response = await fetch(`${endpoint}/available-days?month=${encodeURIComponent(monthKey)}&meeting_type=${meetingType()}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "予約可能日を取得できませんでした。");
        if (requestId !== calendarRequest) return;
        availableDays = new Set(data.available_days);
    } catch (error) {
        if (requestId !== calendarRequest) return;
        availableDays = new Set();
        showMessage(error.message);
    }
    calendarDays.innerHTML = "";
    for (let index = 0; index < new Date(year, month, 1).getDay(); index += 1) {
        const blank = document.createElement("span");
        blank.className = "blank";
        calendarDays.append(blank);
    }
    for (let day = 1; day <= new Date(year, month + 1, 0).getDate(); day += 1) {
        const key = isoDate(year, month, day);
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = day;
        button.disabled = !availableDays.has(key);
        if (!button.disabled) {
            button.className = "available";
            button.addEventListener("click", () => { if (!submitting) chooseDate(key); });
        }
        if (key === selectedDay) button.classList.add("selected");
        calendarDays.append(button);
    }
    prevMonthButton.disabled = monthKey <= today.slice(0, 7);
}

function resetTimeSelection() {
    isDragging = false;
    dragStartTime = null;
    selectedStart.textContent = "未選択";
    selectedEnd.textContent = "未選択";
    submitButton.disabled = true;
    reservationStatus.querySelectorAll(".reservation-time").forEach((slot) => {
        slot.classList.remove("selected", "drag-start");
    });
}

async function chooseDate(key) {
    const requestId = ++timeRequest;
    selectedDay = key;
    selectedDate.textContent = `選択日：${key}`;
    resetTimeSelection();
    reservationStatus.innerHTML = "";
    reservationStatus.classList.add("is-disabled");
    clearMessage();
    await renderCalendar();
    if (requestId !== timeRequest) return;
    reservationStatus.classList.add("is-disabled");
    reservationStatus.innerHTML = '<p class="status-loading">空き状況を読み込んでいます...</p>';
    timeMessage.textContent = "空き状況を確認しています";
    try {
        const response = await fetch(`${endpoint}/availability?day=${encodeURIComponent(key)}&meeting_type=${meetingType()}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "空き状況を取得できませんでした。");
        if (requestId !== timeRequest) return;
        renderTimeSlots(data.slots);
    } catch (error) {
        if (requestId !== timeRequest) return;
        reservationStatus.innerHTML = "";
        timeMessage.textContent = "空き状況を取得できませんでした";
        showMessage(error.message);
    }
}

function renderTimeSlots(slots) {
    reservationStatus.innerHTML = "";
    reservationStatus.classList.remove("is-disabled");
    unavailableTimes = new Set(slots.filter((slot) => slot.state !== "available").map((slot) => slot.start_time));
    const labels = {full: "予約済み", unset: "未設定", closed: "受付終了"};
    slots.forEach((data) => {
        const slot = document.createElement("div");
        slot.className = `reservation-time ${data.state}`;
        slot.dataset.time = data.start_time;
        const label = document.createElement("span");
        label.className = "reservation-time-label";
        label.textContent = data.start_time;
        const body = document.createElement("div");
        body.className = "reservation-slot-body";
        const status = document.createElement("span");
        status.className = "reservation-slot-status";
        status.textContent = data.state === "available" ? "空き" : labels[data.state];
        body.append(status);
        slot.append(label, body);
        addTimeEvents(slot);
        reservationStatus.append(slot);
    });
    const marker = document.createElement("div");
    marker.className = "reservation-timeline-end";
    marker.innerHTML = '<span class="reservation-time-label">22:00</span><span class="reservation-timeline-end-line"></span>';
    reservationStatus.append(marker);
    timeMessage.textContent = slots.some((slot) => slot.state === "available")
        ? "空いている時間を開始から終了までドラッグしてください"
        : "この日に予約できる時間はありません";
}

function rangeIsAvailable(startMinutes, endMinutes) {
    for (let value = startMinutes; value < endMinutes; value += 30) {
        if (unavailableTimes.has(minutesToTime(value))) return false;
    }
    return true;
}

function updateSelection(currentTime) {
    const startMinutes = timeToMinutes(dragStartTime);
    const endMinutes = timeToMinutes(currentTime) + 30;
    if (endMinutes <= startMinutes || endMinutes - startMinutes > 180 || !rangeIsAvailable(startMinutes, endMinutes)) return false;
    reservationStatus.querySelectorAll(".reservation-time").forEach((slot) => {
        const value = timeToMinutes(slot.dataset.time);
        slot.classList.toggle("selected", value >= startMinutes && value < endMinutes);
        slot.classList.remove("drag-start");
    });
    reservationStatus.querySelector(`[data-time="${dragStartTime}"]`).classList.add("drag-start");
    selectedStart.textContent = dragStartTime;
    selectedEnd.textContent = minutesToTime(endMinutes);
    return true;
}

function addTimeEvents(slot) {
    slot.addEventListener("mousedown", (event) => {
        if (submitting || event.button !== 0 || !slot.classList.contains("available")) return;
        event.preventDefault();
        resetTimeSelection();
        isDragging = true;
        dragStartTime = slot.dataset.time;
        updateSelection(slot.dataset.time);
        timeMessage.textContent = "終了時間までドラッグしてください";
    });
    slot.addEventListener("mouseenter", () => {
        if (isDragging) updateSelection(slot.dataset.time);
    });
    slot.addEventListener("mouseup", (event) => {
        if (event.button !== 0 || !isDragging) return;
        if (updateSelection(slot.dataset.time)) {
            submitButton.disabled = submitting;
            clearMessage();
            timeMessage.textContent = "予約時間を確認してください";
        }
        isDragging = false;
    });
}

document.addEventListener("mouseup", () => { isDragging = false; });
prevMonthButton.addEventListener("click", () => { cursor.setMonth(cursor.getMonth() - 1); renderCalendar(); });
nextMonthButton.addEventListener("click", () => { cursor.setMonth(cursor.getMonth() + 1); renderCalendar(); });

submitButton.addEventListener("click", async () => {
    if (submitting) return;
    if (!selectedDay || selectedStart.textContent === "未選択" || selectedEnd.textContent === "未選択") {
        showMessage("日付と予約時間を選択してください。");
        return;
    }
    if (!purpose.value.trim()) {
        showMessage("相談内容を入力してください。");
        return;
    }
    if (purpose.value.trim().length > 2000) {
        showMessage("相談内容は2000文字以内で入力してください。");
        return;
    }
    const bookedDay = selectedDay;
    const formData = new FormData();
    formData.append("day", selectedDay);
    formData.append("start_time", selectedStart.textContent);
    formData.append("end_time", selectedEnd.textContent);
    formData.append("consultation", purpose.value);
    formData.append("meeting_type", meetingType());
    formData.append("csrf", csrfToken);
    submitting = true;
    document.querySelectorAll('[name="meeting_type"]').forEach(input => { input.disabled = true; });
    submitButton.disabled = true;
    try {
        const response = await fetch(endpoint, {method: "POST", body: formData});
        const data = await response.json();
        if (!response.ok || !data.result) throw new Error(data.message || "予約を登録できませんでした。");
        purpose.value = "";
        await chooseDate(bookedDay);
        showMessage(data.message, "success");
    } catch (error) {
        await chooseDate(bookedDay);
        showMessage(error.message);
    } finally {
        submitting = false;
        document.querySelectorAll('[name="meeting_type"]').forEach(input => { input.disabled = false; });
    }
});

document.querySelectorAll('[name="meeting_type"]').forEach(input => {
    input.addEventListener("change", () => {
        ++timeRequest;
        selectedDay = "";
        resetTimeSelection();
        clearMessage();
        selectedDate.textContent = "日付を選択してください";
        timeMessage.textContent = "日付を先に選択してください";
        reservationStatus.innerHTML = "";
        reservationStatus.classList.add("is-disabled");
        renderCalendar();
    });
});
reservationStatus.innerHTML = '<p class="status-placeholder">日付を選択すると空き状況が表示されます。</p>';
renderCalendar().then(() => {
    if (initialDay && availableDays.has(initialDay)) chooseDate(initialDay);
});

})();
