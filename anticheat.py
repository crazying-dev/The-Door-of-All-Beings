"""防外挂（骨架，尚未实现任何规则）。

本期只把“位置”留好，让 s2 预热阶段已经能把它当作辅助线程启动：

```python
anticheat.start(store, interval=5)   # 规则为 0 条时只打日志、不起线程
anticheat.stop()
```

为什么不现在就写规则？因为判定“跳坐标 / 穿墙 / 移速异常”必须先有
地图碰撞体与合法移速，而那两个还没做。先建空壳的好处是：
启动流程、关闭流程、日志位置都先占好，之后加规则不需要改 main.py。

写第一条规则时只需两步（不需要改本文件）：

```python
import anticheat
anticheat.register_check("speed", my_check)
```

规则签名::

    fn(store, snapshot, now) -> list[str]

* ``snapshot`` = ``{"world": [物品行…], "players": [玩家行…]}``（拷贝，可安心遍历）；
* 返回违规描述列表（空列表 = 正常），调度器会统一打 WARNING 日志。

规则必须快、必须无副作用、必须自己吞掉可恢复异常（不吞也行，调度器会接住）。
将来真判定到违规时，做法是“写库 + 标记 + 不踢人”，不要在这里直接断连接。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

## 已注册的规则：name -> fn
CHECKS: dict[str, Callable] = {}

_lock = threading.RLock()
_store = None
_interval = 5.0
_thread: threading.Thread | None = None
_stop = threading.Event()
_stats = {"runs": 0, "violations": 0, "errors": 0}


# ---------------------------------------------------------------------------
# 规则注册
# ---------------------------------------------------------------------------

def register_check(name: str, fn: Callable) -> None:
    """注册一条巡检规则（同名覆盖）。"""
    with _lock:
        CHECKS[str(name)] = fn
    log.info("防外挂规则已注册：%s", name)


def unregister_check(name: str) -> bool:
    with _lock:
        return CHECKS.pop(str(name), None) is not None


def checks() -> list[str]:
    with _lock:
        return sorted(CHECKS)


def is_running() -> bool:
    thread = _thread
    return thread is not None and thread.is_alive()


# ---------------------------------------------------------------------------
# 巡检
# ---------------------------------------------------------------------------

def run_once() -> list[str]:
    """跑一轮全部规则，返回违规描述（不抛异常）。"""
    with _lock:
        rules = dict(CHECKS)
        store = _store
    if not rules:
        return []

    import game   # 延迟导入：避免 game <-> anticheat 循环引用

    with game.snapshot() as (items, ps):
        snap = {"world": items, "players": ps}
        now = time.time()
        found: list[str] = []
        errors = 0
        for name, fn in rules.items():
            try:
                out = fn(store, snap, now) or []
            except Exception:  # noqa: BLE001 - 单条规则报错不能拖垮巡检
                errors += 1
                log.exception("防外挂规则 %s 执行失败", name)
                continue
            for message in out:
                found.append(f"{name}: {message}")

    with _lock:
        stats["runs"] += 1
        stats["violations"] += len(found)
        stats["errors"] += errors
    return found


def start(store, interval: float = 5.0) -> bool:
    """启动巡检线程。规则为空时**不起线程**，返回 False。"""
    global _store, _interval
    with _lock:
        _store = store
        _interval = max(1.0, float(interval))
        if not CHECKS:
            log.info("防外挂：尚未实现任何规则，本轮不启动巡检线程")
            return False
        if is_running():
            return True
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="anticheat", daemon=True)
        _thread.start()
    log.info("防外挂巡检已启动（%d 条规则，每 %.1fs）", len(CHECKS), _interval)
    return True


def stop() -> None:
    """停巡检（幂等；未启动时调用也安全）。"""
    global _thread
    _stop.set()
    with _lock:
        thread = _thread
        _thread = None
    if thread is not None and thread.is_alive():
        thread.join(timeout=2.0)


def _loop() -> None:
    while not _stop.is_set():
        try:
            for message in run_once():
                log.warning("防外挂：%s", message)
        except Exception:  # noqa: BLE001 - 巡检线程不能死
            log.exception("防外挂巡检异常")
        _stop.wait(_interval)
    log.info("防外挂巡检已停止")
