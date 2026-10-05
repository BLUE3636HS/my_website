let student_reg_btn = document.getElementById("student_reg_btn");
let student_id = document.getElementById("student_id");
let student_pwd = document.getElementById("student_pwd");
let teacher_reg_btn = document.getElementById("teacher_reg_btn");
let teacher_id = document.getElementById("teacher_id");
let teacher_pwd = document.getElementById("teacher_pwd");

let student_form_btn = document.getElementById("student_form_btn");
let teacher_form_btn = document.getElementById("teacher_form_btn");

let student_form = document.getElementById("student_form");
let teacher_form = document.getElementById("teacher_form");

student_form.style.display = "block";

student_form_btn.addEventListener("click", () => {
    student_form.style.display = "block";
    teacher_form.style.display = "none";

    student_form_btn.classList.add("active");
    teacher_form_btn.classList.remove("active");
});

teacher_form_btn.addEventListener("click", () => {
    student_form.style.display = "none";
    teacher_form.style.display = "block";

    teacher_form_btn.classList.add("active");
    student_form_btn.classList.remove("active");
});

student_reg_btn.addEventListener("click", function(event){
    event.preventDefault();
    const form_data = new FormData();
    
    form_data.append("type", "student");
    form_data.append("id", student_id.value);
    form_data.append("pwd", student_pwd.value);
    form_data.append("school", student_school.value);

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
