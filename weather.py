"""좌표 → 기상 정보 모듈.

핵심 함수:
- latlon_to_grid: 위경도(WGS84) → 기상청 LCC 격자 (nx, ny)
- grid_to_latlon: 격자 → 위경도 (역변환)
- fetch_weather_ncst: 격자 좌표로 초단기실황 조회
- fetch_weather_for_latlon: 위경도로 바로 조회 (내부에서 격자 변환)
- fetch_weather_for_points: 여러 좌표 일괄 조회 (메모리 캐시 적용)

기상청 단기예보 조회서비스 2.0 / getUltraSrtNcst 사용.
- base_time은 매 정시 발표 (현재시각 -1시간으로 안정 조회)
- 응답 항목: T1H 기온, RN1 1시간강수, REH 습도, WSD 풍속, PTY 강수형태,
            UUU 동서바람, VVV 남북바람, VEC 풍향
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable

import requests


SERVICE_KEY = os.getenv("KMA_SERVICE_KEY")
if not SERVICE_KEY:
    raise RuntimeError("KMA_SERVICE_KEY 환경변수가 필요합니다.")

NCST_URL = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst"
VILAGE_URL = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"

# 단기예보 발표 슬롯 (시각, 분). 매일 8회.
VILAGE_BASE_SLOTS = [(2, 0), (5, 0), (8, 0), (11, 0), (14, 0), (17, 0), (20, 0), (23, 0)]
VILAGE_RELEASE_DELAY_MIN = 10   # 발표 후 자료 수신까지 안전 대기 (분)


# ============================================================
# 1. LCC 격자 변환 (기상청 공식)
# ============================================================
#
# 기상청 단기예보는 람베르트 정각원추도법(LCC)을 쓴다.
# 격자 5km 간격, 가로 149 × 세로 253.
# 아래 상수는 기상청 격자 정의서 기준.

_RE = 6371.00877       # 지구 반지름 (km)
_GRID = 5.0            # 격자 간격 (km)
_SLAT1 = 30.0          # 표준위도 1
_SLAT2 = 60.0          # 표준위도 2
_OLON = 126.0          # 기준점 경도
_OLAT = 38.0           # 기준점 위도
_XO = 43               # 기준점 X (격자)
_YO = 136              # 기준점 Y (격자)


def _lcc_constants():
    """LCC 변환에 쓰는 미리 계산되는 상수 묶음."""
    DEGRAD = math.pi / 180.0
    re = _RE / _GRID
    slat1 = _SLAT1 * DEGRAD
    slat2 = _SLAT2 * DEGRAD
    olon = _OLON * DEGRAD
    olat = _OLAT * DEGRAD

    sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
    sf = (sf ** sn) * math.cos(slat1) / sn
    ro = math.tan(math.pi * 0.25 + olat * 0.5)
    ro = re * sf / (ro ** sn)

    return DEGRAD, re, sn, sf, ro, olon


def latlon_to_grid(lat: float, lon: float) -> tuple[int, int]:
    """WGS84 위경도 → 기상청 LCC 격자 (nx, ny)."""
    DEGRAD, re, sn, sf, ro, olon = _lcc_constants()

    ra = math.tan(math.pi * 0.25 + lat * DEGRAD * 0.5)
    ra = re * sf / (ra ** sn)
    theta = lon * DEGRAD - olon
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn

    nx = int(ra * math.sin(theta) + _XO + 0.5)
    ny = int(ro - ra * math.cos(theta) + _YO + 0.5)
    return (nx, ny)


def grid_to_latlon(nx: int, ny: int) -> tuple[float, float]:
    """격자 (nx, ny) → 위경도 (역변환)."""
    DEGRAD, re, sn, sf, ro, olon = _lcc_constants()
    RADDEG = 180.0 / math.pi

    xn = nx - _XO
    yn = ro - ny + _YO
    ra = math.sqrt(xn * xn + yn * yn)
    if sn < 0:
        ra = -ra
    alat = (re * sf / ra) ** (1.0 / sn)
    alat = 2.0 * math.atan(alat) - math.pi * 0.5

    if abs(xn) <= 0:
        theta = 0.0
    else:
        if abs(yn) <= 0:
            theta = math.pi * 0.5
            if xn < 0:
                theta = -theta
        else:
            theta = math.atan2(xn, yn)

    alon = theta / sn + olon
    return (alat * RADDEG, alon * RADDEG)


# ============================================================
# 2. 기상청 발표 기준 시각 계산
# ============================================================

def get_base_datetime(now: datetime | None = None) -> tuple[str, str]:
    """초단기실황 base_date / base_time 산출.

    매 정시(40분 이후) 자료가 안정적으로 들어오므로
    현재시각 -1시간 정시를 기준으로 잡는다.
    """
    now = now or datetime.now()
    target = now - timedelta(hours=1)
    return target.strftime("%Y%m%d"), target.strftime("%H00")


def get_vilage_base_datetime(now: datetime | None = None) -> tuple[str, str]:
    """단기예보 base_date / base_time 산출.

    발표 슬롯: 02/05/08/11/14/17/20/23시 (3시간 단위).
    각 발표분은 약 10분 후부터 안정적으로 조회 가능하므로
    현재시각 - 10분 시점에서 가장 최근 슬롯을 선택한다.

    예: 19:50 호출 → 17:00 발표분 매핑.
        20:15 호출 → 20:00 발표분 매핑.
        20:05 호출 → 17:00 발표분 (20:00은 아직 미수신)
    """
    now = now or datetime.now()
    cutoff = now - timedelta(minutes=VILAGE_RELEASE_DELAY_MIN)

    # 오늘 자정부터 슬롯 후보 생성
    today = cutoff.replace(hour=0, minute=0, second=0, microsecond=0)
    candidates = [today.replace(hour=h, minute=m) for h, m in VILAGE_BASE_SLOTS]
    # 어제 23시도 포함 (새벽 호출 대비)
    candidates.insert(0, today - timedelta(hours=1))   # 어제 23:00

    # cutoff 이전의 가장 최근 슬롯
    valid = [c for c in candidates if c <= cutoff]
    chosen = valid[-1] if valid else candidates[0]
    return chosen.strftime("%Y%m%d"), chosen.strftime("%H%M")


# ============================================================
# 3. API 호출
# ============================================================

@dataclass(frozen=True)
class Weather:
    """초단기실황 결과 한 격자 분량."""
    nx: int
    ny: int
    base_date: str
    base_time: str
    t1h: float | None = None    # 기온 (℃)
    rn1: float | None = None    # 1시간 강수량 (mm)
    reh: float | None = None    # 습도 (%)
    wsd: float | None = None    # 풍속 (m/s)
    pty: int | None = None      # 강수형태 (0없음/1비/2비눈/3눈/5빗방울/6빗방울눈날림/7눈날림)
    uuu: float | None = None    # 동서바람 성분
    vvv: float | None = None    # 남북바람 성분
    vec: float | None = None    # 풍향 (deg)
    raw: dict = field(default_factory=dict)


def _to_float(value) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    if s == "" or s == "강수없음":
        return 0.0 if s == "강수없음" else None
    try:
        return float(s)
    except ValueError:
        return None


def _to_int(value) -> int | None:
    f = _to_float(value)
    return int(f) if f is not None else None


def fetch_weather_ncst(
    nx: int,
    ny: int,
    base_datetime: tuple[str, str] | None = None,
    timeout: float = 10.0,
) -> Weather:
    """기상청 초단기실황 API 호출.

    base_datetime: (yyyymmdd, hhmm). None이면 현재시각 -1h 정시 사용.
    """
    base_date, base_time = base_datetime or get_base_datetime()

    params = {
        "serviceKey": SERVICE_KEY,
        "pageNo": "1",
        "numOfRows": "1000",
        "dataType": "JSON",
        "base_date": base_date,
        "base_time": base_time,
        "nx": str(nx),
        "ny": str(ny),
    }

    response = requests.get(NCST_URL, params=params, timeout=timeout)
    response.raise_for_status()
    data = response.json()

    header = data["response"]["header"]
    if header["resultCode"] != "00":
        raise RuntimeError(
            f"기상청 API 오류: {header['resultCode']} / {header['resultMsg']}"
        )

    items = data["response"]["body"]["items"]["item"]
    raw = {item["category"]: item["obsrValue"] for item in items}

    return Weather(
        nx=nx,
        ny=ny,
        base_date=base_date,
        base_time=base_time,
        t1h=_to_float(raw.get("T1H")),
        rn1=_to_float(raw.get("RN1")),
        reh=_to_float(raw.get("REH")),
        wsd=_to_float(raw.get("WSD")),
        pty=_to_int(raw.get("PTY")),
        uuu=_to_float(raw.get("UUU")),
        vvv=_to_float(raw.get("VVV")),
        vec=_to_float(raw.get("VEC")),
        raw=raw,
    )


def fetch_weather_for_latlon(
    lat: float,
    lon: float,
    base_datetime: tuple[str, str] | None = None,
) -> Weather:
    """위경도로 바로 조회 (내부에서 격자 변환)."""
    nx, ny = latlon_to_grid(lat, lon)
    return fetch_weather_ncst(nx, ny, base_datetime=base_datetime)


# ============================================================
# 3-2. 단기예보 (3시간 단위 발표, 미래 예보 조회)
# ============================================================

# 단기예보 카테고리 (초단기와 일부 다름)
#   TMP 기온, REH 습도, WSD 풍속, PTY 강수형태, PCP 1시간강수량,
#   POP 강수확률, SKY 하늘상태, VEC 풍향, UUU/VVV 바람성분, WAV 파고

@dataclass(frozen=True)
class Forecast:
    """단기예보 한 격자 × 한 예보시각."""
    nx: int
    ny: int
    base_date: str          # 발표일자
    base_time: str          # 발표시각 (예: "1700")
    fcst_date: str          # 예보일자
    fcst_time: str          # 예보시각 (예: "2000")
    tmp: float | None = None    # 기온 (℃)
    reh: float | None = None    # 습도 (%)
    wsd: float | None = None    # 풍속 (m/s)
    pty: int | None = None      # 강수형태
    pcp: float | None = None    # 1시간강수량 (mm)
    pop: float | None = None    # 강수확률 (%)
    sky: int | None = None      # 하늘상태 (1맑음/3구름많음/4흐림)
    vec: float | None = None    # 풍향 (deg)
    uuu: float | None = None
    vvv: float | None = None
    wav: float | None = None    # 파고 (m, 해상격자만)
    raw: dict = field(default_factory=dict)

    @property
    def fcst_datetime(self) -> datetime:
        return datetime.strptime(self.fcst_date + self.fcst_time, "%Y%m%d%H%M")


def _parse_pcp(value) -> float | None:
    """단기예보 PCP는 '강수없음' / '1mm 미만' / '30.0~50.0mm' 등 문자열."""
    if value is None:
        return None
    s = str(value).strip()
    if s in ("", "강수없음"):
        return 0.0
    if "미만" in s:
        return 0.5
    if "~" in s:
        # "30.0~50.0mm" → 중간값
        try:
            lo, hi = s.replace("mm", "").split("~")
            return (float(lo) + float(hi)) / 2.0
        except Exception:
            return None
    if "이상" in s:
        try:
            return float(s.replace("mm 이상", "").replace("이상", "").strip())
        except Exception:
            return None
    try:
        return float(s.replace("mm", ""))
    except ValueError:
        return None


def fetch_vilage_forecast(
    nx: int,
    ny: int,
    base_datetime: tuple[str, str] | None = None,
    timeout: float = 10.0,
) -> dict[datetime, Forecast]:
    """단기예보 API 호출. 한 격자의 +3일치 시계열을 dict로 반환.

    key: fcst_datetime (datetime), value: Forecast
    """
    base_date, base_time = base_datetime or get_vilage_base_datetime()

    params = {
        "serviceKey": SERVICE_KEY,
        "pageNo": "1",
        "numOfRows": "1000",
        "dataType": "JSON",
        "base_date": base_date,
        "base_time": base_time,
        "nx": str(nx),
        "ny": str(ny),
    }

    response = requests.get(VILAGE_URL, params=params, timeout=timeout)
    response.raise_for_status()
    data = response.json()

    header = data["response"]["header"]
    if header["resultCode"] != "00":
        raise RuntimeError(
            f"기상청 API 오류: {header['resultCode']} / {header['resultMsg']}"
        )

    items = data["response"]["body"]["items"]["item"]

    # (fcst_date, fcst_time) 별로 카테고리 묶기
    grouped: dict[tuple[str, str], dict[str, str]] = {}
    for item in items:
        key = (item["fcstDate"], item["fcstTime"])
        grouped.setdefault(key, {})[item["category"]] = item["fcstValue"]

    result: dict[datetime, Forecast] = {}
    for (fdate, ftime), cats in grouped.items():
        f = Forecast(
            nx=nx,
            ny=ny,
            base_date=base_date,
            base_time=base_time,
            fcst_date=fdate,
            fcst_time=ftime,
            tmp=_to_float(cats.get("TMP")),
            reh=_to_float(cats.get("REH")),
            wsd=_to_float(cats.get("WSD")),
            pty=_to_int(cats.get("PTY")),
            pcp=_parse_pcp(cats.get("PCP")),
            pop=_to_float(cats.get("POP")),
            sky=_to_int(cats.get("SKY")),
            vec=_to_float(cats.get("VEC")),
            uuu=_to_float(cats.get("UUU")),
            vvv=_to_float(cats.get("VVV")),
            wav=_to_float(cats.get("WAV")),
            raw=cats,
        )
        result[f.fcst_datetime] = f

    return result


def pick_forecast_at(
    forecasts: dict[datetime, Forecast],
    target: datetime,
) -> Forecast | None:
    """원하는 시각에 가장 가까운 예보를 선택.

    단기예보는 보통 1시간 또는 3시간 간격이라 정확히 일치하지 않을 수 있음.
    target 이상 중 가장 빠른 것을 우선 (도착 시점 기상이므로 늦어선 안 됨),
    없으면 그 이전 중 가장 늦은 것.
    """
    if not forecasts:
        return None

    sorted_keys = sorted(forecasts.keys())
    after = [k for k in sorted_keys if k >= target]
    if after:
        return forecasts[after[0]]
    return forecasts[sorted_keys[-1]]


def fetch_forecast_for_latlon_at(
    lat: float,
    lon: float,
    target: datetime,
    base_datetime: tuple[str, str] | None = None,
) -> Forecast | None:
    """위경도 + 도착예상시각 → 해당 시점에 가장 가까운 예보."""
    nx, ny = latlon_to_grid(lat, lon)
    forecasts = fetch_vilage_forecast(nx, ny, base_datetime=base_datetime)
    return pick_forecast_at(forecasts, target)


# ============================================================
# 4. 다중 좌표 일괄 조회 (격자 단위 메모리 캐시)
# ============================================================

def fetch_weather_for_points(
    points: Iterable[tuple[float, float]],
    base_datetime: tuple[str, str] | None = None,
) -> list[Weather]:
    """여러 (lat, lon) 좌표의 기상을 한 번에 조회.

    같은 격자에 떨어지는 좌표는 1번만 API 호출. 같은 격자면 같은 Weather 반환.
    호출자가 list로 강제하면 좌표 순서대로 결과 리스트.
    """
    base = base_datetime or get_base_datetime()
    cache: dict[tuple[int, int], Weather] = {}
    results: list[Weather] = []

    for lat, lon in points:
        grid = latlon_to_grid(lat, lon)
        if grid not in cache:
            cache[grid] = fetch_weather_ncst(grid[0], grid[1], base_datetime=base)
        results.append(cache[grid])

    return results


# ============================================================
# 데모
# ============================================================

if __name__ == "__main__":
    # 부산 (35.18, 129.07)
    lat, lon = 35.18, 129.07
    nx, ny = latlon_to_grid(lat, lon)
    back_lat, back_lon = grid_to_latlon(nx, ny)

    print(f"부산 위경도 → 격자: ({lat}, {lon}) → ({nx}, {ny})")
    print(f"역변환: ({nx}, {ny}) → ({back_lat:.4f}, {back_lon:.4f})  [격자 중심 좌표]")

    print(f"\n현재 base_datetime: {get_base_datetime()}")

    print("\n부산 기상 조회…")
    w = fetch_weather_for_latlon(lat, lon)
    print(f"  T1H={w.t1h}℃  RN1={w.rn1}mm  REH={w.reh}%  WSD={w.wsd}m/s  PTY={w.pty}")
    print(f"  base={w.base_date} {w.base_time}  격자=({w.nx},{w.ny})")

    # 단기예보 base_time 자동 선택 확인
    print("\n단기예보 base_time 매핑 검증:")
    for hh, mm in [(19, 50), (20, 5), (20, 15), (2, 0), (8, 30)]:
        t = datetime(2026, 5, 6, hh, mm)
        base = get_vilage_base_datetime(t)
        print(f"  호출시각 {hh:02}:{mm:02} → base={base}")

    print("\n부산 단기예보 조회 (도착 예상시각 = 현재 +90분)…")
    target = datetime.now() + timedelta(minutes=90)
    f = fetch_forecast_for_latlon_at(lat, lon, target)
    if f:
        print(f"  예보시각 {f.fcst_date} {f.fcst_time}  (요청 {target.strftime('%Y%m%d %H%M')})")
        print(f"  TMP={f.tmp}℃  PCP={f.pcp}mm  REH={f.reh}%  WSD={f.wsd}m/s  PTY={f.pty}  POP={f.pop}%")

    print("\n다중 좌표 일괄 조회…")
    points = [
        (35.18, 129.07),  # 부산
        (35.19, 129.08),  # 부산 같은 격자
        (37.57, 126.98),  # 서울
        (33.49, 126.50),  # 제주
    ]
    weathers = fetch_weather_for_points(points)
    for (la, lo), w in zip(points, weathers):
        print(f"  ({la}, {lo}) → 격자({w.nx},{w.ny})  T1H={w.t1h}℃  WSD={w.wsd}m/s")
