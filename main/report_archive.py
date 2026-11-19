"""成果レポートのアーカイブ（学校ごとの永久保存）。

- super_teacher が、締め切られた提出版を「アーカイブに保存」する。保存した時点の枠名・グループ名・メンバー・テーマ・本文・
  テンプレート・先生のレビューを、元のグループ・枠・提出の表とは結びつけずにコピーして持つ（あとで元が消えても残る）。
- 同じ学校の先生と生徒は、全件を一覧で見て、キーワード・年度・期間・枠・提出レポート名で検索できる。
- PDFは保存せず、見るたびに保存した内容から作る。画像は今ある画像ファイルを参照するだけで、アーカイブ用に複製しない。
- 削除は super_teacher が自校の分、super admin が全校の分を、「delete」と入力して行う。
"""
import asyncio
import datetime
import json
import re
import secrets
import unicodedata
import zipfile
from contextlib import closing
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from group_reports import JST, pdf_sections
from report_header import group_context_snapshot, header_text, normalize_header_style, DEFAULT_HEADER_ITEMS
from report_reviews import (attempt_label, day_label, group_display_name, review_row, safe_filename, school_groups,
                            submission_by_id, utc_text)
from studies import connect_studies, theme_value
from study_pdf import render_study_pdf
from workspace_frames import frame_label

PER_PAGE = 50
ZIP_LIMIT = 200
DELETE_WORD = 'delete'
ARCHIVE_CSRF_KEY = 'report_archive_csrf'
ADMIN_CSRF_KEY = 'report_archive_admin_csrf'
SEARCH_TEXT_MAX = 200_000
HASH_PATTERN = re.compile(r'[0-9a-f]{64}')


# ---------------------------------------------------------------- 共通

def normalize_search(text):
    """検索用に、全角半角・大文字小文字・カタカナとひらがなの違いをなくす。"""
    text = unicodedata.normalize('NFKC', text or '').casefold()
    return ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヶ' else c for c in text)


def fiscal_year(day):
    """提出日（YYYY-MM-DD）の年度（4月始まり）。年度は保存せず、日付から求める。"""
    value = datetime.date.fromisoformat(day)
    return value.year if value.month >= 4 else value.year - 1


def fiscal_range(year):
    return f'{year}-04-01', f'{year + 1}-03-31'


def ymd(value):
    """保存された日時（UTC）を、日本時間の「YYYY/MM/DD」にする。"""
    try:
        moment = datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return value or ''
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.astimezone(JST).strftime('%Y/%m/%d')


def image_hashes(report):
    """レポート本文に使われている画像のハッシュ。"""
    found = []
    for item in (report or {}).values():
        if isinstance(item, dict):
            for image in item.get('images', []) or []:
                digest = image.get('content_hash') if isinstance(image, dict) else None
                if isinstance(digest, str) and HASH_PATTERN.fullmatch(digest) and digest not in found:
                    found.append(digest)
    return found


def build_search_text(theme, group_name, frame_name, round_title, members, custom_labels, template, report, review_overall):
    parts = [theme, group_name, frame_name, round_title, review_overall]
    for member in members:
        parts += [member.get('id', ''), member.get('name', ''), member.get('furigana', '')]
        parts += [v for v in (member.get('custom') or {}).values() if v]
    for field in template.get('fields', []):
        item = (report or {}).get(field.get('stable_key')) or {}
        parts += [field.get('label', ''), item.get('value', '') if isinstance(item, dict) else '']
        for image in (item.get('images', []) if isinstance(item, dict) else []):
            parts.append(image.get('caption', '') if isinstance(image, dict) else '')
    return normalize_search('\n'.join(p for p in parts if isinstance(p, str) and p))[:SEARCH_TEXT_MAX]


# ---------------------------------------------------------------- 保存

def archived_ids(db, school, submission_ids):
    ids = [int(i) for i in submission_ids]
    if not ids:
        return {}
    marks = ','.join('?' for _ in ids)
    return {r[0]: r[1] for r in db.execute(
        f'SELECT submission_id,id FROM report_archive WHERE school=? AND submission_id IN ({marks})', (school, *ids))}


def archive_submission(db, school, submission_id, actor, now=None):
    """提出版1件をアーカイブに保存し、アーカイブのIDを返す。保存済み・未提出・他校のものは例外。"""
    sub = submission_by_id(db, submission_id)
    if sub is None or sub['school'] != school:
        raise HTTPException(404, '提出が見つかりません。')
    if sub['version_id'] is None or sub['finalized_at'] is None:
        raise HTTPException(409, '提出されていないため、アーカイブに保存できません。')
    if db.execute('SELECT 1 FROM report_archive WHERE submission_id=?', (submission_id,)).fetchone():
        raise HTTPException(409, 'この提出はすでにアーカイブに保存されています。')
    version = db.execute('''SELECT template_snapshot,report_snapshot,context_snapshot,kind,scheduled_date,version_number,saved_by,saved_at
        FROM group_report_version WHERE id=? AND group_id=?''', (sub['version_id'], sub['group_id'])).fetchone()
    if version is None:
        raise HTTPException(404, '提出版が見つかりません。')
    template, report = json.loads(version[0]), json.loads(version[1])
    context = json.loads(version[2]) if version[2] else group_context_snapshot(db, sub['group_id'], school)
    members, custom_labels = context.get('members', []), context.get('custom_labels', {})
    group = next((g for g in school_groups(db, school) if g['id'] == sub['group_id']), None)
    group_name = group_display_name(group) if group else (context.get('group_name') or sub['group_id'])
    group_kind = group['kind'] if group else 'group'
    frame_key = sub['round_frame'] or 0
    frame_name = frame_label(db, school, frame_key)
    description = ''
    if frame_key:
        row = db.execute('SELECT name,description FROM workspace_frame WHERE id=? AND school=?', (frame_key, school)).fetchone()
        frame_name, description = (row[0], row[1]) if row else (frame_name, '')
    round_title = sub['title'] or day_label(sub['round_day'])
    review = review_row(db, submission_id)
    overall = (review or {}).get('overall_comment', '') if review and review['result'] else ''
    fields = (review or {}).get('field_comments', {}) if review and review['result'] else {}
    theme = theme_value(template, report)
    search = build_search_text(theme, group_name, frame_name, round_title, members, custom_labels, template, report, overall)
    version_json = json.dumps({'kind': version[3], 'scheduled_date': version[4], 'version_number': version[5],
                               'saved_by': version[6], 'saved_at': version[7]}, ensure_ascii=False)
    now = now or utc_text()
    archive_id = db.execute('''INSERT INTO report_archive(school,frame_id,frame_name,frame_description,round_day,round_title,attempt,
        submission_id,group_id,group_name,group_kind,theme,members_json,custom_labels_json,template_snapshot,report_snapshot,version_json,
        submitted_at,submit_method,submitted_by,review_overall,review_fields_json,review_result,returned_at,returned_by,
        archived_at,archived_by,search_text) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (school, frame_key, frame_name, description, sub['round_day'], round_title, sub['attempt'], submission_id,
         sub['group_id'], group_name, group_kind, theme, json.dumps(members, ensure_ascii=False),
         json.dumps(custom_labels, ensure_ascii=False), version[0], version[1], version_json,
         sub['submitted_at'], sub['method'], sub['submitted_by'],
         overall, json.dumps(fields, ensure_ascii=False), review['result'] if review else None,
         review['returned_at'] if review else None, review['returned_by'] if review else None,
         now, actor, search)).lastrowid
    db.executemany('INSERT OR IGNORE INTO report_archive_asset(archive_id,content_hash) VALUES(?,?)',
                   [(archive_id, digest) for digest in image_hashes(report)])
    return archive_id


def archive_many(db, school, submission_ids, actor, now=None):
    """まとめて保存する。保存済み・未提出のものは飛ばし、結果の件数を返す。"""
    saved, skipped = 0, 0
    for submission_id in dict.fromkeys(int(i) for i in submission_ids):
        try:
            archive_submission(db, school, submission_id, actor, now)
            saved += 1
        except HTTPException as error:
            if error.status_code not in (404, 409):
                raise
            skipped += 1
    return {'saved': saved, 'skipped': skipped}


def round_latest(db, school, frame_key, day):
    """提出回で、グループごとに一番新しい（再提出があればそれ）提出済みの提出ID。"""
    latest = {}
    for submission_id, group_id in db.execute("""SELECT id,group_id FROM group_report_submission
            WHERE school=? AND round_frame=? AND round_day=? AND version_id IS NOT NULL AND finalized_at IS NOT NULL
            ORDER BY group_id,attempt""", (school, frame_key, day)):
        latest[group_id] = submission_id
    return list(latest.values())


def round_archive_status(db, school, frame_key, day):
    """提出回のアーカイブの状況：保存できる（まだ）グループ数・保存済み数・再提出待ちのグループ数。"""
    latest = round_latest(db, school, frame_key, day)
    archived = archived_ids(db, school, latest)
    waiting = db.execute("""SELECT COUNT(DISTINCT group_id) FROM group_report_submission
        WHERE school=? AND round_frame=? AND round_day=? AND version_id IS NULL""", (school, frame_key, day)).fetchone()[0]
    return {'pending': len(latest) - len(archived), 'archived': len(archived), 'waiting': waiting}


def archive_round(db, school, frame_key, day, actor, now=None):
    """提出回の、グループごとに一番新しい提出を自動でアーカイブに保存する（保存済みは飛ばす）。締め切られた提出回だけ。"""
    info = db.execute('SELECT finalized_at FROM group_report_submission_day WHERE school=? AND frame_id=? AND day=?',
                      (school, frame_key, day)).fetchone()
    if info is None:
        raise HTTPException(404, '提出日が見つかりません。')
    if info[0] is None:
        raise HTTPException(409, '期限前の提出回は、アーカイブに保存できません。')
    latest = round_latest(db, school, frame_key, day)
    already = archived_ids(db, school, latest)
    pending = [i for i in latest if i not in already]
    if not pending:
        raise HTTPException(409, '保存できる提出がありません（すべて保存済み、または提出がありません）。')
    result = archive_many(db, school, pending, actor, now)
    return {**result, 'already': len(already), 'waiting': round_archive_status(db, school, frame_key, day)['waiting']}


# ---------------------------------------------------------------- 検索・一覧

def clean_day(value):
    try:
        return datetime.date.fromisoformat(value).isoformat() if value else ''
    except ValueError:
        return ''


def parse_filters(q='', fy='', date_from='', date_to='', frame='', title='', page='1', school=''):
    try:
        year = int(fy) if fy not in ('', None, 'all') else None
    except ValueError:
        year = None
    try:
        page_number = max(1, int(page))
    except (TypeError, ValueError):
        page_number = 1
    return {'q': (q or '').strip()[:200], 'fy': year, 'date_from': clean_day(date_from), 'date_to': clean_day(date_to),
            'frame': (frame or '')[:100], 'title': (title or '')[:100], 'page': page_number, 'school': (school or '')[:200]}


def filter_query(filters, **override):
    """検索条件をURLの文字列にする（空の条件は省く）。"""
    values = {**filters, **override}
    pairs = {k: values[k] for k in ('q', 'fy', 'date_from', 'date_to', 'frame', 'title', 'school') if values.get(k) not in ('', None)}
    if values.get('page', 1) != 1:
        pairs['page'] = values['page']
    return ('?' + urlencode(pairs)) if pairs else ''


def _where(filters, school):
    where, params = [], []
    if school is not None:
        where.append('school=?')
        params.append(school)
    elif filters.get('school'):
        where.append('school=?')
        params.append(filters['school'])
    for term in normalize_search(filters['q']).split():
        where.append('instr(search_text,?)>0')
        params.append(term)
    if filters['fy'] is not None:
        start, end = fiscal_range(filters['fy'])
        where.append('round_day BETWEEN ? AND ?')
        params += [start, end]
    if filters['date_from']:
        where.append('round_day>=?')
        params.append(filters['date_from'])
    if filters['date_to']:
        where.append('round_day<=?')
        params.append(filters['date_to'])
    if filters['frame']:
        where.append('frame_name=?')
        params.append(filters['frame'])
    if filters['title']:
        where.append('round_title=?')
        params.append(filters['title'])
    return (' WHERE ' + ' AND '.join(where)) if where else '', params


def member_names(members_json):
    try:
        members = json.loads(members_json or '[]')
    except ValueError:
        return []
    return [m.get('name') or m.get('id') or '' for m in members if isinstance(m, dict)]


def search_archives(db, school, filters, per_page=PER_PAGE):
    """school=None なら全校（super admin 用）。"""
    where, params = _where(filters, school)
    total = db.execute(f'SELECT COUNT(*) FROM report_archive{where}', params).fetchone()[0]
    pages = max(1, -(-total // per_page))
    page = min(filters['page'], pages)
    rows = db.execute(f'''SELECT id,school,round_day,round_title,attempt,frame_name,group_name,group_kind,theme,members_json,
        archived_at,review_result FROM report_archive{where} ORDER BY round_day DESC,id DESC LIMIT ? OFFSET ?''',
                      (*params, per_page, (page - 1) * per_page)).fetchall()
    items = [{'id': r[0], 'school': r[1], 'round_day': r[2], 'day_label': r[2].replace('-', '/'), 'round_title': r[3],
              'attempt_label': attempt_label(r[4]), 'frame_name': r[5], 'group_name': r[6], 'group_kind': r[7],
              'theme': r[8], 'members': member_names(r[9]), 'archived_label': ymd(r[10]), 'review_result': r[11],
              'fiscal_year': fiscal_year(r[2])} for r in rows]
    return {'rows': items, 'total': total, 'page': page, 'pages': pages, 'per_page': per_page}


def archive_facets(db, school):
    """絞り込みの選択肢。年度は提出日から求める。"""
    scope, args = ('WHERE school=?', (school,)) if school is not None else ('', ())
    years = [r[0] for r in db.execute(f'''SELECT DISTINCT CAST(substr(round_day,1,4) AS INTEGER)-(CAST(substr(round_day,6,2) AS INTEGER)<4)
        FROM report_archive {scope} ORDER BY 1 DESC''', args)]
    frames = [r[0] for r in db.execute(f'SELECT DISTINCT frame_name FROM report_archive {scope} ORDER BY frame_name', args)]
    titles = [r[0] for r in db.execute(f'SELECT DISTINCT round_title FROM report_archive {scope} ORDER BY round_title', args)]
    schools = [r[0] for r in db.execute('SELECT DISTINCT school FROM report_archive ORDER BY school')] if school is None else []
    return {'years': years, 'frames': frames, 'titles': titles, 'schools': schools}


# ---------------------------------------------------------------- 詳細・PDF

def load_archive(db, archive_id, school=None):
    cursor = db.execute('SELECT * FROM report_archive WHERE id=?', (archive_id,))
    row = cursor.fetchone()
    if row is None:
        raise HTTPException(404, 'アーカイブが見つかりません。')
    archive = dict(zip([c[0] for c in cursor.description], row))
    if school is not None and archive['school'] != school:
        raise HTTPException(404, 'アーカイブが見つかりません。')
    return archive


def archive_detail(db, archive_id, base_url, school=None):
    archive = load_archive(db, archive_id, school)
    template, report = json.loads(archive['template_snapshot']), json.loads(archive['report_snapshot'])
    comments = json.loads(archive['review_fields_json'] or '{}')
    fields = []
    for field in template.get('fields', []):
        item = report.get(field.get('stable_key')) or {}
        fields.append({'key': field.get('stable_key'), 'label': field.get('label', ''), 'type': field.get('field_type', 'text'),
                       'value': item.get('value', '') if isinstance(item, dict) else '',
                       'images': [{'url': f"{base_url}/{archive_id}/assets/{i['content_hash']}", 'caption': i.get('caption', '')}
                                  for i in (item.get('images', []) if isinstance(item, dict) else [])
                                  if isinstance(i, dict) and HASH_PATTERN.fullmatch(str(i.get('content_hash', '')))],
                       'comment': comments.get(field.get('stable_key'), '')})
    members = json.loads(archive['members_json'] or '[]')
    labels = json.loads(archive['custom_labels_json'] or '{}')
    return {**archive, 'fields': fields, 'members': members, 'custom_labels': labels,
            'day_label': archive['round_day'].replace('-', '/'), 'attempt_label': attempt_label(archive['attempt']),
            'archived_label': ymd(archive['archived_at']), 'submitted_label': ymd(archive['submitted_at']),
            'returned_label': ymd(archive['returned_at']), 'fiscal_year': fiscal_year(archive['round_day'])}


def archive_pdf(db, image_dir, archive):
    """保存した内容からPDFを作る（PDFそのものは保存しない）。"""
    template, report = json.loads(archive['template_snapshot']), json.loads(archive['report_snapshot'])
    meta = json.loads(archive['version_json'] or '{}')
    context = {'group_name': archive['group_name'], 'members': json.loads(archive['members_json'] or '[]'),
               'custom_labels': json.loads(archive['custom_labels_json'] or '{}')}
    header = header_text(template.get('header_items', DEFAULT_HEADER_ITEMS),
                         {**meta, 'school_name': archive['school'], 'group_id': archive['group_id']}, context)
    review = None
    if archive['review_overall'] or archive['review_fields_json'] not in ('', '{}'):
        review = {'overall': archive['review_overall'], 'fields': json.loads(archive['review_fields_json'] or '{}')}
    sections = pdf_sections(db, image_dir, template, report, header, normalize_header_style(template.get('header_style')), review=review)
    return render_study_pdf(sections)


def archive_zip(db, image_dir, school, filters):
    """検索結果のPDFをZIPにまとめる（上限 ZIP_LIMIT 件）。"""
    where, params = _where(filters, school)
    rows = db.execute(f'SELECT id FROM report_archive{where} ORDER BY round_day DESC,id DESC LIMIT ?', (*params, ZIP_LIMIT)).fetchall()
    buffer, used = BytesIO(), set()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive_file:
        for index, (archive_id,) in enumerate(rows, 1):
            archive = load_archive(db, archive_id)
            name = safe_filename(f"{index:03d}_{archive['round_day']}_{archive['round_title']}_{archive['group_name']}_{attempt_label(archive['attempt'])}")
            while name in used:
                name += '_'
            used.add(name)
            archive_file.writestr(name + '.pdf', archive_pdf(db, image_dir, archive))
    return buffer.getvalue(), len(rows)


# ---------------------------------------------------------------- 削除

def delete_archive(db, archive_id, actor, role, confirm, school=None, now=None):
    """アーカイブを削除する（画像ファイルは消さない）。school を渡すと、その学校のものだけ削除できる。"""
    if not isinstance(confirm, str) or confirm.strip().lower() != DELETE_WORD:
        raise HTTPException(422, f'確認のため「{DELETE_WORD}」と入力してください。')
    archive = load_archive(db, archive_id, school)
    db.execute('''INSERT INTO report_archive_deletion_log(school,archive_id,group_name,round_title,round_day,deleted_at,deleted_by,deleted_role)
        VALUES(?,?,?,?,?,?,?,?)''', (archive['school'], archive_id, archive['group_name'], archive['round_title'], archive['round_day'],
                                       now or utc_text(), actor, role))
    db.execute('DELETE FROM report_archive_asset WHERE archive_id=?', (archive_id,))
    db.execute('DELETE FROM report_archive WHERE id=?', (archive_id,))
    return {'ok': True}


# ---------------------------------------------------------------- ルーター

def build_report_archive_router(database_path, templates, image_dir, require_super_admin=None):
    router = APIRouter()
    image_dir = Path(image_dir)

    def teacher_identity(request, super_only=False):
        if request.session.get('teacher_login') is not True:
            raise HTTPException(403, '先生としてログインしてください。')
        teacher_id = request.session.get('teacher_id')
        with closing(connect_studies(database_path)) as db:
            row = db.execute('SELECT school,role FROM teacher WHERE id=?', (teacher_id,)).fetchone()
        if row is None:
            raise HTTPException(403, '先生アカウントが確認できません。')
        if super_only and row[1] != 'super_teacher':
            raise HTTPException(403, 'super_teacherのみ利用できます。')
        return teacher_id, row[0], row[1]

    def student_school(request):
        if request.session.get('user_login') is not True or not request.session.get('user_id'):
            raise HTTPException(401, '生徒としてログインしてください。')
        with closing(connect_studies(database_path)) as db:
            row = db.execute('SELECT school FROM student WHERE id=?', (request.session['user_id'],)).fetchone()
        if row is None or not row[0]:
            raise HTTPException(403, '学校が確認できません。')
        return row[0]

    def csrf_token(request, key=ARCHIVE_CSRF_KEY):
        return request.session.setdefault(key, secrets.token_urlsafe(32))

    def check_csrf(request, value, key=ARCHIVE_CSRF_KEY):
        expected = request.session.get(key, '')
        if not expected or not isinstance(value, str) or not secrets.compare_digest(expected, value):
            raise HTTPException(403, '画面を再読み込みしてください。')

    def list_context(request, school, base_url, filters, extra=None):
        with closing(connect_studies(database_path)) as db:
            result = search_archives(db, school, filters)
            facets = archive_facets(db, school)
        return {'request': request, 'base_url': base_url, 'filters': filters, 'result': result, 'facets': facets,
                'query': filter_query(filters, page=1), 'page_query': lambda page: filter_query(filters, page=page),
                'zip_limit': ZIP_LIMIT, **(extra or {})}

    def safe_back(request):
        """詳細画面から一覧へ戻るときの検索条件（?で始まる短い文字列だけ受け付ける）。"""
        back = request.query_params.get('back', '')
        return back if re.fullmatch(r'\?[A-Za-z0-9%=&_.~+\-]{0,600}', back or '') else ''

    def filters_from(request):
        p = request.query_params
        return parse_filters(p.get('q', ''), p.get('fy', ''), p.get('date_from', ''), p.get('date_to', ''),
                             p.get('frame', ''), p.get('title', ''), p.get('page', '1'), p.get('school', ''))

    async def pdf_response(archive_id, school):
        def build():
            with closing(connect_studies(database_path)) as db:
                archive = load_archive(db, archive_id, school)
                return archive_pdf(db, image_dir, archive), archive
        pdf, archive = await asyncio.to_thread(build)
        name = safe_filename(f"{archive['round_day']}_{archive['round_title']}_{archive['group_name']}") + '.pdf'
        return Response(pdf, media_type='application/pdf',
                        headers={'Content-Disposition': f"inline; filename=\"report.pdf\"; filename*=UTF-8''{quote(name)}"})

    def asset_response(archive_id, content_hash, school):
        if not HASH_PATTERN.fullmatch(content_hash):
            raise HTTPException(404, '画像が見つかりません。')
        with closing(connect_studies(database_path)) as db:
            owner = db.execute('SELECT school FROM report_archive WHERE id=?', (archive_id,)).fetchone()
            used = db.execute('SELECT 1 FROM report_archive_asset WHERE archive_id=? AND content_hash=?', (archive_id, content_hash)).fetchone()
            row = db.execute('SELECT stored_name,content_type FROM group_report_asset WHERE content_hash=?', (content_hash,)).fetchone()
        if not owner or owner[0] != school or not used or not row or not (image_dir / row[0]).is_file():
            raise HTTPException(404, '画像が見つかりません。')
        return Response((image_dir / row[0]).read_bytes(), media_type=row[1], headers={'Cache-Control': 'private, max-age=3600'})

    # ---- 先生
    @router.get('/teacher/report-archive')
    async def teacher_list(request: Request):
        teacher_id, school, role = teacher_identity(request)
        context = list_context(request, school, '/teacher/report-archive', filters_from(request),
                               {'school': school, 'teacher_id': teacher_id, 'teacher_role': role,
                                'zip_url': '/teacher/report-archive/download.zip'})
        return templates.TemplateResponse(request=request, name='teacher/report_archive.html', context=context)

    @router.get('/teacher/report-archive/download.zip')
    async def teacher_zip(request: Request):
        _, school, _ = teacher_identity(request)
        filters = filters_from(request)

        def build():
            with closing(connect_studies(database_path)) as db:
                return archive_zip(db, image_dir, school, filters)
        data, count = await asyncio.to_thread(build)
        if not count:
            raise HTTPException(404, 'ダウンロードできるアーカイブがありません。')
        return Response(data, media_type='application/zip', headers={'Content-Disposition': 'attachment; filename="report-archive.zip"'})

    @router.get('/teacher/report-archive/{archive_id}')
    async def teacher_detail(request: Request, archive_id: int):
        teacher_id, school, role = teacher_identity(request)
        with closing(connect_studies(database_path)) as db:
            detail = archive_detail(db, archive_id, '/teacher/report-archive', school)
        return templates.TemplateResponse(request=request, name='teacher/report_archive_detail.html', context={
            'request': request, 'base_url': '/teacher/report-archive', 'a': detail, 'school': school, 'teacher_id': teacher_id,
            'teacher_role': role, 'can_delete': role == 'super_teacher', 'csrf_token': csrf_token(request),
            'delete_word': DELETE_WORD, 'back_query': safe_back(request),
            'delete_url': f'/teacher/api/report-archive/{archive_id}/delete'})

    @router.get('/teacher/report-archive/{archive_id}/report.pdf')
    async def teacher_pdf(request: Request, archive_id: int):
        _, school, _ = teacher_identity(request)
        return await pdf_response(archive_id, school)

    @router.get('/teacher/report-archive/{archive_id}/assets/{content_hash}')
    async def teacher_asset(request: Request, archive_id: int, content_hash: str):
        _, school, _ = teacher_identity(request)
        return asset_response(archive_id, content_hash, school)

    async def json_body(request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422, '送信内容が正しくありません。')
        return body

    @router.post('/teacher/api/report-archive')
    async def teacher_save(request: Request):
        teacher_id, school, _ = teacher_identity(request, super_only=True)
        body = await json_body(request)
        check_csrf(request, body.get('csrf_token'))
        frame_key, day = body.get('frame_key'), body.get('day')
        if not isinstance(frame_key, int) or isinstance(frame_key, bool) or not isinstance(day, str):
            raise HTTPException(422, '保存する提出回を指定してください。')
        with closing(connect_studies(database_path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            return {'ok': True, **archive_round(db, school, frame_key, day, teacher_id)}

    @router.post('/teacher/api/report-archive/{archive_id}/delete')
    async def teacher_delete(request: Request, archive_id: int):
        teacher_id, school, role = teacher_identity(request, super_only=True)
        body = await json_body(request)
        check_csrf(request, body.get('csrf_token'))
        with closing(connect_studies(database_path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            return delete_archive(db, archive_id, teacher_id, role, body.get('confirm'), school=school)

    # ---- 生徒（同じ学校の全件を、先生と同じ一覧で見られる）
    @router.get('/report-archive')
    async def student_list(request: Request):
        school = student_school(request)
        context = list_context(request, school, '/report-archive', filters_from(request), {'school': school})
        return templates.TemplateResponse(request=request, name='report_archive.html', context=context)

    @router.get('/report-archive/{archive_id}')
    async def student_detail(request: Request, archive_id: int):
        school = student_school(request)
        with closing(connect_studies(database_path)) as db:
            detail = archive_detail(db, archive_id, '/report-archive', school)
        return templates.TemplateResponse(request=request, name='report_archive_detail.html', context={
            'request': request, 'base_url': '/report-archive', 'a': detail, 'school': school, 'can_delete': False,
            'back_query': safe_back(request)})

    @router.get('/report-archive/{archive_id}/report.pdf')
    async def student_pdf(request: Request, archive_id: int):
        return await pdf_response(archive_id, student_school(request))

    @router.get('/report-archive/{archive_id}/assets/{content_hash}')
    async def student_asset(request: Request, archive_id: int, content_hash: str):
        return asset_response(archive_id, content_hash, student_school(request))

    # ---- super admin（全校）
    def admin_identity(request):
        if require_super_admin is None:
            raise HTTPException(403, '最上位管理者の権限が必要です。')
        with closing(connect_studies(database_path)) as db:
            return require_super_admin(db, request)

    @router.get('/admin/report-archive')
    async def admin_list(request: Request):
        admin = admin_identity(request)
        context = list_context(request, None, '/admin/report-archive', filters_from(request),
                               {'csrf_token': csrf_token(request, ADMIN_CSRF_KEY), 'delete_word': DELETE_WORD, 'admin_name': admin[1]})
        return templates.TemplateResponse(request=request, name='admin/report_archive.html', context=context)

    @router.get('/admin/report-archive/{archive_id}')
    async def admin_detail(request: Request, archive_id: int):
        admin_identity(request)
        with closing(connect_studies(database_path)) as db:
            detail = archive_detail(db, archive_id, '/admin/report-archive')
        return templates.TemplateResponse(request=request, name='admin/report_archive_detail.html', context={
            'request': request, 'base_url': '/admin/report-archive', 'a': detail, 'can_delete': True, 'show_school': True,
            'csrf_token': csrf_token(request, ADMIN_CSRF_KEY), 'delete_word': DELETE_WORD, 'back_query': safe_back(request),
            'delete_url': f'/admin/api/report-archive/{archive_id}/delete'})

    @router.get('/admin/report-archive/{archive_id}/report.pdf')
    async def admin_pdf(request: Request, archive_id: int):
        admin_identity(request)
        return await pdf_response(archive_id, None)

    @router.get('/admin/report-archive/{archive_id}/assets/{content_hash}')
    async def admin_asset(request: Request, archive_id: int, content_hash: str):
        admin_identity(request)
        with closing(connect_studies(database_path)) as db:
            row = db.execute('SELECT school FROM report_archive WHERE id=?', (archive_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '画像が見つかりません。')
        return asset_response(archive_id, content_hash, row[0])

    @router.post('/admin/api/report-archive/{archive_id}/delete')
    async def admin_delete(request: Request, archive_id: int):
        admin = admin_identity(request)
        body = await json_body(request)
        check_csrf(request, body.get('csrf_token'), ADMIN_CSRF_KEY)
        with closing(connect_studies(database_path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            return delete_archive(db, archive_id, admin[0], 'super_admin', body.get('confirm'))

    return router
