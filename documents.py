"""
Generates the real paper-trail documents for a sales order (Proforma, Invoice, etc.)
as actual downloadable PDFs, using reportlab (pure Python, no system dependencies).
"""

import io
import base64
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_LEFT


def generate_document_pdf(doc_type_label: str, company_name: str, order, line_items,
                           bank_details: str = None, logo_base64: str = None, logo_mime: str = None,
                           is_delivery_note: bool = False) -> bytes:
    """
    doc_type_label: what to print at the top, e.g. "PROFORMA INVOICE (QUOTE)", "SALES INVOICE", "DELIVERY NOTE"
    order: a SalesOrder ORM object
    line_items: list of SalesOrderLineItem ORM objects
    bank_details: the company's saved bank/payment info, shown at the bottom if provided
    logo_base64 / logo_mime: the company's uploaded logo, shown in the header if provided
    is_delivery_note: when True, prices/VAT are hidden (a delivery note proves goods were
    delivered, it isn't a bill) and signature lines are added at the bottom instead.
    Returns raw PDF bytes.
    """
    is_proforma = "PROFORMA" in doc_type_label.upper()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=15 * mm, bottomMargin=20 * mm)
    styles = getSampleStyleSheet()
    right_style = ParagraphStyle("right", parent=styles["Normal"], alignment=TA_RIGHT)

    elements = []

    # --- Letterhead: logo (if set) alongside company name + document type ---
    name_block = [
        Paragraph(f"<b>{company_name}</b>", styles["Title"]),
        Paragraph(doc_type_label, styles["Heading2"]),
    ]

    if logo_base64:
        try:
            logo_bytes = base64.b64decode(logo_base64)
            logo_img = Image(io.BytesIO(logo_bytes))
            # Scale to a sensible header size while keeping proportions
            max_width, max_height = 35 * mm, 25 * mm
            ratio = min(max_width / logo_img.imageWidth, max_height / logo_img.imageHeight)
            logo_img.drawWidth = logo_img.imageWidth * ratio
            logo_img.drawHeight = logo_img.imageHeight * ratio

            header_table = Table([[logo_img, name_block]], colWidths=[40 * mm, 130 * mm])
            header_table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (0, 0), "LEFT"),
            ]))
            elements.append(header_table)
        except Exception:
            # If the logo fails to decode/render for any reason, fall back to text-only header
            elements.extend(name_block)
    else:
        elements.extend(name_block)

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

    subtotal = 0
    for item in line_items:
        subtotal += item.quantity * item.unit_price

    vat_percent = getattr(order, "vat_percent", 15.0) or 0
    vat_amount = subtotal * (vat_percent / 100)
    grand_total = subtotal + vat_amount

    if is_delivery_note:
        # A delivery note proves goods were handed over — it's not a bill, so no prices.
        rows = [["Description", "Qty Delivered"]]
        for item in line_items:
            rows.append([item.description, f"{item.quantity:g}"])
        item_table = Table(rows, colWidths=[130 * mm, 40 * mm])
        item_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
        ]))
    else:
        rows = [["Description", "Qty", "Unit Price", "Line Total"]]
        for item in line_items:
            line_total = item.quantity * item.unit_price
            rows.append([item.description, f"{item.quantity:g}", f"{item.unit_price:,.2f}", f"{line_total:,.2f}"])

        rows.append(["", "", "Subtotal (before VAT):", f"{subtotal:,.2f}"])
        if vat_percent:
            rows.append(["", "", f"VAT ({vat_percent:g}%):", f"{vat_amount:,.2f}"])
        rows.append(["", "", "TOTAL (after VAT):", f"{grand_total:,.2f} {order.currency}"])

        item_table = Table(rows, colWidths=[80 * mm, 20 * mm, 35 * mm, 35 * mm])
        style_commands = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, len(line_items)), 0.5, colors.HexColor("#dddddd")),
            ("LINEABOVE", (0, len(line_items) + 1), (-1, len(line_items) + 1), 1, colors.black),
            ("FONTNAME", (2, -1), (-1, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
        ]
        item_table.setStyle(TableStyle(style_commands))
    elements.append(item_table)

    # Commercial terms — validity, delivery, downpayment, payment terms.
    # A delivery note only cares about the delivery terms themselves, not pricing/quote terms.
    terms_rows = []
    if not is_delivery_note:
        if is_proforma and getattr(order, "validity_days", None):
            terms_rows.append(["Quote Validity:", f"{order.validity_days} days from the date above"])
        if getattr(order, "downpayment_percent", None):
            downpayment_amount = grand_total * (order.downpayment_percent / 100)
            terms_rows.append([
                "Downpayment Required:",
                f"{order.downpayment_percent:g}% ({downpayment_amount:,.2f} {order.currency})"
            ])
        if getattr(order, "payment_terms", None):
            terms_rows.append(["Payment Terms:", order.payment_terms])
    if getattr(order, "delivery_terms", None):
        terms_rows.append(["Delivery:", order.delivery_terms])

    if terms_rows:
        elements.append(Spacer(1, 8 * mm))
        elements.append(Paragraph("<b>Commercial Terms</b>" if not is_delivery_note else "<b>Delivery Terms</b>", styles["Heading4"]))
        terms_table = Table(terms_rows, colWidths=[45 * mm, 110 * mm])
        terms_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        elements.append(terms_table)

    if order.note:
        elements.append(Spacer(1, 6 * mm))
        elements.append(Paragraph(f"<b>Note:</b> {order.note}", styles["Normal"]))

    if bank_details and not is_delivery_note:
        elements.append(Spacer(1, 8 * mm))
        elements.append(Paragraph("<b>Payment Details</b>", styles["Heading4"]))
        elements.append(Paragraph(bank_details.replace("\n", "<br/>"), styles["Normal"]))

    if is_delivery_note:
        elements.append(Spacer(1, 20 * mm))
        sig_rows = [
            ["Delivered By:", "___________________________", "Date:", "______________"],
            ["Received By:", "___________________________", "Date:", "______________"],
        ]
        sig_table = Table(sig_rows, colWidths=[30 * mm, 65 * mm, 20 * mm, 40 * mm])
        sig_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 14),
        ]))
        elements.append(sig_table)

    elements.append(Spacer(1, 15 * mm))
    elements.append(Paragraph("Designed & Developed by Tesfaye Alemayehu", styles["Normal"]))

    doc.build(elements)
    return buffer.getvalue()


def generate_receipt_pdf(company_name: str, order, payment, total_paid_to_date: float,
                          grand_total: float, logo_base64: str = None, logo_mime: str = None) -> bytes:
    """
    Generates a Payment Receipt for a single payment against a sales order — a company can
    have several of these per order (e.g. a downpayment, then a balance payment).
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=15 * mm, bottomMargin=20 * mm)
    styles = getSampleStyleSheet()

    elements = []

    name_block = [
        Paragraph(f"<b>{company_name}</b>", styles["Title"]),
        Paragraph("PAYMENT RECEIPT", styles["Heading2"]),
    ]

    if logo_base64:
        try:
            logo_bytes = base64.b64decode(logo_base64)
            logo_img = Image(io.BytesIO(logo_bytes))
            max_width, max_height = 35 * mm, 25 * mm
            ratio = min(max_width / logo_img.imageWidth, max_height / logo_img.imageHeight)
            logo_img.drawWidth = logo_img.imageWidth * ratio
            logo_img.drawHeight = logo_img.imageHeight * ratio
            header_table = Table([[logo_img, name_block]], colWidths=[40 * mm, 130 * mm])
            header_table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (0, 0), "LEFT"),
            ]))
            elements.append(header_table)
        except Exception:
            elements.extend(name_block)
    else:
        elements.extend(name_block)

    elements.append(Spacer(1, 10 * mm))

    balance_remaining = max(grand_total - total_paid_to_date, 0)

    meta = [
        ["Receipt #:", f"SO-{order.id}-P{payment.id}"],
        ["Date:", payment.created_at.strftime("%Y-%m-%d")],
        ["Customer:", order.customer_name],
        ["Order Reference:", f"SO-{order.id}"],
        ["Received By:", payment.received_by],
    ]
    meta_table = Table(meta, colWidths=[40 * mm, 115 * mm])
    meta_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(meta_table)
    elements.append(Spacer(1, 10 * mm))

    payment_rows = [
        ["Amount Received:", f"{payment.amount:,.2f} {order.currency}"],
        ["Payment Method:", payment.method or "-"],
        ["Reference / Transaction #:", payment.reference or "-"],
    ]
    payment_table = Table(payment_rows, colWidths=[55 * mm, 100 * mm])
    payment_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eafaf1")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
    ]))
    elements.append(payment_table)
    elements.append(Spacer(1, 10 * mm))

    balance_rows = [
        ["Order Total (incl. VAT):", f"{grand_total:,.2f} {order.currency}"],
        ["Total Paid to Date:", f"{total_paid_to_date:,.2f} {order.currency}"],
        ["Balance Remaining:", f"{balance_remaining:,.2f} {order.currency}"],
    ]
    balance_table = Table(balance_rows, colWidths=[55 * mm, 100 * mm])
    balance_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, -1), (1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEABOVE", (0, -1), (-1, -1), 1, colors.black),
    ]))
    elements.append(balance_table)

    if payment.note:
        elements.append(Spacer(1, 8 * mm))
        elements.append(Paragraph(f"<b>Note:</b> {payment.note}", styles["Normal"]))

    elements.append(Spacer(1, 15 * mm))
    elements.append(Paragraph("Designed & Developed by Tesfaye Alemayehu", styles["Normal"]))

    doc.build(elements)
    return buffer.getvalue()
