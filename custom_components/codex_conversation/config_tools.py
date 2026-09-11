"""Home Assistant configuration tools exposed to the Codex agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
import voluptuous as vol
import yaml

CONFIG_API_ID = "codex_hass_config"
FILES_API_ID = "codex_hass_files"

_SCRIPT_FILE = "scripts.yaml"
_AUTOMATION_FILE = "automations.yaml"
_DASHBOARD_DIR = "codex_dashboards"
_SENSITIVE_KEY_PARTS = (
    "token",
    "password",
    "secret",
    "api_key",
    "apikey",
    "credential",
    "authorization",
)


def _config_path(hass: HomeAssistant, filename: str) -> Path:
    return Path(hass.config.path(filename)).resolve()


def _ensure_config_path(hass: HomeAssistant, path: Path) -> None:
    root = Path(hass.config.path()).resolve()
    if path != root and root not in path.parents:
        raise vol.Invalid("Path must remain inside the Home Assistant config directory")


def _read_yaml(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    return default if value is None else value


def _atomic_write_yaml(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.codex.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        yaml.safe_dump(value, stream, allow_unicode=True, sort_keys=False)
    temporary.replace(path)


def _redact_yaml(value: Any) -> Any:
    """Redact common credential fields before returning YAML to the model."""
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if any(part in str(key).lower() for part in _SENSITIVE_KEY_PARTS)
            else _redact_yaml(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_yaml(item) for item in value]
    return value


def _contains_sensitive_keys(value: Any) -> bool:
    """Return whether YAML contains fields that cannot be safely round-tripped."""
    if isinstance(value, dict):
        return any(
            any(part in str(key).lower() for part in _SENSITIVE_KEY_PARTS)
            or _contains_sensitive_keys(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_keys(item) for item in value)
    return False


async def _reload(hass: HomeAssistant, domain: str) -> dict[str, Any]:
    await hass.services.async_call(domain, "reload", {}, blocking=True)
    return {"success": True, "reloaded": domain}


class _ConfigTool(llm.Tool):
    def __init__(self, name: str, description: str, parameters: vol.Schema):
        self.name = name
        self.description = description
        self.parameters = parameters


class ListScriptsTool(_ConfigTool):
    def __init__(self):
        super().__init__("list_scripts", "List configured Home Assistant scripts.", vol.Schema({}))

    async def async_call(self, hass, tool_input, llm_context):
        path = _config_path(hass, _SCRIPT_FILE)
        return {"scripts": await hass.async_add_executor_job(_read_yaml, path, {})}


class UpsertScriptTool(_ConfigTool):
    def __init__(self):
        super().__init__(
            "upsert_script",
            "Create or replace one script in scripts.yaml. The script_id must use underscores.",
            vol.Schema({
                vol.Required("script_id"): str,
                vol.Required("config"): dict,
                vol.Optional("reload", default=True): bool,
            }),
        )

    async def async_call(self, hass, tool_input, llm_context):
        script_id = tool_input.tool_args["script_id"]
        config = tool_input.tool_args["config"]
        if not script_id.replace("_", "").isalnum() or "-" in script_id:
            raise vol.Invalid("script_id must contain only letters, numbers, and underscores")
        path = _config_path(hass, _SCRIPT_FILE)

        def update():
            scripts = _read_yaml(path, {})
            if not isinstance(scripts, dict):
                raise vol.Invalid("scripts.yaml must contain a mapping")
            scripts[script_id] = config
            _atomic_write_yaml(path, scripts)

        await hass.async_add_executor_job(update)
        if tool_input.tool_args["reload"]:
            return await _reload(hass, "script")
        return {"success": True, "script_id": script_id, "reloaded": False}


class ListAutomationsTool(_ConfigTool):
    def __init__(self):
        super().__init__("list_automations", "List automations in automations.yaml.", vol.Schema({}))

    async def async_call(self, hass, tool_input, llm_context):
        path = _config_path(hass, _AUTOMATION_FILE)
        return {"automations": await hass.async_add_executor_job(_read_yaml, path, [])}


class UpsertAutomationTool(_ConfigTool):
    def __init__(self):
        super().__init__(
            "upsert_automation",
            "Create or replace one automation in automations.yaml. Use a stable id.",
            vol.Schema({
                vol.Required("automation_id"): str,
                vol.Required("config"): dict,
                vol.Optional("reload", default=True): bool,
            }),
        )

    async def async_call(self, hass, tool_input, llm_context):
        automation_id = tool_input.tool_args["automation_id"]
        config = dict(tool_input.tool_args["config"])
        config["id"] = automation_id
        path = _config_path(hass, _AUTOMATION_FILE)

        def update():
            automations = _read_yaml(path, [])
            if not isinstance(automations, list):
                raise vol.Invalid("automations.yaml must contain a list")
            for index, item in enumerate(automations):
                if isinstance(item, dict) and str(item.get("id")) == automation_id:
                    automations[index] = config
                    break
            else:
                automations.append(config)
            _atomic_write_yaml(path, automations)

        await hass.async_add_executor_job(update)
        if tool_input.tool_args["reload"]:
            return await _reload(hass, "automation")
        return {"success": True, "automation_id": automation_id, "reloaded": False}


class ReloadScriptsTool(_ConfigTool):
    def __init__(self):
        super().__init__("reload_scripts", "Reload scripts.yaml in Home Assistant.", vol.Schema({}))

    async def async_call(self, hass, tool_input, llm_context):
        return await _reload(hass, "script")


class ReloadAutomationsTool(_ConfigTool):
    def __init__(self):
        super().__init__("reload_automations", "Reload automations.yaml in Home Assistant.", vol.Schema({}))

    async def async_call(self, hass, tool_input, llm_context):
        return await _reload(hass, "automation")


class CreateDashboardTool(_ConfigTool):
    def __init__(self):
        super().__init__(
            "create_dashboard",
            "Create a Lovelace dashboard YAML file under config/codex_dashboards. The user can add it to Lovelace YAML mode afterward.",
            vol.Schema(
                {
                    vol.Required("dashboard_id"): str,
                    vol.Required("config"): dict,
                    vol.Required("confirm"): bool,
                }
            ),
        )

    async def async_call(self, hass, tool_input, llm_context):
        if not tool_input.tool_args["confirm"]:
            raise vol.Invalid("Explicit confirmation is required")
        dashboard_id = tool_input.tool_args["dashboard_id"]
        if not dashboard_id.replace("_", "").isalnum() or "-" in dashboard_id:
            raise vol.Invalid("dashboard_id must contain only letters, numbers, and underscores")
        config = tool_input.tool_args["config"]
        if not isinstance(config.get("views"), list):
            raise vol.Invalid("Dashboard config must contain a views list")
        path = _config_path(hass, f"{_DASHBOARD_DIR}/{dashboard_id}.yaml")
        await hass.async_add_executor_job(_atomic_write_yaml, path, config)
        return {
            "success": True,
            "path": str(path.relative_to(Path(hass.config.path()).resolve())),
            "note": "Add this file to a Lovelace YAML dashboard configuration to display it.",
        }


class EditYamlFileTool(_ConfigTool):
    def __init__(self):
        super().__init__(
            "edit_yaml_file",
            "Replace a YAML file inside the Home Assistant config directory. This tool is opt-in and should be used only with explicit user approval.",
            vol.Schema({
                vol.Required("path"): str,
                vol.Required("content"): str,
                vol.Required("confirm"): bool,
            }),
        )

    async def async_call(self, hass, tool_input, llm_context):
        if not tool_input.tool_args["confirm"]:
            raise vol.Invalid("Explicit confirmation is required")
        path = Path(hass.config.path(tool_input.tool_args["path"])).resolve()
        _ensure_config_path(hass, path)
        if path.suffix not in {".yaml", ".yml"}:
            raise vol.Invalid("Only YAML files can be edited")
        existing = await hass.async_add_executor_job(_read_yaml, path, None)
        if _contains_sensitive_keys(existing):
            raise vol.Invalid(
                "Refusing to replace a YAML file containing sensitive fields. "
                "Back it up and use a targeted editor that preserves secrets."
            )
        content = tool_input.tool_args["content"]
        parsed = await hass.async_add_executor_job(yaml.safe_load, content)
        await hass.async_add_executor_job(_atomic_write_yaml, path, parsed)
        return {"success": True, "path": str(path.relative_to(Path(hass.config.path()).resolve()))}


class ReadYamlFileTool(_ConfigTool):
    def __init__(self):
        super().__init__(
            "read_yaml_file",
            "Read and validate a YAML file inside the Home Assistant config directory. Common credential fields are redacted. The result is not safe to write back; back up the file and manually restore redacted values.",
            vol.Schema({vol.Required("path"): str}),
        )

    async def async_call(self, hass, tool_input, llm_context):
        path = Path(hass.config.path(tool_input.tool_args["path"])).resolve()
        _ensure_config_path(hass, path)
        if path.suffix not in {".yaml", ".yml"}:
            raise vol.Invalid("Only YAML files can be read")
        if not path.exists():
            raise vol.Invalid("YAML file does not exist")
        parsed = await hass.async_add_executor_job(_read_yaml, path, None)
        return {
            "success": True,
            "path": str(path.relative_to(Path(hass.config.path()).resolve())),
            "content": _redact_yaml(parsed),
            "warning": (
                "This content may contain [REDACTED] placeholders and must not be "
                "written back as a full-file replacement. Back up the original "
                "file and manually restore redacted values before any manual edit."
            ),
        }


class _ToolAPI(llm.API):
    def __init__(self, hass: HomeAssistant, api_id: str, name: str, tools: list[llm.Tool]):
        super().__init__(hass=hass, id=api_id, name=name)
        self._tools = tools

    async def async_get_api_instance(self, llm_context):
        return llm.APIInstance(self, "", llm_context, self._tools)


def register_apis(hass: HomeAssistant) -> list:
    """Register the safe and opt-in configuration tool APIs."""
    unregister = [
        llm.async_register_api(
            hass,
            _ToolAPI(
                hass,
                CONFIG_API_ID,
                "Codex Home Assistant configuration",
                [
                    ListScriptsTool(),
                    UpsertScriptTool(),
                    ListAutomationsTool(),
                    UpsertAutomationTool(),
                    ReloadScriptsTool(),
                    ReloadAutomationsTool(),
                    CreateDashboardTool(),
                ],
            ),
        ),
        llm.async_register_api(
            hass,
            _ToolAPI(
                hass,
                FILES_API_ID,
                "Codex Home Assistant YAML files",
                [ReadYamlFileTool(), EditYamlFileTool()],
            ),
        ),
    ]
    return unregister
