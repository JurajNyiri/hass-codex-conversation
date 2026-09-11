# Attributions

## Upstream Codex conversation integration

This project is based on:

- [JurajNyiri/hass-codex-conversation](https://github.com/JurajNyiri/hass-codex-conversation)
- [petretiandrea/hass-codex-conversation](https://github.com/petretiandrea/hass-codex-conversation)

Those projects provide the Home Assistant Codex conversation integration,
device-code authentication, OAuth token refresh, Codex API client, streaming
conversation handling, and existing Home Assistant Assist integration.

The `GeneralUltra758/hass-codex-conversation` fork adds the configuration-tool
layer in `custom_components/codex_conversation/config_tools.py`, including
script and automation management, reload tools, dashboard YAML creation, and
the opt-in YAML file editor. Tests for that layer are in
`tests/test_config_tools.py`.

The upstream repositories did not include a separate `LICENSE` file at the
time of this fork. Refer to their repository history and published project
terms for the applicable upstream licensing information.