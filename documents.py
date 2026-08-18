"""
Generates the real paper-trail documents for a sales order (Proforma, Invoice, etc.)
as actual downloadable PDFs, using reportlab (pure Python, no system dependencies).
"""

import io
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT


def generate_document_pdf(doc_type_label: str, company_name: str, order, line_items) -> bytes:
    """
    doc_type_label: what to print at the top, e.g. "PROFORMA INVOICE (QUOTE)", "SALES INVOICE"
    order: a SalesOrder ORM object
    line_items: list of SalesOrderLineItem ORM objects
    Returns raw PDF bytes.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm)
    styles = getSampleStyleSheet()
    right_style = ParagraphStyle("right", parent=styles["Normal"], alignment=TA_RIGHT)

    elements = []

    elements.append(Paragraph(f"<b>{company_name}</b>", styles["Title"]))
    elements.append(Paragraph(doc_type_label, styles["Heading2"]))
    elements.append(Spacer(1, 10 * mm))

    meta = [
        ["Document #:", f"SO-{order.id}"],
        ["Date:", datetime.utcnow().strftime("%Y-%m-%d")],
        ["Customer:", order.customer_name],
        ["Contact:", order.customer_contact or "-"],
        ["Currency:", order.currency],
    ]
    meta_table = Table(meta, colWidths=[35 * mm, 120 * mm])
    meta_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(meta_table)
    elements.append(Spacer(1, 8 * mm))

    rows = [["Description", "Qty", "Unit Price", "Line Total"]]
    total = 0
    for item in line_items:
        line_total = item.quantity * item.unit_price
        total += line_total
        rows.append([item.description, f"{item.quantity:g}", f"{item.unit_price:,.2f}", f"{line_total:,.2f}"])
    rows.append(["", "", "TOTAL", f"{total:,.2f} {order.currency}"])

    item_table = Table(rows, colWidths=[80 * mm, 20 * mm, 35 * mm, 35 * mm])
    item_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (2, -1), (-1, -1), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -2), 0.5, colors.HexColor("#dddddd")),
        ("LINEABOVE", (0, -1), (-1, -1), 1, colors.black),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(item_table)

    if order.note:
        elements.append(Spacer(1, 8 * mm))
        elements.append(Paragraph(f"<b>Note:</b> {order.note}", styles["Normal"]))

    elements.append(Spacer(1, 15 * mm))
    elements.append(Paragraph("Designed & Developed by Tesfaye Alemayehu", styles["Normal"]))

    doc.build(elements)
    return buffer.getvalue()
