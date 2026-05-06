"""hospital_master_merged.csv를 MySQL hospitals_master로 적재한다."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from db import connect, normalize_hospital_csv_row, transaction, upsert_hospitals


DEFAULT_CSV = Path(__file__).with_name("hospital_master_merged.csv")


def load_hospital_rows(path: str | Path) -> list[dict]:
    rows: list[dict] = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            normalized = normalize_hospital_csv_row(row)
            if normalized is not None:
                rows.append(normalized)
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="병원 CSV를 MySQL에 upsert")
    parser.add_argument(
        "--csv",
        default=str(DEFAULT_CSV),
        help="hospital_master_merged.csv 경로",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="DB에 쓰지 않고 파싱 결과만 확인",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rows = load_hospital_rows(args.csv)

    if args.dry_run:
        print(f"parsed_rows={len(rows)}")
        if rows:
            sample = rows[0]
            print(
                "sample="
                f"{sample['hpid']} {sample['duty_name']} "
                f"({sample['lat']}, {sample['lon']}) "
                f"grid=({sample['nx']},{sample['ny']})"
            )
        return 0

    conn = connect()
    try:
        with transaction(conn):
            affected = upsert_hospitals(conn, rows)
    finally:
        conn.close()

    print(f"upsert_rows={len(rows)} affected={affected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
