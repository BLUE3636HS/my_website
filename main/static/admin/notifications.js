(() => {
    const type = document.getElementById("target-type");
    const student = document.getElementById("student-target");
    const school = document.getElementById("school-target");
    const studentInput = document.getElementById("student-id");
    const schoolSelect = school?.querySelector("select");
    if (!type || !student || !school || !studentInput || !schoolSelect) return;
    const update = () => {
        const usesStudent = type.value === "student";
        const usesSchool = type.value === "school";
        student.hidden = !usesStudent;
        school.hidden = !usesSchool;
        studentInput.disabled = !usesStudent;
        studentInput.required = usesStudent;
        schoolSelect.disabled = !usesSchool;
        schoolSelect.required = usesSchool;
    };
    type.addEventListener("change", update);
    update();
})();
