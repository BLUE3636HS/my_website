(() => {
    const targetType = document.getElementById("teacher-notification-target");
    const studentTarget = document.getElementById("teacher-student-target");
    const studentId = document.getElementById("teacher-student-id");
    if (!targetType || !studentTarget || !studentId) return;

    const updateTarget = () => {
        const targetsStudent = targetType.value === "student";
        studentTarget.hidden = !targetsStudent;
        studentId.disabled = !targetsStudent;
        studentId.required = targetsStudent;
    };
    targetType.addEventListener("change", updateTarget);
    updateTarget();
})();
