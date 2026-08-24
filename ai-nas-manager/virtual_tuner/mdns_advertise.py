"""仮想Tuner自身をmDNS/Bonjourでアドバタイズする処理。"""

from __future__ import annotations

import logging
import socket

from zeroconf import ServiceInfo, Zeroconf

logger = logging.getLogger(__name__)

SERVICE_TYPE = "_tuner._tcp.local."

_zeroconf: Zeroconf | None = None
_service_info: ServiceInfo | None = None


def _local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def advertise(name: str, port: int) -> None:
    """`<name>.SERVICE_TYPE` としてmDNSアドバタイズを開始する。"""
    global _zeroconf, _service_info

    ip = _local_ip()
    info = ServiceInfo(
        SERVICE_TYPE,
        f"{name}.{SERVICE_TYPE}",
        addresses=[socket.inet_aton(ip)],
        port=port,
        properties={"kind": "virtual"},
        server=f"{socket.gethostname()}.local.",
    )

    _zeroconf = Zeroconf()
    _zeroconf.register_service(info)
    _service_info = info
    logger.info("advertising %s at %s:%s", info.name, ip, port)


def unadvertise() -> None:
    """アドバタイズを停止する。"""
    global _zeroconf, _service_info
    if _zeroconf is not None and _service_info is not None:
        _zeroconf.unregister_service(_service_info)
        _zeroconf.close()
        logger.info("stopped advertising %s", _service_info.name)
    _zeroconf = None
    _service_info = None
