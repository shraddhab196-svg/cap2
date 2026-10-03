// Landing page motion: GSAP (ScrollTrigger, SplitText) + Lenis.
// Without GSAP, or with reduced motion, the page renders in its final, fully readable state.
(() => {
    const root = document.documentElement;
    const { gsap, ScrollTrigger, SplitText, Lenis } = window;
    const $ = (selector) => document.querySelector(selector);
    const $$ = (selector) => Array.from(document.querySelectorAll(selector));
    const nav = $(".nav");
    const steps = $$(".how-step");

    if (matchMedia("(prefers-reduced-motion: reduce)").matches || !gsap || !ScrollTrigger || !SplitText) {
        root.classList.add("no-motion");
        steps.forEach((step) => step.classList.add("is-active"));
        addEventListener("scroll", () => nav.classList.toggle("is-scrolled", scrollY > 40), { passive: true });
        return;
    }

    gsap.registerPlugin(ScrollTrigger, SplitText);

    // --- Smooth scroll, driven by GSAP's ticker so ScrollTrigger stays in sync.
    let lenis = null;
    if (Lenis) {
        lenis = new Lenis({ lerp: 0.1 });
        lenis.on("scroll", ScrollTrigger.update);
        gsap.ticker.add((time) => lenis.raf(time * 1000));
        gsap.ticker.lagSmoothing(0);
        $$('a[href^="#"]').forEach((link) => link.addEventListener("click", (event) => {
            const target = $(link.getAttribute("href"));
            if (!target) return;
            event.preventDefault();
            lenis.scrollTo(target, { duration: 1.6 });
        }));
    }

    // --- Nav: solid once scrolled, tucks away while scrolling down.
    ScrollTrigger.create({
        start: 0,
        end: "max",
        onUpdate(self) {
            const y = self.scroll();
            nav.classList.toggle("is-scrolled", y > 40);
            nav.classList.toggle("is-hidden", self.direction === 1 && y > 500);
        },
    });

    // SplitText measures lines, so wait for the web fonts.
    document.fonts.ready.then(() => {
        intro();
        scrollScenes();
        cursor();
        tilt();
    });

    function intro() {
        const title = $(".hero-title");
        const split = SplitText.create(title, { type: "lines", mask: "lines", linesClass: "line" });
        const underline = $(".underline path");
        const length = underline.getTotalLength();
        gsap.set(underline, { strokeDasharray: length, strokeDashoffset: length });
        gsap.set("[data-note]", { autoAlpha: 0 });

        const tl = gsap.timeline({ defaults: { ease: "expo.out" } });
        // Opacity only: the nav's transform belongs to the CSS hide-on-scroll transition.
        tl.from(nav, { opacity: 0, duration: 1.1 }, 0)
            .from(split.lines, { yPercent: 115, rotate: 2.5, duration: 1.35, stagger: 0.1 }, 0.1)
            .from("[data-fade]", { y: 28, opacity: 0, duration: 1.1, stagger: 0.1 }, 0.55)
            .from(".stage", { y: 110, rotate: 6, opacity: 0, duration: 1.7 }, 0.35)
            .add(() => split.revert(), 1.6)
            .to(underline, { strokeDashoffset: 0, duration: 0.9, ease: "power2.inOut" }, 1.5)
            .add(typeLetter, 1.9);
        gsap.set("[data-intro]", { visibility: "visible" });
    }

    // The sample draft types itself, the generic line gets struck, the rewrite lands in red.
    function typeLetter() {
        const lines = $$("[data-type]");
        const texts = lines.map((line) => line.textContent);
        const caret = document.createElement("span");
        caret.className = "caret";
        lines.forEach((line) => { line.textContent = ""; });

        const tl = gsap.timeline();
        lines.forEach((line, i) => {
            const node = document.createTextNode("");
            const progress = { n: 0 };
            if (line.classList.contains("redline")) {
                tl.add(() => $(".paper .strike").classList.add("is-struck")).to({}, { duration: 0.9 });
            }
            tl.add(() => line.append(node, caret))
                .to(progress, {
                    n: texts[i].length,
                    duration: texts[i].length * 0.026,
                    ease: "none",
                    onUpdate: () => { node.data = texts[i].slice(0, Math.round(progress.n)); },
                })
                .to({}, { duration: 0.3 });
            if (line.classList.contains("redline")) {
                tl.fromTo("[data-note]", { autoAlpha: 0, y: 18, scale: 0.7 }, { autoAlpha: 1, y: 0, scale: 1, duration: 0.9, ease: "back.out(2.2)" });
            }
        });
    }

    function scrollScenes() {
        // Hero drifts apart as it leaves.
        gsap.to(".hero-title", { yPercent: -16, ease: "none", scrollTrigger: { trigger: ".hero", start: "top top", end: "bottom top", scrub: true } });
        gsap.to(".stage", { yPercent: -10, ease: "none", scrollTrigger: { trigger: ".hero", start: "top top", end: "bottom top", scrub: true } });

        // Marquee speeds up with scroll velocity, then settles.
        const marquee = $(".marquee-track").getAnimations()[0];
        if (lenis && marquee) {
            let boost = 0;
            lenis.on("scroll", ({ velocity }) => { boost = Math.max(boost, Math.min(Math.abs(velocity) * 0.3, 7)); });
            gsap.ticker.add(() => {
                boost *= 0.93;
                marquee.playbackRate = 1 + boost;
            });
        }

        // How it works: pinned horizontal track on wide screens, plain stack below.
        const mm = gsap.matchMedia();
        const track = $(".how-track");
        mm.add("(min-width: 900px)", () => {
            const distance = () => track.scrollWidth - innerWidth;
            const travel = gsap.to(track, {
                x: () => -distance(),
                ease: "none",
                scrollTrigger: { trigger: ".how", start: "top top", end: () => `+=${distance()}`, pin: true, scrub: 1, invalidateOnRefresh: true },
            });
            steps.forEach((step) => {
                ScrollTrigger.create({ trigger: step, containerAnimation: travel, start: "left 70%", onEnter: () => step.classList.add("is-active") });
                gsap.fromTo(step, { rotate: 2.5, y: 40 }, { rotate: -0.5, y: 0, ease: "none", scrollTrigger: { trigger: step, containerAnimation: travel, start: "left right", end: "center center", scrub: true } });
            });
        });
        mm.add("(max-width: 899px)", () => {
            steps.forEach((step) => ScrollTrigger.create({ trigger: step, start: "top 70%", onEnter: () => step.classList.add("is-active") }));
        });

        // Manifesto fills with ink word by word.
        const words = SplitText.create("[data-words]", { type: "words" }).words;
        gsap.fromTo(words, { opacity: 0.12 }, { opacity: 1, stagger: 0.1, ease: "none", scrollTrigger: { trigger: ".manifesto", start: "top 75%", end: "bottom 65%", scrub: true } });

        // House rules: the clichés get struck through.
        ScrollTrigger.create({
            trigger: ".never-grid",
            start: "top 78%",
            onEnter: () => $$(".never .strike").forEach((strike) => strike.classList.add("is-struck")),
        });

        // Final CTA: title rises, signature writes itself.
        const ctaTitle = SplitText.create("[data-cta-title]", { type: "lines", mask: "lines", linesClass: "line" });
        gsap.from(ctaTitle.lines, { yPercent: 115, duration: 1.3, stagger: 0.1, ease: "expo.out", scrollTrigger: { trigger: ".cta", start: "top 60%" } });
        const signature = $(".signature path");
        const length = signature.getTotalLength();
        gsap.fromTo(signature, { strokeDasharray: length, strokeDashoffset: length }, { strokeDashoffset: 0, duration: 2.4, ease: "power2.inOut", scrollTrigger: { trigger: ".cta-row", start: "top 88%" } });
    }

    // Ink-dot cursor that swells over anything clickable (mouse only).
    function cursor() {
        if (!matchMedia("(pointer: fine)").matches) return;
        const dot = $(".cursor");
        const xTo = gsap.quickTo(dot, "x", { duration: 0.45, ease: "power3" });
        const yTo = gsap.quickTo(dot, "y", { duration: 0.45, ease: "power3" });
        addEventListener("pointermove", (event) => {
            dot.classList.add("is-on");
            xTo(event.clientX);
            yTo(event.clientY);
        });
        document.addEventListener("pointerover", (event) => dot.classList.toggle("is-hover", Boolean(event.target.closest("a, button"))));
        document.documentElement.addEventListener("pointerleave", () => dot.classList.remove("is-on"));
    }

    // The draft leans toward the pointer.
    function tilt() {
        if (!matchMedia("(pointer: fine)").matches) return;
        const paper = $("[data-tilt]");
        const hero = $(".hero");
        const rx = gsap.quickTo(paper, "rotationX", { duration: 0.9, ease: "power3" });
        const ry = gsap.quickTo(paper, "rotationY", { duration: 0.9, ease: "power3" });
        hero.addEventListener("pointermove", (event) => {
            const rect = paper.getBoundingClientRect();
            ry(((event.clientX - (rect.left + rect.width / 2)) / innerWidth) * 16);
            rx(((event.clientY - (rect.top + rect.height / 2)) / innerHeight) * -12);
        });
        hero.addEventListener("pointerleave", () => { rx(0); ry(0); });
    }
})();
