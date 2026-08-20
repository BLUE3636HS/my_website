const id_form_btn = document.getElementById("id_form_btn");
const pwd_form_btn = document.getElementById("pwd_form_btn");

const id_form = document.getElementById("id_form");
const pwd_form = document.getElementById("pwd_form");

const old_id = document.getElementById("old_id");
const new_id = document.getElementById("new_id");

const old_pwd = document.getElementById("old_pwd");
const new_pwd = document.getElementById("new_pwd");

const id_edit_btn = document.getElementById("id_edit_btn");
const pwd_edit_btn = document.getElementById("pwd_edit_btn");
const profileImageInput = document.getElementById("profile_image");
const profileImagePreview = document.getElementById("profile_image_preview");
const profileImageError = document.getElementById("profile_image_error");
let profilePreviewUrl = null;

if (profileImageInput) {
    profileImageInput.addEventListener("change", () => {
        const file = profileImageInput.files[0];
        profileImageError.hidden = true;
        if (!file) return;
        if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
            profileImageError.textContent = "対応していない画像形式です。";
            profileImageError.hidden = false;
            profileImageInput.value = "";
            return;
        }
        if (file.size > 5 * 1024 * 1024) {
            profileImageError.textContent = "ファイルサイズが5MBを超えています。";
            profileImageError.hidden = false;
            profileImageInput.value = "";
            return;
        }
        if (profilePreviewUrl) URL.revokeObjectURL(profilePreviewUrl);
        profilePreviewUrl = URL.createObjectURL(file);
        profileImagePreview.src = profilePreviewUrl;
    });
}

pwd_form.style.display = "none";

id_form_btn.addEventListener("click", () => {
    id_form.style.display = "block";
    pwd_form.style.display = "none";

    id_form_btn.classList.add("active");
    pwd_form_btn.classList.remove("active");
});

pwd_form_btn.addEventListener("click", () => {
    id_form.style.display = "none";
    pwd_form.style.display = "block";
    
    pwd_form_btn.classList.add("active");
    id_form_btn.classList.remove("active");
});

id_edit_btn.addEventListener("click", () => {
    const form_data = new FormData();
    form_data.append("old_id", old_id.value);
    form_data.append("old_pwd", old_pwd.value);
    form_data.append("new_id", new_id.value);

    fetch("/mypage/edit/id", {
        method: "POST",
        body: form_data
    })
    .then(function(response){
        return response.json();
    })
    .then(function(data){
        if(data.result == 0){
            alert("現在のID,またはパスワードが違います");
        }
        else if(data.result == 1){
            alert("IDがすでに使用されています");
        }
        else if(data.result == 2){
            alert("新しいIDが条件を満たしていません")
        }
        else if(data.result == 3){
            alert("IDを変更しました");
        }
    });
})

pwd_edit_btn.addEventListener("click", () => {
    const form_data = new FormData();
    form_data.append("old_id", old_id.value);
    form_data.append("old_pwd", old_pwd.value);
    form_data.append("new_pwd", new_pwd.value);

    fetch("/mypage/edit/pwd", {
        method: "POST",
        body: form_data
    })
    .then(function(response){
        return response.json();
    })
    .then(function(data){
        if(data.result == 0){
            alert("現在のID,またはパスワードが違います");
        }
        else if(data.result == 1){
            alert("新しいパスワードが条件を満たしていません");
        }
        else if(data.result == 2){
            alert("パスワードを変更しました")
        }
    });
})
