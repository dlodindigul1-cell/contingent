"""பணியாளர் விடுப்பு மேலாண்மை — தனித்த Module (Phase 1: Admin தரவுப் பட்டியல்கள்).
பழைய சில்லறை செலவின / வரவின routes-ஐ மாற்றாது."""
import datetime as dt
from flask import Blueprint, jsonify, request, session
import db

leave = Blueprint('leave', __name__)


def _deny():
    if not session.get('admin'):
        return jsonify(success=False, message='Admin உள்நுழைவு தேவை'), 401
    return None


def staff_label(name, no, daily, lib_name):
    """காட்டும் பெயர்: BASKAR-43050328644 / தினக்கூலி-ஊ.49 சமுத்திராபட்டி"""
    if daily:
        return 'தினக்கூலி-' + (lib_name or '')
    return f'{name}-{no}' if no else (name or '')


@leave.get('/admin/api/staff')
def staff_list():
    d = _deny()
    if d: return d
    try:
        rows = db.fetch_all(
            "SELECT s.id, s.staff_name, s.staff_no, s.is_daily_wage, s.seq, s.active, l.name AS lib, l.lib_type, l.is_test, l.is_office, "
            "(SELECT COUNT(*) FROM staff_leave_records r WHERE r.staff_id = s.id) AS recs "
            "FROM library_staff s JOIN libraries l ON l.id = s.library_id "
            "ORDER BY l.is_test, l.id, s.is_daily_wage, s.id")
    except Exception as e:
        hint = 'Database அட்டவணைகள் இன்னும் உருவாக்கப்படவில்லை (Admin 1 → அட்டவணைகளை உருவாக்கு).' if 'does not exist' in str(e) else 'Database பிழை.'
        return jsonify(success=False, message=hint)
    return jsonify(success=True, rows=[{
        'id': r['id'], 'library': r['lib'], 'type': r['lib_type'], 'name': r['staff_name'], 'no': r['staff_no'],
        'daily': r['is_daily_wage'], 'active': r['active'], 'records': r['recs'], 'test': bool(r['is_test']) and not r['is_office'], 'office': bool(r['is_office']),
        'label': staff_label(r['staff_name'], r['staff_no'], r['is_daily_wage'], r['lib'])} for r in rows])


@leave.post('/admin/api/staff/delete')
def staff_delete():
    """விடுப்புப் பதிவு இல்லையெனில் நீக்கும்; இருந்தால் வரலாறு காக்க செயலற்றதாக்கும்."""
    d = _deny()
    if d: return d
    sid = (request.get_json(silent=True) or {}).get('id')
    try:
        sid = int(sid)
    except (TypeError, ValueError):
        return jsonify(success=False, message='பணியாளர் தெரியவில்லை')
    n = db.fetch_one('SELECT COUNT(*) AS n FROM staff_leave_records WHERE staff_id = %s', (sid,))['n']
    if n:
        db.execute('UPDATE library_staff SET active = FALSE WHERE id = %s', (sid,))
        return jsonify(success=True, message=f'{n} விடுப்புப் பதிவுகள் இருப்பதால் செயலற்றதாக்கப்பட்டது (விடுப்பு வரலாறு காக்கப்படும்).')
    db.execute('DELETE FROM library_staff WHERE id = %s', (sid,))
    return jsonify(success=True, message='நீக்கப்பட்டது.')


@leave.post('/admin/api/staff/activate')
def staff_activate():
    d = _deny()
    if d: return d
    b = request.get_json(silent=True) or {}
    try:
        sid = int(b.get('id'))
    except (TypeError, ValueError):
        return jsonify(success=False, message='பணியாளர் தெரியவில்லை')
    db.execute('UPDATE library_staff SET active = %s WHERE id = %s', (bool(b.get('active')), sid))
    return jsonify(success=True)


@leave.get('/admin/api/religious')
def religious_list():
    d = _deny()
    if d: return d
    try:
        rows = db.fetch_all('SELECT holiday_date, name FROM religious_holidays ORDER BY holiday_date')
    except Exception as e:
        hint = 'Database அட்டவணைகள் இன்னும் உருவாக்கப்படவில்லை.' if 'does not exist' in str(e) else 'Database பிழை.'
        return jsonify(success=False, message=hint)
    return jsonify(success=True, rows=[{'date': r['holiday_date'].isoformat(), 'name': r['name']} for r in rows])


@leave.post('/admin/api/religious/add')
def religious_add():
    d = _deny()
    if d: return d
    b = request.get_json(silent=True) or {}
    try:
        day = dt.date.fromisoformat(str(b.get('date') or ''))
    except ValueError:
        return jsonify(success=False, message='தேதியைத் தேர்ந்தெடுக்கவும்.')
    db.execute("INSERT INTO religious_holidays (holiday_date, name) VALUES (%s, %s) "
               "ON CONFLICT (holiday_date) DO UPDATE SET name = EXCLUDED.name", (day, str(b.get('name') or '').strip()))
    return jsonify(success=True)


@leave.post('/admin/api/religious/delete')
def religious_delete():
    d = _deny()
    if d: return d
    b = request.get_json(silent=True) or {}
    try:
        day = dt.date.fromisoformat(str(b.get('date') or ''))
    except ValueError:
        return jsonify(success=False, message='தேதி தெரியவில்லை')
    db.execute('DELETE FROM religious_holidays WHERE holiday_date = %s', (day,))
    return jsonify(success=True)


# ======================================================================
#  Phase 2 — விடுப்புப் பதிவு விதிகள் + பொதுப் பக்கம் /leave (கடவுச்சொல் இல்லை)
# ======================================================================
from zoneinfo import ZoneInfo
from flask import render_template
from receipts_module import _group_of, LIB_GROUPS

IST = ZoneInfo('Asia/Kolkata')
OFFICE_NAME = 'மாவட்ட நூலக அலுவலகம்'
RL_YEARLY = 3                       # மத (வரையறுக்கப்பட்ட) விடுப்பு — ஆண்டுக்கு அதிகபட்சம் 3 நாட்கள் (ஜனவரி–டிசம்பர்)
CL_YEARLY = 12                      # தற்செயல் விடுப்பு — ஆண்டுக்கு (ஜனவரி–டிசம்பர்)
PERMISSION_MAX_PER_MONTH = 2
DAILY_BLOCKED_TYPES = ('permission', 'religious')   # தினக்கூலிப் பணியாளருக்கு இல்லாத விடுப்பு வகைகள்

LEAVE_TYPES = {
    'permission': 'அனுமதி விடுப்பு', 'casual': 'தற்செயல் விடுப்பு', 'religious': 'மத விடுப்பு',
    'earned': 'ஈட்டிய விடுப்பு', 'medical': 'மருத்துவ விடுப்பு',
}
DAY_PARTS = {'FN': 'முற்பகல் அரை நாள்', 'AN': 'பிற்பகல் அரை நாள்', 'FULL': 'முழு நாள்'}
PERM_KINDS = {'start': 'பணி துவங்கும் போது', 'end': 'பணி முடியும்போது'}

# அனுமதி விடுப்பு நேர இடைவெளிகள் — A: மாவட்ட மைய + முழு நேர கிளை, B: கிளை, C: ஊர்ப்புற (மற்றவையும்)
SLOTS = {
    'A': {'start': ['காலை 8.00 மணி முதல் 9.00 மணி வரை', 'காலை 10.00 மணி முதல் 11.00 மணி வரை', 'மதியம் 2.00 மணி முதல் 3.00 மணி வரை'],
          'end': ['பிற்பகல் 1.00 மணி முதல் பிற்பகல் 2.00 மணி வரை', 'பிற்பகல் 4.00 மணி முதல் பிற்பகல் 5.00 மணி வரை', 'இரவு 7.00 மணி முதல் இரவு 8.00 மணி வரை']},
    'B': {'start': ['காலை 9.00 மணி முதல் 10.00 மணி வரை'],
          'end': ['மாலை 5.00 மணி முதல் 6.00 மணி வரை', 'மாலை 6.00 மணி முதல் 7.00 மணி வரை']},
    'C': {'start': ['காலை 9.00 மணி முதல் 10.00 மணி வரை'],
          'end': ['மாலை 5.30 மணி முதல் 6.30 மணி வரை']},
    'O': {'start': ['காலை 10.00 மணி முதல் 11.00 மணி வரை'],        # மாவட்ட நூலக அலுவலகம்
          'end': ['மாலை 4.45 மணி முதல் 5.45 மணி வரை']},
}


def category_of(lib_type):
    if OFFICE_NAME in str(lib_type or ''): return 'O'
    g = _group_of(lib_type)
    if g in ('மாவட்ட மைய நூலகம்', 'முழு நேர நூலகங்கள்'): return 'A'
    if g == 'கிளை நூலகங்கள்': return 'B'
    return 'C'


def allowed_types(lib_type, daily=False):
    """ஈட்டிய, மருத்துவ விடுப்பு ஊர்ப்புற நூலகப் பணியாளர்களுக்குக் கிடையாது.
    தினக்கூலிப் பணியாளர்களுக்கு அனுமதி விடுப்பு, மத விடுப்பு இரண்டும் கிடையாது."""
    rural = _group_of(lib_type) == 'ஊர்ப்புற நூலகங்கள்'
    return [k for k in LEAVE_TYPES
            if not (rural and k in ('earned', 'medical'))
            and not (daily and k in DAILY_BLOCKED_TYPES)]


def _today():
    return dt.datetime.now(IST).date()


def _d(v, what='தேதி'):
    try:
        return dt.date.fromisoformat(str(v or ''))
    except ValueError:
        raise ValueError(f'{what} சரியாக இல்லை.')


def _fmt(d):
    return d.strftime('%d/%m/%Y')


def describe(r):
    """ஒரு பதிவின் சுருக்க விளக்கம்."""
    t = LEAVE_TYPES.get(r['leave_type'], r['leave_type'])
    if r['leave_type'] == 'permission':
        return f"{PERM_KINDS.get(r['perm_kind'], '')}: {r['perm_slot']}"
    if r['leave_type'] == 'casual':
        return DAY_PARTS.get(r['day_part'], '')
    return 'முழு நாள்' if r['start_date'] == r['end_date'] else f"{_fmt(r['start_date'])} – {_fmt(r['end_date'])}"


def _staff_row(sid):
    row = db.fetch_one(
        "SELECT s.id, s.staff_name, s.staff_no, s.is_daily_wage, s.active, l.name AS lib, l.lib_type "
        "FROM library_staff s JOIN libraries l ON l.id = s.library_id WHERE s.id = %s", (sid,))
    if not row or not row['active']:
        raise ValueError('பணியாளர் தெரியவில்லை.')
    return row


def casual_used(staff_id, year, exclude_id=None):
    r = db.fetch_one("SELECT COALESCE(SUM(days), 0) AS n FROM staff_leave_records WHERE staff_id = %s AND leave_type = 'casual' "
                     "AND EXTRACT(YEAR FROM start_date) = %s AND id IS DISTINCT FROM %s", (staff_id, year, exclude_id))
    return float(r['n'])


def religious_used(staff_id, year, exclude_id=None):
    r = db.fetch_one("SELECT COALESCE(SUM(days), 0) AS n FROM staff_leave_records WHERE staff_id = %s AND leave_type = 'religious' "
                     "AND EXTRACT(YEAR FROM start_date) = %s AND id IS DISTINCT FROM %s", (staff_id, year, exclude_id))
    return float(r['n'])


def permissions_in_month(staff_id, year, month, exclude_id=None):
    r = db.fetch_one("SELECT COUNT(*) AS n FROM staff_leave_records WHERE staff_id = %s AND leave_type = 'permission' "
                     "AND EXTRACT(YEAR FROM start_date) = %s AND EXTRACT(MONTH FROM start_date) = %s AND id IS DISTINCT FROM %s",
                     (staff_id, year, month, exclude_id))
    return r['n']


# ---------------------------------------------------------------- விடுமுறை நாள் உதவிகள் (விண்ணப்பம் + நாள் கணக்கு)
def holiday_map(a, b):
    """அரசு விடுமுறை நாட்கள் {தேதி: பெயர்} — a முதல் b வரை."""
    return {r['holiday_date']: (r['name'] or '') for r in db.fetch_all(
        'SELECT holiday_date, name FROM holidays WHERE holiday_date BETWEEN %s AND %s', (a, b))}


def off_label(d, hmap):
    """வெள்ளி / இரண்டாம் சனி / அரசு விடுமுறை எனில் அதன் பெயர்; பணி நாள் எனில் None."""
    parts = []
    if d in hmap: parts.append(hmap[d] or 'அரசு விடுமுறை')
    if d.weekday() == 4: parts.append('வெள்ளி')
    elif d.weekday() == 5 and 7 < d.day <= 14: parts.append('இரண்டாம் சனி')
    return ' / '.join(parts) if parts else None


def trim_trailing(start, end, hmap):
    """விடுப்பு முடியும் நாள் வெள்ளி / இரண்டாம் சனி / அரசு விடுமுறை எனில் அவற்றைக் கணக்கிலிருந்து நீக்கும்
    (இடையில் வருபவை கணக்கில் சேரும்). -> (பயனுள்ள கடைசி நாள், [(தேதி, பெயர்)])"""
    eff, cut = end, []
    while eff >= start and off_label(eff, hmap):
        cut.insert(0, (eff, off_label(eff, hmap)))
        eff -= dt.timedelta(days=1)
    return eff, cut


def attached_days(start, end, hmap, trimmed=()):
    """விடுப்புக்கு முந்தைய / அடுத்த தொடர்ச்சியான வார விடுமுறை, அரசு விடுமுறை நாட்கள் [(தேதி, பெயர்)] (தேதி வரிசையில்)."""
    out = list(trimmed)
    d = start - dt.timedelta(days=1)
    while off_label(d, hmap):
        out.append((d, off_label(d, hmap))); d -= dt.timedelta(days=1)
    d = end + dt.timedelta(days=1)
    while off_label(d, hmap):
        out.append((d, off_label(d, hmap))); d += dt.timedelta(days=1)
    return sorted(set(out))


def counted_days(start, end, hmap):
    """start–end இடையே வெள்ளி / இரண்டாம் சனி / அரசு விடுமுறை நீங்கலாகக் கணக்கில் வரும் நாட்கள்."""
    n, d = 0, start
    while d <= end:
        if not off_label(d, hmap): n += 1
        d += dt.timedelta(days=1)
    return n


def religious_name(d):
    r = db.fetch_one('SELECT name FROM religious_holidays WHERE holiday_date = %s', (d,))
    return (r['name'] or '') if r else ''


def validate(p, exclude_id=None):
    """விடுப்பு விதிகள் அனைத்தையும் சரிபார்த்து, சேமிக்கத் தயாரான dict திருப்பும்; தவறெனில் ValueError(செய்தி)."""
    try:
        sid = int(p.get('staff_id'))
    except (TypeError, ValueError):
        raise ValueError('பணியாளரைத் தேர்ந்தெடுக்கவும்.')
    staff = _staff_row(sid)
    lt = str(p.get('leave_type') or '')
    if lt not in LEAVE_TYPES:
        raise ValueError('விடுப்பு வகையைத் தேர்ந்தெடுக்கவும்.')
    daily = bool(staff['is_daily_wage'])
    if daily and lt in DAILY_BLOCKED_TYPES:
        raise ValueError(f'{LEAVE_TYPES[lt]} தினக்கூலிப் பணியாளர்களுக்குக் கிடையாது.')
    if lt not in allowed_types(staff['lib_type'], daily):
        raise ValueError(f'{LEAVE_TYPES[lt]} ஊர்ப்புற நூலகப் பணியாளர்களுக்குக் கிடையாது.')
    start = _d(p.get('start_date'), 'தொடக்க தேதி')
    end = _d(p.get('end_date') or p.get('start_date'), 'முடிவு தேதி')
    rec = {'staff_id': sid, 'leave_type': lt, 'day_part': None, 'perm_kind': None, 'perm_slot': ''}

    if lt == 'permission':
        kind, slot = str(p.get('perm_kind') or ''), str(p.get('perm_slot') or '')
        if kind not in PERM_KINDS:
            raise ValueError('பணி துவங்கும் போதா / பணி முடியும்போதா என்பதைத் தேர்ந்தெடுக்கவும்.')
        if slot not in SLOTS[category_of(staff['lib_type'])][kind]:
            raise ValueError('நேர இடைவெளியைத் தேர்ந்தெடுக்கவும்.')
        end, days = start, 0
        used = permissions_in_month(sid, start.year, start.month, exclude_id)
        if used >= PERMISSION_MAX_PER_MONTH:
            raise ValueError(f'நீங்கள் ஏற்கனவே இம்மாதத்திற்கு {PERMISSION_MAX_PER_MONTH} அனுமதி விடுப்புகளை எடுத்துள்ளீர்கள்.')
        rec.update(perm_kind=kind, perm_slot=slot)
    elif lt == 'casual':
        part = str(p.get('day_part') or '')
        if part not in DAY_PARTS:
            raise ValueError('முற்பகல் / பிற்பகல் / முழு நாள் — ஒன்றைத் தேர்ந்தெடுக்கவும்.')
        if daily and part != 'FULL':
            raise ValueError('தினக்கூலிப் பணியாளர்களுக்குத் தற்செயல் விடுப்பு முழு நாள் மட்டுமே.')
        if part == 'FULL':
            if end < start: raise ValueError('முடிவு தேதி தொடக்க தேதிக்கு முன் இருக்க முடியாது.')
            if end.year != start.year: raise ValueError('ஆண்டு மாறும் தற்செயல் விடுப்பை இரண்டு பதிவுகளாகச் செய்யவும்.')
            # இடையில் வரும் வெள்ளி / இரண்டாம் சனி / அரசு விடுமுறை நாட்கள் தற்செயல் விடுப்புக் கணக்கில் சேராது
            days = counted_days(start, end, holiday_map(start, end))
            if days == 0:
                raise ValueError('தேர்ந்தெடுத்த நாட்கள் அனைத்தும் வார விடுமுறை / அரசு விடுமுறை; விடுப்பு எடுக்கத் தேவையில்லை.')
        else:
            end, days = start, 0.5
        used = casual_used(sid, start.year, exclude_id)
        if used + days > CL_YEARLY:
            raise ValueError(f'தற்செயல் விடுப்பு {start.year}-ல் மீதம் {CL_YEARLY - used:g} நாட்கள் மட்டுமே; கேட்பது {days:g} நாட்கள்.')
        rec['day_part'] = part
    elif lt == 'religious':
        end, days = start, 1                 # மத விடுப்பு — குறிப்பிட்ட ஒரே நாள் மட்டும்
        if not db.fetch_one('SELECT 1 AS x FROM religious_holidays LIMIT 1'):
            raise ValueError('மத விடுப்புத் தேதிகள் இன்னும் ஏற்றப்படவில்லை.')
        if not db.fetch_one('SELECT 1 AS x FROM religious_holidays WHERE holiday_date = %s', (start,)):
            raise ValueError(f'{_fmt(start)} மத விடுப்பு நாள் அல்ல. மத விடுப்பு அனுமதிக்கப்பட்ட தேதியில் மட்டுமே எடுக்க முடியும்.')
        used = religious_used(sid, start.year, exclude_id)
        if used + 1 > RL_YEARLY:
            raise ValueError(f'மத விடுப்பு ஆண்டுக்கு அதிகபட்சம் {RL_YEARLY} நாட்கள் மட்டுமே; {start.year}-ல் ஏற்கனவே {used:g} நாட்கள் எடுத்துள்ளீர்கள்.')
    else:
        if end < start: raise ValueError('முடிவு தேதி தொடக்க தேதிக்கு முன் இருக்க முடியாது.')
        # ஈட்டிய / மருத்துவ: முடியும் நாள் வெள்ளி / இரண்டாம் சனி / அரசு விடுமுறை எனில் கணக்கில் சேராது
        eff, _cut = trim_trailing(start, end, holiday_map(start - dt.timedelta(days=1), end))
        if eff < start:
            raise ValueError('தேர்ந்தெடுத்த நாட்கள் அனைத்தும் வார விடுமுறை / அரசு விடுமுறை; விடுப்பு எடுக்கத் தேவையில்லை.')
        days = (eff - start).days + 1
    if (end - start).days > 366:
        raise ValueError('விடுப்புக் காலம் மிக நீளமாக உள்ளது.')

    # ஒன்றோடொன்று மேற்பொருந்தும் பதிவு — தற்செயல் முற்பகல் + பிற்பகல் (ஒரே நாள்) தவிர
    for r in db.fetch_all(
            "SELECT id, leave_type, start_date, end_date, day_part FROM staff_leave_records "
            "WHERE staff_id = %s AND start_date <= %s AND end_date >= %s AND id IS DISTINCT FROM %s", (sid, end, start, exclude_id)):
        half_pair = (lt == 'casual' and rec['day_part'] in ('FN', 'AN') and r['leave_type'] == 'casual'
                     and r['day_part'] in ('FN', 'AN') and r['day_part'] != rec['day_part'])
        if not half_pair:
            raise ValueError(f"{_fmt(r['start_date'])} முதல் {_fmt(r['end_date'])} வரை ஏற்கனவே {LEAVE_TYPES[r['leave_type']]} பதிவாகியுள்ளது.")
    rec.update(start_date=start, end_date=end, days=days)
    return rec


def _balance(sid, on):
    return {'casual_used': casual_used(sid, on.year), 'casual_total': CL_YEARLY,
            'religious_used': religious_used(sid, on.year), 'religious_total': RL_YEARLY,
            'permission_used': permissions_in_month(sid, on.year, on.month), 'permission_max': PERMISSION_MAX_PER_MONTH,
            'year': on.year, 'month': on.month}


# ======================================================================
#  ஊதியப் பிடித்தம் — விடுப்புக்கு ஏற்பப் பணி நாட்கள் குறைப்பு (சில்லறைச் செலவினப் படிவத்துக்கு)
#  காலம்: முந்தைய மாதம் 16 -> நடப்பு மாதம் 15. வெள்ளி / இரண்டாம் சனி / அரசு விடுமுறை நாட்களில் எடுத்த விடுப்பு கணக்கில் வராது.
#   1. மாவட்ட மைய நூலகம்  : எந்த விடுப்புக்கும் பணி நாள் குறையாது.
#   2. முழு நேர நூலகம்     : தினக்கூலிப் பணியாளர் விடுப்பு -> அவருக்கு மட்டும் குறையும் (கூட்டுநருக்கு இல்லை).
#   3. கிளை / ஊர்ப்புற      : பணியாளர் முற்பகல் (அல்லது முழு நாள்) விடுப்பு -> கூட்டுநருக்கு அந்த நாள் 1 நாள் குறையும்;
#                             பிற்பகல் விடுப்பு -> குறையாது. பணியாளரே இல்லாத நூலகத்தில் தினக்கூலிப் பணியாளர் விடுப்பு ->
#                             தினக்கூலிப் பணியாளர், கூட்டுநர் இருவருக்கும் குறையும்.
#   4. பகுதி நேர நூலகம்    : தினக்கூலிப் பணியாளர் விடுப்பு -> அவருக்குக் குறையும் (கூட்டுநர் இல்லை).
#  அனுமதி விடுப்பு (மணி நேரம்) எந்த நாளையும் குறைக்காது.
# ======================================================================
FORENOON_OFF = ('FN', 'FULL', None)     # casual: முற்பகல் / முழு நாள்; religious / earned / medical: day_part = None (முழு நாள்)


def _is_work_day(d, holidays):
    """app.period_breakdown-ல் உள்ள அதே விதி: வெள்ளி, இரண்டாம் சனி (8-14), அரசு விடுமுறை நீங்கலாக."""
    if d.weekday() == 4: return False
    if d.weekday() == 5 and 7 < d.day <= 14: return False
    return d not in holidays


def _absence(rows, start, end, holidays):
    """rows (ஒரே பணியாளரின் பதிவுகள்) -> {தேதி: (நாள் பகுதி, முற்பகல் இல்லையா)}"""
    out = {}
    for r in rows:
        if r['leave_type'] == 'permission':
            continue
        portion = 0.5 if (r['leave_type'] == 'casual' and r['day_part'] in ('FN', 'AN')) else 1.0
        forenoon = r['leave_type'] != 'casual' or r['day_part'] in FORENOON_OFF
        d = max(r['start_date'], start)
        while d <= min(r['end_date'], end):
            if _is_work_day(d, holidays):
                out[d] = (portion, forenoon)
            d += dt.timedelta(days=1)
    return out


def leave_deductions(library_id, lib_type, start, end, holidays):
    """{'partner', 'staff', 'part_time': குறைக்க வேண்டிய நாட்கள், 'notes': [விளக்கம்]}"""
    res = {'partner': 0.0, 'staff': 0.0, 'part_time': 0.0, 'notes': []}
    group = _group_of(lib_type)
    if group == 'மாவட்ட மைய நூலகம்':
        return res
    staff = db.fetch_all('SELECT id, is_daily_wage FROM library_staff WHERE library_id = %s AND active', (library_id,))
    if not staff:
        return res
    recs = db.fetch_all(
        "SELECT r.* FROM staff_leave_records r JOIN library_staff s ON s.id = r.staff_id "
        "WHERE s.library_id = %s AND s.active AND r.start_date <= %s AND r.end_date >= %s", (library_id, end, start))
    by_staff = {}
    for r in recs:
        by_staff.setdefault(r['staff_id'], []).append(r)
    ab = {s['id']: _absence(by_staff.get(s['id'], []), start, end, holidays) for s in staff}
    regular = [s['id'] for s in staff if not s['is_daily_wage']]
    daily = [s['id'] for s in staff if s['is_daily_wage']]
    daily_days = sum(p for sid in daily for p, _ in ab[sid].values())

    if group == 'பகுதி நேர நூலகங்கள்':
        res['part_time'] = daily_days
        if daily_days: res['notes'].append(f'தினக்கூலிப் பணியாளர் விடுப்பு {daily_days:g} நாட்கள் — பணி நாட்களில் குறைக்கப்பட்டது.')
    elif group == 'முழு நேர நூலகங்கள்':
        res['staff'] = daily_days
        if daily_days: res['notes'].append(f'தினக்கூலிப் பணியாளர் விடுப்பு {daily_days:g} நாட்கள் — அவரது பணி நாட்களில் குறைக்கப்பட்டது (கூட்டுநருக்கு இல்லை).')
    elif group in ('கிளை நூலகங்கள்', 'ஊர்ப்புற நூலகங்கள்'):
        if regular:
            # எல்லாப் பணியாளரும் முற்பகலில் இல்லாத நாட்கள் -> கூட்டுநருக்கு அன்று 1 நாள் குறைப்பு
            dates = set.intersection(*[{d for d, (_, fn) in ab[sid].items() if fn} for sid in regular])
            res['partner'] = float(len(dates))
            res['staff'] = daily_days
            if dates: res['notes'].append(f'பணியாளர் முற்பகல் / முழு நாள் விடுப்பு {len(dates)} நாட்கள் — கூட்டுநர் பணி நாட்களில் குறைக்கப்பட்டது.')
            if daily_days: res['notes'].append(f'தினக்கூலிப் பணியாளர் விடுப்பு {daily_days:g} நாட்கள் — அவரது பணி நாட்களில் குறைக்கப்பட்டது.')
        elif daily:
            # பணியாளர் யாரும் இல்லை: தினக்கூலிப் பணியாளர் விடுப்பு இருவருக்கும்
            dates = set.intersection(*[set(ab[sid]) for sid in daily])
            res['partner'] = sum(min(ab[sid][d][0] for sid in daily) for d in dates)
            res['staff'] = daily_days
            if daily_days: res['notes'].append(f'தினக்கூலிப் பணியாளர் விடுப்பு {daily_days:g} நாட்கள் — பணியாளர், கூட்டுநர் இருவரின் பணி நாட்களிலும் குறைக்கப்பட்டது.')
    else:
        res['staff'] = daily_days
    return res


# ---------------------------------------------------------------- பொது API (/leave)
@leave.get('/leave')
def leave_page():
    return render_template('leave.html')


@leave.get('/leave/api/meta')
def leave_meta():
    return jsonify(success=True, leaveTypes=LEAVE_TYPES, dayParts=DAY_PARTS, permKinds=PERM_KINDS)


@leave.get('/leave/api/libraries')
def leave_libraries():
    rows = db.fetch_all(
        "SELECT l.id, l.name, l.lib_type, l.is_office FROM libraries l WHERE (NOT l.is_test OR l.is_office OR %s) "
        "AND (l.inactive_from IS NULL OR l.inactive_from > CURRENT_DATE) "
        "AND EXISTS (SELECT 1 FROM library_staff s WHERE s.library_id = l.id AND s.active) ORDER BY l.id",
        (bool(session.get('tester') or session.get('admin')),))
    order = [t for _, t in LIB_GROUPS] + ['பிற நூலகங்கள்']
    OFFICE = OFFICE_NAME          # மாவட்ட நூலக அலுவலகப் பணியாளர்கள் — முதல் குழு
    order = [OFFICE] + order
    groups = {t: [] for t in order}
    for r in rows:
        groups[OFFICE if r['is_office'] else _group_of(r['lib_type'])].append({'id': r['id'], 'name': r['name']})
    return jsonify(success=True, groups=[{'title': t, 'libraries': groups[t]} for t in order if groups[t]])


@leave.get('/leave/api/staff')
def leave_staff():
    try:
        lid = int(request.args.get('library_id'))
    except (TypeError, ValueError):
        return jsonify(success=False, message='நூலகத்தைத் தேர்ந்தெடுக்கவும்.')
    lib = db.fetch_one('SELECT name, lib_type FROM libraries WHERE id = %s', (lid,))
    if not lib:
        return jsonify(success=False, message='நூலகம் தெரியவில்லை.')
    rows = db.fetch_all('SELECT id, staff_name, staff_no, is_daily_wage FROM library_staff WHERE library_id = %s AND active '
                        'ORDER BY is_daily_wage, id', (lid,))
    cat = category_of(lib['lib_type'])
    return jsonify(success=True, category=cat, slots=SLOTS[cat], types=allowed_types(lib['lib_type']),
                   staff=[{'id': r['id'], 'daily': bool(r['is_daily_wage']),
                           'types': allowed_types(lib['lib_type'], bool(r['is_daily_wage'])),
                           'fullDayOnly': bool(r['is_daily_wage']),
                           'label': staff_label(r['staff_name'], r['staff_no'], r['is_daily_wage'], lib['name'])} for r in rows])


@leave.get('/leave/api/balance')
def leave_balance():
    try:
        sid = int(request.args.get('staff_id'))
        on = _d(request.args.get('date')) if request.args.get('date') else _today()
        _staff_row(sid)
    except ValueError as e:
        return jsonify(success=False, message=str(e) if 'தேதி' in str(e) else 'பணியாளர் தெரியவில்லை.')
    except TypeError:
        return jsonify(success=False, message='பணியாளர் தெரியவில்லை.')
    return jsonify(success=True, **_balance(sid, on))


@leave.get('/leave/api/religious')
def leave_religious_dates():
    rows = db.fetch_all('SELECT holiday_date, name FROM religious_holidays WHERE holiday_date >= %s ORDER BY holiday_date', (_today() - dt.timedelta(days=30),))
    return jsonify(success=True, rows=[{'date': r['holiday_date'].isoformat(), 'name': r['name']} for r in rows])


@leave.post('/leave/api/register')
def leave_register():
    p = request.get_json(silent=True) or {}
    try:
        rec = validate(p)
    except ValueError as e:
        return jsonify(success=False, message=str(e))
    row = db.fetch_one(
        "INSERT INTO staff_leave_records (staff_id, leave_type, start_date, end_date, days, day_part, perm_kind, perm_slot) "
        "VALUES (%(staff_id)s, %(leave_type)s, %(start_date)s, %(end_date)s, %(days)s, %(day_part)s, %(perm_kind)s, %(perm_slot)s) RETURNING id", rec)
    msg = f"{LEAVE_TYPES[rec['leave_type']]} பதிவு செய்யப்பட்டது ({_fmt(rec['start_date'])}" + \
          ('' if rec['start_date'] == rec['end_date'] else f" – {_fmt(rec['end_date'])}") + ')'
    return jsonify(success=True, id=row['id'], message=msg, **_balance(rec['staff_id'], rec['start_date']))


def _record_json(r):
    return {'id': r['id'], 'type': r['leave_type'], 'typeLabel': LEAVE_TYPES.get(r['leave_type'], ''),
            'start': r['start_date'].isoformat(), 'end': r['end_date'].isoformat(), 'days': float(r['days']),
            'dayPart': r['day_part'], 'permKind': r['perm_kind'], 'permSlot': r['perm_slot'], 'detail': describe(r)}


@leave.get('/leave/api/history')
def leave_history():
    try:
        sid = int(request.args.get('staff_id'))
        year = int(request.args.get('year') or _today().year)
        staff = _staff_row(sid)
    except (TypeError, ValueError):
        return jsonify(success=False, message='பணியாளரைத் தேர்ந்தெடுக்கவும்.')
    rows = db.fetch_all("SELECT * FROM staff_leave_records WHERE staff_id = %s AND EXTRACT(YEAR FROM start_date) = %s "
                        "ORDER BY start_date DESC, id DESC", (sid, year))
    summary = {k: 0.0 for k in LEAVE_TYPES}
    perm_by_month = {}
    for r in rows:
        if r['leave_type'] == 'permission':
            summary['permission'] += 1
            perm_by_month[r['start_date'].month] = perm_by_month.get(r['start_date'].month, 0) + 1
        else:
            summary[r['leave_type']] += float(r['days'])
    return jsonify(success=True, year=year, library=staff['lib'], label=staff_label(staff['staff_name'], staff['staff_no'], staff['is_daily_wage'], staff['lib']),
                   summary=summary, casualTotal=CL_YEARLY, religiousTotal=RL_YEARLY, permByMonth=perm_by_month, records=[_record_json(r) for r in rows])


@leave.get('/leave/api/today')
def leave_today():
    today = _today()
    rows = db.fetch_all(
        "SELECT r.*, s.staff_name, s.staff_no, s.is_daily_wage, l.name AS lib FROM staff_leave_records r "
        "JOIN library_staff s ON s.id = r.staff_id JOIN libraries l ON l.id = s.library_id "
        "WHERE (NOT l.is_test OR l.is_office) AND r.start_date <= %s AND r.end_date >= %s ORDER BY l.is_office DESC, l.id, r.id", (today, today))
    return jsonify(success=True, date=today.isoformat(), count=len(rows), rows=[{
        'label': staff_label(r['staff_name'], r['staff_no'], r['is_daily_wage'], r['lib']), 'library': r['lib'],
        'type': r['leave_type'], 'typeLabel': LEAVE_TYPES[r['leave_type']], 'detail': describe(r)} for r in rows])


# ---------------------------------------------------------------- Admin — பதிவுகளைப் பார்க்க / திருத்த / நீக்க
@leave.get('/admin/api/leave-records')
def admin_records():
    d = _deny()
    if d: return d
    try:
        y, m = int(request.args.get('year')), int(request.args.get('month'))
        first = dt.date(y, m, 1)
    except (TypeError, ValueError):
        return jsonify(success=False, message='மாதத்தைத் தேர்ந்தெடுக்கவும்.')
    last = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    rows = db.fetch_all(
        "SELECT r.*, s.staff_name, s.staff_no, s.is_daily_wage, l.name AS lib, l.lib_type FROM staff_leave_records r "
        "JOIN library_staff s ON s.id = r.staff_id JOIN libraries l ON l.id = s.library_id "
        "WHERE r.start_date <= %s AND r.end_date >= %s ORDER BY r.start_date DESC, l.id, r.id", (last, first))
    out = []
    for r in rows:
        j = _record_json(r)
        cat = category_of(r['lib_type'])
        j.update(staffId=r['staff_id'], library=r['lib'], label=staff_label(r['staff_name'], r['staff_no'], r['is_daily_wage'], r['lib']),
                 slots=SLOTS[cat], daily=bool(r['is_daily_wage']),
                 types=allowed_types(r['lib_type'], bool(r['is_daily_wage'])))
        out.append(j)
    return jsonify(success=True, rows=out, leaveTypes=LEAVE_TYPES, dayParts=DAY_PARTS, permKinds=PERM_KINDS)


@leave.post('/admin/api/leave-records/update')
def admin_record_update():
    d = _deny()
    if d: return d
    p = request.get_json(silent=True) or {}
    try:
        rid = int(p.get('id'))
        cur = db.fetch_one('SELECT staff_id FROM staff_leave_records WHERE id = %s', (rid,))
        if not cur:
            return jsonify(success=False, message='பதிவு இல்லை.')
        p['staff_id'] = cur['staff_id']
        rec = validate(p, exclude_id=rid)
    except (TypeError, ValueError) as e:
        return jsonify(success=False, message=str(e) if isinstance(e, ValueError) and str(e) else 'பதிவு தெரியவில்லை.')
    rec['id'] = rid
    db.execute("UPDATE staff_leave_records SET leave_type = %(leave_type)s, start_date = %(start_date)s, end_date = %(end_date)s, "
               "days = %(days)s, day_part = %(day_part)s, perm_kind = %(perm_kind)s, perm_slot = %(perm_slot)s WHERE id = %(id)s", rec)
    return jsonify(success=True, message='திருத்தப்பட்டது.')


@leave.post('/admin/api/leave-records/delete')
def admin_record_delete():
    d = _deny()
    if d: return d
    try:
        rid = int((request.get_json(silent=True) or {}).get('id'))
    except (TypeError, ValueError):
        return jsonify(success=False, message='பதிவு தெரியவில்லை.')
    db.execute('DELETE FROM staff_leave_records WHERE id = %s', (rid,))
    return jsonify(success=True, message='நீக்கப்பட்டது.')
