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
    rows=db.fetch_all("SELECT id,month_year,to_char(transaction_date,'DD/MM/YYYY') transaction_date,upi_number,payer_name,amount,status,library_name FROM receipt_bank_transactions ORDER BY transaction_date, id")
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
    rows=db.fetch_all(_PENDING_SQL.format(where="r.status='pending'",order="r.created_at DESC"))
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
        for t in order:
            if not buckets[t]: continue
            out.append({'section': t})
            for r in buckets[t]:
                n += 1
                out.append([n, r['library_name'], '\n'.join(u for u, _ in _utr_pairs(r)), _fdate(r['payment_date'])]
                           + pick(amounts(r)) + [r['visitors'] or 0, r['issues'] or 0, r['book_usage'] or 0])
        totals = ['', 'மொத்தம்', '', ''] + pick(sums(all_rows)) + [
            sum(r['visitors'] or 0 for r in all_rows), sum(r['issues'] or 0 for r in all_rows), sum(r['book_usage'] or 0 for r in all_rows)]
        total_libs = db.fetch_one("SELECT count(*) c FROM libraries WHERE (inactive_from IS NULL OR inactive_from > to_date('01/' || %s, 'DD/MM/YYYY'))", (month,))['c']
        notes.append(f'மொத்த நூலகங்கள் {total_libs} — இம்மாதம் பதிவு செய்தவை {len(all_rows)}.')
    return {'success': True, 'kind': kind, 'month': month, 'title': REPORT_TITLES[kind], 'columns': cols,
            'rows': out, 'totals': totals, 'notes': notes, 'includePending': bool(include_pending)}


def _report_args():
    return (request.args.get('month') or '').strip(), request.args.get('pending') in ('1', 'true', 'on')


@receipts.get('/receipts/admin/report/<kind>.json')
def receipt_report_json(kind):
    if not _admin(): return jsonify(success=False, message='Admin அனுமதி தேவை'), 403
    month, pend = _report_args()
    try: return jsonify(build_receipt_report(kind, month, pend))
    except ValueError as e: return jsonify(success=False, message=str(e)), 400
    except Exception:
        current_app.logger.exception('Receipt report')
        return jsonify(success=False, message='அறிக்கையை உருவாக்க முடியவில்லை.'), 500


@receipts.get('/receipts/admin/report/<kind>.xlsx')
def receipt_report_xlsx(kind):
    if not _admin(): return jsonify(success=False, message='Admin அனுமதி தேவை'), 403
    month, pend = _report_args()
    try: d = build_receipt_report(kind, month, pend)
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
    for r_i, (txt, size) in enumerate([('திண்டுக்கல் மாவட்ட நூலக ஆணைக்குழு', 14), (d['title'], 12), ('மாதம்: ' + d['month'], 12)], 1):
        c = ws.cell(row=r_i, column=1, value=txt); c.font = Font(bold=True, size=size); c.alignment = Alignment(horizontal='center')
        ws.merge_cells(start_row=r_i, start_column=1, end_row=r_i, end_column=n)
    hr = 5
    for c_i, col in enumerate(cols, 1):
        x = ws.cell(row=hr, column=c_i, value=col['title']); x.font = bold; x.fill = fill; x.border = box
        x.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[hr].height = 48
    def put(row, vals, is_total=False):
        for c_i, (col, v) in enumerate(zip(cols, vals), 1):
            x = ws.cell(row=row, column=c_i, value=v); x.border = box
            if col['type'] in ('money', 'total'): x.number_format = '#,##0.00'
            if col['type'] == 'multi': x.alignment = Alignment(wrap_text=True, vertical='top')
            if col['type'] in ('no', 'date'): x.alignment = Alignment(horizontal='center', vertical='top')
            if is_total: x.font = bold; x.fill = fill
    r = hr + 1
    for vals in d['rows']:
        if isinstance(vals, dict):
            x = ws.cell(row=r, column=1, value=vals['section']); x.font = bold; x.fill = fill
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=n)
            for c_i in range(1, n + 1): ws.cell(row=r, column=c_i).border = box
        else: put(r, vals)
        r += 1
    put(r, d['totals'], True)
    for c_i, col in enumerate(cols, 1):
        w = {'no': 6, 'date': 13, 'multi': 20, 'text': 30, 'money': 11, 'total': 13, 'int': 11}[col['type']]
        ws.column_dimensions[get_column_letter(c_i)].width = w
    ws.freeze_panes = ws.cell(row=hr + 1, column=1)
    ws.page_setup.orientation = 'landscape'; ws.page_setup.fitToWidth = 1; ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f'{hr}:{hr}'
    buf = io.BytesIO(); wb.save(buf)
    fname = '%s_%s.xlsx' % (REPORT_FILES[kind], d['month'].replace('/', '-'))
    return Response(buf.getvalue(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': 'attachment; filename="report.xlsx"; filename*=UTF-8\'\'' + quote(fname)})

def register_rpc(RPC):
    for f in (validatePaymentWithBank,getReceiptStockInfo,saveOrUpdateReceipt): RPC[f.__name__]=f
