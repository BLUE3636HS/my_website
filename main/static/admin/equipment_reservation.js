document.querySelectorAll(".equipment-cancel-form").forEach((form) => {
    form.addEventListener("submit", (event) => {
        const label = form.dataset.reservationLabel || "この予約";
        const confirmed = window.confirm(`${label}を取り消しますか？\n取り消した予約は元に戻せません。`);
        if (!confirmed) {
            event.preventDefault();
        }
    });
});
