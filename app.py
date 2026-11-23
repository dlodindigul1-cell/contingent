"""நூலக Contingent / வரவினங்கள் — Flask backend (Neon PostgreSQL).

பழைய Google Apps Script function-கள் இங்கே அதே பெயரில், அதே return வடிவில் உள்ளன.
HTML-ல் உள்ள google.script.run அழைப்புகள் (shim மூலம்) /rpc/<பெயர்> க்கு வரும்.
"""
import calendar
import datetime as dt
import hmac
import os
import time
from decimal import Decimal
from html import escape
from zoneinfo import ZoneInfo

from flask import Flask, Response, jsonify, redirect, request, session
from psycopg2.extras import execute_values

import db
from common import multi_worker_type
import importer
from common import (CLAIM_NUM, CLAIM_TEXT, NEWSPAPER_KEYS, TOTAL_SLOTS, WAGE_MAP, jnum,
                    make_unique_key, snake, to_num, upsert_claim)

BASE = os.path.dirname(os.path.abspath(__file__))
IST = ZoneInfo("Asia/Kolkata")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-secret-change-me")
app.config.update(
    SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER")),
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=dt.timedelta(hours=12),
    MAX_CONTENT_LENGTH=25 * 1024 * 1024,   # CSV upload அதிகபட்சம் 25 MB
)


def _json_default(o):
    if isinstance(o, Decimal):
        return jnum(o)
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    raise TypeError(f"JSON-ஆக மாற்ற முடியாது: {type(o)}")


app.json.default = _json_default
app.json.ensure_ascii = False

with open(os.path.join(BASE, "templates", "index.html"), encoding="utf-8") as _f:
    INDEX_HTML = _f.read()
ADMIN_PLACEHOLDER = "<?!= JSON.stringify(isAdmin) ?>"

# ------------------------------------------------------------------
#  RPC பதிவேடு
# ------------------------------------------------------------------
RPC = {}


def rpc(fn):
    RPC[fn.__name__] = fn
    return fn


# புதிய அட்டவணைகள் / நெடுவரிசைகளை (schema.sql — மீண்டும் இயக்கலாம்) முதல் request-ல் தானாக உருவாக்கும்
_schema = {"ready": False, "tries": 0}


_LOGO_CACHE = {}


@app.get("/voucher-logo")
def voucher_logo():
    """வவுச்சர் PDF-இல் லோகோ (Drive படம் CORS தடையின்றி இதே தளத்திலிருந்து வழங்கப்படும்)"""
    import urllib.request
    if "img" not in _LOGO_CACHE:
        try:
            url = "https://drive.google.com/thumbnail?id=1AwZy-uxszelJlyda0Kfvv4_CVgEUBayX&sz=w200"
            with urllib.request.urlopen(url, timeout=8) as r:
                _LOGO_CACHE["img"] = (r.read(), r.headers.get_content_type() or "image/png")
        except Exception:
            return Response(status=404)
    data, ctype = _LOGO_CACHE["img"]
    return Response(data, mimetype=ctype, headers={"Cache-Control": "public, max-age=86400"})


@app.before_request
def _ensure_schema():
    if _schema["ready"] or _schema["tries"] >= 3 or request.path == "/healthz":
        return
    _schema["tries"] += 1
    try:
        importer.do_schema()
        _schema["ready"] = True
    except Exception:
        app.logger.warning("schema தானாக இயங்கவில்லை — Admin > நூலகங்கள் > 'அட்டவணைகளை உருவாக்கு' அழுத்தவும்",
                           exc_info=True)


# admin மட்டும் சேமிக்கக்கூடியவை
ADMIN_ONLY = {"saveNewspaperMasterList", "saveLibraryNewspaperMap", "assignNewspapersToLibraries",
              "saveWageRates", "saveHoliday", "deleteHoliday", "saveDailyWagePermissions",
              "getRegistrationStatus", "getClaimsForApproval", "getClaimForEdit", "adminSaveClaim",
              "approveClaims", "revokeClaimApproval", "deleteClaim",
              "getLibrarianReport", "getPayCenterExpenseReport", "getYearlySummaryReport",
              "getPaperVendorExpenseReport", "getPaperVendorPaymentReport", "getRentPaymentReport"}

# அடுத்த கட்டங்களில் வரும் (இப்போது நட்பான செய்தி மட்டும்)
LATER = {
    "shareContingentBill", "getAvailableMonthsForLibrary",
    "getAvailableMonthsForLibraryAcknowledgement",
    "saveOrUpdateReceipt", "validatePaymentWithBank",
    "sendYearlyReceiptAbstract", "sendYearlyExpenseAbstract",
    "openPrevYearReceiptExpense",
}
LATER_RESULT = {"success": False,
                "message": "இந்த வசதி புதிய அமைப்பில் இன்னும் இணைக்கப்படவில்லை (அடுத்த கட்டத்தில் வரும்)."}


@app.post("/rpc/<name>")
def rpc_call(name):
    args = request.get_json(silent=True)
    if not isinstance(args, list):
        args = []
    if name in ADMIN_ONLY and not session.get("admin"):
        return jsonify({"result": {"success": False,
                                   "message": "Admin அனுமதி தேவை — Admin ஆக உள்நுழையவும்."}})
    fn = RPC.get(name)
    if fn is None:
        if name in LATER:
            return jsonify({"result": LATER_RESULT})
        return jsonify({"__error": "தெரியாத செயல்பாடு: " + name}), 404
    try:
        return jsonify({"result": fn(*args)})
    except Exception:
        app.logger.exception("RPC %s தோல்வி", name)
        return jsonify({"__error": "Server பிழை ஏற்பட்டது. மீண்டும் முயற்சிக்கவும்."})


# ------------------------------------------------------------------
#  நூலகங்கள்
# ------------------------------------------------------------------
DEFAULT_LIB_TYPES = ["மாவட்ட மைய நூலகம்", "முழு நேர கிளை நூலகம்", "கிளை நூலகம்",
                     "ஊர்ப்புற நூலகம்", "பகுதி நேர நூலகம்"]


@rpc
def getLibraryTypes():
    """நூலகப் பட்டியலில் (database) உள்ள வகைகளையே காட்டும் — CSV-ல் உள்ளபடி.
    வழக்கமான வகைகள் முதலில், புதிய வகைகள் பின்னால். Database காலி / பிழை எனில் இயல்பு வகைகள்."""
    try:
        rows = db.fetch_all("SELECT btrim(lib_type) AS t, MIN(id) AS first_id FROM libraries "
                            "WHERE btrim(lib_type) <> '' GROUP BY 1 ORDER BY 2")
    except Exception:
        app.logger.exception("getLibraryTypes")
        rows = []
    found = [r["t"] for r in rows]
    if not found:
        return DEFAULT_LIB_TYPES
    return [t for t in DEFAULT_LIB_TYPES if t in found] + [t for t in found if t not in DEFAULT_LIB_TYPES]


@rpc
def getLibrariesByType(type):
    rows = db.fetch_all("SELECT name FROM libraries WHERE btrim(lib_type) = %s ORDER BY id",
                        (str(type).strip(),))
    return [r["name"] for r in rows]


@rpc
def getEmailsForLibrary(libraryName):
    row = db.fetch_one("SELECT name, emails FROM libraries WHERE name = %s",
                       (str(libraryName or "").strip(),))
    if not row:
        return {"success": False, "message": "நூலகம் பட்டியலில் கிடைக்கவில்லை"}
    emails = [e.strip() for e in row["emails"].lower().replace(";", ",").split(",") if "@" in e]
    # மின்னஞ்சல் இல்லாவிட்டாலும் நூலகர் உள்நுழையலாம் (பெயர் காட்டப்படும்)
    return {"success": True, "library": row["name"], "emails": emails}


@rpc
def getAllLibraryNamesForAdmin():
    return [r["name"] for r in db.fetch_all("SELECT name FROM libraries ORDER BY id")]


# ------------------------------------------------------------------
#  Contingent பதிவு
# ------------------------------------------------------------------
REG_FIRST_DAY, REG_LAST_DAY = 16, 20     # நூலகர் பதிவு செய்யக்கூடிய நாட்கள் (மாதம் 16 – 20)


def _registration_open(month_year):
    """இன்று 16–20 தேதியா, மேலும் பதிவு நடப்பு மாதத்துக்கா?"""
    now = dt.datetime.now(IST)
    return (REG_FIRST_DAY <= now.day <= REG_LAST_DAY
            and month_year == "%02d/%d" % (now.month, now.year))


@rpc
def getExistingContingentData(libraryName, monthYear):
    row = db.fetch_one("SELECT * FROM claims WHERE library_name = %s AND month_year = %s",
                       (str(libraryName or "").strip(), str(monthYear or "").strip()))
    if not row:
        return {"success": False}
    by_slot = {p["slot"]: p["amount"] for p in
               db.fetch_all("SELECT slot, amount FROM claim_newspapers WHERE claim_id = %s", (row["id"],))}
    data = {}
    for k in CLAIM_TEXT:
        data[k] = row[snake(k)] or ""
    for k in CLAIM_NUM:
        data[k] = jnum(row[snake(k)])
    for i, k in enumerate(NEWSPAPER_KEYS):
        data[k] = jnum(by_slot.get(i + 1, 0))
    data["monthYear"] = row["month_year"]
    data["dailyGuardAmount"] = jnum(row["daily_guard_amount"])
    return {"success": True, "rowIndex": row["id"], "data": data,
            "approved": bool(row["approved"]),
            "toiletCleaning": jnum(row["toilet_cleaning_amount"])}


@rpc
def submitOrUpdateContingent(data):
    data = data or {}
    name = str(data.get("libraryName") or "").strip()
    month_year = str(data.get("monthYear") or "").strip()
    if not name or not month_year:
        return {"success": False, "message": "நூலக பெயர் அல்லது மாதம்/வருடம் காலியாக உள்ளது"}

    lib = db.fetch_one("SELECT code FROM libraries WHERE name = %s", (name,))
    if not lib or not (lib["code"] or "").strip():
        return {"success": False, "message": "நூலக குறியீடு கிடைக்கவில்லை: " + name}
    code = lib["code"].strip().upper()

    if not session.get("admin"):
        if not _registration_open(month_year):
            return {"success": False,
                    "message": "பதிவு / திருத்தம் ஒவ்வொரு மாதமும் %d முதல் %d ஆம் தேதி வரை மட்டுமே செய்ய இயலும்." % (REG_FIRST_DAY, REG_LAST_DAY)}
        done = db.fetch_one("SELECT approved FROM claims WHERE unique_key = %s", (make_unique_key(code, month_year),))
        if done and done["approved"]:
            return {"success": False, "message": "இந்த மாதம் ஏற்கனவே அனுமதிக்கப்பட்டுவிட்டது; மாற்ற இயலாது."}

    head = {
        "unique_key": make_unique_key(code, month_year),
        "library_name": name, "library_code": code, "month_year": month_year,
        "login_email": str(data.get("loginEmail") or "").strip(),
        "library_type": str(data.get("libraryType") or "").strip(),
    }
    papers = {i + 1: to_num(data.get(k)) for i, k in enumerate(NEWSPAPER_KEYS)}

    def f(conn):
        with conn.cursor() as cur:
            return upsert_claim(cur, head, data, papers)

    claim_id, inserted = db.run(f)
    return {"success": True, "row": claim_id, "action": "created" if inserted else "updated"}



# ------------------------------------------------------------------
#  சில்லறை செலவின மேலாண்மை (Admin): பதிவு நிலை + திருத்தம் + அனுமதி
# ------------------------------------------------------------------
CLAIM_MONEY = ["electricity_bill", "rent_amount", "phone_wifi_amount", "water_tax", "photocopy_amount",
               "bus_amount", "travel_amount", "postal_amount", "register_amount", "stationery_amount",
               "electrical_amount", "consumer_amount", "other_expense1_amount", "other_expense2_amount",
               "daily_guard_amount", "toilet_cleaning_amount"]

# Admin மட்டும் நிரப்பும் தலைப்புகள் (நூலகர் படிவத்தில் வராது): key -> (column, தலைப்பு)
ADMIN_HEADS = {"toiletCleaningAmount": ("toilet_cleaning_amount", "கழிவறை சுத்தம் (₹)"),
               "newspaperTotalAmount": ("newspaper_total_amount", "நாளிதழ்கள் - மொத்தம் (₹)")}
CLAIM_LABELS = {
    "partnerNameTamil": "கூட்டுநர் பெயர்", "partnerDays": "கூட்டுநர் பணிநாட்கள்",
    "staffNameTamil": "நூலகப் பணியாளர் பெயர்", "staffDays": "நூலகப் பணியாளர் பணிநாட்கள்",
    "partTimeStaffName": "பகுதி நேரப் பணியாளர் பெயர்", "partTimeStaffDays": "பகுதி நேரப் பணியாளர் பணிநாட்கள்",
    "electricityBill": "மின் கட்டணம் (₹)", "rentOwnerName": "கட்டட உரிமையாளர்", "rentAmount": "வாடகை (₹)",
    "phoneWifiAmount": "வைஃபை / தொலைபேசி (₹)", "waterTax": "குடிநீர் வரி (₹)", "photocopyAmount": "நகல் செலவினம் (₹)",
    "busPurpose": "பேருந்து - காரணம்", "busDate": "பேருந்து - நாள்", "busAmount": "பேருந்து கட்டணம் (₹)",
    "travelPurpose": "போக்குவரத்து - காரணம்", "travelDate": "போக்குவரத்து - நாள்", "travelAmount": "போக்குவரத்துக் கட்டணம் (₹)",
    "postalAmount": "அஞ்சல் (₹)", "registerItem": "பதிவேடு - விவரம்", "registerAmount": "பதிவேடு (₹)",
    "stationeryItem": "எழுதுபொருள் - விவரம்", "stationeryAmount": "எழுதுபொருள் (₹)",
    "electricalItem": "மின்சாதனம் - விவரம்", "electricalAmount": "மின்சாதனம் (₹)",
    "consumerItem": "நுகர்பொருள் - விவரம்", "consumerAmount": "நுகர்பொருள் (₹)",
    "otherExpense1Desc": "பிற செலவினம் 1 - விவரம்", "otherExpense1Amount": "பிற செலவினம் 1 (₹)",
    "otherExpense2Desc": "பிற செலவினம் 2 - விவரம்", "otherExpense2Amount": "பிற செலவினம் 2 (₹)",
    "dailyGuardAmount": "Night Watchman (₹)",
}


def _month_or_now(month_year):
    m = str(month_year or "").strip()
    if len(m) == 7 and m[2] == "/" and m[:2].isdigit() and m[3:].isdigit():
        return m
    now = dt.datetime.now(IST)
    return "%02d/%d" % (now.month, now.year)


def _claim_total(row, paper_sum, rates):
    d = lambda v: Decimal(str(v or 0))
    t = d(paper_sum) + d(row.get("newspaper_total_amount"))
    # தினக்கூலி: பதிவின் போது நிலையாகச் சேமித்த தொகை. (தொகை 0 ஆகவும் நாட்கள் > 0 ஆகவும் இருந்தால் மட்டும்,
    # அதாவது schema புதுப்பிப்புக்கு முந்தைய பதிவு எனில், தற்போதைய விகிதத்தில் கணக்கிடும்)
    for amt_col, days_col, rate_key in (("partner_amount", "partner_days", "partner"),
                                        ("staff_amount", "staff_days", "staff"),
                                        ("part_time_amount", "part_time_staff_days", "partTime")):
        amt = d(row.get(amt_col))
        t += amt if amt else d(row[days_col]) * d(rates.get(rate_key))
    for k in CLAIM_MONEY:
        t += d(row[k])
    return t


@rpc
def getRegistrationStatus(monthYear=None):
    """நடப்பு (அல்லது தேர்ந்த) மாதம்: பதிவு செய்தவர்கள் / செய்யாதவர்கள்"""
    m = _month_or_now(monthYear)
    libs = db.fetch_all("SELECT name, lib_type FROM libraries ORDER BY id")
    done = {r["library_name"] for r in
            db.fetch_all("SELECT DISTINCT library_name FROM claims WHERE month_year = %s", (m,))}
    reg = [{"name": l["name"], "type": l["lib_type"]} for l in libs if l["name"] in done]
    notreg = [{"name": l["name"], "type": l["lib_type"]} for l in libs if l["name"] not in done]
    return {"success": True, "month": m, "registered": reg, "notRegistered": notreg, "total": len(libs)}


# ------------------------------------------------------------------
#  அனுமதிக்கப்பட்ட சில்லறை செலவினம் (நூலகர் பட்டன்) — அனுமதித்த செலவினங்கள் மட்டும்
# ------------------------------------------------------------------
_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
         "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _words_below_1000(n):
    out = []
    if n >= 100:
        out.append(_ONES[n // 100] + " Hundred")
        n %= 100
        if n:
            out.append("and")
    if n >= 20:
        out.append(_TENS[n // 10] + ((" " + _ONES[n % 10]) if n % 10 else ""))
    elif n:
        out.append(_ONES[n])
    return " ".join(out)


def amount_in_words(amount):
    """இந்திய முறை (Lakh / Crore): 52941 -> 'Fifty Two Thousand Nine Hundred and Forty One rupees only'"""
    amt = Decimal(str(amount or 0)).quantize(Decimal("0.01"))
    rupees, paise = int(amt), int((amt - int(amt)) * 100)
    parts = []
    for div, name in ((10000000, "Crore"), (100000, "Lakh"), (1000, "Thousand")):
        q, rupees = divmod(rupees, div)
        if q:
            parts.append(_words_below_1000(q) + " " + name)
    if rupees:
        w = _words_below_1000(rupees)
        parts.append(("and " + w) if parts and rupees < 100 else w)
    text = " ".join(parts) or "Zero"
    text += " rupees"
    if paise:
        text += " and " + _words_below_1000(paise) + " paise"
    return text + " only"


@rpc
def getApprovedMonthsForLibrary(libraryName):
    """இந்த நூலகத்தின் அனுமதிக்கப்பட்ட மாதங்கள் (புதியது முதலில்)."""
    rows = db.fetch_all("SELECT month_year FROM claims WHERE library_name = %s AND approved "
                        "ORDER BY substr(month_year, 4, 4) DESC, substr(month_year, 1, 2) DESC",
                        (str(libraryName or "").strip(),))
    if not rows:
        return {"success": False, "message": "இந்த நூலகத்துக்கு அனுமதிக்கப்பட்ட மாதங்கள் எதுவும் இல்லை."}
    return {"success": True, "months": [r["month_year"] for r in rows]}


# (தலைப்பு, claims நெடுவரிசை) — Excel வரிசைப்படி; தொகை 0 எனில் அறிக்கையில் வராது
STATEMENT_ITEMS = [
    ("தினக்கூலி பகுதி நேரத் துப்புரவாளர்", "partner_amount"),
    ("தினக்கூலி நூலகர்", "staff_amount"),
    ("தினக்கூலிக் காவலர்", "daily_guard_amount"),
    ("தினக்கூலி பகுதி நேர நூலகர்", "part_time_amount"),
    ("பேருந்து கட்டணம்", "bus_amount"),
    ("எழுது பொருட்கள் வாங்குதல்", "stationery_amount"),
    ("பதிவேடு வாங்குதல்", "register_amount"),
    ("நுகர்பொருள் வாங்குதல்", "consumer_amount"),
    ("மின்சாதனங்கள் வாங்குதல்", "electrical_amount"),
    ("அஞ்சல் செலவினம்", "postal_amount"),
    ("நகலெடுக்கும் கட்டணம்", "photocopy_amount"),
    ("@1", "other_expense1_amount"),
    ("@2", "other_expense2_amount"),
    ("போக்குவரத்து கட்டணம்", "travel_amount"),
    ("குடிநீர் கட்டணம்", "water_tax"),
    ("தொலைபேசி கட்டணம்", "phone_wifi_amount"),
    ("கழிவறை சுத்தம் செய்தல்", "toilet_cleaning_amount"),
]


@rpc
def getApprovedExpenseStatement(libraryName, monthYear):
    """அனுமதிக்கப்பட்ட மாதத்தின் செலவினங்கள் — தொகை உள்ளவை மட்டும் (PDF அறிக்கைக்கு)."""
    name, my = str(libraryName or "").strip(), str(monthYear or "").strip()
    row = db.fetch_one("SELECT * FROM claims WHERE library_name = %s AND month_year = %s", (name, my))
    if not row:
        return {"success": False, "message": "இந்த மாதத்துக்குப் பதிவு இல்லை."}
    if not row["approved"]:
        return {"success": False, "message": "இந்த மாதம் இன்னும் அனுமதிக்கப்படவில்லை."}
    rates = getWageRates()
    papers = db.fetch_one("SELECT COALESCE(SUM(amount), 0) AS s FROM claim_newspapers WHERE claim_id = %s", (row["id"],))
    paper_total = Decimal(str(papers["s"] or 0)) + Decimal(str(row["newspaper_total_amount"] or 0))
    d = lambda v: Decimal(str(v or 0))

    def amount_of(col):
        v = d(row.get(col))
        if v == 0 and col in ("partner_amount", "staff_amount", "part_time_amount"):   # பழைய பதிவு (நாட்கள் மட்டும்)
            days_col, rk = {"partner_amount": ("partner_days", "partner"), "staff_amount": ("staff_days", "staff"),
                            "part_time_amount": ("part_time_staff_days", "partTime")}[col]
            v = d(row[days_col]) * d(rates.get(rk))
        return v

    items = []
    def add(desc, v):
        if v and v != 0:
            items.append({"desc": desc, "amount": jnum(v)})
    add("நாளிதழ்கள்", paper_total)
    rent = d(row["rent_amount"])
    add("வாடகை", rent)
    add("மின் கட்டணம்", d(row["electricity_bill"]))
    for title, col in STATEMENT_ITEMS:
        if title == "@1":
            title = (row["other_expense1_desc"] or "").strip() or "பிற செலவினம் 1"
        elif title == "@2":
            title = (row["other_expense2_desc"] or "").strip() or "பிற செலவினம் 2"
        add(title, amount_of(col))
    total = d(row["approved_amount"]) if row["approved_amount"] is not None else sum((Decimal(str(i["amount"])) for i in items), Decimal(0))
    total_ex = total - paper_total - rent
    return {"success": True, "library": row["library_name"], "month": row["month_year"], "items": items,
            "totalExcl": jnum(total_ex), "total": jnum(total), "inWords": amount_in_words(total_ex)}


@rpc
def getClaimsForApproval(monthYear=None):
    m = _month_or_now(monthYear)
    rates = getWageRates()
    rows = db.fetch_all("SELECT c.*, l.id AS lib_id FROM claims c LEFT JOIN libraries l ON l.name = c.library_name "
                        "WHERE c.month_year = %s ORDER BY l.id NULLS LAST, c.library_name", (m,))
    papers = {}
    for p in db.fetch_all("SELECT claim_id, SUM(amount) AS s FROM claim_newspapers GROUP BY claim_id"):
        papers[p["claim_id"]] = p["s"]
    out = []
    for r in rows:
        tot = (r["approved_amount"] if r["approved"] and r["approved_amount"] is not None
               else _claim_total(r, papers.get(r["id"], 0), rates))
        out.append({"id": r["id"], "name": r["library_name"], "type": r["library_type"],
                    "total": float(tot), "approved": bool(r["approved"]),
                    "approvedAmount": float(r["approved_amount"]) if r["approved_amount"] is not None else None,
                    "approvedAt": r["approved_at"].astimezone(IST).strftime("%d/%m/%Y %H:%M") if r["approved_at"] else ""})
    return {"success": True, "month": m, "rows": out,
            "approvedTotal": sum(x["approvedAmount"] or 0 for x in out if x["approved"]),
            "grandTotal": sum(x["total"] for x in out)}


@rpc
def getClaimForEdit(claimId):
    row = db.fetch_one("SELECT * FROM claims WHERE id = %s", (claimId,))
    if not row:
        return {"success": False, "message": "பதிவு கிடைக்கவில்லை"}
    names = {n["slot"]: n["name"] for n in db.fetch_all("SELECT slot, name FROM newspapers")}
    paps = db.fetch_all("SELECT slot, amount FROM claim_newspapers WHERE claim_id = %s ORDER BY slot", (claimId,))
    fields = []
    for k in CLAIM_TEXT + CLAIM_NUM:
        v = row[snake(k)]
        fields.append({"key": k, "label": CLAIM_LABELS.get(k, k), "num": k in CLAIM_NUM,
                       "value": jnum(v) if k in CLAIM_NUM else (v or "")})
    extras = [{"key": k, "label": lab, "value": jnum(row[col])} for k, (col, lab) in ADMIN_HEADS.items()]
    return {"success": True, "id": claimId, "library": row["library_name"], "month": row["month_year"],
            "approved": bool(row["approved"]), "fields": fields, "extras": extras,
            "papers": [{"slot": p["slot"], "name": names.get(p["slot"]) or ("நாளிதழ் " + str(p["slot"])),
                        "amount": jnum(p["amount"])} for p in paps]}


@rpc
def adminSaveClaim(claimId, data, papers, extra=None):
    row = db.fetch_one("SELECT * FROM claims WHERE id = %s", (claimId,))
    if not row:
        return {"success": False, "message": "பதிவு கிடைக்கவில்லை"}
    if row["approved"]:
        return {"success": False, "message": "இது அனுமதிக்கப்பட்ட பதிவு — மாற்ற, முதலில் '↩ ரத்து' மூலம் அனுமதியை நீக்கவும்."}
    data = data or {}
    head = {"unique_key": row["unique_key"], "library_name": row["library_name"],
            "library_code": row["library_code"], "month_year": row["month_year"],
            "login_email": row["login_email"], "library_type": row["library_type"]}
    pap = {int(k): to_num(v) for k, v in (papers or {}).items()}

    # தினக்கூலி தொகை: நாட்கள் மாறியிருந்தால் மீண்டும் கணக்கிடப்படும் (upsert_claim-ல்);
    # நாட்கள் இல்லாத (Excel-ல் தொகை மட்டும் ஏற்றிய) பதிவின் தொகை அப்படியே இருக்கும்.
    data = dict(data)
    for days_key, amt_key, _r in WAGE_MAP:
        if to_num(data.get(days_key)) <= 0 and to_num(row[snake(days_key)]) <= 0:
            data[amt_key] = row[snake(amt_key)]          # நாட்கள் இல்லை -> இருந்த தொகையைப் பாதுகாக்க
        elif to_num(data.get(days_key)) <= 0:
            data[amt_key] = 0                            # நாட்களை 0 ஆக்கினால் தொகையும் 0

    extra = extra or {}

    def f(conn):
        with conn.cursor() as cur:
            res = upsert_claim(cur, head, data, pap)
            for k, (col, _lab) in ADMIN_HEADS.items():
                if k in extra:
                    cur.execute(f"UPDATE claims SET {col} = %s WHERE id = %s", (to_num(extra[k]), claimId))
            return res
    db.run(f)
    return {"success": True}


@rpc
def approveClaims(claimIds):
    ids = [int(i) for i in (claimIds or [])]
    if not ids:
        return {"success": False, "message": "எதுவும் தேர்ந்தெடுக்கப்படவில்லை"}
    rates = getWageRates()
    rows = db.fetch_all("SELECT * FROM claims WHERE id = ANY(%s)", (ids,))
    papers = {p["claim_id"]: p["s"] for p in
              db.fetch_all("SELECT claim_id, SUM(amount) AS s FROM claim_newspapers WHERE claim_id = ANY(%s) GROUP BY claim_id", (ids,))}
    vals = [(r["id"], _claim_total(r, papers.get(r["id"], 0), rates)) for r in rows]

    def f(conn):
        with conn.cursor() as cur:
            for cid, tot in vals:
                cur.execute("UPDATE claims SET approved = TRUE, approved_at = now(), approved_amount = %s WHERE id = %s",
                            (tot, cid))
    db.run(f)
    return {"success": True, "count": len(vals), "amount": float(sum(t for _, t in vals))}


@rpc
def revokeClaimApproval(claimId):
    db.execute("UPDATE claims SET approved = FALSE, approved_at = NULL, approved_amount = NULL WHERE id = %s", (claimId,))
    return {"success": True}


@rpc
def deleteClaim(claimId):
    """Admin மட்டும்: ஒரு நூலகத்தின் மாதாந்திரச் சில்லறை செலவினப் பதிவை முழுமையாக நீக்கு.
    claim_newspapers அட்டவணையில் ON DELETE CASCADE இருப்பதால் தொடர்புடைய நாளிதழ் வரிகளும் நீங்கும்."""
    row = db.fetch_one("SELECT id, library_name, month_year FROM claims WHERE id = %s", (int(claimId),))
    if not row:
        return {"success": False, "message": "நீக்க வேண்டிய பதிவு கிடைக்கவில்லை."}
    db.execute("DELETE FROM claims WHERE id = %s", (int(claimId),))
    return {"success": True, "library": row["library_name"], "month": row["month_year"]}


# ------------------------------------------------------------------
#  நாளிதழ் (விலை கணக்கு + Active பட்டியல் + Admin)
# ------------------------------------------------------------------
def parse_month_year(month_year):
    """'MM/YYYY' -> (ஆண்டு, மாதம்). தவறு / காலி எனில் இன்றைய மாதம் (IST)."""
    try:
        m, y = str(month_year or "").strip().split("/")
        m, y = int(m), int(y)
        if 1 <= m <= 12 and 2000 <= y <= 2100:
            return y, m
    except ValueError:
        pass
    today = dt.datetime.now(IST).date()
    return today.year, today.month


def month_max_amount(weekday_rate, sunday_rate, specials, year, month):
    """ஒரு நாளிதழின் அந்த மாத அதிகபட்சத் தொகை.
    சிறப்பு விலை நாள் -> சிறப்பு விலை (ஞாயிறாக இருந்தாலும்); இல்லையெனில் ஞாயிறு -> ஞாயிறு விலை;
    மற்ற நாட்கள் -> திங்கள்–சனி விலை.  specials: {date: Decimal}"""
    wd = sun = 0
    total = Decimal(0)
    used = []
    for day in range(1, calendar.monthrange(year, month)[1] + 1):
        d = dt.date(year, month, day)
        if d in specials:
            total += specials[d]
            used.append({"date": d.isoformat(), "price": jnum(specials[d])})
        elif d.weekday() == 6:
            sun += 1
            total += sunday_rate
        else:
            wd += 1
            total += weekday_rate
    return {"total": total, "weekdayDays": wd, "sundayDays": sun, "specials": used}


def _specials_for_month(year, month):
    first = dt.date(year, month, 1)
    last = dt.date(year, month, calendar.monthrange(year, month)[1])
    out = {}
    for r in db.fetch_all("SELECT slot, special_date, price FROM newspaper_special_prices "
                          "WHERE special_date BETWEEN %s AND %s", (first, last)):
        out.setdefault(r["slot"], {})[r["special_date"]] = r["price"]
    return out


@rpc
def getActiveNewspapersForLibrary(libraryName, monthYear=None):
    """நூலகத்திற்கு அனுமதிக்கப்பட்ட நாளிதழ்கள் + அந்த மாதத்தின் அதிகபட்சத் தொகை (maxRate)."""
    year, month = parse_month_year(monthYear)
    rows = db.fetch_all(
        "SELECT n.slot, n.name, n.max_rate, n.weekday_rate, n.sunday_rate FROM libraries l "
        "JOIN library_newspaper_map m ON m.library_id = l.id AND m.active "
        "JOIN newspapers n ON n.slot = m.slot "
        "WHERE l.name = %s AND n.name <> '' ORDER BY n.slot",
        (str(libraryName or "").strip(),))
    specials = _specials_for_month(year, month)
    out = []
    for r in rows:
        sp = specials.get(r["slot"], {})
        item = {"slot": r["slot"], "key": NEWSPAPER_KEYS[r["slot"] - 1], "name": r["name"],
                "weekdayRate": jnum(r["weekday_rate"]), "sundayRate": jnum(r["sunday_rate"])}
        if not r["weekday_rate"] and not r["sunday_rate"] and not sp and r["max_rate"]:
            # இன்னும் நாள்வாரி விலை உள்ளிடப்படாத பழைய பதிவு -> பழைய மாத அதிகபட்சம்
            item.update(maxRate=jnum(r["max_rate"]), weekdayDays=0, sundayDays=0, specials=[], legacy=True)
        else:
            calc = month_max_amount(r["weekday_rate"], r["sunday_rate"], sp, year, month)
            item.update(maxRate=jnum(calc["total"]), weekdayDays=calc["weekdayDays"],
                        sundayDays=calc["sundayDays"], specials=calc["specials"], legacy=False)
        out.append(item)
    return out


@rpc
def getNewspaperAdminMasterList():
    rows = db.fetch_all("SELECT slot, name, max_rate, weekday_rate, sunday_rate FROM newspapers ORDER BY slot")
    sp = {}
    for r in db.fetch_all("SELECT slot, special_date, price FROM newspaper_special_prices "
                          "ORDER BY special_date"):
        sp.setdefault(r["slot"], []).append({"date": r["special_date"].isoformat(), "price": jnum(r["price"])})
    return [{"slot": r["slot"], "name": r["name"], "maxRate": jnum(r["max_rate"]),
             "weekdayRate": jnum(r["weekday_rate"]), "sundayRate": jnum(r["sunday_rate"]),
             "specials": sp.get(r["slot"], [])} for r in rows]


@rpc
def saveNewspaperMasterList(rows):
    items, specials = {}, []
    for r in rows or []:
        try:
            slot = int(r.get("slot"))
        except (TypeError, ValueError):
            continue
        if not 1 <= slot <= TOTAL_SLOTS:
            continue
        items[slot] = (slot, str(r.get("name") or "").strip(),
                       max(to_num(r.get("weekdayRate")), Decimal(0)),
                       max(to_num(r.get("sundayRate")), Decimal(0)))
        seen = set()
        for sp in r.get("specials") or []:
            try:
                d = dt.date.fromisoformat(str(sp.get("date"))[:10])
            except ValueError:
                continue
            if d not in seen:
                seen.add(d)
                specials.append((slot, d, max(to_num(sp.get("price")), Decimal(0))))

    def f(conn):
        if not items:
            return
        with conn.cursor() as cur:
            execute_values(
                cur,
                "INSERT INTO newspapers (slot, name, weekday_rate, sunday_rate) VALUES %s "
                "ON CONFLICT (slot) DO UPDATE SET name = EXCLUDED.name, "
                "weekday_rate = EXCLUDED.weekday_rate, sunday_rate = EXCLUDED.sunday_rate",
                list(items.values()))
            # சிறப்பு விலைகள்: அனுப்பப்பட்ட நாளிதழ்களுக்கு முழுவதையும் மாற்றுகிறோம்
            cur.execute("DELETE FROM newspaper_special_prices WHERE slot = ANY(%s)", (list(items),))
            if specials:
                execute_values(cur, "INSERT INTO newspaper_special_prices (slot, special_date, price) VALUES %s",
                               specials)

    db.run(f)
    return {"success": True}


@rpc
def getLibraryNewspaperMapForLibrary(libraryName):
    lib = db.fetch_one("SELECT id FROM libraries WHERE name = %s", (str(libraryName or "").strip(),))
    rows = db.fetch_all("SELECT slot, active FROM library_newspaper_map WHERE library_id = %s",
                        (lib["id"],)) if lib else []
    active = {r["slot"]: r["active"] for r in rows}
    return {"success": True, "found": bool(rows),
            "map": {s: bool(active.get(s, False)) for s in range(1, TOTAL_SLOTS + 1)}}


@rpc
def saveLibraryNewspaperMap(libraryName, activeSlotsArray):
    lib = db.fetch_one("SELECT id FROM libraries WHERE name = %s", (str(libraryName or "").strip(),))
    if not lib:
        return {"success": False, "message": "நூலகம் கிடைக்கவில்லை: " + str(libraryName)}
    flags = [bool(v) for v in (activeSlotsArray or [])][:TOTAL_SLOTS]
    flags += [False] * (TOTAL_SLOTS - len(flags))

    def f(conn):
        with conn.cursor() as cur:
            execute_values(
                cur,
                "INSERT INTO library_newspaper_map (library_id, slot, active) VALUES %s "
                "ON CONFLICT (library_id, slot) DO UPDATE SET active = EXCLUDED.active",
                [(lib["id"], i + 1, flags[i]) for i in range(TOTAL_SLOTS)])

    db.run(f)
    return {"success": True}


@rpc
def assignNewspapersToLibraries(slots, libraryNames, allow):
    """பல நாளிதழ் × பல நூலகம்: ஒரே முறையில் அனுமதி கொடுக்க (allow=True) / நீக்க (allow=False).
    தேர்ந்தெடுக்காத மற்ற இணைப்புகள் மாறாது."""
    slot_list = sorted({int(s) for s in (slots or []) if str(s).isdigit() and 1 <= int(s) <= TOTAL_SLOTS})
    names = [str(n).strip() for n in (libraryNames or []) if str(n).strip()]
    if not slot_list or not names:
        return {"success": False, "message": "குறைந்தது ஒரு நாளிதழும் ஒரு நூலகமும் தேர்ந்தெடுக்கவும்."}
    libs = db.fetch_all("SELECT id FROM libraries WHERE name = ANY(%s)", (names,))
    if not libs:
        return {"success": False, "message": "தேர்ந்தெடுத்த நூலகங்கள் கிடைக்கவில்லை."}
    flag = bool(allow)
    rows = [(l["id"], s, flag) for l in libs for s in slot_list]

    def f(conn):
        with conn.cursor() as cur:
            execute_values(
                cur,
                "INSERT INTO library_newspaper_map (library_id, slot, active) VALUES %s "
                "ON CONFLICT (library_id, slot) DO UPDATE SET active = EXCLUDED.active",
                rows, page_size=1000)

    db.run(f)
    return {"success": True, "libraries": len(libs), "newspapers": len(slot_list), "allow": flag}


# ------------------------------------------------------------------
#  பணி நாட்கள், வாடகை, தினக்கூலி
# ------------------------------------------------------------------
TAMIL_MONTHS = ["ஜனவரி", "பிப்ரவரி", "மார்ச்", "ஏப்ரல்", "மே", "ஜூன்",
                "ஜூலை", "ஆகஸ்ட்", "செப்டம்பர்", "அக்டோபர்", "நவம்பர்", "டிசம்பர்"]


def working_period(today):
    """கடந்த மாதம் 16 -> நடப்பு மாதம் 15."""
    end = dt.date(today.year, today.month, 15)
    py, pm = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    return dt.date(py, pm, 16), end


def period_breakdown(start, end, holidays):
    """வெள்ளி, இரண்டாம் சனி (8-14), அரசு விடுமுறை நீங்கலாக பணி நாட்கள் + விவரம்.
    விடுமுறை வெள்ளி / இரண்டாம் சனியில் வந்தால் ஒரே முறைதான் கழிக்கப்படும்."""
    total = fri = sat2 = hol = work = 0
    d = start
    while d <= end:
        total += 1
        if d.weekday() == 4:
            fri += 1
        elif d.weekday() == 5 and 7 < d.day <= 14:
            sat2 += 1
        elif d in holidays:
            hol += 1
        else:
            work += 1
        d += dt.timedelta(days=1)
    return {"total": total, "fridays": fri, "secondSaturdays": sat2, "holidays": hol, "working": work}


def count_working_days(start, end, holidays):
    return period_breakdown(start, end, holidays)["working"]


def _holiday_set(start, end):
    return {r["holiday_date"] for r in db.fetch_all(
        "SELECT holiday_date FROM holidays WHERE holiday_date BETWEEN %s AND %s", (start, end))}


def _period_text(start, end):
    return f"{TAMIL_MONTHS[start.month - 1]} {start.day} முதல் {TAMIL_MONTHS[end.month - 1]} {end.day} முடிய"


@rpc
def getWorkingDaysCount(libName):
    start, end = working_period(dt.datetime.now(IST).date())
    total = count_working_days(start, end, _holiday_set(start, end))
    row = db.fetch_one(
        "SELECT s.leave_days FROM staff_leave_days s JOIN libraries l ON l.id = s.library_id "
        "WHERE l.name = %s", (str(libName or "").strip(),))
    leave = jnum(row["leave_days"]) if row else 0
    return {
        "totalWD": total,
        "partnerLimit": total - leave,
        "staffLimit": total - leave,
        "leaveTaken": leave,
        "period": _period_text(start, end),
    }


@rpc
def getWorkingDaysTable(year):
    """ஒரு ஆண்டின் 12 மாதங்களுக்கான பணி நாட்கள் (கடந்த மாதம் 16 -> அந்த மாதம் 15)."""
    try:
        year = int(year)
    except (TypeError, ValueError):
        year = dt.datetime.now(IST).year
    holidays = _holiday_set(dt.date(year - 1, 12, 16), dt.date(year, 12, 15))
    rows = []
    for m in range(1, 13):
        end = dt.date(year, m, 15)
        start = dt.date(year - 1, 12, 16) if m == 1 else dt.date(year, m - 1, 16)
        b = period_breakdown(start, end, holidays)
        rows.append({"month": m, "label": f"{TAMIL_MONTHS[m - 1]} {year}", "period": _period_text(start, end), **b})
    return rows


# ------------------------------------------------------------------
#  அரசு விடுமுறை நாட்கள் (Admin)
# ------------------------------------------------------------------
@rpc
def getHolidays(year=None):
    try:
        year = int(year)
        rows = db.fetch_all("SELECT holiday_date, name FROM holidays "
                            "WHERE holiday_date BETWEEN %s AND %s ORDER BY holiday_date",
                            (dt.date(year, 1, 1), dt.date(year, 12, 31)))
    except (TypeError, ValueError):
        rows = db.fetch_all("SELECT holiday_date, name FROM holidays ORDER BY holiday_date")
    return [{"date": r["holiday_date"].isoformat(), "name": r["name"]} for r in rows]


@rpc
def saveHoliday(date, name=""):
    try:
        d = dt.date.fromisoformat(str(date)[:10])
    except ValueError:
        return {"success": False, "message": "தேதி சரியில்லை."}
    db.execute("INSERT INTO holidays (holiday_date, name) VALUES (%s, %s) "
               "ON CONFLICT (holiday_date) DO UPDATE SET name = EXCLUDED.name",
               (d, str(name or "").strip()))
    return {"success": True}


@rpc
def deleteHoliday(date):
    try:
        d = dt.date.fromisoformat(str(date)[:10])
    except ValueError:
        return {"success": False, "message": "தேதி சரியில்லை."}
    db.execute("DELETE FROM holidays WHERE holiday_date = %s", (d,))
    return {"success": True}


# ------------------------------------------------------------------
#  தினக்கூலி விகிதங்கள் (Admin உள்ளிடுவது)
# ------------------------------------------------------------------
WAGE_DEFAULTS = {"partner": 75, "staff": 300, "partTime": 100}
WAGE_KIND = {"partner": "partner", "staff": "staff", "partTime": "part_time"}


@rpc
def getWageRates():
    rates = dict(WAGE_DEFAULTS)
    try:
        saved = {r["kind"]: r["rate"] for r in db.fetch_all("SELECT kind, rate FROM wage_rates")}
    except Exception:
        app.logger.exception("getWageRates")
        saved = {}
    for key, kind in WAGE_KIND.items():
        if kind in saved:
            rates[key] = jnum(saved[kind])
    return rates


@rpc
def saveWageRates(rates):
    rows = []
    for key, kind in WAGE_KIND.items():
        if rates and key in rates and str(rates[key]).strip() != "":
            v = to_num(rates[key])
            if v < 0:
                return {"success": False, "message": "தொகை எதிர்மறையாக இருக்கக்கூடாது."}
            rows.append((kind, v))
    if not rows:
        return {"success": False, "message": "தொகை உள்ளிடவும்."}

    def f(conn):
        with conn.cursor() as cur:
            execute_values(cur, "INSERT INTO wage_rates (kind, rate) VALUES %s "
                                "ON CONFLICT (kind) DO UPDATE SET rate = EXCLUDED.rate", rows)

    db.run(f)
    return {"success": True, "rates": getWageRates()}


@rpc
def getRentDetailsForLibrary(libraryName):
    row = db.fetch_one(
        "SELECT r.owner_name, r.amount FROM rent r JOIN libraries l ON l.id = r.library_id "
        "WHERE l.name = %s", (str(libraryName or "").strip(),))
    if not row:
        return {"success": False}
    return {"success": True, "ownerName": row["owner_name"], "rentAmount": jnum(row["amount"])}


@rpc
def isDailyWagePermissionAllowed(libraryName):
    row = db.fetch_one(
        "SELECT d.workers, l.lib_type FROM daily_wage_permission d JOIN libraries l ON l.id = d.library_id "
        "WHERE l.name = %s", (str(libraryName or "").strip(),))
    w = int(row["workers"]) if row and multi_worker_type(row["lib_type"]) else 1
    return {"success": True, "allowed": bool(row), "workers": w}


@rpc
def getDailyWagePermissionsAdmin():
    """Admin: எல்லா நூலகங்களும் + அனுமதி உள்ளதா + அனுமதிக்கப்பட்ட பணியாளர் எண்ணிக்கை"""
    rows = db.fetch_all(
        "SELECT l.name, l.lib_type, d.workers FROM libraries l "
        "LEFT JOIN daily_wage_permission d ON d.library_id = l.id ORDER BY l.id")
    return [{"name": r["name"], "type": r["lib_type"], "allowed": r["workers"] is not None,
             "multi": multi_worker_type(r["lib_type"]),
             "workers": int(r["workers"]) if r["workers"] is not None and multi_worker_type(r["lib_type"]) else 1}
            for r in rows]


@rpc
def saveDailyWagePermissions(rows):
    libs = {r["name"]: (r["id"], multi_worker_type(r["lib_type"]))
            for r in db.fetch_all("SELECT id, name, lib_type FROM libraries")}
    allow, deny = [], []
    for r in rows or []:
        found = libs.get(str(r.get("name") or "").strip())
        if not found:
            continue
        lid, multi = found
        if r.get("allowed"):
            try:
                w = min(max(int(r.get("workers") or 1), 1), 20) if multi else 1
            except (TypeError, ValueError):
                w = 1
            allow.append((lid, w))
        else:
            deny.append(lid)

    def f(conn):
        with conn.cursor() as cur:
            if deny:
                cur.execute("DELETE FROM daily_wage_permission WHERE library_id = ANY(%s)", (deny,))
            if allow:
                execute_values(cur, "INSERT INTO daily_wage_permission (library_id, workers) VALUES %s "
                               "ON CONFLICT (library_id) DO UPDATE SET workers = EXCLUDED.workers", allow)

    db.run(f)
    return {"success": True, "allowed": len(allow)}


# ------------------------------------------------------------------
#  அறிக்கைகள் (Admin): அனுமதிக்கப்பட்ட பதிவுகளிலிருந்து மட்டும்
# ------------------------------------------------------------------
# (key, தலைப்பு) — அறிக்கை 2 மற்றும் ஆண்டுச் சுருக்கத்தின் நெடுவரிசைகள்
REPORT_COLS = [
    ("papers", "நாளிதழ்கள்"), ("rent", "வாடகை"), ("electricity", "மின் கட்டணம்"),
    ("partner", "தினக்கூலி பகுதி நேரத் துப்புரவாளர்"), ("staff", "தினக்கூலி நூலகர்"),
    ("guard", "தினக்கூலிக் காவலர்"), ("part_time", "தினக்கூலி பகுதி நேர நூலகர்"),
    ("bus", "பேருந்து கட்டணம்"), ("stationery", "எழுது பொருட்கள்"), ("register", "பதிவேடு"),
    ("consumer", "நுகர்பொருள்"), ("electrical", "மின்சாதனங்கள்"), ("postal", "அஞ்சல்"),
    ("copy", "நகலெடுக்கும் கட்டணம்"), ("other1", "பிற செலவினம் 1"), ("other2", "பிற செலவினம் 2"),
    ("petty", "சில்லறை செலவினம்"),      # பார்வைக்கு மட்டும்: பேருந்து கட்டணம் முதல் பிற செலவினம் 2 வரை — மொத்தத்தில் சேராது
    ("travel", "போக்குவரத்து"),
    ("water", "குடிநீர்"), ("phone", "தொலைபேசி"), ("toilet", "கழிவறை சுத்தம்"),
]
# சில்லறை செலவினத்தில் அடங்கும் தலைப்புகள் (பேருந்து கட்டணம் → பிற செலவினம் 2)
PETTY_KEYS = ("bus", "stationery", "register", "consumer", "electrical", "postal", "copy", "other1", "other2")
LIB_TYPE_ORDER = ["மாவட்ட மைய நூலகம்", "முழு நேர கிளை நூலகம்", "கிளை நூலகம்",
                  "ஊர்ப்புற நூலகம்", "பகுதி நேர நூலகம்"]
NO_PAYCENTER = "ஊதிய மையம் குறிப்பிடப்படவில்லை"
MONTH_NAMES_TA = ["ஏப்ரல்", "மே", "ஜூன்", "ஜூலை", "ஆகஸ்ட்", "செப்டம்பர்", "அக்டோபர்",
                  "நவம்பர்", "டிசம்பர்", "ஜனவரி", "பிப்ரவரி", "மார்ச்"]


def _claim_parts(row, paper_total, rates):
    """ஒரு claim-ன் தலைப்பு வாரியான தொகைகள் + மொத்தம் (அனுமதித் தொகை இருந்தால் அதுவே மொத்தம்)."""
    d = lambda v: Decimal(str(v or 0))

    def wage(col, days_col, rk):
        v = d(row.get(col))
        return v if v else d(row.get(days_col)) * d(rates.get(rk))

    p = {
        "papers": d(paper_total), "rent": d(row.get("rent_amount")),
        "electricity": d(row.get("electricity_bill")),
        "partner": wage("partner_amount", "partner_days", "partner"),
        "staff": wage("staff_amount", "staff_days", "staff"),
        "guard": d(row.get("daily_guard_amount")),
        "part_time": wage("part_time_amount", "part_time_staff_days", "partTime"),
        "bus": d(row.get("bus_amount")), "stationery": d(row.get("stationery_amount")),
        "register": d(row.get("register_amount")), "consumer": d(row.get("consumer_amount")),
        "electrical": d(row.get("electrical_amount")), "postal": d(row.get("postal_amount")),
        "copy": d(row.get("photocopy_amount")),
        "other1": d(row.get("other_expense1_amount")), "other2": d(row.get("other_expense2_amount")),
        "travel": d(row.get("travel_amount")), "water": d(row.get("water_tax")),
        "phone": d(row.get("phone_wifi_amount")), "toilet": d(row.get("toilet_cleaning_amount")),
    }
    total = d(row["approved_amount"]) if row.get("approved_amount") is not None else sum(p.values(), Decimal(0))
    p["petty"] = sum((p[k] for k in PETTY_KEYS), Decimal(0))     # மொத்தம் கணக்கிட்ட பின்பே சேர்க்கப்படுகிறது (இரட்டிப்பாகாது)
    return p, total


def _approved_claims(month_years, library_name=None):
    """கொடுத்த மாதங்களின் அனுமதிக்கப்பட்ட claims — [(row, parts, total)] (ஊதிய மையத்துடன்).
    library_name கொடுத்தால் அந்த நூலகத்தின் பதிவுகள் மட்டும்."""
    my = list(month_years)
    sql = ("SELECT c.*, COALESCE(btrim(l.pay_center), '') AS pc, l.id AS lib_id, COALESCE(l.lib_type, c.library_type, '') AS ltype "
           "FROM claims c LEFT JOIN libraries l ON l.name = c.library_name "
           "WHERE c.approved AND c.month_year = ANY(%s)")
    args = [my]
    if library_name:
        sql += " AND c.library_name = %s"
        args.append(library_name)
    rows = db.fetch_all(sql, tuple(args))
    ids = [r["id"] for r in rows]
    papers = {}
    if ids:
        papers = {p["claim_id"]: p["s"] for p in db.fetch_all(
            "SELECT claim_id, SUM(amount) AS s FROM claim_newspapers WHERE claim_id = ANY(%s) GROUP BY claim_id", (ids,))}
    rates = getWageRates()
    out = []
    for r in rows:
        pt = Decimal(str(papers.get(r["id"]) or 0)) + Decimal(str(r["newspaper_total_amount"] or 0))
        parts, total = _claim_parts(r, pt, rates)
        out.append((r, parts, total))
    return out


def _zero_parts():
    return {k: Decimal(0) for k, _ in REPORT_COLS}


def _add_parts(acc, parts, total):
    for k, _ in REPORT_COLS:
        acc["vals"][k] += parts[k]
    acc["total"] += total


def _row_json(label, acc):
    net = acc["total"] - acc["vals"]["papers"] - acc["vals"]["rent"]      # மொத்தம் − நாளிதழ்கள் − வாடகை (நூலகர் அறிக்கையின் அதே கணக்கு)
    return {"label": label, "vals": [jnum(acc["vals"][k]) for k, _ in REPORT_COLS], "net": jnum(net), "total": jnum(acc["total"])}


def _valid_my(m):
    m = str(m or "").strip()
    return m if (len(m) == 7 and m[2] == "/" and m[:2].isdigit() and m[3:].isdigit() and 1 <= int(m[:2]) <= 12) else ""


def _month_label(my):
    mm, yy = int(my[:2]), my[3:]
    return "%s %s" % (MONTH_NAMES_TA[(mm - 4) % 12], yy)


@rpc
def getLibrarianReport(payCenter, monthYear):
    """அறிக்கை 1: ஊதிய மைய நூலகர் அறிக்கை — நூலகம் வாரியாக (நாளிதழ் / வாடகை நீங்கலாக) அனுமதித் தொகை."""
    pc, my = str(payCenter or "").strip(), _valid_my(monthYear)
    if not pc:
        return {"success": False, "message": "ஊதிய மையத்தைத் தேர்ந்தெடுக்கவும்."}
    if not my:
        return {"success": False, "message": "மாதத்தைத் தேர்ந்தெடுக்கவும்."}
    if pc == NO_PAYCENTER:
        libs = db.fetch_all("SELECT id, name, lib_type FROM libraries WHERE btrim(pay_center) = '' ORDER BY id")
    else:
        libs = db.fetch_all("SELECT id, name, lib_type FROM libraries WHERE btrim(pay_center) = %s ORDER BY id", (pc,))
    net = {}
    for r, parts, total in _approved_claims([my]):
        net[r["library_name"]] = net.get(r["library_name"], Decimal(0)) + total - parts["papers"] - parts["rent"]

    def rank(l):
        t = (l["lib_type"] or "").strip()
        return LIB_TYPE_ORDER.index(t) if t in LIB_TYPE_ORDER else len(LIB_TYPE_ORDER)
    libs = sorted(libs, key=lambda l: (rank(l), l["id"]))     # ஒரே வகைக்குள் நூலகப் பட்டியல் (id) வரிசை
    rows, grand = [], Decimal(0)
    for i, l in enumerate(libs, 1):
        amt = net.get(l["name"], Decimal(0))
        grand += amt
        rows.append({"no": i, "name": l["name"], "type": (l["lib_type"] or "").strip(), "amount": jnum(amt)})
    return {"success": True, "payCenter": pc, "month": my, "rows": rows,
            "total": jnum(grand), "inWords": amount_in_words(grand)}


def _paycenter_names():
    return [r["name"] for r in db.fetch_all("SELECT name FROM pay_centers ORDER BY id")]


@rpc
def getPayCenterExpenseReport(monthYear):
    """அறிக்கை 2: ஊதிய மையங்கள் வாரியாகச் செலவினம் (தேர்ந்த மாதம்)."""
    my = _valid_my(monthYear)
    if not my:
        return {"success": False, "message": "மாதத்தைத் தேர்ந்தெடுக்கவும்."}
    names = _paycenter_names()
    acc = {n: {"vals": _zero_parts(), "total": Decimal(0)} for n in names}
    unknown = {"vals": _zero_parts(), "total": Decimal(0)}
    has_unknown = False
    for r, parts, total in _approved_claims([my]):
        if r["pc"] in acc:
            _add_parts(acc[r["pc"]], parts, total)
        else:
            _add_parts(unknown, parts, total)
            has_unknown = True
    rows = [_row_json(n, acc[n]) for n in names]
    if has_unknown:
        rows.append(_row_json(NO_PAYCENTER, unknown))
    grand = {"vals": _zero_parts(), "total": Decimal(0)}
    for a in list(acc.values()) + ([unknown] if has_unknown else []):
        for k, _ in REPORT_COLS:
            grand["vals"][k] += a["vals"][k]
        grand["total"] += a["total"]
    return {"success": True, "month": my, "cols": [{"key": k, "title": t} for k, t in REPORT_COLS],
            "rows": rows, "totals": _row_json("மொத்தம்", grand)}


def _fy_months(fy):
    """'2026-27' -> ['04/2026', ..., '03/2027']; தவறெனில் நடப்பு நிதியாண்டு."""
    fy = str(fy or "").strip()
    try:
        y = int(fy[:4])
        if len(fy) != 7 or fy[4] != "-" or int(fy[5:]) != (y + 1) % 100:
            raise ValueError
    except ValueError:
        now = dt.datetime.now(IST)
        y = now.year if now.month >= 4 else now.year - 1
    return "%d-%02d" % (y, (y + 1) % 100), ["%02d/%d" % (m, y if m >= 4 else y + 1) for m in (4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3)]


@rpc
def getYearlySummaryReport(fy):
    """ஆண்டுச் சுருக்கம்: நிதியாண்டு (ஏப்ரல்–மார்ச்) மாதம் வாரியாக, எல்லா ஊதிய மையங்களின் கூட்டு."""
    fy_label, months = _fy_months(fy)
    acc = {m: {"vals": _zero_parts(), "total": Decimal(0)} for m in months}
    for r, parts, total in _approved_claims(months):
        _add_parts(acc[r["month_year"]], parts, total)
    grand = {"vals": _zero_parts(), "total": Decimal(0)}
    for a in acc.values():
        for k, _ in REPORT_COLS:
            grand["vals"][k] += a["vals"][k]
        grand["total"] += a["total"]
    return {"success": True, "fy": fy_label, "cols": [{"key": k, "title": t} for k, t in REPORT_COLS],
            "rows": [_row_json(_month_label(m), acc[m]) for m in months],
            "totals": _row_json("ஆண்டு மொத்தம்", grand)}


# நூலகர் உள்நுழைவு › 4. நடப்பு ஆண்டு சுருக்கம் – நூலக செலவுகள் (தலைப்புகள் — தொகை உள்ளவை மட்டும் காட்டப்படும்)
LIB_YEAR_COLS = [
    ("papers", "நாளிதழ்கள்"), ("rent", "வாடகை"), ("electricity", "மின் கட்டணம்"),
    ("partner", "தினக்கூலி பகுதிநேரத் துப்புரவாளர்"), ("staff", "தினக்கூலி நூலகர்"),
    ("guard", "தினக்கூலிக் காவலர்"), ("part_time", "தினக்கூலி பகுதிநேர நூலகர்"),
    ("bus", "பேருந்து கட்டணம்"), ("stationery", "எழுது பொருட்கள் வாங்குதல்"),
    ("register", "பதிவேடு வாங்குதல்"), ("consumer", "நுகர்பொருள் வாங்குதல்"),
    ("electrical", "மின்சாதனங்கள் வாங்குதல்"), ("postal", "அஞ்சல் செலவினம்"),
    ("copy", "நகலெடுக்கும் கட்டணம்"), ("other1", "பிற செலவினங்கள் 1"), ("other2", "பிற செலவினங்கள் 2"),
    ("travel", "போக்குவரத்து கட்டணம்"), ("water", "குடிநீர் கட்டணம்"),
    ("phone", "தொலைபேசி கட்டணம்"), ("toilet", "கழிவறை சுத்தம் செய்தல்"),
]


@rpc
def getLibraryYearlyExpense(libraryName, fy=None):
    """ஒரு நூலகத்தின் நடப்பு நிதியாண்டு செலவினச் சுருக்கம் — மாத வாரியாக; அனுமதிக்கப்பட்ட பதிவுகள் மட்டும்;
    ஆண்டு முழுதும் தொகை இல்லாத தலைப்புகள் காட்டப்படாது."""
    name = str(libraryName or "").strip()
    if not name:
        return {"success": False, "message": "நூலக பெயர் கிடைக்கவில்லை."}
    fy_label, months = _fy_months(fy)
    by_month = {}
    for r, parts, total in _approved_claims(months, name):
        a = by_month.setdefault(r["month_year"], {"vals": _zero_parts(), "total": Decimal(0)})
        _add_parts(a, parts, total)
    if not by_month:
        return {"success": False, "message": "நடப்பு நிதியாண்டில் (%s) அனுமதிக்கப்பட்ட செலவினப் பதிவுகள் இல்லை." % fy_label}

    keys = [k for k, _ in LIB_YEAR_COLS if any(by_month[m]["vals"][k] != 0 for m in by_month)]
    cols = [{"key": k, "title": t} for k, t in LIB_YEAR_COLS if k in keys]
    rows, grand_vals, grand_net, grand_total = [], {k: Decimal(0) for k in keys}, Decimal(0), Decimal(0)
    for m in months:                                   # ஏப்ரல் → மார்ச் வரிசை
        a = by_month.get(m)
        if not a:
            continue
        net = a["total"] - a["vals"]["papers"] - a["vals"]["rent"]      # மொத்தம் − நாளிதழ்கள் − வாடகை
        rows.append({"label": "%s-%s" % (m[:2], m[5:]), "vals": [jnum(a["vals"][k]) for k in keys],
                     "net": jnum(net), "total": jnum(a["total"])})
        for k in keys:
            grand_vals[k] += a["vals"][k]
        grand_net += net
        grand_total += a["total"]
    return {"success": True, "library": name, "fy": fy_label, "cols": cols, "rows": rows,
            "totals": {"label": "மொத்தம்", "vals": [jnum(grand_vals[k]) for k in keys],
                       "net": jnum(grand_net), "total": jnum(grand_total)}}


# ------------------------------------------------------------------
#  Vendor சார்ந்த அறிக்கைகள் — நாளிதழ் / வாடகைத் தொகையை vendor-க்கு நேரடியாகச் செலுத்த (அனுமதிக்கப்பட்ட பதிவுகள் மட்டும்)
# ------------------------------------------------------------------
VENDOR_MISSING = {"success": False, "message": "Vendor அட்டவணை இன்னும் உருவாகவில்லை. Render-ஐ மீண்டும் தொடங்கி (Restart) முயலவும்."}


def _vendor_map(kind):
    """{library_id: [vendor, ...]}"""
    by = {}
    for v in db.fetch_all("SELECT library_id, vendor_code, name, rent_share FROM vendors WHERE kind = %s ORDER BY library_id, id", (kind,)):
        by.setdefault(v["library_id"], []).append(v)
    return by


def _lib_order():
    def rank(t):
        t = (t or "").strip()
        return LIB_TYPE_ORDER.index(t) if t in LIB_TYPE_ORDER else len(LIB_TYPE_ORDER)
    return {l["id"]: (rank(l["lib_type"]), l["id"]) for l in db.fetch_all("SELECT id, lib_type FROM libraries")}


def _month_libs(my, field):
    """தேர்ந்த மாதத்தில் அனுமதிக்கப்பட்ட, தொகை > 0 உள்ள நூலகங்கள் (வகை, id வரிசையில்): [{lib_id, name, amount}]"""
    acc = {}
    for r, parts, total in _approved_claims([my]):
        key = r.get("lib_id") if r.get("lib_id") is not None else "n:" + r["library_name"]
        d = acc.setdefault(key, {"lib_id": r.get("lib_id"), "name": r["library_name"], "amount": Decimal(0)})
        d["amount"] += parts[field]
    order = _lib_order()
    last = (len(LIB_TYPE_ORDER) + 1, 10 ** 9)
    return sorted((d for d in acc.values() if d["amount"] > 0), key=lambda d: order.get(d["lib_id"], last))


def _split_amount(amount, weights):
    """தொகையை எடைகளின்படி பிரிக்கும் (கூட்டுத்தொகை சரியாக அமையும்; எடை இல்லையெனில் சமம்)."""
    n, tot = len(weights), sum(weights)
    if tot <= 0:
        weights, tot = [Decimal(1)] * n, Decimal(n)
    shares, acc = [], Decimal(0)
    for i, w in enumerate(weights):
        s = (amount - acc) if i == n - 1 else (amount * w / tot).quantize(Decimal("0.01"))
        shares.append(s)
        acc += s
    return shares


def _vendor_report_base(monthYear):
    my = _valid_my(monthYear)
    return my, (None if my else {"success": False, "message": "மாதத்தைத் தேர்ந்தெடுக்கவும்."})


@rpc
def getPaperVendorExpenseReport(monthYear):
    """அறிக்கை: நூலகங்கள் வாரியாக நாளிதழ்கள் வாங்குதல் செலவினம் (நூலகம் · vendor · code · தொகை)."""
    my, err = _vendor_report_base(monthYear)
    if err:
        return err
    try:
        vend = _vendor_map("paper")
    except Exception:
        return VENDOR_MISSING
    rows, grand, missing = [], Decimal(0), []
    for i, d in enumerate(_month_libs(my, "papers"), 1):
        vs = vend.get(d["lib_id"], [])
        if not vs:
            missing.append(d["name"])
        rows.append({"no": i, "library": d["name"], "vendor": " / ".join(v["name"] for v in vs),
                     "code": " / ".join(v["vendor_code"] for v in vs), "amount": jnum(d["amount"])})
        grand += d["amount"]
    return {"success": True, "month": my, "rows": rows, "total": jnum(grand), "inWords": amount_in_words(grand),
            "missing": missing, "notes": []}


@rpc
def getPaperVendorPaymentReport(monthYear):
    """அறிக்கை: நாளிதழ்கள் vendor-க்குத் தொகை செலுத்துதல் (ஒரு vendor-இன் எல்லா நூலகங்களும் கூட்டப்படும்)."""
    my, err = _vendor_report_base(monthYear)
    if err:
        return err
    try:
        vend = _vendor_map("paper")
    except Exception:
        return VENDOR_MISSING
    agg, unassigned, missing, multi = {}, Decimal(0), [], []
    for d in _month_libs(my, "papers"):
        vs = vend.get(d["lib_id"], [])
        if not vs:
            unassigned += d["amount"]
            missing.append(d["name"])
            continue
        if len(vs) > 1:
            multi.append(d["name"])
        for v, share in zip(vs, _split_amount(d["amount"], [Decimal(1)] * len(vs))):
            a = agg.setdefault(v["vendor_code"], {"vendor": v["name"], "code": v["vendor_code"], "amount": Decimal(0)})
            a["amount"] += share
    rows = [dict(a, no=0) for _, a in sorted(agg.items())]
    if unassigned > 0:
        rows.append({"no": 0, "vendor": "Vendor விவரம் இல்லாத நூலகங்கள்", "code": "", "amount": unassigned})
    grand = sum((r["amount"] for r in rows), Decimal(0))
    for i, r in enumerate(rows, 1):
        r["no"], r["amount"] = i, jnum(r["amount"])
    notes = []
    if multi:
        notes.append("ஒன்றுக்கு மேற்பட்ட நாளிதழ் vendor உள்ள நூலகங்கள் (தொகை சமமாகப் பிரிக்கப்பட்டது): " + ", ".join(multi))
    return {"success": True, "month": my, "rows": rows, "total": jnum(grand), "inWords": amount_in_words(grand),
            "missing": missing, "notes": notes}


@rpc
def getRentPaymentReport(monthYear):
    """அறிக்கை: வாடகை செலுத்துதல் (நூலகம் · vendor · code · தொகை). ஒரு நூலகத்துக்கு 2 vendor இருந்தால் வாடகை சமமாகப் பிரியும்."""
    my, err = _vendor_report_base(monthYear)
    if err:
        return err
    try:
        vend = _vendor_map("rent")
    except Exception:
        return VENDOR_MISSING
    rows, grand, missing = [], Decimal(0), []
    n = 0
    for d in _month_libs(my, "rent"):
        vs = vend.get(d["lib_id"], [])
        if not vs:
            missing.append(d["name"])
            n += 1
            rows.append({"no": n, "library": d["name"], "vendor": "", "code": "", "amount": jnum(d["amount"])})
            grand += d["amount"]
            continue
        for v, share in zip(vs, _split_amount(d["amount"], [Decimal(1)] * len(vs))):
            n += 1
            rows.append({"no": n, "library": d["name"], "vendor": v["name"], "code": v["vendor_code"], "amount": jnum(share)})
            grand += share
    return {"success": True, "month": my, "rows": rows, "total": jnum(grand), "inWords": amount_in_words(grand),
            "missing": missing, "notes": []}


# Excel பதிவிறக்கத்துக்கான அமைப்பு
VENDOR_REPORTS = {
    "papervendor": {"fn": getPaperVendorExpenseReport, "sheet": "நாளிதழ் செலவினம்", "file": "Paper_Expense_Libraries",
                    "title": "நூலகங்கள் வாரியாக நாளிதழ்கள் வாங்குதல் செலவினம்",
                    "heads": ["நூலகத்தின் பெயர்", "Vendor பெயர்", "Vendor Code", "நாளிதழ் தொகை (₹)"],
                    "keys": ["library", "vendor", "code", "amount"], "widths": [8, 44, 36, 20, 20]},
    "paperpay": {"fn": getPaperVendorPaymentReport, "sheet": "நாளிதழ் Vendor தொகை", "file": "Paper_Vendor_Payment",
                 "title": "நாளிதழ்கள் Vendor-க்கு தொகை செலுத்துதல்",
                 "heads": ["Vendor பெயர்", "Vendor Code", "தொகை (₹)"],
                 "keys": ["vendor", "code", "amount"], "widths": [8, 48, 22, 20]},
    "rentpay": {"fn": getRentPaymentReport, "sheet": "வாடகை செலுத்துதல்", "file": "Rent_Payment",
                "title": "வாடகை செலுத்துதல்",
                "heads": ["நூலகத்தின் பெயர்", "Vendor பெயர்", "Vendor Code", "தொகை (₹)"],
                "keys": ["library", "vendor", "code", "amount"], "widths": [8, 44, 36, 20, 20]},
}


@app.get("/admin/report/<kind>.xlsx")
def admin_report_xlsx(kind):
    """அறிக்கை Excel பதிவிறக்கம் (திரையில் உள்ள அதே தரவு)."""
    denied = admin_required_json()
    if denied:
        return denied
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    a = request.args
    if kind == "librarian":
        data = getLibrarianReport(a.get("payCenter", ""), a.get("month", ""))
    elif kind == "paycenter":
        data = getPayCenterExpenseReport(a.get("month", ""))
    elif kind == "yearly":
        data = getYearlySummaryReport(a.get("fy", ""))
    elif kind in VENDOR_REPORTS:
        data = VENDOR_REPORTS[kind]["fn"](a.get("month", ""))
    else:
        return jsonify({"success": False, "message": "தெரியாத அறிக்கை"}), 404
    if not data.get("success"):
        return jsonify(data), 400

    wb = Workbook()
    ws = wb.active
    bold = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="E8EFF9")
    thin = Side(style="thin", color="000000")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    money = "#,##0.00"

    def title(text, ncols, row, size=12):
        ws.cell(row=row, column=1, value=text).font = Font(bold=True, size=size)
        ws.cell(row=row, column=1).alignment = Alignment(horizontal="center")
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)

    if kind == "librarian":
        ws.title = "நூலகர் அறிக்கை"
        title("ஊதிய மையம்: " + data["payCenter"], 3, 1, 13)
        title("மாதம்: " + data["month"], 3, 2, 12)
        for c, h in enumerate(["வ.எண்.", "நூலகத்தின் பெயர்", "மொத்தத் தொகை (₹)"], 1):
            x = ws.cell(row=4, column=c, value=h); x.font = bold; x.fill = head_fill; x.alignment = center; x.border = box
        r = 5
        for row in data["rows"]:
            vals = [row["no"], row["name"], row["amount"]]
            for c, v in enumerate(vals, 1):
                x = ws.cell(row=r, column=c, value=v); x.border = box
                if c == 3: x.number_format = money
                if c == 1: x.alignment = Alignment(horizontal="center")
            r += 1
        ws.cell(row=r, column=2, value="மொத்தம்").font = bold
        t = ws.cell(row=r, column=3, value=data["total"]); t.font = bold; t.number_format = money
        for c in (1, 2, 3): ws.cell(row=r, column=c).border = box
        ws.cell(row=r + 2, column=1, value="In Words: " + data["inWords"])
        for c, w in enumerate([9, 48, 22], 1): ws.column_dimensions[get_column_letter(c)].width = w
        fname = "Librarian_Report_%s_%s.xlsx" % (data["payCenter"], data["month"].replace("/", "-"))
    elif kind in VENDOR_REPORTS:
        spec = VENDOR_REPORTS[kind]
        ws.title = spec["sheet"]
        ncols = len(spec["heads"]) + 1
        title("திண்டுக்கல் மாவட்ட நூலக ஆணைக்குழு", ncols, 1, 14)
        title(spec["title"], ncols, 2, 12)
        title("மாதம்: " + data["month"], ncols, 3, 12)
        for c, h in enumerate(["வ.எண்."] + spec["heads"], 1):
            x = ws.cell(row=5, column=c, value=h); x.font = bold; x.fill = head_fill; x.alignment = center; x.border = box
        r = 6
        for row in data["rows"]:
            for c, v in enumerate([row["no"]] + [row[k] for k in spec["keys"]], 1):
                x = ws.cell(row=r, column=c, value=v); x.border = box
                if c == 1: x.alignment = Alignment(horizontal="center")
                if c == ncols: x.number_format = money
            r += 1
        ws.cell(row=r, column=ncols - 1, value="மொத்தம்").font = bold
        t = ws.cell(row=r, column=ncols, value=data["total"]); t.font = bold; t.number_format = money
        for c in range(1, ncols + 1): ws.cell(row=r, column=c).border = box
        ws.cell(row=r + 2, column=1, value="In Words: " + data["inWords"])
        for c, w in enumerate(spec["widths"], 1): ws.column_dimensions[get_column_letter(c)].width = w
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        fname = "%s_%s.xlsx" % (spec["file"], data["month"].replace("/", "-"))
    else:
        cols = data["cols"]
        n = len(cols) + 4                       # வ.எண். + பெயர் + தலைப்புகள் + மொத்த செலவினம் (நாளிதழ்/வாடகை தவிர்த்து) + மொத்தம்
        if kind == "paycenter":
            ws.title = "ஊதிய மையச் செலவினம்"
            title("திண்டுக்கல் மாவட்ட நூலக ஆணைக்குழு", n, 1, 14)
            title("ஊதிய மையங்கள் வாரியாகச் செலவினம்", n, 2, 12)
            title("மாதம்: " + data["month"], n, 3, 12)
            first = "ஊதிய மையம் பெயர்"
            fname = "PayCentre_Expense_%s.xlsx" % data["month"].replace("/", "-")
        else:
            ws.title = "ஆண்டுச் சுருக்கம்"
            title("திண்டுக்கல் மாவட்ட நூலக ஆணைக்குழு", n, 1, 14)
            title("ஆண்டுச் சுருக்கம் — மாதாந்திரச் செலவினம்", n, 2, 12)
            title("நிதியாண்டு: " + data["fy"], n, 3, 12)
            first = "மாதம்"
            fname = "Yearly_Summary_%s.xlsx" % data["fy"]
        for c, h in enumerate(["வ.எண்.", first] + [x["title"] for x in cols] + ["மொத்த செலவினம் (நாளிதழ் / வாடகை தவிர்த்து)", "மொத்தம்"], 1):
            x = ws.cell(row=5, column=c, value=h); x.font = bold; x.fill = head_fill; x.alignment = center; x.border = box
        ws.row_dimensions[5].height = 75
        r = 6
        for i, row in enumerate(data["rows"], 1):
            vals = [i, row["label"]] + row["vals"] + [row["net"], row["total"]]
            for c, v in enumerate(vals, 1):
                x = ws.cell(row=r, column=c, value=v); x.border = box
                if c > 2: x.number_format = money
                if c == 1: x.alignment = Alignment(horizontal="center")
            r += 1
        tot = data["totals"]
        vals = ["", tot["label"]] + tot["vals"] + [tot["net"], tot["total"]]
        for c, v in enumerate(vals, 1):
            x = ws.cell(row=r, column=c, value=v); x.font = bold; x.border = box; x.fill = head_fill
            if c > 2: x.number_format = money
        ws.column_dimensions["A"].width = 8
        ws.column_dimensions["B"].width = 30
        for c in range(3, n + 1): ws.column_dimensions[get_column_letter(c)].width = 15
        ws.freeze_panes = "C6"
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True

    buf = io.BytesIO()
    wb.save(buf)
    from urllib.parse import quote
    safe = quote(fname.replace("/", "-"))
    return Response(buf.getvalue(),
                    mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=\"report.xlsx\"; filename*=UTF-8''" + safe})


# ------------------------------------------------------------------
#  பக்கங்கள் + Admin உள்நுழைவு
# ------------------------------------------------------------------
DEFAULT_ADMIN_PASSWORD = "Dlodgl@789"   # Render-ல் ADMIN_PASSWORD அமைத்தால் அதுவே முன்னுரிமை
NEXT_OK = {"/", "/admin", "/admin/import"}

with open(os.path.join(BASE, "templates", "login.html"), encoding="utf-8") as _f:
    LANDING = _f.read()


def landing_page(error="", next_url="/", open_admin=False):
    err = f'<p class="err">{escape(error)}</p>' if error else ""
    html = (LANDING.replace("__ERR__", err).replace("__NEXT__", escape(next_url))
            .replace("__OPEN__", "true" if open_admin else "false"))
    return Response(html, mimetype="text/html")


@app.get("/")
def index():
    if not (session.get("admin") or session.get("librarian")):
        return landing_page(open_admin="admin" in request.args)
    is_admin = bool(session.get("admin"))
    if is_admin and "librarian" not in request.args:
        return redirect("/admin")
    page = INDEX_HTML.replace(ADMIN_PLACEHOLDER, "true" if is_admin else "false")
    return Response(page, mimetype="text/html")


@app.post("/login")
def login():
    role = request.form.get("role", "")
    nxt = request.form.get("next", "/")
    if nxt not in NEXT_OK:
        nxt = "/"
    session.clear()
    if role == "librarian":
        session.permanent = True
        session["librarian"] = True
        return redirect("/")            # நூலகருக்கு Admin பக்கம் இல்லை
    if role == "admin":
        expected = os.environ.get("ADMIN_PASSWORD") or DEFAULT_ADMIN_PASSWORD
        given = request.form.get("password", "")
        if hmac.compare_digest(given.encode(), expected.encode()):
            session.permanent = True
            session["admin"] = True
            return redirect("/admin" if nxt in ("/", "/admin/import") else nxt)
        time.sleep(1)                   # தொடர்ந்து யூகிப்பதைத் தடுக்க
        return landing_page("கடவுச்சொல் தவறு", nxt, open_admin=True)
    return redirect("/")


@app.get("/logout")
def logout():
    session.clear()
    return redirect("/")


# ------------------------------------------------------------------
#  Admin — CSV தரவு இறக்கம் (upload)
# ------------------------------------------------------------------
IMPORT_ORDER = ["libraries", "newspapers", "map", "holidays", "leave", "rent", "dailywage", "claims"]

with open(os.path.join(BASE, "templates", "admin.html"), encoding="utf-8") as _f:
    ADMIN_HTML = _f.read()

# Excel கோப்புகள்: வகை -> (கோப்புப் பெயர், தலைப்பு வரிகள், மாதிரி வரி, நெடுவரிசை அகலங்கள்)
# ஏற்கனவே பதிவு உள்ளவற்றை நிரப்பித் தரும்; பதிவு இல்லையெனில் மட்டும் மாதிரி வரி வரும்.
def _hist_template():
    """ஏற்கனவே அனுமதித்த மாதங்கள் — புதிய (தொகை மட்டும்) மாதிரி Excel. மொத்த நெடுவரிசைகள் சூத்திரமாக இருக்கும்."""
    heads = [t for t, _ in importer.HIST_NEW_COLS]
    sample = [""] * len(heads)
    sample[0], sample[1], sample[2] = "04/2025", "எடுத்துக்காட்டு ஊதிய மையம்", "எடுத்துக்காட்டு நூலகம்"
    widths = [12, 26, 34, 14] + [16] * (len(heads) - 4)
    return ("approved_months_template.xlsx", [heads], sample, widths)


HIST_FORMULA_ROWS = 1500      # சூத்திரம் முன்பே நிரப்பப்படும் வரிகள்


def _add_hist_formulas(ws, last_row=HIST_FORMULA_ROWS):
    """U = மொத்தம் (பேருந்து..பிற 2), Z = நாளிதழ்/வாடகை தவிர்த்த மொத்தம், AA = மொத்த செலவினம்."""
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    ix = {k: i + 1 for i, (_, k) in enumerate(importer.HIST_NEW_COLS)}
    L = lambda k: get_column_letter(ix[k])
    fill = PatternFill("solid", fgColor="EEF2FA")
    for r in range(2, last_row + 1):
        lib = f"${L('_lib')}{r}"
        sub = (f"=IF({lib}=\"\",\"\",SUM({L('busAmount')}{r}:{L('photocopyAmount')}{r},"
               f"{L('otherExpense1Amount')}{r},{L('otherExpense2Amount')}{r}))")
        total = (f"=IF({lib}=\"\",\"\",SUM({L('_papers')}{r}:{L('dailyGuardAmount')}{r},"
                 f"{L('partTimeAmount')}{r},{L('_sub')}{r}:{L('toiletCleaningAmount')}{r}))")
        ex = f"=IF({lib}=\"\",\"\",{L('_total')}{r}-{L('_papers')}{r}-{L('rentAmount')}{r})"
        for key, f in (("_sub", sub), ("_total", total), ("_total_ex", ex)):
            c = ws.cell(r, ix[key], f)
            c.fill = fill
            c.font = Font(name="Nirmala UI", bold=True)
        ws.cell(r, 1).number_format = "@"          # 04/2025 தேதியாக மாறாமல் இருக்க


TEMPLATE_XLSX = {
    "claims_approved": _hist_template(),
    "libraries": ("libraries_template.xlsx",
                  [["நூலகத்தின் பெயர்", "நூலக குறியீடு", "மின்னஞ்சல்", "நூலக வகை", "ஊதிய மையம்"]],
                  ["எடுத்துக்காட்டு நூலகம்", "TNDPL00001", "library@example.com", "கிளை நூலகம்", "எடுத்துக்காட்டு ஊதிய மையம்"],
                  [38, 18, 32, 24, 30]),
    "holidays": ("holidays_template.xlsx",
                 [["விடுமுறை தேதி (dd/mm/yyyy)", "விடுமுறை பெயர்"]],
                 ["26/01/2026", "குடியரசு தினம்"],
                 [28, 34]),
    "leave": ("leave_template.xlsx",
              [["நூலகர் விடுப்பு விவரம்", ""], ["நூலகத்தின் பெயர்", "விடுப்பு நாட்கள்"]],
              ["எடுத்துக்காட்டு நூலகம்", 0],
              [38, 18]),
    "dailywage": ("dailywage_template.xlsx",
                  [["தினக்கூலி பணியாளர் அனுமதிக்கப்பட்ட நூலகங்கள்", "பணியாளர் எண்ணிக்கை"]],
                  ["எடுத்துக்காட்டு நூலகம்", 1],
                  [44, 22]),
    "vendors_paper": ("vendors_paper_template.xlsx",
                      [["LIBRARY CODE", "VENDOR CODE", "NAME", "PAN NUMBER", "BANK NAME", "BRANCH", "IFSC CODE", "ACCOUNT NUMBER"]],
                      ["TNDPL00001", "V001", "எடுத்துக்காட்டு Vendor", "ABCDE1234F", "State Bank of India", "Dindigul", "SBIN0000001", "123456789012"],
                      [18, 16, 34, 16, 26, 22, 16, 24]),
    "vendors_rent": ("vendors_rent_template.xlsx",
                     [["LIBRARY CODE", "VENDOR CODE", "NAME", "PAN NUMBER", "BANK NAME", "BRANCH", "IFSC CODE", "ACCOUNT NUMBER"]],
                     ["TNDPL00001", "R001", "எடுத்துக்காட்டு உரிமையாளர்", "", "State Bank of India", "Dindigul", "SBIN0000001", "123456789012"],
                     [18, 16, 34, 16, 26, 22, 16, 24]),
}


def _template_rows(kind):
    """தரவுத்தளத்தில் உள்ள பதிவுகளை Excel வரிகளாகத் தரும் (இல்லையெனில் [])."""
    try:
        if kind == "libraries":
            return [[r["name"], r["code"] or "", r["emails"], r["lib_type"], r["pay_center"]] for r in
                    db.fetch_all("SELECT name, code, emails, lib_type, pay_center FROM libraries ORDER BY id")]
        if kind == "holidays":
            return [[r["holiday_date"].strftime("%d/%m/%Y"), r["name"]] for r in
                    db.fetch_all("SELECT holiday_date, name FROM holidays ORDER BY holiday_date")]
        if kind == "leave":
            return [[r["name"], jnum(r["leave_days"])] for r in
                    db.fetch_all("SELECT l.name, s.leave_days FROM staff_leave_days s "
                                 "JOIN libraries l ON l.id = s.library_id ORDER BY l.id")]
        if kind == "dailywage":
            return [[r["name"], int(r["workers"])] for r in
                    db.fetch_all("SELECT l.name, d.workers FROM daily_wage_permission d "
                                 "JOIN libraries l ON l.id = d.library_id ORDER BY l.id")]
        if kind in ("vendors_paper", "vendors_rent"):
            k = "paper" if kind == "vendors_paper" else "rent"
            out = []
            for r in db.fetch_all("SELECT l.code AS lib_code, v.* FROM vendors v JOIN libraries l ON l.id = v.library_id "
                                  "WHERE v.kind = %s ORDER BY l.id, v.id", (k,)):
                row = [r["lib_code"] or "", r["vendor_code"], r["name"], r["pan_no"], r["bank_name"], r["branch"], r["ifsc"], r["account_no"]]
                out.append(row)
            return out
    except Exception:
        app.logger.exception("template data %s", kind)
    return []


def build_template(kind):
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    fname, heads, sample, widths = TEMPLATE_XLSX[kind]
    data = _template_rows(kind)
    wb = Workbook()
    ws = wb.active
    ws.title = "தரவு"
    for h in heads:
        ws.append(h)
    if data:
        for row in data:
            ws.append(row)
    else:
        ws.append(sample)               # பதிவு இல்லையெனில் மட்டும் மாதிரி வரி
    for r in range(1, len(heads) + 1):
        for c in ws[r]:
            c.font = Font(name="Nirmala UI", bold=True, color="FFFFFF", size=12)
            c.fill = PatternFill("solid", fgColor="12306B")
            c.alignment = Alignment(vertical="center", wrap_text=True)
    if not data:
        for c in ws[len(heads) + 1]:
            c.font = Font(name="Nirmala UI", italic=True, color="808080")
    for i, w in enumerate(widths):
        ws.column_dimensions[get_column_letter(i + 1)].width = w
    ws.freeze_panes = ws.cell(len(heads) + 1, 1)
    # குறியீடு உரையாக இருக்க (முன் பூஜ்யம் போகாமல்); தேதி உரையாகவே இருக்கும்
    if kind == "claims_approved":
        _add_hist_formulas(ws)
    if kind == "libraries":
        for r in range(len(heads) + 1, max(ws.max_row, 400) + 1):
            ws.cell(r, 2).number_format = "@"
    if kind in ("vendors_paper", "vendors_rent"):      # குறியீடு / PAN / IFSC / கணக்கு எண் உரையாகவே இருக்க (முன் பூஜ்யம் போகாமல்)
        for r in range(len(heads) + 1, max(ws.max_row, 400) + 1):
            for c in range(1, 9):
                ws.cell(r, c).number_format = "@"
    buf = io.BytesIO()
    wb.save(buf)
    return fname, buf.getvalue()


@app.get("/admin")
def admin_home():
    if not session.get("admin"):
        return landing_page(next_url="/admin", open_admin=True)
    return Response(ADMIN_HTML, mimetype="text/html")


@app.get("/admin/import")
def admin_import_page():
    return redirect("/admin#libraries")


@app.get("/admin/api/summary")
def admin_summary():
    denied = admin_required_json()
    if denied:
        return denied
    try:
        types = db.fetch_all("SELECT COALESCE(NULLIF(btrim(lib_type), ''), '(வகை இல்லை)') AS t, COUNT(*) AS n "
                             "FROM libraries GROUP BY 1 ORDER BY MIN(id)")
        tot = db.fetch_one("SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE emails <> '') AS with_email, "
                           "COUNT(*) FILTER (WHERE code IS NOT NULL) AS with_code FROM libraries")
        papers = db.fetch_one("SELECT COUNT(*) FILTER (WHERE name <> '') AS n FROM newspapers")
        return jsonify({"success": True, "libraries": tot["n"], "withEmail": tot["with_email"],
                        "withCode": tot["with_code"], "newspapers": papers["n"] if papers else 0,
                        "types": [{"type": r["t"], "count": r["n"]} for r in types]})
    except Exception as e:
        app.logger.exception("summary")
        hint = "Database அட்டவணைகள் இன்னும் உருவாக்கப்படவில்லை." if "does not exist" in str(e) else "Database பிழை."
        return jsonify({"success": False, "message": hint})


@app.get("/admin/api/libraries")
def admin_libraries():
    denied = admin_required_json()
    if denied:
        return denied
    try:
        rows = db.fetch_all("SELECT id, name, code, emails, lib_type, pay_center FROM libraries ORDER BY id")
    except Exception:
        return jsonify({"success": False, "message": "நூலகப் பட்டியலைப் படிக்க முடியவில்லை"})
    return jsonify({"success": True, "rows": [
        {"id": r["id"], "name": r["name"], "code": r["code"] or "", "emails": r["emails"],
         "type": r["lib_type"], "payCenter": r["pay_center"]} for r in rows]})


@app.get("/admin/template/<kind>.xlsx")
def admin_template(kind):
    if not session.get("admin"):
        return redirect("/admin")
    if kind not in TEMPLATE_XLSX:
        return Response("இல்லை", status=404)
    fname, data = build_template(kind)
    return Response(data, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})


def admin_required_json():
    if not session.get("admin"):
        return jsonify({"success": False, "message": "Admin உள்நுழைவு தேவை"}), 401
    return None


@app.post("/admin/import/<kind>")
def admin_import_run(kind):
    denied = admin_required_json()
    if denied:
        return denied
    try:
        if kind == "schema":
            importer.do_schema()
            return jsonify({"success": True, "message": "Database அட்டவணைகள் தயார்", "skipped": [], "skipped_count": 0})
        if kind not in importer.IMPORTERS:
            return jsonify({"success": False, "message": "தெரியாத வகை: " + kind}), 404
        f = request.files.get("file")
        raw = f.read() if f else b""
        if not raw:
            return jsonify({"success": False, "message": "கோப்பு எதுவும் வரவில்லை"})
        res = importer.IMPORTERS[kind][0](raw)
        msg = f"{res['done']} பதிவுகள் ஏற்றப்பட்டன"
        if "inserted" in res:
            msg += f" (புதியவை {res['inserted']}, புதுப்பிக்கப்பட்டவை {res['updated']})"
        return jsonify({"success": True, "message": msg,
                        "skipped": res["skipped"][:50], "skipped_count": len(res["skipped"])})
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)})
    except Exception as e:
        app.logger.exception("இறக்கம் தோல்வி: %s", kind)
        hint = "libraries அட்டவணை இல்லையெனில் முதலில் 'Database அட்டவணைகளை உருவாக்கு' அழுத்தவும்." \
            if "does not exist" in str(e) else ""
        return jsonify({"success": False, "message": "இறக்கம் தோல்வி. " + hint + " (" + type(e).__name__ + ")"})


# ------------------------------------------------------------------
#  Admin — நூலகம் / வகை / ஊதிய மையம்: சேர்க்க · திருத்த · நீக்க
# ------------------------------------------------------------------
LIST_TABLES = {"types": ("library_types", "lib_type"), "paycenters": ("pay_centers", "pay_center")}


def _ok(**kw):
    return jsonify({"success": True, **kw})


def _bad(msg):
    return jsonify({"success": False, "message": msg})


def _clean(v):
    return " ".join(str(v or "").split())


@app.get("/admin/api/lists")
def admin_lists():
    denied = admin_required_json()
    if denied:
        return denied
    out = {}
    for key, (table, col) in LIST_TABLES.items():
        rows = db.fetch_all(
            f"SELECT t.id, t.name, (SELECT COUNT(*) FROM libraries l WHERE btrim(l.{col}) = t.name) AS n "
            f"FROM {table} t ORDER BY t.id")
        out[key] = [{"id": r["id"], "name": r["name"], "count": r["n"]} for r in rows]
    return _ok(**out)


@app.post("/admin/api/list/<kind>/save")
def admin_list_save(kind):
    denied = admin_required_json()
    if denied:
        return denied
    if kind not in LIST_TABLES:
        return _bad("தெரியாத பட்டியல்")
    table, col = LIST_TABLES[kind]
    j = request.get_json(silent=True) or {}
    name = _clean(j.get("name"))
    if not name:
        return _bad("பெயரை உள்ளிடவும்.")
    lid = j.get("id")
    try:
        if lid:
            old = db.fetch_one(f"SELECT name FROM {table} WHERE id = %s", (int(lid),))
            if not old:
                return _bad("பதிவு கிடைக்கவில்லை.")

            def f(conn):
                with conn.cursor() as cur:
                    cur.execute(f"UPDATE {table} SET name = %s WHERE id = %s", (name, int(lid)))
                    # பெயர் மாற்றம் அந்த நூலகங்கள் அனைத்துக்கும் பொருந்தும்
                    cur.execute(f"UPDATE libraries SET {col} = %s WHERE btrim({col}) = %s", (name, old["name"]))
                    if kind == "types":
                        cur.execute("UPDATE claims SET library_type = %s WHERE btrim(library_type) = %s",
                                    (name, old["name"]))
            db.run(f)
        else:
            db.execute(f"INSERT INTO {table} (name) VALUES (%s)", (name,))
    except Exception as e:
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            return _bad("இந்தப் பெயர் ஏற்கனவே உள்ளது.")
        app.logger.exception("list save")
        return _bad("சேமிக்க முடியவில்லை.")
    return _ok()


@app.post("/admin/api/list/<kind>/delete")
def admin_list_delete(kind):
    denied = admin_required_json()
    if denied:
        return denied
    if kind not in LIST_TABLES:
        return _bad("தெரியாத பட்டியல்")
    table, col = LIST_TABLES[kind]
    j = request.get_json(silent=True) or {}
    row = db.fetch_one(f"SELECT name FROM {table} WHERE id = %s", (int(j.get("id") or 0),))
    if not row:
        return _bad("பதிவு கிடைக்கவில்லை.")
    n = db.fetch_one(f"SELECT COUNT(*) AS n FROM libraries WHERE btrim({col}) = %s", (row["name"],))["n"]
    if n:
        return _bad(f"இதை {n} நூலகங்கள் பயன்படுத்துகின்றன — முதலில் அந்த நூலகங்களை மாற்றவும் / நீக்கவும்.")
    db.execute(f"DELETE FROM {table} WHERE name = %s", (row["name"],))
    return _ok()


@app.post("/admin/api/library/save")
def admin_library_save():
    """id இருந்தால் திருத்தம்; இல்லையெனில் புதிய நூலகம்."""
    denied = admin_required_json()
    if denied:
        return denied
    j = request.get_json(silent=True) or {}
    name = _clean(j.get("name"))
    code = _clean(j.get("code")).upper() or None
    emails = ",".join(e.strip() for e in str(j.get("emails") or "").lower().replace(";", ",").replace("\n", ",").split(",")
                      if e.strip())
    ltype, pay = _clean(j.get("type")), _clean(j.get("payCenter"))
    if not name:
        return _bad("நூலகப் பெயரை உள்ளிடவும்.")
    if emails and any("@" not in e for e in emails.split(",")):
        return _bad("மின்னஞ்சல் முகவரி சரியில்லை.")
    lid = int(j["id"]) if j.get("id") else None
    if db.fetch_one("SELECT 1 FROM libraries WHERE name = %s AND id IS DISTINCT FROM %s", (name, lid)):
        return _bad("இந்தப் பெயரில் நூலகம் ஏற்கனவே உள்ளது.")
    if code and db.fetch_one("SELECT 1 FROM libraries WHERE code = %s AND id IS DISTINCT FROM %s", (code, lid)):
        return _bad("இந்தக் குறியீடு வேறு நூலகத்துக்கு உள்ளது.")

    def f(conn):
        with conn.cursor() as cur:
            if ltype:
                cur.execute("INSERT INTO library_types (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (ltype,))
            if pay:
                cur.execute("INSERT INTO pay_centers (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (pay,))
            if lid:
                cur.execute("SELECT name FROM libraries WHERE id = %s", (lid,))
                old = cur.fetchone()
                if not old:
                    return False
                cur.execute("UPDATE libraries SET name=%s, code=%s, emails=%s, lib_type=%s, pay_center=%s WHERE id=%s",
                            (name, code, emails, ltype, pay, lid))
                if old[0] != name:      # ஏற்கனவே உள்ள contingent பதிவுகளும் புதிய பெயருடன் இணைந்திருக்க
                    cur.execute("UPDATE claims SET library_name = %s WHERE library_name = %s", (name, old[0]))
            else:
                cur.execute("INSERT INTO libraries (name, code, emails, lib_type, pay_center) "
                            "VALUES (%s,%s,%s,%s,%s)", (name, code, emails, ltype, pay))
        return True
    if not db.run(f):
        return _bad("நூலகம் கிடைக்கவில்லை.")
    return _ok()


@app.post("/admin/api/library/delete")
def admin_library_delete():
    denied = admin_required_json()
    if denied:
        return denied
    j = request.get_json(silent=True) or {}
    row = db.fetch_one("SELECT name FROM libraries WHERE id = %s", (int(j.get("id") or 0),))
    if not row:
        return _bad("நூலகம் கிடைக்கவில்லை.")
    claims = db.fetch_one("SELECT COUNT(*) AS n FROM claims WHERE library_name = %s", (row["name"],))["n"]
    if claims and not j.get("force"):
        return jsonify({"success": False, "needConfirm": True, "claims": claims,
                        "message": f"இந்த நூலகத்துக்கு {claims} contingent பதிவுகள் உள்ளன. நீக்கினாலும் அவை அழியாது; "
                                   f"ஆனால் நூலகப் பட்டியலில் இருந்து நீங்கும். தொடரவா?"})
    db.execute("DELETE FROM libraries WHERE id = %s", (int(j["id"]),))   # இணைப்பு / வாடகை / விடுப்பு தானாக நீங்கும்
    return _ok()


# ------------------------------------------------------------------
#  Admin — Vendor விவரம்: பட்டியல் · சேர்க்க / திருத்த · நீக்க
# ------------------------------------------------------------------
import re as _re
_PAN_RE = _re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_IFSC_RE = _re.compile(r"^[A-Z]{4}[0-9A-Z]{7}$")
_VENDOR_SELECT = "SELECT v.*, l.name AS lib_name, l.code AS lib_code FROM vendors v JOIN libraries l ON l.id = v.library_id "


def _vendor_json(r):
    return {"id": r["id"], "kind": r["kind"], "libraryId": r["library_id"], "library": r["lib_name"],
            "libraryCode": r["lib_code"] or "", "code": r["vendor_code"], "name": r["name"], "pan": r["pan_no"],
            "bank": r["bank_name"], "branch": r["branch"], "ifsc": r["ifsc"], "account": r["account_no"],
            "share": jnum(r["rent_share"])}


@app.get("/admin/api/vendors")
def admin_vendors():
    denied = admin_required_json()
    if denied:
        return denied
    where, params = [], []
    kind, lib = request.args.get("kind", ""), request.args.get("library", "")
    if kind in ("paper", "rent"):
        where.append("v.kind = %s")
        params.append(kind)
    if lib.isdigit():
        where.append("v.library_id = %s")
        params.append(int(lib))
    sql = _VENDOR_SELECT + ("WHERE " + " AND ".join(where) + " " if where else "") + "ORDER BY l.id, v.kind, v.id"
    try:
        rows = db.fetch_all(sql, tuple(params))
    except Exception:
        app.logger.exception("vendors list")
        return _bad("Vendor பட்டியலைப் படிக்க முடியவில்லை — Render-ஐ மீண்டும் தொடங்கி (Restart) முயலவும்.")
    return _ok(rows=[_vendor_json(r) for r in rows])


@app.post("/admin/api/vendor/save")
def admin_vendor_save():
    denied = admin_required_json()
    if denied:
        return denied
    j = request.get_json(silent=True) or {}
    vid = int(j["id"]) if j.get("id") else None
    kind = _clean(j.get("kind"))
    try:
        lib_id = int(j.get("libraryId") or 0)
    except ValueError:
        lib_id = 0
    code = "".join(str(j.get("code") or "").split()).upper()
    name = _clean(j.get("name"))
    pan = _clean(j.get("pan"))
    ifsc = _clean(j.get("ifsc"))
    account = "".join(str(j.get("account") or "").replace("-", "").split())
    bank, branch = _clean(j.get("bank")), _clean(j.get("branch"))
    share = to_num(j.get("share"))
    if not vid and kind not in ("paper", "rent"):
        return _bad("Vendor வகை சரியில்லை.")
    if not db.fetch_one("SELECT 1 FROM libraries WHERE id = %s", (lib_id,)):
        return _bad("நூலகத்தைத் தேர்ந்தெடுக்கவும்.")
    if not code:
        return _bad("Vendor Code உள்ளிடவும்.")
    if not name:
        return _bad("Vendor பெயரை உள்ளிடவும்.")

    def f(conn):
        with conn.cursor() as cur:
            k = kind
            if vid:
                cur.execute("SELECT kind, vendor_code FROM vendors WHERE id = %s", (vid,))
                old = cur.fetchone()
                if not old:
                    return None
                k = old[0]
            cur.execute("SELECT 1 FROM vendors WHERE kind = %s AND library_id = %s AND vendor_code = %s "
                        "AND id IS DISTINCT FROM %s", (k, lib_id, code, vid))
            if cur.fetchone():
                return "dup"
            if vid:
                cur.execute("UPDATE vendors SET library_id=%s, vendor_code=%s, name=%s, pan_no=%s, bank_name=%s, branch=%s, "
                            "ifsc=%s, account_no=%s, rent_share=%s, updated_at=now() WHERE id=%s",
                            (lib_id, code, name, pan, bank, branch, ifsc, account, share, vid))
                if j.get("applyAll"):      # அதே Vendor Code உள்ள மற்ற நூலகப் பதிவுகளிலும் வங்கி விவரம் புதுப்பிப்பு
                    cur.execute("UPDATE vendors SET name=%s, pan_no=%s, bank_name=%s, branch=%s, ifsc=%s, account_no=%s, "
                                "updated_at=now() WHERE kind=%s AND vendor_code=%s AND id<>%s",
                                (name, pan, bank, branch, ifsc, account, k, old[1], vid))
                    return cur.rowcount
                return 0
            cur.execute("INSERT INTO vendors (kind, library_id, vendor_code, name, pan_no, bank_name, branch, ifsc, account_no, rent_share) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (k, lib_id, code, name, pan, bank, branch, ifsc, account, share))
            return 0
    res = db.run(f)
    if res is None:
        return _bad("Vendor பதிவு கிடைக்கவில்லை.")
    if res == "dup":
        return _bad("இந்த நூலகத்துக்கு இதே Vendor Code ஏற்கனவே உள்ளது.")
    return _ok(others=res)


@app.post("/admin/api/vendor/delete")
def admin_vendor_delete():
    denied = admin_required_json()
    if denied:
        return denied
    j = request.get_json(silent=True) or {}
    db.execute("DELETE FROM vendors WHERE id = %s", (int(j.get("id") or 0),))
    return _ok()


@app.get("/healthz")
def healthz():
    return "ok"


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", "5000")))
