const menuToggle = document.querySelector(".student-menu-toggle");
const studentSidebar = document.getElementById("student-sidebar");
const mobilePortal = matchMedia("(max-width: 800px)");

function setStudentMenu(open, restoreFocus = false) {
    menuToggle.setAttribute("aria-expanded", String(open));
    studentSidebar.classList.toggle("is-open", open);
    if (open) (studentSidebar.querySelector('[aria-current="page"]') || studentSidebar.querySelector("a")).focus();
    if (restoreFocus) menuToggle.focus();
}

menuToggle.addEventListener("click", () => {
    const open = menuToggle.getAttribute("aria-expanded") === "true";
    setStudentMenu(!open, open);
});
document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && studentSidebar.classList.contains("is-open")) {
        setStudentMenu(false, true);
    }
});
document.addEventListener("pointerdown", (event) => {
    if (studentSidebar.classList.contains("is-open") &&
        !studentSidebar.contains(event.target) && !menuToggle.contains(event.target)) {
        setStudentMenu(false, studentSidebar.contains(document.activeElement));
    }
});
mobilePortal.addEventListener("change", () => setStudentMenu(false));
