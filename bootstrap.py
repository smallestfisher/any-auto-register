from __future__ import annotations

import threading
from contextlib import AbstractContextManager

from core.db import init_db
from core.registry import list_platforms, load_all


_init_lock = threading.Lock()
_initialized = False


class RuntimeManager(AbstractContextManager):
    def __init__(self, *, start_background_services: bool = True, announce: bool = True):
        self.start_background_services = start_background_services
        self.announce = announce
        self._started = False

    def __enter__(self):
        initialize_core(announce=self.announce)
        if self.start_background_services:
            start_background_services()
            self._started = True
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._started:
            stop_background_services()
        return False


def initialize_core(*, announce: bool = True) -> None:
    global _initialized
    with _init_lock:
        if _initialized:
            return
        init_db()
        load_all()
        if announce:
            print("[OK] 数据库初始化完成")
            print(f"[OK] 已加载平台: {[p['name'] for p in list_platforms()]}")
        _initialized = True


def start_background_services() -> None:
    from core.scheduler import scheduler
    from services.task_runtime import task_runtime
    from services.solver_manager import start_async
    from core.lifecycle import lifecycle_manager

    scheduler.start()
    task_runtime.start()
    start_async()
    lifecycle_manager.start()


def stop_background_services() -> None:
    from core.lifecycle import lifecycle_manager
    from core.scheduler import scheduler
    from services.task_runtime import task_runtime
    from services.solver_manager import stop

    lifecycle_manager.stop()
    scheduler.stop()
    task_runtime.stop()
    stop()
