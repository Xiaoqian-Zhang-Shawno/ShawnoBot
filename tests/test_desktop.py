from __future__ import annotations

import json
from pathlib import Path

import pytest

from shawnobot.models import (
    AppConfig,
    CodexConfig,
    FeishuConfig,
    LimitsConfig,
    ManagementConfig,
    ProjectConfig,
    Settings,
)
from shawnobot.services.desktop import (
    DesktopProcessInventory,
    DesktopProjectError,
    DesktopService,
)


def make_settings(tmp_path: Path, allowlisted_path: Path) -> Settings:
    return Settings(
        app=AppConfig("/", tmp_path / "data", tmp_path / "audit.jsonl", 6000),
        feishu=FeishuConfig("", "", True, frozenset(), frozenset()),
        limits=LimitsConfig(10, 60, 100, 1000, 1, 1),
        codex=CodexConfig(True, "codex", "read-only"),
        management=ManagementConfig(True, (tmp_path,)),
        projects={
            "allowed": ProjectConfig(
                "allowed",
                allowlisted_path,
                codex_enabled=True,
            )
        },
        sessions={},
        config_path=tmp_path / "config.yaml",
    )


def test_aligns_desktop_projects_with_allowlist_and_child_pids(
    tmp_path: Path,
    monkeypatch,
) -> None:
    allowed = tmp_path / "attention2"
    candidate = tmp_path / "experiment"
    allowed.mkdir()
    candidate.mkdir()
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    state = {
        "local-projects": {
            "local-attention": {"rootPaths": [str(allowed)]},
            "local-experiment": {"rootPaths": [str(candidate)]},
        },
        "project-order": ["local-attention", "local-experiment"],
        "selected-project": {"type": "local", "projectId": "local-experiment"},
        "active-workspace-roots": str(allowed),
    }
    (codex_home / ".codex-global-state.json").write_text(
        json.dumps(state),
        encoding="utf-8",
    )
    service = DesktopService(
        make_settings(tmp_path, allowed),
        codex_home=codex_home,
    )
    candidate_key = service._path_key(candidate)
    monkeypatch.setattr(
        service,
        "_process_inventory",
        lambda paths: DesktopProcessInventory(
            main_pids=(100,),
            app_server_pids=(200,),
            memory_mb=256,
            project_child_pids={candidate_key: (300, 301)},
        ),
    )

    result = service.status()
    by_number = service.resolve_project("2")
    by_name = service.resolve_project("experiment")

    assert "共享 app-server PID：200" in result
    assert "白名单：allowed" in result
    assert "项目子进程 PID：300,301" in result
    assert "桌面当前选择" in result
    assert by_number.path == candidate.resolve()
    assert by_name == by_number


def test_requires_number_for_desktop_project_with_multiple_roots(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / ".codex-global-state.json").write_text(
        json.dumps(
            {
                "local-projects": {
                    "multi": {"rootPaths": [str(first), str(second)]},
                },
                "project-order": ["multi"],
            }
        ),
        encoding="utf-8",
    )
    service = DesktopService(
        make_settings(tmp_path, first),
        codex_home=codex_home,
    )
    monkeypatch.setattr(
        service,
        "_process_inventory",
        lambda paths: DesktopProcessInventory((), (), 0, {}),
    )

    assert service.resolve_project("1").path == first.resolve()
    with pytest.raises(DesktopProjectError, match="多个根目录"):
        service.resolve_project("first")
