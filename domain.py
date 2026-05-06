"""Shared domain types and input converters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from distance import ShipVelocity
from patient_eval import VitalInput


DEFAULT_HELI_SPEED_KNOTS = 140.0


@dataclass(frozen=True)
class TransferCase:
    """후송 케이스 입력."""

    ship_lat: float
    ship_lon: float
    vital: VitalInput
    trauma_flag: bool = False
    ship_name: str | None = None
    velocity: ShipVelocity = field(
        default_factory=lambda: ShipVelocity(heading_deg=0.0, speed_knots=0.0)
    )
    elapsed_min: float = 0.0
    heli_speed_knots: float = DEFAULT_HELI_SPEED_KNOTS


@dataclass(frozen=True)
class HospitalCandidate:
    """후보 병원 입력."""

    hpid: str
    name: str
    lat: float
    lon: float
    is_trauma_center: bool = False
    available_resources: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


def vital_from_dict(data: dict[str, Any]) -> VitalInput:
    return VitalInput(
        rr=int(data["rr"]),
        spo2=int(data["spo2"]),
        oxygen=bool(data["oxygen"]),
        sbp=int(data["sbp"]),
        hr=int(data["hr"]),
        consciousness=str(data["consciousness"]),
        temp=float(data["temp"]),
        spo2_scale=int(data.get("spo2_scale", 1)),
    )


def case_from_dict(data: dict[str, Any]) -> TransferCase:
    """JSON/dict 입력을 TransferCase로 변환."""
    velocity_data = data.get("velocity", {})
    if "east_knots" in velocity_data or "north_knots" in velocity_data:
        velocity = ShipVelocity.from_components(
            east_knots=float(velocity_data.get("east_knots", 0.0)),
            north_knots=float(velocity_data.get("north_knots", 0.0)),
        )
    else:
        velocity = ShipVelocity(
            heading_deg=float(velocity_data.get("heading_deg", 0.0)),
            speed_knots=float(velocity_data.get("speed_knots", 0.0)),
        )

    return TransferCase(
        ship_name=data.get("ship_name"),
        ship_lat=float(data["ship_lat"]),
        ship_lon=float(data["ship_lon"]),
        velocity=velocity,
        elapsed_min=float(data.get("elapsed_min", 0.0)),
        heli_speed_knots=float(data.get("heli_speed_knots", DEFAULT_HELI_SPEED_KNOTS)),
        trauma_flag=bool(data.get("trauma_flag", False)),
        vital=vital_from_dict(data["vital"]),
    )


def demo_case() -> TransferCase:
    return TransferCase(
        ship_name="Demo Vessel",
        ship_lat=34.8,
        ship_lon=129.0,
        velocity=ShipVelocity(heading_deg=45.0, speed_knots=12.0),
        elapsed_min=30.0,
        trauma_flag=False,
        vital=VitalInput(
            rr=24,
            spo2=92,
            oxygen=True,
            sbp=82,
            hr=128,
            consciousness="A",
            temp=36.4,
        ),
    )
