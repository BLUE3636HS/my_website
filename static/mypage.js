const del_reservation = document.querySelectorAll(".del_reservation");

del_reservation.forEach((btn) => {
    btn.addEventListener("click", (event) => {
        if (!confirm("本当にこの予約を削除しますか？")) {
            event.preventDefault();
        }
    });
});