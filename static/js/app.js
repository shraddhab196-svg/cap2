// Micro-interactions for every page. Vanilla, no dependencies; every feature is progressive enhancement.
(() => {
    const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const finePointer = matchMedia("(pointer: fine)").matches;
    const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

    // --- Busy states. data-loading: spinner on the button. data-overlay="a|b|c": ink overlay with rotating status lines.
    const overlay = document.querySelector(".ink-overlay");
    const status = overlay && overlay.querySelector("[data-ink-status]");
    const hint = overlay && overlay.querySelector(".ink-hint");
    const defaultHint = hint && hint.textContent;
    let ticker;
    let slowTimer;

    document.addEventListener("submit", (event) => {
        const form = event.target;
        if (event.defaultPrevented || !(form.hasAttribute("data-loading") || form.hasAttribute("data-overlay"))) return;
        const button = event.submitter || form.querySelector('[type="submit"]');
        if (button) {
            button.setAttribute("aria-busy", "true");
            // Disable after the submit has been dispatched so the request still goes out.
            setTimeout(() => { button.disabled = true; });
        }
        const lines = (form.dataset.overlay || "").split("|").filter(Boolean);
        if (!overlay || !lines.length) return;
        const rect = (button || form).getBoundingClientRect();
        overlay.style.setProperty("--x", `${rect.left + rect.width / 2}px`);
        overlay.style.setProperty("--y", `${rect.top + rect.height / 2}px`);
        let index = 0;
        status.textContent = lines[0];
        clearInterval(ticker);
        ticker = setInterval(() => {
            if (index === lines.length - 1) return clearInterval(ticker); // ponytail: hold the last line, no fake looping
            status.classList.add("is-swapping");
            setTimeout(() => {
                status.textContent = lines[++index];
                status.classList.remove("is-swapping");
            }, 360);
        }, 3200);
        document.body.classList.add("is-working");
        clearTimeout(slowTimer);
        slowTimer = setTimeout(() => {
            if (hint) hint.textContent = "Taking longer than usual · still working, no need to refresh";
        }, 45000);
    });

    // Back/forward cache restores the page mid-"working": reset it.
    addEventListener("pageshow", (event) => {
        if (!event.persisted) return;
        clearInterval(ticker);
        clearTimeout(slowTimer);
        if (hint) hint.textContent = defaultHint;
        document.body.classList.remove("is-working");
        $$(".dropzone.is-uploading").forEach((zone) => zone.classList.remove("is-uploading", "pen-loader"));
        $$('[aria-busy="true"]').forEach((button) => {
            button.removeAttribute("aria-busy");
            button.disabled = false;
        });
    });

    // --- File drop zones: show the chosen name, highlight on drag. data-autoupload forms send the file
    // as soon as it's picked or dropped (their Upload/Add button is only a no-JS fallback).
    $$(".dropzone").forEach((zone) => {
        const input = zone.querySelector('input[type="file"]');
        const text = zone.querySelector("[data-file-text]");
        const defaultHTML = text.innerHTML;
        const form = input.form;
        const sync = () => {
            const files = Array.from(input.files || []);
            if (files.length) text.textContent = files.length > 1 ? `${files.length} files` : files[0].name;
            else text.innerHTML = defaultHTML;
            zone.classList.toggle("has-file", files.length > 0);
        };
        input.addEventListener("change", () => {
            sync();
            if (!form || !form.hasAttribute("data-autoupload") || !input.files.length) return;
            // After the file-type check below has run for this change.
            setTimeout(() => {
                if (!input.checkValidity()) return;
                const files = Array.from(input.files);
                text.textContent = files.length > 1 ? `Uploading ${files.length} files…` : `Uploading ${files[0].name}…`;
                zone.classList.add("is-uploading", "pen-loader");
                form.requestSubmit();
            });
        });
        ["dragenter", "dragover"].forEach((type) => zone.addEventListener(type, () => zone.classList.add("is-over")));
        ["dragleave", "drop"].forEach((type) => zone.addEventListener(type, () => zone.classList.remove("is-over")));
        sync();
    });

    // --- Textareas grow with their content; optional live word count.
    $$("textarea[data-autogrow]").forEach((area) => {
        const counter = document.querySelector(`[data-count-for="${area.id}"]`);
        const grow = () => {
            area.style.height = "auto";
            area.style.height = `${area.scrollHeight + 2}px`;
            if (counter) {
                const words = area.value.trim() ? area.value.trim().split(/\s+/).length : 0;
                counter.textContent = words ? `${words.toLocaleString()} words` : "";
            }
        };
        area.style.overflow = "hidden";
        area.addEventListener("input", grow);
        grow();
    });

    // --- Password reveal.
    $$("[data-reveal]").forEach((button) => {
        button.addEventListener("click", () => {
            const input = document.getElementById(button.dataset.reveal);
            const show = input.type === "password";
            input.type = show ? "text" : "password";
            button.setAttribute("aria-pressed", String(show));
            button.setAttribute("aria-label", show ? "Hide password" : "Show password");
        });
    });

    // --- Feedback chips append a quick note to the textarea.
    $$("[data-chip]").forEach((chip) => {
        chip.addEventListener("click", () => {
            const area = document.getElementById(chip.dataset.chip);
            const note = `${chip.textContent.trim()}.`;
            if (area.value.includes(note)) return;
            area.value = area.value.trim() ? `${area.value.trim()} ${note}` : note;
            area.dispatchEvent(new Event("input"));
            chip.classList.add("is-used");
            area.focus();
        });
    });

    // --- Copy the letter.
    $$("[data-copy]").forEach((button) => {
        const label = button.querySelector("[data-copy-label]");
        button.addEventListener("click", async () => {
            try {
                await navigator.clipboard.writeText(document.getElementById(button.dataset.copy).innerText);
                button.classList.add("is-done");
                label.textContent = "Copied";
            } catch {
                label.textContent = "Select & copy";
            }
            setTimeout(() => {
                button.classList.remove("is-done");
                label.textContent = "Copy";
            }, 2000);
        });
    });

    // --- Edit the letter in place: the text becomes a textarea; Save posts it, Cancel restores it.
    $$("[data-edit-letter]").forEach((button) => {
        const form = document.getElementById(button.getAttribute("aria-controls"));
        if (!form) return;
        const wrap = button.closest(".sheet-wrap");
        const area = form.querySelector("textarea");
        const original = area.value;
        let saving = false;
        const open = () => {
            wrap.classList.add("is-editing");
            form.hidden = false;
            area.dispatchEvent(new Event("input")); // let autogrow size it now that it's visible
            area.focus();
            area.setSelectionRange(area.value.length, area.value.length);
        };
        const close = () => {
            area.value = original;
            form.hidden = true;
            wrap.classList.remove("is-editing");
            button.focus();
        };
        button.addEventListener("click", open);
        form.querySelector("[data-edit-cancel]").addEventListener("click", close);
        form.addEventListener("keydown", (event) => {
            if (event.key === "Escape") close();
            if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) form.requestSubmit();
        });
        form.addEventListener("submit", () => { saving = true; });
        addEventListener("beforeunload", (event) => {
            if (!saving && !form.hidden && area.value !== original) event.preventDefault(); // unsaved edits
        });
    });

    // --- Signup password checklist; the same rules are enforced on the server.
    $$("[data-password-rules]").forEach((input) => {
        const rules = {
            length: (value) => value.length >= 8,
            mix: (value) => /[A-Za-z]/.test(value) && /\d/.test(value),
        };
        const items = $$("[data-rule]", input.closest(".field"));
        const check = () => {
            let ok = true;
            items.forEach((item) => {
                const met = rules[item.dataset.rule](input.value);
                item.classList.toggle("is-met", met);
                ok = ok && met;
            });
            input.setCustomValidity(ok || !input.value ? "" : "Use at least 8 characters with a letter and a number.");
        };
        input.addEventListener("input", check);
        check();
    });

    // --- Our own field messages instead of the browser's plain bubbles. Wording per field via data-msg-missing /
    // data-msg-format; the server still validates everything.
    const fieldMessage = (field) => {
        const v = field.validity;
        if (v.customError) return field.validationMessage;
        if (v.valueMissing) {
            if (field.dataset.msgMissing) return field.dataset.msgMissing;
            if (field.type === "radio") return "Pick one to continue.";
            if (field.type === "file") return "Choose a file first.";
            return "This one's needed before you go on.";
        }
        if (field.dataset.msgFormat) return field.dataset.msgFormat;
        if (v.typeMismatch && field.type === "email") return "That doesn't look like an email yet. Try name@company.com.";
        if (v.typeMismatch && field.type === "url") return "Use the full web address, like https://company.com.";
        if (v.tooShort) return `A little more, please: at least ${field.minLength} characters.`;
        if (v.tooLong) return `That's a bit long: ${field.maxLength} characters at most.`;
        return "Something's not quite right here.";
    };
    // Where the message goes: under the field's wrapper, so it never splits an input from its button.
    const messageHost = (field) => field.closest(".field, .dropzone, fieldset, .angles") || field;
    const clearMessage = (field) => {
        const form = field.form || document;
        const group = field.type === "radio" ? $$(`input[name="${CSS.escape(field.name)}"]`, form) : [field];
        group.forEach((item) => {
            item.removeAttribute("aria-invalid");
            if ("describedby" in item.dataset) {
                if (item.dataset.describedby) item.setAttribute("aria-describedby", item.dataset.describedby);
                else item.removeAttribute("aria-describedby");
            }
        });
        const host = messageHost(field);
        const note = (host.classList.contains("field") ? host : host.parentNode).querySelector(`[data-error-for="${CSS.escape(field.name)}"]`);
        if (note) note.remove();
    };
    document.addEventListener("invalid", (event) => {
        const field = event.target;
        event.preventDefault(); // no browser bubble
        if (field.type === "url" && field.value.trim() && !/^[a-z][a-z0-9+.-]*:\/\//i.test(field.value.trim())) {
            field.value = `https://${field.value.trim()}`; // pressed Enter before blur: fix up, then try again
            if (field.validity.valid) {
                clearMessage(field);
                // validity.valid (not checkValidity) so no extra invalid events fire for the other fields
                if (Array.from(field.form.elements).every((el) => el.validity.valid)) setTimeout(() => field.form.requestSubmit());
                return;
            }
        }
        clearMessage(field);
        const note = document.createElement("p");
        note.className = "field-error";
        note.id = `err-${field.name}`;
        note.dataset.errorFor = field.name;
        note.setAttribute("role", "alert");
        note.textContent = fieldMessage(field);
        const host = messageHost(field);
        if (host.classList.contains("field")) host.append(note); // keeps the field's own spacing below the message
        else host.after(note);
        field.setAttribute("aria-invalid", "true");
        if (!("describedby" in field.dataset)) field.dataset.describedby = field.getAttribute("aria-describedby") || "";
        field.setAttribute("aria-describedby", note.id);
        // Focus the first problem in the form, once per submit attempt.
        const form = field.form;
        if (form && !form.dataset.focused) {
            form.dataset.focused = "1";
            (field.type === "file" ? messageHost(field) : field).scrollIntoView({ block: "center", behavior: reduceMotion ? "auto" : "smooth" });
            field.focus({ preventScroll: true });
            setTimeout(() => delete form.dataset.focused);
        }
    }, true);
    ["input", "change"].forEach((type) => document.addEventListener(type, (event) => {
        const field = event.target;
        if (field.name && field.matches("input, textarea, select") && (field.getAttribute("aria-invalid") || field.type === "radio")) {
            if (field.checkValidity()) clearMessage(field);
        }
    }));

    // Company URLs typed without https:// ("dhan.ai") are fixed up instead of rejected.
    $$('input[type="url"]').forEach((input) => {
        input.addEventListener("blur", () => {
            const value = input.value.trim();
            if (value && !/^[a-z][a-z0-9+.-]*:\/\//i.test(value)) input.value = `https://${value}`;
        });
    });

    // Upload boxes only take PDF or TXT, also when a file is dropped (the accept attribute doesn't stop drops).
    $$('input[type="file"][accept]').forEach((input) => {
        input.addEventListener("change", () => {
            const allowed = input.accept.split(",").map((ext) => ext.trim().toLowerCase());
            const ok = Array.from(input.files || []).every((file) => allowed.some((ext) => file.name.toLowerCase().endsWith(ext)));
            input.setCustomValidity(ok ? "" : "That file type won't work. Upload a PDF or a .txt file.");
            if (!ok) input.reportValidity();
        });
    });

    // --- Slow navigation: a thin progress bar if the next page takes longer than 300 ms.
    const progress = document.createElement("div");
    progress.className = "nav-progress";
    progress.setAttribute("aria-hidden", "true");
    document.body.append(progress);
    let progressTimer;
    const startProgress = () => {
        clearTimeout(progressTimer);
        progressTimer = setTimeout(() => progress.classList.add("is-active"), 300);
    };
    document.addEventListener("click", (event) => {
        const link = event.target.closest("a[href]");
        if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || link.target || link.hasAttribute("download")) return;
        const url = new URL(link.href, location.href);
        if (url.origin !== location.origin || url.protocol === "javascript:" || (url.hash && url.pathname === location.pathname)) return;
        startProgress();
    });
    document.addEventListener("submit", (event) => {
        if (!event.defaultPrevented && !document.body.classList.contains("is-working")) startProgress();
    });
    addEventListener("pageshow", () => {
        clearTimeout(progressTimer);
        progress.classList.remove("is-active");
    });

    // --- Account menu also closes on Escape.
    document.addEventListener("keydown", (event) => {
        const menu = document.querySelector(".account-menu[open]");
        if (event.key === "Escape" && menu) {
            menu.open = false;
            menu.querySelector("summary").focus();
        }
    });

    // --- Magnetic buttons (mouse only).
    if (finePointer && !reduceMotion) {
        $$("[data-magnetic]").forEach((el) => {
            const strength = Number(el.dataset.magnetic) || 0.3;
            el.addEventListener("pointermove", (event) => {
                const rect = el.getBoundingClientRect();
                const x = event.clientX - rect.left - rect.width / 2;
                const y = event.clientY - rect.top - rect.height / 2;
                el.classList.add("is-attracted");
                el.style.translate = `${x * strength}px ${y * strength * 1.3}px`;
            });
            el.addEventListener("pointerleave", () => {
                el.classList.remove("is-attracted");
                el.style.translate = "";
            });
        });
    }
})();
