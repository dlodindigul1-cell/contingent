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
    libs=db.fetch_all("SELECT name,lib_type FROM libraries ORDER BY id")
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
    rows=db.fetch_all('SELECT name,lib_type FROM libraries ORDER BY id')
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
            c.execute('SELECT id FROM libraries WHERE name=%s',(lib,))
            if not c.fetchone(): raise ValueError('நூலகம் கிடைக்கவில்லை.')
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

@receipts.get('/receipts/admin/pending')
def pending():
    if not _admin(): return jsonify(success=False),403
    rows=db.fetch_all("SELECT r.*, COALESCE(json_agg(json_build_object('upi',t.upi_number,'amount',t.amount,'date',t.transaction_date)) FILTER(WHERE t.id IS NOT NULL),'[]') transactions FROM library_receipts r LEFT JOIN receipt_bank_transactions t ON t.receipt_id=r.id GROUP BY r.id ORDER BY r.created_at DESC")
    return jsonify(rows=[dict(r) for r in rows])

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

def register_rpc(RPC):
    for f in (validatePaymentWithBank,getReceiptStockInfo,saveOrUpdateReceipt): RPC[f.__name__]=f
