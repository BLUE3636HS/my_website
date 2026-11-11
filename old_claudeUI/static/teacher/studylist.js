document.addEventListener("DOMContentLoaded", () => {
    const page = document.querySelector(".teacher-studylist-page");
    if (page?.dataset.deleteSucceeded === "true") {
        window.alert("提出物を削除しました。");
    }

    document.querySelectorAll(".teacher-study-delete-form").forEach((form) => {
        form.addEventListener("submit", (event) => {
            const confirmed = window.confirm(
                "この提出物を削除しますか？\n削除したデータは元に戻せません。"
            );
            if (!confirmed) event.preventDefault();
        });
    });

    const searchInput = document.querySelector("#teacher-study-search");
    const tableBody = document.querySelector(".teacher-studylist-card tbody");
    if (searchInput && tableBody) {
        const rows = Array.from(tableBody.querySelectorAll("[data-searchable-row]"));
        const noResults = tableBody.querySelector(".teacher-study-no-results");
        searchInput.addEventListener("input", () => {
            const query = searchInput.value.trim().toLocaleLowerCase();
            let visibleCount = 0;
            rows.forEach((row) => {
                const searchableText = Array.from(row.cells)
                    .slice(0, 5)
                    .map((cell) => cell.textContent)
                    .join(" ")
                    .toLocaleLowerCase();
                const matches = !query || searchableText.includes(query);
                row.hidden = !matches;
                if (matches) visibleCount += 1;
            });
            if (noResults) noResults.hidden = visibleCount !== 0 || query === "";
        });
    }
});
