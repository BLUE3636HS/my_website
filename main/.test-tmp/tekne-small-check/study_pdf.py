"""In-memory rendering of DB-defined research sections."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, CondPageBreak, Image as PdfImage, Table, TableStyle, KeepTogether


FONT = 'ResearchJapanese'
BOLD_FONT = 'ResearchJapaneseBold'
pdfmetrics.registerFont(TTFont(FONT, str(Path(__file__).parent / 'assets/fonts/ZenKakuGothicNew-Regular.ttf')))
pdfmetrics.registerFont(TTFont(BOLD_FONT, str(Path(__file__).parent / 'assets/fonts/ZenKakuGothicNew-Bold.ttf')))


def render_study_pdf(sections, user_id=None):
    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4, leftMargin=20*mm,
                                 rightMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm)
    body = ParagraphStyle('body', fontName=FONT, fontSize=11, leading=17,
                          wordWrap='CJK', splitLongWords=True)
    heading = ParagraphStyle('heading', parent=body, fontSize=13, leading=19,
                             spaceBefore=12, spaceAfter=6, keepWithNext=False)
    story = []
    if user_id is not None:
        story.append(Paragraph('ユーザーID：' + escape(str(user_id)), body))
        story.append(Spacer(1, 8))
    for section in sections:
        if isinstance(section, dict):
            images = section['images']
            if not images:
                continue
            heading_size = section['heading_font_size']
            body_size = section['body_font_size']
            heading_alignment = section['heading_alignment']
            body_alignment = section['body_alignment']
            field_body = ParagraphStyle('image-caption', parent=body, fontSize=body_size,
                leading=body_size * 17 / 11, alignment={'left': 0, 'center': 1, 'right': 2}[body_alignment],
                fontName=BOLD_FONT if section['body_bold'] else FONT, spaceBefore=3, spaceAfter=6)
            field_heading = ParagraphStyle('image-heading', parent=heading, fontSize=heading_size,
                leading=heading_size * 19 / 13, alignment={'left': 0, 'center': 1, 'right': 2}[heading_alignment],
                fontName=BOLD_FONT if section['heading_bold'] else FONT)
            heading_flow = [] if section['hide_heading'] else [Paragraph(escape(section['label']), field_heading)]

            content_width = A4[0] - 40*mm
            small_width = (content_width - 6*mm) / 2

            def image_cell(path, caption, width, max_height):
                picture = PdfImage(path)
                scale = min(width / picture.imageWidth, max_height / picture.imageHeight, 1)
                picture.drawWidth = picture.imageWidth * scale
                picture.drawHeight = picture.imageHeight * scale
                picture.hAlign = {'left': 'LEFT', 'center': 'CENTER', 'right': 'RIGHT'}[section['image_alignment']]
                return [picture, Paragraph(escape(caption), field_body)]

            if section['image_size'] == 'large':
                for path, caption in images:
                    table = Table([[image_cell(path, caption, content_width, 210*mm)]], colWidths=[content_width])
                    table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'),
                                               ('LEFTPADDING', (0, 0), (-1, -1), 0),
                                               ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
                    story.append(KeepTogether(heading_flow + [table]))
                    heading_flow = []
            else:
                pairs = [images[index:index + 2] for index in range(0, len(images), 2)]
                for pair in pairs:
                    if len(pair) == 2:
                        cells = [image_cell(path, caption, small_width, 90*mm) for path, caption in pair]
                        table = Table([cells], colWidths=[small_width, small_width], hAlign='CENTER')
                    else:
                        cell = image_cell(pair[0][0], pair[0][1], small_width, 90*mm)
                        table = Table([[cell]], colWidths=[small_width],
                                      hAlign={'left': 'LEFT', 'center': 'CENTER', 'right': 'RIGHT'}[section['image_alignment']])
                    table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'),
                                               ('LEFTPADDING', (0, 0), (-1, -1), 0),
                                               ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
                    story.append(KeepTogether(heading_flow + [table]))
                    heading_flow = []
            story.append(Spacer(1, 8))
            continue
        label, value = section[:2]
        heading_size, body_size, hide_heading = section[2:5] if len(section) > 2 else (11, 10, False)
        heading_alignment, body_alignment, heading_bold, body_bold = section[5:9] if len(section) > 5 else ('left', 'left', False, False)
        alignments = {'left': 0, 'center': 1, 'right': 2}
        if not value.strip():
            continue
        field_body = ParagraphStyle('field-body', parent=body, fontSize=body_size, leading=body_size * 17 / 11,
                                    alignment=alignments[body_alignment], fontName=BOLD_FONT if body_bold else FONT)
        field_heading = ParagraphStyle('field-heading', parent=heading, fontSize=heading_size, leading=heading_size * 19 / 13,
                                       alignment=alignments[heading_alignment], fontName=BOLD_FONT if heading_bold else FONT)
        if not hide_heading:
            title = Paragraph(escape(label), field_heading)
            _, title_height = title.wrap(A4[0] - 40*mm, A4[1] - 40*mm)
            # Reserve room for the heading and the first body lines, rather than
            # keeping a potentially multi-page paragraph together with its heading.
            story.append(CondPageBreak(title_height + 18 + 2 * field_body.leading))
            story.append(title)
        # Escaping precedes the insertion of our own line-break markup.
        text = escape(value.replace('\r\n', '\n').replace('\r', '\n')).replace('\n', '<br/>')
        story.append(Paragraph(text, field_body))
        if hide_heading:
            story.append(Spacer(1, 8))
    # A flowable ensures even an empty submission produces a valid, blank page.
    document.build(story or [Spacer(1, 1)])
    return output.getvalue()
