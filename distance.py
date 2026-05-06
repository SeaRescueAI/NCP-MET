"""해상 응급후송 거리/위치 계산 유틸.

핵심 함수:
- haversine_m: 두 위경도 간 대권거리 (미터)
- destination_point: 출발 좌표 + 방위 + 거리 → 도착 좌표
- bearing_from_vector: 동/북 성분 벡터 → 방위각 (deg, 북=0, 시계방향)
- predict_ship_position: 선박 현재좌표 + 속도벡터 + 경과시간 → 예측좌표
- clip_to_kr_eez: 한국 EEZ 범위 안으로 좌표 클리핑

좌표는 모두 (latitude, longitude) WGS84 deg 기준.
거리는 미터, 시간은 분, 속력은 노트(knots) 기본.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


# ============================================================
# 상수
# ============================================================

EARTH_RADIUS_M = 6_371_008.8        # WGS84 평균 반지름 (m)
KNOTS_TO_MPS = 0.514444             # 1 knot = 0.514444 m/s
MPS_TO_KNOTS = 1.0 / KNOTS_TO_MPS

# 한국 EEZ 대략 bbox (해양수산부 공시 EEZ를 단순화한 직사각형 근사)
# 정밀하게 가려면 폴리곤 필요. 여기서는 1차 게이트용.
KR_EEZ_LAT_MIN = 32.0   # 이어도 남방
KR_EEZ_LAT_MAX = 39.0   # 동해 북방한계
KR_EEZ_LON_MIN = 124.0  # 서해 격렬비열도 외측
KR_EEZ_LON_MAX = 132.0  # 동해 독도 외측


# ============================================================
# 데이터 구조
# ============================================================

@dataclass(frozen=True)
class GeoPoint:
    """위경도 좌표 (WGS84 deg)."""
    lat: float
    lon: float


@dataclass(frozen=True)
class ShipVelocity:
    """선박 속도벡터.

    표현 방식 둘 중 하나로 만든다:
    1) heading_deg + speed_knots  (방위 + 속력)
    2) east_knots + north_knots   (동/북 성분)

    내부적으로는 둘 다 가지고 있도록 정규화한다.
    """
    heading_deg: float    # 0=북, 90=동, 180=남, 270=서
    speed_knots: float    # 항상 0 이상

    @classmethod
    def from_components(cls, east_knots: float, north_knots: float) -> "ShipVelocity":
        speed = math.hypot(east_knots, north_knots)
        if speed == 0:
            return cls(heading_deg=0.0, speed_knots=0.0)
        heading = bearing_from_vector(east_knots, north_knots)
        return cls(heading_deg=heading, speed_knots=speed)

    @property
    def east_knots(self) -> float:
        return self.speed_knots * math.sin(math.radians(self.heading_deg))

    @property
    def north_knots(self) -> float:
        return self.speed_knots * math.cos(math.radians(self.heading_deg))


# ============================================================
# 기본 지오 함수
# ============================================================

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """두 위경도 간 대권거리(m). Haversine 공식."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_M * c


def bearing_from_vector(east: float, north: float) -> float:
    """동(east) / 북(north) 성분에서 방위각(deg) 계산.

    방위각 정의: 북쪽 = 0°, 시계방향 (동=90, 남=180, 서=270).
    """
    if east == 0 and north == 0:
        return 0.0
    bearing_rad = math.atan2(east, north)
    bearing_deg = math.degrees(bearing_rad)
    return (bearing_deg + 360.0) % 360.0


def destination_point(
    lat: float,
    lon: float,
    bearing_deg: float,
    distance_m: float,
) -> tuple[float, float]:
    """출발 좌표 + 방위 + 거리 → 도착 좌표.

    구면 위 직접해(direct problem). Haversine의 역방향 공식.
    영해/EEZ 범위 안에서는 충분히 정확 (수백 km 단위 오차 m).

    반환: (lat, lon) deg
    """
    if distance_m == 0:
        return (lat, lon)

    angular_distance = distance_m / EARTH_RADIUS_M
    bearing = math.radians(bearing_deg)
    phi1 = math.radians(lat)
    lam1 = math.radians(lon)

    sin_phi1 = math.sin(phi1)
    cos_phi1 = math.cos(phi1)
    sin_d = math.sin(angular_distance)
    cos_d = math.cos(angular_distance)

    sin_phi2 = sin_phi1 * cos_d + cos_phi1 * sin_d * math.cos(bearing)
    phi2 = math.asin(sin_phi2)

    y = math.sin(bearing) * sin_d * cos_phi1
    x = cos_d - sin_phi1 * sin_phi2
    lam2 = lam1 + math.atan2(y, x)

    lat2 = math.degrees(phi2)
    lon2 = ((math.degrees(lam2) + 540.0) % 360.0) - 180.0  # -180 ~ +180 정규화
    return (lat2, lon2)


# ============================================================
# 선박 위치 예측
# ============================================================

def predict_ship_position(
    lat: float,
    lon: float,
    velocity: ShipVelocity,
    elapsed_min: float,
) -> tuple[float, float]:
    """선박 현재 좌표 + 속도벡터 + 경과시간(분) → 예측 좌표.

    정속 직진 가정. 해류/풍향 보정 없음.
    """
    if elapsed_min <= 0 or velocity.speed_knots <= 0:
        return (lat, lon)

    distance_m = velocity.speed_knots * KNOTS_TO_MPS * elapsed_min * 60.0
    return destination_point(lat, lon, velocity.heading_deg, distance_m)


# ============================================================
# EEZ 클리핑
# ============================================================

def is_in_kr_eez(lat: float, lon: float) -> bool:
    """대한민국 EEZ 단순 bbox 안에 있는지 검사."""
    return (
        KR_EEZ_LAT_MIN <= lat <= KR_EEZ_LAT_MAX
        and KR_EEZ_LON_MIN <= lon <= KR_EEZ_LON_MAX
    )


def clip_to_kr_eez(lat: float, lon: float) -> tuple[float, float]:
    """좌표가 EEZ 밖이면 가장 가까운 경계로 끌어당긴다.

    예측좌표가 영해 밖으로 튀어나가면 후송 의사결정 무의미하므로
    경계에 클램핑해서 무한대 거리 산출을 방지하는 안전장치.
    """
    clipped_lat = min(max(lat, KR_EEZ_LAT_MIN), KR_EEZ_LAT_MAX)
    clipped_lon = min(max(lon, KR_EEZ_LON_MIN), KR_EEZ_LON_MAX)
    return (clipped_lat, clipped_lon)


# ============================================================
# 경로 샘플링 (헬기 비행경로 중간점 기상 평가용)
# ============================================================

def decide_route_sample_count(distance_m: float) -> int:
    """거리에 따른 중간 샘플 개수 결정.

    출발/도착은 별도 평가하므로 여기서 반환하는 건 *중간* 샘플 개수다.
    경로 중간 어딘가에 풍속/강수 NO_GO 패치가 있는지 확인이 목적.

    임계치 근거:
    - 기상청 격자 5km. 그 ~10배(50km)마다 1샘플이면 국지 기상 패치를
      놓치지 않으면서 격자 캐시 효율도 유지.
    - 너무 많이 뽑으면 같은 격자 중복 → API 호출은 캐시로 막히지만
      판정 dict 처리만 늘어남.

    | 거리        | 샘플 개수 |
    |-------------|----------|
    | < 100km     | 1        |
    | 100~200km   | 2        |
    | 200~300km   | 3        |
    | 300km 초과  | 4        |
    """
    km = distance_m / 1000.0
    if km < 100:
        return 1
    if km < 200:
        return 2
    if km < 300:
        return 3
    return 4


def sample_route_points(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    n: int | None = None,
) -> list[tuple[float, float]]:
    """출발-도착 사이의 중간 좌표 n개를 균등 분할로 생성.

    n=None이면 두 좌표 거리에서 자동 결정 (decide_route_sample_count).
    선형 보간(λ ∈ (0, 1) 균등 분할)을 쓴다. 단거리(~수백 km)에서는
    선형이 대권경로와 거의 차이 없음.

    반환: [(lat, lon), ...] — 출발/도착은 포함하지 않음.

    예: n=2면 t=1/3, 2/3 위치
        n=3이면 t=1/4, 2/4, 3/4 위치
    """
    if n is None:
        d = haversine_m(start_lat, start_lon, end_lat, end_lon)
        n = decide_route_sample_count(d)

    if n <= 0:
        return []

    points: list[tuple[float, float]] = []
    for i in range(1, n + 1):
        t = i / (n + 1)
        lat = (1 - t) * start_lat + t * end_lat
        lon = (1 - t) * start_lon + t * end_lon
        points.append((lat, lon))
    return points


def predict_ship_position_clipped(
    lat: float,
    lon: float,
    velocity: ShipVelocity,
    elapsed_min: float,
) -> tuple[float, float, bool]:
    """예측 좌표 + EEZ 클리핑.

    반환: (lat, lon, was_clipped)
    """
    raw_lat, raw_lon = predict_ship_position(lat, lon, velocity, elapsed_min)
    clipped_lat, clipped_lon = clip_to_kr_eez(raw_lat, raw_lon)
    was_clipped = (raw_lat != clipped_lat) or (raw_lon != clipped_lon)
    return (clipped_lat, clipped_lon, was_clipped)


# ============================================================
# 동작 확인용 데모
# ============================================================

if __name__ == "__main__":
    # 예: 부산 남방 30nm 해상에서 침로 045도, 속력 12노트로 항해 중
    ship_lat = 34.8
    ship_lon = 129.0
    velocity = ShipVelocity(heading_deg=45.0, speed_knots=12.0)

    print("초기 위치:", (ship_lat, ship_lon))
    print("속도벡터:", velocity)
    print("  east_knots=", round(velocity.east_knots, 3),
          "north_knots=", round(velocity.north_knots, 3))

    for t_min in [0, 15, 30, 60, 120]:
        lat2, lon2, clipped = predict_ship_position_clipped(
            ship_lat, ship_lon, velocity, t_min
        )
        d_m = haversine_m(ship_lat, ship_lon, lat2, lon2)
        print(f"  t={t_min:>3}분 → ({lat2:.5f}, {lon2:.5f})"
              f"  이동거리 {d_m / 1000:6.2f} km  clipped={clipped}")

    # 컴포넌트 입력 변환 확인
    v2 = ShipVelocity.from_components(east_knots=8.485, north_knots=8.485)
    print("\n성분→방위 변환:", v2)

    # 경로 샘플링 검증
    print("\n경로 샘플링 (선박 → 병원 가정):")
    cases = [
        ("부산해상→부산대 80km",   34.50, 129.10, 35.10, 129.02),
        ("동해해상→강릉 150km",    37.20, 130.50, 37.75, 128.91),
        ("울릉도→포항 230km",      37.49, 130.90, 36.02, 129.36),
        ("이어도→제주대 350km",    32.12, 125.18, 33.48, 126.55),
    ]
    for name, sla, slo, ela, elo in cases:
        dist = haversine_m(sla, slo, ela, elo)
        n = decide_route_sample_count(dist)
        pts = sample_route_points(sla, slo, ela, elo)
        print(f"  {name}  거리 {dist/1000:6.1f}km → 샘플 {n}개")
        for i, (la, lo) in enumerate(pts, 1):
            print(f"     [{i}] ({la:.4f}, {lo:.4f})")
