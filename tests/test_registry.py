from __future__ import annotations

from pathlib import Path

import pytest

from shawnobot.models import (
    AppConfig,
    CodexConfig,
    FeishuConfig,
    LimitsConfig,
    ManagementConfig,
    Settings,
)
from shawnobot.services.registry import RegistryError, RegistryService


def make_settings(tmp_path: Path, allowed_root: Path) -> Settings:
    config = tmp_path / "config.yaml"
    config.write_text(
        """
app:
  data_dir: ./data
projects: {}
sessions: {}
""",
        encoding="utf-8",
    )
    return Settings(
        app=AppConfig("/", tmp_path / "data", tmp_path / "audit.jsonl", 6000),
        feishu=FeishuConfig("", "", True, frozenset(), frozenset()),
        limits=LimitsConfig(10, 60, 100, 1000, 1, 1),
        codex=CodexConfig(True, "codex", "read-only"),
        management=ManagementConfig(True, (allowed_root,)),
        projects={},
        sessions={},
        config_path=config,
    )


def test_creates_project_and_persists_allowlist(tmp_path: Path) -> None:
    allowed_root = tmp_path / "projects"
    allowed_root.mkdir()
    settings = make_settings(tmp_path, allowed_root)
    registry = RegistryService(settings)

    project = registry.create_project(
        "demo",
        str(allowed_root / "demo"),
        "Demo project",
    )

    assert project.path.is_dir()
    assert settings.projects["demo"].codex_enabled
    persisted = settings.config_path.read_text(encoding="utf-8")
    assert "demo:" in persisted
    assert "codex_enabled: true" in persisted


def test_allows_chinese_project_and_session_aliases(tmp_path: Path) -> None:
    allowed_root = tmp_path / "projects"
    allowed_root.mkdir()
    settings = make_settings(tmp_path, allowed_root)
    registry = RegistryService(settings)

    project = registry.create_project(
        "医学影像_实验3",
        str(allowed_root / "experiment3"),
        "中文项目",
    )
    session = registry.add_session(
        "主会话-实验3",
        project.name,
        "11111111-1111-4111-8111-111111111111",
        "主会话",
    )

    assert settings.projects["医学影像_实验3"] == project
    assert settings.sessions["主会话-实验3"] == session


def test_explains_that_documentation_brackets_are_placeholders(tmp_path: Path) -> None:
    allowed_root = tmp_path / "projects"
    allowed_root.mkdir()
    registry = RegistryService(make_settings(tmp_path, allowed_root))

    with pytest.raises(RegistryError, match="占位符"):
        registry.create_project("<demo>", str(allowed_root / "demo"), "")


def test_rejects_project_outside_allowed_roots(tmp_path: Path) -> None:
    allowed_root = tmp_path / "projects"
    allowed_root.mkdir()
    settings = make_settings(tmp_path, allowed_root)
    registry = RegistryService(settings)

    with pytest.raises(RegistryError, match="允许的根目录"):
        registry.create_project("unsafe", str(tmp_path / "outside"), "")


def test_rejects_registration_when_remote_management_is_disabled(tmp_path: Path) -> None:
    allowed_root = tmp_path / "projects"
    allowed_root.mkdir()
    settings = make_settings(tmp_path, allowed_root)
    object.__setattr__(
        settings,
        "management",
        ManagementConfig(False, (allowed_root,)),
    )
    registry = RegistryService(settings)

    with pytest.raises(RegistryError, match="未启用"):
        registry.create_project("demo", str(allowed_root / "demo"), "")


def test_adds_session_to_runtime_and_persistent_allowlist(tmp_path: Path) -> None:
    allowed_root = tmp_path / "projects"
    project_root = allowed_root / "demo"
    project_root.mkdir(parents=True)
    settings = make_settings(tmp_path, allowed_root)
    registry = RegistryService(settings)
    registry.create_project("demo", str(project_root), "")

    session = registry.add_session(
        "research",
        "demo",
        "11111111-1111-4111-8111-111111111111",
        "Research",
    )

    assert settings.sessions["research"] == session
    persisted = settings.config_path.read_text(encoding="utf-8")
    assert "research:" in persisted
    assert "11111111-1111-4111-8111-111111111111" in persisted
