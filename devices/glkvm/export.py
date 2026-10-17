# ========================================================================== #
#                                                                            #
#    KVMD - The main PiKVM daemon.                                           #
#                                                                            #
#    Copyright (C) 2018-2024  Maxim Devaev <mdevaev@gmail.com>               #
#                                                                            #
#    This program is free software: you can redistribute it and/or modify    #
#    it under the terms of the GNU General Public License as published by    #
#    the Free Software Foundation, either version 3 of the License, or       #
#    (at your option) any later version.                                     #
#                                                                            #
#    This program is distributed in the hope that it will be useful,         #
#    but WITHOUT ANY WARRANTY; without even the implied warranty of          #
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the           #
#    GNU General Public License for more details.                            #
#                                                                            #
#    You should have received a copy of the GNU General Public License       #
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.  #
#                                                                            #
# ========================================================================== #


# Installed as /usr/lib/python3.12/site-packages/kvmd/apps/kvmd/api/export.py,
# where it takes precedence over the vendor's sourceless export.pyc. That
# module asks InfoManager for "health" and "fan", and firmware V1.10.1 release2
# registers no "fan", so every request fails with HTTP 500. This copy asks only
# for registered submanagers; the rest reproduces the vendor bytecode. Any
# other vendor export.pyc runs unchanged. See devices/glkvm/README.md#monitoring.

import asyncio
import hashlib
import os

from typing import Any

import async_lru

from aiohttp.web import Request
from aiohttp.web import Response

from .... import tools

from ....htserver import exposed_http

from ....plugins.atx import BaseAtx
from ....plugins.ugpio import UserGpioModes

from ..info import InfoManager
from ..ugpio import UserGpio


_VENDOR_PYC = os.path.join(os.path.dirname(__file__), "export.pyc")
_VENDOR_PYC_SHA256 = "8018d3f7bef4ce7c293fa09387328970006604a1d124a69313d35309545b0fe2"


def _is_patched_vendor() -> bool:
    try:
        with open(_VENDOR_PYC, "rb") as file:
            return (hashlib.sha256(file.read()).hexdigest() == _VENDOR_PYC_SHA256)
    except OSError:
        return False


if not _is_patched_vendor():
    from importlib.machinery import SourcelessFileLoader
    exec(SourcelessFileLoader(__name__, _VENDOR_PYC).get_code(__name__), globals())  # pylint: disable=exec-used

else:
    # =====
    class ExportApi:
        def __init__(self, info_manager: InfoManager, atx: BaseAtx, user_gpio: UserGpio) -> None:
            self.__info_manager = info_manager
            self.__atx = atx
            self.__user_gpio = user_gpio

        # =====

        @exposed_http("GET", "/export/prometheus/metrics")
        async def __prometheus_metrics_handler(self, _: Request) -> Response:
            return Response(text=(await self.__get_prometheus_metrics()))

        @async_lru.alru_cache(maxsize=1, ttl=5)
        async def __get_prometheus_metrics(self) -> str:
            fields = sorted({"health", "fan"} & self.__info_manager.get_subs())
            (atx_state, info_state, gpio_state) = await asyncio.gather(*[
                self.__atx.get_state(),
                self.__info_manager.get_state(fields),
                self.__user_gpio.get_state(),
            ])
            rows: list[str] = []

            self.__append_prometheus_rows(rows, atx_state["enabled"], "pikvm_atx_enabled")  # type: ignore
            self.__append_prometheus_rows(rows, atx_state["leds"]["power"], "pikvm_atx_power")  # type: ignore

            for mode in sorted(UserGpioModes.ALL):
                for (channel, ch_state) in gpio_state["state"][f"{mode}s"].items():  # type: ignore
                    if not channel.startswith("__"):  # Hide special GPIOs
                        for key in ["online", "state"]:
                            self.__append_prometheus_rows(rows, ch_state["state"], f"pikvm_gpio_{mode}_{key}_{channel}")

            for (field, path) in [("health", "pikvm_hw"), ("fan", "pikvm_fan")]:
                if field in info_state:
                    self.__append_prometheus_rows(rows, info_state[field], path)  # type: ignore
            return "\n".join(rows)

        def __append_prometheus_rows(self, rows: list[str], value: Any, path: str) -> None:
            if isinstance(value, bool):
                value = int(value)
            if isinstance(value, (int, float)):
                rows.extend([
                    f"# TYPE {path} gauge",
                    f"{path} {value}",
                    "",
                ])
            elif isinstance(value, dict):
                for (sub_key, sub_value) in tools.sorted_kvs(value):
                    sub_path = (f"{path}_{sub_key}" if sub_key != "parsed_flags" else path)
                    self.__append_prometheus_rows(rows, sub_value, sub_path)
