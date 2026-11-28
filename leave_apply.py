"""நிரந்தரப் பணியாளர் (பணியாளர் எண் உள்ளவர்) விடுப்பு விண்ணப்பம் — தற்செயல் / ஈட்டிய / மருத்துவ விடுப்பு.
விடுப்புப் பதிவுக்குப் பின், பதிவான விவரங்கள் தானாக நிரப்பப்படும்; பணியாளர் மீதமுள்ள விவரங்களை நிரப்பியதும்
PDF நேரடியாகப் பதிவிறக்கலாம் அல்லது Brevo (mailer.py) மூலம் மின்னஞ்சலில் பெறலாம்.
தினக்கூலிப் பணியாளர்களுக்குக் கிடையாது."""
import datetime as dt
import time

from flask import Blueprint, jsonify, request, Response

import db
import mailer
from leave_module import (_staff_row, casual_used, _fmt, _today, CL_YEARLY, RL_YEARLY, religious_used, DAY_PARTS,
                          holiday_map, trim_trailing, attached_days, counted_days, off_label, religious_name)
from pdfs import _new_pdf

leave_apply = Blueprint('leave_apply', __name__)

APPLY_TYPES = ('casual', 'religious', 'earned', 'medical')
DESIGNATIONS = ['நூலக ஆய்வாளர்', 'முதல் நிலை நூலகர்', 'கண்காணிப்பாளர்', 'உதவியாளர்', 'கணக்கர்',
                'இரண்டாம் நிலை நூலகர்', 'இளநிலை உதவியாளர்', 'மூன்றாம் நிலை நூலகர்', 'பதிவறை எழுத்தர்', 'ஊர்ப்புற நூலகர்']
TITLES = {'casual': 'தற்செயல் விடுப்பு விண்ணப்பம்', 'religious': 'வரையறுக்கப்பட்ட விடுப்பு விண்ணப்பம்', 'earned': 'ஈட்டிய விடுப்பு விண்ணப்பம்',
          'medical': 'மருத்துவ விடுப்பு விண்ணப்பம்'}

_MAIL_LOG = {}            # staff_id -> [நேரங்கள்] — பொதுப் பக்கம் என்பதால் மின்னஞ்சல் தவறாகப் பயன்படுவதைத் தடுக்க
_MAIL_MAX_PER_HOUR = 5


def _s(v, n=500):
    return str(v or '').strip()[:n]


def _record(rid):
    """பதிவு + பணியாளர் (நிரந்தரம் மட்டும், விண்ணப்ப வகை மட்டும்) — இல்லையெனில் ValueError."""
    try:
        rid = int(rid)
    except (TypeError, ValueError):
        raise ValueError('விடுப்புப் பதிவு தெரியவில்லை.')
    r = db.fetch_one('SELECT * FROM staff_leave_records WHERE id = %s', (rid,))
    if not r:
        raise ValueError('விடுப்புப் பதிவு தெரியவில்லை.')
    staff = _staff_row(r['staff_id'])
    if staff['is_daily_wage'] or not str(staff['staff_no'] or '').strip():
        raise ValueError('விடுப்பு விண்ணப்பம் நிரந்தரப் பணியாளர்களுக்கு (பணியாளர் எண் உள்ளவர்) மட்டுமே.')
    if r['leave_type'] not in APPLY_TYPES:
        raise ValueError('தற்செயல், வரையறுக்கப்பட்ட (மத), ஈட்டிய, மருத்துவ விடுப்புகளுக்கு மட்டுமே விண்ணப்பம் தயாராகும்.')
    return r, staff


def _hmap(r):
    return holiday_map(r['start_date'] - dt.timedelta(days=15), r['end_date'] + dt.timedelta(days=15))


def _lbl(items):
    return ', '.join(f'{_fmt(d)} ({n})' for d, n in items)


def _period_text(r, hmap=None):
    if r['leave_type'] == 'casual' and r['day_part'] in ('FN', 'AN'):
        return f"{_fmt(r['start_date'])} ({DAY_PARTS[r['day_part']]})"
    if r['leave_type'] == 'religious':
        nm = religious_name(r['start_date'])
        return f"{_fmt(r['start_date'])}" + (f' ({nm})' if nm else '') + ' (1 நாள்)'
    if r['leave_type'] in ('earned', 'medical'):     # முடியும் நாள் வார / அரசு விடுமுறை எனில் கணக்கில் இல்லை
        eff, _ = trim_trailing(r['start_date'], r['end_date'], hmap if hmap is not None else _hmap(r))
        if eff < r['start_date']: eff = r['start_date']
        end = eff
    else:
        end = r['end_date']
    if r['start_date'] == end:
        return f"{_fmt(r['start_date'])} (1 நாள்)"
    if r['leave_type'] == 'casual':        # இடையில் வரும் வார / அரசு விடுமுறை நாட்கள் கணக்கில் இல்லை
        n = counted_days(r['start_date'], end, hmap if hmap is not None else _hmap(r))
    else:
        n = (end - r['start_date']).days + 1
    return f"{_fmt(r['start_date'])} முதல் {_fmt(end)} வரை ({n:g} நாட்கள்)"


def _form(r, staff, f):
    """விண்ணப்ப வரிகள் [(எண்/தலைப்பு, மதிப்பு)] + தலைப்பு."""
    apply_date = _s(f.get('apply_date'), 10)
    try:
        apply_date = _fmt(dt.date.fromisoformat(apply_date))
    except ValueError:
        apply_date = _fmt(_today())
    desig, addr = _s(f.get('designation'), 60), _s(f.get('address'))
    lt, name, work = r['leave_type'], staff['staff_name'], staff['lib']
    if desig not in DESIGNATIONS:
        raise ValueError('பதவியைத் தேர்ந்தெடுக்கவும்.')
    if not addr:
        raise ValueError('விடுப்பின் போது முகவரியை எழுதவும்.')
    hmap = _hmap(r)
    period = _period_text(r, hmap)

    if lt == 'religious':       # காரணம் தானாக: "24/11/2026 · கார்த்திகை தீபம் முன்னிட்டு"
        nm = religious_name(r['start_date'])
        reason = f"{_fmt(r['start_date'])}" + (f' · {nm}' if nm else '') + ' முன்னிட்டு'
    else:
        reason = _s(f.get('reason'))
        if not reason:
            raise ValueError('விடுப்பிற்கான காரணத்தை எழுதவும்.')

    if lt in ('casual', 'religious'):
        yr = r['start_date'].year
        if lt == 'casual':
            used = casual_used(staff['id'], yr, r['id'])
            tot, used_s, rem = str(CL_YEARLY), f'{used:g}', f'{CL_YEARLY - used:g}'
            tot = f'{CL_YEARLY} (தற்செயல் விடுப்பு — {yr})'
        else:
            u = religious_used(staff['id'], yr, r['id'])
            tot, used_s, rem = f'{RL_YEARLY} (மத விடுப்பு — {yr})', f'{u:g}', f'{RL_YEARLY - u:g}'
        att = attached_days(r['start_date'], r['end_date'], hmap)
        if lt == 'casual':          # விடுப்புக்கு இடையில் வரும் வார / அரசு விடுமுறையும் இணைப்பாகக் காட்டப்படும்
            d = r['start_date']
            while d <= r['end_date']:
                if off_label(d, hmap): att.append((d, off_label(d, hmap)))
                d += dt.timedelta(days=1)
            att = sorted(set(att))
        rows = [('1. பணியாளர் பெயர்', name), ('2. பதவி', desig), ('3. பணியாற்றும் இடம்', work),
                ('4. மொத்த விடுப்பு நாட்கள்', tot), ('5. இதுவரை எடுத்த விடுப்பு நாட்கள்', used_s),
                ('6. மீதமுள்ள விடுப்பு நாட்கள்', rem), ('7. தற்போது தேவைப்படும் விடுப்பு நாட்கள்', period),
                ('8. அனுமதி விடுப்பு நாட்கள்', ('ஆம் — ' + _lbl(att)) if att else 'இல்லை'),
                ('9. விடுப்பிற்கான காரணம்', reason), ('10. விடுப்பின் போது முகவரி', addr)]
    else:
        med = _s(f.get('med_cert'), 4) == 'ஆம்'
        eff, cut = trim_trailing(r['start_date'], r['end_date'], hmap)
        att = attached_days(r['start_date'], r['end_date'], hmap, cut)
        gov_txt = ('ஆம் — ' + _lbl(att)) if att else 'இல்லை'
        prev = db.fetch_all(
            "SELECT leave_type, start_date, end_date FROM staff_leave_records WHERE staff_id = %s AND leave_type IN ('earned','medical') "
            "AND id <> %s AND start_date < %s ORDER BY start_date DESC LIMIT 3", (staff['id'], r['id'], r['start_date']))
        prev_txt = '; '.join(f"{_fmt(p['start_date'])}" + ('' if p['start_date'] == p['end_date'] else f" – {_fmt(p['end_date'])}") +
                             (' ஈட்டிய' if p['leave_type'] == 'earned' else ' மருத்துவ') for p in prev) or '—'
        rows = [('1. விண்ணப்பதார் பெயர்', name), ('2. பதவியின் பெயர்', desig),
                ('3. பணிபுரியும் துறை / பிரிவு / அலுவலகம்', work),
                ('4. ஊதியம்', ('ரூ. ' + _s(f.get('salary'), 20)) if _s(f.get('salary'), 20) else ''),
                ('5.(1) விண்ணப்பிக்கும் விடுப்பின் தன்மை', 'ஈட்டிய விடுப்பு' if lt == 'earned' else 'மருத்துவ விடுப்பு'),
                ('     (2) விடுப்பின் கால அளவு / விடுப்பில் செல்லும் நாள்', period),
                ('6.(3) மருத்துவ சான்று இணைக்கப்பட்டுள்ளதா?', 'ஆம்' if med else 'இல்லை'),
                ('7. விடுப்பில் செல்லக் காரணம்', reason),
                ('8. வார விடுமுறை / அரசு விடுமுறை இணைப்பு உத்தேசிக்கப்பட்டுள்ளதா?', gov_txt),
                ('9. இதற்கு முன் விடுப்பு விண்ணப்பித்திருந்தால் கால அளவு மற்றும் தன்மை', _s(f.get('prev_leave')) or prev_txt),
                ('10. விடுப்பின் போது முகவரி', addr),
                ('11. தமிழ்நாடு விடுப்பு விதிகளின் விதி 5 கீழ் விதிமுறை 4-ன்படி உறுதிமொழி இணைக்கப்பட்டுள்ளதா?',
                 'ஆம்' if _s(f.get('tamil_rule'), 4) == 'ஆம்' else 'இல்லை')]
    return TITLES[lt], rows, apply_date


def build_pdf(title, rows, apply_date):
    pdf = _new_pdf()
    W = pdf.w - pdf.l_margin - pdf.r_margin
    top = pdf.get_y() - 4
    for txt, size in (('தமிழ்நாடு அரசு பொது நூலகத்துறை', 14), ('திண்டுக்கல் மாவட்ட நூலக ஆணைக்குழு', 13)):
        pdf.set_font('Tamil', 'B', size)
        pdf.cell(0, 8, txt, align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.set_font('Tamil', 'B', 12.5)
    pdf.cell(0, 9, title, align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(2)
    pdf.set_font('Tamil', '', 11)
    with pdf.table(col_widths=(W * 0.50, W * 0.50), borders_layout='NONE', line_height=6.2, padding=1.6) as t:
        for k, v in rows:
            r = t.row()
            r.cell(k)
            r.cell(': ' + str(v))
    pdf.ln(12)
    y = pdf.get_y()
    pdf.set_font('Tamil', '', 11)
    pdf.cell(W / 2, 6.5, 'திண்டுக்கல்', new_x='RIGHT', new_y='TOP')
    pdf.cell(W / 2, 6.5, 'தங்கள் உண்மையுள்ள', align='R', new_x='LMARGIN', new_y='NEXT')
    pdf.cell(0, 6.5, 'நாள்: ' + apply_date, new_x='LMARGIN', new_y='NEXT')
    pdf.ln(12)
    pdf.cell(0, 6.5, 'பணியாளர் கையொப்பம்', align='R', new_x='LMARGIN', new_y='NEXT')
    pdf.rect(pdf.l_margin - 3, top, W + 6, pdf.get_y() - top + 3)
    return bytes(pdf.output())


def _prepare(p):
    r, staff = _record(p.get('record_id'))
    title, rows, apply_date = _form(r, staff, p)
    return r, staff, build_pdf(title, rows, apply_date), title


def _filename(r, staff):
    return f"{staff['staff_no']}_{r['leave_type']}_{r['start_date'].isoformat()}.pdf"


@leave_apply.get('/leave/api/apply-info')
def apply_info():
    """விடுப்புப் பதிவுக்குப் பின் விண்ணப்பம் தயாரிக்கலாமா? (நிரந்தரப் பணியாளர் + 3 வகை)"""
    try:
        r, staff = _record(request.args.get('record_id'))
    except ValueError as e:
        return jsonify(success=False, message=str(e))
    return jsonify(success=True, designations=DESIGNATIONS, leaveType=r['leave_type'], title=TITLES[r['leave_type']],
                   period=_period_text(r), name=staff['staff_name'], library=staff['lib'], today=_today().isoformat())


@leave_apply.post('/leave/api/apply/pdf')
def apply_pdf():
    p = request.get_json(silent=True) or {}
    try:
        r, staff, pdf, _ = _prepare(p)
    except ValueError as e:
        return jsonify(success=False, message=str(e)), 400
    return Response(pdf, mimetype='application/pdf',
                    headers={'Content-Disposition': f'attachment; filename="{_filename(r, staff)}"'})


@leave_apply.post('/leave/api/apply/email')
def apply_email():
    p = request.get_json(silent=True) or {}
    to = mailer.valid_emails(p.get('email'))[:1]
    if not to:
        return jsonify(success=False, message='சரியான மின்னஞ்சல் முகவரியை உள்ளிடவும்.')
    if not mailer.configured():
        return jsonify(success=False, message='மின்னஞ்சல் அமைப்பு இன்னும் சேர்க்கப்படவில்லை; PDF-ஐ நேரடியாகப் பதிவிறக்கவும்.')
    try:
        r, staff, pdf, title = _prepare(p)
    except ValueError as e:
        return jsonify(success=False, message=str(e))
    now = time.time()
    log = [t for t in _MAIL_LOG.get(staff['id'], []) if now - t < 3600]
    if len(log) >= _MAIL_MAX_PER_HOUR:
        return jsonify(success=False, message='ஒரு மணி நேரத்தில் அதிகமான மின்னஞ்சல் கோரிக்கைகள்; PDF-ஐ நேரடியாகப் பதிவிறக்கவும்.')
    body = (f"<div style='font-family:Arial,sans-serif;font-size:14px'><p>வணக்கம் <b>{staff['staff_name']}</b>,</p>"
            f"<p>உங்கள் <b>{title}</b> (PDF) இணைக்கப்பட்டுள்ளது. அச்சிட்டு, கையொப்பமிட்டு அலுவலகத்தில் சமர்ப்பிக்கவும்.</p>"
            "<p style='font-size:12px;color:#64748b'>தமிழ்நாடு அரசு பொது நூலகத்துறை — திண்டுக்கல் மாவட்ட நூலக ஆணைக்குழு</p></div>")
    try:
        mailer.send_email(to, title, body, [(_filename(r, staff), pdf)])
    except RuntimeError as e:
        return jsonify(success=False, message=str(e))
    log.append(now)
    _MAIL_LOG[staff['id']] = log
    return jsonify(success=True, message='விண்ணப்பம் ' + mailer.mask_email(to[0]) + ' முகவரிக்கு அனுப்பப்பட்டது.')
