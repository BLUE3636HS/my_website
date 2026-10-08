let student_reg_btn = document.getElementById("student_reg_btn");
let student_id = document.getElementById("student_id");
let student_pwd = document.getElementById("student_pwd");
let teacher_reg_btn = document.getElementById("teacher_reg_btn");
let teacher_id = document.getElementById("teacher_id");
let teacher_pwd = document.getElementById("teacher_pwd");

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

student_reg_btn.addEventListener("click", function(event){
    event.preventDefault();
    const form_data = new FormData();
    
    form_data.append("type", "student");
    form_data.append("id", student_id.value);
    form_data.append("pwd", student_pwd.value);
    form_data.append("email", document.getElementById("student_email").value);

    fetch("/registration", {
        method: "POST",
        body: form_data
    })
    .then(function(response){
        return response.json();
    })
    .then(function(data){
        document.getElementById("student_registration_message").textContent = data.message || "未実装の機能です";
    })
    .catch(function(){
        document.getElementById("student_registration_message").textContent = "未実装の機能です";
    });
});

teacher_reg_btn.addEventListener("click", function(event){
    event.preventDefault();
    const form_data = new FormData();

    form_data.append("type", "teacher");
    form_data.append("id", teacher_id.value);
    form_data.append("pwd", teacher_pwd.value);
    form_data.append("school", teacher_school.value);

    fetch("/registration", {
        method: "POST",
        body: form_data
    })
    .then(function(response){
        return response.json();
    })
    .then(function(data){
        if(data.result == 0){
            alert("登録しました")
        }
        else if(data.result == 2){
            alert("IDがすでに使用されています\n変更してください")
        }
        else{
            alert("条件を満たしていません")
        }
    });
});
