# NCP Micro Server Runbook

NCP 마이크로 서버에서는 계산 로직은 Python이 수행하고, MySQL은 입력/마스터/결과 저장소로 둔다.

## 1. Python 패키지 설치

```bash
cd /mnt/c/Users/hepsgram/Repos/maritime-emergency-transfer-db
python3 -m pip install -r requirements.txt
```

## 2. DB 환경변수 설정

```bash
export DB_HOST=127.0.0.1
export DB_PORT=3306
export DB_USER=maritime_user
export DB_PASSWORD='비밀번호'
export DB_NAME=maritime_transfer
export KMA_SERVICE_KEY='기상청키'
export EMERGENCY_SERVICE_KEY='응급의료정보키'
```

## 3. 스키마 생성

```bash
mysql -u root -p < schema.sql
```

운영에서는 root 대신 `maritime_user`를 만들고 `maritime_transfer.*` 권한만 부여한다.

## 4. 병원 마스터 CSV 적재

```bash
python3 import_hospitals.py --dry-run
python3 import_hospitals.py
```

`--dry-run`은 CSV 파싱과 기상청 격자 변환만 확인한다. 실제 DB write는 하지 않는다.

## 5. 실시간 가용병상 snapshot 적재

응급의료기관 실시간 가용병상정보 API 키가 있을 때 실행:

```bash
python3 fetch_hospital_snapshot.py --dry-run
python3 fetch_hospital_snapshot.py
```

특정 지역만 조회:

```bash
python3 fetch_hospital_snapshot.py --stage1 부산광역시 --dry-run
python3 fetch_hospital_snapshot.py --stage1 부산광역시
```

snapshot이 적재되면 추천 결과의 자원 판정이 `UNKNOWN`에서 `CHECKED`로 바뀐다.
현재 판정 자원은 `ICU`, `OPERATING_ROOM`, `CT`, `ANGIOGRAPHY`, `VENTILATOR`, `MI_ACCEPTABLE`이다.

## 6. 평가 실행

DB 병원 마스터를 읽고 결과를 터미널에 출력:

```bash
python3 main.py --use-db --pretty --limit 5
```

케이스/바이탈/NEWS2/추천 결과까지 DB에 저장:

```bash
python3 main.py --use-db --save-db --pretty --limit 20
```

실제 기상청 실황까지 조회:

```bash
python3 main.py --use-db --save-db --live-weather --pretty --limit 20
```

## 7. Web UI 실행

웹 폼으로 선박/바이탈 데이터를 입력:

```bash
python3 web_app.py
```

브라우저에서:

```text
http://서버IP:8000
```

포트를 바꿀 때:

```bash
PORT=8080 python3 web_app.py
```

웹 폼의 `DB 병원 마스터`를 켜면 `hospitals_master`에서 후보 병원을 읽고, `DB 저장`을 켜면 입력/평가/추천 결과를 MySQL에 저장한다.

운영 실행 예:

```bash
gunicorn -w 2 -b 0.0.0.0:8000 web_app:app
```

JSON API로 호출:

```bash
curl -X POST http://127.0.0.1:8000/api/evaluate \
  -H 'Content-Type: application/json' \
  -d '{
    "case": {
      "ship_name": "Test Vessel",
      "ship_lat": 34.8,
      "ship_lon": 129.0,
      "velocity": {"heading_deg": 45, "speed_knots": 12},
      "elapsed_min": 30,
      "trauma_flag": false,
      "vital": {
        "rr": 24, "spo2": 92, "oxygen": true,
        "sbp": 82, "hr": 128, "consciousness": "A",
        "temp": 36.4, "spo2_scale": 1
      }
    },
    "options": {"use_db": true, "save_db": false, "limit": 5}
  }'
```

## 8. 추천 결과 조회

```sql
SELECT case_id, duty_name, flight_level, flight_time_min, distance_m, decision_reason
FROM v_recommendations_ranked
WHERE case_id = 1
LIMIT 10;
```

## 현재 한계

- `hospital_master_merged.csv`는 정적 병원 정보만 담고 있어 ICU/수술실/혈관조영 등 실시간 자원 판정은 `UNKNOWN`이다.
- `hospital_snapshot` API 연동 후에는 `available_resources`를 채워 `resource_check_status=CHECKED`로 바꾸면 된다.
- `recommendations.hospital_snapshot_at`은 현재 NULL 허용이다. 실시간 snapshot 연동 뒤에는 해당 시각을 저장하면 된다.
