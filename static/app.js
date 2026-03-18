const FORM_MEMORY_KEY = "cdta-last-search-form-v1";
const REMEMBERED_FIELDS = [
    "origin_typed_input",
    "origin_saved_id",
    "origin_current_lat",
    "origin_current_lon",
    "origin_current_label",
    "destination_typed_input",
    "destination_saved_id",
    "destination_current_lat",
    "destination_current_lon",
    "destination_current_label",
];
const REMEMBERED_RADIOS = ["origin_mode", "destination_mode"];

document.addEventListener("DOMContentLoaded", () => {
    const searchForm = document.querySelector(".search-form");
    if (searchForm) {
        if (window.location.pathname === "/") {
            restoreSearchFormState(searchForm);
        }
        registerSearchFormMemory(searchForm);
    }

    document.querySelectorAll(".location-panel").forEach((panel) => {
        initializeLocationPanel(panel);
    });
});

function initializeLocationPanel(panel) {
    const scope = panel.dataset.scope;
    const radios = panel.querySelectorAll(`input[name="${scope}_mode"]`);
    const contentSections = panel.querySelectorAll(".mode-content");
    const button = panel.querySelector(".js-current-location");
    const status = panel.querySelector(".js-current-status");
    const latInput = panel.querySelector(`input[name="${scope}_current_lat"]`);
    const lonInput = panel.querySelector(`input[name="${scope}_current_lon"]`);
    const labelInput = panel.querySelector(`input[name="${scope}_current_label"]`);
    const form = panel.closest("form");

    const setStatus = (message, state) => {
        if (!status) {
            return;
        }
        status.textContent = message;
        status.dataset.state = state;
    };

    const syncMode = () => {
        const selected = panel.querySelector(`input[name="${scope}_mode"]:checked`);
        const selectedMode = selected ? selected.value : "typed";
        contentSections.forEach((section) => {
            section.hidden = section.dataset.mode !== selectedMode;
        });
    };

    radios.forEach((radio) => {
        radio.addEventListener("change", syncMode);
    });
    syncMode();

    if (latInput && lonInput && latInput.value && lonInput.value) {
        setStatus(`Using ${latInput.value}, ${lonInput.value}`, "success");
    }

    if (!button) {
        return;
    }

    button.addEventListener("click", () => {
        if (!navigator.geolocation) {
            setStatus("Browser geolocation is not available in this browser.", "error");
            return;
        }

        setStatus("Requesting browser location permission...", "pending");

        navigator.geolocation.getCurrentPosition(
            (position) => {
                const latitude = position.coords.latitude.toFixed(6);
                const longitude = position.coords.longitude.toFixed(6);

                if (latInput) {
                    latInput.value = latitude;
                }
                if (lonInput) {
                    lonInput.value = longitude;
                }
                if (labelInput) {
                    labelInput.value = `Browser location ${latitude}, ${longitude}`;
                }

                const currentMode = panel.querySelector(`input[name="${scope}_mode"][value="current"]`);
                if (currentMode) {
                    currentMode.checked = true;
                    syncMode();
                }

                if (form) {
                    persistSearchFormState(form);
                }
                setStatus(`Location captured: ${latitude}, ${longitude}`, "success");
            },
            (error) => {
                let message = "Browser location permission was denied.";
                if (error.code === error.POSITION_UNAVAILABLE) {
                    message = "Browser location is currently unavailable.";
                } else if (error.code === error.TIMEOUT) {
                    message = "Browser location request timed out.";
                }
                setStatus(message, "error");
            },
            {
                enableHighAccuracy: true,
                timeout: 10000,
                maximumAge: 60000,
            },
        );
    });
}

function registerSearchFormMemory(form) {
    const persist = () => persistSearchFormState(form);
    form.addEventListener("change", persist);
    form.addEventListener("input", persist);
    form.addEventListener("submit", persist);
}

function persistSearchFormState(form) {
    try {
        const payload = {};
        REMEMBERED_RADIOS.forEach((name) => {
            const selected = form.querySelector(`input[name="${name}"]:checked`);
            if (selected) {
                payload[name] = selected.value;
            }
        });
        REMEMBERED_FIELDS.forEach((name) => {
            const field = form.querySelector(`[name="${name}"]`);
            if (field) {
                payload[name] = field.value;
            }
        });
        window.localStorage.setItem(FORM_MEMORY_KEY, JSON.stringify(payload));
    } catch (error) {
        // Ignore storage errors so lookup still works normally.
    }
}

function restoreSearchFormState(form) {
    const state = readSearchFormState();
    if (!state) {
        return;
    }

    REMEMBERED_RADIOS.forEach((name) => {
        const value = state[name];
        if (!value) {
            return;
        }
        const radio = form.querySelector(`input[name="${name}"][value="${value}"]`);
        if (radio) {
            radio.checked = true;
        }
    });

    REMEMBERED_FIELDS.forEach((name) => {
        const field = form.querySelector(`[name="${name}"]`);
        if (!field || field.value) {
            return;
        }
        if (typeof state[name] === "string") {
            field.value = state[name];
        }
    });
}

function readSearchFormState() {
    try {
        const rawValue = window.localStorage.getItem(FORM_MEMORY_KEY);
        if (!rawValue) {
            return null;
        }
        const parsed = JSON.parse(rawValue);
        return parsed && typeof parsed === "object" ? parsed : null;
    } catch (error) {
        return null;
    }
}
