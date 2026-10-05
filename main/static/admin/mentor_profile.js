const mentorImageInput = document.getElementById("mentor-image-input");
const mentorImagePreview = document.getElementById("mentor-image-preview");
let mentorPreviewUrl = null;

if (mentorImageInput && mentorImagePreview) {
    mentorImageInput.addEventListener("change", () => {
        if (mentorPreviewUrl) {
            URL.revokeObjectURL(mentorPreviewUrl);
            mentorPreviewUrl = null;
        }

        const selectedImage = mentorImageInput.files?.[0];
        if (!selectedImage) {
            mentorImagePreview.src = mentorImagePreview.dataset.savedSrc;
            mentorImagePreview.alt = "現在のメンター画像";
            return;
        }

        mentorPreviewUrl = URL.createObjectURL(selectedImage);
        mentorImagePreview.src = mentorPreviewUrl;
        mentorImagePreview.alt = "選択したメンター画像のプレビュー";
    });

    window.addEventListener("pagehide", () => {
        if (mentorPreviewUrl) URL.revokeObjectURL(mentorPreviewUrl);
    });
}
