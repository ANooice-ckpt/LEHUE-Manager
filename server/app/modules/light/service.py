from __future__ import annotations

import csv
import hashlib
import math
import os
import re
import secrets
import statistics
import tempfile
import zipfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.db import db
from app.modules.light.storage import get_light_storage

EXPECTED_SAMPLES = 7200
VALID_THRESHOLD_PCT = 90.0
PARSER_VERSION = "light-v1"
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".txt"}

DATE_PATTERNS = (
    re.compile(r"(?P<date>20\d{2}[-_./]?\d{1,2}[-_./]?\d{1,2})"),
    re.compile(r"(?P<date>\d{4}[-_./]\d{1,2}[-_./]\d{1,2})"),
)
SUBJECT_PATTERNS = (
    re.compile(r"(?:^|[^0-9])(?P<sid>\d{3})(?:[^0-9]|$)"),
    re.compile(r"(?P<sid>P\d{3})", re.I),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_date(value: str) -> str:
    text = str(value or "").strip().replace(".", "-").replace("/", "-").replace("_", "-")
    match = re.match(r"^(20\d{2})-(\d{1,2})-(\d{1,2})$", text) or re.match(r"^(20\d{2})(\d{2})(\d{2})$", text)
    if not match:
        return ""
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
    except ValueError:
        return ""


def infer_subject_id(filename: str) -> str:
    for pattern in SUBJECT_PATTERNS:
        if match := pattern.search(filename or ""):
            sid = match.group("sid").upper().removeprefix("P")
            return sid.zfill(3) if sid.isdigit() else sid
    return ""


def infer_date(filename: str) -> str:
    for pattern in DATE_PATTERNS:
        if match := pattern.search(filename or ""):
            return normalize_date(match.group("date"))
    return ""


def _to_float(value):
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in {"nan", "none", "null", "--", "-"}:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _is_saturated(value) -> bool:
    return str(value or "").strip().lower() in {"yes", "true", "1", "y", "saturate", "saturated", "sat", "饱和", "是"}


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        raw = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    root = ET.fromstring(raw)
    return ["".join((node.text or "") for node in item.findall(".//a:t", ns)) for item in root.findall("a:si", ns)]


def _xlsx_col_index(ref: str) -> int:
    match = re.match(r"([A-Z]+)", ref or "")
    if not match:
        return 0
    result = 0
    for char in match.group(1):
        result = result * 26 + ord(char) - 64
    return result - 1


def _rows_from_xlsx(path: Path) -> list[list[str]]:
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows: list[list[str]] = []
    with zipfile.ZipFile(path) as zf:
        shared = _xlsx_shared_strings(zf)
        sheets = sorted(name for name in zf.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
        for sheet in sheets:
            root = ET.fromstring(zf.read(sheet))
            for row in root.findall(".//a:sheetData/a:row", ns):
                values: dict[int, str] = {}
                for cell in row.findall("a:c", ns):
                    index = _xlsx_col_index(cell.attrib.get("r", ""))
                    value = ""
                    scalar = cell.find("a:v", ns)
                    inline = cell.find("a:is", ns)
                    if scalar is not None:
                        value = scalar.text or ""
                        if cell.attrib.get("t") == "s":
                            try:
                                value = shared[int(value)]
                            except (IndexError, ValueError):
                                pass
                    elif inline is not None:
                        value = "".join((node.text or "") for node in inline.findall(".//a:t", ns))
                    values[index] = str(value).strip()
                if values:
                    rows.append([values.get(index, "") for index in range(max(values) + 1)])
    return rows


def _rows_from_text(path: Path) -> list[list[str]]:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "utf-16", "big5"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                sample = handle.read(4096)
                handle.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
                except csv.Error:
                    dialect = csv.excel
                return [[str(value).strip() for value in row] for row in csv.reader(handle, dialect)]
        except (UnicodeError, csv.Error) as exc:
            last_error = exc
    raise ValueError(f"CSV/TXT 编码或格式无法读取：{last_error}")


def _label(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _extract_repeated_records(rows: list[list[str]]) -> list[dict]:
    records: list[dict] = []
    current: dict = {}
    saw_repeated_marker = False

    def finish() -> None:
        nonlocal current
        if current and ("photopic" in current or "melanopic" in current or "modify_time" in current):
            records.append(current)
        current = {}

    for row in rows:
        if not row:
            continue
        key = _label(row[0])
        value = row[1] if len(row) > 1 else ""
        if key == "file name":
            saw_repeated_marker = True
            if current and ("photopic" in current or "melanopic" in current or "modify_time" in current):
                finish()
            current = {"file_name": value}
        elif key == "modify time":
            saw_repeated_marker = True
            if current and ("modify_time" in current or "photopic" in current or "melanopic" in current):
                finish()
            current["modify_time"] = value
        elif key == "is saturate":
            current["saturate"] = value
        elif key == "photopic lux":
            current["photopic"] = _to_float(value)
        elif key == "melanopic":
            current["melanopic"] = _to_float(value)
    finish()
    if not saw_repeated_marker:
        return []
    return [record for record in records if "photopic" in record or "melanopic" in record]


def _extract_table_records(rows: list[list[str]]) -> list[dict]:
    for index, row in enumerate(rows[:80]):
        labels = [_label(value) for value in row]
        if "photopic lux" not in labels or "melanopic" not in labels:
            continue
        photo_index = labels.index("photopic lux")
        melanopic_index = labels.index("melanopic")
        saturated_index = labels.index("is saturate") if "is saturate" in labels else -1
        records: list[dict] = []
        for values in rows[index + 1:]:
            if not any(str(value).strip() for value in values):
                continue
            record = {
                "photopic": _to_float(values[photo_index] if photo_index < len(values) else None),
                "melanopic": _to_float(values[melanopic_index] if melanopic_index < len(values) else None),
            }
            if saturated_index >= 0:
                record["saturate"] = values[saturated_index] if saturated_index < len(values) else ""
            records.append(record)
        return records
    return []


def _stats(values: list[float]) -> tuple[float | None, float | None, float | None]:
    if not values:
        return None, None, None
    return round(sum(values) / len(values), 3), round(float(statistics.median(values)), 3), round(max(values), 3)


def parse_light_path(filename: str, path: Path) -> dict:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError("Lighting 只支持 .csv、.xlsx 或 .txt")
    rows = _rows_from_xlsx(path) if suffix == ".xlsx" else _rows_from_text(path)
    records = _extract_repeated_records(rows) or _extract_table_records(rows)
    total = len(records)
    photopic_values: list[float] = []
    melanopic_values: list[float] = []
    saturated = 0
    for record in records:
        is_saturated = _is_saturated(record.get("saturate"))
        saturated += int(is_saturated)
        photopic = record.get("photopic")
        melanopic = record.get("melanopic")
        if photopic is not None and melanopic is not None and not is_saturated:
            photopic_values.append(photopic)
            melanopic_values.append(melanopic)
    valid = len(photopic_values)
    valid_pct = round(valid / EXPECTED_SAMPLES * 100, 1)
    quality = "unreadable" if total <= 0 else "valid" if valid_pct >= VALID_THRESHOLD_PCT else "insufficient"
    photo_mean, photo_median, photo_max = _stats(photopic_values)
    melanopic_mean, melanopic_median, melanopic_max = _stats(melanopic_values)
    return {
        "records_expected": EXPECTED_SAMPLES,
        "records_total": total,
        "records_valid": valid,
        "records_saturated": saturated,
        "valid_pct": valid_pct,
        "quality": quality,
        "photopic_mean": photo_mean,
        "photopic_median": photo_median,
        "photopic_max": photo_max,
        "melanopic_mean": melanopic_mean,
        "melanopic_median": melanopic_median,
        "melanopic_max": melanopic_max,
        "parse_error": "" if total else "未识别到 Photopic Lux / Melanopic 光谱记录",
    }


def parse_light_bytes(filename: str, raw: bytes) -> dict:
    """Backward-compatible helper; normal ingestion parses from a temporary path."""
    suffix = Path(filename or "").suffix.lower()
    fd, name = tempfile.mkstemp(prefix="lehue-light-parse-", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
        return parse_light_path(filename, Path(name))
    finally:
        Path(name).unlink(missing_ok=True)


async def request_to_temp(request, filename: str) -> Path:
    """Stream an HTTP body to a size-limited temporary file."""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError("Lighting 只支持 .csv、.xlsx 或 .txt")
    fd, name = tempfile.mkstemp(prefix="lehue-light-upload-", suffix=suffix)
    path = Path(name)
    size = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            async for chunk in request.stream():
                size += len(chunk)
                if size > settings.light_upload_max_bytes:
                    raise ValueError(
                        f"Lighting 文件超过上传上限 "
                        f"{settings.light_upload_max_bytes // 1024 // 1024} MB"
                    )
                handle.write(chunk)
        if size == 0:
            raise ValueError("Lighting 文件为空")
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _public_upload(row, duplicate: bool = False) -> dict:
    result = dict(row)
    result.pop("stored_path", None)
    result.pop("object_key", None)
    result.pop("sha256", None)
    result["duplicate"] = duplicate
    return result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def store_upload_path(participant_id: str, date_local: str, filename: str, source_path: Path, uploaded_by: str) -> dict:
    participant_id = str(participant_id or "").strip()
    date_local = normalize_date(date_local)
    filename = Path(str(filename or "")).name
    suffix = Path(filename).suffix.lower()
    if not date_local:
        raise ValueError("Lighting 暴露日期必须使用 YYYY-MM-DD")
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError("Lighting 只支持 .csv、.xlsx 或 .txt")
    source_path = Path(source_path)
    size_bytes = source_path.stat().st_size
    if size_bytes <= 0:
        raise ValueError("Lighting 文件为空")
    if size_bytes > settings.light_upload_max_bytes:
        raise ValueError(f"Lighting 文件超过上传上限 {settings.light_upload_max_bytes // 1024 // 1024} MB")
    inferred_sid = infer_subject_id(filename)
    inferred_date = infer_date(filename)
    if inferred_sid and inferred_sid != participant_id:
        raise ValueError("文件名中的被试 ID 与当前专属入口不一致，请检查是否选错文件")
    if inferred_date and inferred_date != date_local:
        raise ValueError("文件名中的日期与所选暴露日期不一致")
    with db() as conn:
        if not conn.execute("SELECT 1 FROM study_subjects WHERE participant_id=?", (participant_id,)).fetchone():
            raise ValueError("participant not found")
    digest = _sha256_file(source_path)
    with db() as conn:
        existing = conn.execute(
            "SELECT * FROM lighting_files WHERE participant_id=? AND date_local=? AND sha256=?",
            (participant_id, date_local, digest),
        ).fetchone()
    if existing:
        return _public_upload(existing, duplicate=True)

    upload_uid = f"light_{secrets.token_hex(10)}"
    object_key = f"raw/lighting/{participant_id}/{date_local}/{upload_uid}{suffix}"
    storage = get_light_storage()
    head = storage.save(source_path, object_key)
    if head.size_bytes != size_bytes:
        storage.delete(object_key)
        raise RuntimeError("Lighting stored object size does not match upload")

    qc_path: Path | None = None
    try:
        qc_path = storage.download_to_temp(object_key)
        try:
            summary = parse_light_path(filename, qc_path)
        except Exception as exc:
            summary = {
                "records_expected": EXPECTED_SAMPLES, "records_total": 0, "records_valid": 0,
                "records_saturated": 0, "valid_pct": 0.0, "quality": "unreadable",
                "photopic_mean": None, "photopic_median": None, "photopic_max": None,
                "melanopic_mean": None, "melanopic_median": None, "melanopic_max": None,
                "parse_error": str(exc),
            }
        with db() as conn:
            conn.execute(
                """INSERT INTO lighting_files(
                    upload_uid,participant_id,date_local,original_filename,stored_path,storage_backend,object_key,file_size_bytes,sha256,
                    uploaded_at_utc,uploaded_by,parser_version,records_expected,records_total,records_valid,
                    records_saturated,valid_pct,quality,photopic_mean,photopic_median,photopic_max,
                    melanopic_mean,melanopic_median,melanopic_max,parse_error
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    upload_uid, participant_id, date_local, filename, object_key, storage.backend, object_key,
                    size_bytes, digest, now_iso(), uploaded_by, PARSER_VERSION,
                    summary["records_expected"], summary["records_total"], summary["records_valid"], summary["records_saturated"],
                    summary["valid_pct"], summary["quality"], summary["photopic_mean"], summary["photopic_median"],
                    summary["photopic_max"], summary["melanopic_mean"], summary["melanopic_median"], summary["melanopic_max"], summary["parse_error"],
                ),
            )
            row = conn.execute("SELECT * FROM lighting_files WHERE upload_uid=?", (upload_uid,)).fetchone()
    except Exception:
        storage.delete(object_key)
        raise
    finally:
        if qc_path is not None:
            qc_path.unlink(missing_ok=True)
    return _public_upload(row)


def store_upload(participant_id: str, date_local: str, filename: str, raw: bytes, uploaded_by: str) -> dict:
    """Compatibility entry point for existing Python callers and tests."""
    suffix = Path(filename or "").suffix.lower()
    fd, name = tempfile.mkstemp(prefix="lehue-light-upload-", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
        return store_upload_path(participant_id, date_local, filename, Path(name), uploaded_by)
    finally:
        Path(name).unlink(missing_ok=True)


def _quality_rank(row: dict) -> tuple[int, float, int]:
    return (
        {"valid": 0, "insufficient": 1, "unreadable": 2}.get(row.get("quality"), 9),
        -float(row.get("valid_pct") or 0),
        -int(row.get("id") or 0),
    )


def best_for_date(participant_id: str, date_local: str) -> dict | None:
    with db() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM lighting_files WHERE participant_id=? AND date_local=?", (participant_id, date_local))]
    return min(rows, key=_quality_rank) if rows else None


def portal_light_state(participant_id: str, date_local: str) -> dict:
    row = best_for_date(participant_id, date_local)
    if not row:
        return {"status": "missing", "uploaded": False, "quality": None, "valid_pct": None, "filename": None}
    return {
        "status": "done" if row["quality"] == "valid" else row["quality"],
        "uploaded": True,
        "quality": row["quality"],
        "valid_pct": row["valid_pct"],
        "filename": row["original_filename"],
        "uploaded_at_utc": row["uploaded_at_utc"],
        "message": row["parse_error"],
    }


def list_uploads(participant_id: str = "", date_local: str = "") -> list[dict]:
    clauses: list[str] = []
    params: list[str] = []
    if participant_id:
        clauses.append("participant_id=?")
        params.append(participant_id)
    if date_local:
        clauses.append("date_local=?")
        params.append(date_local)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with db() as conn:
        rows = conn.execute(f"SELECT * FROM lighting_files {where} ORDER BY date_local DESC,participant_id,uploaded_at_utc DESC LIMIT 1000", params)
        return [_public_upload(row) for row in rows]


def source_stats() -> dict:
    with db() as conn:
        row = conn.execute("SELECT COUNT(*) records,MAX(uploaded_at_utc) last_event,COUNT(DISTINCT participant_id) participants FROM lighting_files").fetchone()
    return dict(row)


def _date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _utc_bounds(day: date) -> tuple[str, str]:
    zone = ZoneInfo(settings.study_timezone)
    start = datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc)
    end = (datetime.combine(day, time.min, tzinfo=zone) + timedelta(days=1)).astimezone(timezone.utc)
    return start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")


def daily_qc_rows() -> list[dict]:
    now_local = datetime.now(ZoneInfo(settings.study_timezone))
    today = now_local.date()
    with db() as conn:
        subjects = [dict(row) for row in conn.execute("SELECT * FROM study_subjects WHERE status IN ('running','closed','finished') AND start_date<>'' ORDER BY participant_id")]
        forms = {(row["participant_id"], row["date_local"], row["form_key"]) for row in conn.execute("SELECT participant_id,date_local,form_key FROM questionnaire_responses")}
        light_rows = [dict(row) for row in conn.execute("SELECT * FROM lighting_files")]
    best: dict[tuple[str, str], dict] = {}
    for row in light_rows:
        key = (row["participant_id"], row["date_local"])
        if key not in best or _quality_rank(row) < _quality_rank(best[key]):
            best[key] = row

    results: list[dict] = []
    with db() as conn:
        for subject in subjects:
            try:
                start = date.fromisoformat(subject["start_date"])
            except ValueError:
                continue
            end_text = subject["final_end"] or subject["end_date"]
            try:
                planned_end = date.fromisoformat(end_text) if end_text else today
            except ValueError:
                planned_end = today
            if subject["status"] == "running":
                if subject.get("awaiting_final_morning") and subject.get("final_end"):
                    try:
                        end = min(date.fromisoformat(subject["final_end"]), today)
                    except ValueError:
                        end = today
                else:
                    end = today
            else:
                end = min(planned_end, today)
            if end < start:
                continue
            for exposure_day in _date_range(start, end):
                day_text = exposure_day.isoformat()
                morning_text = (exposure_day + timedelta(days=1)).isoformat()
                light = best.get((subject["participant_id"], day_text))
                start_utc, end_utc = _utc_bounds(exposure_day)
                gps = bool(conn.execute(
                    "SELECT 1 FROM gps_locations WHERE participant_id=? AND recorded_at_utc>=? AND recorded_at_utc<? LIMIT 1",
                    (subject["participant_id"], start_utc, end_utc),
                ).fetchone())
                evening = (subject["participant_id"], day_text, "evening") in forms
                morning = (subject["participant_id"], morning_text, "morning") in forms
                issues: list[dict[str, str]] = []
                due = exposure_day < today - timedelta(days=1) or (
                    exposure_day == today - timedelta(days=1) and now_local.hour >= settings.qc_day_close_hour
                )
                if due:
                    if not evening:
                        issues.append({"type": "missing_evening", "label": "当晚问卷"})
                    if not light:
                        issues.append({"type": "missing_light", "label": "Lighting未上传"})
                    elif light["quality"] == "insufficient":
                        issues.append({"type": "insufficient_light", "label": f"Lighting样本不足（{light['valid_pct']:.1f}%）"})
                    elif light["quality"] != "valid":
                        issues.append({"type": "unreadable_light", "label": "Lighting无法解析"})
                    if not gps:
                        issues.append({"type": "missing_gps", "label": "GPS"})
                    if not morning:
                        issues.append({"type": "missing_morning", "label": f"次晨问卷（{morning_text}）"})
                status = "pending" if not due else "missing" if issues else "ok"
                results.append({
                    "participant_id": subject["participant_id"], "batch_id": subject["batch_id"],
                    "date_local": day_text, "morning_date": morning_text,
                    "study_day": (exposure_day - start).days + 1, "status": status,
                    "evening": evening, "morning": morning, "gps": gps,
                    "lighting": "missing" if not light else light["quality"],
                    "light_valid_pct": light["valid_pct"] if light else None,
                    "light_records_valid": light["records_valid"] if light else None,
                    "light_records_total": light["records_total"] if light else None,
                    "photopic_mean": light["photopic_mean"] if light else None,
                    "melanopic_mean": light["melanopic_mean"] if light else None,
                    "issues": issues, "missing_items": "、".join(item["label"] for item in issues),
                })
    return results


def run_daily_qc(operator: str) -> dict:
    rows = daily_qc_rows()
    running_ids: set[str]
    with db() as conn:
        running_ids = {row["participant_id"] for row in conn.execute("SELECT participant_id FROM study_subjects WHERE status='running'")}
        expected_incidents: set[str] = set()
        valid_days: dict[str, int] = {}
        for row in rows:
            if row["status"] == "ok":
                valid_days[row["participant_id"]] = valid_days.get(row["participant_id"], 0) + 1
            if row["participant_id"] not in running_ids or row["status"] != "missing":
                continue
            for item in row["issues"]:
                uid = f"acq_{row['participant_id']}_{row['date_local']}_{item['type']}"
                expected_incidents.add(uid)
                existing = conn.execute("SELECT status FROM incidents WHERE incident_uid=?", (uid,)).fetchone()
                summary = f"{row['participant_id']} 暴露日 {row['date_local']}：{item['label']}"
                now = now_iso()
                if existing:
                    if existing["status"] != "closed":
                        conn.execute("UPDATE incidents SET status='open',summary=?,updated_at_utc=? WHERE incident_uid=?", (summary, now, uid))
                else:
                    conn.execute(
                        """INSERT INTO incidents(incident_uid,participant_id,date_local,source,incident_type,severity,status,summary,created_at_utc,updated_at_utc)
                           VALUES(?,?,?,?,?,'normal','open',?,?,?)""",
                        (uid, row["participant_id"], row["date_local"], "acquisition_qc", item["type"], summary, now, now),
                    )
        auto_open = [row["incident_uid"] for row in conn.execute("SELECT incident_uid FROM incidents WHERE source='acquisition_qc' AND status IN ('open','handling')")]
        for uid in auto_open:
            if uid not in expected_incidents:
                conn.execute("UPDATE incidents SET status='resolved',updated_at_utc=? WHERE incident_uid=?", (now_iso(), uid))
        participant_ids = {row["participant_id"] for row in rows}
        for participant_id in participant_ids:
            conn.execute("UPDATE study_subjects SET valid_days=?,updated_at_utc=? WHERE participant_id=?", (valid_days.get(participant_id, 0), now_iso(), participant_id))
    return {
        "rows": rows,
        "summary": {
            "total": len(rows),
            "ok": sum(row["status"] == "ok" for row in rows),
            "missing": sum(row["status"] == "missing" for row in rows),
            "pending": sum(row["status"] == "pending" for row in rows),
        },
        "operator": operator,
    }
