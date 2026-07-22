let add_btn = document.getElementById("add_btn");
const name_input = document.getElementById("name");
const introduce_input = document.getElementById("introduce");
const pdf_input = document.getElementById("pdf_file");

add_btn.addEventListener("click", function(event){
    event.preventDefault();

    //空欄がないかを確認
    if( 
        name_input.value === "" ||
        introduce_input.value === "" ||
        pdf_input.files.length === 0
    ){
        alert("すべての要素を入力してください");
        return;
    }

    alert("研究成果を追加しました");
    
    const form_data = new FormData();

    form_data.append("name", name_input.value);
    form_data.append("introduce", introduce_input.value);
    form_data.append("pdf", pdf_input.files[0]);

    fetch("/addform", {
        method:"POST",
        body: form_data
    });
});