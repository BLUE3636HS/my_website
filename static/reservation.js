function createCalendar() {
    html = `
        <table border='1'>
        <tr>
            <th>日</th>
            <th>月</th>
            <th>火</th>
            <th>水</th>
            <th>木</th>
            <th>金</th>
            <th>土</th>
        </tr>
    `;
    let first_day = new Date(Number(year.textContent), Number(month.textContent) - 1, 1).getDay();
    let len_day = new Date(Number(year.textContent), Number(month.textContent), 0).getDate();

    for (let i = 1; true; i++){
        if(i < first_day + 1){
            html += "<td></td>";
        }
        else if(i <= len_day + first_day){
            const month_text = String(month.textContent).padStart(2, "0");
            const day_text = String(i - first_day).padStart(2, "0");

            html += "<td><a href='/reservation/"
                + year.textContent
                + "/"
                + month_text
                + "/"
                + day_text
                + "' id = "
                + (i - first_day)
                + ">"
                + (i - first_day)
                + "</a></td>";
        }
        else{
            html += "<td></td>";
        }
        if(i % 7 == 0){
            if(i > len_day + first_day){
                break;
            }
            html += "</tr><tr>";
        }
    }

    calendar.innerHTML = html;
}

let calendar = document.getElementById("calendar");
let year = document.getElementById("year");
let month = document.getElementById("month");
let dec_month_btn = document.getElementById("dec_month_btn");
let inc_month_btn = document.getElementById("inc_month_btn");

year.textContent = new Date().getFullYear();
month.textContent = new Date().getMonth() + 1;

html = `
    <table border='1'>
    <tr>
        <th>日</th>
        <th>月</th>
        <th>火</th>
        <th>水</th>
        <th>木</th>
        <th>金</th>
        <th>土</th>
    </tr>
`;

createCalendar();

dec_month_btn.addEventListener("click", () => {
    month.textContent = Number(month.textContent) - 1;
    if(Number(month.textContent) == 0){
        month.textContent = 12;
        year.textContent = Number(year.textContent) - 1;
    }
    createCalendar();
});

inc_month_btn.addEventListener("click", () => {
    month.textContent = Number(month.textContent) + 1;
    if(Number(month.textContent) == 13){
        month.textContent = 1;
        year.textContent = Number(year.textContent) + 1;
    }
    createCalendar();
});