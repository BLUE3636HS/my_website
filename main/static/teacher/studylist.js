document.addEventListener("DOMContentLoaded", () => {
    const page = document.querySelector(".teacher-studylist-page");
    if (page?.dataset.deleteSucceeded === "true") {
        window.alert("提出物を削除しました。");
    }

    document.querySelectorAll(".teacher-study-delete-form").forEach((form) => {
        form.addEventListener("submit", (event) => {
            const confirmed = window.confirm(
                "この提出物を削除しますか？\n削除したデータは元に戻せません。"
            );
            if (!confirmed) event.preventDefault();
        });
    });
});
