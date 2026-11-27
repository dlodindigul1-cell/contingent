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


def _wrap_lines(pdf, text, width):
    """சொல் எல்லையில் மட்டும் உடைத்தால் தேவைப்படும் வரிகள் (தற்போதைய font-ல்)."""
    sp, lines, cur = pdf.get_string_width(" "), 1, 0.0
    for w in str(text).split():
        ww = pdf.get_string_width(w)
        if cur and cur + sp + ww > width:
            lines += 1
            cur = ww
        else:
            cur += (sp if cur else 0) + ww
    return lines


def _adaptive_table(pdf, cols, nbody, foot_label, foot_span, reserve_h=0, max_pt=11.0, min_pt=5.5, num_align="CENTER"):
    """நெடுவரிசை எண்ணிக்கைக்கு ஏற்ப தானாக அளவு பொருத்தும் அட்டவணை.
    cols: [{"head": str, "cells": [str]*nbody, "foot": str|None (முதல் foot_span நெடுவரிசைகளுக்கு None)}]
    - தலைப்புகள் குறைவு -> எழுத்து பெரிது (max_pt வரை); அதிகம் -> தேவையான அளவுக்கு மட்டும் சிறிது.
    - தலைப்புச் சொல்லும் எண்ணும் உடையாத குறைந்த அகலம்; மீதி இடம் மிதமாகப் பகிரப்பட்டு அட்டவணை நடுவில் அமையும்.
    - பக்கத்தின் உயரத்துக்குள் (reserve_h தவிர) ஒரே பக்கத்தில் அடங்கும் மிகப்பெரிய எழுத்தளவு தேர்வாகும்."""
    avail_w = pdf.w - pdf.l_margin - pdf.r_margin
    avail_h = pdf.h - pdf.get_y() - 10 - reserve_h          # கீழ் ஓரம் 10 மிமீ
    if foot_span == 1:                                     # 'மொத்தம்' ஒற்றை நெடுவரிசையில் வருவதால் அதன் அகலமும் கணக்கில்
        cols[0]["foot"] = foot_label
    size = max_pt
    chosen = None
    while size >= min_pt - 1e-9:
        hs = max(round(size * 0.8, 1), 5.2)
        pad = 0.7 + (size - 6) * 0.1
        lh = size * 0.3528 * 1.3
        needs = []
        for c in cols:
            pdf.set_font("Tamil", "B", hs)
            wh = max(pdf.get_string_width(w) for w in (str(c["head"]).split() or [" "]))
            pdf.set_font("Tamil", "B", size)
            texts = list(c["cells"]) + ([c["foot"]] if c.get("foot") is not None else [])
            wb = max([pdf.get_string_width(x) for x in texts] + [0])
            needs.append(max(wh, wb) * 1.08 + 2 * pad + 1.0)
        if sum(needs) <= avail_w:
            extra = avail_w - sum(needs)
            adds = [min(extra / len(cols), 0.3 * n) for n in needs]
            widths = [n + a for n, a in zip(needs, adds)]
            pdf.set_font("Tamil", "B", hs)
            hl = max(_wrap_lines(pdf, c["head"], w - 2 * pad - 0.8) for c, w in zip(cols, widths))
            height = hl * lh + 2 * pad + (nbody + 1) * (lh + 2 * pad)
            chosen = (size, hs, pad, lh, widths)
            if height <= avail_h:
                break
        size -= 0.5
    if chosen is None or size < min_pt - 1e-9:                 # எதுவும் பொருந்தவில்லை -> குறைந்த அளவு
        size = min_pt
        hs, pad, lh = round(size * 0.9, 1), 0.7, size * 0.3528 * 1.3
        w0 = avail_w / len(cols)
        chosen = (size, hs, pad, lh, [w0] * len(cols))
    size, hs, pad, lh, widths = chosen
    head = FontFace(emphasis="BOLD", size_pt=hs, fill_color=(223, 241, 253))
    bold = FontFace(emphasis="BOLD", size_pt=size, fill_color=(244, 244, 244))
    pdf.set_font("Tamil", "", size)
    aligns = tuple(["CENTER"] * foot_span + [num_align] * (len(cols) - foot_span))
    with pdf.table(col_widths=tuple(widths), width=sum(widths), align="CENTER", text_align=aligns,
                   headings_style=head, line_height=lh, padding=pad, borders_layout="ALL") as t:
        h = t.row()
        for c in cols:
            h.cell(str(c["head"]), align="C")
        for r in range(nbody):
            row = t.row()
            for c in cols:
                row.cell(str(c["cells"][r]))
        row = t.row()
        row.cell(foot_label, style=bold, colspan=foot_span, align="C")
        for c in cols[foot_span:]:
            row.cell(str(c["foot"]), style=bold)
    return size


def _year_table(pdf, d, with_sno, reserve_h=0, max_pt=11.0):
    """மாதம் × இனங்கள் அட்டவணை (வரவுச் சுருக்கம் / தணிக்கைப் படிவம்) — தானாக அளவு பொருந்தும்."""
    rows, nb = d["rows"], len(d["rows"])
    cols = []
    if with_sno:
        cols.append({"head": "வ.எண்", "cells": [str(i) for i in range(1, nb + 1)], "foot": None})
    cols.append({"head": "மாதம்", "cells": [str(r["label"]) for r in rows], "foot": None})
    for i, c in enumerate(d["cols"]):
        cols.append({"head": str(c["title"]), "cells": [_money(r["vals"][i]) for r in rows],
                     "foot": _money(d["totals"]["vals"][i])})
    cols.append({"head": "மொத்தம்", "cells": [_money(r["total"]) for r in rows], "foot": _money(d["totals"]["total"])})
    return _adaptive_table(pdf, cols, nb, "மொத்தம்", 2 if with_sno else 1, reserve_h, max_pt)


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
    _year_table(pdf, d, True)
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
    _year_table(pdf, d, False, reserve_h=92, max_pt=10)
    pdf.ln(6)
    W = pdf.w - pdf.l_margin - pdf.r_margin
    gap = 8
    cw = (W - gap) / 2
    y0 = pdf.get_y()
    bold = FontFace(emphasis="BOLD")

    def kv_table(x, title, pairs):
        pdf.set_xy(x, y0)
        pdf.set_font("Tamil", "B", 12)
        pdf.cell(cw, 8, title, align="C", new_x="LEFT", new_y="NEXT")
        pdf.set_x(x)
        pdf.set_font("Tamil", "", 11)
        old_l = pdf.l_margin
        pdf.set_left_margin(x)
        pdf.set_x(x)
        with pdf.table(col_widths=(cw - 30, 30), text_align=("LEFT", "CENTER"), line_height=5.2,
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
    pdf.set_font("Tamil", "B", 11)
    pdf.cell(0, 6, "VERIFIED WITH OFFICE ACCOUNTS", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, "AND FOUND CORRECT", align="R", new_x="LMARGIN", new_y="NEXT")
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
    rows, nb = d["rows"], len(d["rows"])
    heads = [str(c["title"]) for c in d["cols"]] + ["மொத்த செலவினம் (நாளிதழ் / வாடகை தவிர்த்து)", "மொத்த செலவினம்"]
    sums = list(t_["vals"]) + [t_["net"], t_["total"]]
    cols = [{"head": "வ.எண்", "cells": [str(i) for i in range(1, nb + 1)], "foot": None},
            {"head": "மாதம்", "cells": [str(r["label"]) for r in rows], "foot": None}]
    for i, h in enumerate(heads):
        if i < len(d["cols"]):
            cells = [_blank_if_zero(r["vals"][i]) for r in rows]
        elif i == len(d["cols"]):
            cells = [_money(r["net"]) for r in rows]
        else:
            cells = [_money(r["total"]) for r in rows]
        cols.append({"head": h, "cells": cells, "foot": _money(sums[i])})
    # தலைப்புகள் குறைவு -> எழுத்து பெரிது; அதிகம் -> தேவைக்கேற்பச் சிறிது (landscape ஒரே பக்கத்தில்)
    _adaptive_table(pdf, cols, nb, str(t_["label"]), 2, max_pt=11, num_align="RIGHT")
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
