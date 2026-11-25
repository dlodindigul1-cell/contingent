"""Independent library-receipts module. Existing contingent routes are untouched."""
import calendar, datetime as dt, io, re, hmac, os
from decimal import Decimal, InvalidOperation
from flask import Blueprint, current_app, jsonify, request, session, render_template
import db

receipts = Blueprint('receipts', __name__)
PDF_PASSWORD = os.environ.get('RECEIPTS_PDF_PASSWORD', 'EKQ002866')
RESET_PASSWORD = os.environ.get('RECEIPTS_RESET_PASSWORD', 'Dlodgl@789')
EXCLUDED = ('REPAYMENT CREDIT', 'SWEEP TRANSFER', 'SWEEP', 'CLOSURE PROCEEDS')


def _admin(): return bool(session.get('admin'))
def _librarian(): return bool(session.get('librarian'))
def _valid_month(value):
    value = str(value or '').strip()
    if not re.fullmatch(r'(0[1-9]|1[0-2])/\d{4}', value):
        raise ValueError('மாதத்தை MM/YYYY வடிவில் குறிப்பிடவும். உதா: 06/2026')
    return value

def _money(v):
    try:
        n=Decimal(str(v or '0').replace(',','').strip())
        if not n.is_finite() or n < 0: raise ValueError()
        return n.quantize(Decimal('0.01'))
    except Exception: raise ValueError('தொகை சரியாக இல்லை.')


def _split_upi(particulars):
    """'UPI/CR/630249538322/BABU V/SBIN/babukalai66@oks/U' -> ('630249538322','BABU V-babukalai66@oks').
    பெயர் + UPI ID (வங்கிக் குறியீடு நீக்கப்படும்); UPI ID இல்லையெனில் பெயர் மட்டும்."""
    m=re.match(r'\s*UPI/CR/(\d+)(?:/(.*))?',str(particulars or ''),re.I)
    if not m: return '',''
    parts=[re.sub(r'\s+',' ',x).strip() for x in (m.group(2) or '').split('/')]
    name=parts[0] if parts else ''
    vpa=parts[2] if len(parts)>2 else ''
    return m.group(1),'-'.join(x for x in (name,vpa) if x)

def _month_key(m): return m[3:]+m[:2]

@receipts.get('/receipts')
def page():
    if _admin(): return render_template('receipts_admin.html')
    if _librarian(): return render_template('receipts_librarian.html')
    return ('உள்நுழைவு தேவை', 401)

@receipts.get('/receipts/reports')
def reports_page():
    """வரவின அறிக்கைகள் — தனிப் பக்கம் (Admin மட்டும்)."""
    if not _admin(): return ('உள்நுழைவு தேவை — Admin ஆகப் புகுபதிவு செய்யவும்', 401)
    return render_template('receipts_reports.html')

@receipts.get('/receipts/admin/months')
def admin_months():
    """மாதத் தேர்வுப் பட்டியல் மட்டும் (தரவு இல்லை) — பக்கம் திறக்கும்போது தானாகத் தரவு ஏற்றாமல் இருக்க."""
    if not _admin(): return jsonify(success=False),403
    months=[r['month_year'] for r in db.fetch_all("SELECT DISTINCT month_year FROM (SELECT month_year FROM receipt_bank_transactions UNION SELECT month_year FROM library_receipts) x")]
    months=sorted([m for m in months if m and len(m)==7],key=_month_key,reverse=True)
    return jsonify(success=True,months=months)

@receipts.post('/receipts/admin/upload')
def upload_statement():
    if not _admin(): return jsonify(success=False,message='Admin அனுமதி தேவை'),403
    f=request.files.get('statement')
    if not f or not f.filename.lower().endswith('.pdf'):
        return jsonify(success=False,message='PDF பாஸ்புத்தகத்தைத் தேர்ந்தெடுக்கவும்.'),400
    try:
        from pypdf import PdfReader
        reader=PdfReader(io.BytesIO(f.read()))
        if reader.is_encrypted and not reader.decrypt(PDF_PASSWORD):
            return jsonify(success=False,message='PDF கடவுச்சொல் மூலம் திறக்க முடியவில்லை.'),400
        text='\n'.join(p.extract_text() or '' for p in reader.pages)
    except Exception as e:
        current_app.logger.exception('Bank PDF parse')
        return jsonify(success=False,message='PDF வாசிக்க முடியவில்லை: '+str(e)),400
    try:
        month = _valid_month(request.form.get('month'))
    except ValueError as e:
        return jsonify(success=False,message=str(e)),400
    rows=[]
    # Statement rows: date + particulars + optional cheque no + withdrawal + deposit + balance.
    for line in text.splitlines():
        up=line.upper()
        if any(x in up for x in EXCLUDED): continue
        if 'UPI/CR/' not in up: continue
        m=re.search(r'(\d{2}-[A-Za-z]{3}-\d{4}).*?(UPI/CR/\d+(?:/.*?)?)\s+(\d[\d,]*\.\d{2})\s+(\d[\d,]*\.\d{2})\s*CR',line,re.I)
        if not m: continue
        date_s,particulars,a,b=m.groups()
        # "UPI/CR/627901018885/ANANTHAK/SBIN/..." -> எண்: 627901018885, பெயர்: ANANTHAK
        upi,payer=_split_upi(particulars)
        if not upi: continue
        # Deposit is the amount which increases the balance; statement's last two numbers are deposit and balance.
        try: amount=Decimal(a.replace(',',''))
        except InvalidOperation: continue
        rows.append((date_s,upi,payer,amount))
    if not rows:
        return jsonify(success=False,message='UPI/CR வரிகள் தானாகப் பிரிக்கப்படவில்லை. PDF உரை அமைப்பைச் சரிபார்க்கவும்.'),422
    def save(conn):
        with conn.cursor() as c:
            c.execute("INSERT INTO receipt_statement_batches(month_year,filename,uploaded_by) VALUES(%s,%s,%s) RETURNING id",(month,f.filename,session.get('admin_user','Admin')))
            bid=c.fetchone()[0]
            for date_s,upi,payer,amount in rows:
                c.execute("INSERT INTO receipt_bank_transactions(batch_id,month_year,transaction_date,upi_number,payer_name,amount,status) VALUES(%s,%s,to_date(%s,'DD-Mon-YYYY'),%s,%s,%s,'available') ON CONFLICT(upi_number) DO UPDATE SET payer_name=EXCLUDED.payer_name WHERE receipt_bank_transactions.payer_name IS DISTINCT FROM EXCLUDED.payer_name",(bid,month,date_s,upi,payer,amount))
    db.run(save)
    return jsonify(success=True,month=month,inserted=len(rows),message=f'{month} மாதத்திற்கான UPI பரிவர்த்தனைகள் ஏற்றப்பட்டன. பொருந்தாத வரிகளை Admin சரிபார்க்க வேண்டும்.')

@receipts.post('/receipts/admin/reset-month')
def reset_month():
    """Destructively clear every receipts-module record for one selected month.
    Admin-only and protected by a separate confirmation password.
    Keeps the audit trail of the reset itself.
    """
    if not _admin():
        return jsonify(success=False, message='Admin அனுமதி தேவை'), 403
    j = request.get_json(silent=True) or {}
    try:
        month = _valid_month(j.get('month'))
    except ValueError as e:
        return jsonify(success=False, message=str(e)), 400
    supplied = str(j.get('password') or '')
    if not hmac.compare_digest(supplied, RESET_PASSWORD):
        return jsonify(success=False, message='கடவுச்சொல் தவறானது.'), 403
    def clear_month(conn):
        with conn.cursor() as c:
            # Delete only the selected month from the new receipts module.
            c.execute('DELETE FROM receipt_bank_transactions WHERE month_year=%s', (month,))
            deleted_transactions = c.rowcount
            c.execute('DELETE FROM library_receipts WHERE month_year=%s', (month,))
            deleted_receipts = c.rowcount
            c.execute('DELETE FROM receipt_statement_batches WHERE month_year=%s', (month,))
            deleted_batches = c.rowcount
            c.execute("INSERT INTO receipt_admin_audit(transaction_id,action,reason,admin_name) VALUES(NULL,'reset_month',%s,%s)",
                      (f'{month} மாதத்தின் அனைத்து வரவு/UPI/பாஸ்புத்தக பதிவுகளும் அழிக்கப்பட்டன. Tx={deleted_transactions}, Receipts={deleted_receipts}, Batches={deleted_batches}', session.get('admin_user','Admin')))
            return deleted_transactions, deleted_receipts, deleted_batches
    try:
        tx_count, receipt_count, batch_count = db.run(clear_month)
    except Exception as e:
        current_app.logger.exception('Receipt month reset failed')
        return jsonify(success=False, message='அழிக்கும் நடவடிக்கை தோல்வியடைந்தது.'), 500
    return jsonify(success=True, month=month, deleted_transactions=tx_count,
                   deleted_receipts=receipt_count, deleted_batches=batch_count,
                   message=f'{month} மாதத்திற்கான வரவு, UPI மற்றும் பாஸ்புத்தகப் பதிவுகள் அழிக்கப்பட்டன. Audit பதிவு பாதுகாக்கப்பட்டுள்ளது.')


@receipts.get('/receipts/admin/transactions')
def admin_transactions():
    if not _admin(): return jsonify(success=False),403
    month=(request.args.get('month') or '').strip()
    where=" WHERE month_year=%s" if month else ""
    rows=db.fetch_all("SELECT id,month_year,to_char(transaction_date,'DD/MM/YYYY') transaction_date,upi_number,payer_name,amount,status,library_name FROM receipt_bank_transactions"+where+" ORDER BY transaction_date, id",(month,) if month else None)
    return jsonify(rows=[dict(r) for r in rows])


@receipts.get('/receipts/admin/dashboard')
def dashboard():
    """இடது: தேர்ந்த மாதத்தில் இதுவரை வரவு பதிவு செய்யாத நூலகங்கள்; வலது: எந்த நூலகமும் பதிவு செய்யாத UPI எண்கள்."""
    if not _admin(): return jsonify(success=False),403
    months=[r['month_year'] for r in db.fetch_all("SELECT DISTINCT month_year FROM (SELECT month_year FROM receipt_bank_transactions UNION SELECT month_year FROM library_receipts) x")]
    months.sort(key=_month_key,reverse=True)
    month=(request.args.get('month') or '').strip() or (months[0] if months else '')
    libs=db.fetch_all("SELECT name,lib_type FROM libraries WHERE (inactive_from IS NULL OR inactive_from > to_date('01/' || %s, 'DD/MM/YYYY')) ORDER BY id",(month or '01/2000',))
    done={r['library_name'] for r in db.fetch_all("SELECT library_name FROM library_receipts WHERE month_year=%s",(month,))}
    not_reg=[dict(l) for l in libs if l['name'] not in done]
    upis=db.fetch_all("SELECT id,to_char(transaction_date,'DD/MM/YYYY') transaction_date,upi_number,payer_name,amount FROM receipt_bank_transactions WHERE month_year=%s AND status='available' ORDER BY transaction_date,id",(month,))
    out=[]
    for r in upis:
        r=dict(r); n,nm=_split_upi(r['upi_number'])
        if n: r['upi_number']=n; r['payer_name']=r.get('payer_name') or nm  # பழைய பதிவுகளுக்கும் எண் மட்டும்
        out.append(r)
    return jsonify(success=True,month=month,months=months,total_libraries=len(libs),registered_count=len(libs)-len(not_reg),
                   not_registered_libraries=not_reg,unregistered_upis=out)

@receipts.get('/receipts/libraries')
def receipt_libraries():
    if not (_admin() or _librarian()): return jsonify(success=False),403
    rows=db.fetch_all('SELECT name,lib_type FROM libraries WHERE (inactive_from IS NULL OR inactive_from > CURRENT_DATE) ORDER BY id')
    return jsonify(rows=[dict(r) for r in rows])

@receipts.get('/receipts/available')
def available_transactions():
    if not _librarian(): return jsonify(success=False),403
    month=request.args.get('month','')
    rows=db.fetch_all("SELECT id,to_char(transaction_date,'DD/MM/YYYY') transaction_date,upi_number,payer_name,amount FROM receipt_bank_transactions WHERE month_year=%s AND status='available' AND uploaded_at >= now()-interval '5 days' ORDER BY transaction_date,id",(month,))
    return jsonify(rows=[dict(r) for r in rows])

@receipts.post('/receipts/submit')
def submit_receipt():
    if not _librarian(): return jsonify(success=False,message='நூலகர் உள்நுழைவு தேவை'),403
    j=request.get_json(silent=True) or {}; lib=str(j.get('library') or '').strip(); month=str(j.get('month') or '').strip(); ids=j.get('transaction_ids') or []
    if not lib or not month or not ids: return jsonify(success=False,message='நூலகம், மாதம், UPI பரிவர்த்தனைகளைத் தேர்ந்தெடுக்கவும்.'),400
    try: security=_money(j.get('security')); annual=_money(j.get('annual')); newspaper=_money(j.get('newspaper'))
    except ValueError as e: return jsonify(success=False,message=str(e)),400
    total=security+annual+newspaper
    def save(conn):
        with conn.cursor() as c:
            c.execute('SELECT id,inactive_from FROM libraries WHERE name=%s',(lib,))
            lrow=c.fetchone()
            if not lrow: raise ValueError('நூலகம் கிடைக்கவில்லை.')
            if lrow[1] and re.fullmatch(r'\d{2}/\d{4}',month) and lrow[1]<=dt.date(int(month[3:]),int(month[:2]),1): raise ValueError('இந்த நூலகம் செயல்படவில்லை.')
            c.execute("SELECT id,amount FROM receipt_bank_transactions WHERE id=ANY(%s) AND month_year=%s AND status='available' AND uploaded_at>=now()-interval '5 days' FOR UPDATE",(list(map(int,ids)),month))
            trs=c.fetchall()
            if len(trs)!=len(set(map(int,ids))): raise ValueError('UPI எண் ஏற்கனவே பயன்படுத்தப்பட்டுள்ளது அல்லது காலவரம்பு முடிந்துள்ளது.')
            bank_total=sum((Decimal(str(r[1])) for r in trs),Decimal('0'))
            if total != bank_total: raise ValueError(f'தொகை பொருந்தவில்லை. UPI மொத்தம் ₹{bank_total}; இனங்களின் மொத்தம் ₹{total}.')
            c.execute("SELECT id,security_amount,annual_amount,newspaper_amount FROM library_receipts WHERE library_name=%s AND month_year=%s AND status='pending' FOR UPDATE",(lib,month))
            old=c.fetchone()
            if old:
                rid=old[0]; security+=Decimal(str(old[1])); annual+=Decimal(str(old[2])); newspaper+=Decimal(str(old[3])); total=security+annual+newspaper+ (bank_total - (security+annual+newspaper))
                # The category figures are cumulative; require this submission's category increment to match this batch.
                c.execute("UPDATE library_receipts SET security_amount=%s,annual_amount=%s,newspaper_amount=%s,total_amount=total_amount+%s WHERE id=%s",(security,annual,newspaper,bank_total,rid))
            else:
                c.execute("INSERT INTO library_receipts(library_name,month_year,security_amount,annual_amount,newspaper_amount,total_amount,status,created_by) VALUES(%s,%s,%s,%s,%s,%s,'pending',%s) RETURNING id",(lib,month,security,annual,newspaper,bank_total,session.get('librarian_email','Librarian')))
                rid=c.fetchone()[0]
            for tid in ids:
                c.execute("UPDATE receipt_bank_transactions SET status='reserved',library_name=%s,receipt_id=%s WHERE id=%s",(lib,rid,int(tid)))
    try: db.run(save)
    except Exception as e: return jsonify(success=False,message=str(e)),400
    return jsonify(success=True,message='பதிவு சேமிக்கப்பட்டது. Admin ஒப்புதல் நிலுவையில் உள்ளது.')

_PENDING_SQL = ("SELECT r.*, COALESCE(json_agg(json_build_object('upi',t.upi_number,'amount',t.amount,'date',t.transaction_date)) "
                "FILTER(WHERE t.id IS NOT NULL),'[]') transactions FROM library_receipts r "
                "LEFT JOIN receipt_bank_transactions t ON t.receipt_id=r.id WHERE {where} "
                "AND NOT EXISTS (SELECT 1 FROM libraries l WHERE l.name=r.library_name AND l.inactive_from IS NOT NULL "
                "AND l.inactive_from <= to_date('01/' || r.month_year, 'DD/MM/YYYY')) GROUP BY r.id ORDER BY {order}")

@receipts.get('/receipts/admin/pending')
def pending():
    """நிலுவையில் உள்ள (ஒப்புதல் அளிக்கப்படாத) பதிவுகள் மட்டும்."""
    if not _admin(): return jsonify(success=False),403
    month=(request.args.get('month') or '').strip()
    where="r.status='pending'"+(" AND r.month_year=%s" if month else "")
    rows=db.fetch_all(_PENDING_SQL.format(where=where,order="r.created_at DESC"),(month,) if month else None)
    return jsonify(rows=[dict(r) for r in rows])

@receipts.get('/receipts/admin/approved')
def approved_list():
    """ஒப்புதல் பெற்ற பதிவுகள் (மாதம் / நூலகப் பெயர் வடிகட்டி; அதிகபட்சம் 600)."""
    if not _admin(): return jsonify(success=False),403
    month=(request.args.get('month') or '').strip(); q=(request.args.get('q') or '').strip()
    months=[m['month_year'] for m in db.fetch_all("SELECT DISTINCT month_year FROM library_receipts WHERE status='approved'")]
    months=sorted([m for m in months if m and len(m)==7],key=lambda m:(m[3:],m[:2]),reverse=True)
    cond,args=["r.status='approved'"],[]
    if month: cond.append("r.month_year=%s"); args.append(month)
    if q: cond.append("r.library_name ILIKE %s"); args.append('%'+q+'%')
    sql=_PENDING_SQL.format(where=" AND ".join(cond),order="r.library_name")+" LIMIT 600"
    rows=db.fetch_all(sql,tuple(args))
    return jsonify(months=months,rows=[dict(r) for r in rows],limited=len(rows)>=600)

@receipts.post('/receipts/admin/approve/<int:rid>')
def approve(rid):
    if not _admin(): return jsonify(success=False),403
    n=db.execute("UPDATE library_receipts SET status='approved',approved_by=%s,approved_at=now() WHERE id=%s AND status='pending'",(session.get('admin_user','Admin'),rid))
    if not n:return jsonify(success=False,message='நிலுவைப் பதிவு கிடைக்கவில்லை.'),404
    db.execute("UPDATE receipt_bank_transactions SET status='used' WHERE receipt_id=%s",(rid,))
    return jsonify(success=True,message='ஒப்புதல் வழங்கப்பட்டது.')

@receipts.post('/receipts/admin/delete-transaction/<int:tid>')
def release_transaction(tid):
    if not _admin(): return jsonify(success=False),403
    j=request.get_json(silent=True) or {}; reason=str(j.get('reason') or '').strip()
    if not reason:return jsonify(success=False,message='நீக்கக் காரணம் அவசியம்.'),400
    def work(conn):
        with conn.cursor() as c:
            c.execute('SELECT id,receipt_id FROM receipt_bank_transactions WHERE id=%s FOR UPDATE',(tid,)); row=c.fetchone()
            if not row: raise ValueError('UPI பதிவு கிடைக்கவில்லை.')
            c.execute("DELETE FROM receipt_bank_transactions WHERE id=%s",(tid,))
            if row[1]:
                c.execute('SELECT count(*) FROM receipt_bank_transactions WHERE receipt_id=%s',(row[1],))
                if c.fetchone()[0]==0:c.execute("DELETE FROM library_receipts WHERE id=%s AND status='pending'",(row[1],))
            c.execute("INSERT INTO receipt_admin_audit(transaction_id,action,reason,admin_name) VALUES(%s,'delete_release',%s,%s)",(tid,reason,session.get('admin_user','Admin')))
    try:db.run(work)
    except Exception as e:return jsonify(success=False,message=str(e)),400
    return jsonify(success=True,message='குறிப்பிட்ட UPI பதிவு மட்டும் நீக்கப்பட்டது.')


# ------------------------------------------------------------------
#  நூலகர் படிவம் (index.html) — RPC செயல்பாடுகள்
# ------------------------------------------------------------------
def _prev_month():
    ist=dt.timezone(dt.timedelta(hours=5,minutes=30))
    t=dt.datetime.now(ist).date().replace(day=1)-dt.timedelta(days=1)
    return f'{t.month:02d}/{t.year}'

def _fail(msg): return {'success':False,'message':msg}

def _can_use(): return _admin() or _librarian()

def _int(v):
    try: n=int(float(v or 0))
    except Exception: raise ValueError('எண் மதிப்பு சரியாக இல்லை.')
    if n<0: raise ValueError('எதிர் மதிப்பு அனுமதிக்கப்படாது.')
    return n

def validatePaymentWithBank(utr, lib=None):
    """UPI எண் -> தேதி + தொகை (நூலகர் 'சரி' என உறுதி செய்ய)."""
    if not _can_use(): return _fail('உள்நுழைவு தேவை')
    utr=re.sub(r'\D','',str(utr or ''))
    if not re.fullmatch(r'\d{11,12}',utr): return _fail('சரியான 11 அல்லது 12 இலக்க UPI எண்ணை உள்ளிடவும்.')
    r=db.fetch_one("SELECT id,month_year,to_char(transaction_date,'YYYY-MM-DD') d,amount,payer_name,status,library_name FROM receipt_bank_transactions WHERE upi_number=%s",(utr,))
    if not r: return _fail('இந்த UPI எண் வங்கிப் பாஸ்புத்தகத்தில் கிடைக்கவில்லை. எண்ணைச் சரிபார்க்கவும்; புதிய பாஸ்புத்தகம் Admin ஏற்றிய பின் மீண்டும் முயலவும்.')
    if r['status']=='used' or (r['status']=='reserved' and r['library_name']!=lib):
        return _fail('இந்த UPI எண் ஏற்கனவே ஒரு நூலகத்தால் பயன்படுத்தப்பட்டுள்ளது.')
    return {'success':True,'upi':utr,'amount':float(r['amount']),'date':r['d'],'name':r['payer_name'] or '','month':r['month_year']}

def getReceiptStockInfo(lib):
    """நூல் இருப்பை முதல் முறையாகப் பதிவு செய்கிறாரா?"""
    if not _can_use(): return _fail('உள்நுழைவு தேவை')
    r=db.fetch_one("SELECT count(*) c FROM library_receipts WHERE library_name=%s AND book_stock>0",(str(lib or ''),))
    return {'success':True,'first':(r['c']==0)}

def saveOrUpdateReceipt(data):
    if not _can_use(): return _fail('உள்நுழைவு தேவை')
    data=data or {}
    lib=str(data.get('libraryName') or '').strip()
    mode=str(data.get('mode') or '').strip()
    if mode not in ('income','usage'): return _fail('பதிவு வகையைத் தேர்வு செய்யவும்.')
    try:
        visitors,issues,usage,stock=_int(data.get('visitors')),_int(data.get('issues')),_int(data.get('usage')),_int(data.get('stock'))
        heads={k:_int(data.get(k)) for k in ('deposit','subscription','lateFee','bookFine','oldPaperSale','oldBookSale','patron','chiefPatron','donor','otherIncome')}
    except ValueError as e: return _fail(str(e))
    if min(visitors,issues,usage,stock)<=0: return _fail('வாசகர், நூலிரவல், நூல்கள் பயன்பாடு, நூல் இருப்பு ஆகியவை 0-ஐ விட அதிகமாக இருக்க வேண்டும்.')
    utrs=[]
    if mode=='income':
        raw=data.get('utrs') or re.split(r'[,\s]+',str(data.get('utr') or ''))
        utrs=list(dict.fromkeys(re.sub(r'\D','',str(x)) for x in raw if re.sub(r'\D','',str(x))))
        if not utrs: return _fail('UPI எண்ணைச் சரிபார்த்து உறுதி செய்யவும்.')
    else:
        heads={k:0 for k in heads}
    total=sum(heads.values())
    def save(conn):
        with conn.cursor() as c:
            c.execute('SELECT 1 FROM libraries WHERE name=%s',(lib,))
            if not c.fetchone(): raise ValueError('நூலகம் கிடைக்கவில்லை.')
            trs=[]
            if mode=='income':
                c.execute('SELECT id,upi_number,amount,month_year,status,library_name,transaction_date FROM receipt_bank_transactions WHERE upi_number=ANY(%s) FOR UPDATE',(utrs,))
                trs=c.fetchall()
                if len(trs)!=len(utrs): raise ValueError('ஒரு UPI எண் வங்கிப் பாஸ்புத்தகத்தில் இல்லை.')
                months={t[3] for t in trs}
                if len(months)!=1: raise ValueError('வெவ்வேறு மாத UPI எண்களை ஒரே பதிவில் சேர்க்க முடியாது.')
                month=months.pop()
                for t in trs:
                    if t[4]=='used' or (t[4]=='reserved' and t[5]!=lib): raise ValueError(f'UPI {t[1]} ஏற்கனவே பயன்படுத்தப்பட்டுள்ளது.')
                bank=sum((Decimal(str(t[2])) for t in trs),Decimal('0'))
                if bank!=Decimal(total): raise ValueError(f'தொகை பொருந்தவில்லை. UPI மொத்தம் ₹{bank}; இனங்களின் மொத்தம் ₹{total}.')
                pdate=min(t[6] for t in trs)
            else:
                month=_prev_month(); pdate=None
            c.execute("SELECT inactive_from FROM libraries WHERE name=%s",(lib,))
            inf=c.fetchone()[0]
            if inf and inf<=dt.date(int(month[3:]),int(month[:2]),1): raise ValueError(f'இந்த நூலகம் {inf.strftime("%d/%m/%Y")} முதல் செயல்படவில்லை; {month} மாதத்திற்குப் பதிவு செய்ய இயலாது.')
            c.execute('SELECT id,status FROM library_receipts WHERE library_name=%s AND month_year=%s FOR UPDATE',(lib,month))
            old=c.fetchone()
            if old and old[1]=='approved': raise ValueError(f'{month} மாதப் பதிவு ஏற்கனவே ஒப்புதல் பெற்றுள்ளது. மாற்ற Admin-ஐ அணுகவும்.')
            vals=(heads['deposit'],heads['subscription'],heads['oldPaperSale'],total,mode,heads['lateFee'],heads['bookFine'],heads['oldBookSale'],heads['patron'],heads['chiefPatron'],heads['donor'],heads['otherIncome'],pdate,visitors,issues,usage,stock)
            if old:
                rid=old[0]; action='updated'
                c.execute("UPDATE receipt_bank_transactions SET status='available',library_name=NULL,receipt_id=NULL WHERE receipt_id=%s",(rid,))
                c.execute("UPDATE library_receipts SET security_amount=%s,annual_amount=%s,newspaper_amount=%s,total_amount=%s,mode=%s,late_fee=%s,book_fine=%s,old_book_sale=%s,patron=%s,chief_patron=%s,donor=%s,other_income=%s,payment_date=%s,visitors=%s,issues=%s,book_usage=%s,book_stock=%s WHERE id=%s",vals+(rid,))
            else:
                action='saved'
                c.execute("INSERT INTO library_receipts(library_name,month_year,security_amount,annual_amount,newspaper_amount,total_amount,mode,late_fee,book_fine,old_book_sale,patron,chief_patron,donor,other_income,payment_date,visitors,issues,book_usage,book_stock,status,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s) RETURNING id",(lib,month)+vals+(session.get('librarian_email','Librarian'),))
                rid=c.fetchone()[0]
            for t in trs:
                c.execute("UPDATE receipt_bank_transactions SET status='reserved',library_name=%s,receipt_id=%s WHERE id=%s",(lib,rid,t[0]))
            return rid,month,action
    try: rid,month,action=db.run(save)
    except ValueError as e: return _fail(str(e))
    except Exception:
        current_app.logger.exception('saveOrUpdateReceipt')
        return _fail('சேமிக்க முடியவில்லை. மீண்டும் முயலவும்.')
    return {'success':True,'row':rid,'month':month,'total':total,'action':action}


# ------------------------------------------------------------------
#  வரவின அறிக்கைகள் (Admin) — daily | upi | monthly
#  ஒரே வடிவ JSON: columns [{title,type}], rows [[...]], totals [...]; திரை / PDF / Excel மூன்றும் இதையே பயன்படுத்தும்.
#  type: no | text | date | multi (பல வரி) | money | total | int
# ------------------------------------------------------------------
REPORT_HEADS = [
    ('காப்புத்தொகை', 'security_amount'), ('சந்தாத் தொகை', 'annual_amount'),
    ('காலதாமதக் கட்டணம்', 'late_fee'), ('நூல் விலைப் பிடித்தம்', 'book_fine'),
    ('பழைய பத்திரிக்கை விற்பனை', 'newspaper_amount'), ('பழைய நூல்கள் விற்பனை', 'old_book_sale'),
    ('புரவலர்', 'patron'), ('பெரும்புரவலர்', 'chief_patron'), ('கொடையாளர்', 'donor'),
    ('இதர வரவுகள்', 'other_income'),
]
REPORT_TITLES = {
    'daily': 'மாத வரவுகள் — வங்கி அறிக்கைப்படி',
    'upi': 'மாத வரவுகள் — வங்கி அறிக்கைப்படி (As per UPI)',
    'monthly': 'மாதாந்திர வரவின அறிக்கை (மொத்தம் — புள்ளி விவரத்துடன்)',
}
REPORT_FILES = {'daily': 'Receipts_Daily', 'upi': 'Receipts_UPI', 'monthly': 'Receipts_Monthly'}


def _fdate(d):
    """date / 'YYYY-MM-DD' -> 'DD/MM/YYYY'"""
    if not d: return ''
    s = str(d)[:10]
    return '/'.join(reversed(s.split('-'))) if re.fullmatch(r'\d{4}-\d{2}-\d{2}', s) else s


def _num(v):
    n = Decimal(str(v or 0))
    return int(n) if n == n.to_integral_value() else float(n)


def _report_rows(month, include_pending):
    """தேர்ந்த மாதத்தின் வரவுப் பதிவுகள் (நூலகப் பட்டியல் வரிசையில்) + அவற்றின் UPI பரிவர்த்தனைகள்."""
    where = "r.month_year=%s" + ("" if include_pending else " AND r.status='approved'")
    rows = db.fetch_all(
        "SELECT r.id, r.library_name, r.mode, r.status, r.total_amount, r.payment_date, r.payment_dates, r.upi_numbers, "
        "r.visitors, r.issues, r.book_usage, "
        + ", ".join('r.' + c for _, c in REPORT_HEADS) + ", "
        "COALESCE(json_agg(json_build_object('upi',t.upi_number,'date',to_char(t.transaction_date,'YYYY-MM-DD')) "
        "ORDER BY t.transaction_date,t.id) FILTER (WHERE t.id IS NOT NULL),'[]') txs "
        "FROM library_receipts r JOIN libraries l ON l.name=r.library_name AND (l.inactive_from IS NULL OR l.inactive_from > to_date('01/' || r.month_year, 'DD/MM/YYYY')) "
        "LEFT JOIN receipt_bank_transactions t ON t.receipt_id=r.id "
        "WHERE " + where + " GROUP BY r.id, l.id ORDER BY l.id", (month,))
    return [dict(r) for r in rows]


def _utr_pairs(r):
    """[(utr, 'YYYY-MM-DD'|'')] — வங்கிப் பதிவிலிருந்து; Excel மூலம் ஏற்றியவற்றுக்கு upi_numbers / payment_dates-இலிருந்து."""
    pairs = [(t['upi'], t['date']) for t in (r.get('txs') or [])]
    seen = {u for u, _ in pairs}
    extra = [u for u in re.split(r'[,;\s]+', str(r.get('upi_numbers') or '')) if u and u not in seen]
    dates = [d for d in re.split(r',\s*', str(r.get('payment_dates') or '')) if d]
    for i, u in enumerate(extra):
        d = dates[i] if i < len(dates) else (str(r['payment_date'])[:10] if r.get('payment_date') else '')
        pairs.append((u, d))
    return pairs


LIB_GROUPS = [('மாவட்ட மைய நூலகம்', 'மாவட்ட மைய நூலகம்'), ('முழு நேர', 'முழு நேர நூலகங்கள்'),
              ('கிளை', 'கிளை நூலகங்கள்'), ('ஊர்ப்புற', 'ஊர்ப்புற நூலகங்கள்'), ('பகுதி நேர', 'பகுதி நேர நூலகங்கள்')]


def _group_of(lib_type):
    t = str(lib_type or '')
    for key, title in LIB_GROUPS:       # வரிசை முக்கியம்: 'முழு நேர கிளை' முதலில் முழு நேரத்தில் சேரும்
        if key in t: return title
    return 'பிற நூலகங்கள்'


def build_receipt_report(kind, month, include_pending=False):
    if kind not in REPORT_TITLES: raise ValueError('தெரியாத அறிக்கை')
    month = _valid_month(month)
    all_rows = _report_rows(month, include_pending)
    # மொத்தம் 0 எனில் UPI எண் எதுவாக இருந்தாலும் காட்டக்கூடாது (பழைய placeholder பதிவுகளுக்கும்)
    for r in all_rows:
        if not Decimal(str(r['total_amount'] or 0)):
            r['txs'] = []; r['upi_numbers'] = ''; r['payment_dates'] = ''; r['payment_date'] = None
    income = [r for r in all_rows if r['mode'] == 'income' and Decimal(str(r['total_amount'] or 0)) > 0]
    pending_skipped = 0
    if not include_pending:
        pending_skipped = db.fetch_one("SELECT count(*) c FROM library_receipts WHERE month_year=%s AND status='pending'", (month,))['c']

    def amounts(r): return [Decimal(str(r[c] or 0)) for _, c in REPORT_HEADS] + [Decimal(str(r['total_amount'] or 0))]

    def sums(rs):
        tot = [Decimal(0)] * (len(REPORT_HEADS) + 1)
        for r in rs: tot = [a + b for a, b in zip(tot, amounts(r))]
        return tot

    # தொகை இல்லாத (கூட்டு 0) இன நெடுவரிசைகளை நீக்கு; 'மொத்தம்' எப்போதும் இருக்கும்
    base = sums(income if kind != 'monthly' else all_rows)
    keep = [i for i in range(len(REPORT_HEADS)) if base[i] != 0] + [len(REPORT_HEADS)]
    money_cols = [{'title': REPORT_HEADS[i][0] if i < len(REPORT_HEADS) else 'மொத்தம்',
                   'type': 'money' if i < len(REPORT_HEADS) else 'total'} for i in keep]

    def pick(vals): return [_num(vals[i]) for i in keep]

    notes = []
    if pending_skipped: notes.append(f'ஒப்புதல் நிலுவையில் உள்ள {pending_skipped} பதிவுகள் சேர்க்கப்படவில்லை (ஒப்புதல் அளித்தபின் சேரும்).')

    if kind == 'daily':
        groups = {}
        for r in income:
            if r['payment_date']: groups.setdefault(str(r['payment_date'])[:10], []).append(r)
        cols = [{'title': 'வ.எண்.', 'type': 'no'}, {'title': 'நாள்', 'type': 'date'}] + money_cols
        out = [[i, _fdate(k)] + pick(sums(groups[k])) for i, k in enumerate(sorted(groups), 1)]
        totals = ['', 'மொத்தம்'] + pick(sums([r for g in groups.values() for r in g]))
        notes.append('ஒரே நாளில் பல நூலகங்கள் பதிவு செய்திருந்தால் அவை கூட்டி ஒரே வரியாகக் காட்டப்படும்.')
    elif kind == 'upi':
        recs = sorted((r for r in income if _utr_pairs(r)), key=lambda r: str(r['payment_date'] or '9999'))
        cols = ([{'title': 'வ.எண்.', 'type': 'no'}, {'title': 'நாள்', 'type': 'multi'},
                 {'title': 'Google Pay / PhonePe Transaction UTR எண்', 'type': 'multi'},
                 {'title': 'நூலகத்தின் பெயர்', 'type': 'text'}] + money_cols)
        out = []
        for i, r in enumerate(recs, 1):
            pairs = _utr_pairs(r)
            out.append([i, '\n'.join(_fdate(d) for _, d in pairs), '\n'.join(u for u, _ in pairs), r['library_name']] + pick(amounts(r)))
        totals = ['', '', '', 'மொத்தம்'] + pick(sums(recs))
    else:  # monthly — வகை வாரியாகத் தலைப்புடன்; புள்ளி விவரப் பதிவுகளும் சேரும்
        cols = ([{'title': 'வ.எண்.', 'type': 'no'}, {'title': 'நூலகத்தின் பெயர்', 'type': 'text'},
                 {'title': 'Google Pay / PhonePe Transaction UTR எண்', 'type': 'multi'},
                 {'title': 'தொகை செலுத்திய நாள்', 'type': 'date'}] + money_cols +
                [{'title': 'நடப்பு மாத வாசகர் வருகை', 'type': 'int'}, {'title': 'நடப்பு மாத நூலிரவல்', 'type': 'int'},
                 {'title': 'நடப்பு மாத நூல்கள் பயன்பாடு', 'type': 'int'}])
        types = {r['name']: r['lib_type'] for r in db.fetch_all('SELECT name, lib_type FROM libraries')}
        order = [t for _, t in LIB_GROUPS] + ['பிற நூலகங்கள்']
        buckets = {t: [] for t in order}
        for r in all_rows: buckets[_group_of(types.get(r['library_name']))].append(r)   # வரிசை: நூலகப் பட்டியல் வரிசை
        out = []; n = 0
        def stats(rs):
            return [sum(r['visitors'] or 0 for r in rs), sum(r['issues'] or 0 for r in rs), sum(r['book_usage'] or 0 for r in rs)]
        for t in order:
            if not buckets[t]: continue
            out.append({'section': t})
            for r in buckets[t]:
                n += 1
                out.append([n, r['library_name'], '\n'.join(u for u, _ in _utr_pairs(r)), _fdate(r['payment_date'])]
                           + pick(amounts(r)) + [r['visitors'] or 0, r['issues'] or 0, r['book_usage'] or 0])
            # ஒவ்வொரு வகை முடிவிலும் அந்த வகையின் மொத்தம்
            out.append({'subtotal': ['', t + ' — மொத்தம்', '', ''] + pick(sums(buckets[t])) + stats(buckets[t])})
        # அறிக்கையின் இறுதியில் வகை வாரியான சுருக்கம் (Abstract)
        out.append({'section': 'வகை வாரியான வரவின சுருக்கம் (Abstract)'})
        for t in order:
            if not buckets[t]: continue
            out.append(['', t, '', ''] + pick(sums(buckets[t])) + stats(buckets[t]))
        totals = ['', 'பெரு மொத்தம்', '', ''] + pick(sums(all_rows)) + stats(all_rows)
        total_libs = db.fetch_one("SELECT count(*) c FROM libraries WHERE (inactive_from IS NULL OR inactive_from > to_date('01/' || %s, 'DD/MM/YYYY'))", (month,))['c']
        notes.append(f'மொத்த நூலகங்கள் {total_libs} — இம்மாதம் பதிவு செய்தவை {len(all_rows)}.')
    return {'success': True, 'kind': kind, 'month': month, 'title': REPORT_TITLES[kind], 'columns': cols,
            'rows': out, 'totals': totals, 'notes': notes, 'includePending': bool(include_pending)}


# ------------------------------------------------------------------
#  நூலகப் புள்ளி விவர அறிக்கைகள்
#    R4 = உறுப்பினர் · புரவலர் · பெரும்புரவலர் · கொடையாளர்      (kind 'stats')
#    R5 = வாசகர் · நூலிரவல் · நூல்கள் பயன்பாடு                  (kind 'usage')
#  இரண்டிலும் இரு வகை: மாதாந்திரம் (mode=month) | நிதியாண்டு (mode=fy)
#    நிதியாண்டு: scope=full (ஏப்ரல்–மார்ச் முழுவதும்) | scope=upto (ஏப்ரல் முதல் தேர்ந்த மாதம் வரை)
#  R4 அடிப்படை = மார்ச் 2024 (library_stats_base); ஏப்ரல் 2024 முதல் ஒப்புதல் பெற்ற வரவுகளிலிருந்து தானாகக் கணக்கிடப்படும்:
#    புதிய உறுப்பினர் = காப்புத் தொகை ÷ அந்த நூலகத்தின் காப்புத் தொகை கட்டணம்;
#    புரவலர் = தொகை ÷ 1000; பெரும்புரவலர் = தொகை ÷ 5000; கொடையாளர் = தொகை ÷ 10000.
#  R4 நிதியாண்டு அறிக்கை: முந்தைய நிதி ஆண்டு = முந்தைய ஆண்டு இறுதி (மார்ச்) வரையிலான மொத்தம்;
#                         நடப்பு நிதி ஆண்டு = நடப்பு ஆண்டு இறுதி (அல்லது தேர்ந்த மாதம்) வரையிலான மொத்தம்.
# ------------------------------------------------------------------
STATS_PATRON_RATES = (Decimal(1000), Decimal(5000), Decimal(10000))
STATS_FIRST_KEY = '202404'
TA_MONTHS = ['ஜனவரி','பிப்ரவரி','மார்ச்','ஏப்ரல்','மே','ஜூன்','ஜூலை','ஆகஸ்ட்','செப்டம்பர்','அக்டோபர்','நவம்பர்','டிசம்பர்']


def _ta_month(my): return TA_MONTHS[int(my[:2]) - 1] + ' ' + my[3:]


def _fy_label(sy): return f'{sy}-{str(sy + 1)[2:]}'


def _stats_period(mode, month, fy, scope, what):
    """அறிக்கைக் காலத்தைத் தீர்மானி. what = 'புள்ளி விவர' / 'வாசகர் · நூலிரவல் · பயன்பாடு' (தலைப்புக்கு)."""
    if mode not in ('month', 'fy'): raise ValueError('அறிக்கை வகையைத் தேர்ந்தெடுக்கவும்.')
    p = {'mode': mode, 'scope': None, 'upto_label': ''}
    if mode == 'fy' and scope not in ('full', 'upto'): scope = 'full'
    if mode == 'fy' and scope == 'full':
        m = re.fullmatch(r'(\d{4})-(\d{2})', str(fy or '').strip())
        if not m or int(m.group(2)) != (int(m.group(1)) + 1) % 100: raise ValueError('நிதியாண்டைத் தேர்ந்தெடுக்கவும் (உதா: 2025-26).')
        sy = int(m.group(1))
        start_key, end_key = f'{sy}04', f'{sy + 1}03'
        p.update(scope='full', subtitle=f'நிதியாண்டு {_fy_label(sy)} (ஏப்ரல் {sy} – மார்ச் {sy + 1})',
                 fname_part=f'FY_{_fy_label(sy)}', title_part='நிதியாண்டு', cur_sub=_fy_label(sy), month_ref=_fy_label(sy))
    else:
        month = _valid_month(month)
        sy = int(month[3:]) if int(month[:2]) >= 4 else int(month[3:]) - 1
        end_key = _month_key(month)
        if mode == 'month':
            start_key = end_key
            p.update(subtitle='மாதம்: ' + _ta_month(month), fname_part=month.replace('/', '-'), title_part='மாதாந்திரம்', cur_sub='', month_ref=month)
        else:
            start_key = f'{sy}04'
            p.update(scope='upto', subtitle=f'ஏப்ரல் {sy} முதல் {_ta_month(month)} வரை (நிதியாண்டு {_fy_label(sy)})',
                     fname_part='upto_' + month.replace('/', '-'), title_part='நிதியாண்டு (குறிப்பிட்ட மாதம் வரை)',
                     cur_sub=f'{_fy_label(sy)} · {_ta_month(month)} வரை', month_ref=month)
    if end_key < STATS_FIRST_KEY: raise ValueError('ஏப்ரல் 2024 மற்றும் அதற்குப் பிந்தைய மாதங்களுக்கு மட்டுமே கணக்கிட முடியும்.')
    if start_key < STATS_FIRST_KEY: start_key = STATS_FIRST_KEY
    p.update(sy=sy, start_key=start_key, end_key=end_key, prev_key=f'{sy}03', start_my=start_key[4:] + '/' + start_key[:4])
    return p


def _libs_active(start_my):
    return db.fetch_all("SELECT id, name, lib_type FROM libraries WHERE (inactive_from IS NULL OR inactive_from > to_date('01/' || %s, 'DD/MM/YYYY')) ORDER BY id", (start_my,))


def _pending_count(start_key, end_key):
    return db.fetch_one("SELECT count(*) c FROM library_receipts WHERE (substr(month_year,4,4) || substr(month_year,1,2)) BETWEEN %s AND %s AND status='pending'", (start_key, end_key))['c']


def build_stats_report(mode, month, fy, include_pending=False, scope=None):
    """R4 — உறுப்பினர் · புரவலர் · பெரும்புரவலர் · கொடையாளர்."""
    P = _stats_period(mode, month, fy, scope, 'புள்ளி விவர')
    start_key, end_key, prev_key, sy = P['start_key'], P['end_key'], P['prev_key'], P['sy']
    title = 'நூலகப் புள்ளி விவர அறிக்கை — ' + P['title_part']
    CATS = ('உறுப்பினர்', 'புரவலர்', 'பெரும்புரவலர்', 'கொடையாளர்')

    base = {r['library_name']: r for r in db.fetch_all('SELECT * FROM library_stats_base')}
    libs = _libs_active(P['start_my'])
    status_sql = '' if include_pending else " AND status='approved'"
    recs = db.fetch_all("SELECT library_name, month_year, security_amount, patron, chief_patron, donor FROM library_receipts "
                        "WHERE (substr(month_year,4,4) || substr(month_year,1,2)) BETWEEN %s AND %s" + status_sql, (STATS_FIRST_KEY, end_key))
    pending_skipped = 0 if include_pending else _pending_count(start_key, end_key)

    per = {}
    no_fee, fractional = [], []
    for l in libs:
        b = base.get(l['name'])
        per[l['name']] = {'fee': Decimal(str(b['fee'])) if b else Decimal(0),
                          'base': [Decimal(b['members']), Decimal(b['patrons']), Decimal(b['chief_patrons']), Decimal(b['donors'])] if b else [Decimal(0)] * 4,
                          'add': [Decimal(0)] * 4,      # அறிக்கைக் காலத்தில் மட்டும் சேர்ந்தவை (மாதாந்திர)
                          'prev': [Decimal(0)] * 4,     # முந்தைய ஆண்டு இறுதி (மார்ச்) வரை சேர்ந்தவை
                          'cum': [Decimal(0)] * 4,      # அறிக்கைக் காலம் முடிய சேர்ந்தவை
                          'has_base': bool(b)}
    for r in recs:
        p = per.get(r['library_name'])
        if p is None: continue
        sec = Decimal(str(r['security_amount'] or 0))
        if sec > 0 and p['fee'] <= 0:
            if r['library_name'] not in no_fee: no_fee.append(r['library_name'])
        vals = [sec / p['fee'] if p['fee'] > 0 else Decimal(0)] + [Decimal(str(r[c] or 0)) / rate for c, rate in zip(('patron', 'chief_patron', 'donor'), STATS_PATRON_RATES)]
        vals = [v.quantize(Decimal('0.01')) for v in vals]
        k = _month_key(r['month_year'])
        for i in range(4):
            p['cum'][i] += vals[i]
            if k <= prev_key: p['prev'][i] += vals[i]
            if k >= start_key: p['add'][i] += vals[i]
        if any(v != v.to_integral_value() for v in vals) and r['library_name'] not in fractional: fractional.append(r['library_name'])
    for p in per.values():
        p['prev_total'] = [a + b for a, b in zip(p['base'], p['prev'])]
        p['cur_total'] = [a + b for a, b in zip(p['base'], p['cum'])]

    dropped = []
    if mode == 'month':
        # மாதாந்திரம்: சேர்க்கை மட்டும். புரவலர் / பெரும்புரவலர் / கொடையாளர் — அறிக்கை முழுவதும் மொத்தம் 0 எனில் அந்த நெடுவரிசை தவிர்க்கப்படும் (உறுப்பினர் எப்போதும் இருக்கும்)
        grand = [sum((per[l['name']]['add'][i] for l in libs), Decimal(0)) for i in range(4)]
        keep = [i for i in range(4) if i == 0 or grand[i] != 0]
        dropped = [CATS[i] for i in range(4) if i not in keep]
        cols = [{'title': 'வ.எண்.', 'type': 'no'}, {'title': 'நூலகத்தின் பெயர்', 'type': 'text'}] + \
               [{'title': CATS[i], 'type': 'int', 'group': 'சேர்க்கை'} for i in keep]
        def row_vals(p): return [_num(p['add'][i]) for i in keep]
        def sum_of(names): return [_num(sum((per[n]['add'][i] for n in names), Decimal(0))) for i in range(4) if i in keep]
    else:
        # நிதியாண்டு: ஒவ்வொரு வகைக்கும் (முந்தைய நிதி ஆண்டு | நடப்பு நிதி ஆண்டு | மொத்தம்)
        #   முந்தைய = முந்தைய ஆண்டு இறுதி வரையிலான மொத்தம்; நடப்பு = நடப்பு நிதி ஆண்டில் (அல்லது தேர்ந்த மாதம் வரை) சேர்ந்தவை மட்டும்; மொத்தம் = முந்தைய + நடப்பு
        prev_sub = _fy_label(sy - 1)
        cols = [{'title': 'வ.எண்.', 'type': 'no'}, {'title': 'நூலகத்தின் பெயர்', 'type': 'text'}]
        for n in CATS:
            cols.append({'title': 'முந்தைய நிதி ஆண்டு', 'sub': prev_sub, 'type': 'int', 'group': n})
            cols.append({'title': 'நடப்பு நிதி ஆண்டு', 'sub': P['cur_sub'], 'type': 'int', 'group': n})
            cols.append({'title': 'மொத்தம்', 'type': 'int', 'group': n})
        def row_vals(p):
            o = []
            for i in range(4): o += [_num(p['prev_total'][i]), _num(p['add'][i]), _num(p['cur_total'][i])]
            return o
        def sum_of(names):
            o = []
            for i in range(4):
                o += [_num(sum((per[n][k][i] for n in names), Decimal(0))) for k in ('prev_total', 'add', 'cur_total')]
            return o

    order = [t for _, t in LIB_GROUPS] + ['பிற நூலகங்கள்']
    buckets = {t: [] for t in order}
    for l in libs: buckets[_group_of(l['lib_type'])].append(l['name'])
    out, n = [], 0
    for t in order:
        if not buckets[t]: continue
        out.append({'section': t})
        for name in buckets[t]:
            n += 1
            out.append([n, name] + row_vals(per[name]))
        out.append({'subtotal': ['', f'{t} — மொத்தம் ({len(buckets[t])})'] + sum_of(buckets[t])})
    out.append({'section': 'வகை வாரியான நூலகங்களின் சுருக்கம் (Abstract)'})
    for t in order:
        if buckets[t]: out.append(['', f'{t} ({len(buckets[t])})'] + sum_of(buckets[t]))
    totals = ['', f'பெரு மொத்தம் ({len(libs)} நூலகங்கள்)'] + sum_of([l['name'] for l in libs])

    if mode == 'month':
        notes = ['சேர்க்கை = இந்த மாதத்தில் மட்டும் சேர்ந்த உறுப்பினர் / புரவலர் / பெரும்புரவலர் / கொடையாளர் எண்ணிக்கை.']
        if dropped: notes.append('இம்மாதம் சேர்க்கை இல்லாததால் தவிர்க்கப்பட்ட நெடுவரிசைகள்: ' + ', '.join(dropped) + '.')
    else:
        notes = ['முந்தைய நிதி ஆண்டு = முந்தைய ஆண்டு இறுதி (மார்ச் ' + str(sy) + ') வரையிலான மொத்த எண்ணிக்கை (மார்ச் 2024 அடிப்படை + அதன்பின் சேர்ந்தவை); '
                 'நடப்பு நிதி ஆண்டு = ' + ('இந்த நிதி ஆண்டில்' if P['scope'] == 'full' else 'ஏப்ரல் ' + str(sy) + ' முதல் ' + _ta_month(month) + ' வரை') + ' சேர்ந்தவை மட்டும்; '
                 'மொத்தம் = முந்தைய நிதி ஆண்டு + நடப்பு நிதி ஆண்டு.']
    notes.append('கணக்கீடு: உறுப்பினர் = காப்புத் தொகை ÷ நூலகத்தின் கட்டணம்; புரவலர் = தொகை ÷ 1000; பெரும்புரவலர் = தொகை ÷ 5000; கொடையாளர் = தொகை ÷ 10000.')
    if pending_skipped: notes.append(f'ஒப்புதல் நிலுவையில் உள்ள {pending_skipped} பதிவுகள் சேர்க்கப்படவில்லை.')
    nb = [l['name'] for l in libs if not per[l['name']]['has_base']]
    if nb: notes.append(f'மார்ச் 2024 அடிப்படைத் தரவு ஏற்றப்படாத நூலகங்கள் {len(nb)} — அவற்றுக்கு அடிப்படை எண்ணிக்கை 0 எனக் கொள்ளப்பட்டது (தரவு ஏற்றம் › 2E).')
    if no_fee: notes.append('காப்புத் தொகை கட்டணம் இல்லாததால் உறுப்பினர் எண்ணிக்கை கணக்கிட முடியாத நூலகங்கள் (' + str(len(no_fee)) + '): ' + ', '.join(no_fee[:10]) + (' …' if len(no_fee) > 10 else ''))
    if fractional: notes.append('வகுத்தபின் முழு எண்ணாக வராத தொகை உள்ள நூலகங்கள் (' + str(len(fractional)) + '): ' + ', '.join(fractional[:10]) + (' …' if len(fractional) > 10 else '') + ' — தசமத்துடன் காட்டப்படுகிறது.')
    return {'success': True, 'kind': 'stats', 'mode': mode, 'month': P['month_ref'], 'title': title, 'subtitle': P['subtitle'],
            'file': 'Library_Stats_' + P['fname_part'], 'columns': cols, 'rows': out, 'totals': totals, 'notes': notes, 'includePending': bool(include_pending)}


def build_usage_report(mode, month, fy, include_pending=False, scope=None):
    """R5 — வாசகர் வருகை · நூலிரவல் · நூல்கள் பயன்பாடு (நூலகம் வாரியாக, வகை வாரியாக, இறுதியில் Abstract)."""
    P = _stats_period(mode, month, fy, scope, 'வாசகர் · நூலிரவல் · பயன்பாடு')
    start_key, end_key = P['start_key'], P['end_key']
    title = 'வாசகர் / நூலிரவல் / பயன்பாடு அறிக்கை — ' + P['title_part']
    libs = _libs_active(P['start_my'])
    status_sql = '' if include_pending else " AND status='approved'"
    recs = db.fetch_all("SELECT library_name, COALESCE(sum(visitors),0) v, COALESCE(sum(issues),0) i, COALESCE(sum(book_usage),0) u, count(*) n FROM library_receipts "
                        "WHERE (substr(month_year,4,4) || substr(month_year,1,2)) BETWEEN %s AND %s" + status_sql + " GROUP BY library_name", (start_key, end_key))
    got = {r['library_name']: [int(r['v']), int(r['i']), int(r['u'])] for r in recs}
    pending_skipped = 0 if include_pending else _pending_count(start_key, end_key)

    cols = [{'title': 'வ.எண்.', 'type': 'no'}, {'title': 'நூலகத்தின் பெயர்', 'type': 'text'},
            {'title': 'வாசகர் வருகை', 'type': 'int'}, {'title': 'நூலிரவல்', 'type': 'int'}, {'title': 'நூல்கள் பயன்பாடு', 'type': 'int'}]
    def row_vals(name): return got.get(name, [0, 0, 0])
    def sum_of(names):
        return [sum(got.get(n, [0, 0, 0])[i] for n in names) for i in range(3)]
    order = [t for _, t in LIB_GROUPS] + ['பிற நூலகங்கள்']
    buckets = {t: [] for t in order}
    for l in libs: buckets[_group_of(l['lib_type'])].append(l['name'])
    out, n = [], 0
    for t in order:
        if not buckets[t]: continue
        out.append({'section': t})
        for name in buckets[t]:
            n += 1
            out.append([n, name] + row_vals(name))
        out.append({'subtotal': ['', f'{t} — மொத்தம் ({len(buckets[t])})'] + sum_of(buckets[t])})
    out.append({'section': 'வகை வாரியான நூலகங்களின் சுருக்கம் (Abstract)'})
    for t in order:
        if buckets[t]: out.append(['', f'{t} ({len(buckets[t])})'] + sum_of(buckets[t]))
    names = [l['name'] for l in libs]
    totals = ['', f'பெரு மொத்தம் ({len(libs)} நூலகங்கள்)'] + sum_of(names)
    reported = sum(1 for nm in names if nm in got)
    notes = [('இந்த மாதம்' if mode == 'month' else 'அறிக்கைக் காலத்தில்') + f' புள்ளி விவரம் பதிவு செய்த நூலகங்கள் {reported} / {len(libs)}.']
    if mode == 'fy': notes.append('நிதியாண்டு அறிக்கையில் ஒவ்வொரு நூலகத்துக்கும் ஏப்ரல் முதல் அறிக்கைக் காலம் முடிய உள்ள மாதங்களின் கூட்டுத்தொகை காட்டப்படுகிறது.')
    if pending_skipped: notes.append(f'ஒப்புதல் நிலுவையில் உள்ள {pending_skipped} பதிவுகள் சேர்க்கப்படவில்லை.')
    return {'success': True, 'kind': 'usage', 'mode': mode, 'month': P['month_ref'], 'title': title, 'subtitle': P['subtitle'],
            'file': 'Library_Usage_' + P['fname_part'], 'columns': cols, 'rows': out, 'totals': totals, 'notes': notes, 'includePending': bool(include_pending)}


def _build_any(kind):
    month, pend = _report_args()
    a = request.args
    if kind == 'stats':
        return build_stats_report(a.get('mode') or 'month', month, a.get('fy'), pend, a.get('scope'))
    if kind == 'usage':
        return build_usage_report(a.get('mode') or 'month', month, a.get('fy'), pend, a.get('scope'))
    return build_receipt_report(kind, month, pend)


def _report_args():
    return (request.args.get('month') or '').strip(), request.args.get('pending') in ('1', 'true', 'on')


@receipts.get('/receipts/admin/report/<kind>.json')
def receipt_report_json(kind):
    if not _admin(): return jsonify(success=False, message='Admin அனுமதி தேவை'), 403
    try: return jsonify(_build_any(kind))
    except ValueError as e: return jsonify(success=False, message=str(e)), 400
    except Exception:
        current_app.logger.exception('Receipt report')
        return jsonify(success=False, message='அறிக்கையை உருவாக்க முடியவில்லை.'), 500


@receipts.get('/receipts/admin/report/<kind>.xlsx')
def receipt_report_xlsx(kind):
    if not _admin(): return jsonify(success=False, message='Admin அனுமதி தேவை'), 403
    try: d = _build_any(kind)
    except ValueError as e: return jsonify(success=False, message=str(e)), 400
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter
    from urllib.parse import quote
    from flask import Response
    wb = Workbook(); ws = wb.active; ws.title = 'வரவின அறிக்கை'[:31]
    thin = Side(style='thin', color='000000'); box = Border(left=thin, right=thin, top=thin, bottom=thin)
    fill = PatternFill('solid', fgColor='E8EFF9'); bold = Font(bold=True)
    cols = d['columns']; n = len(cols)
    for r_i, (txt, size) in enumerate([('திண்டுக்கல் மாவட்ட நூலக ஆணைக்குழு', 14), (d['title'], 12), (d.get('subtitle') or ('மாதம்: ' + d['month']), 12)], 1):
        c = ws.cell(row=r_i, column=1, value=txt); c.font = Font(bold=True, size=size); c.alignment = Alignment(horizontal='center')
        ws.merge_cells(start_row=r_i, start_column=1, end_row=r_i, end_column=n)
    hr = 5
    grouped = any(c.get('group') for c in cols)
    def hdr(cell, text):
        cell.value = text; cell.font = bold; cell.fill = fill; cell.border = box
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    if not grouped:
        for c_i, col in enumerate(cols, 1): hdr(ws.cell(row=hr, column=c_i), col['title'])
        ws.row_dimensions[hr].height = 48
        first = hr + 1
    else:
        # இரு வரித் தலைப்பு: குழுத் தலைப்பு (வரி 1) → துணைத் தலைப்பு (வரி 2); குழு இல்லாத நெடுவரிசைகள் இரு வரிகளையும் இணைக்கும்
        i = 0
        while i < n:
            col = cols[i]
            if col.get('group'):
                j = i
                while j + 1 < n and cols[j + 1].get('group') == col['group']: j += 1
                hdr(ws.cell(row=hr, column=i + 1), col['group'])
                for k in range(i + 1, j + 2): ws.cell(row=hr, column=k).border = box; ws.cell(row=hr, column=k).fill = fill
                if j > i: ws.merge_cells(start_row=hr, start_column=i + 1, end_row=hr, end_column=j + 1)
                for k in range(i, j + 1):
                    hdr(ws.cell(row=hr + 1, column=k + 1), cols[k]['title'] + ('\n' + cols[k]['sub'] if cols[k].get('sub') else ''))
                i = j + 1
            else:
                hdr(ws.cell(row=hr, column=i + 1), col['title'])
                ws.cell(row=hr + 1, column=i + 1).border = box; ws.cell(row=hr + 1, column=i + 1).fill = fill
                ws.merge_cells(start_row=hr, start_column=i + 1, end_row=hr + 1, end_column=i + 1)
                i += 1
        ws.row_dimensions[hr].height = 24; ws.row_dimensions[hr + 1].height = 40
        first = hr + 2
    def put(row, vals, is_total=False):
        for c_i, (col, v) in enumerate(zip(cols, vals), 1):
            x = ws.cell(row=row, column=c_i, value=v); x.border = box
            if col['type'] in ('money', 'total'): x.number_format = '#,##0.00'
            if col['type'] == 'multi': x.alignment = Alignment(wrap_text=True, vertical='top')
            if col['type'] in ('no', 'date'): x.alignment = Alignment(horizontal='center', vertical='top')
            if is_total: x.font = bold; x.fill = fill
    r = first
    for vals in d['rows']:
        if isinstance(vals, dict) and 'subtotal' in vals: put(r, vals['subtotal'], True)
        elif isinstance(vals, dict):
            x = ws.cell(row=r, column=1, value=vals['section']); x.font = bold; x.fill = fill
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=n)
            for c_i in range(1, n + 1): ws.cell(row=r, column=c_i).border = box
        else: put(r, vals)
        r += 1
    put(r, d['totals'], True)
    for c_i, col in enumerate(cols, 1):
        w = {'no': 6, 'date': 13, 'multi': 20, 'text': 30, 'money': 11, 'total': 13, 'int': 14, 'rate': 13}[col['type']]
        ws.column_dimensions[get_column_letter(c_i)].width = w
    ws.freeze_panes = ws.cell(row=first, column=1)
    ws.page_setup.orientation = 'portrait' if d.get('kind') in ('stats', 'usage') else 'landscape'; ws.page_setup.paperSize = 9; ws.page_setup.fitToWidth = 1; ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f'{hr}:{first - 1}'
    buf = io.BytesIO(); wb.save(buf)
    fname = (d['file'] + '.xlsx') if d.get('file') else '%s_%s.xlsx' % (REPORT_FILES[kind], d['month'].replace('/', '-'))
    return Response(buf.getvalue(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': 'attachment; filename="report.xlsx"; filename*=UTF-8\'\'' + quote(fname)})

# ------------------------------------------------------------------
#  நூலகர் திரை — "நூலக வரவுத் தொகைக்கு ஒப்புதல்" (ஒப்புகை ரசீது PDF)
#  அனுமதிக்கப்பட்ட சில்லறைச் செலவினம் போலவே: ஒப்புதல் பெற்ற மாதங்கள் பட்டியல் → மாதம் தேர்வு → அந்த மாதத்துக்கு மட்டும் PDF
#  (PDF-ஐ உலாவி உருவாக்கும்; இங்கே தரவு மட்டும் தரப்படும்)
# ------------------------------------------------------------------
RECEIPT_BANK_ACCOUNT = os.environ.get('RECEIPT_BANK_ACCOUNT', '2981000402022914')   # திண்டுக்கல் மாவட்ட நூலக அலுவலர் வங்கி கணக்கு எண்
RECEIPT_ACK_ITEMS = [('காப்புத் தொகை', 'security_amount'), ('ஆண்டுச் சந்தா', 'annual_amount'),
                     ('காலதாமதக் கட்டணம்', 'late_fee'), ('நூல் விலைப் பிடித்தம்', 'book_fine'),
                     ('பழைய நாளிதழ்கள் விற்பனை', 'newspaper_amount'), ('பழைய நூல்கள் விற்பனை', 'old_book_sale'),
                     ('புரவலர்', 'patron'), ('பெரும்புரவலர்', 'chief_patron'), ('கொடையாளர்', 'donor'), ('இதர வரவுகள்', 'other_income')]
_W_ONES = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten', 'Eleven', 'Twelve', 'Thirteen',
           'Fourteen', 'Fifteen', 'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen']
_W_TENS = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']


def _w999(n):
    out = []
    if n >= 100: out.append(_W_ONES[n // 100] + ' Hundred'); n %= 100
    if n >= 20: out.append(_W_TENS[n // 10] + ((' ' + _W_ONES[n % 10]) if n % 10 else ''))
    elif n: out.append(_W_ONES[n])
    return ' '.join(out)


def _rupees_words(amount):
    """576 -> 'Five Hundred Seventy Six Rupees Only' (இந்திய முறை: Crore / Lakh / Thousand)"""
    amt = Decimal(str(amount or 0)).quantize(Decimal('0.01'))
    rupees, paise = int(amt), int((amt - int(amt)) * 100)
    parts = []
    for div, name in ((10000000, 'Crore'), (100000, 'Lakh'), (1000, 'Thousand')):
        q, rupees = divmod(rupees, div)
        if q: parts.append(_w999(q) + ' ' + name)
    if rupees: parts.append(_w999(rupees))
    text = ' '.join(parts) or 'Zero'
    text += ' Rupees'
    if paise: text += ' and ' + _w999(paise) + ' Paise'
    return text + ' Only'


def getApprovedReceiptMonths(libraryName):
    """இந்த நூலகத்தின் ஒப்புதல் பெற்ற வரவு மாதங்கள் (புதியது முதலில்) — பணம் செலுத்தியவை மட்டும்; புள்ளி விவரம் மட்டும் பதிவு செய்த மாதங்கள் வராது."""
    if not _can_use(): return _fail('உள்நுழைவு தேவை')
    rows = db.fetch_all("SELECT month_year FROM library_receipts WHERE library_name=%s AND status='approved' AND mode='income' AND total_amount>0 "
                        "ORDER BY substr(month_year,4,4) DESC, substr(month_year,1,2) DESC", (str(libraryName or '').strip(),))
    if not rows: return _fail('இந்த நூலகத்துக்கு ஒப்புதல் பெற்ற வரவுப் பதிவுகள் எதுவும் இல்லை.')
    return {'success': True, 'months': [r['month_year'] for r in rows]}


def getReceiptAcknowledgement(libraryName, monthYear):
    """ஒப்புகை ரசீதுக்கான தரவு — தொகை உள்ள இனங்கள் மட்டும்."""
    if not _can_use(): return _fail('உள்நுழைவு தேவை')
    name, my = str(libraryName or '').strip(), str(monthYear or '').strip()
    r = db.fetch_one("SELECT r.*, COALESCE(json_agg(json_build_object('upi',t.upi_number,'date',to_char(t.transaction_date,'YYYY-MM-DD')) "
                     "ORDER BY t.transaction_date,t.id) FILTER (WHERE t.id IS NOT NULL),'[]') txs "
                     "FROM library_receipts r LEFT JOIN receipt_bank_transactions t ON t.receipt_id=r.id "
                     "WHERE r.library_name=%s AND r.month_year=%s GROUP BY r.id", (name, my))
    if not r: return _fail('இந்த மாதத்துக்கு வரவுப் பதிவு இல்லை.')
    if r['status'] != 'approved': return _fail('இந்த மாத வரவு இன்னும் ஒப்புதல் பெறவில்லை.')
    total = Decimal(str(r['total_amount'] or 0))
    if r['mode'] != 'income' or total <= 0: return _fail('இந்த மாதம் பணம் செலுத்திய வரவு இல்லை.')
    pairs = _utr_pairs(dict(r))
    items = [{'desc': title, 'amount': _num(r[col])} for title, col in RECEIPT_ACK_ITEMS if Decimal(str(r[col] or 0)) > 0]
    ack = r['approved_at']
    if ack: ack = ack.astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30))).strftime('%d/%m/%Y')
    return {'success': True, 'library': r['library_name'], 'month': r['month_year'], 'ackDate': ack or '',
            'total': _num(total), 'inWords': _rupees_words(total), 'account': RECEIPT_BANK_ACCOUNT,
            'utrs': '/'.join(u for u, _ in pairs), 'dates': '/'.join(_fdate(d) for _, d in pairs if d), 'items': items}


def register_rpc(RPC):
    for f in (validatePaymentWithBank,getReceiptStockInfo,saveOrUpdateReceipt,getApprovedReceiptMonths,getReceiptAcknowledgement): RPC[f.__name__]=f
