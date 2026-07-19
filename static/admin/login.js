let log_btn = document.getElementById("log_btn");
let id = document.getElementById("id");
let pwd = document.getElementById("pwd");

log_btn.addEventListener("click", function(event){
    event.preventDefault();
    const form_data = new FormData();

    form_data.append("id", id.value);
    form_data.append("pwd", pwd.value);

    fetch("/admin/login", {
        method:"POST",
        body: form_data
    })
    .then(function(response){
        return response.json();
    })
    .then(function(data){
        if(data.result == true){
            window.location.href = "/admin";
        }
        else{
                alert("ID または パスワード が違います")
        }
    });
});