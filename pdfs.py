"""சர்வரில் தமிழ் PDF உருவாக்கம் — fpdf2 + uharfbuzz (தமிழ் எழுத்துக் கோர்ப்பு) + Noto Sans Tamil."""
import os

from fpdf import FPDF
from fpdf.fonts import FontFace

_FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "fonts")


def _new_pdf():
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(18, 20, 18)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_font("Tamil", "", os.path.join(_FONTS, "NotoSansTamil-Regular.ttf"))
    pdf.add_font("Tamil", "B", os.path.join(_FONTS, "NotoSansTamil-Bold.ttf"))
    pdf.set_text_shaping(True)
    pdf.add_page()
    return pdf


def _money(v):
    v = float(v or 0)
    return ("%.2f" % v).rstrip("0").rstrip(".") if v != int(v) else str(int(v))


def approved_expense_pdf(st):
    """st: getApprovedExpenseStatement() முடிவு -> PDF bytes (A4)."""
    pdf = _new_pdf()
    W = pdf.w - pdf.l_margin - pdf.r_margin
    cell = lambda h, txt, size=12, bold=True, gap=0: (
        pdf.set_font("Tamil", "B" if bold else "", size),
        pdf.cell(0, h, txt, align="C", new_x="LMARGIN", new_y="NEXT"), pdf.ln(gap))
    cell(8, "மாவட்ட நூலக ஆணைக்குழு திண்டுக்கல்", 15)
    cell(8, "சில்லறை செலவின அனுமதி", 13)
    pdf.ln(3)
    cell(7, str(st["library"]), 12)
    cell(7, "FOR THE MONTH OF " + str(st["month"]), 11, gap=4)

    pdf.set_font("Tamil", "", 10.5)
    head = FontFace(emphasis="BOLD", fill_color=(234, 241, 250))
    bold = FontFace(emphasis="BOLD")
    n = len(st["items"])
    with pdf.table(col_widths=(16, W - 16 - 38, 38), text_align=("CENTER", "LEFT", "RIGHT"),
                   headings_style=head, line_height=6.5, padding=1.8, borders_layout="ALL") as t:
        h = t.row()
        h.cell("S.No", align="C"); h.cell("Description", align="C"); h.cell("Amount", align="C")
        for i, it in enumerate(st["items"], 1):
            r = t.row(); r.cell(str(i)); r.cell(str(it["desc"])); r.cell(_money(it["amount"]))
        r = t.row(); r.cell(str(n + 1), style=bold); r.cell("மொத்த செலவினம் (நாளிதழ்/வாடகை தவிர்த்து)", style=bold); r.cell(_money(st["totalExcl"]), style=bold)
        r = t.row(); r.cell(str(n + 2), style=bold); r.cell("மொத்த செலவினம்", style=bold); r.cell(_money(st["total"]), style=bold)
    pdf.ln(6)
    pdf.set_font("Tamil", "B", 12)
    pdf.cell(0, 8, "Total Amount: Rs. " + _money(st["totalExcl"]), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Tamil", "", 10.5)
    pdf.multi_cell(0, 6, "In Words: " + str(st["inWords"]), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(18)
    pdf.set_font("Tamil", "B", 12)
    pdf.cell(0, 6, "District Library Officer", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, "Dindigul", align="R", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


# ------------------------------------------------------------------
#  நூலகர் மெனு அறிக்கைகள் (2–6) — சர்வரில் PDF; மின்னஞ்சல் / நேரடிப் பதிவிறக்கம் இரண்டுக்கும் இதே PDF
# ------------------------------------------------------------------
def _c(pdf, h, txt, size=12, bold=True, gap=0, align="C"):
    pdf.set_font("Tamil", "B" if bold else "", size)
    pdf.cell(0, h, txt, align=align, new_x="LMARGIN", new_y="NEXT")
    if gap:
        pdf.ln(gap)


def _blank_if_zero(v):
    return "" if float(v or 0) == 0 else _money(v)


def receipt_ack_pdf(st):
    """st: receipts_module.getReceiptAcknowledgement() முடிவு -> ஒப்புகை ரசீது."""
    pdf = _new_pdf()
    W = pdf.w - pdf.l_margin - pdf.r_margin
    _c(pdf, 9, "மாவட்ட நூலக அலுவலகம், திண்டுக்கல்", 15)
    _c(pdf, 8, "நூலக வரவுகள் - ஒப்புகை ரசீது", 13)
    _c(pdf, 7, "மாதம்: " + str(st["month"]), 11, gap=5)
    pdf.set_font("Tamil", "", 10.5)
    pdf.multi_cell(0, 6.5, "தொகை ரூ. %s (%s) மட்டும் %s நூலகரிடம் இருந்து இணைய வழி பணப் பரிவர்த்தனை மூலம் பெறப்பட்டு "
                   "திண்டுக்கல் மாவட்ட நூலக அலுவலர் வங்கி கணக்கு எண் %s-ல் வரவாகியுள்ளது என சான்றளிக்கப்படுகிறது. "
                   "மேற்படி தொகை தங்களால் தெரிவிக்கப்பட்டவாறு கீழ்கண்ட இனங்களுக்காக வரவாக்கம் செய்யப்பட்டுள்ளது:"
                   % (_money(st["total"]), st["inWords"], st["library"], st["account"]),
                   new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    lines = [("ஒப்புகை நாள்", st["ackDate"]), ("நூலகம் பெயர்", st["library"]),
             ("பரிவர்த்தனை எண்", st["utrs"]), ("செலுத்தப் பட்ட நாள்", st["dates"])]
    lines += [(it["desc"], _money(it["amount"])) for it in st["items"]]
    head = FontFace(emphasis="BOLD", fill_color=(234, 241, 250))
    bold = FontFace(emphasis="BOLD")
    pdf.set_font("Tamil", "", 10.5)
    with pdf.table(col_widths=(16, (W - 16) / 2, (W - 16) / 2), text_align=("CENTER", "LEFT", "CENTER"),
                   headings_style=head, line_height=6.5, padding=1.8, borders_layout="ALL") as t:
        h = t.row()
        h.cell("வ.எண்.", align="C"); h.cell("விவரம்", align="C"); h.cell("தொகை", align="C")
        for i, (k, v) in enumerate(lines, 1):
            r = t.row(); r.cell(str(i)); r.cell(str(k), style=bold); r.cell(str(v))
        r = t.row(); r.cell(str(len(lines) + 1), style=bold); r.cell("மொத்தம்", style=bold); r.cell(_money(st["total"]), style=bold)
    pdf.ln(16)
    pdf.set_font("Tamil", "B", 12)
    pdf.cell(0, 6, "District Library Officer,", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, "Dindigul", align="R", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def _year_table(pdf, d, with_sno, size_head, size_body, first_w=None, line_h=5.2, pad=0.9, weighted=False):
    """மாதம் × இனங்கள் அட்டவணை (வரவுச் சுருக்கம் / தணிக்கைப் படிவம்)."""
    W = pdf.w - pdf.l_margin - pdf.r_margin
    n = len(d["cols"]) + 1
    first = first_w or ([9, 20] if with_sno else [24])
    if weighted:
        # எழுத்துருவின் உண்மை அகலத்தை அளந்து: தலைப்பின் நீளமான சொல் / மிக நீளமான எண் உடையாமல் பொருந்தும் குறைந்த அகலம்;
        # மீதமுள்ள இடம் சமமாகப் பகிரப்படும்.
        heads = [str(c["title"]) for c in d["cols"]] + ["மொத்தம்"]
        cols_vals = [[r["vals"][i] for r in d["rows"]] + [d["totals"]["vals"][i]] for i in range(len(d["cols"]))]
        cols_vals.append([r["total"] for r in d["rows"]] + [d["totals"]["total"]])
        need = []
        for h, vs in zip(heads, cols_vals):
            pdf.set_font("Tamil", "B", size_head)
            wh = max(pdf.get_string_width(w) for w in h.split())
            pdf.set_font("Tamil", "B", size_body)
            wn = max(pdf.get_string_width(_money(v)) for v in vs)
            need.append(max(wh, wn) + 2 * pad + 1.6)
        avail = W - sum(first)
        if sum(need) > avail:                       # இடம் போதாது எனில் விகிதப்படி சுருக்கு
            need = [x * avail / sum(need) for x in need]
        else:
            extra = (avail - sum(need)) / len(need)
            need = [x + extra for x in need]
        widths = tuple(first + need)
    else:
        rest = (W - sum(first)) / n
        widths = tuple(first + [rest] * n)
    head = FontFace(emphasis="BOLD", size_pt=size_head, fill_color=(223, 241, 253))
    bold = FontFace(emphasis="BOLD", size_pt=size_body, fill_color=(244, 244, 244))
    pdf.set_font("Tamil", "", size_body)
    with pdf.table(col_widths=widths, text_align=tuple(["CENTER"] * len(widths)), headings_style=head,
                   line_height=line_h, padding=pad, borders_layout="ALL") as t:
        h = t.row()
        if with_sno:
            h.cell("வ.எண்")
        h.cell("மாதம்")
        for c in d["cols"]:
            h.cell(str(c["title"]))
        h.cell("மொத்தம்")
        for i, r in enumerate(d["rows"], 1):
            row = t.row()
            if with_sno:
                row.cell(str(i))
            row.cell(str(r["label"]))
            for v in r["vals"]:
                row.cell(_money(v))
            row.cell(_money(r["total"]))
        row = t.row()
        if with_sno:
            row.cell("மொத்தம்", style=bold, colspan=2)
        else:
            row.cell("மொத்தம்", style=bold)
        for v in d["totals"]["vals"]:
            row.cell(_money(v), style=bold)
        row.cell(_money(d["totals"]["total"]), style=bold)


def _year_pdf_new(d, orientation=None, margin=12):
    """orientation: None = நெடுவரிசை >7 எனில் landscape; 'P' / 'L' = கட்டாயம்."""
    pdf = FPDF(orientation=orientation or ("L" if len(d["cols"]) > 7 else "P"), unit="mm", format="A4")
    pdf.set_margins(margin, 12, margin)
    pdf.set_auto_page_break(auto=True, margin=10)
    pdf.add_font("Tamil", "", os.path.join(_FONTS, "NotoSansTamil-Regular.ttf"))
    pdf.add_font("Tamil", "B", os.path.join(_FONTS, "NotoSansTamil-Bold.ttf"))
    pdf.set_text_shaping(True)
    pdf.add_page()
    return pdf


def yearly_receipt_pdf(d):
    """getLibraryReceiptYearly() முடிவு -> நூலக வரவு சுருக்கம்."""
    pdf = _year_pdf_new(d)
    _c(pdf, 8, "திண்டுக்கல் மாவட்ட நூலக ஆணைக்குழு", 15)
    _c(pdf, 7, str(d["library"]), 12, gap=1)
    _c(pdf, 7, "நூலக வரவு சுருக்கம் " + str(d["fyLabel"]), 12, gap=4)
    _year_table(pdf, d, True, 7, 8)
    return bytes(pdf.output())


def audit_receipt_pdf(d, f):
    """getLibraryReceiptYearly(audit=True) + நூலகர் உள்ளிட்ட f={ob,exp,bo,ba,bw} -> தணிக்கைப் படிவம்."""
    pdf = _year_pdf_new(d, "P", 8)          # எப்போதும் A4 portrait (நெடுவரிசை எத்தனை இருந்தாலும்)
    total = f["ob"] + d["officeReceipts"] + d["totals"]["total"]
    closing = total - f["exp"]
    book_end = f["bo"] + f["ba"] - f["bw"]
    _c(pdf, 8, "திண்டுக்கல் மாவட்ட நூலக ஆணைக்குழு", 15)
    _c(pdf, 8, "தணிக்கை படிவம்", 13)
    _c(pdf, 7, "நூலக வரவினங்கள் - " + str(d["fyLabel"]), 11.5)
    _c(pdf, 7, "நூலகம் பெயர் : " + str(d["library"]), 11.5, gap=3)
    _year_table(pdf, d, False, 5.6, 7, first_w=[19], line_h=4.6, pad=0.45, weighted=True)
    pdf.ln(6)
    W = pdf.w - pdf.l_margin - pdf.r_margin
    gap = 8
    cw = (W - gap) / 2
    y0 = pdf.get_y()
    bold = FontFace(emphasis="BOLD")

    def kv_table(x, title, pairs):
        pdf.set_xy(x, y0)
        pdf.set_font("Tamil", "B", 10.5)
        pdf.cell(cw, 7, title, align="C", new_x="LEFT", new_y="NEXT")
        pdf.set_x(x)
        pdf.set_font("Tamil", "", 9.5)
        old_l = pdf.l_margin
        pdf.set_left_margin(x)
        pdf.set_x(x)
        with pdf.table(col_widths=(cw - 26, 26), text_align=("LEFT", "CENTER"), line_height=5.8,
                       padding=1.4, borders_layout="ALL", first_row_as_headings=False, width=cw,
                       align="LEFT") as t:
            for k, v in pairs:
                r = t.row(); r.cell(str(k)); r.cell(str(v))
        pdf.set_left_margin(old_l)
        return pdf.get_y()

    fy = str(d["fyLabel"])
    y1 = kv_table(pdf.l_margin, "வரவு செலவு சுருக்கம்",
                  [("ஆரம்ப இருப்பு", _money(f["ob"])), ("அலுவலக வரவுகள்", _money(d["officeReceipts"])),
                   ("நூலக வரவுகள் இரசீது வழி", _money(d["totals"]["total"])), ("மொத்தம்", _money(total)),
                   ("செலவு", _money(f["exp"])), ("இறுதி இருப்பு", _money(closing))])
    y2 = kv_table(pdf.l_margin + cw + gap, "நூல் இருப்பு விவரம்",
                  [(fy + " ஆரம்ப இருப்பு", f["bo"]), (fy + " சேர்க்கை", f["ba"]),
                   (fy + " விலகல்", f["bw"]), (fy + " இறுதி இருப்பு", book_end)])
    pdf.set_y(max(y1, y2) + 10)
    pdf.set_font("Tamil", "B", 10)
    pdf.cell(0, 5.5, "VERIFIED WITH OFFICE ACCOUNTS", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5.5, "AND FOUND CORRECT", align="R", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def yearly_expense_pdf(d):
    """getLibraryYearlyExpense() முடிவு -> நூலக ஆண்டு செலவின சுருக்கம் (A4 landscape)."""
    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_margins(8, 10, 8)
    pdf.set_auto_page_break(auto=True, margin=8)
    pdf.add_font("Tamil", "", os.path.join(_FONTS, "NotoSansTamil-Regular.ttf"))
    pdf.add_font("Tamil", "B", os.path.join(_FONTS, "NotoSansTamil-Bold.ttf"))
    pdf.set_text_shaping(True)
    pdf.add_page()
    W = pdf.w - pdf.l_margin - pdf.r_margin
    fy = str(d["fy"])
    try:
        fy_txt = "%s-%d" % (fy[:4], int(fy[:4]) + 1)
    except ValueError:
        fy_txt = fy
    _c(pdf, 8, "திண்டுக்கல் மாவட்ட நூலக ஆணைக்குழு", 15)
    _c(pdf, 7, str(d["library"]), 12)
    _c(pdf, 7, "நூலக ஆண்டு செலவின சுருக்கம் " + fy_txt, 11.5, gap=3)
    t_ = d["totals"]
    heads = [str(c["title"]) for c in d["cols"]] + ["மொத்த செலவினம் (நாளிதழ் / வாடகை தவிர்த்து)", "மொத்த செலவினம்"]
    sums = list(t_["vals"]) + [t_["net"], t_["total"]]
    # நெடுவரிசை அகலம்: தலைப்பின் நீளமான சொல் அல்லது மொத்தத் தொகையின் நீளத்துக்கு ஏற்ப
    weights = []
    for h, s in zip(heads, sums):
        longest = max([len(w) for w in h.split()] + [1])
        weights.append(max(longest * 0.9 + 1.5, len(_money(s)) * 1.3 + 1.5, 6))
    fixed = (8, 12)
    scale = (W - sum(fixed)) / sum(weights)
    widths = tuple(list(fixed) + [w * scale for w in weights])
    head = FontFace(emphasis="BOLD", size_pt=6, fill_color=(223, 241, 253))
    bold = FontFace(emphasis="BOLD", size_pt=6.5, fill_color=(244, 244, 244))
    pdf.set_font("Tamil", "", 6.5)
    with pdf.table(col_widths=widths, text_align=tuple(["CENTER", "CENTER"] + ["RIGHT"] * len(heads)),
                   headings_style=head, line_height=4.4, padding=0.7, borders_layout="ALL") as t:
        h = t.row()
        h.cell("வ.எண்"); h.cell("மாதம்")
        for x in heads:
            h.cell(x, align="C")
        for i, r in enumerate(d["rows"], 1):
            row = t.row()
            row.cell(str(i), align="C"); row.cell(str(r["label"]), align="C")
            for v in r["vals"]:
                row.cell(_blank_if_zero(v))
            row.cell(_money(r["net"])); row.cell(_money(r["total"]))
        row = t.row()
        row.cell(str(t_["label"]), style=bold, colspan=2, align="C")
        for s in sums:
            row.cell(_money(s), style=bold)
    return bytes(pdf.output())


def _words_en(n):
    n = int(round(n))
    if n == 0:
        return "Zero"
    a = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
         "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
    b = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    def h(x):
        t = ""
        if x >= 100:
            t += a[x // 100] + " Hundred"
            x %= 100
            if x:
                t += " and "
        if x >= 20:
            t += b[x // 10]
            if x % 10:
                t += " " + a[x % 10]
        elif x:
            t += a[x]
        return t
    parts = []
    for v, nm in ((10000000, "Crore"), (100000, "Lakh"), (1000, "Thousand")):
        if n >= v:
            parts.append(h(n // v) + " " + nm)
            n %= v
    if n:
        parts.append(h(n))
    return " ".join(parts)


def voucher_pdf(v, logo=None):
    """v: {library, type, monthTxt, mm, yy, items:[{text, amt, subs:[..]}]} -> சில்லறைச் செலவினப் பட்டியல் (வவுச்சர்)."""
    pdf = _new_pdf()
    pdf.set_margins(16, 14, 16)
    pdf.set_xy(16, 14)
    W = pdf.w - pdf.l_margin - pdf.r_margin
    if logo:
        try:
            import io
            pdf.image(io.BytesIO(logo), x=16, y=12, w=20)
        except Exception:
            pass
    pdf.set_xy(16, 14)
    pdf.set_font("Tamil", "B", 14)
    pdf.multi_cell(W, 8, "மாவட்ட நூலக ஆணைக்குழு, திண்டுக்கல்\nசில்லறைச் செலவினப் பட்டியல்", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(max(pdf.get_y(), 36) + 3)
    pdf.set_font("Tamil", "B", 11)
    pdf.cell(W * 0.68, 7, "நூலகம் பெயர்: %s - %s" % (v["library"], v["type"]))
    pdf.cell(W * 0.32, 7, "மாதம்: " + str(v["monthTxt"]), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    total = sum(float(i["amt"]) for i in v["items"])
    bold = FontFace(emphasis="BOLD")
    pdf.set_font("Tamil", "", 10)
    head = FontFace(emphasis="BOLD")
    with pdf.table(col_widths=(16, W - 16 - 34, 34), text_align=("CENTER", "LEFT", "RIGHT"), headings_style=head,
                   line_height=5.8, padding=1.5, borders_layout="ALL") as t:
        h = t.row()
        h.cell("S.No.", align="C"); h.cell("Description of Charges", align="C"); h.cell("Amount Rs. P", align="C")
        count = 0
        for i, it in enumerate(v["items"], 1):
            count += 1
            r = t.row(); r.cell(str(i)); r.cell(str(it["text"]), style=bold); r.cell("%.2f" % float(it["amt"]), style=bold)
            for j, sub in enumerate(it.get("subs") or [], 1):
                count += 1
                r = t.row(); r.cell(""); r.cell("%d.%d %s" % (i, j, sub)); r.cell("")
        for _ in range(count, 10):
            r = t.row(); r.cell(" "); r.cell(" "); r.cell(" ")
        r = t.row(); r.cell("Total", style=bold, colspan=2, align="R"); r.cell("%.2f" % total, style=bold)
    pdf.ln(5)
    words = _words_en(total)
    pdf.set_font("Tamil", "B", 10.5)
    pdf.multi_cell(0, 6, "Rs.%.2f/- (Rupees: %s Rupees)" % (total, words), align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Tamil", "", 8.5)
    pdf.multi_cell(0, 4.6, "Recived Payment, I Certifity that the expenditure charged in this bill could not, with due regarded to the interest of the public service be avoided and that, so for as I could as certain the rates allowed are reasonbale and do not exceed local current rates. I have satisfied myself that the charges entered in this bill have been really paid or will be paid on receipt of the money drawn on this bill. voucher for all sums above Rs. 25 in amount and for all sums paid for postage stamps telegrams and house rents are attached to the bill save thouse noted below which will be obtained as soon as the amounts have been paid. i have as for posible obtained voucher for other sums and i am personally reasonable that they been on defaced that they cannot be used again.", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1.5)
    pdf.multi_cell(0, 4.6, "Certified that the work truned out is satsfactory and is worth the amount paid for. Received the above articles in good condition and entered in the stock register; quantities are correct and qualities are good and suitable for the purpose.", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Tamil", "B", 10.5)
    pdf.cell(0, 6, "நூலகர் ஒப்பம்", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, "நூலக முத்திரையுடன்", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Tamil", "", 9.5)
    pdf.cell(0, 5.5, "Station: " + str(v["library"]), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5.5, "Date: ......%02d.%s" % (int(v["mm"]), v["yy"]), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Tamil", "B", 11.5)
    pdf.multi_cell(0, 6.5, "Passed for Rs.%.2f (%s Rupees)" % (total, words), align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_font("Tamil", "B", 12)
    pdf.cell(0, 7, "District Library Officer", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Tamil", "B", 9)
    fw = W / 4
    y = pdf.get_y()
    for k, lab in enumerate(("Head of Account:", "Classification:", "Accountant:", "Commissioner:")):
        x = pdf.l_margin + k * fw
        pdf.set_xy(x, y)
        pdf.cell(fw - 4, 5, lab)
        pdf.line(x, y + 12, x + fw - 4, y + 12)
    return bytes(pdf.output())
