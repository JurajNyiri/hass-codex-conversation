"""Tests for Codex Home Assistant configuration tools."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol

from custom_components.codex_conversation.config_tools import (
    CreateDashboardTool,
    EditYamlFileTool,
    ListAutomationsTool,
    ReadYamlFileTool,
    UpsertAutomationTool,
    UpsertScriptTool,
)


def make_hass(tmp_path: Path):
    hass = MagicMock()
    hass.config.path.side_effect = lambda filename="": str(tmp_path / filename)
    hass.services.async_call = AsyncMock()
    hass.async_add_executor_job = AsyncMock(
        side_effect=lambda function, *args: function(*args)
    )
    return hass


@pytest.mark.asyncio
async def test_upsert_script_writes_and_reloads(tmp_path):
    hass = make_hass(tmp_path)
    tool = UpsertScriptTool()

    result = await tool.async_call(
        hass,
        SimpleNamespace(
            tool_args={
                "script_id": "movie_lights",
                "config": {"sequence": [{"action": "light.turn_on"}]},
                "reload": True,
            }
        ),
        None,
    )

    assert result == {"success": True, "reloaded": "script"}
    assert "movie_lights:" in (tmp_path / "scripts.yaml").read_text()
    hass.services.async_call.assert_awaited_once_with(
        "script", "reload", {}, blocking=True
    )


@pytest.mark.asyncio
async def test_upsert_automation_replaces_matching_id(tmp_path):
    hass = make_hass(tmp_path)
    (tmp_path / "automations.yaml").write_text(
        "- id: existing\n  alias: Old\n- id: other\n  alias: Keep\n"
    )
    tool = UpsertAutomationTool()

    await tool.async_call(
        hass,
        SimpleNamespace(
            tool_args={
                "automation_id": "existing",
                "config": {"alias": "New", "trigger": []},
                "reload": False,
            }
        ),
        None,
    )

    content = (tmp_path / "automations.yaml").read_text()
    assert "alias: New" in content
    assert "alias: Old" not in content
    assert "alias: Keep" in content
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_automations_returns_yaml_list(tmp_path):
    hass = make_hass(tmp_path)
    (tmp_path / "automations.yaml").write_text("- id: one\n  alias: One\n")

    result = await ListAutomationsTool().async_call(
        hass, SimpleNamespace(tool_args={}), None
    )

    assert result["automations"][0]["id"] == "one"


@pytest.mark.asyncio
async def test_edit_yaml_file_requires_confirmation(tmp_path):
    hass = make_hass(tmp_path)

    with pytest.raises(vol.Invalid, match="confirmation"):
        await EditYamlFileTool().async_call(
            hass,
            SimpleNamespace(
                tool_args={"path": "configuration.yaml", "content": "x: 1", "confirm": False}
            ),
            None,
        )


@pytest.mark.asyncio
async def test_edit_yaml_file_refuses_existing_sensitive_fields(tmp_path):
    hass = make_hass(tmp_path)
    (tmp_path / "configuration.yaml").write_text("api_key: keep-this-secret\n")

    with pytest.raises(vol.Invalid, match="sensitive fields"):
        await EditYamlFileTool().async_call(
            hass,
            SimpleNamespace(
                tool_args={
                    "path": "configuration.yaml",
                    "content": "changed: true",
                    "confirm": True,
                }
            ),
            None,
        )

    assert (tmp_path / "configuration.yaml").read_text() == "api_key: keep-this-secret\n"


@pytest.mark.asyncio
async def test_read_yaml_file_redacts_credentials(tmp_path):
    hass = make_hass(tmp_path)
    (tmp_path / "configuration.yaml").write_text(
        "homeassistant:\n  name: Test\napi_key: secret-value\nnested:\n  password: hidden\n"
    )

    result = await ReadYamlFileTool().async_call(
        hass,
        SimpleNamespace(tool_args={"path": "configuration.yaml"}),
        None,
    )

    assert result["content"]["homeassistant"]["name"] == "Test"
    assert result["content"]["api_key"] == "[REDACTED]"
    assert result["content"]["nested"]["password"] == "[REDACTED]"
    assert "must not be" in result["warning"]


@pytest.mark.asyncio
async def test_create_dashboard_writes_validated_dashboard_file(tmp_path):
    hass = make_hass(tmp_path)

    result = await CreateDashboardTool().async_call(
        hass,
        SimpleNamespace(
            tool_args={
                "dashboard_id": "energy",
                "config": {"views": [{"title": "Energy", "cards": []}]},
                "confirm": True,
            }
        ),
        None,
    )

    assert result["success"] is True
    assert (tmp_path / "codex_dashboards/energy.yaml").exists()


@pytest.mark.asyncio
async def test_edit_yaml_file_rejects_paths_outside_config(tmp_path):
    hass = make_hass(tmp_path)
    hass.config.path.side_effect = lambda filename="": (
        str(tmp_path) if not filename else str(Path("/tmp") / filename)
    )

    with pytest.raises(vol.Invalid, match="inside"):
        await EditYamlFileTool().async_call(
            hass,
            SimpleNamespace(
                tool_args={"path": "outside.yaml", "content": "x: 1", "confirm": True}
            ),
            None,
        )
