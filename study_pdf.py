"""In-memory rendering of DB-defined research sections."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, CondPageBreak


FONT = 'ResearchJapanese'
pdfmetrics.registerFont(TTFont(FONT, str(Path(__file__).parent / 'assets/fonts/ZenKakuGothicNew-Regular.ttf')))


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
        label, value = section[:2]
        heading_size, body_size, hide_heading = section[2:] if len(section) > 2 else (13, 11, False)
        if not value.strip():
            continue
        field_body = ParagraphStyle('field-body', parent=body, fontSize=body_size, leading=body_size * 17 / 11)
        field_heading = ParagraphStyle('field-heading', parent=heading, fontSize=heading_size, leading=heading_size * 19 / 13)
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
