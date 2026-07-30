let day = document.getElementById("day").textContent;
let start_time = document.getElementById("start_time");
let end_time = document.getElementById("end_time");
let equipment = document.querySelectorAll(".equipment");
let purpose = document.getElementById("purpose");
let btn = document.getElementById("btn");

let selected_equipment = [];

//start_timeが変更されたとき、end_timeの選択肢を更新する
start_time.addEventListener("change", () => {
    let html = `<option value="" disabled selected>終了時間を選択してください</option>`;
    let [hour, minute] = start_time.value.split(":").map(Number);
    let total_minutes = hour * 60 + minute;
    for (let i = 0; i < 6; i++) {
            total_minutes += 30;

            const hour = Math.floor(total_minutes / 60);
            const minute = total_minutes % 60;

            const time =
                String(hour).padStart(2, "0") +
                ":" +
                String(minute).padStart(2, "0");

            html += `<option value="${time}"> ${time} </option>`;
        }
    end_time.innerHTML = html;
});

//ボタンが押されたときfastAPIにPOSTリクエストを送る
btn.addEventListener("click", () => {
    selected_equipment = [];
    equipment.forEach((equipment) => {
        if (equipment.checked) {
            selected_equipment.push(equipment.value);
        }
    });

    if(start_time.value == ""){
        alert("時間を選択してください");
    }
    else if(purpose.value == ""){
        alert("使用目的を入力してください");
    }
    else{
        const reservationDate = new Date(`${day} ${start_time.value}`);
        const now = new Date();

        if (reservationDate < now) {
            alert("過去の時間は選択できません");
            return;
        }
        else{
            const form_data = new FormData();

            form_data.append("day", day);
            form_data.append("start_time", start_time.value);
            form_data.append("end_time", end_time.value);
            form_data.append("equipment", JSON.stringify(selected_equipment));
            form_data.append("purpose", purpose.value);

            fetch("/reservation/date", {
                method:"POST",
                body: form_data
            })

            alert("予約が完了しました\nマイページで予約の確認ができます");
            location.reload();
        }
    }
});