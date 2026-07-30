const form = document.querySelector("#equipment-reservation-form");
const startDay = document.querySelector("#start_day");
const endDay = document.querySelector("#end_day");
const quantity = document.querySelector("#quantity");
const availability = document.querySelector("#availability");

function setLimits() {
    endDay.min = startDay.value;
    if (endDay.value < endDay.min) endDay.value = endDay.min;
    const max = new Date(`${startDay.value}T00:00:00`);
    max.setDate(max.getDate() + 6);
    endDay.max = max.toISOString().slice(0, 10);
    if (endDay.value > endDay.max) endDay.value = endDay.max;
}

async function getAvailability(equipment) {
    const params = new URLSearchParams({equipment, start_day: startDay.value, end_day: endDay.value});
    const response = await fetch(`/equipment-availability?${params}`);
    return response.ok ? response.json() : null;
}

async function updateAvailability() {
    const cards = [...form.querySelectorAll(".equipment-card")];
    const results = await Promise.all(cards.map(card => getAvailability(card.querySelector("input").value)));
    cards.forEach((card, index) => {
        const stock = card.querySelector(".equipment-stock");
        stock.textContent = results[index] ? `残り ${results[index].available}` : "在庫を確認できません";
    });

    const selected = form.querySelector('[name="equipment"]:checked');
    quantity.innerHTML = '<option value="">器具を選択してください</option>';
    if (!selected) return;
    const data = results[cards.indexOf(selected.closest(".equipment-card"))];
    if (!data) { availability.textContent = "利用可能数を確認できません。"; return; }
    for (let i = 1; i <= data.available; i++) quantity.insertAdjacentHTML("beforeend", `<option value="${i}">${i}</option>`);
    availability.textContent = data.available ? `利用可能数: ${data.available}` : "この期間は在庫がありません。";
}

startDay.addEventListener("change", () => { setLimits(); updateAvailability(); });
endDay.addEventListener("change", updateAvailability);
form.addEventListener("change", event => { if (event.target.name === "equipment") updateAvailability(); });
form.addEventListener("submit", async event => {
    event.preventDefault();
    const response = await fetch("/equipment-reservation", {method: "POST", body: new FormData(form)});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) return alert(data.detail || "予約を登録できませんでした。");
    alert("予約が完了しました。");
    location.href = "/mypage";
});
setLimits();
updateAvailability();
