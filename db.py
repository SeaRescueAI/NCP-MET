"""MySQL persistence helpers for the NCP micro server deployment.

계산 로직은 patient_eval/distance/flight_eval에 두고, 이 모듈은 MySQL 입출력만
담당한다. mysql-connector-python은 DB 기능을 사용할 때만 필요하다.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterator

from weather import latlon_to_grid


RISK_RANK = {
    "HIGH": 0,
    "MEDIUM": 1,
    "SINGLE_RED": 2,
    "LOW": 3,
    "NORMAL": 4,
}

LEVEL_RANK = {
    "GO": 0,
    "CAUTION": 1,
    "NO_GO": 2,
}


def db_config_from_env() -> dict[str, Any]:
    """DB_* 환경변수에서 MySQL 접속 설정을 만든다."""
    return {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("DB_NAME", "maritime_transfer"),
        "charset": "utf8mb4",
        "use_unicode": True,
    }


def connect(**overrides):
    """mysql.connector connection을 반환한다."""
    try:
        import mysql.connector
    except ImportError as exc:
        raise RuntimeError(
            "DB 기능을 쓰려면 mysql-connector-python을 설치하세요: "
            "python3 -m pip install -r requirements.txt"
        ) from exc

    config = db_config_from_env()
    config.update({k: v for k, v in overrides.items() if v is not None})
    return mysql.connector.connect(**config)


@contextmanager
def transaction(conn) -> Iterator[Any]:
    """성공 시 commit, 실패 시 rollback."""
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default)


def _json_default(value: Any):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"{type(value)!r} is not JSON serializable")


def _bool_int(value: Any) -> int:
    return 1 if bool(value) else 0


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


def fetch_active_hospitals(conn) -> list[dict[str, Any]]:
    """hospitals_master와 최신 hospital_snapshot을 읽는다."""
    sql = """
        SELECT
            m.hpid, m.phpid, m.duty_name, m.duty_addr, m.duty_tel, m.duty_tel3,
            m.duty_emcls, m.duty_emcls_name, m.lat, m.lon, m.nx, m.ny,
            m.is_trauma_center, m.region_sido, m.region_sigungu, m.is_active,
            s.snapshot_at, s.hvec, s.hvoc, s.hvicc, s.hv31, s.hv34, s.hvcc,
            s.hv6, s.hv9, s.hv39, s.hv60, s.hv61, s.hvctayn,
            s.hvangioayn, s.hv7, s.hvventiayn, s.mkiosk_ty1,
            s.mkiosk_ty2, s.mkiosk_ty3, s.mkiosk_ty4, s.mkiosk_ty5,
            s.mkiosk_ty6, s.mkiosk_ty11, s.mkiosk_ty19, s.mkiosk_ty22,
            s.mkiosk_ty23
        FROM hospitals_master m
        LEFT JOIN (
            SELECT hs.*
            FROM hospital_snapshot hs
            JOIN (
                SELECT hpid, MAX(snapshot_at) AS snapshot_at
                FROM hospital_snapshot
                GROUP BY hpid
            ) latest
              ON latest.hpid = hs.hpid
             AND latest.snapshot_at = hs.snapshot_at
        ) s
          ON s.hpid = m.hpid
        WHERE m.is_active = 1
          AND m.lat IS NOT NULL
          AND m.lon IS NOT NULL
    """
    cur = conn.cursor(dictionary=True)
    cur.execute(sql)
    rows = cur.fetchall()
    cur.close()
    return rows


def insert_hospital_snapshots(conn, rows: list[dict[str, Any]]) -> int:
    """실시간 가용병상 snapshot을 append-only로 저장한다."""
    if not rows:
        return 0

    sql = """
        INSERT INTO hospital_snapshot (
            hpid, snapshot_at, hvec, hvoc, hvicc, hv31, hv34, hvcc,
            hv6, hv9, hv39, hv60, hv61, hvctayn, hvangioayn, hv7,
            hvventiayn, mkiosk_ty1, mkiosk_ty2, mkiosk_ty3, mkiosk_ty4,
            mkiosk_ty5, mkiosk_ty6, mkiosk_ty11, mkiosk_ty19, mkiosk_ty22,
            mkiosk_ty23, raw_json
        )
        VALUES (
            %(hpid)s, %(snapshot_at)s, %(hvec)s, %(hvoc)s, %(hvicc)s,
            %(hv31)s, %(hv34)s, %(hvcc)s, %(hv6)s, %(hv9)s, %(hv39)s,
            %(hv60)s, %(hv61)s, %(hvctayn)s, %(hvangioayn)s, %(hv7)s,
            %(hvventiayn)s, %(mkiosk_ty1)s, %(mkiosk_ty2)s, %(mkiosk_ty3)s,
            %(mkiosk_ty4)s, %(mkiosk_ty5)s, %(mkiosk_ty6)s, %(mkiosk_ty11)s,
            %(mkiosk_ty19)s, %(mkiosk_ty22)s, %(mkiosk_ty23)s, %(raw_json)s
        )
        ON DUPLICATE KEY UPDATE
            hvec = VALUES(hvec),
            hvoc = VALUES(hvoc),
            hvicc = VALUES(hvicc),
            hv31 = VALUES(hv31),
            hv34 = VALUES(hv34),
            hvcc = VALUES(hvcc),
            hv6 = VALUES(hv6),
            hv9 = VALUES(hv9),
            hv39 = VALUES(hv39),
            hv60 = VALUES(hv60),
            hv61 = VALUES(hv61),
            hvctayn = VALUES(hvctayn),
            hvangioayn = VALUES(hvangioayn),
            hv7 = VALUES(hv7),
            hvventiayn = VALUES(hvventiayn),
            mkiosk_ty1 = VALUES(mkiosk_ty1),
            mkiosk_ty2 = VALUES(mkiosk_ty2),
            mkiosk_ty3 = VALUES(mkiosk_ty3),
            mkiosk_ty4 = VALUES(mkiosk_ty4),
            mkiosk_ty5 = VALUES(mkiosk_ty5),
            mkiosk_ty6 = VALUES(mkiosk_ty6),
            mkiosk_ty11 = VALUES(mkiosk_ty11),
            mkiosk_ty19 = VALUES(mkiosk_ty19),
            mkiosk_ty22 = VALUES(mkiosk_ty22),
            mkiosk_ty23 = VALUES(mkiosk_ty23),
            raw_json = VALUES(raw_json)
    """
    cur = conn.cursor()
    cur.executemany(sql, rows)
    affected = cur.rowcount
    cur.close()
    return affected


def mark_trauma_centers(conn, hpids: set[str]) -> int:
    """공식 외상센터 명단을 hospitals_master에 반영한다."""
    if not hpids:
        return 0

    cur = conn.cursor()
    cur.execute("UPDATE hospitals_master SET is_trauma_center = 0")
    cur.executemany(
        "UPDATE hospitals_master SET is_trauma_center = 1 WHERE hpid = %s",
        [(hpid,) for hpid in sorted(hpids)],
    )
    affected = cur.rowcount
    cur.close()
    return affected


def upsert_hospitals(conn, rows: list[dict[str, Any]]) -> int:
    """CSV에서 읽은 병원 마스터를 upsert한다."""
    sql = """
        INSERT INTO hospitals_master (
            hpid, phpid, duty_name, duty_addr, duty_tel, duty_tel3,
            duty_emcls, duty_emcls_name, lat, lon, nx, ny,
            is_trauma_center, region_sido, region_sigungu,
            is_active, source_collected_at
        )
        VALUES (
            %(hpid)s, %(phpid)s, %(duty_name)s, %(duty_addr)s, %(duty_tel)s,
            %(duty_tel3)s, %(duty_emcls)s, %(duty_emcls_name)s,
            %(lat)s, %(lon)s, %(nx)s, %(ny)s, %(is_trauma_center)s,
            %(region_sido)s, %(region_sigungu)s, 1, %(source_collected_at)s
        )
        ON DUPLICATE KEY UPDATE
            phpid = VALUES(phpid),
            duty_name = VALUES(duty_name),
            duty_addr = VALUES(duty_addr),
            duty_tel = VALUES(duty_tel),
            duty_tel3 = VALUES(duty_tel3),
            duty_emcls = VALUES(duty_emcls),
            duty_emcls_name = VALUES(duty_emcls_name),
            lat = VALUES(lat),
            lon = VALUES(lon),
            nx = VALUES(nx),
            ny = VALUES(ny),
            is_trauma_center = VALUES(is_trauma_center),
            region_sido = VALUES(region_sido),
            region_sigungu = VALUES(region_sigungu),
            is_active = VALUES(is_active),
            source_collected_at = VALUES(source_collected_at)
    """
    cur = conn.cursor()
    cur.executemany(sql, rows)
    affected = cur.rowcount
    cur.close()
    return affected


def normalize_hospital_csv_row(row: dict[str, str]) -> dict[str, Any] | None:
    """hospital_master_merged.csv row를 DB upsert dict로 변환."""
    lat = row.get("latitude") or row.get("lat")
    lon = row.get("longitude") or row.get("lon")
    if not lat or not lon:
        return None

    lat_f = float(lat)
    lon_f = float(lon)
    nx, ny = latlon_to_grid(lat_f, lon_f)
    return {
        "hpid": row.get("hpid") or row.get("phpid") or row["duty_name"],
        "phpid": row.get("phpid"),
        "duty_name": row["duty_name"],
        "duty_addr": row.get("duty_addr"),
        "duty_tel": row.get("duty_tel1"),
        "duty_tel3": row.get("duty_tel3"),
        "duty_emcls": row.get("duty_emcls"),
        "duty_emcls_name": row.get("duty_emcls_name"),
        "lat": lat_f,
        "lon": lon_f,
        "nx": nx,
        "ny": ny,
        "is_trauma_center": 1 if str(row.get("is_trauma", "0")).strip() == "1" else 0,
        "region_sido": row.get("sido"),
        "region_sigungu": row.get("sigungu"),
        "source_collected_at": _parse_datetime(row.get("collected_at")),
    }


def create_case_bundle(conn, case, result: dict[str, Any]) -> int:
    """case/vital/news2/recommendations를 한 트랜잭션에 저장한다."""
    with transaction(conn):
        case_id = insert_case(conn, case)
        insert_vital(conn, case_id, case.vital)
        insert_news2(conn, case_id, result["patient"]["news2"], result["patient"]["required"])
        insert_recommendations(
            conn,
            case_id,
            result["recommendations"]["recommendation_type"],
            result["patient"]["news2"]["risk_level"],
            case.trauma_flag,
            result["recommendations"]["items"],
        )
        mark_case_recommended(conn, case_id)
    return case_id


def insert_case(conn, case) -> int:
    ship_nx, ship_ny = latlon_to_grid(case.ship_lat, case.ship_lon)
    sql = """
        INSERT INTO cases (
            ship_name, ship_lat, ship_lon, ship_nx, ship_ny,
            trauma_flag, patient_status
        )
        VALUES (%s, %s, %s, %s, %s, %s, 'EVALUATING')
    """
    cur = conn.cursor()
    cur.execute(
        sql,
        (
            case.ship_name,
            case.ship_lat,
            case.ship_lon,
            ship_nx,
            ship_ny,
            _bool_int(case.trauma_flag),
        ),
    )
    case_id = cur.lastrowid
    cur.close()
    return case_id


def insert_vital(conn, case_id: int, vital) -> None:
    sql = """
        INSERT INTO vital_inputs (
            case_id, rr, spo2, oxygen, sbp, hr, consciousness, temp, spo2_scale
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    cur = conn.cursor()
    cur.execute(
        sql,
        (
            case_id,
            vital.rr,
            vital.spo2,
            _bool_int(vital.oxygen),
            vital.sbp,
            vital.hr,
            vital.consciousness.strip().upper()[0],
            vital.temp,
            vital.spo2_scale,
        ),
    )
    cur.close()


def insert_news2(conn, case_id: int, news2: dict[str, Any], required: dict[str, Any]) -> None:
    sql = """
        INSERT INTO news2_results (
            case_id, total_score, risk_level, single_red, red_items, item_scores,
            required_hospital_type, required_resources
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """
    cur = conn.cursor()
    cur.execute(
        sql,
        (
            case_id,
            news2["total_score"],
            news2["risk_level"],
            _bool_int(news2["single_red"]),
            _json(news2["red_items"]),
            _json(news2["item_scores"]),
            required["hospital_type"],
            _json(required["resources"]),
        ),
    )
    cur.close()


def insert_recommendations(
    conn,
    case_id: int,
    recommendation_type: str,
    patient_risk_level: str,
    trauma_flag: bool,
    items: list[dict[str, Any]],
) -> int:
    sql = """
        INSERT INTO recommendations (
            case_id, hpid, hospital_snapshot_at, weather_base_datetime,
            distance_m, distance_nm, flight_time_min, distance_level,
            distance_no_go, is_fallback_distance_candidate,
            origin_weather_score, target_weather_score, route_weather_score_max,
            route_weather_score_avg, final_weather_score, weather_no_go,
            flight_level, recommendation_type, recommendation_type_rank,
            patient_risk_rank, trauma_center_rank, flight_level_rank,
            distance_level_rank, tier, can_fly, decision_reason, sort_key_json
        )
        VALUES (
            %(case_id)s, %(hpid)s, NULL, NULL,
            %(distance_m)s, %(distance_nm)s, %(flight_time_min)s,
            %(distance_level)s, %(distance_no_go)s,
            %(is_fallback_distance_candidate)s,
            %(origin_weather_score)s, %(target_weather_score)s,
            %(route_weather_score_max)s, %(route_weather_score_avg)s,
            %(final_weather_score)s, %(weather_no_go)s, %(flight_level)s,
            %(recommendation_type)s, %(recommendation_type_rank)s,
            %(patient_risk_rank)s, %(trauma_center_rank)s,
            %(flight_level_rank)s, %(distance_level_rank)s, %(tier)s,
            %(can_fly)s, %(decision_reason)s, %(sort_key_json)s
        )
    """
    rows = [
        _recommendation_row(case_id, recommendation_type, patient_risk_level, trauma_flag, item)
        for item in items
    ]
    if not rows:
        return 0
    cur = conn.cursor()
    cur.executemany(sql, rows)
    affected = cur.rowcount
    cur.close()
    return affected


def _recommendation_row(
    case_id: int,
    recommendation_type: str,
    patient_risk_level: str,
    trauma_flag: bool,
    item: dict[str, Any],
) -> dict[str, Any]:
    flight_rank = LEVEL_RANK.get(item["final_level"], 9)
    distance_rank = LEVEL_RANK.get(item["distance_level"], 9)
    trauma_rank = 0 if not trauma_flag or item.get("is_trauma_center") else 1
    row = {
        "case_id": case_id,
        "hpid": item["hpid"],
        "distance_m": int(round(item["distance_to_ship_m"])),
        "distance_nm": item["distance_to_ship_nm"],
        "flight_time_min": item["flight_time_min"],
        "distance_level": item["distance_level"],
        "distance_no_go": item["distance_no_go"],
        "is_fallback_distance_candidate": item["is_fallback_distance_candidate"],
        "origin_weather_score": item["origin_weather_score"],
        "target_weather_score": item["target_weather_score"],
        "route_weather_score_max": item["route_weather_score_max"],
        "route_weather_score_avg": item["route_weather_score_avg"],
        "final_weather_score": item["final_weather_score"],
        "weather_no_go": item["weather_no_go"],
        "flight_level": item["final_level"],
        "recommendation_type": recommendation_type,
        "recommendation_type_rank": 0 if recommendation_type == "NORMAL" else 1,
        "patient_risk_rank": RISK_RANK.get(patient_risk_level, 9),
        "trauma_center_rank": trauma_rank,
        "flight_level_rank": flight_rank,
        "distance_level_rank": distance_rank,
        "tier": _tier(item, recommendation_type),
        "can_fly": item["can_fly"],
        "decision_reason": item.get("decision_reason"),
    }
    row["sort_key_json"] = _json(
        {
            "recommendation_type_rank": row["recommendation_type_rank"],
            "patient_risk_rank": row["patient_risk_rank"],
            "trauma_center_rank": row["trauma_center_rank"],
            "flight_level_rank": row["flight_level_rank"],
            "distance_level_rank": row["distance_level_rank"],
            "flight_time_min": row["flight_time_min"],
            "final_weather_score": row["final_weather_score"],
            "distance_m": row["distance_m"],
        }
    )
    return row


def _tier(item: dict[str, Any], recommendation_type: str) -> int:
    if item["can_fly"] and item["final_level"] == "GO":
        return 1
    if item["can_fly"] and item["final_level"] == "CAUTION":
        return 2
    if item["can_fly"]:
        return 3
    if recommendation_type == "FALLBACK" or item.get("is_fallback_distance_candidate"):
        return 4
    if item.get("distance_to_ship_m", 0) <= 450_000:
        return 5
    return 6


def mark_case_recommended(conn, case_id: int) -> None:
    cur = conn.cursor()
    cur.execute(
        "UPDATE cases SET patient_status = 'RECOMMENDED' WHERE case_id = %s",
        (case_id,),
    )
    cur.close()
