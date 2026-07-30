from __future__ import annotations

from pathlib import Path

import pytest

from shawnobot.settings import ConfigurationError, load_settings


def write_config(tmp_path: Path, extra: str = "") -> Path:
    project = tmp_path / "project"
    project.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
app:
  data_dir: ./data
  audit_log: ./data/audit.jsonl
feishu:
  allowed_user_ids: [ou_owner]
  allowed_chat_ids: [oc_lab]
codex:
  enabled: false
  sandbox: read-only
projects:
  demo:
    path: "{project.as_posix()}"
    logs:
      train: logs/train.log
    tasks:
      test:
        argv: ["python", "-c", "print('ok')"]
{extra}
""",
        encoding="utf-8",
    )
    return config


def test_loads_project_and_resolves_paths(tmp_path: Path) -> None:
    settings = load_settings(write_config(tmp_path))
    assert settings.projects["demo"].path == (tmp_path / "project").resolve()
    assert settings.projects["demo"].logs["train"] == (
        tmp_path / "project" / "logs" / "train.log"
    ).resolve()
    assert settings.codex.sandbox == "read-only"


def test_loads_remote_registration_roots(tmp_path: Path) -> None:
    settings = load_settings(
        write_config(
            tmp_path,
            """
management:
  remote_registration_enabled: true
  allowed_project_roots:
    - ./projects
""",
        )
    )

    assert settings.management.remote_registration_enabled
    assert settings.management.allowed_project_roots == ((tmp_path / "projects").resolve(),)


def test_loads_deepseek_nlu_without_putting_key_in_yaml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    settings = load_settings(
        write_config(
            tmp_path,
            """
nlu:
  enabled: true
  model: deepseek-v4-pro
  min_confidence: 0.9
  allowed_commands: [gpu, status]
""",
        )
    )

    assert settings.nlu.enabled
    assert settings.nlu.api_key == "test-secret"
    assert settings.nlu.model == "deepseek-v4-pro"
    assert settings.nlu.min_confidence == 0.9
    assert settings.nlu.allowed_commands == frozenset({"gpu", "status"})


def test_project_dotenv_deepseek_key_overrides_stale_process_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "stale-process-key")
    (tmp_path / ".env").write_text(
        "DEEPSEEK_API_KEY=project-file-key\n",
        encoding="utf-8",
    )

    settings = load_settings(
        write_config(
            tmp_path,
            """
nlu:
  enabled: true
""",
        )
    )

    assert settings.nlu.api_key == "project-file-key"


def test_rejects_log_path_outside_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
projects:
  demo:
    path: "{project.as_posix()}"
    logs:
      bad: ../secret.txt
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="必须位于项目目录内"):
        load_settings(config)


def test_rejects_string_task_command(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
projects:
  demo:
    path: "{project.as_posix()}"
    tasks:
      unsafe:
        argv: "python train.py && del data"
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="argv 必须是非空字符串列表"):
        load_settings(config)


def test_rejects_task_cwd_outside_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
projects:
  demo:
    path: "{project.as_posix()}"
    tasks:
      unsafe:
        cwd: ..
        argv: ["python", "-V"]
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="cwd 必须位于项目目录内"):
        load_settings(config)
