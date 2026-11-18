"""CSV தரவு இறக்கம் — Admin பக்கம் (app.py) மற்றும் import_data.py (command line) இரண்டும் இதையே பயன்படுத்தும்.

ஒவ்வொரு function-ம் (csv உள்ளடக்கம் bytes) -> {"done": எண்ணிக்கை, "skipped": [செய்திகள்]} திருப்பும்.
எல்லாமே upsert; மீண்டும் இயக்கினால் புதுப்பிக்கும், நகல் உருவாகாது.
"""
import csv
import datetime as dt
import io
import os
import re
from decimal import Decimal

from psycopg2.extras import execute_values

import db
from common import make_unique_key, to_num, bulk_upsert_claims

BASE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- பொது உதவிகள்
def decode_bytes(raw):
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError("கோப்பைப் படிக்க முடியவில்லை — CSV (UTF-8) ஆகச் சேமித்து மீண்டும் முயலவும்")


def read_rows(raw, skip=1):
    text = decode_bytes(raw)
    if not text.strip():
        raise ValueError("கோப்பு காலியாக உள்ளது")
    return list(csv.reader(io.StringIO(text, newline="")))[skip:]


def cell(row, i):
    return row[i].strip() if i < len(row) and row[i] is not None else ""


DATE_FORMATS = ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y",
                "%d %b %Y", "%d %B %Y", "%a %b %d %Y", "%m/%d/%Y"]


def parse_date(s):
    s = s.strip()
    for cand in (s, s.split(" ")[0] if s else s):
        for fmt in DATE_FORMATS:
            try:
                return dt.datetime.strptime(cand, fmt).date()
            except ValueError:
                pass
    return None


def library_ids():
    return {r["name"]: r["id"] for r in db.fetch_all("SELECT id, name FROM libraries")}


def run_batch(sql, rows):
    def f(conn):
        with conn.cursor() as cur:
            execute_values(cur, sql, rows, page_size=500)
    if rows:
        db.run(f)


def result(done, skipped):
    return {"done": done, "skipped": skipped}


# ---------------------------------------------------------------- இறக்கங்கள்
def do_schema(_raw=None):
    sql = open(os.path.join(BASE, "schema.sql"), encoding="utf-8").read()

    def f(conn):
        with conn.cursor() as cur:
            cur.execute(sql)
    db.run(f)
    return result(1, [])


def do_libraries(raw):
    rows, skipped, seen_codes, items = read_rows(raw), [], set(), []
    for r in rows:
        name = cell(r, 0)
        if not name:
            continue
        code = cell(r, 1).upper() or None
        if code in seen_codes:
            skipped.append(f"{name}: குறியீடு {code} மீண்டும் வருகிறது — குறியீடு இல்லாமல் சேர்க்கப்பட்டது")
            code = None
        if code:
            seen_codes.add(code)
        items.append((name, code, cell(r, 2).lower(), cell(r, 3)))
    run_batch("INSERT INTO libraries (name, code, emails, lib_type) VALUES %s "
              "ON CONFLICT (name) DO UPDATE SET code = EXCLUDED.code, emails = EXCLUDED.emails, "
              "lib_type = EXCLUDED.lib_type", items)
    return result(len(items), skipped)


def do_newspapers(raw):
    items = {}
    for r in read_rows(raw):
        try:
            slot = int(cell(r, 0))
        except ValueError:
            continue
        if 1 <= slot <= 18:
            items[slot] = (slot, cell(r, 1), to_num(cell(r, 2)))
    run_batch("INSERT INTO newspapers (slot, name, max_rate) VALUES %s ON CONFLICT (slot) "
              "DO UPDATE SET name = EXCLUDED.name, max_rate = EXCLUDED.max_rate", list(items.values()))
    return result(len(items), [])


def do_map(raw):
    ids, items, skipped = library_ids(), [], []
    for r in read_rows(raw):
        name = cell(r, 0)
        if not name:
            continue
        if name not in ids:
            skipped.append(f"{name}: நூலகப் பட்டியலில் இல்லை")
            continue
        for slot in range(1, 19):
            items.append((ids[name], slot, cell(r, slot).lower() == "yes"))
    run_batch("INSERT INTO library_newspaper_map (library_id, slot, active) VALUES %s "
              "ON CONFLICT (library_id, slot) DO UPDATE SET active = EXCLUDED.active", items)
    return result(len(items) // 18, skipped)


def do_holidays(raw):
    items, skipped = set(), []
    for r in read_rows(raw):
        v = cell(r, 0)
        if not v:
            continue
        d = parse_date(v)
        (items.add((d,)) if d else skipped.append(f"தேதி புரியவில்லை: {v}"))
    run_batch("INSERT INTO holidays (holiday_date) VALUES %s ON CONFLICT DO NOTHING", sorted(items))
    return result(len(items), skipped)


def do_leave(raw):
    ids, items, skipped = library_ids(), [], []
    for r in read_rows(raw, skip=2):
        name = cell(r, 0)
        if not name:
            continue
        if name not in ids:
            skipped.append(f"{name}: நூலகப் பட்டியலில் இல்லை")
            continue
        v = cell(r, 1)
        # எண்ணாக இல்லாவிட்டால் (எ.கா. sheet தேதியாக மாற்றியிருந்தால்) 0
        leave = to_num(v) if re.fullmatch(r"\d+(\.\d+)?", v) else Decimal(0)
        items.append((ids[name], leave))
    run_batch("INSERT INTO staff_leave_days (library_id, leave_days) VALUES %s ON CONFLICT (library_id) "
              "DO UPDATE SET leave_days = EXCLUDED.leave_days", items)
    return result(len(items), skipped)


def do_rent(raw):
    ids, items, skipped = library_ids(), [], []
    for r in read_rows(raw):
        name = cell(r, 0)
        if not name:
            continue
        if name not in ids:
            skipped.append(f"{name}: நூலகப் பட்டியலில் இல்லை")
            continue
        items.append((ids[name], cell(r, 1), to_num(cell(r, 2))))
    run_batch("INSERT INTO rent (library_id, owner_name, amount) VALUES %s ON CONFLICT (library_id) "
              "DO UPDATE SET owner_name = EXCLUDED.owner_name, amount = EXCLUDED.amount", items)
    return result(len(items), skipped)


def do_dailywage(raw):
    ids, items, skipped = library_ids(), [], []
    for r in read_rows(raw):
        name = cell(r, 0)
        if not name:
            continue
        if name not in ids:
            skipped.append(f"{name}: நூலகப் பட்டியலில் இல்லை")
            continue
        items.append((ids[name],))
    run_batch("INSERT INTO daily_wage_permission (library_id) VALUES %s ON CONFLICT DO NOTHING", items)
    return result(len(items), skipped)


def norm_month_year(s):
    m = re.fullmatch(r"(\d{1,2})[/-](\d{4})", s.strip())
    if m:
        return f"{int(m.group(1)):02d}/{m.group(2)}"
    d = parse_date(s)  # sheet தானாகத் தேதியாக மாற்றியிருந்தால்
    return f"{d.month:02d}/{d.year}" if d else None


# பழைய contingent sheet-ன் column நிலைகள் (0 தொடங்கி)
C_EMAIL, C_TYPE, C_LIB = 1, 2, 3
C_PAPERS_1_13 = 4          # 4..16
C_TEXT = {"partnerNameTamil": 17, "staffNameTamil": 19, "partTimeStaffName": 21, "rentOwnerName": 24,
          "busPurpose": 29, "busDate": 30, "travelPurpose": 32, "travelDate": 33,
          "registerItem": 36, "stationeryItem": 38, "electricalItem": 40, "consumerItem": 42,
          "otherExpense1Desc": 44, "otherExpense2Desc": 46}
C_NUM = {"partnerDays": 18, "staffDays": 20, "partTimeStaffDays": 22, "electricityBill": 23,
         "rentAmount": 25, "phoneWifiAmount": 26, "waterTax": 27, "photocopyAmount": 28,
         "busAmount": 31, "travelAmount": 34, "postalAmount": 35, "registerAmount": 37,
         "stationeryAmount": 39, "electricalAmount": 41, "consumerAmount": 43,
         "otherExpense1Amount": 45, "otherExpense2Amount": 47}
C_MONTH, C_UKEY, C_PAPERS_14_18, C_PDF = 48, 49, 50, 56

CLAIM_CHUNK = 300


def do_claims(raw):
    codes = {r["name"]: r["code"] for r in db.fetch_all("SELECT name, code FROM libraries")}
    skipped, entries = [], []
    for n, r in enumerate(read_rows(raw), start=2):
        lib = cell(r, C_LIB)
        if not lib:
            continue
        my = norm_month_year(cell(r, C_MONTH))
        if not my:
            skipped.append(f"வரி {n} ({lib}): மாதம்/ஆண்டு புரியவில்லை '{cell(r, C_MONTH)}'")
            continue
        code = (codes.get(lib) or "").upper()
        ukey_old = cell(r, C_UKEY).upper()
        suffix = my.replace("/", "")
        if not code and ukey_old.endswith(suffix) and len(ukey_old) > len(suffix):
            code = ukey_old[:-len(suffix)]            # பழைய key-ல் இருந்து குறியீடு
        if not code:
            skipped.append(f"வரி {n} ({lib}): நூலக குறியீடு கிடைக்கவில்லை")
            continue

        data = {k: cell(r, i) for k, i in {**C_TEXT, **C_NUM}.items()}
        papers = {slot: to_num(cell(r, C_PAPERS_1_13 + slot - 1)) for slot in range(1, 14)}
        papers.update({slot: to_num(cell(r, C_PAPERS_14_18 + slot - 14)) for slot in range(14, 19)})
        head = {"unique_key": make_unique_key(code, my), "library_name": lib, "library_code": code,
                "month_year": my, "login_email": cell(r, C_EMAIL), "library_type": cell(r, C_TYPE)}
        entries.append((head, data, papers, cell(r, C_PDF) or None))

    inserted = updated = 0
    for i in range(0, len(entries), CLAIM_CHUNK):
        chunk = entries[i:i + CLAIM_CHUNK]

        def f(conn, chunk=chunk):
            with conn.cursor() as cur:
                return bulk_upsert_claims(cur, chunk)
        a, b = db.run(f)
        inserted += a
        updated += b
    return result(inserted + updated, skipped) | {"inserted": inserted, "updated": updated}


# வகை -> (function, விளக்கம்)
IMPORTERS = {
    "libraries":  (do_libraries,  "நூலகப் பட்டியல் (Sheet1)"),
    "newspapers": (do_newspapers, "நாளிதழ் பட்டியல் (NEWSPAPER LIST)"),
    "map":        (do_map,        "நூலகம்-நாளிதழ் இணைப்பு (LIBRARY NEWSPAPER MAP)"),
    "holidays":   (do_holidays,   "அரசு விடுமுறை (HOLIDAYS)"),
    "leave":      (do_leave,      "பணியாளர் விடுப்பு (staff leave days)"),
    "rent":       (do_rent,       "வாடகை (RENT)"),
    "dailywage":  (do_dailywage,  "தினக்கூலி அனுமதி (DAILYWAGES PERMISSION)"),
    "claims":     (do_claims,     "பழைய contingent பதிவுகள் (contingent)"),
}
