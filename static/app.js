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
    let currentLookupRequestId = 0;

    const setStatus = (message, state) => {
        if (!status) {
            return;
        }
        status.textContent = message;
        status.dataset.state = state;
    };

    const summarizeLocation = (label, latitude, longitude) => {
        const coordinates = `${latitude}, ${longitude}`;
        const cleanLabel = (label || "").trim();
        if (!cleanLabel) {
            return coordinates;
        }

        const normalizedLabel = cleanLabel.toLowerCase();
        const normalizedCoordinates = coordinates.toLowerCase();
        if (
            normalizedLabel === normalizedCoordinates
            || normalizedLabel === `coordinates ${normalizedCoordinates}`
            || normalizedLabel === `browser location ${normalizedCoordinates}`
        ) {
            return cleanLabel;
        }

        return `${cleanLabel} (${coordinates})`;
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
        setStatus(
            `Using ${summarizeLocation(labelInput ? labelInput.value : "", latInput.value, lonInput.value)}`,
            "success",
        );
    }

    if (!button) {
        return;
    }

    button.addEventListener("click", () => {
        const requestId = ++currentLookupRequestId;
        if (!navigator.geolocation) {
            setStatus("Browser geolocation is not available in this browser.", "error");
            return;
        }

        button.disabled = true;
        setStatus("Requesting browser location permission...", "pending");

        navigator.geolocation.getCurrentPosition(
            async (position) => {
                if (requestId !== currentLookupRequestId) {
                    return;
                }

                const latitude = position.coords.latitude.toFixed(6);
                const longitude = position.coords.longitude.toFixed(6);
                const fallbackLabel = `Coordinates ${latitude}, ${longitude}`;

                if (latInput) {
                    latInput.value = latitude;
                }
                if (lonInput) {
                    lonInput.value = longitude;
                }
                if (labelInput) {
                    labelInput.value = fallbackLabel;
                }

                const currentMode = panel.querySelector(`input[name="${scope}_mode"][value="current"]`);
                if (currentMode) {
                    currentMode.checked = true;
                    syncMode();
                }

                if (form) {
                    persistSearchFormState(form);
                }
                setStatus("Location captured. Looking up nearby address...", "pending");

                try {
                    const response = await fetch(
                        `/api/reverse-geocode?latitude=${encodeURIComponent(latitude)}&longitude=${encodeURIComponent(longitude)}`,
                        {
                            headers: {
                                Accept: "application/json",
                            },
                        },
                    );
                    const payload = await response.json().catch(() => null);

                    if (requestId !== currentLookupRequestId) {
                        return;
                    }
                    if (!response.ok) {
                        throw new Error(
                            payload && payload.error && payload.error.message
                                ? payload.error.message
                                : "Nearby address lookup is unavailable right now.",
                        );
                    }

                    const resolvedLabel = payload && typeof payload.label === "string" ? payload.label.trim() : "";
                    if (labelInput) {
                        labelInput.value = resolvedLabel || fallbackLabel;
                    }
                    if (form) {
                        persistSearchFormState(form);
                    }
                    setStatus(
                        `Using ${summarizeLocation(resolvedLabel || fallbackLabel, latitude, longitude)}`,
                        "success",
                    );
                } catch (error) {
                    if (requestId !== currentLookupRequestId) {
                        return;
                    }

                    if (labelInput) {
                        labelInput.value = fallbackLabel;
                    }
                    if (form) {
                        persistSearchFormState(form);
                    }

                    const fallbackMessage = error instanceof Error && error.message
                        ? error.message
                        : "Nearby address lookup is unavailable right now.";
                    setStatus(`Using ${fallbackLabel}. ${fallbackMessage}`, "warn");
                } finally {
                    if (requestId === currentLookupRequestId) {
                        button.disabled = false;
                    }
                }
            },
            (error) => {
                if (requestId !== currentLookupRequestId) {
                    return;
                }

                let message = "Browser location permission was denied.";
                if (error.code === error.POSITION_UNAVAILABLE) {
                    message = "Browser location is currently unavailable.";
                } else if (error.code === error.TIMEOUT) {
                    message = "Browser location request timed out.";
                }
                button.disabled = false;
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
