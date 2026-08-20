(() => {
    "use strict";
    const page = document.querySelector(".community-page");
    const timeline = document.querySelector("#community-timeline");
    const loading = document.querySelector("#community-loading");
    const end = document.querySelector("#community-end");
    const empty = document.querySelector("#community-empty");
    const sentinel = document.querySelector("#community-sentinel");
    const form = document.querySelector("#community-post-form");
    const content = document.querySelector("#community-content");
    const csrfToken = page.dataset.csrfToken;
    let cursor = null;
    let isLoading = false;
    let hasMore = true;

    function updateCounter(textarea, counter) {
        counter.textContent = `残り ${200 - textarea.value.length}文字`;
    }

    async function request(url, options = {}) {
        const response = await fetch(url, options);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || "処理に失敗しました。");
        return data;
    }

    function makeButton(label, className, handler) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `community-action ${className}`;
        button.textContent = label;
        button.addEventListener("click", handler);
        return button;
    }

    function ensureReplyToggle(container, replies) {
        let actions = container.querySelector(":scope > .community-actions");
        if (!actions) {
            actions = document.createElement("div");
            actions.className = "community-actions";
            container.append(actions);
        }
        let toggle = actions.querySelector(".community-replies-toggle");
        if (!toggle) {
            toggle = makeButton("", "community-replies-toggle", () => {
                replies.hidden = !replies.hidden;
                toggle.setAttribute("aria-expanded", String(!replies.hidden));
                toggle.textContent = replies.hidden
                    ? `返信を表示（${replies.children.length}件）`
                    : "返信を隠す";
            });
            toggle.setAttribute("aria-expanded", "false");
            actions.insertBefore(toggle, actions.querySelector(".community-delete"));
        }
        toggle.textContent = replies.hidden
            ? `返信を表示（${replies.children.length}件）`
            : "返信を隠す";
    }

    function createReplyForm(parentId, container) {
        const replyForm = document.createElement("form");
        replyForm.className = "community-reply-form";
        const textarea = document.createElement("textarea");
        textarea.maxLength = 200;
        textarea.rows = 3;
        textarea.required = true;
        textarea.setAttribute("aria-label", "返信本文");
        const footer = document.createElement("div");
        footer.className = "community-form-footer";
        const counter = document.createElement("span");
        counter.className = "community-counter";
        const submit = document.createElement("button");
        submit.type = "submit";
        submit.className = "student-primary-button";
        submit.textContent = "返信する";
        footer.append(counter, submit);
        replyForm.append(textarea, footer);
        updateCounter(textarea, counter);
        textarea.addEventListener("input", () => updateCounter(textarea, counter));
        replyForm.addEventListener("submit", async (event) => {
            event.preventDefault();
            submit.disabled = true;
            const body = new FormData();
            body.append("content", textarea.value);
            body.append("parent_id", parentId);
            body.append("csrf_token", csrfToken);
            try {
                const data = await request("/community/posts", {method: "POST", body});
                let replies = container.querySelector(":scope > .community-replies");
                if (!replies) {
                    replies = document.createElement("div");
                    replies.className = "community-replies";
                    replies.hidden = true;
                    container.append(replies);
                }
                replies.append(renderPost(data.post));
                ensureReplyToggle(container, replies);
                replyForm.remove();
            } catch (error) {
                window.alert(error.message);
            } finally {
                submit.disabled = false;
            }
        });
        return replyForm;
    }

    function renderPost(post) {
        const article = document.createElement("article");
        article.className = "community-post";
        article.dataset.postId = post.id;
        const header = document.createElement("header");
        header.className = "community-post-header";
        const author = document.createElement("span");
        author.className = "community-author";
        author.textContent = post.is_deleted ? "削除済み" : post.user_id;
        const time = document.createElement("time");
        time.textContent = post.created_at;
        header.append(author, time);
        const body = document.createElement("p");
        body.className = post.is_deleted ? "community-content community-deleted" : "community-content";
        body.textContent = post.is_deleted ? "この投稿は削除されました" : post.content;
        article.append(header, body);

        if (!post.is_deleted) {
            const actions = document.createElement("div");
            actions.className = "community-actions";
            const like = makeButton(`♡ ${post.like_count}`, `community-like${post.liked ? " is-liked" : ""}`, async () => {
                like.disabled = true;
                const requestBody = new FormData();
                requestBody.append("csrf_token", csrfToken);
                try {
                    const data = await request(`/community/posts/${post.id}/like`, {method: "POST", body: requestBody});
                    like.textContent = `♡ ${data.like_count}`;
                    like.classList.toggle("is-liked", data.liked);
                    like.setAttribute("aria-pressed", String(data.liked));
                } catch (error) { window.alert(error.message); }
                finally { like.disabled = false; }
            });
            like.setAttribute("aria-pressed", String(post.liked));
            const reply = makeButton("返信する", "community-reply", () => {
                const existing = article.querySelector(":scope > .community-reply-form");
                if (existing) existing.remove();
                else article.insertBefore(createReplyForm(post.id, article), article.querySelector(":scope > .community-replies"));
            });
            actions.append(like, reply);
            if (post.can_delete) {
                actions.append(makeButton("削除", "community-delete", async () => {
                    if (!window.confirm("この投稿を削除しますか？")) return;
                    const requestBody = new FormData();
                    requestBody.append("csrf_token", csrfToken);
                    try {
                        await request(`/community/posts/${post.id}/delete`, {method: "POST", body: requestBody});
                        author.textContent = "削除済み";
                        body.textContent = "この投稿は削除されました";
                        body.className = "community-content community-deleted";
                        actions.remove();
                    } catch (error) { window.alert(error.message); }
                }));
            }
            article.append(actions);
        }
        if (post.replies.length) {
            const replies = document.createElement("div");
            replies.className = "community-replies";
            replies.hidden = true;
            post.replies.forEach((item) => replies.append(renderPost(item)));
            article.append(replies);
            ensureReplyToggle(article, replies);
        }
        return article;
    }

    async function loadMore() {
        if (isLoading || !hasMore) return;
        isLoading = true;
        loading.hidden = false;
        try {
            const suffix = cursor === null ? "" : `?before_id=${encodeURIComponent(cursor)}`;
            const data = await request(`/community/posts${suffix}`);
            data.posts.forEach((post) => timeline.append(renderPost(post)));
            cursor = data.next_cursor;
            hasMore = data.has_more;
            empty.hidden = timeline.children.length !== 0;
            end.hidden = hasMore || timeline.children.length === 0;
        } catch (error) {
            loading.textContent = error.message;
        } finally {
            isLoading = false;
            if (loading.textContent === "読み込み中...") loading.hidden = true;
            if (hasMore && sentinel.getBoundingClientRect().top < window.innerHeight + 400) {
                queueMicrotask(loadMore);
            }
        }
    }

    content.addEventListener("input", () => updateCounter(content, form.querySelector("[data-counter]")));
    updateCounter(content, form.querySelector("[data-counter]"));
    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const submit = form.querySelector("button[type=submit]");
        const error = form.querySelector(".community-error");
        submit.disabled = true;
        error.hidden = true;
        const body = new FormData(form);
        body.append("csrf_token", csrfToken);
        try {
            const data = await request("/community/posts", {method: "POST", body});
            timeline.prepend(renderPost(data.post));
            content.value = "";
            updateCounter(content, form.querySelector("[data-counter]"));
            empty.hidden = true;
        } catch (requestError) {
            error.textContent = requestError.message;
            error.hidden = false;
        } finally { submit.disabled = false; }
    });
    new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) loadMore();
    }, {rootMargin: "400px 0px"}).observe(sentinel);
    loadMore();
})();
