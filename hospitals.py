"""Hospital candidate loading and conversion helpers."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from domain import HospitalCandidate


DEFAULT_HOSPITAL_CSV = Path(__file__).with_name("hospital_master_merged.csv")
SNAPSHOT_FIELDS = (
    "snapshot_at",
    "hvec",
    "hvoc",
    "hvicc",
    "hv31",
    "hv34",
    "hvcc",
    "hv6",
    "hv9",
    "hv39",
    "hv60",
    "hv61",
    "hvctayn",
    "hvangioayn",
    "hv7",
    "hvventiayn",
    "mkiosk_ty1",
)


def load_csv(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def hospital_from_dict(data: dict[str, Any]) -> HospitalCandidate:
    return HospitalCandidate(
        hpid=str(data.get("hpid") or data.get("id") or data["name"]),
        name=str(data["name"]),
        lat=float(data["lat"]),
        lon=float(data["lon"]),
        is_trauma_center=bool(data.get("is_trauma_center", False)),
        available_resources=list(data.get("available_resources", [])),
        meta=dict(data.get("meta", {})),
    )


def hospital_from_db_row(row: dict[str, Any]) -> HospitalCandidate:
    snapshot = {field: row.get(field) for field in SNAPSHOT_FIELDS}
    return HospitalCandidate(
        hpid=str(row["hpid"]),
        name=str(row["duty_name"]),
        lat=float(row["lat"]),
        lon=float(row["lon"]),
        is_trauma_center=bool(row.get("is_trauma_center", False)),
        available_resources=available_resources_from_snapshot(row),
        meta={
            "phpid": row.get("phpid"),
            "duty_addr": row.get("duty_addr"),
            "duty_tel1": row.get("duty_tel"),
            "duty_tel3": row.get("duty_tel3"),
            "duty_emcls": row.get("duty_emcls"),
            "duty_emcls_name": row.get("duty_emcls_name"),
            "sido": row.get("region_sido"),
            "sigungu": row.get("region_sigungu"),
            "nx": row.get("nx"),
            "ny": row.get("ny"),
            "snapshot": snapshot,
        },
    )


def available_resources_from_snapshot(row: dict[str, Any]) -> list[str]:
    """hospital_snapshot 필드에서 환자 평가 자원명을 만든다."""
    if not row.get("snapshot_at"):
        return []

    resources: list[str] = []
    if _positive_any(row, ("hvicc", "hv31", "hv34", "hvcc", "hv6", "hv9")):
        resources.append("ICU")
    if _positive_any(row, ("hvoc", "hv39")):
        resources.append("OPERATING_ROOM")
    if _yes(row.get("hvctayn")):
        resources.append("CT")
    if _yes(row.get("hvangioayn")) or _yes(row.get("hv7")) or _positive(row.get("hv7")):
        resources.append("ANGIOGRAPHY")
    if _yes(row.get("hvventiayn")):
        resources.append("VENTILATOR")
    if _yes(row.get("mkiosk_ty1")):
        resources.append("MI_ACCEPTABLE")
    return resources


def _positive_any(row: dict[str, Any], fields: tuple[str, ...]) -> bool:
    return any(_positive(row.get(field)) for field in fields)


def _positive(value: Any) -> bool:
    if value is None:
        return False
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def _yes(value: Any) -> bool:
    if value is None:
        return False
    return str(value).strip().upper() in {"Y", "1", "YES", "TRUE", "가능"}


def hospital_from_csv_row(row: dict[str, str]) -> HospitalCandidate | None:
    lat = row.get("latitude") or row.get("lat")
    lon = row.get("longitude") or row.get("lon")
    if not lat or not lon:
        return None

    return HospitalCandidate(
        hpid=str(row.get("hpid") or row.get("phpid") or row["duty_name"]),
        name=str(row["duty_name"]),
        lat=float(lat),
        lon=float(lon),
        is_trauma_center=str(row.get("is_trauma", "0")).strip() == "1",
        available_resources=[],
        meta={
            "phpid": row.get("phpid"),
            "duty_addr": row.get("duty_addr"),
            "duty_tel1": row.get("duty_tel1"),
            "duty_tel3": row.get("duty_tel3"),
            "duty_emcls": row.get("duty_emcls"),
            "duty_emcls_name": row.get("duty_emcls_name"),
            "sido": row.get("sido"),
            "sigungu": row.get("sigungu"),
            "is_emergency": row.get("is_emergency"),
            "collected_at": row.get("collected_at"),
        },
    )


def hospitals_from_csv(path: str | Path = DEFAULT_HOSPITAL_CSV) -> list[HospitalCandidate]:
    hospitals: list[HospitalCandidate] = []
    for row in load_csv(path):
        hospital = hospital_from_csv_row(row)
        if hospital is not None:
            hospitals.append(hospital)
    return hospitals


def demo_hospitals() -> list[HospitalCandidate]:
    return [
        HospitalCandidate(
            hpid="DEMO-PNUH",
            name="부산대병원",
            lat=35.1000,
            lon=129.0180,
            is_trauma_center=True,
            available_resources=[
                "ICU",
                "VENTILATOR",
                "OPERATING_ROOM",
                "ANGIOGRAPHY",
                "MI_ACCEPTABLE",
                "CT",
            ],
        ),
        HospitalCandidate(
            hpid="DEMO-UH",
            name="울산대병원",
            lat=35.5200,
            lon=129.4280,
            is_trauma_center=False,
            available_resources=["ICU", "VENTILATOR", "CT"],
        ),
        HospitalCandidate(
            hpid="DEMO-CJUH",
            name="제주대병원",
            lat=33.4670,
            lon=126.5450,
            is_trauma_center=False,
            available_resources=["ICU", "OPERATING_ROOM", "CT"],
        ),
    ]
