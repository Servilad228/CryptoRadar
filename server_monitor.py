"""
CryptoRadar — Мониторинг серверных ресурсов.
CPU, RAM (с пиком за 30 минут), Disk.
"""

import threading
import time
from collections import deque

import psutil

from logger import log


class ServerMonitor:
    """Мониторинг ресурсов с трекингом пика RAM за 30 минут."""

    def __init__(self, history_seconds: int = 1800, sample_interval: int = 10):
        """
        history_seconds: окно истории (30 мин = 1800с)
        sample_interval: интервал сэмплирования (10с)
        """
        max_samples = history_seconds // sample_interval
        self._ram_history: deque[float] = deque(maxlen=max_samples)
        self._sample_interval = sample_interval
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self):
        """Запуск фонового сбора метрик."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()
        log.info("ServerMonitor: фоновый сбор метрик запущен")

    def stop(self):
        """Остановка сбора."""
        self._running = False

    def _sample_loop(self):
        """Фоновый цикл сэмплирования RAM."""
        while self._running:
            try:
                mem = psutil.virtual_memory()
                self._ram_history.append(mem.percent)
            except Exception:
                pass
            time.sleep(self._sample_interval)

    def get_cpu_load(self) -> dict:
        """CPU load average за 1m, 5m, 15m."""
        try:
            load = psutil.getloadavg()
            cpu_count = psutil.cpu_count() or 1
            return {
                "1m": round(load[0] / cpu_count * 100, 1),
                "5m": round(load[1] / cpu_count * 100, 1),
                "15m": round(load[2] / cpu_count * 100, 1),
            }
        except Exception:
            return {"1m": 0, "5m": 0, "15m": 0}

    def get_ram_usage(self) -> dict:
        """RAM: total, used, free, percent, peak_30m."""
        try:
            mem = psutil.virtual_memory()
            peak = max(self._ram_history) if self._ram_history else mem.percent
            return {
                "total_gb": round(mem.total / (1024 ** 3), 1),
                "used_gb": round(mem.used / (1024 ** 3), 1),
                "free_gb": round(mem.available / (1024 ** 3), 1),
                "percent": mem.percent,
                "peak_30m": round(peak, 1),
            }
        except Exception:
            return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent": 0, "peak_30m": 0}

    def get_disk_usage(self) -> dict:
        """Disk: total, used, free, percent."""
        try:
            disk = psutil.disk_usage("/")
            return {
                "total_gb": round(disk.total / (1024 ** 3), 1),
                "used_gb": round(disk.used / (1024 ** 3), 1),
                "free_gb": round(disk.free / (1024 ** 3), 1),
                "percent": round(disk.percent, 1),
            }
        except Exception:
            return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent": 0}

    def format_report(self) -> str:
        """Форматированный отчёт для Telegram."""
        cpu = self.get_cpu_load()
        ram = self.get_ram_usage()
        disk = self.get_disk_usage()

        return (
            f"📡 Сервер:\n"
            f"  CPU:  1m: {cpu['1m']}% | 5m: {cpu['5m']}% | 15m: {cpu['15m']}%\n"
            f"  RAM:  {ram['used_gb']} / {ram['total_gb']} GB ({ram['percent']}%)"
            f" | Пик 30м: {ram['peak_30m']}%\n"
            f"  Disk: {disk['used_gb']} / {disk['total_gb']} GB ({disk['percent']}%)"
        )


# Глобальный экземпляр
monitor = ServerMonitor()
