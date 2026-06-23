import base64
import io
from datetime import datetime

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.utils import ImageReader
from PIL import Image


def stamp_signature_on_pdf(template_file, signature_b64, signer_name, signed_at=None):
    """
    Overlays a drawn signature onto the last page of a PDF template.
    Returns a BytesIO of the signed PDF.
    """
    if signed_at is None:
        signed_at = datetime.now()

    # Decode the base64 PNG (strip data URI prefix if present)
    if ',' in signature_b64:
        signature_b64 = signature_b64.split(',', 1)[1]
    sig_bytes = base64.b64decode(signature_b64)
    sig_image = Image.open(io.BytesIO(sig_bytes)).convert('RGBA')

    # Read template PDF
    template_file.seek(0)
    reader = PdfReader(template_file)
    last_page = reader.pages[-1]
    page_width = float(last_page.mediabox.width)
    page_height = float(last_page.mediabox.height)

    # Build signature overlay as a single-page PDF
    overlay_buf = io.BytesIO()
    c = rl_canvas.Canvas(overlay_buf, pagesize=(page_width, page_height))

    # Signature box: bottom-right, 40px from edges
    sig_w, sig_h = 220, 70
    sig_x = page_width - sig_w - 50
    sig_y = 55

    # Draw a light box behind the signature
    c.setFillColorRGB(0.97, 0.97, 0.97)
    c.setStrokeColorRGB(0.8, 0.8, 0.8)
    c.roundRect(sig_x - 6, sig_y - 20, sig_w + 12, sig_h + 28, 4, fill=1, stroke=1)

    # Draw the signature image
    sig_reader = ImageReader(io.BytesIO(sig_bytes))
    c.drawImage(sig_reader, sig_x, sig_y, width=sig_w, height=sig_h, mask='auto')

    # Label and date
    c.setFont('Helvetica', 7)
    c.setFillColorRGB(0.4, 0.4, 0.4)
    c.drawString(sig_x, sig_y - 12, f"Signed electronically by {signer_name}")
    c.drawString(sig_x, sig_y - 21, f"on {signed_at.strftime('%d %B %Y at %H:%M')}")

    c.save()

    # Merge overlay onto last page of template
    overlay_buf.seek(0)
    overlay_reader = PdfReader(overlay_buf)
    writer = PdfWriter()

    for i, page in enumerate(reader.pages):
        if i == len(reader.pages) - 1:
            page.merge_page(overlay_reader.pages[0])
        writer.add_page(page)

    output = io.BytesIO()
    writer.write(output)
    output.seek(0)
    return output
