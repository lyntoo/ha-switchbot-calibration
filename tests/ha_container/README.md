# Tests that run inside a Home Assistant Python environment

Copy `custom_components/switchbot` to `/tmp/forktest/custom_components/switchbot` inside the Home Assistant container, then run each script with that
container's Python (for example `docker exec -i homeassistant python3 - < flux_options_dans_conteneur_ha.py`). They simulate the wizard, the safety lock and
the module imports with a fake blind (no Bluetooth).
