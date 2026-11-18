"""app.py மற்றும் import_data.py இரண்டும் பயன்படுத்தும் பகிரப்பட்ட விதிகள்."""
import re
from decimal import Decimal, InvalidOperation

from psycopg2.extras import execute_values

TOTAL_SLOTS = 18

# Slot 1..18 -> HTML பயன்படுத்தும் key (பழைய GAS-ல் இருந்த அதே வரிசை)
NEWSPAPER_KEYS = [
    'dailyThanthi', 'hinduTamil', 'dinamani', 'theekathir', 'tamilMurasu',
    'maalaiMalar', 'maalaiMurasu', 'theHindu', 'indianExpress', 'timesOfIndia',
    'deccanChronicle', 'businessLine', 'economicTimes',
    'paper14', 'paper15', 'paper16', 'paper17', 'paper18',
]

# claims table-ன் text / எண் புலங்கள் (HTML-ன் camelCase key-கள்)
CLAIM_TEXT = [
    'partnerNameTamil', 'staffNameTamil', 'partTimeStaffName', 'rentOwnerName',
    'busPurpose', 'busDate', 'travelPurpose', 'travelDate',
    'registerItem', 'stationeryItem', 'electricalItem', 'consumerItem',
    'otherExpense1Desc', 'otherExpense2Desc',
]
CLAIM_NUM = [
    'partnerDays', 'staffDays', 'partTimeStaffDays', 'electricityBill',
    'rentAmount', 'phoneWifiAmount', 'waterTax', 'photocopyAmount',
    'busAmount', 'travelAmount', 'postalAmount', 'registerAmount',
    'stationeryAmount', 'electricalAmount', 'consumerAmount',
    'otherExpense1Amount', 'otherExpense2Amount',
]


def snake(s):
    return re.sub(r'(?<!^)(?=[A-Z])', '_', s).lower()


def to_num(v):
    """எதையும் பாதுகாப்பாக Decimal ஆக்கும் (காலி / தவறு -> 0)."""
    if v is None or isinstance(v, bool):
        return Decimal(0)
    try:
        d = Decimal(str(v).replace(",", "").strip() or "0")
    except InvalidOperation:
        return Decimal(0)
    if d.is_nan() or d.is_infinite():
        return Decimal(0)
    return d


def jnum(v):
    """JSON-க்கு: முழு எண்ணானால் int, இல்லையெனில் float."""
    if v is None:
        return 0
    d = v if isinstance(v, Decimal) else to_num(v)
    return int(d) if d == d.to_integral_value() else float(d)


def to_text(v):
    return "" if v is None else str(v).strip()


def make_unique_key(code, month_year):
    """TNDPL00765 + '01/2026' -> TNDPL00765012026"""
    return code + month_year.replace("/", "").replace("-", "")


def upsert_claim(cur, head, data, newspapers, pdf_url=None):
    """ஒரு claim-ஐ unique_key மூலம் சேர்க்கும் / புதுப்பிக்கும்; நாளிதழ் தொகைகளையும் மாற்றும்.
    head: unique_key, library_name, library_code, month_year, login_email, library_type
    data: HTML-ன் camelCase புலங்கள்; newspapers: {slot: Decimal}
    திரும்புவது: (claim_id, inserted?)"""
    cols = (["unique_key", "library_name", "library_code", "month_year",
             "login_email", "library_type"]
            + [snake(k) for k in CLAIM_TEXT + CLAIM_NUM]
            + ["claim_pdf_url"])
    vals = ([head["unique_key"], head["library_name"], head["library_code"],
             head["month_year"], head.get("login_email", ""), head.get("library_type", "")]
            + [to_text(data.get(k)) for k in CLAIM_TEXT]
            + [to_num(data.get(k)) for k in CLAIM_NUM]
            + [pdf_url])
    updates = ",".join(f"{c}=EXCLUDED.{c}" for c in cols if c != "unique_key")
    sql = (f"INSERT INTO claims ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))}) "
           f"ON CONFLICT (unique_key) DO UPDATE SET {updates}, updated_at = now() "
           f"RETURNING id, (xmax = 0) AS inserted")
    cur.execute(sql, vals)
    claim_id, inserted = cur.fetchone()

    cur.execute("DELETE FROM claim_newspapers WHERE claim_id = %s", (claim_id,))
    rows = [(claim_id, slot, amt) for slot, amt in newspapers.items() if amt]
    if rows:
        execute_values(cur, "INSERT INTO claim_newspapers (claim_id, slot, amount) VALUES %s", rows)
    return claim_id, inserted


def _claim_cols():
    return (["unique_key", "library_name", "library_code", "month_year",
             "login_email", "library_type"]
            + [snake(k) for k in CLAIM_TEXT + CLAIM_NUM]
            + ["claim_pdf_url"])


def _claim_vals(head, data, pdf_url):
    return ([head["unique_key"], head["library_name"], head["library_code"],
             head["month_year"], head.get("login_email", ""), head.get("library_type", "")]
            + [to_text(data.get(k)) for k in CLAIM_TEXT]
            + [to_num(data.get(k)) for k in CLAIM_NUM]
            + [pdf_url])


def bulk_upsert_claims(cur, entries):
    """பல claim-களை ஒரே நேரத்தில் (வேகமாக) சேர்க்கும் / புதுப்பிக்கும்.
    entries: [(head, data, newspapers, pdf_url), ...]
    ஒரே unique_key மீண்டும் வந்தால் கடைசி வரி மட்டும் எடுக்கப்படும்.
    திரும்புவது: (சேர்க்கப்பட்டவை, புதுப்பிக்கப்பட்டவை)"""
    uniq = {e[0]["unique_key"]: e for e in entries}
    if not uniq:
        return 0, 0
    cols = _claim_cols()
    updates = ",".join(f"{c}=EXCLUDED.{c}" for c in cols if c != "unique_key")
    sql = (f"INSERT INTO claims ({','.join(cols)}) VALUES %s "
           f"ON CONFLICT (unique_key) DO UPDATE SET {updates}, updated_at = now() "
           f"RETURNING id, unique_key, (xmax = 0) AS inserted")
    rows = [_claim_vals(h, d, p) for h, d, _, p in uniq.values()]
    res = execute_values(cur, sql, rows, page_size=200, fetch=True)

    ids = [r[0] for r in res]
    cur.execute("DELETE FROM claim_newspapers WHERE claim_id = ANY(%s)", (ids,))
    paper_rows = []
    for claim_id, ukey, _ in res:
        for slot, amt in uniq[ukey][2].items():
            if amt:
                paper_rows.append((claim_id, slot, amt))
    if paper_rows:
        execute_values(cur, "INSERT INTO claim_newspapers (claim_id, slot, amount) VALUES %s",
                       paper_rows, page_size=1000)
    inserted = sum(1 for r in res if r[2])
    return inserted, len(res) - inserted
