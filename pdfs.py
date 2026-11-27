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
