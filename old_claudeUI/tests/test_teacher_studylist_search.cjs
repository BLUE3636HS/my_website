const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

test("teacher study list search filters visible data columns and reports empty results", () => {
    const makeRow = (cells) => ({ cells: cells.map((textContent) => ({ textContent })), hidden: false });
    const first = makeRow(["Alpha", "First abstract", "2026-10-01 09:00:00", "student-a", "First.PDF", "delete-only"]);
    const second = makeRow(["Beta", "Second abstract", "2026-10-02 09:00:00", "Group Beta", "Second.PDF", "delete-only"]);
    const rows = [first, second];
    const noResults = { hidden: true };
    const handlers = {};
    const searchInput = {
        value: "",
        addEventListener: (name, handler) => { handlers[name] = handler; },
    };
    const tableBody = {
        querySelectorAll: (selector) => selector === "[data-searchable-row]" ? rows : [],
        querySelector: (selector) => selector === ".teacher-study-no-results" ? noResults : null,
    };
    const page = { dataset: { deleteSucceeded: "false" } };
    const document = {
        addEventListener: (name, callback) => { if (name === "DOMContentLoaded") callback(); },
        querySelector: (selector) => ({
            ".teacher-studylist-page": page,
            "#teacher-study-search": searchInput,
            ".teacher-studylist-card tbody": tableBody,
        })[selector] || null,
        querySelectorAll: () => [],
    };

    const scriptPath = path.join(__dirname, "..", "static", "teacher", "studylist.js");
    vm.runInNewContext(fs.readFileSync(scriptPath, "utf8"), {
        document,
        window: { alert() {}, confirm: () => true },
    });

    const search = (value) => {
        searchInput.value = value;
        handlers.input();
    };
    for (const query of ["alp", "FIRST ABSTRACT", "2026-10-01", "student-a", "first.pdf"]) {
        search(query);
        assert.deepEqual(rows.map((row) => row.hidden), [false, true], `query: ${query}`);
    }
    search("group beta");
    assert.deepEqual(rows.map((row) => row.hidden), [true, false]);
    search("delete-only");
    assert.deepEqual(rows.map((row) => row.hidden), [true, true]);
    assert.equal(noResults.hidden, false);
    search("");
    assert.deepEqual(rows.map((row) => row.hidden), [false, false]);
    assert.equal(noResults.hidden, true);
});
