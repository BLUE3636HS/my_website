"use strict";

// UTC is used only for date arithmetic; the initial date comes from the server in JST.
const MyPageCalendar = (() => {
    const parse = (day) => new Date(`${day}T00:00:00Z`);
    const iso = (date) => date.toISOString().slice(0, 10);
    function addDays(day, count) {
        const date = parse(day);
        date.setUTCDate(date.getUTCDate() + count);
        return iso(date);
    }
    function monthDays(month) {
        const first = `${month}-01`;
        const end = parse(first);
        end.setUTCMonth(end.getUTCMonth() + 1);
        end.setUTCDate(0);
        return { first, last: iso(end) };
    }
    function moveMonth(month, offset) {
        const date = parse(`${month}-01`);
        date.setUTCMonth(date.getUTCMonth() + offset);
        return iso(date).slice(0, 7);
    }
    const onDay = (events, day) => events.filter(event => event.start <= day && event.end >= day)
        .sort((a, b) => a.startTime.localeCompare(b.startTime) || a.key.localeCompare(b.key));

    function layoutMonth(events, month, maxLanes = 3) {
        const { first, last } = monthDays(month);
        const weeks = [];
        for (let weekStart = addDays(first, -parse(first).getUTCDay()); weekStart <= last; weekStart = addDays(weekStart, 7)) {
            const days = Array.from({ length: 7 }, (_, index) => addDays(weekStart, index));
            // Include the leading/trailing days shown in this month's grid so
            // their reservations remain visible as read-only context.
            const start = days[0];
            const end = days[6];
            const segments = events.filter(event => event.start <= end && event.end >= start).map(event => {
                const visibleStart = event.start < start ? start : event.start;
                const visibleEnd = event.end > end ? end : event.end;
                return { event, start: days.indexOf(visibleStart), end: days.indexOf(visibleEnd),
                    continuesBefore: event.start < visibleStart, continuesAfter: event.end > visibleEnd };
            }).sort((a, b) => {
                // Reserve upper lanes for continuous takeout bars, then timed reservations.
                return Number(b.event.kind === "takeout") - Number(a.event.kind === "takeout") ||
                    a.start - b.start || (b.end - b.start) - (a.end - a.start) ||
                    a.event.startTime.localeCompare(b.event.startTime) || a.event.key.localeCompare(b.event.key);
            });
            const lanes = [];
            for (const segment of segments) {
                let lane = lanes.findIndex(occupied => !occupied.some(other => other.start <= segment.end && other.end >= segment.start));
                if (lane === -1) { lane = lanes.length; lanes.push([]); }
                lanes[lane].push(segment);
                segment.lane = lane;
            }
            const hiddenCounts = days.map((day, index) =>
                segments.filter(segment => segment.lane >= maxLanes && segment.start <= index && segment.end >= index).length);
            weeks.push({ days, segments: segments.filter(segment => segment.lane < maxLanes), hiddenCounts });
        }
        return weeks;
    }
    function splitSegmentForMonth(segment, days, month) {
        const parts = [];
        for (let index = segment.start; index <= segment.end; index += 1) {
            const inMonth = days[index].startsWith(month);
            const previous = parts.at(-1);
            if (previous && previous.inMonth === inMonth) previous.end = index;
            else parts.push({ start: index, end: index, inMonth });
        }
        return parts;
    }
    return { addDays, monthDays, moveMonth, onDay, layoutMonth, splitSegmentForMonth };
})();

if (typeof module !== "undefined" && module.exports) module.exports = MyPageCalendar;

if (typeof document !== "undefined") {
    const calendar = document.getElementById("mypage-calendar");
    if (calendar) {
        const events = JSON.parse(document.getElementById("mypage-calendar-data").textContent);
        const today = calendar.dataset.today;
        let month = today.slice(0, 7);
        let selectedDay = today;
        const names = { mentor: "メンター", room: "工作室", equipment: "器具" };
        const weekdays = ["日", "月", "火", "水", "木", "金", "土"];
        const detailTitle = document.getElementById("calendar-detail-title");
        const detailScroll = document.querySelector(".admin-mypage-page .admin-detail-scroll, .student-page.mypage-page .mypage-detail-scroll");
        const cards = new Map(events.map(event => [event.key, document.getElementById(`detail-${event.key}`)]));
        function element(tag, className, text) {
            const node = document.createElement(tag);
            if (className) node.className = className;
            if (text !== undefined) node.textContent = text;
            return node;
        }
        function button(className, text, action) {
            const node = element("button", className, text);
            node.type = "button";
            node.addEventListener("click", action);
            return node;
        }
        function showDay(day, key = null, focusDetails = false) {
            const dayChanged = selectedDay !== day;
            selectedDay = day;
            const matching = MyPageCalendar.onDay(events, day);
            const visibleKeys = new Set(matching.map(event => event.key));
            for (const [eventKey, card] of cards) card.hidden = !visibleKeys.has(eventKey);
            const detailContainer = document.getElementById("calendar-details");
            for (const event of matching) detailContainer.append(cards.get(event.key));
            detailTitle.textContent = `${day.replaceAll("-", "/")} の予約（${matching.length}件）`;
            document.getElementById("calendar-empty").hidden = matching.length !== 0;
            if (detailScroll && (dayChanged || !key)) detailScroll.scrollTop = 0;
            for (const dateButton of calendar.querySelectorAll(".calendar-day-hit")) {
                dateButton.setAttribute("aria-pressed", String(dateButton.dataset.day === day));
            }
            if (focusDetails) {
                const target = key ? cards.get(key) : detailTitle;
                target.focus({ preventScroll: true });
                const behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
                if (detailScroll && window.matchMedia("(min-width: 1200px) and (min-height: 600px)").matches) {
                    if (key) detailScroll.scrollTo({
                        top: detailScroll.scrollTop + target.getBoundingClientRect().top - detailScroll.getBoundingClientRect().top,
                        behavior,
                    });
                } else {
                    target.scrollIntoView({ block: "nearest", behavior });
                }
            }
        }
        function render() {
            calendar.replaceChildren();
            const [year, monthNumber] = month.split("-");
            document.getElementById("calendar-month").textContent = `${year}年${Number(monthNumber)}月`;
            const header = element("div", "calendar-weekdays");
            weekdays.forEach(day => header.append(element("span", "", day)));
            calendar.append(header);
            for (const week of MyPageCalendar.layoutMonth(events, month)) {
                const row = element("div", "calendar-week");
                week.days.forEach((day, index) => {
                    const inMonth = day.startsWith(month);
                    if (!inMonth) {
                        const background = element("div", "calendar-cell is-outside");
                        background.style.gridColumn = String(index + 1);
                        background.setAttribute("aria-hidden", "true");
                        background.append(element("span", "calendar-date calendar-date-outside", String(Number(day.slice(-2)))));
                        row.append(background);
                        if (week.hiddenCounts[index]) {
                            const more = element("span", "calendar-more is-outside-more", `他${week.hiddenCounts[index]}件`);
                            more.style.gridColumn = String(index + 1);
                            more.setAttribute("aria-label", `${day}にはほかに${week.hiddenCounts[index]}件の予約があります`);
                            row.append(more);
                        }
                        return;
                    }
                    const dateButton = button("calendar-cell calendar-day-hit", "", () => showDay(day));
                    dateButton.dataset.day = day;
                    dateButton.style.gridColumn = String(index + 1);
                    dateButton.setAttribute("aria-label", `${day}（${weekdays[index]}） ${MyPageCalendar.onDay(events, day).length}件の予約`);
                    if (day === today) dateButton.setAttribute("aria-current", "date");
                    dateButton.append(element("span", "calendar-date", String(Number(day.slice(-2)))));
                    row.append(dateButton);
                    if (week.hiddenCounts[index]) {
                        const more = button("calendar-more", `他${week.hiddenCounts[index]}件`, () => showDay(day, null, true));
                        more.style.gridColumn = String(index + 1);
                        more.setAttribute("aria-label", `${day}の全予約を表示（他${week.hiddenCounts[index]}件）`);
                        row.append(more);
                    }
                });
                week.segments.forEach(segment => {
                    const event = segment.event;
                    const label = `${event.startTime ? event.startTime + " " : ""}${names[event.category]} ${event.title}${event.kind === "takeout" ? "（持ち出し）" : event.kind === "equipment-room" ? "（工作室内）" : ""}${event.studentId ? ` ／ ${event.studentId}` : ""}`;
                    MyPageCalendar.splitSegmentForMonth(segment, week.days, month).forEach(part => {
                        const continuesBefore = event.start < week.days[part.start];
                        const continuesAfter = event.end > week.days[part.end];
                        const className = `calendar-event category-${event.category}${event.isMuted ? " is-muted" : ""}${part.inMonth ? "" : " is-outside-event"}${continuesBefore ? " continues-before" : ""}${continuesAfter ? " continues-after" : ""}`;
                        const text = `${continuesBefore ? "‹ " : ""}${label}${continuesAfter ? " ›" : ""}`;
                        let bar;
                        if (part.inMonth) {
                            bar = button(className, text, () => {
                                const day = selectedDay >= week.days[part.start] && selectedDay <= week.days[part.end] ? selectedDay : week.days[part.start];
                                showDay(day, event.key, true);
                            });
                        } else {
                            bar = element("div", className, text);
                        }
                        bar.style.gridColumn = `${part.start + 1} / ${part.end + 2}`;
                        bar.style.gridRow = String(segment.lane + 2);
                        bar.title = `${label} ${event.start}〜${event.end}${event.endTime ? " " + event.endTime : ""}`;
                        bar.setAttribute("aria-label", `${bar.title}${continuesBefore ? "、前から継続" : ""}${continuesAfter ? "、次へ継続" : ""}${part.inMonth ? "" : "、表示のみ"}`);
                        row.append(bar);
                    });
                });
                calendar.append(row);
            }
            showDay(selectedDay);
        }
        function changeMonth(offset) {
            month = MyPageCalendar.moveMonth(month, offset);
            selectedDay = month === today.slice(0, 7) ? today : `${month}-01`;
            render();
        }
        document.getElementById("calendar-prev").addEventListener("click", () => changeMonth(-1));
        document.getElementById("calendar-next").addEventListener("click", () => changeMonth(1));
        document.getElementById("calendar-today").addEventListener("click", () => {
            month = today.slice(0, 7); selectedDay = today; render();
        });
        render();
    }
    document.querySelectorAll(".reservation-cancel-form").forEach(form => {
        form.addEventListener("submit", event => {
            if (!confirm("本当にこの予約をキャンセルしますか？")) event.preventDefault();
        });
    });
    document.querySelectorAll("a.cancel-reservation").forEach(link => {
        link.addEventListener("click", event => {
            if (!confirm("本当にこの予約をキャンセルしますか？")) event.preventDefault();
        });
    });
}
