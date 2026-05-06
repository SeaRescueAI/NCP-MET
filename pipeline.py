"""Case evaluation pipeline shared by CLI and Web UI."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from distance import haversine_m, predict_ship_position_clipped, sample_route_points
from domain import HospitalCandidate, TransferCase
from flight_eval import evaluate_candidate, recommend_candidates
from patient_eval import calculate_news2, decide_required_resources
from weather import fetch_weather_for_points


SAFE_WEATHER = {"PTY": 0, "RN1": 0.0, "WSD": 3.0, "REH": 55.0, "T1H": 15.0}


def evaluate_transfer_case(
    case: TransferCase,
    hospitals: list[HospitalCandidate],
    *,
    live_weather: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """케이스 1건을 평가하고 추천 후보를 반환."""
    news2 = calculate_news2(case.vital)
    required = decide_required_resources(news2, trauma_flag=case.trauma_flag)

    predicted_lat, predicted_lon, was_clipped = predict_ship_position_clipped(
        case.ship_lat,
        case.ship_lon,
        case.velocity,
        case.elapsed_min,
    )
    ship_point = (predicted_lat, predicted_lon)

    evaluated_candidates: list[dict[str, Any]] = []
    for hospital in hospitals:
        distance_m = haversine_m(predicted_lat, predicted_lon, hospital.lat, hospital.lon)
        route_points = sample_route_points(
            predicted_lat,
            predicted_lon,
            hospital.lat,
            hospital.lon,
        )
        origin_weather, target_weather, route_weathers = _fetch_weather_bundle(
            ship_point,
            hospital,
            route_points,
            live_weather,
        )

        flight = evaluate_candidate(
            distance_m=distance_m,
            origin_weather=origin_weather,
            target_weather=target_weather,
            route_weathers=route_weathers,
            heli_speed_knots=case.heli_speed_knots,
            is_trauma_center=int(hospital.is_trauma_center),
        )
        resource = _resource_match(required, hospital)
        evaluated_candidates.append(
            {
                **flight,
                **resource,
                "hpid": hospital.hpid,
                "hospital_name": hospital.name,
                "hospital_lat": hospital.lat,
                "hospital_lon": hospital.lon,
                "available_resources": hospital.available_resources,
                "meta": hospital.meta,
            }
        )

    recommendations = recommend_candidates(
        evaluated_candidates,
        trauma=case.trauma_flag,
        limit=limit,
    )

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "case": {
            "ship_name": case.ship_name,
            "ship_lat": case.ship_lat,
            "ship_lon": case.ship_lon,
            "predicted_ship_lat": predicted_lat,
            "predicted_ship_lon": predicted_lon,
            "ship_position_was_clipped": was_clipped,
            "elapsed_min": case.elapsed_min,
            "velocity": asdict(case.velocity),
            "trauma_flag": case.trauma_flag,
        },
        "patient": {
            "news2": news2,
            "required": required,
        },
        "recommendations": recommendations,
    }


def _resource_match(required: dict[str, Any], hospital: HospitalCandidate) -> dict[str, Any]:
    required_resources = list(required.get("resources", []))
    if not hospital.available_resources:
        missing = []
        if required.get("hospital_type") == "TRAUMA_CENTER" and not hospital.is_trauma_center:
            missing.append("TRAUMA_CENTER")
        return {
            "resource_check_status": "UNKNOWN",
            "resource_matched": None,
            "missing_resources": missing,
        }

    available = set(hospital.available_resources)
    missing = [r for r in required_resources if r not in available]

    if required.get("hospital_type") == "TRAUMA_CENTER" and not hospital.is_trauma_center:
        missing.insert(0, "TRAUMA_CENTER")

    return {
        "resource_check_status": "CHECKED",
        "resource_matched": len(missing) == 0,
        "missing_resources": missing,
    }


def _fetch_weather_bundle(
    ship_point: tuple[float, float],
    hospital: HospitalCandidate,
    route_points: list[tuple[float, float]],
    live_weather: bool,
) -> tuple[Any, Any, list[Any]]:
    if not live_weather:
        return SAFE_WEATHER, SAFE_WEATHER, [SAFE_WEATHER for _ in route_points]

    points = [ship_point, (hospital.lat, hospital.lon), *route_points]
    weathers = fetch_weather_for_points(points)
    return weathers[0], weathers[1], weathers[2:]
