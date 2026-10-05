document.addEventListener("DOMContentLoaded", () => {
    const modal = document.getElementById("student-modal");
    const modalDialog = modal?.querySelector(".student-modal-dialog");
    const studentId = document.getElementById("student-modal-id");
    const studentSchool = document.getElementById("student-modal-school");
    const foundContent = document.getElementById("student-modal-found");
    const missingContent = document.getElementById("student-modal-missing");
    let lastTrigger = null;

    const closeModal = () => {
        if (!modal || modal.hidden) return;
        modal.hidden = true;
        document.body.classList.remove("modal-open");
        lastTrigger?.focus();
    };

    const openModal = (trigger) => {
        if (!modal || !studentId || !studentSchool || !foundContent || !missingContent) return;

        lastTrigger = trigger;
        const found = trigger.dataset.studentFound === "true";
        foundContent.hidden = !found;
        missingContent.hidden = found;
        studentId.textContent = found ? trigger.dataset.studentId : "";
        studentSchool.textContent = found ? trigger.dataset.studentSchool : "";

        modal.hidden = false;
        document.body.classList.add("modal-open");
        modal.querySelector("[data-modal-close]")?.focus();
    };

    document.querySelectorAll(".studies-user-button").forEach((button) => {
        button.addEventListener("click", () => openModal(button));
    });

    modal?.querySelectorAll("[data-modal-close]").forEach((element) => {
        element.addEventListener("click", closeModal);
    });

    document.addEventListener("keydown", (event) => {
        if (!modal || modal.hidden) return;

        if (event.key === "Escape") {
            event.preventDefault();
            closeModal();
            return;
        }

        if (event.key === "Tab" && modalDialog) {
            const focusable = [...modalDialog.querySelectorAll("button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])")];
            if (focusable.length === 0) return;
            const first = focusable[0];
            const last = focusable[focusable.length - 1];

            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        }
    });

    document.querySelectorAll(".study-delete-form").forEach((form) => {
        form.addEventListener("submit", (event) => {
            const confirmed = window.confirm("この研究成果を削除しますか？\n削除したデータは元に戻せません。");
            if (!confirmed) event.preventDefault();
        });
    });
});
