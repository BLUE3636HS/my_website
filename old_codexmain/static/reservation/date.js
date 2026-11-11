let day = document.getElementById("day").textContent;

let equipment = document.querySelectorAll(".equipment");
let purpose = document.getElementById("purpose");
let btn = document.getElementById("btn");

let reservation_status = document.getElementById("reservation_status");
let selected_start = document.getElementById("selected_start");
let selected_end = document.getElementById("selected_end");
let time_message = document.getElementById("time_message");

let selected_equipment = [];

let is_dragging = false;
let drag_start_time = null;


// =========================
// 時間 → 分
// =========================

function timeToMinutes(time) {

    let [hour, minute] = time.split(":").map(Number);

    return hour * 60 + minute;
}


// =========================
// 分 → 時間
// =========================

function minutesToTime(total_minutes) {

    const hour = Math.floor(total_minutes / 60);
    const minute = total_minutes % 60;

    return (
        String(hour).padStart(2, "0") +
        ":" +
        String(minute).padStart(2, "0")
    );
}


// =========================
// 予約状況を作成
// =========================

document.addEventListener("DOMContentLoaded", () => {

    let html = "";

    let total_minutes = 540; // 09:00

    for (let i = 0; i < 24; i++) {

        const time = minutesToTime(total_minutes);

        if (reserved_times.includes(time)) {

            html += `
                <div
                    class="reservation_time reserved"
                    data-time="${time}"
                >
                    <span>${time}</span>
                    <span>予約済み</span>
                </div>
            `;

        } else {

            html += `
                <div
                    class="reservation_time available"
                    data-time="${time}"
                >
                    <span>${time}</span>
                    <span>空き</span>
                </div>
            `;
        }

        total_minutes += 30;
    }

    reservation_status.innerHTML = html;


    // =========================
    // 時間のドラッグ操作
    // =========================

    const time_buttons =
        document.querySelectorAll(".reservation_time");


    time_buttons.forEach((button) => {


        // -------------------------
        // 左クリックを押した
        // -------------------------

        button.addEventListener("mousedown", (event) => {

            // 左クリック以外は無視
            if (event.button !== 0) {
                return;
            }

            // 予約済みは選択不可
            if (button.classList.contains("reserved")) {
                return;
            }

            is_dragging = true;

            drag_start_time = button.dataset.time;

            clearSelection();

            button.classList.add("drag_start");

            selected_start.textContent = drag_start_time;

            selected_end.textContent = "未選択";

            time_message.textContent =
                "終了時間までドラッグしてください";

        });


        // -------------------------
        // カーソルを時間の上に移動
        // -------------------------

        button.addEventListener("mouseenter", () => {

            if (!is_dragging) {
                return;
            }

            updateSelection(button.dataset.time);

        });


        // -------------------------
        // 左クリックを離した
        // -------------------------

        button.addEventListener("mouseup", (event) => {

            if (event.button !== 0) {
                return;
            }

            if (!is_dragging) {
                return;
            }

            const end_time = button.dataset.time;

            if (checkSelection(end_time)) {

                selected_end.textContent = end_time;

                time_message.textContent =
                    "予約時間を確認してください";

            }

            is_dragging = false;

        });

    });


    // -------------------------
    // 表の外でマウスを離した場合
    // -------------------------

    document.addEventListener("mouseup", () => {

        if (!is_dragging) {
            return;
        }

        is_dragging = false;

    });

});


// =========================
// 選択範囲を更新
// =========================

function updateSelection(current_time) {

    if (!drag_start_time) {
        return;
    }


    const start_minutes =
        timeToMinutes(drag_start_time);

    const current_minutes =
        timeToMinutes(current_time);


    // 開始時間より上に戻った場合
    if (current_minutes < start_minutes) {

        clearSelection();

        document
            .querySelector(
                `[data-time="${drag_start_time}"]`
            )
            .classList.add("drag_start");

        return;
    }


    // 3時間を超える場合
    if (current_minutes - start_minutes > 180) {

        return;
    }


    // 予約済み時間が途中にある場合
    for (
        let time = start_minutes;
        time < current_minutes;
        time += 30
    ) {

        const check_time =
            minutesToTime(time);

        if (reserved_times.includes(check_time)) {

            return;
        }
    }


    clearSelection();


    // 選択範囲を青くする
    document
        .querySelectorAll(".reservation_time")
        .forEach((button) => {

            const button_time =
                timeToMinutes(button.dataset.time);

            if (
                button_time >= start_minutes &&
                button_time <= current_minutes
            ) {

                button.classList.add("selected");

            }

        });


    document
        .querySelector(
            `[data-time="${drag_start_time}"]`
        )
        .classList.add("drag_start");


    selected_start.textContent =
        drag_start_time;

    selected_end.textContent =
        current_time;

}


// =========================
// 選択範囲を確定できるか確認
// =========================

function checkSelection(end_time) {

    const start_minutes =
        timeToMinutes(drag_start_time);

    const end_minutes =
        timeToMinutes(end_time);


    if (end_minutes <= start_minutes) {

        alert(
            "終了時間は開始時間より後にしてください"
        );

        clearSelection();

        selected_start.textContent = "未選択";
        selected_end.textContent = "未選択";

        return false;
    }


    // 3時間制限
    if (end_minutes - start_minutes > 180) {

        alert("予約は3時間までです");

        clearSelection();

        selected_start.textContent = "未選択";
        selected_end.textContent = "未選択";

        return false;
    }


    // 途中に予約済みがないか確認
    for (
        let time = start_minutes;
        time < end_minutes;
        time += 30
    ) {

        const check_time =
            minutesToTime(time);

        if (reserved_times.includes(check_time)) {

            alert(
                "予約済みの時間を含むため、この時間帯は選択できません"
            );

            clearSelection();

            selected_start.textContent = "未選択";
            selected_end.textContent = "未選択";

            return false;
        }
    }


    return true;
}


// =========================
// 選択状態を解除
// =========================

function clearSelection() {

    document
        .querySelectorAll(".reservation_time")
        .forEach((button) => {

            button.classList.remove("selected");
            button.classList.remove("drag_start");

        });

}


// =========================
// 予約ボタン
// =========================

btn.addEventListener("click", () => {

    selected_equipment = [];


    equipment.forEach((equipment) => {

        if (equipment.checked) {

            selected_equipment.push(
                equipment.value
            );

        }

    });


    if (selected_start.textContent === "未選択") {

        alert("開始時間を選択してください");

        return;
    }


    if (selected_end.textContent === "未選択") {

        alert("終了時間を選択してください");

        return;
    }


    if (purpose.value === "") {

        alert("使用目的を入力してください");

        return;
    }


    const reservationDate =
        new Date(
            `${day} ${selected_start.textContent}`
        );

    const now = new Date();


    if (reservationDate < now) {

        alert("過去の時間は選択できません");

        return;
    }


    const form_data = new FormData();

    form_data.append(
        "day",
        day
    );

    form_data.append(
        "start_time",
        selected_start.textContent
    );

    form_data.append(
        "end_time",
        selected_end.textContent
    );

    form_data.append(
        "equipment",
        JSON.stringify(selected_equipment)
    );

    form_data.append(
        "purpose",
        purpose.value
    );


    fetch("/reservation/date", {

        method: "POST",

        body: form_data

    });


    alert(
        "予約が完了しました\n" +
        "マイページで予約の確認ができます"
    );

    location.reload();

});