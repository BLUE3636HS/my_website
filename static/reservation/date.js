let day = document.getElementById("day").textContent;
let time = document.getElementById("time");
let equipment = document.querySelectorAll(".equipment");
let purpose = document.getElementById("purpose");
let btn = document.getElementById("btn");

let selected_equipment = [];


btn.addEventListener("click", () => {
    selected_equipment = [];
    equipment.forEach((equipment) => {
        if (equipment.checked) {
            selected_equipment.push(equipment.value);
        }
    });

    if(time.value == ""){
        alert("時間を選択してください");
    }
    else if(purpose.value == ""){
        alert("使用目的を入力してください");
    }
    else{
        const reservationDate = new Date(`${day} ${time.value}`);
        const now = new Date();

        if (reservationDate < now) {
            alert("過去の時間は選択できません");
            return;
        }
        else{
            const form_data = new FormData();

            form_data.append("day", day);
            form_data.append("time", time.value);
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