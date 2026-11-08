let student_login_btn = document.getElementById("student_login_btn");
let student_id = document.getElementById("student_id");
let student_pwd = document.getElementById("student_pwd");
let teacher_login_btn = document.getElementById("teacher_login_btn");
let teacher_id = document.getElementById("teacher_id");
let teacher_pwd = document.getElementById("teacher_pwd");

let student_form_btn = document.getElementById("student_form_btn");
let teacher_form_btn = document.getElementById("teacher_form_btn");

let student_form = document.getElementById("student_form");
let teacher_form = document.getElementById("teacher_form");

const studentLoginMessage = document.getElementById("student-login-message");
const loginViewport = document.getElementById("auth-viewport");

function setLoginMode(isStudent) {
    document.body.classList.toggle("student-mode", isStudent);
    loginViewport.content = isStudent ? "width=device-width, initial-scale=1" : "";
    student_form_btn.setAttribute("aria-pressed", String(isStudent));
    teacher_form_btn.setAttribute("aria-pressed", String(!isStudent));
}

student_form.style.display = "block";

student_form_btn.addEventListener("click", () => {
    setLoginMode(true);
    student_form.style.display = "block";
    teacher_form.style.display = "none";

    student_form_btn.classList.add("active");
    teacher_form_btn.classList.remove("active");
});

teacher_form_btn.addEventListener("click", () => {
    setLoginMode(false);
    student_form.style.display = "none";
    teacher_form.style.display = "block";

    teacher_form_btn.classList.add("active");
    student_form_btn.classList.remove("active");
});

student_form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (student_login_btn.disabled) return;
    studentLoginMessage.textContent = "";
    student_login_btn.disabled = true;
    student_login_btn.textContent = "ログイン中…";
    const formData = new FormData();
    formData.append("type", "student");
    formData.append("id", student_id.value);
    formData.append("pwd", student_pwd.value);
    try {
        const response = await fetch("/login", {method: "POST", body: formData});
        const data = await response.json();
        if (data.result === true) {
            window.location.href = "/dashboard";
            return;
        }
        studentLoginMessage.textContent = "IDまたはパスワードを確認してください。";
    } catch {
        studentLoginMessage.textContent = "ログインできませんでした。時間をおいて再度お試しください。";
    } finally {
        student_login_btn.disabled = false;
        student_login_btn.textContent = "ログイン";
    }
});

teacher_login_btn.addEventListener("click", function(event){
    event.preventDefault();
    const form_data = new FormData();

    form_data.append("type", "teacher");
    form_data.append("id", teacher_id.value);
    form_data.append("pwd", teacher_pwd.value);

    fetch("/login", {
        method:"POST",
        body: form_data
    })
    .then(function(response){
        return response.json();
    })
    .then(function(data){
        if(data.result == true){
            window.location.href = "/teacher";
        }
        else{
            alert("ID または パスワード が違います");
        }
    });
});
