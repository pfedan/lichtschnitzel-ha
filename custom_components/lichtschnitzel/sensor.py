"""Diagnostic sensors from the lamp's status (once a second over the WebSocket). Values are
rounded, so Home Assistant only records a change that matters."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfInformation,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import LichtschnitzelConfigEntry
from .entity import LichtschnitzelEntity
from .hub import LichtschnitzelHub


def _round(v, step):
    return None if v is None else int(round(v / step) * step)


def _get(info: dict, *keys):
    for k in keys:
        if not isinstance(info, dict):
            return None
        info = info.get(k)
    return info


@dataclass(frozen=True, kw_only=True)
class LampSensorDescription(SensorEntityDescription):
    value: Callable[[dict], object]
    supply: bool = False  # only on the Lichtschnitzel board, which reports "supply"


SENSORS = (
    LampSensorDescription(key="led_current", translation_key="led_current", device_class=SensorDeviceClass.CURRENT,
                          native_unit_of_measurement=UnitOfElectricCurrent.MILLIAMPERE, state_class=SensorStateClass.MEASUREMENT,
                          value=lambda i: _round(_get(i, "power", "estimatedMa"), 50)),
    LampSensorDescription(key="led_budget", translation_key="led_budget", device_class=SensorDeviceClass.CURRENT,
                          native_unit_of_measurement=UnitOfElectricCurrent.MILLIAMPERE, entity_category=EntityCategory.DIAGNOSTIC,
                          value=lambda i: _get(i, "power", "budgetMa")),
    LampSensorDescription(key="dimming", translation_key="dimming", native_unit_of_measurement=PERCENTAGE,
                          state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC,
                          value=lambda i: None if _get(i, "power", "scale") is None else round(100 * i["power"]["scale"])),
    LampSensorDescription(key="supply_voltage", translation_key="supply_voltage", device_class=SensorDeviceClass.VOLTAGE,
                          native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT, entity_category=EntityCategory.DIAGNOSTIC,
                          supply=True, value=lambda i: _get(i, "supply", "contractMv")),
    LampSensorDescription(key="supply_current", translation_key="supply_current", device_class=SensorDeviceClass.CURRENT,
                          native_unit_of_measurement=UnitOfElectricCurrent.MILLIAMPERE, entity_category=EntityCategory.DIAGNOSTIC,
                          supply=True, value=lambda i: _get(i, "supply", "contractMa")),
    LampSensorDescription(key="input_current", translation_key="input_current", device_class=SensorDeviceClass.CURRENT,
                          native_unit_of_measurement=UnitOfElectricCurrent.MILLIAMPERE, state_class=SensorStateClass.MEASUREMENT,
                          entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False,
                          supply=True, value=lambda i: _round(_get(i, "supply", "inputMa"), 50)),
    LampSensorDescription(key="wifi_signal", translation_key="wifi_signal", device_class=SensorDeviceClass.SIGNAL_STRENGTH,
                          native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT, state_class=SensorStateClass.MEASUREMENT,
                          entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False,
                          value=lambda i: _round(_get(i, "wifi", "rssi"), 3)),
    LampSensorDescription(key="started", translation_key="started", device_class=SensorDeviceClass.TIMESTAMP,
                          entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False,
                          value=lambda i: None if i.get("uptimeS") is None else
                          (dt_util.utcnow() - timedelta(seconds=i["uptimeS"])).replace(second=0, microsecond=0)),
    LampSensorDescription(key="heap_free", translation_key="heap_free", device_class=SensorDeviceClass.DATA_SIZE,
                          native_unit_of_measurement=UnitOfInformation.KILOBYTES, entity_category=EntityCategory.DIAGNOSTIC,
                          entity_registry_enabled_default=False,
                          value=lambda i: None if i.get("heapFree") is None else i["heapFree"] // 1024),
    LampSensorDescription(key="late_frames", translation_key="late_frames", state_class=SensorStateClass.TOTAL_INCREASING,
                          entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False,
                          value=lambda i: i.get("lateFrames")),
)


async def async_setup_entry(hass: HomeAssistant, entry: LichtschnitzelConfigEntry, add: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    has_supply = isinstance(hub.info.get("supply"), dict)
    add([LampSensor(hub, d) for d in SENSORS if has_supply or not d.supply])


class LampSensor(LichtschnitzelEntity, SensorEntity):
    entity_description: LampSensorDescription

    def __init__(self, hub: LichtschnitzelHub, description: LampSensorDescription) -> None:
        super().__init__(hub, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        return self.entity_description.value(self.hub.info)

    def _snapshot(self) -> object:
        v = self.native_value
        # The start time moves by a minute now and then from rounding; only a reboot counts.
        if isinstance(v, datetime) and isinstance(self._last, tuple) and isinstance(self._last[1], datetime):
            if abs((v - self._last[1]).total_seconds()) <= 120:
                v = self._last[1]
        return (self.available, v)
