(() => {
    "use strict";

    // Mobile navigation
    const navToggle = document.querySelector(".nav-toggle");
    const navigation = document.querySelector(".app-nav");

    if (navToggle && navigation) {
        navToggle.addEventListener("click", () => {
            const isOpen = navigation.classList.toggle("is-open");
            navToggle.setAttribute("aria-expanded", String(isOpen));
        });

        navigation.addEventListener("click", (event) => {
            if (event.target.closest("a")) {
                navigation.classList.remove("is-open");
                navToggle.setAttribute("aria-expanded", "false");
            }
        });
    }

    // Clock
    const clock = document.querySelector("[data-signaldvr-clock]");

    function updateClock() {
        if (!clock) return;

        clock.textContent = new Intl.DateTimeFormat(undefined, {
            hour: "numeric",
            minute: "2-digit"
        }).format(new Date());
    }

    updateClock();
    setInterval(updateClock, 15000);

    // Confirmation dialogs
    document.querySelectorAll("[data-confirm]").forEach((element) => {
        element.addEventListener("click", (event) => {
            const message =
                element.getAttribute("data-confirm") ||
                "Are you sure?";

            if (!window.confirm(message)) {
                event.preventDefault();
            }
        });
    });

    // Progress bars
    document.querySelectorAll("[data-progress]").forEach((element) => {
        const raw = Number(element.getAttribute("data-progress"));
        const value = Number.isFinite(raw)
            ? Math.max(0, Math.min(100, raw))
            : 0;

        element.style.width = value + "%";
        element.setAttribute("aria-valuenow", value);
    });

    // Auto refresh support
    function refreshElement(id, url) {
        const target = document.getElementById(id);
        if (!target) return;

        fetch(url)
            .then(r => r.text())
            .then(html => {
                target.innerHTML = html;
            })
            .catch(console.error);
    }

    window.SignalDVR = {
        refreshElement
    };

})();