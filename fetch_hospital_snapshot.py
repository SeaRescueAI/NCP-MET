"""응급의료 API 자원 정보를 hospital_snapshot에 저장한다.

환자-평가-v2.ipynb의 기존 로직을 배포용 스크립트로 옮긴 버전.

수집 API:
1. getEmrrmRltmUsefulSckbdInfoInqire: 실시간 가용병상/장비
2. getSrsillDissAceptncPosblInfoInqire: 중증질환 수용 가능 여부
3. getStrmListInfoInqire: 외상센터 명단

환경변수:
    EMERGENCY_SERVICE_KEY 또는 HOSPITAL_SERVICE_KEY
"""

from __future__ import annotations

import argparse
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

import requests

from db import (
    connect,
    fetch_active_hospitals,
    insert_hospital_snapshots,
    mark_trauma_centers,
    transaction,
)


BASE_URL = "http://apis.data.go.kr/B552657/ErmctInfoInqireService"
REALTIME_OPERATION = "getEmrrmRltmUsefulSckbdInfoInqire"
SEVERE_OPERATION = "getSrsillDissAceptncPosblInfoInqire"
TRAUMA_OPERATION = "getStrmListInfoInqire"

REALTIME_FIELDS = (
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
)
SEVERE_FIELDS = (
    "mkiosk_ty1",
    "mkiosk_ty2",
    "mkiosk_ty3",
    "mkiosk_ty4",
    "mkiosk_ty5",
    "mkiosk_ty6",
    "mkiosk_ty11",
    "mkiosk_ty19",
    "mkiosk_ty22",
    "mkiosk_ty23",
)
SNAPSHOT_FIELDS = REALTIME_FIELDS + SEVERE_FIELDS
INT_FIELDS = {
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
}


def _service_key() -> str:
    key = os.getenv("EMERGENCY_SERVICE_KEY") or os.getenv("HOSPITAL_SERVICE_KEY")
    if not key:
        raise RuntimeError("EMERGENCY_SERVICE_KEY 또는 HOSPITAL_SERVICE_KEY가 필요합니다.")
    return key


def _snake(name: str) -> str:
    """XML 태그를 DB 컬럼명에 맞춘다. MKioskTy1 -> mkiosk_ty1."""
    name = name.strip()
    if name.lower().startswith("mkioskty"):
        return "mkiosk_ty" + name[len("MKioskTy") :]
    name = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    return name.replace("__", "_")


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _get(row: dict[str, Any], key: str) -> Any:
    return row.get(key) or row.get(key.lower()) or row.get(_snake(key))


def fetch_xml_items_all(
    operation: str,
    *,
    params: dict[str, Any] | None = None,
    num_of_rows: int = 100,
    timeout: float = 20.0,
) -> list[dict[str, Any]]:
    """공공데이터 XML API를 전체 페이지 조회한다."""
    params = params or {}
    page_no = 1
    all_items: list[dict[str, Any]] = []

    while True:
        request_params = {
            "serviceKey": _service_key(),
            "pageNo": str(page_no),
            "numOfRows": str(num_of_rows),
            **params,
        }
        response = requests.get(
            f"{BASE_URL}/{operation}",
            params=request_params,
            timeout=timeout,
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)

        result_code = root.findtext("./header/resultCode")
        result_msg = root.findtext("./header/resultMsg")
        if result_code and result_code != "00":
            raise RuntimeError(f"{operation} API 오류: {result_code} / {result_msg}")

        items = [_xml_item_to_dict(item) for item in root.findall("./body/items/item")]
        total_count = int(root.findtext("./body/totalCount", "0") or "0")
        all_items.extend(items)

        print(
            f"{operation} page={page_no} fetched={len(items)} "
            f"accumulated={len(all_items)} total={total_count}"
        )

        if len(all_items) >= total_count or not items:
            break
        page_no += 1

    return all_items


def _xml_item_to_dict(item: ET.Element) -> dict[str, Any]:
    row = {}
    for child in list(item):
        row[_snake(child.tag)] = child.text
    return row


def fetch_realtime_status_all() -> list[dict[str, Any]]:
    rows = []
    for item in fetch_xml_items_all(REALTIME_OPERATION):
        row = {
            "hpid": _get(item, "hpid"),
            "duty_name": _get(item, "dutyName"),
        }
        for field in REALTIME_FIELDS:
            row[field] = _get(item, field)
        rows.append(row)
    return rows


def fetch_severe_illness_acceptance_all() -> list[dict[str, Any]]:
    rows = []
    for item in fetch_xml_items_all(SEVERE_OPERATION):
        row = {
            "hpid": _get(item, "hpid"),
            "duty_name": _get(item, "dutyName"),
        }
        for field in SEVERE_FIELDS:
            row[field] = _get(item, field)
        rows.append(row)
    return rows


def fetch_trauma_centers_list() -> list[dict[str, Any]]:
    rows = []
    for item in fetch_xml_items_all(TRAUMA_OPERATION):
        rows.append(
            {
                "hpid": _get(item, "hpid"),
                "duty_name": _get(item, "dutyName"),
                "is_trauma_center": True,
            }
        )
    return rows


def merge_hospital_rows(
    realtime_rows: list[dict[str, Any]],
    severe_rows: list[dict[str, Any]],
    trauma_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for group in (realtime_rows, severe_rows, trauma_rows or []):
        for row in group:
            hpid = row.get("hpid")
            if not hpid:
                continue
            merged.setdefault(hpid, {"hpid": hpid, "is_trauma_center": False})
            merged[hpid].update(row)
    return list(merged.values())


def snapshot_rows(
    merged_rows: list[dict[str, Any]],
    valid_hpids: set[str],
) -> list[dict[str, Any]]:
    snapshot_at = datetime.now()
    rows = []
    for item in merged_rows:
        hpid = item.get("hpid")
        if not hpid or hpid not in valid_hpids:
            continue

        row = {
            "hpid": hpid,
            "snapshot_at": snapshot_at,
            "raw_json": json.dumps(item, ensure_ascii=False),
        }
        for field in SNAPSHOT_FIELDS:
            value = item.get(field)
            row[field] = _to_int(value) if field in INT_FIELDS else value
        rows.append(row)
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="응급의료 API snapshot 적재")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--num-of-rows", type=int, default=100)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    conn = connect()
    try:
        valid_hpids = {row["hpid"] for row in fetch_active_hospitals(conn)}
        realtime_rows = fetch_realtime_status_all()
        severe_rows = fetch_severe_illness_acceptance_all()
        trauma_rows = fetch_trauma_centers_list()
        merged_rows = merge_hospital_rows(realtime_rows, severe_rows, trauma_rows)
        rows = snapshot_rows(merged_rows, valid_hpids)
        trauma_hpids = {
            row["hpid"]
            for row in trauma_rows
            if row.get("hpid") in valid_hpids
        }

        print(f"API1 realtime={len(realtime_rows)}")
        print(f"API2 severe={len(severe_rows)}")
        print(f"API3 trauma={len(trauma_rows)}")
        print(f"merged={len(merged_rows)} matched_snapshot={len(rows)}")
        print(f"matched_trauma={len(trauma_hpids)}")

        if args.dry_run:
            if rows:
                sample = rows[0]
                print(
                    f"sample={sample['hpid']} OR={sample.get('hvoc')} "
                    f"ICU(hv31)={sample.get('hv31')} "
                    f"Vent={sample.get('hvventiayn')} "
                    f"Angio={sample.get('hvangioayn')} "
                    f"MI={sample.get('mkiosk_ty1')}"
                )
            return 0

        with transaction(conn):
            affected = insert_hospital_snapshots(conn, rows)
            trauma_affected = mark_trauma_centers(conn, trauma_hpids)
        print(f"snapshot_rows={len(rows)} affected={affected}")
        print(f"trauma_centers={len(trauma_hpids)} affected={trauma_affected}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
