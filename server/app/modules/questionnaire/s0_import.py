from __future__ import annotations

import csv
import hashlib
import json
import re
import secrets
import zipfile
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
from xml.etree import ElementTree as ET

from app.core.identity_db import identity_db


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _rows_from_xlsx(raw: bytes) -> list[list[str]]:
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows: list[list[str]] = []
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        shared = _xlsx_shared_strings(zf)
        sheets = sorted(
            name for name in zf.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
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


def _rows_from_csv(raw: bytes) -> list[list[str]]:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "utf-16", "big5"):
        try:
            text = raw.decode(encoding)
            try:
                dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t;|")
            except csv.Error:
                dialect = csv.excel
            return [[str(value).strip() for value in row] for row in csv.reader(StringIO(text), dialect)]
        except (UnicodeError, csv.Error) as exc:
            last_error = exc
    raise ValueError(f"CSV 编码或格式无法读取：{last_error}")


def parse_table(filename: str, raw: bytes) -> list[dict[str, str]]:
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".xls":
        raise ValueError("暂不支持旧 .xls；请从问卷星导出 .xlsx 或另存为 .csv")
    if suffix not in {".xlsx", ".csv"}:
        raise ValueError("S0 只支持问卷星 .xlsx 或 .csv 文件")
    if not raw:
        raise ValueError("上传文件为空")
    rows = _rows_from_xlsx(raw) if suffix == ".xlsx" else _rows_from_csv(raw)
    header_index = next((i for i, row in enumerate(rows) if any(str(value).strip() for value in row)), -1)
    if header_index < 0:
        return []
    headers = [str(value or "").strip() for value in rows[header_index]]
    objects: list[dict[str, str]] = []
    for row in rows[header_index + 1:]:
        if not any(str(value).strip() for value in row):
            continue
        objects.append({header: str(row[i] if i < len(row) else "").strip() for i, header in enumerate(headers) if header})
    return objects


def _clean_header(value: str) -> str:
    return re.sub(r"[\s：:？?]", "", str(value or "")).strip()


def _pick_exact(row: dict[str, str], keys: list[str]) -> str:
    values = {_clean_header(header): str(value or "").strip() for header, value in row.items()}
    for key in keys:
        if _clean_header(key) in values:
            return values[_clean_header(key)]
    return ""


def _pick(row: dict[str, str], keys: list[str]) -> str:
    for key in keys:
        clean_key = _clean_header(key)
        for header, value in row.items():
            if clean_key in _clean_header(header):
                return str(value or "").strip()
    return ""


def _preferred(row: dict[str, str], exact: list[str], fallback: list[str] | None = None) -> str:
    return _pick_exact(row, exact) or _pick(row, exact) or _pick(row, fallback or [])


def _normalize_contact(value: str) -> str:
    return re.sub(r"[\s\-()（）]", "", str(value or "").strip()).removesuffix(".0")


def _normalize_seq(value: str) -> str:
    return str(value or "").strip().removesuffix(".0")


def _stable_keys(source_seq: str, phone: str, wechat: str) -> list[str]:
    keys: list[str] = []
    if source_seq := _normalize_seq(source_seq):
        keys.append(f"seq:{source_seq}")
    if phone := _normalize_contact(phone):
        keys.append(f"phone:{phone}")
    if wechat := _normalize_contact(wechat).lower():
        keys.append(f"wechat:{wechat}")
    return keys


def _willing(row: dict[str, str]) -> bool:
    hit = None
    for header, value in row.items():
        key = _clean_header(header)
        if "参与" in key and (("研究" in key and re.search(r"愿意|同意|接受|补贴", key)) or "补贴" in key):
            hit = str(value or "").strip()
            break
    if hit is None:
        return True
    if not hit or re.search(r"不愿|不同意|拒绝|否|不接受", hit):
        return False
    return bool(re.search(r"愿意|同意|接受|是|可以|可", hit))


def _infer_light_type(row: dict[str, str]) -> str:
    fixed = _preferred(
        row,
        ["典型工作/学习日中，您的工作/学习时间有多少比例在同一个固定工位、座位或学习位完成", "工作/学习时间在同一固定位置完成的比例", "固定位置占比"],
        ["固定工位", "固定位置", "同一个固定"],
    )
    screen = _preferred(
        row,
        ["典型工作/学习日中，您的屏幕工作/学习时间占总工作/学习时间的比例大约为", "屏幕工作/学习时间占总工作/学习时间的比例", "屏幕时间占比"],
        ["屏幕"],
    )
    if "<50" in fixed or "小于50" in fixed:
        return "混合移动室内型"
    if any(value in fixed for value in ("70", "75", "大于75")) and any(value in screen for value in ("70", "75", "大于75")):
        return "固定高屏幕工位型"
    return "固定综合办公型" if fixed else ""


def _mapped(row: dict[str, str]) -> dict[str, str]:
    return {
        "source_seq": _normalize_seq(_pick_exact(row, ["序号"]) or _pick(row, ["序号"])),
        "name": _preferred(row, ["姓名或称呼", "姓名", "称呼"]),
        "phone": _preferred(row, ["您的手机号（仅实验负责人可见）", "手机号", "联系电话"], ["手机", "电话"]),
        "wechat": _preferred(row, ["微信号", "微信"]),
        "sex": _preferred(row, ["您的性别", "性别"]),
        "age_group": _preferred(row, ["您的年龄", "年龄"]),
        "identity_type": _preferred(row, ["您目前最接近哪类身份", "当前主要身份", "主要身份", "身份"]),
        "light_type": _infer_light_type(row),
        "work_district": _preferred(row, ["您的主要工作/学习地点大致位于北京市哪个区", "主要工作/学习区", "工作/学习区"], ["工作/学习地点"]),
        "home_district": _preferred(row, ["您的主要居住地点大致位于北京市哪个区", "主要居住区", "居住区"], ["居住地点"]),
        "phone_os": _preferred(row, ["您的手机系统是", "手机系统"]),
        "pickup_method": _preferred(row, ["您更方便的设备领取和归还方式", "设备领取和归还方式", "领取和归还方式"], ["领取", "归还"]),
        "availability": _preferred(row, ["您方便参与实验的时间段是", "方便参与实验的时间段", "可参与时间"], ["参与实验的时间"]),
    }


def _validate_s0(rows: list[dict[str, str]]) -> None:
    if not rows:
        raise ValueError("文件中没有可读取的数据行")
    headers = {_clean_header(header) for header in rows[0]}
    signals = sum(any(token in header for header in headers) for token in ("序号", "年龄", "性别", "参与", "手机号"))
    if signals < 2:
        raise ValueError("文件不像 S0 招募问卷累计表，请检查是否选择了正确文件")


def import_s0(filename: str, raw: bytes, operator: str) -> dict:
    rows = parse_table(filename, raw)
    _validate_s0(rows)
    digest = hashlib.sha256(raw).hexdigest()
    with identity_db() as conn:
        existing_import = conn.execute("SELECT * FROM s0_imports WHERE sha256=?", (digest,)).fetchone()
        if existing_import:
            return {
                "import_uid": existing_import["import_uid"],
                "total": existing_import["row_count"],
                "imported": existing_import["imported_count"],
                "filtered": existing_import["filtered_count"],
                "duplicate": True,
            }

        import_uid = f"s0_{secrets.token_hex(8)}"
        now = now_iso()
        old_rows = [dict(row) for row in conn.execute("SELECT * FROM candidates ORDER BY linked_participant_id<>'' DESC, updated_at_utc DESC")]
        old_by_key: dict[str, dict] = {}
        for old in old_rows:
            for key in _stable_keys(old.get("source_seq", ""), old.get("phone", ""), old.get("wechat", "")):
                old_by_key.setdefault(key, old)

        seen: set[str] = set()
        imported = 0
        filtered = 0
        for row in rows:
            if not _willing(row):
                filtered += 1
                continue
            mapped = _mapped(row)
            old = next((old_by_key[key] for key in _stable_keys(mapped["source_seq"], mapped["phone"], mapped["wechat"]) if key in old_by_key), {})
            candidate_uid = old.get("candidate_uid") or f"cand_{secrets.token_hex(8)}"
            seen.add(candidate_uid)
            # Preserve fields which may have been corrected manually after import.
            for field in ("name", "phone", "wechat", "light_type"):
                mapped[field] = old.get(field) or mapped[field]
            values = {
                **mapped,
                "source": old.get("source") or "问卷星",
                "linked_participant_id": old.get("linked_participant_id"),
                "notes": old.get("notes") or "",
                "in_latest_snapshot": 1,
                "s0_import_uid": import_uid,
                "s0_raw_json": json.dumps(row, ensure_ascii=False, separators=(",", ":")),
                "created_at_utc": old.get("created_at_utc") or now,
                "updated_at_utc": now,
            }
            fields = [
                "linked_participant_id", "name", "phone", "wechat", "source", "sex", "age_group",
                "identity_type", "light_type", "work_district", "home_district", "phone_os",
                "pickup_method", "availability", "notes", "source_seq", "in_latest_snapshot",
                "s0_import_uid", "s0_raw_json", "created_at_utc", "updated_at_utc",
            ]
            conn.execute(
                f"INSERT INTO candidates(candidate_uid,{','.join(fields)}) VALUES(?{',?' * len(fields)}) "
                f"ON CONFLICT(candidate_uid) DO UPDATE SET {','.join(f'{field}=excluded.{field}' for field in fields if field != 'created_at_utc')}",
                [candidate_uid, *(values[field] for field in fields)],
            )
            imported += 1

        if seen:
            placeholders = ",".join("?" for _ in seen)
            conn.execute(
                f"UPDATE candidates SET in_latest_snapshot=0,updated_at_utc=? WHERE (source='问卷星' OR s0_import_uid<>'') AND candidate_uid NOT IN ({placeholders})",
                [now, *seen],
            )
        else:
            conn.execute("UPDATE candidates SET in_latest_snapshot=0,updated_at_utc=? WHERE source='问卷星' OR s0_import_uid<>''", (now,))
        conn.execute(
            "INSERT INTO s0_imports(import_uid,filename,sha256,file_bytes,row_count,imported_count,filtered_count,imported_at_utc,imported_by) VALUES(?,?,?,?,?,?,?,?,?)",
            (import_uid, Path(filename).name, digest, raw, len(rows), imported, filtered, now, operator),
        )
    return {"import_uid": import_uid, "total": len(rows), "imported": imported, "filtered": filtered, "duplicate": False}

