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
import importer
from common import (CLAIM_NUM, CLAIM_TEXT, NEWSPAPER_KEYS, TOTAL_SLOTS, jnum,
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
              "saveWageRates", "saveHoliday", "deleteHoliday"}

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
    return {"success": True, "rowIndex": row["id"], "data": data}


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
        "SELECT 1 AS ok FROM daily_wage_permission d JOIN libraries l ON l.id = d.library_id "
        "WHERE l.name = %s", (str(libraryName or "").strip(),))
    return {"success": True, "allowed": bool(row)}


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

# மாதிரி Excel கோப்புகள்: வகை -> (கோப்புப் பெயர், தலைப்பு வரிகள், மாதிரி வரி, நெடுவரிசை அகலங்கள்)
TEMPLATE_XLSX = {
    "libraries": ("libraries_template.xlsx",
                  [["நூலகத்தின் பெயர்", "நூலக குறியீடு", "மின்னஞ்சல்", "நூலக வகை"]],
                  ["எடுத்துக்காட்டு நூலகம்", "TNDPL00001", "library@example.com", "கிளை நூலகம்"],
                  [38, 18, 32, 24]),
    "holidays": ("holidays_template.xlsx",
                 [["விடுமுறை தேதி (dd/mm/yyyy)", "விடுமுறை பெயர்"]],
                 ["26/01/2026", "குடியரசு தினம்"],
                 [28, 34]),
    "leave": ("leave_template.xlsx",
              [["நூலகர் விடுப்பு விவரம்", ""], ["நூலகத்தின் பெயர்", "விடுப்பு நாட்கள்"]],
              ["எடுத்துக்காட்டு நூலகம்", 0],
              [38, 18]),
    "dailywage": ("dailywage_template.xlsx",
                  [["தினக்கூலி பணியாளர் அனுமதிக்கப்பட்ட நூலகங்கள்"]],
                  ["எடுத்துக்காட்டு நூலகம்"],
                  [44]),
}


def build_template(kind):
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    fname, heads, sample, widths = TEMPLATE_XLSX[kind]
    wb = Workbook()
    ws = wb.active
    ws.title = "தரவு"
    for h in heads:
        ws.append(h)
    ws.append(sample)
    for r in range(1, len(heads) + 1):
        for c in ws[r]:
            c.font = Font(name="Nirmala UI", bold=True, color="FFFFFF", size=12)
            c.fill = PatternFill("solid", fgColor="12306B")
            c.alignment = Alignment(vertical="center", wrap_text=True)
    for c in ws[len(heads) + 1]:
        c.font = Font(name="Nirmala UI", italic=True, color="808080")
    for i, w in enumerate(widths):
        ws.column_dimensions[chr(65 + i)].width = w
    # குறியீடு, எண் புலங்கள் உரையாக இருக்க (முன் பூஜ்யம் போகாமல்)
    if kind == "libraries":
        for r in range(2, 400):
            ws.cell(r, 2).number_format = "@"
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
        rows = db.fetch_all("SELECT name, code, emails, lib_type FROM libraries ORDER BY id")
    except Exception:
        return jsonify({"success": False, "message": "நூலகப் பட்டியலைப் படிக்க முடியவில்லை"})
    return jsonify({"success": True, "rows": [
        {"name": r["name"], "code": r["code"] or "", "emails": r["emails"], "type": r["lib_type"]} for r in rows]})


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


@app.get("/healthz")
def healthz():
    return "ok"


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", "5000")))
