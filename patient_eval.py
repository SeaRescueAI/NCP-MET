"""환자 상태 평가 (NEWS2) + 필요 자원 결정.

★ 이 모듈은 *환자 평가*만 담당. 비행 가능성(거리/기상)은 flight_eval.py. ★
입력: vital sign (호흡수, SpO2, 산소투여, 혈압, 맥박, 의식, 체온)
출력: NEWS2 총점/위험도 + 후송 시 필요한 병원 자원 요구사항

환자-평가-v2.ipynb 의 셀들을 모듈로 추출한 것. 로직 변경 없음.

NEWS2 (National Early Warning Score 2):
- RR/SpO2/Oxygen/SBP/HR/Consciousness/Temp 7개 항목 점수 합산
- 단일 항목 3점이면 SINGLE_RED, 총점 5+ MEDIUM, 7+ HIGH
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


# ============================================================
# 입력
# ============================================================

@dataclass
class VitalInput:
    """환자 vital sign 입력.

    consciousness: 'A','C','V','P','U' 또는 'Alert','Confusion','Voice','Pain','Unresponsive'
    spo2_scale: 1=일반, 2=만성고탄산성호흡부전 환자
    """
    rr: int            # 호흡수 (breaths/min)
    spo2: int          # 산소포화도 (%)
    oxygen: bool       # 산소 투여 여부
    sbp: int           # 수축기혈압 (mmHg)
    hr: int            # 맥박수 (beats/min)
    consciousness: str
    temp: float        # 체온 (℃)
    spo2_scale: int = 1


# ============================================================
# 항목별 점수
# ============================================================

def score_rr(rr: int) -> int:
    if rr <= 8: return 3
    if 9 <= rr <= 11: return 1
    if 12 <= rr <= 20: return 0
    if 21 <= rr <= 24: return 2
    return 3


def score_spo2_scale1(spo2: int) -> int:
    if spo2 <= 91: return 3
    if 92 <= spo2 <= 93: return 2
    if 94 <= spo2 <= 95: return 1
    return 0


def score_spo2_scale2(spo2: int, oxygen: bool) -> int:
    if spo2 <= 83: return 3
    if 84 <= spo2 <= 85: return 2
    if 86 <= spo2 <= 87: return 1
    if 88 <= spo2 <= 92: return 0
    if not oxygen: return 0
    if 93 <= spo2 <= 94: return 1
    if 95 <= spo2 <= 96: return 2
    return 3


def score_spo2(spo2: int, oxygen: bool, scale: int = 1) -> int:
    if scale == 1:
        return score_spo2_scale1(spo2)
    if scale == 2:
        return score_spo2_scale2(spo2, oxygen)
    raise ValueError("spo2_scale은 1 또는 2만 가능합니다.")


def score_oxygen(oxygen: bool) -> int:
    return 2 if oxygen else 0


def score_sbp(sbp: int) -> int:
    if sbp <= 90: return 3
    if 91 <= sbp <= 100: return 2
    if 101 <= sbp <= 110: return 1
    if 111 <= sbp <= 219: return 0
    return 3


def score_hr(hr: int) -> int:
    if hr <= 40: return 3
    if 41 <= hr <= 50: return 1
    if 51 <= hr <= 90: return 0
    if 91 <= hr <= 110: return 1
    if 111 <= hr <= 130: return 2
    return 3


def score_consciousness(consciousness: str) -> int:
    """ACVPU → NEWS2 점수. Alert만 0점, 나머지는 모두 3점."""
    value = consciousness.strip().upper()
    alert_values = {"A", "ALERT"}
    abnormal_values = {
        "C", "CONFUSION", "NEW CONFUSION",
        "V", "VOICE",
        "P", "PAIN",
        "U", "UNRESPONSIVE",
    }
    if value in alert_values:
        return 0
    if value in abnormal_values:
        return 3
    raise ValueError(
        "consciousness는 A, C, V, P, U 또는 Alert, Confusion, Voice, Pain, Unresponsive 중 하나여야 합니다."
    )


def score_temp(temp: float) -> int:
    if temp <= 35.0: return 3
    if 35.1 <= temp <= 36.0: return 1
    if 36.1 <= temp <= 38.0: return 0
    if 38.1 <= temp <= 39.0: return 1
    return 2


# ============================================================
# 종합 점수 + 위험도
# ============================================================

def classify_news2(total_score: int, single_red: bool) -> str:
    """총점·단일 3점 여부로 위험도 분류."""
    if total_score >= 7: return "HIGH"
    if total_score >= 5: return "MEDIUM"
    if single_red: return "SINGLE_RED"
    if total_score >= 1: return "LOW"
    return "NORMAL"


def calculate_news2(vital: VitalInput) -> Dict:
    """항목별 점수 + 총점 + 위험도 + 단일 3점 항목들."""
    item_scores = {
        "rr": score_rr(vital.rr),
        "spo2": score_spo2(vital.spo2, vital.oxygen, vital.spo2_scale),
        "oxygen": score_oxygen(vital.oxygen),
        "sbp": score_sbp(vital.sbp),
        "hr": score_hr(vital.hr),
        "consciousness": score_consciousness(vital.consciousness),
        "temp": score_temp(vital.temp),
    }

    total_score = sum(item_scores.values())
    red_items: List[str] = [item for item, s in item_scores.items() if s == 3]
    single_red = len(red_items) > 0

    return {
        "total_score": total_score,
        "risk_level": classify_news2(total_score, single_red),
        "single_red": single_red,
        "red_items": red_items,
        "item_scores": item_scores,
    }


# ============================================================
# 필요 자원 결정
# ============================================================

def decide_required_resources(news2_result: Dict, trauma_flag: bool) -> Dict:
    """NEWS2 결과 + 외상 플래그 → 후송 시 필요한 병원 자원.

    반환:
        hospital_type: TRAUMA_CENTER 또는 EMERGENCY_MEDICAL_INSTITUTION
        resources: ICU/VENTILATOR/OPERATING_ROOM/ANGIOGRAPHY/MI_ACCEPTABLE/CT 중 일부
    """
    required = {"hospital_type": None, "resources": []}

    if trauma_flag:
        required["hospital_type"] = "TRAUMA_CENTER"
        required["resources"].extend(["OPERATING_ROOM", "ICU"])
        return required

    required["hospital_type"] = "EMERGENCY_MEDICAL_INSTITUTION"
    total_score = news2_result["total_score"]
    red_items = news2_result["red_items"]

    if total_score >= 7:
        required["resources"].append("ICU")
    if "rr" in red_items or "spo2" in red_items:
        required["resources"].extend(["VENTILATOR", "ICU"])
    if "hr" in red_items or "sbp" in red_items:
        required["resources"].extend(["OPERATING_ROOM", "ANGIOGRAPHY", "MI_ACCEPTABLE"])
    if "consciousness" in red_items:
        required["resources"].extend(["ICU", "CT"])

    required["resources"] = list(dict.fromkeys(required["resources"]))
    return required


# ============================================================
# 데모
# ============================================================

if __name__ == "__main__":
    from pprint import pprint

    cases = [
        ("STEMI 심인성쇼크", VitalInput(rr=24, spo2=92, oxygen=True, sbp=82, hr=128,
                                         consciousness="A", temp=36.4), False),
        ("패혈성 쇼크",       VitalInput(rr=28, spo2=88, oxygen=True, sbp=76, hr=132,
                                         consciousness="C", temp=39.6), False),
        ("다발성 외상",       VitalInput(rr=30, spo2=84, oxygen=True, sbp=72, hr=142,
                                         consciousness="P", temp=35.2), True),
    ]

    for name, vital, trauma in cases:
        result = calculate_news2(vital)
        required = decide_required_resources(result, trauma_flag=trauma)
        print(f"\n[{name}]  trauma={trauma}")
        print(f"  total={result['total_score']}  risk={result['risk_level']}  "
              f"red={result['red_items']}")
        print(f"  hospital_type={required['hospital_type']}")
        print(f"  resources={required['resources']}")
