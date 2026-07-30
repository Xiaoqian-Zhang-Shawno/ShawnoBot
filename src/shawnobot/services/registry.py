from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path
from typing import Any

import yaml

from ..models import ProjectConfig, SessionConfig, Settings


class RegistryError(ValueError):
    """Raised when a remote project or session registration is unsafe."""


class RegistryService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.projects = settings.projects
        self.sessions = settings.sessions
        self._lock = threading.Lock()

    def validate_session_registration(self, alias: str, project_name: str) -> None:
        self._require_enabled()
        self._validate_alias(alias, "会话别名")
        if alias in self.sessions:
            raise RegistryError(f"会话别名 `{alias}` 已存在")
        if project_name not in self.projects:
            choices = "、".join(sorted(self.projects)) or "无"
            raise RegistryError(f"项目 `{project_name}` 不在白名单；可用项目：{choices}")

    def create_project(
        self,
        name: str,
        path_text: str,
        description: str = "",
    ) -> ProjectConfig:
        self._require_enabled()
        self._validate_alias(name, "项目名称")
        if name in self.projects:
            raise RegistryError(f"项目 `{name}` 已在白名单")
        if len(description) > 300:
            raise RegistryError("项目描述不能超过 300 个字符")

        candidate = self._resolve_project_path(path_text)
        for existing in self.projects.values():
            if candidate == existing.path.resolve():
                raise RegistryError(
                    f"该路径已登记为项目 `{existing.name}`：`{candidate}`"
                )
        if candidate.exists() and not candidate.is_dir():
            raise RegistryError(f"项目路径不是目录：`{candidate}`")
        candidate.mkdir(parents=True, exist_ok=True)

        project = ProjectConfig(
            name=name,
            path=candidate,
            description=description,
            codex_enabled=True,
        )
        with self._lock:
            if name in self.projects:
                raise RegistryError(f"项目 `{name}` 已在白名单")
            for existing in self.projects.values():
                if candidate == existing.path.resolve():
                    raise RegistryError(
                        f"该路径已登记为项目 `{existing.name}`：`{candidate}`"
                    )
            raw = self._load_config()
            projects = self._mapping(raw, "projects")
            projects[name] = {
                "path": candidate.as_posix(),
                "description": description,
                "codex_enabled": True,
                "logs": {},
                "tasks": {},
            }
            raw["projects"] = projects
            self._write_config(raw)
            self.projects[name] = project
        return project

    def add_session(
        self,
        alias: str,
        project_name: str,
        session_id: str,
        title: str = "",
    ) -> SessionConfig:
        self.validate_session_registration(alias, project_name)
        try:
            uuid.UUID(session_id)
        except ValueError as exc:
            raise RegistryError("Codex 会话 ID 格式无效") from exc
        if len(title) > 200:
            raise RegistryError("会话标题不能超过 200 个字符")

        session = SessionConfig(
            alias=alias,
            session_id=session_id,
            title=title or alias,
            project=project_name,
        )
        with self._lock:
            if alias in self.sessions:
                raise RegistryError(f"会话别名 `{alias}` 已存在")
            raw = self._load_config()
            sessions = self._mapping(raw, "sessions")
            sessions[alias] = {
                "id": session_id,
                "title": session.title,
                "project": project_name,
            }
            raw["sessions"] = sessions
            self._write_config(raw)
            self.sessions[alias] = session
        return session

    def _require_enabled(self) -> None:
        if not self.settings.management.remote_registration_enabled:
            raise RegistryError("远程项目/会话登记当前未启用")

    @staticmethod
    def _validate_alias(value: str, label: str) -> None:
        if any(character in value for character in "<>[]"):
            raise RegistryError(
                f"{label}中的 `< >` 或 `[ ]` 是帮助文本的占位符，发送指令时请移除"
            )
        is_valid = (
            1 <= len(value) <= 64
            and value[0].isalnum()
            and all(character.isalnum() or character in "_-" for character in value)
        )
        if not is_valid:
            raise RegistryError(
                f"{label}只能包含中文、英文字母、数字、下划线和连字符，"
                "必须以文字或数字开头，长度为 1-64"
            )

    def _resolve_project_path(self, path_text: str) -> Path:
        expanded = Path(os.path.expandvars(os.path.expanduser(path_text)))
        if not expanded.is_absolute():
            raise RegistryError("项目路径必须是绝对路径")
        candidate = expanded.resolve()
        for root in self.settings.management.allowed_project_roots:
            resolved_root = root.resolve()
            if not resolved_root.is_dir():
                continue
            try:
                relative = candidate.relative_to(resolved_root)
            except ValueError:
                continue
            if relative.parts:
                return candidate
        roots = "、".join(f"`{root}`" for root in self.settings.management.allowed_project_roots)
        raise RegistryError(f"项目路径必须位于允许的根目录下：{roots or '未配置'}")

    def _load_config(self) -> dict[str, Any]:
        try:
            value = yaml.safe_load(self.settings.config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise RegistryError(f"无法读取配置文件：{exc}") from exc
        if not isinstance(value, dict):
            raise RegistryError("配置文件根节点不是映射")
        return value

    @staticmethod
    def _mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
        value = raw.get(key)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise RegistryError(f"配置项 `{key}` 不是映射")
        return value

    def _write_config(self, raw: dict[str, Any]) -> None:
        temporary = self.settings.config_path.with_suffix(
            self.settings.config_path.suffix + ".tmp"
        )
        try:
            temporary.write_text(
                yaml.safe_dump(
                    raw,
                    allow_unicode=True,
                    sort_keys=False,
                    default_flow_style=False,
                ),
                encoding="utf-8",
            )
            os.replace(temporary, self.settings.config_path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise RegistryError(f"无法保存配置文件：{exc}") from exc
