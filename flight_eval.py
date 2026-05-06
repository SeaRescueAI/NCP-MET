"""헬기 후송 비행 가능성 판정 (거리 + 기상).

★ 이 모듈은 *비행 평가*만 담당. 환자 상태(NEWS2)는 patient_eval.py에 있음. ★
입력: 출발지·도착지·경로의 기상 + 거리(m)
출력: GO / CAUTION / NO_GO + 정렬용 점수/사유

distance.py / weather.py에 의존하지 않는다 (입력은 표준 dict).
weather.Weather / weather.Forecast 객체는 normalize_weather()로 변환해서 입력.

핵심 함수:
- evaluate_point_weather(weather_dict): 한 지점 기상 점수화
- evaluate_candidate(distance_m, origin_w, target_w, route_ws, ...): 후보 1개 종합 평가
- recommend_candidates(candidates, trauma): 후보 리스트 정렬/필터
- normalize_weather(Weather|Forecast|dict): 어떤 형태로 들어와도 표준 dict로

표준 기상 dict 키:
    PTY (int)  강수형태 0없음/1비/2비눈/3눈/5빗방울/6빗방울눈/7눈날림
    RN1 (float) 1시간강수량 mm  (예보의 PCP도 여기로 매핑)
    WSD (float) 풍속 m/s
    REH (float) 습도 %
    T1H (float) 기온 ℃          (예보의 TMP도 여기로 매핑)
"""

from __future__ import annotations

from statistics import mean
from typing import Any


# ============================================================
# 임계값
# ============================================================

DISTANCE_CAUTION_M = 240_000      # 이 이하면 거리 GO
DISTANCE_MAX_M = 300_000          # 이 이하면 CAUTION 허용, 초과시 NO_GO
DISTANCE_FALLBACK_M = 450_000     # NO_GO 중에서도 fallback 후보로 살아남는 한계

# 기상 hard NO_GO 임계
HARD_WSD = 14.0      # 풍속 m/s 이상
HARD_RN1 = 30.0      # 시간당 강수량 mm 이상
HARD_PTY = {2, 3, 6, 7}   # 비눈/눈/빗방울눈/눈날림

# 기상 soft (CAUTION 누적 점수)
SOFT_THRESHOLDS = {
    "wsd_caution": (8.0, 14.0, 30),    # (min, max_excl, score)
    "rn1_caution": (5.0, 30.0, 25),
    "pty_rain":    ({1, 5}, 20),
    "reh_high":    (90.0, 15),
    "cold_rain":   (3.0, 15),          # T1H <= 3 and PTY != 0
}


# ============================================================
# 입력 정규화
# ============================================================

def _to_float(value, default=0.0):
    if value is None:
        return default
    s = str(value).strip()
    if s in ("", "-", "None", "null"):
        return default
    if s == "강수없음":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return default


def _to_int(value, default=0):
    try:
        return int(round(_to_float(value, default)))
    except (TypeError, ValueError):
        return default


def normalize_weather(weather: Any) -> dict:
    """다양한 입력 → 표준 기상 dict로 변환.

    지원 입력:
    - dict: {"PTY":..., "RN1":..., "WSD":..., "REH":..., "T1H":...}  (기존 형식)
    - weather.Weather: NCST 결과 (t1h, rn1 등 소문자 속성)
    - weather.Forecast: VilageFcst 결과 (tmp/pcp는 t1h/rn1로 매핑)
    """
    if weather is None:
        return {"PTY": 0, "RN1": 0.0, "WSD": 0.0, "REH": 0.0, "T1H": 0.0}

    # dataclass 객체 (Weather / Forecast) 처리
    if hasattr(weather, "raw"):
        # Weather (NCST): t1h, rn1, wsd, reh, pty
        # Forecast (VilageFcst): tmp, pcp, wsd, reh, pty
        t1h = getattr(weather, "t1h", None)
        if t1h is None:
            t1h = getattr(weather, "tmp", None)
        rn1 = getattr(weather, "rn1", None)
        if rn1 is None:
            rn1 = getattr(weather, "pcp", None)
        return {
            "PTY": _to_int(getattr(weather, "pty", 0)),
            "RN1": _to_float(rn1),
            "WSD": _to_float(getattr(weather, "wsd", None)),
            "REH": _to_float(getattr(weather, "reh", None)),
            "T1H": _to_float(t1h),
        }

    # plain dict
    return {
        "PTY": _to_int(weather.get("PTY"), 0),
        "RN1": _to_float(weather.get("RN1"), 0.0),
        "WSD": _to_float(weather.get("WSD"), 0.0),
        "REH": _to_float(weather.get("REH"), 0.0),
        "T1H": _to_float(weather.get("T1H"), 0.0),
    }


def _unique(items):
    return list(dict.fromkeys(items))


# ============================================================
# 한 지점 기상 평가
# ============================================================

def evaluate_point_weather(weather: Any) -> dict:
    """한 지점 기상 → 점수/사유.

    반환:
        normalized_weather: 표준 dict
        weather_no_go: 0/1
        weather_level: GO / CAUTION / NO_GO
        final_weather_score: 0~100 (높을수록 위험)
        reasons: 사유 문자열 리스트
    """
    w = normalize_weather(weather)

    hard_reasons = []
    if w["WSD"] >= HARD_WSD:
        hard_reasons.append(f"풍속 {HARD_WSD}m/s 이상")
    if w["RN1"] >= HARD_RN1:
        hard_reasons.append(f"시간당 강수량 {HARD_RN1}mm 이상")
    if w["PTY"] in HARD_PTY:
        hard_reasons.append("위험 강수형태")
    if w["T1H"] <= 0 and w["PTY"] != 0:
        hard_reasons.append("영하권 강수")

    soft_score = 0
    soft_reasons = []

    wsd_min, wsd_max, wsd_pts = SOFT_THRESHOLDS["wsd_caution"]
    if wsd_min <= w["WSD"] < wsd_max:
        soft_score += wsd_pts
        soft_reasons.append("풍속 주의")

    rn1_min, rn1_max, rn1_pts = SOFT_THRESHOLDS["rn1_caution"]
    if rn1_min <= w["RN1"] < rn1_max:
        soft_score += rn1_pts
        soft_reasons.append("강수량 주의")

    pty_set, pty_pts = SOFT_THRESHOLDS["pty_rain"]
    if w["PTY"] in pty_set:
        soft_score += pty_pts
        soft_reasons.append("비 또는 빗방울")

    reh_thr, reh_pts = SOFT_THRESHOLDS["reh_high"]
    if w["REH"] >= reh_thr:
        soft_score += reh_pts
        soft_reasons.append("고습도")

    cold_thr, cold_pts = SOFT_THRESHOLDS["cold_rain"]
    if w["T1H"] <= cold_thr and w["PTY"] != 0:
        soft_score += cold_pts
        soft_reasons.append("저온 강수")

    weather_no_go = int(bool(hard_reasons))
    final_score = 100 if weather_no_go else soft_score

    if weather_no_go:
        level = "NO_GO"
    elif final_score >= 40:
        level = "CAUTION"
    else:
        level = "GO"

    return {
        "normalized_weather": w,
        "weather_no_go": weather_no_go,
        "weather_level": level,
        "final_weather_score": final_score,
        "reasons": _unique(hard_reasons + soft_reasons),
    }


# ============================================================
# 후보 1개 종합 평가
# ============================================================

def evaluate_candidate(
    distance_m: float,
    origin_weather: Any,
    target_weather: Any,
    route_weathers: list | None = None,
    heli_speed_knots: float = 140.0,
    is_trauma_center: int = 0,
) -> dict:
    """병원 후보 1개의 거리·기상 종합 평가.

    매개변수:
        distance_m: 헬기 출발지 → 병원 거리 (보통 선박 예측좌표 기준)
        origin_weather: 출발지(선박) 기상
        target_weather: 도착지(병원) 기상
        route_weathers: 경로 중간 샘플 기상 리스트 (None이면 빈 리스트)
        heli_speed_knots: 헬기 평균 순항속도
        is_trauma_center: 병원의 외상센터 여부 (0/1) — 정렬용 메타

    반환: 정렬·필터·표시에 필요한 모든 키 포함된 dict.
    """
    route_weathers = route_weathers or []

    distance_m = float(distance_m)
    distance_nm = distance_m / 1852.0
    flight_time_min = round(60.0 * distance_nm / float(heli_speed_knots), 2)

    # 거리 등급
    if distance_m <= DISTANCE_CAUTION_M:
        distance_level = "GO"
    elif distance_m <= DISTANCE_MAX_M:
        distance_level = "CAUTION"
    else:
        distance_level = "NO_GO"

    distance_no_go = int(distance_m > DISTANCE_MAX_M)
    is_fallback_distance_candidate = int(
        DISTANCE_MAX_M < distance_m <= DISTANCE_FALLBACK_M
    )
    distance_risk_score = round(
        min(100.0, 100.0 * distance_m / DISTANCE_MAX_M), 2
    )

    # 기상 평가
    origin_eval = evaluate_point_weather(origin_weather)
    target_eval = evaluate_point_weather(target_weather)
    route_evals = [evaluate_point_weather(w) for w in route_weathers]

    route_scores = [r["final_weather_score"] for r in route_evals]
    route_reasons = _unique([rsn for r in route_evals for rsn in r["reasons"]])

    route_score_max = max(route_scores, default=0)
    route_score_avg = round(mean(route_scores), 2) if route_scores else 0.0

    final_weather_score = max(
        [
            origin_eval["final_weather_score"],
            target_eval["final_weather_score"],
            *route_scores,
        ]
    )
    weather_no_go = int(
        any(
            [
                origin_eval["weather_no_go"],
                target_eval["weather_no_go"],
                *[r["weather_no_go"] for r in route_evals],
            ]
        )
    )

    can_fly = 1 - max(distance_no_go, weather_no_go)

    # 종합 등급
    if can_fly == 0:
        final_level = "NO_GO"
    elif distance_level == "CAUTION" or final_weather_score >= 40:
        final_level = "CAUTION"
    else:
        final_level = "GO"

    # 사유
    decision_reasons = []
    if distance_no_go:
        decision_reasons.append(f"거리 기준 {DISTANCE_MAX_M//1000}km 초과")
    elif distance_level == "CAUTION":
        decision_reasons.append("거리 주의 구간")

    if weather_no_go or final_weather_score >= 40:
        decision_reasons.extend(origin_eval["reasons"])
        decision_reasons.extend(target_eval["reasons"])
        decision_reasons.extend(route_reasons)

    if can_fly == 1 and not decision_reasons:
        decision_reasons.append("거리와 기상 기준 모두 충족")

    if can_fly == 0 and is_fallback_distance_candidate:
        decision_reasons.append("fallback 참고 후보")

    return {
        "distance_to_ship_m": round(distance_m, 2),
        "distance_to_ship_nm": round(distance_nm, 2),
        "flight_time_min": flight_time_min,
        "distance_level": distance_level,
        "distance_no_go": distance_no_go,
        "distance_risk_score": distance_risk_score,
        "is_fallback_distance_candidate": is_fallback_distance_candidate,
        "origin_weather_score": origin_eval["final_weather_score"],
        "target_weather_score": target_eval["final_weather_score"],
        "route_weather_score_max": route_score_max,
        "route_weather_score_avg": route_score_avg,
        "final_weather_score": final_weather_score,
        "weather_no_go": weather_no_go,
        "can_fly": can_fly,
        "final_level": final_level,
        "is_trauma_center": int(is_trauma_center),
        "decision_reason": "; ".join(_unique(decision_reasons)),
    }


# ============================================================
# 후보 리스트 정렬/필터
# ============================================================

def recommend_candidates(
    candidates: list[dict],
    trauma: bool = False,
    limit: int | None = None,
) -> dict:
    """후보 리스트를 정렬해 NORMAL 또는 FALLBACK 추천 반환.

    NORMAL: can_fly=1 후보들 중 정렬
    FALLBACK: NORMAL이 비었을 때 fallback 거리 한계(450km) 안의 후보로 대체

    정렬 키:
        - trauma=True면 외상센터 우선
        - 그 다음 final_weather_score (낮을수록 좋음)
        - 그 다음 flight_time_min
        - 그 다음 distance_to_ship_m
    """
    if trauma:
        rank_key = lambda c: (
            -int(c.get("is_trauma_center", 0)),
            c["final_weather_score"],
            c["flight_time_min"],
            c["distance_to_ship_m"],
        )
    else:
        rank_key = lambda c: (
            c["final_weather_score"],
            c["flight_time_min"],
            c["distance_to_ship_m"],
        )

    normal = sorted([c for c in candidates if c["can_fly"] == 1], key=rank_key)

    if normal:
        ranked = normal
        rec_type = "NORMAL"
    else:
        fallback = [
            c for c in candidates
            if c["distance_to_ship_m"] <= DISTANCE_FALLBACK_M
        ]
        ranked = sorted(fallback, key=rank_key)
        rec_type = "FALLBACK"

    if limit is not None:
        ranked = ranked[:limit]

    return {
        "recommendation_type": rec_type,
        "items": ranked,
    }


# ============================================================
# 데모
# ============================================================

if __name__ == "__main__":
    safe = {"PTY": "0", "RN1": "0", "WSD": "3.0", "REH": "55", "T1H": "12"}
    caution = {"PTY": "1", "RN1": "8", "WSD": "9.5", "REH": "92", "T1H": "4"}
    nogo = {"PTY": "3", "RN1": "35", "WSD": "16.0", "REH": "95", "T1H": "-1"}

    cases = [
        ("1) 둘 다 충족",         180_000, safe,    safe, [safe], False, 0),
        ("2) 거리 주의",          260_000, safe,    safe, [safe], False, 0),
        ("3) 기상 주의",          180_000, caution, safe, [caution], False, 0),
        ("4) 거리 NO_GO",         330_000, safe,    safe, [safe], False, 0),
        ("5) 기상 NO_GO",         180_000, nogo,    safe, [safe], False, 0),
        ("6) fallback 거리",      420_000, safe,    safe, [safe], False, 0),
        ("7) 외상 + 외상센터",    260_000, safe,    safe, [safe], True,  1),
    ]

    for title, dist, ow, tw, rw, trauma, is_tc in cases:
        r = evaluate_candidate(
            distance_m=dist,
            origin_weather=ow,
            target_weather=tw,
            route_weathers=rw,
            is_trauma_center=is_tc,
        )
        print(f"\n[{title}]  거리={dist/1000:.0f}km")
        print(f"  level={r['final_level']}  can_fly={r['can_fly']}  "
              f"weather={r['final_weather_score']}  fallback={r['is_fallback_distance_candidate']}")
        print(f"  사유: {r['decision_reason']}")

    # 추천 종합
    print("\n--- recommend_candidates 일반 환자 ---")
    cands = [
        evaluate_candidate(180_000, safe, safe, [safe], is_trauma_center=0),
        evaluate_candidate(260_000, safe, safe, [safe], is_trauma_center=1),
        evaluate_candidate(170_000, caution, safe, [caution], is_trauma_center=1),
    ]
    rec = recommend_candidates(cands, trauma=False, limit=3)
    print(f"type={rec['recommendation_type']}  count={len(rec['items'])}")
    for i, item in enumerate(rec["items"], 1):
        print(f"  {i}. dist={item['distance_to_ship_m']/1000:.0f}km  "
              f"wx={item['final_weather_score']}  "
              f"fly={item['flight_time_min']}min  "
              f"trauma={item['is_trauma_center']}")
