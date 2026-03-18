document.addEventListener("DOMContentLoaded", () => {
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
