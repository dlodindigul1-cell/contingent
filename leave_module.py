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
            "SELECT s.id, s.staff_name, s.staff_no, s.is_daily_wage, s.seq, s.active, l.name AS lib, l.lib_type, "
            "(SELECT COUNT(*) FROM staff_leave_records r WHERE r.staff_id = s.id) AS recs "
            "FROM library_staff s JOIN libraries l ON l.id = s.library_id WHERE NOT l.is_test "
            "ORDER BY l.id, s.is_daily_wage, s.id")
    except Exception as e:
        hint = 'Database அட்டவணைகள் இன்னும் உருவாக்கப்படவில்லை (Admin 1 → அட்டவணைகளை உருவாக்கு).' if 'does not exist' in str(e) else 'Database பிழை.'
        return jsonify(success=False, message=hint)
    return jsonify(success=True, rows=[{
        'id': r['id'], 'library': r['lib'], 'type': r['lib_type'], 'name': r['staff_name'], 'no': r['staff_no'],
        'daily': r['is_daily_wage'], 'active': r['active'], 'records': r['recs'],
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
