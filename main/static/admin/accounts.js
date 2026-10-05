document.addEventListener("DOMContentLoaded", () => {
    const modal = document.getElementById("account-auth-modal");
    const dialog = modal?.querySelector(".account-auth-dialog");
    const typeInput = document.getElementById("delete-account-type");
    const rowidInput = document.getElementById("delete-account-rowid");
    const accountIdInput = document.getElementById("delete-account-id");
    const adminIdInput = document.getElementById("reauth-admin-id");
    const passwordInput = document.getElementById("reauth-admin-password");
    let lastTrigger = null;

    const clearSensitiveValues = () => {
        if (adminIdInput) adminIdInput.value = "";
        if (passwordInput) passwordInput.value = "";
    };

    const closeModal = () => {
        if (!modal || modal.hidden) return;
        modal.hidden = true;
        document.body.classList.remove("modal-open");
        clearSensitiveValues();
        lastTrigger?.focus();
    };

    const openModal = (button) => {
        if (!modal || !typeInput || !rowidInput || !accountIdInput || !adminIdInput) return;

        const confirmed = window.confirm("\u3053\u306e\u30a2\u30ab\u30a6\u30f3\u30c8\u3092\u524a\u9664\u3057\u307e\u3059\u304b\uff1f\n\u3053\u306e\u64cd\u4f5c\u306f\u5143\u306b\u623b\u305b\u307e\u305b\u3093\u3002");
        if (!confirmed) return;

        lastTrigger = button;
        typeInput.value = button.dataset.accountType || "";
        rowidInput.value = button.dataset.accountRowid || "";
        accountIdInput.value = button.dataset.accountId || "";
        clearSensitiveValues();
        modal.hidden = false;
        document.body.classList.add("modal-open");
        adminIdInput.focus();
    };

    document.querySelectorAll(".account-delete-button").forEach((button) => {
        button.addEventListener("click", () => openModal(button));
    });

    modal?.querySelectorAll("[data-auth-close]").forEach((element) => {
        element.addEventListener("click", closeModal);
    });

    document.addEventListener("keydown", (event) => {
        if (!modal || modal.hidden) return;

        if (event.key === "Escape") {
            event.preventDefault();
            closeModal();
            return;
        }

        if (event.key === "Tab" && dialog) {
            const focusable = [...dialog.querySelectorAll("button:not([disabled]), input:not([type='hidden']):not([disabled]), [tabindex]:not([tabindex='-1'])")];
            if (!focusable.length) return;
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
});
