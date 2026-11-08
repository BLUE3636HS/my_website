// 先生・管理者用：スマホ幅でのサイドバー（ドロワー）開閉と、メニューのスクロール位置の保持
(function () {
    keepNavScroll(document.querySelector(".staff-nav"), "staff-nav-scroll:" + document.body.className);

    var layout = document.getElementById("staff-layout");
    var toggle = document.querySelector("[data-nav-open]");
    if (!layout || !toggle) return;
    function setOpen(open) {
        layout.classList.toggle("is-nav-open", open);
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
        if (open) {
            var first = layout.querySelector(".staff-sidebar a, .staff-sidebar button");
            if (first) first.focus({ preventScroll: true });
        }
    }
    toggle.addEventListener("click", function () { setOpen(true); });
    layout.querySelectorAll("[data-nav-close]").forEach(function (el) {
        el.addEventListener("click", function () { setOpen(false); toggle.focus(); });
    });
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && layout.classList.contains("is-nav-open")) { setOpen(false); toggle.focus(); }
    });

    // ページを移動してもメニューのスクロール位置が先頭に戻らないようにする
    function keepNavScroll(nav, key) {
        if (!nav) return;
        try {
            var saved = sessionStorage.getItem(key);
            if (saved !== null) nav.scrollTop = Number(saved) || 0;
        } catch (error) { /* ストレージが使えない環境では何もしない */ }
        // 保存位置がない・ずれている場合も、選択中の項目は見える位置に置く
        var active = nav.querySelector('[aria-current="page"]');
        if (active) {
            var navBox = nav.getBoundingClientRect();
            var itemBox = active.getBoundingClientRect();
            if (itemBox.top < navBox.top) nav.scrollTop -= navBox.top - itemBox.top + 8;
            else if (itemBox.bottom > navBox.bottom) nav.scrollTop += itemBox.bottom - navBox.bottom + 8;
        }
        function save() {
            try { sessionStorage.setItem(key, String(nav.scrollTop)); } catch (error) { /* 無視 */ }
        }
        nav.addEventListener("scroll", save, { passive: true });
        nav.addEventListener("click", save);
        window.addEventListener("pagehide", save);
    }
})();
