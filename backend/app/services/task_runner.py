"""合成任务的生命周期管理：启动 / 取消 / 查询。

不直接使用 Starlette 的 BackgroundTasks —— 它不暴露 asyncio.Task 引用，
无法在请求中途真正中断已经跑起来的合成（关掉前端轮询，后端仍在烧额度）。
这里改为自行 create_task 并持有引用，从而支持取消。
"""
import asyncio
import logging
import threading

logger = logging.getLogger(__name__)

# episode_id -> asyncio.Task
_tasks: dict[int, asyncio.Task] = {}
# 已被请求取消的 episode（协作式取消的兜底标志）
_cancelled: set[int] = set()
# 这几个结构只在同步代码里短暂持锁，用 threading.Lock 即可，避免 asyncio 原语的 event loop 绑定问题
_guard = threading.Lock()


def _forget(episode_id: int) -> None:
    """任务结束后回收引用，避免字典无限增长。"""
    with _guard:
        task = _tasks.get(episode_id)
        if task is not None and task.done():
            _tasks.pop(episode_id, None)


def start(episode_id: int, coro) -> bool:
    """启动合成任务。同一 episode 已有未结束任务时返回 False，不重复启动。"""
    with _guard:
        current = _tasks.get(episode_id)
        if current is not None and not current.done():
            # 调用方传进来的 coroutine 不会被使用，显式关闭，
            # 否则会留下 "coroutine was never awaited" 警告与未回收的协程对象
            close = getattr(coro, "close", None)
            if close is not None:
                close()
            return False
        # 新一轮开始，清掉上一轮遗留的取消标志
        _cancelled.discard(episode_id)
        task = asyncio.create_task(coro, name=f"synthesize-{episode_id}")
        _tasks[episode_id] = task
    # 在锁外注册回调：任务可能已完成，回调会被立即调度
    task.add_done_callback(lambda _t, eid=episode_id: _forget(eid))
    return True


def is_running(episode_id: int) -> bool:
    with _guard:
        task = _tasks.get(episode_id)
        return task is not None and not task.done()


def cancel(episode_id: int) -> bool:
    """请求取消。返回是否确实中断了一个正在运行的任务。

    task.cancel() 会让该任务在下一个 await 点抛出 CancelledError，
    正在进行的 HTTP 请求随之中断；同时置位标志，防止取消瞬间又有排队请求发出。
    空闲时调用不打标志，避免污染下一轮 start 前的 is_cancelled 读数。
    """
    with _guard:
        task = _tasks.get(episode_id)
        if task is None or task.done():
            return False
        _cancelled.add(episode_id)
        task.cancel()
    logger.info("已请求取消单集 %s 的合成任务", episode_id)
    return True


def is_cancelled(episode_id: int) -> bool:
    """协作式检查，供合成循环逐句轮询。"""
    with _guard:
        return episode_id in _cancelled


def clear(episode_id: int) -> None:
    """清理任务引用与取消标志。

    活任务不会被直接 pop：否则 is_running 变 False，还能再 start 一个并发合成，
    且旧任务再也无法被 cancel。活任务改为请求取消，等自然结束后由 _forget 回收。
    """
    with _guard:
        task = _tasks.get(episode_id)
        if task is not None and not task.done():
            _cancelled.add(episode_id)
            task.cancel()
            logger.info("clear(%s)：任务仍在运行，已改为请求取消", episode_id)
            return
        _cancelled.discard(episode_id)
        _tasks.pop(episode_id, None)


def cancel_all() -> list[int]:
    """取消全部在跑任务（进程 shutdown 时调用）。返回被取消的 episode id。"""
    cancelled: list[int] = []
    with _guard:
        for eid, task in list(_tasks.items()):
            if task is not None and not task.done():
                _cancelled.add(eid)
                task.cancel()
                cancelled.append(eid)
    if cancelled:
        logger.info("shutdown：取消合成任务 %s", cancelled)
    return cancelled
