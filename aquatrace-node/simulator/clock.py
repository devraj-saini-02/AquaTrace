"""The one shared simulated-time source for the whole node/ process (§7). Every timestamp
placed on the wire comes from SimClock.now() — nothing reads wall-clock time directly."""
import asyncio
from datetime import datetime, timedelta, timezone


class SimClock:
    def __init__(self, time_scale: float, tick_interval_s: float = 0.25):
        self.time_scale = time_scale
        self.tick_interval_s = tick_interval_s
        self._sim_time = datetime.now(timezone.utc)
        self._running = False
        self._task: asyncio.Task | None = None

    def now(self) -> datetime:
        return self._sim_time

    def elapsed_since(self, t: datetime) -> float:
        return (self._sim_time - t).total_seconds()

    async def _run(self) -> None:
        while self._running:
            await asyncio.sleep(self.tick_interval_s)
            self._sim_time += timedelta(seconds=self.tick_interval_s * self.time_scale)

    def start(self) -> None:
        if self._running:
            return
        self._sim_time = datetime.now(timezone.utc)
        self._running = True
        self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None

    @property
    def running(self) -> bool:
        return self._running
