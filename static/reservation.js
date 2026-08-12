const page = document.getElementById("reservation-page");
const today = page.dataset.today;
const initialDay = page.dataset.initialDay;
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

let selectedDay = "";
let unavailableTimes = new Set();
let isDragging = false;
let dragStartTime = null;
let cursor = initialDay ? new Date(`${initialDay}T00:00:00`) : new Date(`${today}T00:00:00`);
cursor.setDate(1);

const pad = (number) => String(number).padStart(2, "0");
const isoDate = (year, month, day) => `${year}-${pad(month + 1)}-${pad(day)}`;

function timeToMinutes(time) {
    const [hour, minute] = time.split(":").map(Number);
    return hour * 60 + minute;
}

function minutesToTime(totalMinutes) {
    return `${pad(Math.floor(totalMinutes / 60))}:${pad(totalMinutes % 60)}`;
}

function showMessage(text, type = "error") {
    message.textContent = text;
    message.className = `reservation-message is-${type}`;
    message.hidden = false;
}

function clearMessage() {
    message.hidden = true;
    message.textContent = "";
}

function renderCalendar() {
    const year = cursor.getFullYear();
    const month = cursor.getMonth();
    monthTitle.textContent = `${year}年 ${month + 1}月`;
    calendarDays.innerHTML = "";

    const firstWeekday = new Date(year, month, 1).getDay();
    for (let index = 0; index < firstWeekday; index += 1) {
        const blank = document.createElement("span");
        blank.className = "blank";
        calendarDays.append(blank);
    }

    const lastDay = new Date(year, month + 1, 0).getDate();
    for (let day = 1; day <= lastDay; day += 1) {
        const key = isoDate(year, month, day);
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = day;
        button.disabled = key < today;
        if (!button.disabled) {
            button.className = "available";
            button.addEventListener("click", () => chooseDate(key));
        }
        if (key === selectedDay) {
            button.classList.add("selected");
        }
        calendarDays.append(button);
    }

    const currentMonth = today.slice(0, 7);
    const displayedMonth = `${year}-${pad(month + 1)}`;
    prevMonthButton.disabled = displayedMonth <= currentMonth;
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
    selectedDay = key;
    selectedDate.textContent = `選択日：${key}`;
    resetTimeSelection();
    clearMessage();
    renderCalendar();
    reservationStatus.classList.add("is-disabled");
    reservationStatus.innerHTML = '<p class="status-loading">空き状況を読み込んでいます...</p>';
    timeMessage.textContent = "空き状況を確認しています";

    try {
        const response = await fetch(`/reservation/availability?day=${encodeURIComponent(key)}`);
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || "空き状況を取得できませんでした。");
        }
        unavailableTimes = new Set([...data.reserved_times, ...data.closed_times]);
        renderTimeSlots(new Set(data.reserved_times), new Set(data.closed_times));
    } catch (error) {
        reservationStatus.innerHTML = "";
        timeMessage.textContent = "空き状況を取得できませんでした";
        showMessage(error.message);
    }
}

function renderTimeSlots(reservedTimes, closedTimes) {
    reservationStatus.innerHTML = "";
    reservationStatus.classList.remove("is-disabled");

    for (let totalMinutes = 9 * 60; totalMinutes < 21 * 60; totalMinutes += 30) {
        const time = minutesToTime(totalMinutes);
        const slot = document.createElement("div");
        slot.className = "reservation-time";
        slot.dataset.time = time;

        const label = document.createElement("span");
        label.textContent = time;
        const status = document.createElement("span");

        if (reservedTimes.has(time)) {
            slot.classList.add("reserved");
            status.textContent = "予約済み";
        } else if (closedTimes.has(time)) {
            slot.classList.add("closed");
            status.textContent = "受付終了";
        } else {
            slot.classList.add("available");
            status.textContent = "空き";
        }

        slot.append(label, status);
        addTimeEvents(slot);
        reservationStatus.append(slot);
    }

    timeMessage.textContent = "空いている時間を開始から終了までドラッグしてください";
}

function addTimeEvents(slot) {
    slot.addEventListener("mousedown", (event) => {
        if (event.button !== 0 || !slot.classList.contains("available")) {
            return;
        }
        event.preventDefault();
        isDragging = true;
        dragStartTime = slot.dataset.time;
        resetTimeSelection();
        isDragging = true;
        dragStartTime = slot.dataset.time;
        slot.classList.add("drag-start");
        selectedStart.textContent = dragStartTime;
        timeMessage.textContent = "終了時間までドラッグしてください";
    });

    slot.addEventListener("mouseenter", () => {
        if (isDragging) {
            updateSelection(slot.dataset.time);
        }
    });

    slot.addEventListener("mouseup", (event) => {
        if (event.button !== 0 || !isDragging) {
            return;
        }
        updateSelection(slot.dataset.time);
        if (checkSelection(slot.dataset.time)) {
            selectedEnd.textContent = slot.dataset.time;
            submitButton.disabled = false;
            timeMessage.textContent = "予約時間を確認してください";
        }
        isDragging = false;
    });
}

function rangeIsAvailable(startMinutes, endMinutes) {
    for (let time = startMinutes; time < endMinutes; time += 30) {
        if (unavailableTimes.has(minutesToTime(time))) {
            return false;
        }
    }
    return true;
}

function updateSelection(currentTime) {
    const startMinutes = timeToMinutes(dragStartTime);
    const currentMinutes = timeToMinutes(currentTime);
    if (currentMinutes < startMinutes || currentMinutes - startMinutes > 180 || !rangeIsAvailable(startMinutes, currentMinutes)) {
        return;
    }

    reservationStatus.querySelectorAll(".reservation-time").forEach((slot) => {
        const slotMinutes = timeToMinutes(slot.dataset.time);
        slot.classList.toggle("selected", slotMinutes >= startMinutes && slotMinutes <= currentMinutes);
        slot.classList.remove("drag-start");
    });
    reservationStatus.querySelector(`[data-time="${dragStartTime}"]`).classList.add("drag-start");
    selectedStart.textContent = dragStartTime;
    selectedEnd.textContent = currentTime;
}

function checkSelection(endTime) {
    const startMinutes = timeToMinutes(dragStartTime);
    const endMinutes = timeToMinutes(endTime);
    if (endMinutes <= startMinutes) {
        showMessage("終了時間は開始時間より後にしてください。");
        resetTimeSelection();
        return false;
    }
    if (endMinutes - startMinutes > 180) {
        showMessage("予約は3時間までです。");
        resetTimeSelection();
        return false;
    }
    if (!rangeIsAvailable(startMinutes, endMinutes)) {
        showMessage("予約済み、または受付終了の時間を含むため選択できません。");
        resetTimeSelection();
        return false;
    }
    clearMessage();
    return true;
}

document.addEventListener("mouseup", () => {
    isDragging = false;
});

prevMonthButton.addEventListener("click", () => {
    if (!prevMonthButton.disabled) {
        cursor.setMonth(cursor.getMonth() - 1);
        renderCalendar();
    }
});

nextMonthButton.addEventListener("click", () => {
    cursor.setMonth(cursor.getMonth() + 1);
    renderCalendar();
});

submitButton.addEventListener("click", async () => {
    const selectedEquipment = [...document.querySelectorAll(".equipment:checked")].map((input) => input.value);
    if (!selectedDay || selectedStart.textContent === "未選択" || selectedEnd.textContent === "未選択") {
        showMessage("日付と予約時間を選択してください。");
        return;
    }
    if (!purpose.value.trim()) {
        showMessage("使用目的を入力してください。");
        return;
    }

    const formData = new FormData();
    formData.append("day", selectedDay);
    formData.append("start_time", selectedStart.textContent);
    formData.append("end_time", selectedEnd.textContent);
    formData.append("equipment", JSON.stringify(selectedEquipment));
    formData.append("purpose", purpose.value);

    submitButton.disabled = true;
    try {
        const response = await fetch("/reservation/date", {method: "POST", body: formData});
        const data = await response.json();
        if (!response.ok || !data.result) {
            throw new Error(data.message || "予約を登録できませんでした。");
        }
        document.querySelectorAll(".equipment:checked").forEach((input) => { input.checked = false; });
        purpose.value = "";
        showMessage(data.message, "success");
        await chooseDate(selectedDay);
        showMessage(data.message, "success");
    } catch (error) {
        showMessage(error.message);
        await chooseDate(selectedDay);
        showMessage(error.message);
    }
});

renderCalendar();
reservationStatus.innerHTML = '<p class="status-placeholder">日付を選択すると空き状況が表示されます。</p>';
if (initialDay) {
    chooseDate(initialDay);
}
