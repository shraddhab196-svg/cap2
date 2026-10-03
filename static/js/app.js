// Micro-interactions for every page. Vanilla, no dependencies; every feature is progressive enhancement.
(() => {
    const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const finePointer = matchMedia("(pointer: fine)").matches;
    const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

    // --- Busy states. data-loading: spinner on the button. data-overlay="a|b|c": ink overlay with rotating status lines.
    const overlay = document.querySelector(".ink-overlay");
    const status = overlay && overlay.querySelector("[data-ink-status]");
    let ticker;

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
    });

    // Back/forward cache restores the page mid-"working": reset it.
    addEventListener("pageshow", (event) => {
        if (!event.persisted) return;
        clearInterval(ticker);
        document.body.classList.remove("is-working");
        $$('[aria-busy="true"]').forEach((button) => {
            button.removeAttribute("aria-busy");
            button.disabled = false;
        });
    });

    // --- File drop zones: show the chosen name, highlight on drag.
    $$(".dropzone").forEach((zone) => {
        const input = zone.querySelector('input[type="file"]');
        const text = zone.querySelector("[data-file-text]");
        const sync = () => {
            const file = input.files && input.files[0];
            text.textContent = file ? file.name : text.dataset.default;
            zone.classList.toggle("has-file", Boolean(file));
        };
        input.addEventListener("change", sync);
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
