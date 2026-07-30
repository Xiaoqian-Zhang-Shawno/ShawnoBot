from __future__ import annotations

import shutil
from pathlib import Path

from ..models import ProjectConfig
from ..processes import run_command


class ProjectService:
    def __init__(self, projects: dict[str, ProjectConfig], timeout_seconds: int) -> None:
        self.projects = projects
        self.timeout_seconds = timeout_seconds

    def get(self, name: str) -> ProjectConfig:
        try:
            return self.projects[name]
        except KeyError as exc:
            choices = "、".join(sorted(self.projects)) or "无"
            raise KeyError(f"未知项目 `{name}`；可用项目：{choices}") from exc

    def list_projects(self) -> str:
        if not self.projects:
            return "尚未配置项目。"
        lines = ["### 项目"]
        for name, project in sorted(self.projects.copy().items()):
            exists = "可用" if project.path.is_dir() else "目录不存在"
            description = f" — {project.description}" if project.description else ""
            lines.append(f"- **{name}**：{exists}{description}")
            if project.tasks:
                lines.append(f"  - 任务：{', '.join(sorted(project.tasks))}")
            if project.logs:
                lines.append(f"  - 日志：{', '.join(sorted(project.logs))}")
        return "\n".join(lines)

    def status(self, name: str) -> str:
        project = self.get(name)
        lines = [f"### 项目状态 · {name}", f"- 路径：`{project.path}`"]
        if not project.path.is_dir():
            lines.append("- 状态：目录不存在")
            return "\n".join(lines)

        git = shutil.which("git")
        if git and (project.path / ".git").exists():
            result = run_command(
                [git, "status", "--short", "--branch"],
                cwd=project.path,
                timeout_seconds=min(self.timeout_seconds, 30),
            )
            if result.return_code == 0:
                output = result.stdout.strip() or "工作区干净"
                lines.extend(["- Git：", "```text", output, "```"])
            else:
                lines.append("- Git：状态查询失败")
        else:
            lines.append("- Git：不是 Git 仓库或未找到 Git")

        existing_logs = sum(path.is_file() for path in project.logs.values())
        lines.append(f"- 已配置日志：{len(project.logs)}（当前存在 {existing_logs}）")
        lines.append(f"- 已配置任务：{len(project.tasks)}")
        lines.append(f"- Codex：{'允许' if project.codex_enabled else '禁用'}")
        return "\n".join(lines)

    def tail_log(self, project_name: str, log_name: str | None, lines: int) -> str:
        project = self.get(project_name)
        if not project.logs:
            return f"项目 `{project_name}` 没有配置日志。"
        selected_name = log_name or next(iter(project.logs))
        if selected_name not in project.logs:
            choices = "、".join(sorted(project.logs))
            return f"未知日志 `{selected_name}`；可用日志：{choices}"
        path = project.logs[selected_name]
        if not path.is_file():
            return f"日志 `{project_name}/{selected_name}` 尚不存在：`{path}`"
        content = _tail_text(path, lines)
        return "\n".join(
            [
                f"### 日志 · {project_name}/{selected_name} · 最后 {lines} 行",
                "```text",
                content or "（空文件）",
                "```",
            ]
        )


def _tail_text(path: Path, lines: int) -> str:
    block_size = 8192
    with path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        chunks: list[bytes] = []
        newline_count = 0
        while position > 0 and newline_count <= lines:
            read_size = min(block_size, position)
            position -= read_size
            handle.seek(position)
            chunk = handle.read(read_size)
            chunks.append(chunk)
            newline_count += chunk.count(b"\n")
    data = b"".join(reversed(chunks))
    decoded = data.decode("utf-8", errors="replace").splitlines()
    return "\n".join(decoded[-lines:])
