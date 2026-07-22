let id = document.getElementById("id");
let pwd = document.getElementById("pwd");
let school = document.getElementById("school");
let reg_btn = document.getElementById("reg_btn");

reg_btn.addEventListener("click", function(event){
    event.preventDefault();
    const form_data = new FormData();

    form_data.append("id", id.value);
    form_data.append("pwd", pwd.value);
    form_data.append("school", school.value);

    fetch("/admin/registration", {
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
})
