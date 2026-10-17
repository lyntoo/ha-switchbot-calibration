# Relationship with Home Assistant Core

This integration replaces Core's `switchbot` integration (same domain). When Home Assistant updates its own integration,
this copy does not follow automatically. A weekly workflow compares the latest Core release with `upstream-baseline/` and opens an
issue when they differ. To update: copy the new Core files, re-apply the changes listed in `FORK.md`, run the tests, publish a release.

To go back to the official integration: remove `custom_components/switchbot` from your configuration and restart Home Assistant. Your devices and
entities are kept (same domain and IDs); only the calibration wizard, the two diagnostic entities and the safety lock disappear.
