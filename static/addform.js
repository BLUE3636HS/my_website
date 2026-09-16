"use strict";
const form = document.getElementById("study-form");
const button = document.getElementById("add_btn");
const statusMessage = document.getElementById("submission-status");
const templates = JSON.parse(document.getElementById("study-templates").textContent);
const selector = document.getElementById("template-id");
const fields = document.getElementById("template-fields");
const drafts = new Map();
let currentTemplate = null;
let sending = false;
let succeeded = false;

function generateFields() {
    if (currentTemplate !== null) {
        drafts.set(currentTemplate, Object.fromEntries(
            [...fields.querySelectorAll("textarea")].map(input => [input.name, input.value])));
    }
    fields.replaceChildren();
    currentTemplate = selector.value;
    const template = templates.find(item => String(item.id) === currentTemplate);
    for (const field of template?.fields || []) {
        const input = document.createElement("textarea");
        input.id = input.name = `field_${field.id}`;
        input.required = field.required;
        input.value = drafts.get(currentTemplate)?.[input.name] || "";
        if (field.max_length > 0) {
            const validateLength = () => {
                const count = Array.from(input.value.replace(/\r\n?/g, '\n')).length;
                input.setCustomValidity(count > field.max_length ? `${field.label}は${field.max_length}文字以内で入力してください。` : '');
            };
            input.addEventListener('input', validateLength);
            validateLength();
        }
        const label = document.createElement("label");
        label.htmlFor = input.id;
        label.textContent = field.label + (field.required ? "（必須）" : "（任意）");
        if (field.max_length > 0) label.textContent += `［${field.max_length}文字以内］`;
        fields.append(label, input);
    }
    updateMode();
}
function updateMode() {
    const isPdf = form.elements.registration_type.value === "pdf";
    document.getElementById("pdf-panel").hidden = !isPdf;
    document.getElementById("pdf_file").disabled = !isPdf;
    document.getElementById("template-panel").hidden = isPdf;
    selector.disabled = isPdf;
    selector.required = !isPdf;
    fields.querySelectorAll("textarea").forEach(input => { input.disabled = isPdf; });
}
selector.addEventListener("change", generateFields);
form.querySelectorAll('[name="registration_type"]').forEach(input => input.addEventListener("change", updateMode));
generateFields();
form.addEventListener("submit", async event => {
    event.preventDefault();
    if (sending || succeeded || !form.reportValidity()) return;
    sending = true;
    button.disabled = true;
    form.setAttribute("aria-busy", "true");
    statusMessage.textContent = "登録しています…";
    try {
        const response = await fetch(form.action, { method: "POST", body: new FormData(form) });
        const result = await response.json().catch(() => null);
        if (response.redirected || !response.ok || result?.ok !== true) {
            throw new Error(typeof result?.detail === "string" ? result.detail : "登録できませんでした。ログイン状態を確認して再度お試しください。");
        }
        succeeded = true;
        statusMessage.textContent = "研究成果を追加しました。";
        const next = document.createElement("a");
        next.href = "/addform";
        next.textContent = "続けて別の研究成果を追加";
        statusMessage.append(document.createTextNode(" "), next);
        button.textContent = "登録済み";
    } catch (error) {
        statusMessage.textContent = error.message || "通信に失敗しました。再度お試しください。";
    } finally {
        sending = false;
        button.disabled = succeeded;
        form.removeAttribute("aria-busy");
    }
});
