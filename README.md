# NCP-MET

Maritime Emergency Transfer 웹 입력/평가/추천 서비스.

## 구성

- `web_app.py`: Web UI와 JSON API
- `main.py`: CLI 진입점
- `domain.py`: 케이스/병원 도메인 타입과 입력 변환
- `hospitals.py`: CSV/DB/데모 병원 후보 로딩
- `pipeline.py`: 환자/거리/기상/비행 평가 파이프라인
- `db.py`: MySQL 저장/조회
- `import_hospitals.py`: `hospital_master_merged.csv` 병원 마스터 적재
- `patient_eval.py`: NEWS2 환자 평가
- `flight_eval.py`: 비행 가능성 평가
- `distance.py`: 거리, 선박 위치 예측, 경로 샘플링
- `weather.py`: 기상청 격자 변환/API
- `schema.sql`: MySQL 스키마
- `templates/`, `static/`: 웹 화면

## 빠른 실행

```bash
python3 -m pip install -r requirements.txt
python3 web_app.py
```

브라우저:

```text
http://127.0.0.1:8000
```

## NCP 배포

서버 배포 절차는 `NCP_RUNBOOK.md`를 따른다.
