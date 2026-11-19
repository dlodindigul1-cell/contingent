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


def _xl_val(v):
    """Excel கலத்தை உரையாக மாற்றும் (5.0 -> '5', தேதி -> dd/mm/yyyy)."""
    if v is None:
        return ""
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def read_rows(raw, skip=1):
    """Excel (.xlsx) அல்லது CSV இரண்டையும் படிக்கும் — கோப்பின் உள்ளடக்கத்தைக் கொண்டே கண்டறியும்."""
    if raw[:2] == b"PK":                      # .xlsx என்பது zip கோப்பு
        from openpyxl import load_workbook
        try:
            wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        except Exception:
            raise ValueError("Excel கோப்பைப் படிக்க முடியவில்லை — .xlsx வடிவில் சேமித்து மீண்டும் முயலவும்")
        ws = wb.active
        rows = [[_xl_val(c) for c in r] for r in ws.iter_rows(values_only=True)]
        if not any(any(c for c in r) for r in rows):
            raise ValueError("கோப்பு காலியாக உள்ளது")
        return rows[skip:]
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
    """A பெயர், B குறியீடு, C மின்னஞ்சல், D வகை, E ஊதிய மையம் (விருப்பம்; காலியெனில் பழைய மதிப்பு மாறாது)."""
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
        items.append((name, code, cell(r, 2).lower(), cell(r, 3), cell(r, 4)))
    run_batch("INSERT INTO libraries (name, code, emails, lib_type, pay_center) VALUES %s "
              "ON CONFLICT (name) DO UPDATE SET code = EXCLUDED.code, emails = EXCLUDED.emails, "
              "lib_type = EXCLUDED.lib_type, "
              "pay_center = CASE WHEN EXCLUDED.pay_center <> '' THEN EXCLUDED.pay_center ELSE libraries.pay_center END",
              items)
    # புதிய வகை / ஊதிய மையங்களை Admin பட்டியலிலும் சேர்க்கும்
    run_batch("INSERT INTO library_types (name) VALUES %s ON CONFLICT (name) DO NOTHING",
              [(t,) for t in dict.fromkeys(i[3] for i in items if i[3])])
    run_batch("INSERT INTO pay_centers (name) VALUES %s ON CONFLICT (name) DO NOTHING",
              [(t,) for t in dict.fromkeys(i[4] for i in items if i[4])])
    return result(len(items), skipped)


def do_newspapers(raw):
    """நெடுவரிசை: A இடம், B பெயர், C திங்கள்–சனி விலை, D ஞாயிறு விலை.
    D காலியாக இருந்தால் C பழைய 'மாத அதிகபட்சம்' ஆகக் கருதப்படும் (பழைய Sheet இணக்கம்)."""
    items = {}
    for r in read_rows(raw):
        try:
            slot = int(cell(r, 0))
        except ValueError:
            continue
        if 1 <= slot <= 18:
            if cell(r, 3) != "":
                items[slot] = (slot, cell(r, 1), Decimal(0), to_num(cell(r, 2)), to_num(cell(r, 3)))
            else:
                items[slot] = (slot, cell(r, 1), to_num(cell(r, 2)), Decimal(0), Decimal(0))
    run_batch("INSERT INTO newspapers (slot, name, max_rate, weekday_rate, sunday_rate) VALUES %s "
              "ON CONFLICT (slot) DO UPDATE SET name = EXCLUDED.name, max_rate = EXCLUDED.max_rate, "
              "weekday_rate = EXCLUDED.weekday_rate, sunday_rate = EXCLUDED.sunday_rate", list(items.values()))
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
    """A தேதி, B விடுமுறை பெயர் (விருப்பம்). முதல் வரி தலைப்பு."""
    items, skipped = {}, []
    for r in read_rows(raw):
        v = cell(r, 0)
        if not v:
            continue
        d = parse_date(v)
        if d:
            items[d] = cell(r, 1) or items.get(d, "")
        else:
            skipped.append(f"தேதி புரியவில்லை: {v}")
    run_batch("INSERT INTO holidays (holiday_date, name) VALUES %s ON CONFLICT (holiday_date) DO UPDATE "
              "SET name = CASE WHEN EXCLUDED.name <> '' THEN EXCLUDED.name ELSE holidays.name END",
              sorted(items.items()))
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
        try:
            w = min(max(int(float(cell(r, 1) or 1)), 1), 20)   # B நெடுவரிசை (விருப்பம்): பணியாளர் எண்ணிக்கை
        except ValueError:
            w = 1
        items.append((ids[name], w))
    run_batch("INSERT INTO daily_wage_permission (library_id, workers) VALUES %s "
              "ON CONFLICT (library_id) DO UPDATE SET workers = EXCLUDED.workers", items)
    # மாவட்ட மைய / முழு நேர நூலகங்கள் தவிர மற்றவற்றுக்கு அதிகபட்சம் 1
    db.execute("UPDATE daily_wage_permission d SET workers = 1 FROM libraries l WHERE l.id = d.library_id "
               "AND d.workers > 1 AND NOT (l.lib_type LIKE '%மாவட்ட மைய%' OR l.lib_type LIKE '%முழு நேர%')")
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


# ---------------------------------------------------------------- ஏற்கனவே அனுமதித்த மாதங்கள் (Excel)
# முதல் நெடுவரிசை = மாதம். ஒரு மாதம் அல்லது ஒரு ஆண்டு முழுவதும் (எல்லா நூலகங்களும்) ஒரே கோப்பில் ஏற்றலாம்.
# தலைப்பின் பெயரைக் கொண்டே நெடுவரிசை அடையாளம் காணப்படும் (வரிசை மாறினாலும் சரி).
HIST_COLS = [
    ("மாதம் (MM/YYYY)", "_month"), ("நூலகத்தின் பெயர்", "_lib"),
    ("கூட்டுநர் பெயர்", "partnerNameTamil"), ("கூட்டுநர் பணிநாட்கள்", "partnerDays"),
    ("நூலகப் பணியாளர் பெயர்", "staffNameTamil"), ("நூலகப் பணியாளர் பணிநாட்கள்", "staffDays"),
    ("பகுதி நேரப் பணியாளர் பெயர்", "partTimeStaffName"), ("பகுதி நேரப் பணிநாட்கள்", "partTimeStaffDays"),
    ("மின் கட்டணம்", "electricityBill"), ("கட்டட உரிமையாளர்", "rentOwnerName"), ("வாடகை", "rentAmount"),
    ("வைஃபை / தொலைபேசி", "phoneWifiAmount"), ("குடிநீர் வரி", "waterTax"), ("நகல் செலவினம்", "photocopyAmount"),
    ("பேருந்து காரணம்", "busPurpose"), ("பேருந்து நாள்", "busDate"), ("பேருந்து கட்டணம்", "busAmount"),
    ("போக்குவரத்து காரணம்", "travelPurpose"), ("போக்குவரத்து நாள்", "travelDate"), ("போக்குவரத்துக் கட்டணம்", "travelAmount"),
    ("அஞ்சல்", "postalAmount"),
    ("பதிவேடு விவரம்", "registerItem"), ("பதிவேடு தொகை", "registerAmount"),
    ("எழுதுபொருள் விவரம்", "stationeryItem"), ("எழுதுபொருள் தொகை", "stationeryAmount"),
    ("மின்சாதனம் விவரம்", "electricalItem"), ("மின்சாதனம் தொகை", "electricalAmount"),
    ("நுகர்பொருள் விவரம்", "consumerItem"), ("நுகர்பொருள் தொகை", "consumerAmount"),
    ("பிற செலவினம் 1 விவரம்", "otherExpense1Desc"), ("பிற செலவினம் 1 தொகை", "otherExpense1Amount"),
    ("பிற செலவினம் 2 விவரம்", "otherExpense2Desc"), ("பிற செலவினம் 2 தொகை", "otherExpense2Amount"),
    ("கழிவறை சுத்தம்", "toiletCleaningAmount"),
    ("தினக்கூலி காவலர்", "dailyGuardAmount"),
    ("அனுமதித் தொகை (மொத்தம்)", "_approved"),
]
HIST_PAPER_RE = re.compile(r"^\s*நாளிதழ்\s*(\d+)")
HIST_FIRST_YEAR_START = (2025, 4)      # ஏப்ரல் 2025 (நிதியாண்டு 2025-26 தொடக்கம்)
HIST_LAST = (2027, 3)                  # மார்ச் 2027 (நிதியாண்டு 2026-27 முடிவு)
_MONEY_KEYS = ["electricityBill", "rentAmount", "phoneWifiAmount", "waterTax", "photocopyAmount",
               "busAmount", "travelAmount", "postalAmount", "registerAmount", "stationeryAmount",
               "electricalAmount", "consumerAmount", "otherExpense1Amount", "otherExpense2Amount", "dailyGuardAmount",
               "toiletCleaningAmount"]


def hist_month(s):
    """'04/2025', '4-2025', '2025-04', தேதி -> 'MM/YYYY' """
    s = (s or "").strip()
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})", s)
    if m:
        return f"{int(m.group(2)):02d}/{m.group(1)}"
    return norm_month_year(s)


def _hist_date(s):
    s = (s or "").strip()
    if not s:
        return ""
    d = parse_date(s)
    return d.isoformat() if d else s


def do_claims_approved(raw):
    """ஏற்கனவே அனுமதித்த மாதங்களை ஏற்றும். இவை 'அனுமதிக்கப்பட்டவை' என்றே சேமிக்கப்படும்;
    அனுமதித் தொகை Excel-ல் உள்ளபடியே நிலையாகச் சேமிக்கப்படும் (பின்னர் விகிதம் மாறினாலும் மாறாது).
    Excel-ல் அனுமதித் தொகை காலியாக இருந்தால் மட்டும், ஏற்றும் நேரத்தில் கணக்கிட்டு நிலையாக்கும்.
    ஏற்கனவே அனுமதிக்கப்பட்ட நூலகம்-மாதம் இருந்தால் அதை மாற்றாமல் தவிர்க்கும்."""
    rows = read_rows(raw, skip=0)
    if not rows:
        raise ValueError("கோப்பு காலியாக உள்ளது")
    hdr = rows[0]
    col, paper_cols = {}, {}
    for i, h in enumerate(hdr):
        h = (h or "").strip()
        for title, key in HIST_COLS:
            if h == title:
                col[key] = i
        pm = HIST_PAPER_RE.match(h)
        if pm and 1 <= int(pm.group(1)) <= 18:
            paper_cols[int(pm.group(1))] = i
    newspaper_total_col = next((i for i, h in enumerate(hdr) if (h or "").strip() == "நாளிதழ்கள்"), None)
    for need in ("_month", "_lib"):
        if need not in col:
            raise ValueError("தலைப்பு வரி சரியில்லை — மாதிரி Excel-ஐப் பதிவிறக்கி, அதன் தலைப்புகளை மாற்றாமல் பயன்படுத்தவும் "
                             "(முதல் நெடுவரிசை 'மாதம் (MM/YYYY)', இரண்டாவது 'நூலகத்தின் பெயர்').")

    libs = {r["name"]: r for r in db.fetch_all("SELECT name, code, lib_type FROM libraries")}
    rates = {r["kind"]: to_num(r["rate"]) for r in db.fetch_all("SELECT kind, rate FROM wage_rates")}
    skipped, entries, meta = [], [], {}
    for n, r in enumerate(rows[1:], start=2):
        lib = cell(r, col["_lib"])
        if not lib:
            continue
        my = hist_month(cell(r, col["_month"]))
        if not my:
            skipped.append(f"வரி {n} ({lib}): மாதம் புரியவில்லை '{cell(r, col['_month'])}'")
            continue
        mm, yy = int(my[:2]), int(my[3:])
        if not (HIST_FIRST_YEAR_START <= (yy, mm) <= HIST_LAST):
            skipped.append(f"வரி {n} ({lib}): மாதம் {my} — ஏப்ரல் 2025 முதல் மார்ச் 2027 வரை மட்டுமே ஏற்றலாம்")
            continue
        L = libs.get(lib)
        code = ((L or {}).get("code") or "").strip().upper()
        if not L or not code:
            skipped.append(f"வரி {n}: நூலகம் '{lib}' பட்டியலில் இல்லை / குறியீடு இல்லை")
            continue
        data = {}
        for _, key in HIST_COLS:
            if key.startswith("_") or key not in col:
                continue
            v = cell(r, col[key])
            data[key] = _hist_date(v) if key in ("busDate", "travelDate") else v
        papers = {s: to_num(cell(r, i)) for s, i in paper_cols.items()}
        if newspaper_total_col is not None:
            # ஒரே மொத்தத் தொகையை slot 1-ல் சேமிக்கிறோம்; மொத்தக் கணக்கில் முழுத் தொகையும் சேரும்.
            papers = {1: to_num(cell(r, newspaper_total_col))}
        toilet = to_num(data.get("toiletCleaningAmount"))
        approved_txt = cell(r, col["_approved"]) if "_approved" in col else ""
        if approved_txt != "":
            approved = to_num(approved_txt)
        else:
            approved = (sum(papers.values(), Decimal(0))
                        + to_num(data.get("partnerDays")) * rates.get("partner", Decimal(0))
                        + to_num(data.get("staffDays")) * rates.get("staff", Decimal(0))
                        + to_num(data.get("partTimeStaffDays")) * rates.get("part_time", Decimal(0))
                        + sum((to_num(data.get(k)) for k in _MONEY_KEYS), Decimal(0)))
        head = {"unique_key": make_unique_key(code, my), "library_name": lib, "library_code": code,
                "month_year": my, "login_email": "", "library_type": L.get("lib_type") or ""}
        entries.append((head, data, papers, None))
        meta[head["unique_key"]] = (toilet, approved)

    if not entries:
        return result(0, skipped) | {"inserted": 0, "updated": 0}

    # ஏற்கனவே அனுமதிக்கப்பட்டவற்றைத் தொடாமல் தவிர்
    keys = list(meta.keys())
    locked = {r["unique_key"] for r in db.fetch_all(
        "SELECT unique_key FROM claims WHERE approved AND unique_key = ANY(%s)", (keys,))}
    todo = []
    for e in entries:
        if e[0]["unique_key"] in locked:
            skipped.append(f"{e[0]['library_name']} · {e[0]['month_year']}: ஏற்கனவே அனுமதிக்கப்பட்டது — மாற்றப்படவில்லை "
                           "(மாற்ற வேண்டுமெனில் முதலில் அனுமதியை ரத்து செய்யவும்)")
        else:
            todo.append(e)

    inserted = updated = 0
    for i in range(0, len(todo), CLAIM_CHUNK):
        chunk = todo[i:i + CLAIM_CHUNK]

        def f(conn, chunk=chunk):
            with conn.cursor() as cur:
                a, b = bulk_upsert_claims(cur, chunk)
                vals = [(c[0]["unique_key"], meta[c[0]["unique_key"]][0], meta[c[0]["unique_key"]][1]) for c in chunk]
                execute_values(cur,
                    "UPDATE claims c SET toilet_cleaning_amount = v.t, approved = TRUE, approved_at = now(), "
                    "approved_amount = v.a FROM (VALUES %s) AS v(k, t, a) WHERE c.unique_key = v.k",
                    vals, template="(%s, %s::numeric, %s::numeric)")
                return a, b
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
    "claims_approved": (do_claims_approved, "ஏற்கனவே அனுமதித்த சில்லறை செலவின மாதங்கள்"),
}
