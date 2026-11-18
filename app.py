"""நூலக Contingent / வரவினங்கள் — Flask backend (Neon PostgreSQL).

பழைய Google Apps Script function-கள் இங்கே அதே பெயரில், அதே return வடிவில் உள்ளன.
HTML-ல் உள்ள google.script.run அழைப்புகள் (shim மூலம்) /rpc/<பெயர்> க்கு வரும்.
"""
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


# admin மட்டும் சேமிக்கக்கூடியவை
ADMIN_ONLY = {"saveNewspaperMasterList", "saveLibraryNewspaperMap"}

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
@rpc
def getLibraryTypes():
    return ["மாவட்ட மைய நூலகம்", "முழு நேர கிளை நூலகம்", "கிளை நூலகம்",
            "ஊர்ப்புற நூலகம்", "பகுதி நேர நூலகம்"]


@rpc
def getLibrariesByType(type):
    rows = db.fetch_all("SELECT name FROM libraries WHERE lib_type = %s ORDER BY id", (str(type).strip(),))
    return [r["name"] for r in rows]


@rpc
def getEmailsForLibrary(libraryName):
    row = db.fetch_one("SELECT name, emails FROM libraries WHERE name = %s",
                       (str(libraryName or "").strip(),))
    emails = []
    if row:
        emails = [e.strip() for e in row["emails"].lower().split(",") if "@" in e]
    if not row or not emails:
        return {"success": False, "message": "நூலகம் கிடைக்கவில்லை அல்லது மின்னஞ்சல் இல்லை"}
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
#  நாளிதழ் (Active பட்டியல் + Admin)
# ------------------------------------------------------------------
@rpc
def getActiveNewspapersForLibrary(libraryName):
    rows = db.fetch_all(
        "SELECT n.slot, n.name, n.max_rate FROM libraries l "
        "JOIN library_newspaper_map m ON m.library_id = l.id AND m.active "
        "JOIN newspapers n ON n.slot = m.slot "
        "WHERE l.name = %s AND n.name <> '' ORDER BY n.slot",
        (str(libraryName or "").strip(),))
    return [{"slot": r["slot"], "key": NEWSPAPER_KEYS[r["slot"] - 1],
             "name": r["name"], "maxRate": jnum(r["max_rate"])} for r in rows]


@rpc
def getNewspaperAdminMasterList():
    rows = db.fetch_all("SELECT slot, name, max_rate FROM newspapers ORDER BY slot")
    return [{"slot": r["slot"], "name": r["name"], "maxRate": jnum(r["max_rate"])} for r in rows]


@rpc
def saveNewspaperMasterList(rows):
    items = {}
    for r in rows or []:
        try:
            slot = int(r.get("slot"))
        except (TypeError, ValueError):
            continue
        if 1 <= slot <= TOTAL_SLOTS:
            items[slot] = (slot, str(r.get("name") or "").strip(), to_num(r.get("maxRate")))

    def f(conn):
        if items:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    "INSERT INTO newspapers (slot, name, max_rate) VALUES %s "
                    "ON CONFLICT (slot) DO UPDATE SET name = EXCLUDED.name, max_rate = EXCLUDED.max_rate",
                    list(items.values()))

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


def count_working_days(start, end, holidays):
    """வெள்ளி, இரண்டாம் சனி (8-14), அரசு விடுமுறை நீங்கலாக."""
    total, d = 0, start
    while d <= end:
        is_friday = d.weekday() == 4
        is_second_sat = d.weekday() == 5 and 7 < d.day <= 14
        if not (is_friday or is_second_sat or d in holidays):
            total += 1
        d += dt.timedelta(days=1)
    return total


@rpc
def getWorkingDaysCount(libName):
    start, end = working_period(dt.datetime.now(IST).date())
    holidays = {r["holiday_date"] for r in db.fetch_all(
        "SELECT holiday_date FROM holidays WHERE holiday_date BETWEEN %s AND %s", (start, end))}
    total = count_working_days(start, end, holidays)
    row = db.fetch_one(
        "SELECT s.leave_days FROM staff_leave_days s JOIN libraries l ON l.id = s.library_id "
        "WHERE l.name = %s", (str(libName or "").strip(),))
    leave = jnum(row["leave_days"]) if row else 0
    return {
        "totalWD": total,
        "partnerLimit": total - leave,
        "staffLimit": total - leave,
        "leaveTaken": leave,
        "period": f"{TAMIL_MONTHS[start.month - 1]} {start.day} முதல் {TAMIL_MONTHS[end.month - 1]} {end.day} முடிய",
    }


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
NEXT_OK = {"/", "/admin/import"}

LANDING = r"""<!DOCTYPE html><html lang="ta"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>உள்நுழைவு — மாவட்ட நூலக அலுவலகம்</title>
<style>
*{box-sizing:border-box}
body{font-family:Segoe UI,Latha,Arial,sans-serif;background:linear-gradient(135deg,#eef2ff,#e0e7ff);margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:16px}
.card{background:#fff;width:100%;max-width:380px;padding:28px 24px;border-radius:16px;box-shadow:0 8px 28px rgba(30,41,59,.18);text-align:center}
h2{margin:0;color:#1e3a8a}h4{margin:4px 0 20px;color:#475569;font-weight:600}
.role{display:block;width:100%;padding:14px;margin-bottom:12px;border:0;border-radius:10px;font-size:17px;font-weight:700;color:#fff;cursor:pointer}
.lib{background:#16a34a}.adm{background:#7c3aed}
#adminBox{display:none;text-align:left;margin-top:6px;border-top:1px solid #e2e8f0;padding-top:14px}
input[type=password]{width:100%;padding:11px;border:1.5px solid #cbd5e1;border-radius:8px;font-size:16px;margin-bottom:10px}
.go{background:#2563eb;margin-bottom:6px}.err{color:#b91c1c;font-weight:700;margin:0 0 12px}
</style></head><body><div class="card">
<h2>📚 மாவட்ட நூலக அலுவலகம்</h2><h4>திண்டுக்கல் மாவட்டம் — உள்நுழைவு</h4>
__ERR__
<form method="post" action="/login"><input type="hidden" name="next" value="__NEXT__">
  <button class="role lib" name="role" value="librarian" id="libBtn">👩‍💼 நூலகர் (Librarian)</button>
</form>
<button class="role adm" type="button" onclick="document.getElementById('adminBox').style.display='block';document.getElementById('pw').focus()">🔑 Admin</button>
<form id="adminBox" method="post" action="/login"><input type="hidden" name="next" value="__NEXT__">
  <input type="hidden" name="role" value="admin">
  <input type="password" id="pw" name="password" placeholder="Admin கடவுச்சொல்" autocomplete="current-password" required>
  <button class="role go">உள்நுழை</button>
</form></div>
<script>if (__OPEN__) { document.getElementById('adminBox').style.display='block'; document.getElementById('pw').focus(); }</script>
</body></html>"""


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
            return redirect(nxt)
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

IMPORT_PAGE = r"""<!DOCTYPE html><html lang="ta"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Admin — தரவு இறக்கம்</title>
<style>
body{font-family:Segoe UI,Arial,sans-serif;background:#eef2ff;margin:0;padding:20px;color:#1e293b}
.box{max-width:820px;margin:0 auto;background:#fff;padding:24px;border-radius:12px;box-shadow:0 2px 10px rgba(0,0,0,.12)}
h2{margin-top:0}.note{background:#fef3c7;border:1px solid #f59e0b;border-radius:8px;padding:10px 14px;font-size:14px}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:12px 0;border-bottom:1px solid #e2e8f0}
.row b{flex:1 1 100%}.row input[type=file]{flex:1 1 240px;font-size:14px}
button{padding:9px 16px;border:0;border-radius:8px;font-weight:700;cursor:pointer;color:#fff;background:#2563eb}
button:disabled{opacity:.5;cursor:wait}.alt{background:#7c3aed}.gray{background:#64748b}
.st{flex:1 1 100%;font-size:14px;white-space:pre-wrap}.ok{color:#15803d}.bad{color:#b91c1c}.warn{color:#b45309}
.top{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}a{color:#2563eb}
</style></head><body><div class="box">
<h2>📥 தரவு இறக்கம் (CSV Upload)</h2>
<p><a href="/?admin">← Admin முகப்புக்கு</a></p>
<div class="note">ஒவ்வொரு Google Sheet-ஐயும் <b>File ▸ Download ▸ CSV</b> ஆகப் பதிவிறக்கி இங்கே தேர்ந்தெடுக்கவும்.
முதலில் <b>நூலகப் பட்டியல்</b> இருக்க வேண்டும்; அதன்பின் மற்றவை. மீண்டும் upload செய்தால் புதுப்பிக்கும் (நகல் உருவாகாது).</div>
<div class="top">
  <button class="gray" id="schemaBtn" onclick="runSchema()">🗄 Database அட்டவணைகளை உருவாக்கு (முதல் முறை மட்டும்)</button>
  <button class="alt" id="allBtn" onclick="runAll()">▶ தேர்ந்தெடுத்த அனைத்தையும் வரிசையாக ஏற்று</button>
</div>
<div id="schemaSt" class="st"></div>
__ROWS__
</div>
<script>
const ORDER = __ORDER__;
async function post(url, fd){
  const r = await fetch(url, {method:'POST', body: fd});
  if (r.status === 401) { location.href = '/admin/import'; throw new Error('உள்நுழைவு தேவை'); }
  let j; try { j = await r.json(); } catch(e) { throw new Error('Server பதில் புரியவில்லை (HTTP '+r.status+')'); }
  return j;
}
function show(el, j){
  if (!j.success) { el.className='st bad'; el.textContent = '❌ ' + j.message; return false; }
  let t = '✅ ' + j.message;
  if (j.skipped && j.skipped.length) {
    t += '\n⚠️ தவிர்க்கப்பட்டவை: ' + j.skipped_count + '\n  - ' + j.skipped.join('\n  - ');
    if (j.skipped_count > j.skipped.length) t += '\n  ... மேலும் ' + (j.skipped_count - j.skipped.length);
  }
  el.className = 'st ' + (j.skipped_count ? 'warn' : 'ok'); el.textContent = t; return true;
}
async function runSchema(){
  const b = document.getElementById('schemaBtn'), el = document.getElementById('schemaSt');
  b.disabled = true; el.className='st'; el.textContent='⏳ உருவாக்குகிறது...';
  try { show(el, await post('/admin/import/schema', new FormData())); }
  catch(e){ el.className='st bad'; el.textContent='❌ '+e.message; }
  b.disabled = false;
}
async function upload(kind){
  const inp = document.getElementById('f_'+kind), el = document.getElementById('s_'+kind), b = document.getElementById('b_'+kind);
  if (!inp.files.length) { el.className='st bad'; el.textContent='❌ முதலில் CSV கோப்பைத் தேர்ந்தெடுக்கவும்'; return false; }
  const fd = new FormData(); fd.append('file', inp.files[0]);
  b.disabled = true; el.className='st'; el.textContent='⏳ ஏற்றுகிறது... (பெரிய கோப்புக்குச் சற்று நேரம் ஆகலாம்)';
  let ok = false;
  try { ok = show(el, await post('/admin/import/'+kind, fd)); }
  catch(e){ el.className='st bad'; el.textContent='❌ '+e.message; }
  b.disabled = false; return ok;
}
async function runAll(){
  const b = document.getElementById('allBtn'); b.disabled = true;
  for (const k of ORDER) {
    if (document.getElementById('f_'+k).files.length) { if (!(await upload(k))) break; }
  }
  b.disabled = false;
}
</script></body></html>"""


def admin_required_json():
    if not session.get("admin"):
        return jsonify({"success": False, "message": "Admin உள்நுழைவு தேவை"}), 401
    return None


@app.get("/admin/import")
def admin_import_page():
    if not session.get("admin"):
        return landing_page(next_url="/admin/import", open_admin=True)
    rows = "".join(
        f'<div class="row"><b>{i}. {escape(importer.IMPORTERS[k][1])}</b>'
        f'<input type="file" id="f_{k}" accept=".csv,text/csv">'
        f'<button id="b_{k}" onclick="upload(\'{k}\')">ஏற்று</button>'
        f'<div class="st" id="s_{k}"></div></div>'
        for i, k in enumerate(IMPORT_ORDER, start=1))
    page = (IMPORT_PAGE.replace("__ROWS__", rows)
            .replace("__ORDER__", str(IMPORT_ORDER).replace("'", '"')))
    return Response(page, mimetype="text/html")


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
            return jsonify({"success": False, "message": "CSV கோப்பு எதுவும் வரவில்லை"})
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
