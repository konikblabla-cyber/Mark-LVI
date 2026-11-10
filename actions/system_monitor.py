"""
System Monitor — background metric checks with voice alert support.
Zero subprocess calls on all platforms — uses ctypes/pynvml/psutil/wmi only.
"""
import ctypes
import os
import platform
import time
from pathlib import Path

import psutil

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

DEFAULT_THRESHOLDS = {
    "cpu":  90.0,
    "ram":  90.0,
    "temp": 85.0,
    "gpu":  95.0,
    "disk_free_percent": 8.0,
    "battery": 15.0,
}

_COOLDOWN   = 300
_CPU_STREAK = 3

# ── NVML DLL cache (Windows: nvml.dll, Linux: libnvidia-ml.so.1) ─────────────
_nvml_lib: object = None
_nvml_ok:  object = None   # None=untested  True=works  False=unavailable


def _nvml_gpu() -> float:
    """GPU utilisation via NVML — zero subprocess on all platforms."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            if _OS == "Windows":
                candidates = ("nvml", r"C:\Windows\System32\nvml.dll")
                _load = ctypes.WinDLL
            else:
                candidates = (
                    "libnvidia-ml.so.1",
                    "libnvidia-ml.so",
                    "libnvidia-ml.dylib",
                )
                _load = ctypes.CDLL
            for name in candidates:
                try:
                    lib = _load(name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            _nvml_ok = False
            return -1.0

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        u = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
        _nvml_ok = True
        return float(u.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


def _get_gpu_usage() -> float:
    # pynvml — subprocess-free, works everywhere if installed
    try:
        import pynvml  # type: ignore
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)
    except Exception:
        pass

    return _nvml_gpu()


def _get_cpu_temp() -> float:
    # psutil — works on Linux; occasionally Windows with proper drivers
    try:
        temps = psutil.sensors_temperatures()
        for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                     "cpu-thermal", "zenpower", "it8688"]:
            if name in temps and temps[name]:
                return temps[name][0].current
        for entries in temps.values():
            if entries:
                return entries[0].current
    except Exception:
        pass

    # Windows: wmi module (pure Python COM, zero subprocess)
    if _OS == "Windows":
        try:
            import wmi  # type: ignore
            w = wmi.WMI(namespace="root/wmi")
            tz = w.MSAcpi_ThermalZoneTemperature()
            if tz:
                return (tz[0].CurrentTemperature / 10.0) - 273.15
        except Exception:
            pass

    return -1.0


def _system_disk_usage() -> dict | None:
    """Usage of the Windows system volume (or root on other hosts)."""
    try:
        root = os.environ.get("SystemDrive", "").strip()
        if root:
            root = os.path.join(root, os.sep)
        else:
            root = Path.home().anchor or str(Path.home())
        usage = psutil.disk_usage(root)
        total = int(usage.total)
        free = int(usage.free)
        return {
            "total_gb": round(total / 1024 ** 3, 1),
            "free_gb": round(free / 1024 ** 3, 1),
            "free_percent": round((free / total) * 100, 1) if total else None,
        }
    except Exception:
        return None


def _battery_info() -> dict | None:
    """Battery information where the hardware/driver exposes it."""
    try:
        battery = psutil.sensors_battery()
        if battery is None:
            return None
        percent = float(battery.percent)
        return {
            "percent": round(max(0.0, min(100.0, percent)), 1),
            "plugged": bool(battery.power_plugged),
        }
    except Exception:
        return None


def _network_adapters_up() -> int | None:
    """Count active network interfaces; this does not claim internet access."""
    try:
        stats = psutil.net_if_stats()
        return sum(1 for name, item in stats.items()
                   if item.isup and "loopback" not in name.casefold())
    except Exception:
        return None


def _top_memory_processes(limit: int = 5) -> list[dict]:
    """Return process names and memory use only; never expose command lines."""
    found = []
    try:
        for process in psutil.process_iter(["name", "memory_info"]):
            try:
                info = process.info
                name = str(info.get("name") or "").strip()
                memory = info.get("memory_info")
                rss = int(getattr(memory, "rss", 0) or 0)
                if name and rss > 0:
                    found.append({
                        "name": name[:80],
                        "memory_mb": round(rss / 1024 ** 2, 1),
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                continue
    except Exception:
        return []
    found.sort(key=lambda item: item["memory_mb"], reverse=True)
    return found[:max(1, min(int(limit), 10))]


def get_system_status() -> dict:
    """Snapshot of Windows performance, power, storage and process health."""
    cpu = psutil.cpu_percent(interval=0.2)
    ram = psutil.virtual_memory()
    temp = _get_cpu_temp()
    gpu = _get_gpu_usage()
    disk = _system_disk_usage()
    battery = _battery_info()
    network_up = _network_adapters_up()

    boot_time = psutil.boot_time()
    uptime_secs = max(0.0, time.time() - boot_time)
    uptime_h = int(uptime_secs // 3600)
    uptime_m = int((uptime_secs % 3600) // 60)

    return {
        "cpu_percent": round(cpu, 1),
        "ram_percent": round(ram.percent, 1),
        "ram_used_gb": round(ram.used / 1024 ** 3, 1),
        "ram_total_gb": round(ram.total / 1024 ** 3, 1),
        "cpu_temp_c": round(temp, 1) if temp > 0 else None,
        "gpu_percent": round(gpu, 1) if gpu >= 0 else None,
        "disk": disk,
        "battery": battery,
        "network_adapters_up": network_up,
        "top_memory_processes": _top_memory_processes(),
        "uptime": f"{uptime_h}h {uptime_m}m",
        "process_count": len(psutil.pids()),
    }


class SystemMonitor:
    """
    Stateful monitor — cooldown state persists across session reconnections.
    Call check() periodically; returns a [SYSTEM_ALERT] string or None.
    """

    def __init__(self, thresholds: dict | None = None):
        self.thresholds   = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self._last_alert: dict[str, float] = {}
        self._cpu_streak  = 0

    def _can_alert(self, key: str) -> bool:
        return (time.monotonic() - self._last_alert.get(key, 0)) > _COOLDOWN

    def _record(self, key: str):
        self._last_alert[key] = time.monotonic()

    def check(self) -> str | None:
        try:
            cpu  = psutil.cpu_percent(interval=None)
            ram  = psutil.virtual_memory().percent
            temp = _get_cpu_temp()
            gpu  = _get_gpu_usage()
            disk = _system_disk_usage()
            battery = _battery_info()
        except Exception:
            return None

        alerts: list[str] = []

        if cpu >= self.thresholds["cpu"]:
            self._cpu_streak += 1
            if self._cpu_streak >= _CPU_STREAK and self._can_alert("cpu"):
                alerts.append(
                    f"[SYSTEM_ALERT] CPU usage has been critically high ({cpu:.0f}%) "
                    "for several seconds. Warn the user in their language and suggest "
                    "closing heavy applications."
                )
                self._record("cpu")
                self._cpu_streak = 0
        else:
            self._cpu_streak = 0

        if ram >= self.thresholds["ram"] and self._can_alert("ram"):
            alerts.append(
                f"[SYSTEM_ALERT] RAM is at {ram:.0f}% — nearly exhausted. "
                "Warn the user in their language and suggest freeing memory."
            )
            self._record("ram")

        if temp > 0 and temp >= self.thresholds["temp"] and self._can_alert("temp"):
            alerts.append(
                f"[SYSTEM_ALERT] CPU temperature is {temp:.0f}°C — above the safe limit. "
                "Warn the user in their language and advise reducing system load "
                "or checking cooling."
            )
            self._record("temp")

        if gpu >= 0 and gpu >= self.thresholds["gpu"] and self._can_alert("gpu"):
            alerts.append(
                f"[SYSTEM_ALERT] GPU load is at {gpu:.0f}%. "
                "Briefly inform the user in their language."
            )
            self._record("gpu")

        if disk and disk.get("free_percent") is not None:
            free_percent = float(disk["free_percent"])
            if free_percent <= self.thresholds["disk_free_percent"] and self._can_alert("disk"):
                alerts.append(
                    f"[SYSTEM_ALERT] System drive has only {free_percent:.1f}% free "
                    f"({disk['free_gb']:.1f} GB). Explain that storage is nearly full "
                    "and offer to inspect large folders; do not delete anything automatically."
                )
                self._record("disk")

        if battery and battery.get("plugged") is False:
            battery_percent = float(battery["percent"])
            if battery_percent <= self.thresholds["battery"] and self._can_alert("battery"):
                alerts.append(
                    f"[SYSTEM_ALERT] Laptop battery is low ({battery_percent:.0f}%) "
                    "and external power is not connected. Tell the user calmly."
                )
                self._record("battery")

        return " ".join(alerts) if alerts else None


def diagnose_computer(parameters: dict, player=None, session_memory=None) -> str:
    """Return a read-only Windows health report with actionable observations."""
    if _OS != "Windows":
        return "Computer diagnostics are currently supported on Windows only."
    try:
        status = get_system_status()
    except Exception as exc:
        return f"Could not collect system diagnostics: {type(exc).__name__}."

    findings = []
    cpu = status["cpu_percent"]
    ram = status["ram_percent"]
    if cpu >= 90:
        findings.append(f"CPU is heavily loaded ({cpu:.0f}%).")
    if ram >= 90:
        findings.append(f"Memory is nearly full ({ram:.0f}%).")
    disk = status.get("disk") or {}
    free_percent = disk.get("free_percent")
    if free_percent is not None and free_percent <= 10:
        findings.append(
            f"System drive is low on space ({disk.get('free_gb', '?')} GB free, "
            f"{free_percent:.1f}%)."
        )
    temp = status.get("cpu_temp_c")
    if temp is not None and temp >= 85:
        findings.append(f"CPU temperature is high ({temp:.0f}°C).")
    battery = status.get("battery")
    if battery and not battery.get("plugged") and battery.get("percent", 100) <= 15:
        findings.append(f"Battery is low ({battery['percent']:.0f}%) and power is disconnected.")

    top = status.get("top_memory_processes") or []
    top_text = ", ".join(
        f"{item['name']} ({item['memory_mb']:.0f} MB)" for item in top[:3]
    ) or "unavailable"
    disk_text = (
        f"{disk.get('free_gb', '?')} GB free of {disk.get('total_gb', '?')} GB"
        if disk else "unavailable"
    )
    gpu = status.get("gpu_percent")
    gpu_text = f"{gpu:.0f}%" if gpu is not None else "not exposed by the installed driver"
    temp_text = f"{temp:.0f}°C" if temp is not None else "not exposed by the installed driver"
    battery = status.get("battery")
    battery_text = (
        f"{battery['percent']:.0f}% ({'plugged in' if battery.get('plugged') else 'on battery'})"
        if battery else "not available"
    )
    network = status.get("network_adapters_up")
    network_text = str(network) if network is not None else "unknown"
    summary = "No critical resource warning detected." if not findings else " ".join(findings)
    return (
        f"Windows health report: CPU {cpu:.0f}%; RAM {ram:.0f}% "
        f"({status['ram_used_gb']:.1f}/{status['ram_total_gb']:.1f} GB); "
        f"GPU {gpu_text}; CPU temperature {temp_text}; system drive {disk_text}; "
        f"battery {battery_text}; active network adapters {network_text}; "
        f"uptime {status['uptime']}; {status['process_count']} processes. "
        f"Top memory users: {top_text}. {summary} "
        "This report is read-only; no process was stopped and no files or settings were changed."
    )


TOOL = {
    "name": "diagnose_computer",
    "description": (
        "Run a read-only Windows computer health check. Reports CPU, RAM, GPU "
        "when available, CPU temperature when exposed by the driver, system "
        "drive space, battery, uptime and top memory-using process names. Use "
        "when the user asks what is wrong, whether the PC is overloaded, or "
        "for a system health report. Never claim unavailable sensor readings. "
        "This action never kills processes, deletes files or changes settings."
    ),
    "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    "handler": diagnose_computer,
}
