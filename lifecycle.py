"""启动 / 关闭的四阶段执行器。

流程固定为 ``s1 初始化 -> s2 预热 -> s3 启动 -> s4 关闭``：

* **s1 初始化**：只做“能失败就早失败”的事——环境变量写入、数据库游标、
  套接字句柄、注册关闭；任何一步挂了进程根本不进入服务状态。
* **s2 预热**：把重活儿集中做完——读权威数据、起辅助线程、绑端口。
  这时端口已占但还不 accept，客户端连接会被内核排队而不是被拒。
* **s3 启动**：真正对外服务——accept 线程、推送线程、HTTP 监听。
* **s4 关闭**：逆向收尾——先把游戏数据落库，再按**注册的逆序**执行关闭回调。

关闭回调是 LIFO（后注册先执行）。比如 ``store.close`` 在 s1 最早注册，
所以 s4 最后执行，保证服务停掉、数据落库之后才断数据库连接。

设计上不做“回滚”：s2 失败时已执行到 s4，把已经建好的东西关掉就行。
"""

from __future__ import annotations

import logging
import time
from typing import Callable

## 四个阶段的机器名
PHASES = ("s1", "s2", "s3", "s4")
## 给人看的阶段名
PHASE_TITLES = {
    "s1": "初始化",
    "s2": "预热",
    "s3": "启动",
    "s4": "关闭",
}


class Lifecycle:
    """阶段推进 + 关闭回调栈。一个进程一个实例。"""

    def __init__(self, name: str = "service") -> None:
        self.name = name
        self.log = logging.getLogger(name)
        self._hooks: list[tuple[str, Callable]] = []
        self._phase = ""
        self._t0 = time.monotonic()
        self._phase_t0 = self._t0

    # ------------------------------------------------------------------ #
    # 阶段
    # ------------------------------------------------------------------ #

    def enter(self, phase: str, title: str = "") -> "Lifecycle":
        """进入一个阶段（只打日志，不做别的）。"""
        self._phase = phase
        self._phase_t0 = time.monotonic()
        label = PHASE_TITLES.get(phase, phase)
        if title:
            self.log.info("========== %s %s / %s ==========", phase, label, title)
        else:
            self.log.info("========== %s %s ==========", phase, label)
        return self

    def step(self, text: str) -> "Lifecycle":
        """阶段内一步（已完成的动作，给人看的进度）。"""
        self.log.info("   - %s", text)
        return self

    def done(self, text: str = "") -> "Lifecycle":
        """当前阶段结束，打耗时。"""
        cost = time.monotonic() - self._phase_t0
        label = PHASE_TITLES.get(self._phase, self._phase)
        suffix = f" / {text}" if text else ""
        self.log.info(
            "========== %s %s 完成 %.3fs%s ==========",
            self._phase, label, cost, suffix,
        )
        return self

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._t0

    # ------------------------------------------------------------------ #
    # 关闭回调
    # ------------------------------------------------------------------ #

    def on_shutdown(self, name: str, fn: Callable | None) -> Callable | None:
        """注册一个关闭动作（LIFO）。注册顺序就是“依赖顺序”：
        A 依赖 B ⇒ 先注册 B 的关闭，再注册 A 的关闭。
        """
        if fn is None:
            return None
        self._hooks.append((str(name), fn))
        return fn

    def hooks(self) -> tuple[str, ...]:
        """已注册的关闭动作名（注册顺序）。"""
        return tuple(name for name, _ in self._hooks)

    def shutdown(self) -> int:
        """逆序执行全部关闭动作；返回失败数量（单条失败不影响其它）。"""
        failed = 0
        for name, fn in reversed(self._hooks):
            try:
                fn()
                self.step(f"已关闭 {name}")
            except Exception:  # noqa: BLE001 - 关不干净也得继续关其它
                failed += 1
                self.log.exception("关闭 %s 失败（继续执行后面的）", name)
        self._hooks.clear()
        return failed
