from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
import uuid
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

from ..models import CodexConfig, ProjectConfig
from ..processes import run_command, terminate_command


class CodexService:
    def __init__(
        self,
        config: CodexConfig,
        *,
        data_dir: Path,
        timeout_seconds: int,
        max_prompt_chars: int,
        max_parallel_runs: int,
    ) -> None:
        self.config = config
        self.data_dir = data_dir
        self.output_dir = data_dir / "codex_results"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.conversation_state_path = data_dir / "codex_conversations.json"
        self.timeout_seconds = timeout_seconds
        self.max_prompt_chars = max_prompt_chars
        self._slots = threading.BoundedSemaphore(max_parallel_runs)
        self._state_lock = threading.Lock()
        self._active_lock = threading.Lock()
        self._active_process: subprocess.Popen[str] | None = None
        self._active_label = ""
        self._active_started_at = ""

    def usage(self) -> str:
        """Read the signed-in ChatGPT Codex quota through the official app-server."""
        executable = self._find_executable()
        if executable is None:
            return f"未找到 Codex CLI：`{self.config.executable}`"

        try:
            responses, stderr, timed_out = self._query_usage_app_server(executable)
        except OSError as exc:
            return f"Codex 用量查询启动失败：{exc}"

        if 3 not in responses:
            if timed_out:
                return "Codex 用量查询超过 30 秒，已终止。"
            detail = stderr.strip()
            if detail:
                return f"Codex app-server 未返回用量数据：{detail[-1000:]}"
            return "Codex app-server 未返回用量数据；请确认 Codex CLI 已登录并为最新版。"

        account_response = responses.get(2, {})
        rate_response = responses[3]
        if "error" in rate_response:
            return self._format_rpc_error(rate_response["error"])

        account_result = account_response.get("result")
        rate_result = rate_response.get("result")
        if not isinstance(account_result, dict):
            account_result = {}
        if not isinstance(rate_result, dict):
            return "Codex app-server 返回了无法识别的用量数据。"
        return self._format_usage(account_result, rate_result)

    def _find_executable(self) -> str | None:
        configured = Path(self.config.executable)
        if configured.is_file():
            return str(configured.resolve())
        return shutil.which(self.config.executable)

    def _query_usage_app_server(
        self,
        executable: str,
    ) -> tuple[dict[int, dict[str, Any]], str, bool]:
        """Perform the required sequential app-server handshake and usage query."""
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0  # type: ignore[attr-defined]
        )
        process = subprocess.Popen(
            [executable, "app-server"],
            cwd=str(self.output_dir),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None

        output_queue: queue.Queue[str | None] = queue.Queue()
        stderr_lines: list[str] = []

        def read_stdout() -> None:
            for line in process.stdout:
                output_queue.put(line)
            output_queue.put(None)

        def read_stderr() -> None:
            stderr_lines.extend(process.stderr.readlines())

        stdout_thread = threading.Thread(target=read_stdout, daemon=True)
        stderr_thread = threading.Thread(target=read_stderr, daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        responses: dict[int, dict[str, Any]] = {}
        deadline = time.monotonic() + min(self.timeout_seconds, 30)
        timed_out = False

        def send(message: dict[str, Any]) -> None:
            process.stdin.write(
                json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            process.stdin.flush()

        def wait_for(request_ids: set[int]) -> bool:
            nonlocal timed_out
            while not request_ids.issubset(responses):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    return False
                try:
                    line = output_queue.get(timeout=remaining)
                except queue.Empty:
                    timed_out = True
                    return False
                if line is None:
                    return False
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict) and isinstance(message.get("id"), int):
                    responses[message["id"]] = message
            return True

        try:
            send(
                {
                    "method": "initialize",
                    "id": 1,
                    "params": {
                        "clientInfo": {
                            "name": "shawnobot",
                            "title": "ShawnoBot",
                            "version": "0.1.0",
                        }
                    },
                }
            )
            if wait_for({1}):
                send({"method": "initialized"})
                send(
                    {
                        "method": "account/read",
                        "id": 2,
                        "params": {"refreshToken": False},
                    }
                )
                send({"method": "account/rateLimits/read", "id": 3})
                wait_for({2, 3})
        finally:
            with suppress(OSError):
                process.stdin.close()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)

        return responses, "".join(stderr_lines), timed_out

    @staticmethod
    def _format_rpc_error(error: Any) -> str:
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("code") or "未知错误")
        else:
            detail = str(error)
        lowered = detail.lower()
        if "login" in lowered or "auth" in lowered or "credential" in lowered:
            return "Codex 尚未登录。请在运行 ShawnoBot 的同一 Windows 账号下执行 `codex login`。"
        return f"Codex 用量查询失败：{detail}"

    @classmethod
    def _format_usage(
        cls,
        account_result: dict[str, Any],
        rate_result: dict[str, Any],
    ) -> str:
        account = account_result.get("account")
        if not isinstance(account, dict):
            account = {}
        rate_limits = rate_result.get("rateLimits")
        if not isinstance(rate_limits, dict):
            rate_limits = {}

        plan = account.get("planType") or rate_limits.get("planType")
        lines = ["### Codex 用量"]
        if plan:
            lines.append(f"- 套餐：{cls._plan_label(str(plan))}")

        window_count = 0
        for key in ("primary", "secondary"):
            window = rate_limits.get(key)
            if not isinstance(window, dict):
                continue
            used = cls._number(window.get("usedPercent"))
            duration = cls._integer(window.get("windowDurationMins"))
            resets_at = cls._integer(window.get("resetsAt"))
            label = cls._window_label(duration, key)
            if used is None:
                lines.append(f"- {label}：暂无百分比数据")
            else:
                remaining = max(0.0, min(100.0, 100.0 - used))
                lines.append(
                    f"- {label}：剩余 {cls._percent(remaining)}"
                    f"（已用 {cls._percent(used)}）"
                )
            if resets_at is not None:
                lines.append(f"  - 自动恢复：{cls._local_time(resets_at)}")
            window_count += 1

        credits = rate_limits.get("credits")
        if isinstance(credits, dict):
            if credits.get("unlimited") is True:
                lines.append("- 额外 Credits：不限量")
            elif credits.get("hasCredits") is True and credits.get("balance") is not None:
                lines.append(f"- 额外 Credits 余额：{credits['balance']}")

        reset_credits = rate_result.get("rateLimitResetCredits")
        if isinstance(reset_credits, dict):
            available = cls._integer(reset_credits.get("availableCount"))
            if available is not None:
                lines.append(f"- 可用额度重置：{available} 次")
            details = reset_credits.get("credits")
            if isinstance(details, list):
                for credit in details[:3]:
                    if not isinstance(credit, dict):
                        continue
                    title = str(credit.get("title") or "额度重置")
                    expires_at = cls._integer(credit.get("expiresAt"))
                    suffix = f"，有效期至 {cls._local_time(expires_at)}" if expires_at else ""
                    lines.append(f"  - {title}{suffix}")

        reached = rate_limits.get("rateLimitReachedType")
        if reached:
            lines.append(f"- 当前限制状态：{reached}")
        if rate_result.get("spendControlReached") is True:
            lines.append("- 工作区消费上限：已达到")

        if window_count == 0:
            if not account:
                return (
                    "Codex 尚未登录，或当前登录方式不提供 ChatGPT 配额。"
                    "请在运行 ShawnoBot 的同一 Windows 账号下执行 `codex login`。"
                )
            lines.append("- 当前账户未返回可显示的配额窗口。")
        lines.extend(
            [
                "",
                "说明：这里显示的是官方配额窗口百分比，不是固定 Token 个数。",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _number(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return None

    @staticmethod
    def _integer(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return int(value)
        return None

    @staticmethod
    def _percent(value: float) -> str:
        if value.is_integer():
            return f"{int(value)}%"
        return f"{value:.1f}%"

    @staticmethod
    def _window_label(duration_minutes: int | None, fallback: str) -> str:
        labels = {
            60: "每小时额度",
            300: "5 小时额度",
            1440: "每日额度",
            10080: "每周额度",
        }
        if duration_minutes in labels:
            return labels[duration_minutes]
        if duration_minutes is not None:
            return f"{duration_minutes} 分钟额度"
        return "主要额度" if fallback == "primary" else "次要额度"

    @staticmethod
    def _local_time(timestamp: int) -> str:
        try:
            return datetime.fromtimestamp(timestamp).astimezone().strftime("%Y-%m-%d %H:%M")
        except (OverflowError, OSError, ValueError):
            return str(timestamp)

    @staticmethod
    def _plan_label(plan: str) -> str:
        labels = {
            "free": "Free",
            "plus": "Plus",
            "pro": "Pro",
            "team": "Team",
            "business": "Business",
            "enterprise": "Enterprise",
            "edu": "Edu",
            "prolite": "ProLite",
            "self_serve_business_prolite": "Business ProLite",
        }
        return labels.get(plan.lower(), plan)

    def build_argv(self, project: ProjectConfig, output_path: Path) -> list[str]:
        argv = [
            self.config.executable,
            "exec",
            "--cd",
            str(project.path),
            "--sandbox",
            self.config.sandbox,
            "--ephemeral",
            "--color",
            "never",
            "--output-last-message",
            str(output_path),
            "--skip-git-repo-check",
        ]
        if self.config.model:
            argv.extend(["--model", self.config.model])
        argv.append("-")
        return argv

    def build_chat_argv(
        self,
        project: ProjectConfig,
        output_path: Path,
        session_id: str | None,
    ) -> list[str]:
        if session_id:
            argv = [
                self.config.executable,
                "exec",
                "resume",
                "--all",
                "--json",
                "-c",
                f'sandbox_mode="{self.config.sandbox}"',
                "--output-last-message",
                str(output_path),
                "--skip-git-repo-check",
            ]
            if self.config.model:
                argv.extend(["--model", self.config.model])
            argv.extend([session_id, "-"])
            return argv

        argv = [
            self.config.executable,
            "exec",
            "--cd",
            str(project.path),
            "--sandbox",
            self.config.sandbox,
            "--json",
            "--color",
            "never",
            "--output-last-message",
            str(output_path),
            "--skip-git-repo-check",
        ]
        if self.config.model:
            argv.extend(["--model", self.config.model])
        argv.append("-")
        return argv

    def chat(self, project: ProjectConfig, prompt: str, conversation_key: str) -> str:
        error = self._validate_agent_request(project, prompt)
        if error:
            return error
        state = self._conversation_state(conversation_key)
        session_id: str | None = None
        if state and state.get("project") == project.name:
            candidate = state.get("session_id")
            if isinstance(candidate, str):
                session_id = candidate
        result = self._run_persistent(
            project,
            prompt,
            session_id,
            label=f"连续对话/{project.name}",
        )
        if result[0] is None:
            return result[2]
        actual_session_id, final_message, _ = result
        self._save_conversation_state(
            conversation_key,
            {
                "session_id": actual_session_id,
                "project": project.name,
                "updated_at": datetime.now().astimezone().isoformat(),
            },
        )
        return "\n".join(
            [
                f"### Codex 对话 · {project.name}",
                f"- 会话：`{actual_session_id}`",
                f"- 沙箱：`{self.config.sandbox}`",
                "",
                final_message or "Codex 已完成，但未返回文本。",
            ]
        )

    def resume_session(
        self,
        project: ProjectConfig,
        session_id: str,
        prompt: str,
        *,
        title: str,
    ) -> str:
        error = self._validate_agent_request(project, prompt)
        if error:
            return error
        try:
            uuid.UUID(session_id)
        except ValueError:
            return "Codex 会话 ID 格式无效。"
        actual_session_id, final_message, error_message = self._run_persistent(
            project,
            prompt,
            session_id,
            timeout_seconds=min(self.timeout_seconds, 300),
            label=f"桌面会话/{title}",
        )
        if actual_session_id is None:
            return error_message
        return "\n".join(
            [
                f"### 桌面会话已续写 · {title}",
                f"- 会话：`{actual_session_id}`",
                f"- 沙箱：`{self.config.sandbox}`",
                "- 提醒：不要同时在桌面版和飞书中向该会话发送消息。",
                "",
                final_message or "Codex 已完成，但未返回文本。",
            ]
        )

    def create_session(
        self,
        project: ProjectConfig,
        prompt: str,
        *,
        title: str,
    ) -> tuple[str | None, str]:
        error = self._validate_agent_request(project, prompt)
        if error:
            return None, error
        session_id, final_message, error_message = self._run_persistent(
            project,
            prompt,
            None,
            label=f"新建会话/{title}",
        )
        if session_id is None:
            return None, error_message
        response = "\n".join(
            [
                f"### Codex 会话已创建 · {title}",
                f"- 会话：`{session_id}`",
                f"- 项目：`{project.name}`",
                f"- 沙箱：`{self.config.sandbox}`",
                "",
                final_message or "Codex 已完成，但未返回文本。",
            ]
        )
        return session_id, response

    def conversation_context(self, conversation_key: str) -> str:
        state = self._conversation_state(conversation_key)
        if not state:
            return "当前飞书聊天尚未建立 Codex 连续会话。直接发送自然语言即可创建。"
        return "\n".join(
            [
                "### 当前 Codex 对话",
                f"- 项目：`{state.get('project', '未知')}`",
                f"- 会话：`{state.get('session_id', '未知')}`",
                f"- 最近更新：{state.get('updated_at', '未知')}",
                f"- 沙箱：`{self.config.sandbox}`",
            ]
        )

    def reset_conversation(self, conversation_key: str) -> str:
        with self._state_lock:
            states = self._load_conversation_states()
            previous = states.pop(conversation_key, None)
            self._write_conversation_states(states)
        if previous:
            return "已解除当前飞书聊天与旧 Codex 会话的关联；下一条自然语言会创建新会话。"
        return "当前飞书聊天没有需要重置的 Codex 会话。"

    def _validate_agent_request(self, project: ProjectConfig, prompt: str) -> str | None:
        if not self.config.enabled:
            return "Codex 远程调用当前为全局禁用。"
        if not project.codex_enabled:
            return f"项目 `{project.name}` 未启用 Codex。"
        if not prompt.strip():
            return "Codex 指令不能为空。"
        if len(prompt) > self.max_prompt_chars:
            return f"Codex 指令过长；上限为 {self.max_prompt_chars} 字符。"
        if not project.path.is_dir():
            return f"项目目录不存在：`{project.path}`"
        if self._find_executable() is None:
            return f"未找到 Codex CLI：`{self.config.executable}`"
        return None

    def _run_persistent(
        self,
        project: ProjectConfig,
        prompt: str,
        session_id: str | None,
        *,
        timeout_seconds: int | None = None,
        label: str = "Codex",
    ) -> tuple[str | None, str, str]:
        if not self._slots.acquire(blocking=False):
            return None, "", "已有 Codex 任务在运行，请稍后再试。"
        output_path = self.output_dir / f"{uuid.uuid4().hex}.md"
        try:
            result = run_command(
                self.build_chat_argv(project, output_path, session_id),
                cwd=project.path,
                timeout_seconds=timeout_seconds or self.timeout_seconds,
                input_text=prompt,
                on_start=lambda process: self._set_active(process, label),
                on_finish=self._clear_active,
            )
            if result.timed_out:
                actual_timeout = timeout_seconds or self.timeout_seconds
                return None, "", f"Codex 执行超过 {actual_timeout} 秒，已终止。"
            final_message = (
                output_path.read_text(encoding="utf-8", errors="replace").strip()
                if output_path.is_file()
                else ""
            )
            if result.return_code != 0:
                detail = (result.stderr or result.stdout).strip()
                return (
                    None,
                    "",
                    "\n".join(
                        [
                            f"Codex 执行失败（退出码 {result.return_code}）。",
                            "```text",
                            detail[-2000:] or "无错误详情",
                            "```",
                        ]
                    ),
                )
            actual_session_id = session_id or self._thread_id_from_events(result.stdout)
            if not actual_session_id:
                return None, "", "Codex 已回复，但未返回可继续的会话 ID。"
            return actual_session_id, final_message, ""
        except OSError as exc:
            return None, "", f"Codex 启动失败：{exc}"
        finally:
            self._slots.release()

    @staticmethod
    def _thread_id_from_events(stdout: str) -> str | None:
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") not in {"thread.started", "thread_started"}:
                continue
            value = event.get("thread_id") or event.get("threadId")
            if isinstance(value, str):
                return value
        return None

    def _conversation_state(self, conversation_key: str) -> dict[str, Any] | None:
        with self._state_lock:
            value = self._load_conversation_states().get(conversation_key)
        return value if isinstance(value, dict) else None

    def _save_conversation_state(self, conversation_key: str, value: dict[str, Any]) -> None:
        with self._state_lock:
            states = self._load_conversation_states()
            states[conversation_key] = value
            self._write_conversation_states(states)

    def _load_conversation_states(self) -> dict[str, Any]:
        if not self.conversation_state_path.is_file():
            return {}
        try:
            value = json.loads(self.conversation_state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write_conversation_states(self, states: dict[str, Any]) -> None:
        temporary = self.conversation_state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(states, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.conversation_state_path)

    def active_status(self) -> str:
        with self._active_lock:
            process = self._active_process
            label = self._active_label
            started_at = self._active_started_at
        if process is None or process.poll() is not None:
            return "当前没有正在运行的 Codex 任务。"
        return "\n".join(
            [
                "### Codex 当前任务",
                f"- 类型：{label}",
                f"- PID：{process.pid}",
                f"- 开始时间：{started_at}",
                "- 可使用 `/codex-cancel` 终止该任务。",
            ]
        )

    def cancel_active(self) -> str:
        with self._active_lock:
            process = self._active_process
            label = self._active_label
        if process is None or process.poll() is not None:
            return "当前没有可终止的 Codex 任务。"
        try:
            terminate_command(process)
        except (OSError, psutil.Error) as exc:
            return f"Codex 任务终止失败：{exc}"
        return f"已请求终止 Codex 任务：{label}（PID {process.pid}）。"

    def _set_active(self, process: subprocess.Popen[str], label: str) -> None:
        with self._active_lock:
            self._active_process = process
            self._active_label = label
            self._active_started_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")

    def _clear_active(self, process: subprocess.Popen[str]) -> None:
        with self._active_lock:
            if self._active_process is process:
                self._active_process = None
                self._active_label = ""
                self._active_started_at = ""

    def run(self, project: ProjectConfig, prompt: str) -> str:
        error = self._validate_agent_request(project, prompt)
        if error:
            return error
        if not self._slots.acquire(blocking=False):
            return "已有 Codex 任务在运行，请稍后再试。"

        output_path = self.output_dir / f"{uuid.uuid4().hex}.md"
        try:
            result = run_command(
                self.build_argv(project, output_path),
                cwd=project.path,
                timeout_seconds=self.timeout_seconds,
                input_text=prompt,
                on_start=lambda process: self._set_active(
                    process,
                    f"一次性任务/{project.name}",
                ),
                on_finish=self._clear_active,
            )
            if result.timed_out:
                return f"Codex 执行超过 {self.timeout_seconds} 秒，已终止。"
            if output_path.is_file():
                final_message = output_path.read_text(encoding="utf-8", errors="replace").strip()
            else:
                final_message = result.stdout.strip()
            if result.return_code != 0:
                detail = (result.stderr or result.stdout).strip()
                return "\n".join(
                    [
                        f"Codex 执行失败（退出码 {result.return_code}）。",
                        "```text",
                        detail or "无错误详情",
                        "```",
                    ]
                )
            return "\n".join(
                [
                    f"### Codex · {project.name}",
                    f"- 沙箱：`{self.config.sandbox}`",
                    "",
                    final_message or "Codex 已完成，但未返回文本。",
                ]
            )
        except OSError as exc:
            return f"Codex 启动失败：{exc}"
        finally:
            self._slots.release()
