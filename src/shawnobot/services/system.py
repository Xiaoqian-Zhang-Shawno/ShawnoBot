from __future__ import annotations

import platform
import shutil
from pathlib import Path

import psutil

from ..processes import run_command


def human_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    number = float(value)
    for unit in units:
        if number < 1024 or unit == units[-1]:
            return f"{number:.1f} {unit}"
        number /= 1024
    return f"{number:.1f} TB"


class SystemService:
    def status(self) -> str:
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(Path.cwd().anchor or "/")
        boot_seconds = max(0, int(__import__("time").time() - psutil.boot_time()))
        hours, remainder = divmod(boot_seconds, 3600)
        minutes = remainder // 60
        return "\n".join(
            [
                "### 主机状态",
                f"- 系统：{platform.system()} {platform.release()}",
                f"- CPU：{psutil.cpu_percent(interval=0.2):.0f}%（{psutil.cpu_count()} 逻辑核）",
                (
                    f"- 内存：{memory.percent:.0f}%"
                    f"（{human_bytes(memory.used)} / {human_bytes(memory.total)}）"
                ),
                f"- 系统盘：{disk.percent:.0f}%（剩余 {human_bytes(disk.free)}）",
                f"- 运行时长：{hours} 小时 {minutes} 分钟",
            ]
        )

    def gpu_status(self) -> str:
        executable = shutil.which("nvidia-smi")
        if not executable:
            return "未找到 `nvidia-smi`。请确认 NVIDIA 驱动已安装且命令位于 PATH。"
        fields = [
            "index",
            "name",
            "utilization.gpu",
            "memory.used",
            "memory.total",
            "temperature.gpu",
            "power.draw",
        ]
        result = run_command(
            [
                executable,
                f"--query-gpu={','.join(fields)}",
                "--format=csv,noheader,nounits",
            ],
            cwd=Path.cwd(),
            timeout_seconds=15,
        )
        if result.timed_out:
            return "`nvidia-smi` 查询超时。"
        if result.return_code != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            return f"GPU 查询失败：{detail}"
        lines = ["### GPU 状态"]
        for raw_line in result.stdout.splitlines():
            parts = [part.strip() for part in raw_line.split(",")]
            if len(parts) != len(fields):
                lines.append(f"- {raw_line.strip()}")
                continue
            index, name, util, used, total, temperature, power = parts
            lines.extend(
                [
                    f"**GPU {index} · {name}**",
                    f"- 利用率：{util}%",
                    f"- 显存：{used} / {total} MiB",
                    f"- 温度：{temperature} °C",
                    f"- 功耗：{power} W",
                ]
            )
        return "\n".join(lines)

    def codex_desktop_status(self) -> str:
        processes: list[tuple[int, str, float]] = []
        for process in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                name = str(process.info.get("name") or "")
                if name.lower() not in {"codex.exe", "codex"}:
                    continue
                memory = process.info.get("memory_info")
                memory_mb = float(memory.rss) / (1024**2) if memory else 0.0
                processes.append((int(process.info["pid"]), name, memory_mb))
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
        if not processes:
            return "### Codex 桌面版\n- 状态：未检测到 Codex 相关进程"
        total_memory = sum(item[2] for item in processes)
        pids = "、".join(str(item[0]) for item in processes[:12])
        return "\n".join(
            [
                "### Codex 桌面版",
                f"- 状态：运行中（相关进程 {len(processes)} 个）",
                f"- PID：{pids}",
                f"- 合计内存：{total_memory:.0f} MiB",
                "- 说明：进程状态不包含 GUI 中正在生成的内部内容。",
            ]
        )
