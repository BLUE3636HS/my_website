let teacher_id = document.getElementById("teacher_id");
let teacher_pwd = document.getElementById("teacher_pwd");
let teacher_registration_form = document.getElementById("teacher_registration_form");

let student_form_btn = document.getElementById("student_form_btn");
let teacher_form_btn = document.getElementById("teacher_form_btn");

let registration_page = document.querySelector(".registration-page");

function setRegistrationMode(mode) {
    const isStudent = mode === "student";
    registration_page.classList.toggle("student-mode", isStudent);
    registration_page.classList.toggle("teacher-mode", !isStudent);
    student_form_btn.classList.toggle("active", isStudent);
    teacher_form_btn.classList.toggle("active", !isStudent);
    student_form_btn.setAttribute("aria-selected", String(isStudent));
    teacher_form_btn.setAttribute("aria-selected", String(!isStudent));
}

student_form_btn.addEventListener("click", () => {
    setRegistrationMode("student");
});

teacher_form_btn.addEventListener("click", () => {
    setRegistrationMode("teacher");
});

teacher_registration_form.addEventListener("submit", function(event){
    event.preventDefault();
    const form_data = new FormData();

    form_data.append("type", "teacher");
    form_data.append("id", teacher_id.value);
    form_data.append("pwd", teacher_pwd.value);
    form_data.append("school_id", document.getElementById("teacher_school_id").value);

    fetch("/registration", {
        method: "POST",
        body: form_data
    })
    .then(async function(response){
        const data = await response.json();
        if (!response.ok) throw new Error(data.message || "入力内容を確認してください。");
        return data;
    })
    .then(function(data){
        document.getElementById("teacher_registration_message").textContent = data.message || "登録しました";
        if (data.result === 0) teacher_registration_form.reset();
    })
    .catch(function(error){
        document.getElementById("teacher_registration_message").textContent = error.message || "登録できませんでした。時間をおいて再度お試しください。";
    });
});
