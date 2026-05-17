import asyncio
import time
import platform
import socket
from typing import Optional

import psutil


class SystemMetrics:
    """Collect system metrics (CPU, RAM, disk, network, system info)."""

    @staticmethod
    async def collect() -> dict:
        loop = asyncio.get_event_loop()
        cpu = await loop.run_in_executor(None, lambda: psutil.cpu_percent(interval=0.5))
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        net = psutil.net_io_counters()
        disk_io = psutil.disk_io_counters()
        boot_time = psutil.boot_time()

        system_info = {
            "hostname": socket.gethostname(),
            "platform": platform.system(),
            "platform_release": platform.release(),
            "architecture": platform.machine(),
            "uptime_seconds": int(time.time() - boot_time),
        }

        try:
            load_avg = psutil.getloadavg()
            system_info["load_avg"] = {"1min": load_avg[0], "5min": load_avg[1], "15min": load_avg[2]}
        except Exception:
            system_info["load_avg"] = None

        process_count = len(psutil.pids())

        db_status = "unknown"
        try:
            from app.core.database import AsyncSessionFactory
            async with AsyncSessionFactory() as session:
                from sqlalchemy import text
                await session.execute(text("SELECT 1"))
            db_status = "connected"
        except Exception:
            db_status = "error"

        return {
            "cpu": round(cpu, 1),
            "ram": {
                "used": round(mem.used / (1024 ** 3), 2),
                "total": round(mem.total / (1024 ** 3), 2),
                "percent": mem.percent,
                "available": round(mem.available / (1024 ** 3), 2),
            },
            "disk": {
                "used": round(disk.used / (1024 ** 3), 2),
                "total": round(disk.total / (1024 ** 3), 2),
                "percent": disk.percent,
                "free": round(disk.free / (1024 ** 3), 2),
            },
            "disk_io": {
                "read_mb": round(disk_io.read_bytes / (1024 ** 2), 2) if disk_io else 0,
                "write_mb": round(disk_io.write_bytes / (1024 ** 2), 2) if disk_io else 0,
            },
            "net": {
                "sent_mb": round(net.bytes_sent / (1024 ** 2), 2),
                "recv_mb": round(net.bytes_recv / (1024 ** 2), 2),
                "packets_sent": net.packets_sent,
                "packets_recv": net.packets_recv,
                "errors_in": net.errin,
                "errors_out": net.errout,
            },
            "system": system_info,
            "processes": process_count,
            "database": db_status,
        }
