"use strict";
const form = document.getElementById("study-form");
const button = document.getElementById("add_btn");
const statusMessage = document.getElementById("submission-status");
const templates = JSON.parse(document.getElementById("study-templates").textContent);
const selector = document.getElementById("template-id");
const fields = document.getElementById("template-fields");
const drafts = new Map();
const imageDrafts = new Map();
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
        if (field.field_type === "image") {
            renderImageField(field);
            continue;
        }
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
function templateImageDrafts() {
    if (!imageDrafts.has(currentTemplate)) imageDrafts.set(currentTemplate, new Map());
    return imageDrafts.get(currentTemplate);
}
function renderImageField(field) {
    const wrapper = document.createElement("fieldset");
    wrapper.className = "study-image-field";
    const legend = document.createElement("legend");
    legend.textContent = field.label + (field.required ? "（必須）" : "（任意）");
    const help = document.createElement("p");
    help.textContent = "JPEG・PNGを最大4枚、各5MBまで。各画像の説明は50文字以内で必須です。";
    const picker = document.createElement("input");
    picker.type = "file"; picker.accept = ".jpg,.jpeg,.png,image/jpeg,image/png"; picker.multiple = true;
    picker.setAttribute("aria-label", `${field.label}の画像を選択`);
    const list = document.createElement("div"); list.className = "study-image-list";
    const records = templateImageDrafts().get(field.id) || [];
    templateImageDrafts().set(field.id, records);
    function validate() {
        picker.setCustomValidity(field.required && records.length === 0 ? `「${field.label}」の画像を1枚以上選択してください。` : "");
    }
    function draw() {
        list.replaceChildren();
        records.forEach((record, index) => {
            const card = document.createElement("div"); card.className = "study-image-card";
            const image = document.createElement("img"); image.alt = "";
            const url = URL.createObjectURL(record.file); image.src = url; image.addEventListener("load", () => URL.revokeObjectURL(url), {once: true});
            const name = document.createElement("p"); name.textContent = record.file.name;
            const captionLabel = document.createElement("label");
            const caption = document.createElement("input"); caption.type = "text"; caption.required = true; caption.maxLength = 50;
            caption.value = record.caption; caption.id = `caption_${field.id}_${index}`;
            captionLabel.htmlFor = caption.id; captionLabel.textContent = `画像${index + 1}の説明（必須・50文字以内）`;
            caption.addEventListener("input", () => { record.caption = caption.value; });
            const actions = document.createElement("div"); actions.className = "study-image-actions";
            const action = (text, callback) => { const button = document.createElement("button"); button.type = "button"; button.textContent = text; button.addEventListener("click", callback); return button; };
            const up = action("↑ 上へ", () => { records.splice(index - 1, 0, records.splice(index, 1)[0]); draw(); }); up.disabled = index === 0;
            const down = action("↓ 下へ", () => { records.splice(index + 1, 0, records.splice(index, 1)[0]); draw(); }); down.disabled = index === records.length - 1;
            const remove = action("削除", () => { records.splice(index, 1); draw(); validate(); });
            actions.append(up, down, remove); card.append(image, name, captionLabel, caption, actions); list.append(card);
        });
        picker.disabled = records.length >= 4 || form.elements.registration_type.value === "pdf";
        validate();
    }
    picker.addEventListener("change", () => {
        const selected = [...picker.files];
        picker.value = "";
        if (records.length + selected.length > 4) {
            picker.setCustomValidity("画像は1項目につき最大4枚です。"); picker.reportValidity(); picker.setCustomValidity(""); return;
        }
        for (const file of selected) records.push({file, caption: ""});
        draw();
    });
    wrapper.append(legend, help, picker, list); fields.append(wrapper); draw();
}
function updateMode() {
    const isPdf = form.elements.registration_type.value === "pdf";
    document.getElementById("pdf-panel").hidden = !isPdf;
    document.getElementById("pdf_file").disabled = !isPdf;
    document.getElementById("template-panel").hidden = isPdf;
    selector.disabled = isPdf;
    selector.required = !isPdf;
    fields.querySelectorAll("textarea, input, button").forEach(input => { input.disabled = isPdf; });
    if (!isPdf) fields.querySelectorAll(".study-image-field").forEach(wrapper => {
        const picker = wrapper.querySelector('input[type="file"]');
        const cards = wrapper.querySelectorAll('.study-image-card').length;
        picker.disabled = cards >= 4;
        wrapper.querySelectorAll('input[type="text"], button').forEach(input => { input.disabled = false; });
    });
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
        const payload = new FormData(form);
        if (form.elements.registration_type.value === "template") {
            for (const [fieldId, records] of (imageDrafts.get(currentTemplate) || new Map())) {
                for (const record of records) {
                    payload.append(`image_${fieldId}`, record.file, record.file.name);
                    payload.append(`caption_${fieldId}`, record.caption);
                }
            }
        }
        const response = await fetch(form.action, { method: "POST", body: payload });
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
