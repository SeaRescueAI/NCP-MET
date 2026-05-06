"""NCP-MET command line entrypoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from domain import case_from_dict, demo_case
from hospitals import (
    DEFAULT_HOSPITAL_CSV,
    demo_hospitals,
    hospital_from_db_row,
    hospital_from_dict,
    hospitals_from_csv,
)
from pipeline import evaluate_transfer_case


def _load_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NCP-MET 통합 평가 실행기")
    parser.add_argument(
        "--case-json",
        help="후송 케이스 JSON 경로. 생략하면 데모 케이스를 사용한다.",
    )
    parser.add_argument(
        "--hospitals-json",
        help="병원 후보 JSON 배열 경로. 지정하면 CSV보다 우선한다.",
    )
    parser.add_argument(
        "--hospitals-csv",
        default=str(DEFAULT_HOSPITAL_CSV) if DEFAULT_HOSPITAL_CSV.exists() else None,
        help="병원 후보 CSV 경로. 기본값은 hospital_master_merged.csv이다.",
    )
    parser.add_argument(
        "--use-db",
        action="store_true",
        help="병원 후보를 MySQL hospitals_master에서 읽는다.",
    )
    parser.add_argument(
        "--save-db",
        action="store_true",
        help="case/vital/news2/recommendations 결과를 MySQL에 저장한다.",
    )
    parser.add_argument(
        "--live-weather",
        action="store_true",
        help="기상청 API로 실제 기상을 조회한다. 생략하면 데모 기상값을 사용한다.",
    )
    parser.add_argument("--limit", type=int, default=5, help="추천 후보 출력 개수")
    parser.add_argument("--pretty", action="store_true", help="JSON 들여쓰기 출력")
    return parser


def load_case(path: str | None):
    return case_from_dict(_load_json(path)) if path else demo_case()


def load_hospitals(args, conn):
    if args.use_db:
        from db import fetch_active_hospitals

        return [hospital_from_db_row(row) for row in fetch_active_hospitals(conn)]
    if args.hospitals_json:
        return [hospital_from_dict(item) for item in _load_json(args.hospitals_json)]
    if args.hospitals_csv:
        return hospitals_from_csv(args.hospitals_csv)
    return demo_hospitals()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    conn = None
    if args.use_db or args.save_db:
        from db import connect

        conn = connect()

    try:
        case = load_case(args.case_json)
        hospitals = load_hospitals(args, conn)
        result = evaluate_transfer_case(
            case,
            hospitals,
            live_weather=args.live_weather,
            limit=args.limit,
        )

        if args.save_db:
            from db import create_case_bundle

            case_id = create_case_bundle(conn, case, result)
            result["db"] = {"case_id": case_id}
    finally:
        if conn is not None:
            conn.close()

    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
