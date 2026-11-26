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


def do_leave(raw, month=None):
    """A நூலகப் பெயர், B விடுப்பு நாட்கள் — தேர்ந்தெடுத்த மாதத்திற்கு (முந்தைய மாதம் 16 -> அந்த மாதம் 15)."""
    if not re.fullmatch(r"(0[1-9]|1[0-2])/\d{4}", str(month or "")):
        raise ValueError("முதலில் மாதத்தைத் தேர்ந்தெடுக்கவும் (MM/YYYY).")
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
        items.append((ids[name], month, leave))
    run_batch("INSERT INTO staff_leave_monthly (library_id, month_year, leave_days) VALUES %s ON CONFLICT (library_id, month_year) "
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
# புதிய (தொகை மட்டும்) வடிவம் — மாதிரி Excel இதையே பயன்படுத்தும். வரிசை = Excel நெடுவரிசை வரிசை.
# '_' தொடங்கும் விசைகள் சேமிக்கப்படாது (மொத்தங்கள் அமைப்பே கணக்கிடும்).
HIST_NEW_COLS = [
    ("மாதம்", "_month"), ("ஊதிய மையம்", "_paycenter"), ("நூலகத்தின் பெயர்", "_lib"), ("நாளிதழ்கள்", "_papers"),
    ("வாடகை", "rentAmount"), ("மின் கட்டணம்", "electricityBill"),
    ("தினக்கூலி பகுதிநேரத் துப்புரவாளர்", "partnerAmount"), ("தினக்கூலி நூலகர்", "staffAmount"),
    ("தினக்கூலிக் காவலர்", "dailyGuardAmount"), ("தினக்கூலி பகுதிநேர நூலகர்", "partTimeAmount"),
    ("பேருந்து கட்டணம்", "busAmount"), ("எழுது பொருட்கள் வாங்குதல்", "stationeryAmount"),
    ("நுகர்பொருள் வாங்குதல்", "consumerAmount"), ("மின்சாதனங்கள் வாங்குதல்", "electricalAmount"),
    ("அஞ்சல் செலவினம்", "postalAmount"), ("நகலெடுக்கும் கட்டணம்", "photocopyAmount"),
    ("பிற செலவினங்கள் 1 தலைப்பு", "otherExpense1Desc"), ("பிற செலவினங்கள் 1", "otherExpense1Amount"),
    ("பிற செலவினங்கள் 2 தலைப்பு", "otherExpense2Desc"), ("பிற செலவினங்கள் 2", "otherExpense2Amount"),
    ("மொத்தம்", "_sub"),
    ("போக்குவரத்து கட்டணம்", "travelAmount"), ("குடிநீர் கட்டணம்", "waterTax"),
    ("தொலைபேசி கட்டணம்", "phoneWifiAmount"), ("கழிவறை சுத்தம் செய்தல்", "toiletCleaningAmount"),
    ("மொத்த செலவினம் (நாளிதழ்/வாடகை தவிர்த்து)", "_total_ex"), ("மொத்த செலவினம்", "_total"),
]
# தலைப்பில் எழுத்துப்பிழை இருந்தாலும் (எ.கா. 'மொத்த செலவினம') அடையாளம் காண
HIST_HEADER_ALIASES = {"மொத்த செலவினம": "_total", "மொத்த செலவினம (நாளிதழ்/வாடகை தவிர்த்து)": "_total_ex"}
HIST_PAPER_RE = re.compile(r"^\s*நாளிதழ்\s*(\d+)")
HIST_FIRST_YEAR_START = (2024, 4)      # ஏப்ரல் 2024 (நிதியாண்டு 2024-25 தொடக்கம்)
HIST_LAST = (2027, 3)                  # மார்ச் 2027 (நிதியாண்டு 2026-27 முடிவு)
_MONEY_KEYS = ["electricityBill", "rentAmount", "phoneWifiAmount", "waterTax", "photocopyAmount",
               "busAmount", "travelAmount", "postalAmount", "registerAmount", "stationeryAmount",
               "electricalAmount", "consumerAmount", "otherExpense1Amount", "otherExpense2Amount", "dailyGuardAmount",
               "toiletCleaningAmount", "partnerAmount", "staffAmount", "partTimeAmount"]


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
        for title, key in HIST_COLS + HIST_NEW_COLS:
            if h == title:
                col[key] = i
        if h in HIST_HEADER_ALIASES:
            col[HIST_HEADER_ALIASES[h]] = i
        pm = HIST_PAPER_RE.match(h)
        if pm and 1 <= int(pm.group(1)) <= 18:
            paper_cols[int(pm.group(1))] = i
    newspaper_total_col = next((i for i, h in enumerate(hdr) if (h or "").strip() == "நாளிதழ்கள்"), None)
    for need in ("_month", "_lib"):
        if need not in col:
            raise ValueError("தலைப்பு வரி சரியில்லை — மாதிரி Excel-ஐப் பதிவிறக்கி, அதன் தலைப்புகளை மாற்றாமல் பயன்படுத்தவும் "
                             "(தலைப்புகள் 'மாதம்' மற்றும் 'நூலகத்தின் பெயர்' இருக்க வேண்டும்).")

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
            skipped.append(f"வரி {n} ({lib}): மாதம் {my} — ஏப்ரல் 2024 முதல் மார்ச் 2027 வரை மட்டுமே ஏற்றலாம்")
            continue
        L = libs.get(lib)
        code = ((L or {}).get("code") or "").strip().upper()
        if not L or not code:
            skipped.append(f"வரி {n}: நூலகம் '{lib}' பட்டியலில் இல்லை / குறியீடு இல்லை")
            continue
        data = {}
        for _, key in HIST_COLS + HIST_NEW_COLS:
            if key.startswith("_") or key not in col:
                continue
            v = cell(r, col[key])
            data[key] = _hist_date(v) if key in ("busDate", "travelDate") else v
        papers = {s: to_num(cell(r, i)) for s, i in paper_cols.items()}
        paper_total = Decimal(0)
        if newspaper_total_col is not None:
            # 18 நாளிதழ்களின் மொத்தத் தொகை — தனி நெடுவரிசையில் (newspaper_total_amount) சேமிக்கப்படும்
            paper_total = to_num(cell(r, newspaper_total_col))
            papers = {}
        toilet = to_num(data.get("toiletCleaningAmount"))
        approved_txt = cell(r, col["_approved"]) if "_approved" in col else ""
        if approved_txt != "":
            approved = to_num(approved_txt)
        else:
            approved = (sum(papers.values(), Decimal(0)) + paper_total
                        + to_num(data.get("partnerDays")) * rates.get("partner", Decimal(0))
                        + to_num(data.get("staffDays")) * rates.get("staff", Decimal(0))
                        + to_num(data.get("partTimeStaffDays")) * rates.get("part_time", Decimal(0))
                        + sum((to_num(data.get(k)) for k in _MONEY_KEYS), Decimal(0)))
        head = {"unique_key": make_unique_key(code, my), "library_name": lib, "library_code": code,
                "month_year": my, "login_email": "", "library_type": L.get("lib_type") or ""}
        entries.append((head, data, papers, None))
        meta[head["unique_key"]] = (toilet, approved, paper_total)

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
                vals = [(c[0]["unique_key"], meta[c[0]["unique_key"]][0], meta[c[0]["unique_key"]][1],
                         meta[c[0]["unique_key"]][2]) for c in chunk]
                execute_values(cur,
                    "UPDATE claims c SET toilet_cleaning_amount = v.t, approved = TRUE, approved_at = now(), "
                    "approved_amount = v.a, newspaper_total_amount = v.p "
                    "FROM (VALUES %s) AS v(k, t, a, p) WHERE c.unique_key = v.k",
                    vals, template="(%s, %s::numeric, %s::numeric, %s::numeric)")
                return a, b
        a, b = db.run(f)
        inserted += a
        updated += b
    return result(inserted + updated, skipped) | {"inserted": inserted, "updated": updated}


# ---------------------------------------------------------------- ஏற்கனவே பதிவான வரவினங்கள் (Excel)
# தலைப்பின் பெயரைக் கொண்டே நெடுவரிசை அடையாளம் காணப்படும். 'மொத்தம்' நெடுவரிசை சேமிக்கப்படாது —
# அமைப்பே இனங்களைக் கூட்டிக் கணக்கிடும். நூல் இருப்பு இதில் இல்லை (நூலகர் பதிவு செய்யும்போது சேர்ப்பார்).
RECEIPT_COLS = [
    ("மாதம்", "_month"), ("நூலகத்தின் பெயர்", "_lib"), ("UPI NUMBER", "_upi"), ("தொகை செலுத்திய நாள்", "_date"),
    ("காப்புத்தொகை", "security"), ("சந்தாத் தொகை", "annual"), ("காலதாமதக் கட்டணம்", "late_fee"),
    ("நூல் விலைப் பிடித்தம்", "book_fine"), ("பழைய பத்திரிக்கை விற்பனை", "newspaper"),
    ("பழைய நூல்கள் விற்பனை", "old_book_sale"), ("புரவலர்", "patron"), ("பெரும்புரவலர்", "chief_patron"),
    ("கொடையாளர்", "donor"), ("இதர வரவுகள்", "other_income"), ("மொத்தம்", "_total"),
    ("நடப்பு மாத வாசகர் வருகை", "visitors"), ("நடப்பு மாத நூலிரவல்", "issues"),
    ("நடப்பு மாத நூல்கள் பயன்பாடு", "book_usage"),
]
RECEIPT_MONEY = ["security", "annual", "late_fee", "book_fine", "newspaper", "old_book_sale",
                 "patron", "chief_patron", "donor", "other_income"]
RECEIPT_INTS = ["visitors", "issues", "book_usage"]


def _receipt_head(s):
    """தலைப்பை ஒப்பிட: இடைவெளி / அடைப்புக்குறி / பெரிய-சிறிய எழுத்து வேறுபாடு பொருட்படுத்தாது."""
    return re.sub(r"[\s\.\(\)_\-/]+", "", (s or "").strip()).upper()


_RECEIPT_HEAD_MAP = {_receipt_head(t): k for t, k in RECEIPT_COLS}
_RECEIPT_HEAD_MAP.update({_receipt_head("UPI NO"): "_upi", _receipt_head("UTR"): "_upi",
                          _receipt_head("பழைய நாளிதழ் விற்பனை"): "newspaper",
                          _receipt_head("பழைய பத்திரிகை விற்பனை"): "newspaper",
                          _receipt_head("மாதம் (MM/YYYY)"): "_month"})


def _upi_list(s):
    """'627901018885, 630249538322' -> ['627901018885', '630249538322'] (இலக்கங்கள் மட்டும்; நகல் நீக்கம்)."""
    out = []
    for part in re.split(r"[,;\s/]+", s or ""):
        d = re.sub(r"\D", "", part)
        if d and d.strip("0") and d not in out:     # 000000000000 போன்ற இடம் நிரப்பும் எண்கள் UPI அல்ல
            out.append(d)
    return out


def _date_list(s):
    """'05/04/2024, 10/04/2024' -> [date, date]; புரியாத பகுதி இருந்தால் (dates, [தவறானவை])."""
    out, bad = [], []
    for part in re.split(r"[,;\n]+", s or ""):
        part = part.strip()
        if not part:
            continue
        d = parse_date(part)
        if d:
            out.append(d)
            continue
        toks = part.split()
        ds = [parse_date(t) for t in toks]
        if len(toks) > 1 and all(ds):
            out.extend(ds)
        else:
            bad.append(part)
    return list(dict.fromkeys(out)), bad


def do_receipts_approved(raw):
    """ஏற்கனவே பதிவான மாத வரவினங்களை ஏற்றும் (ஏப்ரல் 2024 முதல்). இவை 'ஒப்புதல் பெற்றவை' என்றே சேமிக்கப்படும்.
    - ஏற்கனவே ஒப்புதல் பெற்ற நூலகம்-மாதம் இருந்தால் மாற்றாமல் தவிர்க்கும்.
    - ஒரே ஒரு UPI எண் இருந்தால், அது 'பயன்படுத்தப்பட்டது' எனப் பதிவாகும் (பின்னர் அதே பாஸ்புத்தகத்தை ஏற்றினாலும் மீண்டும் பயன்படுத்த முடியாது).
    - அனைத்து இனங்களும் 0 ஆகவும் UPI காலியாகவும் இருந்தால் 'பயன்பாடு மட்டும்' பதிவாகச் சேமிக்கப்படும்.
    - எந்த வரியிலாவது வாசகர் வருகை / நூலிரவல் / நூல்கள் பயன்பாடு 0 அல்லது காலி எனில் கோப்பு முழுவதும் ஏற்றப்படாது; அந்த வரிகள் பட்டியலிடப்படும்."""
    rows = read_rows(raw, skip=0)
    if not rows:
        raise ValueError("கோப்பு காலியாக உள்ளது")
    col = {}
    for i, h in enumerate(rows[0]):
        k = _RECEIPT_HEAD_MAP.get(_receipt_head(h))
        if k and k not in col:
            col[k] = i
    for need in ("_month", "_lib"):
        if need not in col:
            raise ValueError("தலைப்பு வரி சரியில்லை — மாதிரி Excel-ஐப் பதிவிறக்கி, அதன் தலைப்புகளை மாற்றாமல் பயன்படுத்தவும் "
                             "(தலைப்புகள் 'மாதம்' மற்றும் 'நூலகத்தின் பெயர்' இருக்க வேண்டும்).")
    libs = {r["name"] for r in db.fetch_all("SELECT name FROM libraries")}
    skipped, entries, seen, stat_problems = [], [], set(), []
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
            skipped.append(f"வரி {n} ({lib}): மாதம் {my} — ஏப்ரல் 2024 முதல் மார்ச் 2027 வரை மட்டுமே ஏற்றலாம்")
            continue
        if lib not in libs:
            skipped.append(f"வரி {n}: நூலகம் '{lib}' நூலகப் பட்டியலில் இல்லை")
            continue
        if (lib, my) in seen:
            skipped.append(f"வரி {n} ({lib}): {my} மாதம் இந்தக் கோப்பிலேயே ஏற்கனவே வந்துவிட்டது — இந்த வரி தவிர்க்கப்பட்டது")
            continue
        seen.add((lib, my))
        g = lambda k: cell(r, col[k]) if k in col else ""
        money = {k: to_num(g(k)) for k in RECEIPT_MONEY}
        if any(v < 0 for v in money.values()):
            skipped.append(f"வரி {n} ({lib}): எதிர் தொகை அனுமதிக்கப்படாது")
            continue
        ints = {}
        for k in RECEIPT_INTS:
            try:
                ints[k] = max(int(to_num(g(k))), 0)
            except Exception:
                ints[k] = 0
        upis = _upi_list(g("_upi"))
        dates, bad = _date_list(g("_date"))
        if bad:
            skipped.append(f"வரி {n} ({lib}): தொகை செலுத்திய நாள் புரியவில்லை '{g('_date')}' — தேதி இல்லாமல் சேர்க்கப்பட்டது")
            dates = []
        allowed = max(len(upis), 1)           # எத்தனை UPI உள்ளதோ அத்தனை தேதிகள் வரை அனுமதி
        if len(dates) > allowed:
            skipped.append(f"வரி {n} ({lib}): {len(upis)} UPI எண்ணுக்கு {len(dates)} தேதிகள் உள்ளன — "
                           f"தேதிகளின் எண்ணிக்கை UPI எண்ணிக்கையை மிகக்கூடாது; இந்த வரி தவிர்க்கப்பட்டது")
            continue
        pdate = min(dates) if dates else None
        total = sum(money.values(), Decimal(0))
        miss = [lbl for lbl, k in (("வாசகர் வருகை", "visitors"), ("நூலிரவல்", "issues"), ("நூல்கள் பயன்பாடு", "book_usage"))
                if not ints[k]]
        if miss:
            stat_problems.append(f"வரி {n} · {lib} · {my}: " + ", ".join(miss) + " — 0 / காலி")
        mode = "income" if (total > 0 or upis) else "usage"
        entries.append({"lib": lib, "month": my, "money": money, "ints": ints, "date": pdate,
                        "upis": upis, "dates": dates, "total": total, "mode": mode, "line": n})
    if stat_problems:
        # ஒரு வரியிலாவது புள்ளி விவரம் 0 / காலி எனில் கோப்பு முழுவதும் ஏற்றப்படாது
        shown = stat_problems[:150]
        raise ValueError(f"❌ எதுவும் ஏற்றப்படவில்லை. {len(stat_problems)} வரிகளில் புள்ளி விவரம் (வாசகர் வருகை / நூலிரவல் / நூல்கள் பயன்பாடு) "
                         "0 அல்லது காலியாக உள்ளது. Excel-ல் சரிசெய்து மீண்டும் முழுவதுமாக ஏற்றவும்:\n - " + "\n - ".join(shown)
                         + (f"\n … மேலும் {len(stat_problems) - len(shown)}" if len(stat_problems) > len(shown) else ""))
    if not entries:
        return result(0, skipped) | {"inserted": 0, "updated": 0}

    done = {"inserted": 0, "updated": 0}

    def f(conn):
        with conn.cursor() as c:
            for e in entries:
                lib, my, m, iv = e["lib"], e["month"], e["money"], e["ints"]
                if not e["total"]:   # மொத்தம் 0 எனில் UPI எண் / தேதி எதுவாக இருந்தாலும் (எ.கா. 03102021) சேமிக்காமல் அழி
                    e["upis"], e["dates"], e["date"] = [], [], None
                c.execute("SELECT id, status FROM library_receipts WHERE library_name=%s AND month_year=%s FOR UPDATE", (lib, my))
                old = c.fetchone()
                if old and old[1] == "approved":
                    skipped.append(f"{lib} · {my}: ஏற்கனவே ஒப்புதல் பெற்றது — மாற்றப்படவில்லை")
                    continue
                # UPI எண் வேறொரு நூலகம்/மாதத்தால் ஏற்கனவே பயன்படுத்தப்பட்டிருந்தால் அந்த வரியைத் தவிர்
                if e["upis"]:
                    c.execute("SELECT upi_number, library_name, receipt_id FROM receipt_bank_transactions "
                              "WHERE upi_number = ANY(%s)", (e["upis"],))
                    clash = [t for t in c.fetchall()
                             if not (old and t[2] == old[0]) and (t[1] or "") not in ("", lib)]
                    if clash:
                        skipped.append(f"வரி {e['line']} ({lib}): UPI {clash[0][0]} ஏற்கனவே '{clash[0][1]}' நூலகத்தால் பயன்படுத்தப்பட்டுள்ளது — தவிர்க்கப்பட்டது")
                        continue
                vals = (m["security"], m["annual"], m["newspaper"], e["total"], e["mode"], m["late_fee"], m["book_fine"],
                        m["old_book_sale"], m["patron"], m["chief_patron"], m["donor"], m["other_income"], e["date"],
                        iv["visitors"], iv["issues"], iv["book_usage"], ", ".join(e["upis"]),
                        ", ".join(d.isoformat() for d in e["dates"]) if len(e["dates"]) > 1 else "")
                if old:
                    rid = old[0]
                    c.execute("UPDATE receipt_bank_transactions SET status='available', library_name=NULL, receipt_id=NULL "
                              "WHERE receipt_id=%s", (rid,))
                    c.execute("UPDATE library_receipts SET security_amount=%s, annual_amount=%s, newspaper_amount=%s, total_amount=%s, "
                              "mode=%s, late_fee=%s, book_fine=%s, old_book_sale=%s, patron=%s, chief_patron=%s, donor=%s, "
                              "other_income=%s, payment_date=%s, visitors=%s, issues=%s, book_usage=%s, upi_numbers=%s, payment_dates=%s, "
                              "status='approved', approved_by='Excel ஏற்றம்', approved_at=now() WHERE id=%s", vals + (rid,))
                    done["updated"] += 1
                else:
                    c.execute("INSERT INTO library_receipts(library_name, month_year, security_amount, annual_amount, newspaper_amount, "
                              "total_amount, mode, late_fee, book_fine, old_book_sale, patron, chief_patron, donor, other_income, "
                              "payment_date, visitors, issues, book_usage, upi_numbers, payment_dates, status, created_by, approved_by, approved_at) "
                              "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'approved','Excel ஏற்றம்','Excel ஏற்றம்',now()) "
                              "RETURNING id", (lib, my) + vals)
                    rid = c.fetchone()[0]
                    done["inserted"] += 1
                # ஒரே UPI + தேதி இருந்தால் மட்டுமே வங்கிப் பதிவாகச் சேர்க்க முடியும் (தொகை = மொத்தம்)
                if len(e["upis"]) == 1 and e["date"] and e["total"] > 0:
                    c.execute("INSERT INTO receipt_bank_transactions(month_year, transaction_date, upi_number, amount, status, library_name, receipt_id) "
                              "VALUES (%s,%s,%s,%s,'used',%s,%s) "
                              "ON CONFLICT (upi_number) DO UPDATE SET status='used', library_name=EXCLUDED.library_name, receipt_id=EXCLUDED.receipt_id",
                              (my, e["date"], e["upis"][0], e["total"], lib, rid))
                elif e["upis"]:
                    c.execute("UPDATE receipt_bank_transactions SET status='used', library_name=%s, receipt_id=%s WHERE upi_number = ANY(%s)",
                              (lib, rid, e["upis"]))
    db.run(f)
    return result(done["inserted"] + done["updated"], skipped) | done


# வகை -> (function, விளக்கம்)
# ---------------------------------------------------------------- Vendor விவரம் (நாளிதழ் / வாடகை)
# நெடுவரிசைகள்: LIBRARY CODE, VENDOR CODE, NAME, PAN NUMBER, BANK NAME, BRANCH, IFSC CODE, ACCOUNT NUMBER
# தலைப்பின் பெயரைக் கொண்டே அடையாளம் காணும்; புரியாவிட்டால் A..I வரிசை.
VENDOR_KEYS = ["lib", "vcode", "name", "pan", "bank", "branch", "ifsc", "acct", "share"]
VENDOR_HEAD_MAP = {
    "LIBRARYCODE": "lib", "LIBCODE": "lib", "VENDORCODE": "vcode",
    "LIBRARYNAME": "libname", "LIBNAME": "libname",
    "NAME": "name", "VENDORNAME": "name",
    "PANNUMBER": "pan", "PANNO": "pan", "PAN": "pan",
    "BANKNAME": "bank", "BANK": "bank", "BRANCH": "branch",
    "IFSCCODE": "ifsc", "IFSC": "ifsc",
    "ACCOUNTNUMBER": "acct", "ACCOUNTNO": "acct", "ACCOUNT": "acct", "ACNO": "acct",
    "RENTAMOUNT": "share", "AMOUNT": "share",
}
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
IFSC_RE = re.compile(r"^[A-Z]{4}[0-9A-Z]{7}$")


def _vend_head(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _do_vendors(raw, kind):
    rows = read_rows(raw, skip=0)
    if not rows:
        raise ValueError("கோப்பு காலியாக உள்ளது")
    col = {}
    for i, h in enumerate(rows[0]):
        k = VENDOR_HEAD_MAP.get(_vend_head(h))
        if k and k not in col:
            col[k] = i
    if ("lib" not in col and "libname" not in col) or "vcode" not in col:
        col = {k: i for i, k in enumerate(VENDOR_KEYS)}
    libs = db.fetch_all("SELECT id, name, code FROM libraries")
    by_code = {(l["code"] or "").upper(): l["id"] for l in libs if l["code"]}
    by_name = {l["name"]: l["id"] for l in libs}
    items, skipped = {}, []
    for n, r in enumerate(rows[1:], start=2):
        g = lambda k: cell(r, col[k]) if k in col else ""
        lcode, vcode, name = g("lib"), re.sub(r"\s", "", g("vcode")).upper(), g("name")
        lname = g("libname").strip()
        if not (lcode or lname or vcode or name):
            continue
        lid = by_code.get(lcode.upper()) or by_name.get(lcode) or by_name.get(lname)     # LIBRARY CODE இல்லையெனில் LIBRARY NAME கொண்டு
        if not lid:
            skipped.append(f"வரி {n}: நூலகம் '{lcode or lname}' நூலகப் பட்டியலில் இல்லை")
            continue
        if not vcode:
            skipped.append(f"வரி {n} ({lcode}): VENDOR CODE இல்லை")
            continue
        if not name:
            skipped.append(f"வரி {n} ({lcode}): NAME இல்லை")
            continue
        pan = g("pan").strip()        # PAN / IFSC — கோப்பில் உள்ளபடியே ஏற்றப்படும் (வடிவச் சரிபார்ப்பு இல்லை)
        ifsc = g("ifsc").strip()
        acct = re.sub(r"[\s\-]", "", g("acct"))
        share = Decimal(0)      # வாடகை எப்போதும் சமமாகப் பிரிக்கப்படுவதால் தொகை நெடுவரிசை தேவையில்லை
        items[(lid, vcode)] = (kind, lid, vcode, name, pan, g("bank"), g("branch"), ifsc, acct, share)
    run_batch("INSERT INTO vendors (kind, library_id, vendor_code, name, pan_no, bank_name, branch, ifsc, account_no, rent_share) "
              "VALUES %s ON CONFLICT (kind, library_id, vendor_code) DO UPDATE SET name = EXCLUDED.name, "
              "pan_no = EXCLUDED.pan_no, bank_name = EXCLUDED.bank_name, branch = EXCLUDED.branch, ifsc = EXCLUDED.ifsc, "
              "account_no = EXCLUDED.account_no, "
              "rent_share = CASE WHEN EXCLUDED.rent_share > 0 THEN EXCLUDED.rent_share ELSE vendors.rent_share END, "
              "updated_at = now()", list(items.values()))
    return result(len(items), skipped)


def do_vendors_paper(raw):
    return _do_vendors(raw, "paper")


def do_vendors_rent(raw):
    return _do_vendors(raw, "rent")


# ---------------------------------------------------------------- நூலகப் புள்ளி விவரம் — மார்ச் 2024 அடிப்படை எண்ணிக்கை
STATS_BASE_COLS = [("வ.எண்.", "_no"), ("நூலக வகை", "_type"), ("நூலகத்தின் பெயர்", "_lib"),
                   ("உறுப்பினர் காப்புத் தொகை கட்டணம்", "fee"), ("உறுப்பினர் எண்ணிக்கை", "members"),
                   ("புரவலர் எண்ணிக்கை", "patrons"), ("பெரும்புரவலர் எண்ணிக்கை", "chief_patrons"),
                   ("கொடையாளர் எண்ணிக்கை", "donors")]


def _stat_int(s):
    s = str(s or "").replace(",", "").strip()
    if not s:
        return 0
    n = Decimal(s)
    if n < 0 or n != n.to_integral_value():
        raise ValueError("எண்ணிக்கை முழு எண்ணாக இருக்க வேண்டும்")
    return int(n)


def do_stats_base(raw):
    """மார்ச் 2024 நூலகப் புள்ளி விவரம்: A வ.எண்., B வகை, C நூலகம், D காப்புத் தொகை கட்டணம்,
    E உறுப்பினர், F புரவலர், G பெரும்புரவலர், H கொடையாளர். காலியான எண்ணிக்கை = 0. மீண்டும் ஏற்றினால் புதுப்பிக்கும்."""
    rows, skipped, items = read_rows(raw), [], {}
    known = set(library_ids())
    for n, r in enumerate(rows, 2):
        name = cell(r, 2)
        if not name:
            continue
        if name not in known:
            skipped.append(f"வரிசை {n}: '{name}' — நூலகப் பட்டியலில் இல்லை")
            continue
        try:
            fee = Decimal(cell(r, 3).replace(",", "") or "0")
            if fee < 0:
                raise ValueError("கட்டணம் குறைவாக இருக்க முடியாது")
            vals = [_stat_int(cell(r, i)) for i in (4, 5, 6, 7)]
        except Exception as e:
            skipped.append(f"வரிசை {n}: {name} — {e}")
            continue
        items[name] = (name, fee, *vals)
    run_batch("INSERT INTO library_stats_base (library_name, fee, members, patrons, chief_patrons, donors) VALUES %s "
              "ON CONFLICT (library_name) DO UPDATE SET fee = EXCLUDED.fee, members = EXCLUDED.members, "
              "patrons = EXCLUDED.patrons, chief_patrons = EXCLUDED.chief_patrons, donors = EXCLUDED.donors, "
              "updated_at = now()", list(items.values()))
    return result(len(items), skipped)


IMPORTERS = {
    "libraries":  (do_libraries,  "நூலகப் பட்டியல் (Sheet1)"),
    "newspapers": (do_newspapers, "நாளிதழ் பட்டியல் (NEWSPAPER LIST)"),
    "map":        (do_map,        "நூலகம்-நாளிதழ் இணைப்பு (LIBRARY NEWSPAPER MAP)"),
    "holidays":   (do_holidays,   "அரசு விடுமுறை (HOLIDAYS)"),
    "leave":      (do_leave,      "பணியாளர் விடுப்பு (staff leave days)"),
    "rent":       (do_rent,       "வாடகை (RENT)"),
    "dailywage":  (do_dailywage,  "தினக்கூலி அனுமதி (DAILYWAGES PERMISSION)"),
    "claims":     (do_claims,     "பழைய contingent பதிவுகள் (contingent)"),
    "vendors_paper": (do_vendors_paper, "நாளிதழ் Vendor விவரம்"),
    "vendors_rent": (do_vendors_rent, "வாடகை Vendor விவரம்"),
    "claims_approved": (do_claims_approved, "ஏற்கனவே அனுமதித்த சில்லறை செலவின மாதங்கள்"),
    "receipts_approved": (do_receipts_approved, "ஏற்கனவே பதிவான வரவினங்கள்"),
    "stats_base": (do_stats_base, "மார்ச் 2024 நூலகப் புள்ளி விவரம்"),
}
