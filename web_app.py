"""Web UI/API for maritime emergency transfer evaluation."""

from __future__ import annotations

import os
from typing import Any

from flask import Flask, jsonify, render_template, request

from distance import ShipVelocity
from domain import TransferCase, case_from_dict
from hospitals import DEFAULT_HOSPITAL_CSV, hospital_from_db_row, hospitals_from_csv
from patient_eval import VitalInput
from pipeline import evaluate_transfer_case


DEFAULT_LIMIT = 10

app = Flask(__name__)


def _bool_form(name: str) -> bool:
    return request.form.get(name) in ("1", "true", "on", "yes")


def _int_form(name: str) -> int:
    return int(request.form[name])


def _float_form(name: str, default: float | None = None) -> float:
    value = request.form.get(name)
    if value in (None, ""):
        if default is None:
            raise ValueError(f"{name} is required")
        return default
    return float(value)


def case_from_form() -> TransferCase:
    vital = VitalInput(
        rr=_int_form("rr"),
        spo2=_int_form("spo2"),
        oxygen=_bool_form("oxygen"),
        sbp=_int_form("sbp"),
        hr=_int_form("hr"),
        consciousness=request.form["consciousness"],
        temp=_float_form("temp"),
        spo2_scale=int(request.form.get("spo2_scale", "1")),
    )
    velocity = ShipVelocity(
        heading_deg=_float_form("heading_deg", 0.0),
        speed_knots=_float_form("speed_knots", 0.0),
    )
    return TransferCase(
        ship_name=request.form.get("ship_name") or None,
        ship_lat=_float_form("ship_lat"),
        ship_lon=_float_form("ship_lon"),
        velocity=velocity,
        elapsed_min=_float_form("elapsed_min", 0.0),
        heli_speed_knots=_float_form("heli_speed_knots", 140.0),
        trauma_flag=_bool_form("trauma_flag"),
        vital=vital,
    )


def _load_hospitals(use_db: bool) -> tuple[list, Any | None, str]:
    if use_db:
        from db import connect, fetch_active_hospitals

        conn = connect()
        rows = fetch_active_hospitals(conn)
        return [hospital_from_db_row(row) for row in rows], conn, "db"

    return hospitals_from_csv(DEFAULT_HOSPITAL_CSV), None, "csv"


def _evaluate_and_optionally_save(
    case: TransferCase,
    *,
    use_db: bool,
    save_db: bool,
    live_weather: bool,
    limit: int,
) -> dict[str, Any]:
    hospitals, conn, hospital_source = _load_hospitals(use_db or save_db)
    try:
        result = evaluate_transfer_case(
            case,
            hospitals,
            live_weather=live_weather,
            limit=limit,
        )
        result["runtime"] = {
            "hospital_source": hospital_source,
            "hospital_count": len(hospitals),
            "live_weather": live_weather,
        }
        if save_db:
            from db import create_case_bundle

            case_id = create_case_bundle(conn, case, result)
            result["db"] = {"case_id": case_id}
        return result
    finally:
        if conn is not None:
            conn.close()


@app.get("/")
def index():
    return render_template("index.html", result=None, form={})


@app.post("/evaluate")
def evaluate_form():
    form = dict(request.form)
    try:
        case = case_from_form()
        result = _evaluate_and_optionally_save(
            case,
            use_db=_bool_form("use_db"),
            save_db=_bool_form("save_db"),
            live_weather=_bool_form("live_weather"),
            limit=int(request.form.get("limit", DEFAULT_LIMIT)),
        )
        return render_template("index.html", result=result, form=form, error=None)
    except Exception as exc:
        return render_template("index.html", result=None, form=form, error=str(exc)), 400


@app.post("/api/evaluate")
def evaluate_api():
    payload = request.get_json(force=True)
    case = case_from_dict(payload["case"])
    options = payload.get("options", {})
    result = _evaluate_and_optionally_save(
        case,
        use_db=bool(options.get("use_db", True)),
        save_db=bool(options.get("save_db", False)),
        live_weather=bool(options.get("live_weather", False)),
        limit=int(options.get("limit", DEFAULT_LIMIT)),
    )
    return jsonify(result)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG") == "1")
