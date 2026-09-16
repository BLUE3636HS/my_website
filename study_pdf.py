"""In-memory rendering of DB-defined research sections."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer


FONT = 'ResearchJapanese'
pdfmetrics.registerFont(TTFont(FONT, str(Path(__file__).parent / 'assets/fonts/ZenKakuGothicNew-Regular.ttf')))


def render_study_pdf(sections, user_id=None):
    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4, leftMargin=20*mm,
                                 rightMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm)
    body = ParagraphStyle('body', fontName=FONT, fontSize=11, leading=17,
                          wordWrap='CJK', splitLongWords=True)
    heading = ParagraphStyle('heading', parent=body, fontSize=13, leading=19,
                             spaceBefore=12, spaceAfter=6, keepWithNext=True)
    story = []
    if user_id is not None:
        story.append(Paragraph('ユーザーID：' + escape(str(user_id)), body))
        story.append(Spacer(1, 8))
    for label, value in sections:
        if not value.strip():
            continue
        story.append(Paragraph(escape(label), heading))
        # Escaping precedes the insertion of our own line-break markup.
        text = escape(value.replace('\r\n', '\n').replace('\r', '\n')).replace('\n', '<br/>')
        story.append(Paragraph(text, body))
    # A flowable ensures even an empty submission produces a valid, blank page.
    document.build(story or [Spacer(1, 1)])
    return output.getvalue()
