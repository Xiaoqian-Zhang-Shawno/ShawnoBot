from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

from ..models import Settings


class DesktopProjectError(ValueError):
    """Raised when a desktop project reference is missing or ambiguous."""


@dataclass(frozen=True)
class DesktopProjectEntry:
    index: int
    project_id: str
    label: str
    path: Path
    root_index: int
    root_count: int
    selected: bool
    active: bool
    allowlisted_as: tuple[str, ...]
    child_pids: tuple[int, ...]


@dataclass(frozen=True)
class DesktopProcessInventory:
    main_pids: tuple[int, ...]
    app_server_pids: tuple[int, ...]
    memory_mb: float
    project_child_pids: dict[str, tuple[int, ...]]


class DesktopService:
    def __init__(
        self,
        settings: Settings,
        *,
        codex_home: Path | None = None,
    ) -> None:
        self.settings = settings
        configured_home = os.environ.get("CODEX_HOME")
        self.codex_home = (
            codex_home
            or (Path(configured_home).expanduser() if configured_home else Path.home() / ".codex")
        ).resolve()
        self.state_path = self.codex_home / ".codex-global-state.json"

    def status(self) -> str:
        entries, inventory = self._snapshot()
        if not inventory.main_pids and not inventory.app_server_pids:
            return "### Codex 桌面版\n- 状态：未检测到 Codex 桌面版核心进程"

        lines = [
            "### Codex 桌面版",
            "- 状态：运行中",
            (
                "- 桌面主进程 PID："
                + ("、".join(map(str, inventory.main_pids)) or "未识别")
            ),
            (
                "- 共享 app-server PID："
                + ("、".join(map(str, inventory.app_server_pids)) or "未识别")
            ),
            f"- 核心进程合计内存：{inventory.memory_mb:.0f} MiB",
            "- 说明：上述核心 PID 由多个桌面项目共享，不能归属于单个项目。",
            "",
            "### 桌面项目与进程对齐",
        ]
        if not entries:
            lines.append("- 未从桌面状态中找到本地项目。")
            return "\n".join(lines)
        for entry in entries:
            markers: list[str] = []
            if entry.selected:
                markers.append("桌面当前选择")
            if entry.active:
                markers.append("活跃工作区")
            if entry.allowlisted_as:
                markers.append(f"白名单：{','.join(entry.allowlisted_as)}")
            elif not entry.path.is_dir():
                markers.append("目录不存在")
            elif self._path_can_be_registered(entry.path):
                markers.append("可导入")
            else:
                markers.append("路径受限")
            if entry.child_pids:
                markers.append("项目子进程 PID：" + ",".join(map(str, entry.child_pids)))
            else:
                markers.append("无可独立归属的子进程")
            root_suffix = (
                f" · 根目录 {entry.root_index}/{entry.root_count}"
                if entry.root_count > 1
                else ""
            )
            lines.extend(
                [
                    f"- `{entry.index}` · **{entry.label}**{root_suffix}",
                    f"  - 路径：`{entry.path}`",
                    f"  - {'；'.join(markers)}",
                ]
            )
        lines.append("")
        lines.append("使用 `/project-allow <编号或名称> [白名单名称]` 导入可用项目。")
        return "\n".join(lines)

    def resolve_project(self, reference: str) -> DesktopProjectEntry:
        entries, _ = self._snapshot()
        if reference.isdigit():
            index = int(reference)
            for entry in entries:
                if entry.index == index:
                    return entry
            raise DesktopProjectError(f"桌面项目编号 `{reference}` 不存在")

        lowered = reference.casefold()
        id_matches = [entry for entry in entries if entry.project_id.casefold() == lowered]
        matches = id_matches or [
            entry
            for entry in entries
            if entry.label.casefold() == lowered or entry.path.name.casefold() == lowered
        ]
        if not matches:
            raise DesktopProjectError(
                f"未找到桌面项目 `{reference}`；请先使用 `/desktop-projects` 查看"
            )
        distinct_paths = {self._path_key(entry.path) for entry in matches}
        if len(distinct_paths) > 1:
            indexes = "、".join(str(entry.index) for entry in matches)
            raise DesktopProjectError(
                f"桌面项目 `{reference}` 有多个根目录，请改用编号：{indexes}"
            )
        return matches[0]

    @staticmethod
    def suggested_alias(entry: DesktopProjectEntry) -> str:
        value = "".join(
            character if character.isalnum() or character in "_-" else "_"
            for character in entry.path.name
        ).strip("_-")
        return value[:64] or f"desktop_{entry.index}"

    def _snapshot(self) -> tuple[list[DesktopProjectEntry], DesktopProcessInventory]:
        state = self._load_state()
        raw_projects = state.get("local-projects")
        if not isinstance(raw_projects, dict):
            raw_projects = {}
        selected_raw = state.get("selected-project")
        selected_id = (
            str(selected_raw.get("projectId", ""))
            if isinstance(selected_raw, dict) and selected_raw.get("type") == "local"
            else ""
        )
        active_roots = self._string_values(state.get("active-workspace-roots"))

        ordered_ids = self._string_values(state.get("project-order"))
        ordered_ids.extend(
            project_id for project_id in raw_projects if project_id not in ordered_ids
        )
        raw_entries: list[tuple[str, str, Path, int, int]] = []
        for project_id in ordered_ids:
            value = raw_projects.get(project_id)
            if not isinstance(value, dict):
                continue
            roots = self._string_values(value.get("rootPaths"))
            if not roots:
                continue
            first_path = Path(roots[0])
            label_value = value.get("label")
            label = str(label_value).strip() if label_value else first_path.name
            for root_index, root in enumerate(roots, start=1):
                raw_entries.append(
                    (project_id, label, Path(root).resolve(), root_index, len(roots))
                )

        inventory = self._process_inventory([item[2] for item in raw_entries])
        active_keys = {self._path_key(Path(value)) for value in active_roots}
        allowlisted_by_path: dict[str, list[str]] = {}
        for alias, project in self.settings.projects.copy().items():
            allowlisted_by_path.setdefault(self._path_key(project.path), []).append(alias)

        entries: list[DesktopProjectEntry] = []
        for index, (project_id, label, path, root_index, root_count) in enumerate(
            raw_entries,
            start=1,
        ):
            path_key = self._path_key(path)
            entries.append(
                DesktopProjectEntry(
                    index=index,
                    project_id=project_id,
                    label=label,
                    path=path,
                    root_index=root_index,
                    root_count=root_count,
                    selected=project_id == selected_id,
                    active=path_key in active_keys,
                    allowlisted_as=tuple(sorted(allowlisted_by_path.get(path_key, []))),
                    child_pids=inventory.project_child_pids.get(path_key, ()),
                )
            )
        return entries, inventory

    def _load_state(self) -> dict[str, Any]:
        for path in (self.state_path, self.state_path.with_suffix(".json.bak")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                return value
        return {}

    def _process_inventory(self, project_paths: list[Path]) -> DesktopProcessInventory:
        main_pids: list[int] = []
        app_server_pids: list[int] = []
        memory_mb = 0.0
        processes: dict[int, psutil.Process] = {}
        for process in psutil.process_iter(
            ["pid", "ppid", "name", "exe", "cmdline", "memory_info"]
        ):
            try:
                name = str(process.info.get("name") or "")
                executable = str(process.info.get("exe") or "")
                command = " ".join(process.info.get("cmdline") or [])
                packaged_desktop = "openai.codex_" in executable.casefold()
                is_main_process = (
                    packaged_desktop
                    and name.casefold() == "chatgpt.exe"
                    and "--type=" not in command
                )
                if is_main_process:
                    main_pids.append(process.pid)
                    memory = process.info.get("memory_info")
                    memory_mb += float(memory.rss) / (1024**2) if memory else 0.0
                if (
                    packaged_desktop
                    and name.casefold() == "codex.exe"
                    and "app-server" in command
                ):
                    app_server_pids.append(process.pid)
                    memory = process.info.get("memory_info")
                    memory_mb += float(memory.rss) / (1024**2) if memory else 0.0
                processes[process.pid] = process
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                continue

        project_pids: dict[str, set[int]] = {}
        path_keys = sorted(
            ((self._path_key(path), path) for path in project_paths),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        descendants: dict[int, psutil.Process] = {}
        for pid in app_server_pids:
            process = processes.get(pid)
            if process is None:
                continue
            try:
                for child in process.children(recursive=True):
                    descendants[child.pid] = child
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
        for pid, process in descendants.items():
            try:
                command_key = os.path.normcase(" ".join(process.cmdline()))
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            for path_key, _ in path_keys:
                if path_key in command_key:
                    project_pids.setdefault(path_key, set()).add(pid)
                    break
        return DesktopProcessInventory(
            main_pids=tuple(sorted(main_pids)),
            app_server_pids=tuple(sorted(app_server_pids)),
            memory_mb=memory_mb,
            project_child_pids={
                key: tuple(sorted(pids)) for key, pids in project_pids.items()
            },
        )

    def _path_can_be_registered(self, path: Path) -> bool:
        candidate = path.resolve()
        if not candidate.is_dir():
            return False
        for root in self.settings.management.allowed_project_roots:
            try:
                relative = candidate.relative_to(root.resolve())
            except ValueError:
                continue
            if relative.parts:
                return True
        return False

    @staticmethod
    def _string_values(value: Any) -> list[str]:
        if isinstance(value, str) and value:
            return [value]
        if isinstance(value, list):
            return [item for item in value if isinstance(item, str) and item]
        return []

    @staticmethod
    def _path_key(path: Path) -> str:
        return os.path.normcase(str(path.resolve()))
