document.getElementById("school_registration_form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData();
    form.append("id", document.getElementById("school_student_id").value);
    form.append("pwd", document.getElementById("school_student_pwd").value);
    form.append("school_id", document.getElementById("school_id").value);

    const message = document.getElementById("school_registration_message");
    message.textContent = "確認しています…";
    try {
        const response = await fetch("/school-registration", { method: "POST", body: form });
        const result = await response.json();
        message.textContent = result.message || "入力内容を確認してください。";
        if (result.result === 0) {
            document.getElementById("school_registration_form").reset();
        }
    } catch (_) {
        message.textContent = "登録できませんでした。時間をおいて再度お試しください。";
    }
});
