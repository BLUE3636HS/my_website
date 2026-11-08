const mentorPage = document.getElementById("mentor-schedule-page");
const mentorTomorrow = mentorPage.dataset.tomorrow;
let mentorSelectedDay = mentorPage.dataset.selectedDay;
let mentorSlots = JSON.parse(document.getElementById("mentor-initial-slots").textContent);
let mentorStartIndex = null;
let mentorEndIndex = null;
let mentorDragStart = null;
let mentorDragging = false;
let mentorRequestNumber = 0;

const mentorCalendarTitle = document.getElementById("mentor-calendar-title");
const mentorCalendarDays = document.getElementById("mentor-calendar-days");
const mentorPrevMonth = document.getElementById("mentor-prev-month");
const mentorNextMonth = document.getElementById("mentor-next-month");
const mentorSelectedDayLabel = document.getElementById("mentor-selected-day");
const mentorFormDay = document.getElementById("mentor-form-day");
const mentorTimeline = document.getElementById("mentor-timeline");
const mentorTimeScale = document.getElementById("mentor-time-scale");
const mentorLoading = document.getElementById("mentor-schedule-loading");
const mentorLoadError = document.getElementById("mentor-schedule-load-error");
const mentorForm = document.getElementById("mentor-schedule-form");
const mentorSubmit = document.getElementById("mentor-schedule-submit");
const mentorStartTime = document.getElementById("mentor-start-time");
const mentorEndTime = document.getElementById("mentor-end-time");
const mentorActionInputs = [...mentorForm.querySelectorAll('input[name="action"]')];
const mentorModeInputs = [...mentorForm.querySelectorAll('input[name="online"], input[name="offline"]')];
const mentorDetailTime = document.getElementById("mentor-detail-time");
const mentorDetailOnline = document.getElementById("mentor-detail-online");
const mentorDetailOffline = document.getElementById("mentor-detail-offline");
const mentorDetailBooking = document.getElementById("mentor-detail-booking");
const mentorDetailReserved = document.getElementById("mentor-detail-reserved");
let mentorCalendarCursor = new Date(`${mentorSelectedDay}T00:00:00`);
mentorCalendarCursor.setDate(1);

const mentorPad = value => String(value).padStart(2, "0");
const mentorIsoDate = (year, month, day) => `${year}-${mentorPad(month + 1)}-${mentorPad(day)}`;

function renderMentorCalendar() {
    const year = mentorCalendarCursor.getFullYear();
    const month = mentorCalendarCursor.getMonth();
    const monthKey = `${year}-${mentorPad(month + 1)}`;
    mentorCalendarTitle.textContent = `${year}年 ${month + 1}月`;
    mentorCalendarDays.innerHTML = "";
    for (let index = 0; index < new Date(year, month, 1).getDay(); index += 1) {
        const blank = document.createElement("span");
        blank.className = "schedule-calendar-blank";
        mentorCalendarDays.append(blank);
    }
    const endDay = new Date(year, month + 1, 0).getDate();
    for (let day = 1; day <= endDay; day += 1) {
        const key = mentorIsoDate(year, month, day);
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = day;
        button.disabled = key < mentorTomorrow;
        button.className = "schedule-calendar-day";
        if (key === mentorSelectedDay) {
            button.classList.add("is-selected");
            button.setAttribute("aria-current", "date");
        }
        if (!button.disabled) button.addEventListener("click", () => selectMentorDate(key));
        mentorCalendarDays.append(button);
    }
    mentorPrevMonth.disabled = monthKey <= mentorTomorrow.slice(0, 7);
}

function renderMentorScale() {
    mentorTimeScale.innerHTML = "";
    for (let hour = 9; hour <= 22; hour += 1) {
        const label = document.createElement("span");
        label.textContent = `${hour}:00`;
        label.style.left = `${((hour - 9) / 13) * 100}%`;
        mentorTimeScale.append(label);
    }
}

function mentorStateLabel(slot) {
    return {online: "オンライン", offline: "オフライン", both: "両方", unset: "未設定"}[slot.state] || slot.state;
}

function clearMentorSelection() {
    mentorStartIndex = null;
    mentorEndIndex = null;
    mentorStartTime.value = "";
    mentorEndTime.value = "";
    mentorDetailTime.textContent = "時間帯をドラッグして選択してください";
    mentorDetailOnline.textContent = "-";
    mentorDetailOffline.textContent = "-";
    mentorDetailBooking.textContent = "-";
    mentorDetailReserved.hidden = true;
    updateMentorSubmit();
}

function showMentorRange() {
    if (mentorStartIndex === null || mentorEndIndex === null) {
        clearMentorSelection();
        return;
    }
    const firstIndex = Math.min(mentorStartIndex, mentorEndIndex);
    const lastIndex = Math.max(mentorStartIndex, mentorEndIndex);
    const selected = mentorSlots.slice(firstIndex, lastIndex + 1);
    mentorStartTime.value = selected[0].start_time;
    mentorEndTime.value = selected[selected.length - 1].end_time;
    mentorDetailTime.textContent = `${mentorStartTime.value}〜${mentorEndTime.value}`;
    mentorDetailOnline.textContent = selected.every(slot => slot.online_available) ? "全枠対応可" : selected.some(slot => slot.online_available) ? "一部対応可" : "未設定";
    mentorDetailOffline.textContent = selected.every(slot => slot.offline_available) ? "全枠対応可" : selected.some(slot => slot.offline_available) ? "一部対応可" : "未設定";
    const onlineBooked = selected.some(slot => slot.online_reserved);
    const offlineBooked = selected.some(slot => slot.offline_reserved);
    mentorDetailBooking.textContent = onlineBooked && offlineBooked ? "オンライン・オフライン" : onlineBooked ? "オンライン" : offlineBooked ? "オフライン" : "なし";
    mentorDetailReserved.hidden = !(onlineBooked || offlineBooked);
    mentorTimeline.querySelectorAll(".schedule-timeline-slot").forEach((button, index) => {
        const inRange = index >= firstIndex && index <= lastIndex;
        button.classList.toggle("is-range-selected", inRange);
        button.classList.toggle("is-range-start", index === firstIndex);
        button.classList.toggle("is-range-end", index === lastIndex);
        button.setAttribute("aria-pressed", String(inRange));
    });
    updateMentorSubmit();
}

function selectMentorRange(start, end) {
    if (!mentorSlots[start] || !mentorSlots[end]) return;
    mentorStartIndex = start;
    mentorEndIndex = end;
    showMentorRange();
}

function renderMentorTimeline() {
    mentorTimeline.innerHTML = "";
    mentorSlots.forEach((slot, index) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `schedule-timeline-slot mentor-slot is-${slot.state}${slot.reserved ? " has-reservation" : ""}`;
        button.dataset.index = String(index);
        button.setAttribute("aria-pressed", "false");
        const reservations = [slot.online_reserved ? "オンライン予約あり" : "", slot.offline_reserved ? "オフライン予約あり" : ""].filter(Boolean).join("、");
        button.setAttribute("aria-label", `${slot.start_time}から${slot.end_time}、${mentorStateLabel(slot)}${reservations ? `、${reservations}` : ""}`);
        button.title = `${slot.start_time}〜${slot.end_time} ${mentorStateLabel(slot)}${reservations ? ` / ${reservations}` : ""}`;
        if (slot.reserved) {
            const marker = document.createElement("span");
            marker.className = "mentor-reservation-marker";
            marker.setAttribute("aria-hidden", "true");
            button.append(marker);
        }
        button.addEventListener("pointerdown", event => {
            if (event.button !== 0) return;
            event.preventDefault();
            mentorDragging = true;
            mentorDragStart = index;
            selectMentorRange(index, index);
        });
        button.addEventListener("click", event => {
            if (event.detail === 0) selectMentorRange(index, index);
        });
        mentorTimeline.append(button);
    });
    clearMentorSelection();
}

async function selectMentorDate(day) {
    if (day === mentorSelectedDay) return;
    const currentRequest = ++mentorRequestNumber;
    mentorLoading.hidden = false;
    mentorLoadError.hidden = true;
    try {
        const response = await fetch(`/admin/mentor-schedule/availability?day=${encodeURIComponent(day)}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "対応可能時間を取得できませんでした。");
        if (currentRequest !== mentorRequestNumber) return;
        mentorSelectedDay = data.day;
        mentorSlots = data.slots;
        mentorSelectedDayLabel.textContent = day;
        mentorFormDay.value = day;
        window.history.replaceState(null, "", `/admin/mentor-schedule?day=${encodeURIComponent(day)}`);
        renderMentorCalendar();
        renderMentorTimeline();
    } catch (error) {
        if (currentRequest !== mentorRequestNumber) return;
        mentorLoadError.textContent = error.message;
        mentorLoadError.hidden = false;
    } finally {
        if (currentRequest === mentorRequestNumber) mentorLoading.hidden = true;
    }
}

function updateMentorSubmit() {
    const deleting = mentorActionInputs.find(input => input.checked)?.value === "remove";
    const hasMode = mentorModeInputs.some(input => input.checked);
    const hasRange = Boolean(mentorStartTime.value && mentorEndTime.value);
    mentorSubmit.disabled = !(hasMode && hasRange);
    mentorSubmit.classList.toggle("is-delete", deleting);
    mentorSubmit.textContent = !hasRange || !hasMode
        ? "時間帯と利用形式を選択してください"
        : deleting ? "選択した利用形式を削除" : "選択した利用形式を追加";
}

mentorPrevMonth.addEventListener("click", () => {
    if (!mentorPrevMonth.disabled) {
        mentorCalendarCursor.setMonth(mentorCalendarCursor.getMonth() - 1);
        renderMentorCalendar();
    }
});
mentorNextMonth.addEventListener("click", () => {
    mentorCalendarCursor.setMonth(mentorCalendarCursor.getMonth() + 1);
    renderMentorCalendar();
});
mentorActionInputs.forEach(input => input.addEventListener("change", updateMentorSubmit));
mentorModeInputs.forEach(input => input.addEventListener("change", updateMentorSubmit));
mentorTimeline.addEventListener("pointermove", event => {
    if (!mentorDragging || mentorDragStart === null) return;
    const target = document.elementFromPoint(event.clientX, event.clientY)?.closest(".schedule-timeline-slot");
    if (target && mentorTimeline.contains(target)) selectMentorRange(mentorDragStart, Number(target.dataset.index));
});
document.addEventListener("pointerup", () => { mentorDragging = false; mentorDragStart = null; });
document.addEventListener("pointercancel", () => { mentorDragging = false; mentorDragStart = null; });
mentorForm.addEventListener("submit", event => {
    if (mentorSubmit.disabled) {
        event.preventDefault();
        return;
    }
    const deleting = mentorActionInputs.find(input => input.checked)?.value === "remove";
    if (deleting && !confirm("選択した時間帯から、指定した利用形式の対応設定を削除しますか？")) event.preventDefault();
});

renderMentorCalendar();
renderMentorScale();
renderMentorTimeline();
updateMentorSubmit();
