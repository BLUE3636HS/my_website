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
        const applySearch = () => {
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
        };
        searchInput.addEventListener("input", applySearch);
        if (searchInput.value.trim()) applySearch();
    }

    const highlightId = new URLSearchParams(window.location.search).get("study");
    if (highlightId) {
        const target = Array.from(document.querySelectorAll("[data-study-id]"))
            .find((row) => row.dataset.studyId === highlightId && !row.hidden);
        if (target) {
            target.classList.add("teacher-study-row-highlight");
            target.scrollIntoView({ block: "center" });
        }
    }
});
