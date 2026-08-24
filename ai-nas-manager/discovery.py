"""mDNS/BonjourでTuner(仮想Tuner、将来は実機Tunerも含む)をネットワーク上から発見する。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

logger = logging.getLogger(__name__)

# mDNSサービスタイプ名。実機Tunerの命名規約は未確定のため暫定値(第11章 未確定事項)。
SERVICE_TYPE = "_tuner._tcp.local."


@dataclass(frozen=True)
class TunerInfo:
    name: str
    host: str
    port: int
    service_name: str


def discover_tuners(timeout_sec: float = 3.0) -> list[TunerInfo]:
    """SERVICE_TYPEを名乗るTuner(仮想Tunerを含む)をmDNSで探し、見つかった一覧を返す。"""
    found: dict[str, TunerInfo] = {}

    class _Listener(ServiceListener):
        def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            info = zc.get_service_info(type_, name, timeout=1000)
            if info is None or not info.addresses:
                logger.warning("tuner discovered but service info unavailable: %s", name)
                return
            host = info.parsed_addresses()[0]
            display_name = name.removesuffix(f".{SERVICE_TYPE}")
            found[name] = TunerInfo(
                name=display_name,
                host=host,
                port=info.port or 0,
                service_name=name,
            )
            logger.info("tuner found: %s at %s:%s", display_name, host, info.port)

        def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            self.add_service(zc, type_, name)

        def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            found.pop(name, None)
            logger.info("tuner removed: %s", name)

    zeroconf = Zeroconf()
    try:
        ServiceBrowser(zeroconf, SERVICE_TYPE, _Listener())
        time.sleep(timeout_sec)
    finally:
        zeroconf.close()

    return list(found.values())
