import asyncio
import json
import time
import uuid

from .state import Action


class Engine:
    def __init__(self, store, model, tools, max_steps=12):
        self.store, self.model, self.tools = store, model, tools
        self.max_steps = max_steps
        self.jobs = {}
        self.model_gate = asyncio.Semaphore(1)
        self.phone = None

    def start(self, task_id):
        if task_id in self.jobs and not self.jobs[task_id].done():
            raise ValueError("任务正在运行")
        task = self.store.get(task_id)
        if any(e["status"] == "unknown" for e in task["executions"]):
            raise ValueError("存在结果未知的工具操作，请核实后新建任务，不自动重放")
        if task["status"] in {"cancelled", "completed"}:
            raise ValueError("任务已结束")
        task["status"] = "running"
        self.store.save(task, "running")
        self.jobs[task_id] = asyncio.create_task(self.run(task_id))

    async def stop(self, task_id, status="paused"):
        task = self.store.get(task_id)
        if task["status"] in {"completed", "cancelled"}:
            return
        task["status"] = status
        for execution in task["executions"]:
            if execution["status"] == "running":
                execution["status"] = "unknown"
        self.store.save(task, status)
        job = self.jobs.get(task_id)
        if job and not job.done():
            job.cancel()
            try:
                await job
            except asyncio.CancelledError:
                pass
        # Always explicitly attempt to release only this task's hardware lease.
        if any(e["tool"] in {"phone.dial", "phone.answer"} for e in task["executions"]):
            try:
                result = await self.tools.execute("phone.hangup", {}, task_id, uuid.uuid4().hex)
                self.store.save(self.store.get(task_id), "phone_cleanup", result)
            except Exception:
                self.store.save(
                    self.store.get(task_id),
                    "phone_cleanup_failed",
                    {"message": "请检查实际通话状态"},
                )

    async def run(self, task_id):
        deadline, failures = time.monotonic() + 300, 0
        try:
            for _ in range(self.max_steps):
                task = self.store.get(task_id)
                if task["status"] != "running":
                    return
                if time.monotonic() >= deadline:
                    raise TimeoutError("任务执行时间达到上限")
                pending = task["pending"]
                if pending and pending.get("approved"):
                    action = Action.model_validate(pending["action"])
                    task["pending"] = None
                else:
                    async with self.model_gate:
                        action = await asyncio.wait_for(
                            self.model.decide(task, self.tools.catalog()),
                            timeout=min(
                                getattr(self.model, "request_timeout", 60),
                                max(0.01, deadline - time.monotonic()),
                            ),
                        )
                if action.plan:
                    task["plan"] = action.plan
                task["steps"] += 1
                if action.action != "call_tool":
                    task["messages"].append({"role": "assistant", "content": action.message})
                    task["status"] = {"complete": "completed", "fail": "failed"}.get(
                        action.action, "waiting_user"
                    )
                    task["result"] = action.message
                    self.store.save(task, action.action, {"message": action.message})
                    if action.action in {"complete", "fail"} and any(
                        e["tool"] in {"phone.dial", "phone.answer"} for e in task["executions"]
                    ):
                        try:
                            result = await self.tools.execute(
                                "phone.hangup", {}, task_id, uuid.uuid4().hex
                            )
                            self.store.save(task, "phone_cleanup", result)
                        except Exception:
                            self.store.save(
                                task,
                                "phone_cleanup_failed",
                                {"message": "任务结束但电话释放未确认，请检查电话"},
                            )
                    return
                args = self.tools.validate(action.tool, action.arguments)
                if not (pending and pending.get("approved")) and self.tools.needs_approval(
                    action.tool, args, task
                ):
                    task["pending"] = {"action": action.model_dump(), "approved": False}
                    task["status"] = "waiting_approval"
                    self.store.save(task, "approval_required", task["pending"])
                    return
                execution = {
                    "id": uuid.uuid4().hex,
                    "tool": action.tool,
                    "arguments": args,
                    "status": "running",
                    "started_at": time.time(),
                }
                task["executions"].append(execution)
                self.store.save(task, "tool_started", execution)
                try:
                    result = await asyncio.wait_for(
                        self.tools.execute(action.tool, args, task_id, execution["id"]), timeout=25
                    )
                    execution["status"], execution["result"] = "succeeded", result
                    failures = 0
                except Exception as exc:
                    execution["status"] = (
                        "unknown"
                        if action.tool in {"phone.dial", "phone.answer", "phone.hangup"}
                        else "failed"
                    )
                    execution["result"] = {
                        "error": type(exc).__name__,
                        "message": "工具失败，请检查配置或输入；未声明操作成功",
                    }
                    failures += 1
                task["messages"].append(
                    {
                        "role": "tool",
                        "name": action.tool,
                        "content": json.dumps(execution["result"], ensure_ascii=False),
                    }
                )
                self.store.save(task, "tool_finished", execution)
                if (
                    self.phone is not None
                    and action.tool in {"phone.dial", "phone.answer"}
                    and execution["status"] == "succeeded"
                ):
                    try:
                        self.phone.start(
                            args.get("number", ""), task["goal"], task_id=task_id, attached=True
                        )
                    except Exception:
                        await self.tools.execute("phone.hangup", {}, task_id, uuid.uuid4().hex)
                        raise RuntimeError("语音会话启动失败，已请求释放电话") from None
                    # The phone controller now owns task state and conversation turns.
                    return
                if failures >= 2 or execution["status"] == "unknown":
                    raise RuntimeError("工具失败或结果未知，任务已暂停，需核实")
            raise RuntimeError("达到单次执行步数上限，任务已暂停")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            task = self.store.get(task_id)
            task["status"] = "paused"
            # Provider errors are deliberately sanitized in the adapter.
            message = (
                str(exc)
                if type(exc).__name__ in {"ProviderError", "RuntimeError"}
                else type(exc).__name__
            )
            self.store.save(task, "error", {"message": message})

    async def shutdown(self):
        for task_id, job in list(self.jobs.items()):
            if not job.done():
                await self.stop(task_id)
