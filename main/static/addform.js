"use strict";
const form = document.getElementById("study-form");
const button = document.getElementById("add_btn");
const statusMessage = document.getElementById("submission-status");
let sending = false;
let succeeded = false;

function updateMode() {
    const selectedSubmissionKind = form.querySelector('input[name="submission_kind"]:checked');
    const isGroup = selectedSubmissionKind?.value === "group";
    const groupPanel = document.getElementById("group-submission-panel");
    const groupSelector = document.getElementById("chat-group-id");
    groupPanel.hidden = !isGroup;
    groupSelector.disabled = !isGroup;
    groupSelector.required = isGroup;
}
form.addEventListener("change", event => {
    if (event.target.matches('[name="submission_kind"]')) updateMode();
});
updateMode();
form.addEventListener("submit", async event => {
    event.preventDefault();
    if (sending || succeeded || !form.reportValidity()) return;
    sending = true;
    button.disabled = true;
    form.setAttribute("aria-busy", "true");
    statusMessage.textContent = "提出しています…";
    try {
        const response = await fetch(form.action, { method: "POST", body: new FormData(form) });
        const result = await response.json().catch(() => null);
        if (response.redirected || !response.ok || result?.ok !== true) {
            throw new Error(typeof result?.detail === "string" ? result.detail : "提出できませんでした。ログイン状態を確認して再度お試しください。");
        }
        succeeded = true;
        statusMessage.textContent = "PDFを提出しました。";
        const next = document.createElement("a");
        next.href = "/addform";
        next.textContent = "続けて別のPDFを提出";
        statusMessage.append(document.createTextNode(" "), next);
        button.textContent = "提出済み";
    } catch (error) {
        statusMessage.textContent = error.message || "通信に失敗しました。再度お試しください。";
    } finally {
        sending = false;
        button.disabled = succeeded;
        form.removeAttribute("aria-busy");
    }
});
