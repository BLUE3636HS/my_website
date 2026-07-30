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
            const day = i - first_day;
            const month_text = String(month.textContent).padStart(2, "0");
            const day_text = String(day).padStart(2, "0");
            const target_date = new Date(
                Number(year.textContent),
                Number(month.textContent) - 1,
                day
            );

            if (target_date < today) {
                html += "<td class='past_day'><span class='disabled_day'>"
                    + day
                    + "</span></td>";
            }
            else {
                html += "<td><a href='/reservation/"
                    + year.textContent
                    + "/"
                    + month_text
                    + "/"
                    + day_text
                    + "' id = "
                    + day
                    + ">"
                    + day
                    + "</a></td>";
            }
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

function updateDecMonthButtonState() {
    const display_year = Number(year.textContent);
    const display_month = Number(month.textContent);

    dec_month_btn.disabled =
        display_year < today.getFullYear() ||
        (
            display_year === today.getFullYear() &&
            display_month <= today.getMonth() + 1
        );
}

let calendar = document.getElementById("calendar");
let year = document.getElementById("year");
let month = document.getElementById("month");
let dec_month_btn = document.getElementById("dec_month_btn");
let inc_month_btn = document.getElementById("inc_month_btn");
let today = new Date();
today = new Date(today.getFullYear(), today.getMonth(), today.getDate());

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
updateDecMonthButtonState();

dec_month_btn.addEventListener("click", () => {
    if (dec_month_btn.disabled) {
        return;
    }

    month.textContent = Number(month.textContent) - 1;
    if(Number(month.textContent) == 0){
        month.textContent = 12;
        year.textContent = Number(year.textContent) - 1;
    }
    createCalendar();
    updateDecMonthButtonState();
});

inc_month_btn.addEventListener("click", () => {
    month.textContent = Number(month.textContent) + 1;
    if(Number(month.textContent) == 13){
        month.textContent = 1;
        year.textContent = Number(year.textContent) + 1;
    }
    createCalendar();
    updateDecMonthButtonState();
});