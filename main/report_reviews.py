"""成果レポートの提出日・提出・先生のレビュー・期限前リマインド。

- 提出日は「定期自動保存日」のうち、先生が提出日に指定した日（名前付き）。
- 生徒は提出日の23:59までに「提出」ボタンで提出する。提出した時点で確定し、先生が返却するまで出し直せない。
- 期限（23:59）を過ぎると、まだ提出していないグループはその時点の下書きが自動で提出される。
- 提出された版は、期限前でも先生がレビューできる。レビュー中はロックされ、「レビューを終了」か「返却」で外れる。
- 「要再提出」で返却すると、再提出期限つきの再提出（attempt+1）が作られ、同じ流れで提出・レビューする。
"""
import asyncio
import datetime
import json
import re
import secrets
import shutil
import sqlite3
import zipfile
from contextlib import closing
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from starlette.datastructures import UploadFile as StarletteUploadFile

from group_reports import JST, load_version, pdf_sections
from notifications import create_notification
from report_header import group_context_snapshot
from studies import connect_studies, prune_manual_report_versions, study_file_path, validate_pdf
from study_pdf import render_study_pdf
from workspace_frames import (FILTER_ALL, FILTER_NONE, NO_FRAME_KEY, frame_filter, frame_key_filter, frame_label,
                              group_frame_key, has_frame_column, matches_frame)

WEEKDAYS = '月火水木金土日'
TITLE_MAX = 50
OVERALL_COMMENT_MAX = 5000
FIELD_COMMENT_MAX = 2000
RESUBMIT_MAX_DAYS = 365
REMINDER_DAYS = 3
AUTO_SUBMITTED_BY = '自動提出'
REVIEW_CSRF_KEY = 'report_review_csrf'
# 提出日ごとの提出形式：テンプレートで提出／PDFで提出／どちらでも（両方出せる）
SUBMIT_FORMATS = ('template', 'pdf', 'either')
FORMAT_LABELS = {'template': 'テンプレート', 'pdf': 'PDF', 'either': 'どちらでも'}
PDF_MAX_BYTES = 100 * 1024 * 1024
ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024
ATTACHMENT_MAX_COUNT = 10
# 先生がレビューに添付できるファイル（拡張子 → 配信する種類）。PDF・画像・Office
ATTACHMENT_TYPES = {
    '.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
    '.doc': 'application/msword', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xls': 'application/vnd.ms-excel', '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.ppt': 'application/vnd.ms-powerpoint', '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
}
SUBMITTED_SQL = '(version_id IS NOT NULL OR pdf_path IS NOT NULL)'  # テンプレート版かPDFのどちらかが提出されている


# ---------------------------------------------------------------- 共通

def today_jst(now=None):
    return (now or datetime.datetime.now(JST)).astimezone(JST).date()


def utc_text(now=None):
    return (now or datetime.datetime.now(JST)).astimezone(datetime.timezone.utc).isoformat(timespec='seconds')


def day_label(value):
    day = datetime.date.fromisoformat(value)
    return f"{day.month}/{day.day}（{WEEKDAYS[day.weekday()]}）"


def attempt_label(attempt):
    return '本提出' if attempt == 0 else f'再提出{attempt}'


def format_time(value):
    """UTC の ISO 文字列を日本時間の「2026/11/17 12:34」にする。"""
    if not value:
        return ''
    try:
        moment = datetime.datetime.fromisoformat(value)
    except ValueError:
        return value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.astimezone(JST).strftime('%Y/%m/%d %H:%M')


def _table_exists(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def insert_version(db, group_id, school_id, school_name, template_snapshot, report_data, saved_by, saved_at, kind, scheduled_date):
    number = db.execute('SELECT COALESCE(MAX(version_number),0)+1 FROM group_report_version WHERE group_id=?', (group_id,)).fetchone()[0]
    context = json.dumps(group_context_snapshot(db, group_id, school_name), ensure_ascii=False)
    return db.execute("""INSERT INTO group_report_version(group_id,school_id,school_name,version_number,template_snapshot,
        report_snapshot,saved_by,saved_at,kind,scheduled_date,context_snapshot) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (group_id, school_id, school_name, number, template_snapshot, report_data, saved_by, saved_at, kind, scheduled_date, context)).lastrowid


def group_members(db, group_id):
    """通知を送る生徒（ログインID）。退会・脱退したメンバーは除く。"""
    try:
        return [r[0] for r in db.execute('''SELECT i.login_id FROM tekne_chat_member m
            JOIN tekne_student_identity i ON i.id=m.student_id
            WHERE m.group_id=? AND m.left_at IS NULL AND i.login_id IS NOT NULL AND i.deleted_at IS NULL
            ORDER BY m.joined_at,i.login_id''', (group_id,))]
    except sqlite3.OperationalError:
        return []


def notify_group(db, group_id, title, body, notification_type, sender_type='system', sender_name='成果レポート'):
    members = group_members(db, group_id)
    for login_id in members:
        create_notification(db, login_id, title, body, notification_type, 'group_report', None,
                            sender_type=sender_type, sender_name=sender_name)
    return len(members)


def school_groups(db, school, frame=FILTER_ALL):
    """学校の今あるグループと個人スペース。frame で枠を絞り込む（all／none／枠ID）。"""
    if not _table_exists(db, 'tekne_chat_group'):
        return []
    owner = 'COALESCE(st.name,i.login_id)' if _table_exists(db, 'student') else 'i.login_id'
    student_join = 'LEFT JOIN student st ON st.id=i.login_id' if _table_exists(db, 'student') else ''
    with_frame = has_frame_column(db) and _table_exists(db, 'workspace_frame')
    frame_cols = 'g.frame_id,f.name' if with_frame else 'NULL,NULL'
    frame_join = 'LEFT JOIN workspace_frame f ON f.id=g.frame_id AND f.school=s.school_name' if with_frame else ''
    rows = db.execute(f'''SELECT g.id,g.name,COALESCE(g.kind,'group'),{owner},{frame_cols}
        FROM tekne_chat_group g JOIN tekne_school_identity s ON s.id=g.school_id
        LEFT JOIN tekne_student_identity i ON i.id=g.created_by {student_join} {frame_join}
        WHERE s.school_name=? ORDER BY COALESCE(g.kind,'group'),g.name,g.id''', (school,)).fetchall()
    groups = [{'id': r[0], 'name': r[1] or r[0], 'kind': r[2], 'owner': r[3] or '',
               'frame_id': r[4] if r[5] is not None else None, 'frame_name': r[5]} for r in rows]
    return [g for g in groups if matches_frame(g['frame_id'], frame)]


def group_display_name(group):
    if group['kind'] == 'personal':
        return f"個人：{group['owner'] or '不明な生徒'}（{group['name']}）"
    return group['name']


def submission_row(db, group_id, round_day, attempt):
    cursor = db.execute('''SELECT id,school,group_id,round_day,attempt,due_day,version_id,method,submitted_by,submitted_at,finalized_at,round_frame,
        pdf_path,pdf_filename,pdf_submitted_by,pdf_submitted_at
        FROM group_report_submission WHERE group_id=? AND round_day=? AND attempt=?''', (group_id, round_day, attempt))
    row = cursor.fetchone()
    return dict(zip([c[0] for c in cursor.description], row)) if row else None


def submission_by_id(db, submission_id):
    cursor = db.execute('''SELECT s.id,s.school,s.group_id,s.round_day,s.attempt,s.due_day,s.version_id,s.method,s.submitted_by,
        s.submitted_at,s.finalized_at,d.title,s.round_frame,s.pdf_path,s.pdf_filename,s.pdf_submitted_by,s.pdf_submitted_at,
        COALESCE(d.submit_format,'template') AS submit_format FROM group_report_submission s
        LEFT JOIN group_report_submission_day d ON d.school=s.school AND d.frame_id=s.round_frame AND d.day=s.round_day WHERE s.id=?''', (submission_id,))
    row = cursor.fetchone()
    return dict(zip([c[0] for c in cursor.description], row)) if row else None


def is_submitted(sub):
    """テンプレート版かPDFのどちらかが提出されているか。"""
    return bool(sub and (sub['version_id'] is not None or sub['pdf_path']))


def round_format(db, school, frame_key, day):
    row = db.execute('SELECT submit_format FROM group_report_submission_day WHERE school=? AND frame_id=? AND day=?',
                     (school, frame_key, day)).fetchone()
    return row[0] if row else 'template'


def review_returned(db, submission_id):
    row = db.execute('SELECT 1 FROM group_report_review WHERE submission_id=? AND result IS NOT NULL', (submission_id,)).fetchone()
    return row is not None


def review_row(db, submission_id):
    cursor = db.execute('''SELECT overall_comment,field_comments,result,returned_at,returned_by,updated_at,updated_by
        FROM group_report_review WHERE submission_id=?''', (submission_id,))
    row = cursor.fetchone()
    if row is None:
        return None
    review = dict(zip([c[0] for c in cursor.description], row))
    review['field_comments'] = json.loads(review['field_comments'] or '{}')
    review['editors'] = review_editors(db, submission_id)
    return review


def review_editors(db, submission_id):
    return [r[0] for r in db.execute(
        'SELECT teacher_id FROM group_report_review_editor WHERE submission_id=? ORDER BY first_edited_at,teacher_id', (submission_id,))]


def lock_holder(db, submission_id):
    row = db.execute('SELECT teacher_id FROM group_report_review_lock WHERE submission_id=?', (submission_id,)).fetchone()
    return row[0] if row else None


def submission_status(sub, review=None, holder=None, round_finalized=False, submit_format='template'):
    """一覧に出すステータス（key と表示名）。sub は最新の提出（なければ None）。"""
    if not is_submitted(sub):
        if sub is not None and sub['attempt'] > 0:
            if sub['finalized_at'] is None:
                return 'awaiting', f"再提出待ち（期限 {day_label(sub['due_day'])}）"
            return 'no_draft', '再提出なし'
        if round_finalized:
            return ('no_draft', '提出なし' if submit_format == 'pdf' else '未提出（下書きなし）')
        return 'not_submitted', '未提出'
    if sub['finalized_at'] is None:
        return 'early', '再提出済み（期限前）' if sub['attempt'] else '提出済み（期限前）'
    if review and review['result'] == 'returned':
        return 'returned', '返却済み'
    if review and review['result'] == 'resubmit':
        return 'resubmit', '要再提出'
    if review or holder:
        return 'reviewing', 'レビュー中'
    return 'unreviewed', '未レビュー'


def input_status(template_snapshot, report_data):
    """下書きの未入力項目の数（文字の項目は空欄、画像の項目は画像なし、文字と画像は両方とも空）。"""
    try:
        fields = json.loads(template_snapshot).get('fields', [])
        values = json.loads(report_data)
    except (TypeError, ValueError):
        return {'empty': 0, 'total': 0}
    empty = 0
    for field in fields:
        item = values.get(field.get('stable_key'), {}) or {}
        has_text = bool((item.get('value') or '').strip())
        has_images = bool(item.get('images'))
        kind = field.get('field_type', 'text')
        filled = has_text if kind == 'text' else has_images if kind == 'image' else (has_text or has_images)
        empty += 0 if filled else 1
    return {'empty': empty, 'total': len(fields)}


# ---------------------------------------------------------------- 提出日の設定

def submission_days(db, school, frame_key=NO_FRAME_KEY):
    return {r[0]: {'title': r[1], 'finalized': r[2] is not None, 'format': r[3]} for r in db.execute(
        'SELECT day,title,finalized_at,submit_format FROM group_report_submission_day WHERE school=? AND frame_id=? ORDER BY day', (school, frame_key))}


def normalize_submission_titles(submissions, requested_days):
    """{日付: 名前} を検査して整える。提出日は定期自動保存日の中から選ぶ。"""
    if not isinstance(submissions, dict):
        raise HTTPException(422, '提出日の指定が正しくありません。')
    result = {}
    for day, title in submissions.items():
        if day not in requested_days:
            raise HTTPException(422, '提出日は自動保存日の中から選んでください。')
        clean = title.strip() if isinstance(title, str) else ''
        if not clean:
            raise HTTPException(422, '提出日の名前を入力してください。')
        if len(clean) > TITLE_MAX:
            raise HTTPException(422, f'提出日の名前は{TITLE_MAX}文字以内にしてください。')
        result[day] = clean
    return result


def normalize_submission_formats(formats, titles):
    """{日付: 形式} を検査して整える。指定がない提出日はテンプレートでの提出にする。"""
    if formats is None:
        formats = {}
    if not isinstance(formats, dict):
        raise HTTPException(422, '提出形式の指定が正しくありません。')
    result = {}
    for day in titles:
        value = formats.get(day, 'template')
        if value not in SUBMIT_FORMATS:
            raise HTTPException(422, '提出形式はテンプレート・PDF・どちらでも のいずれかを選んでください。')
        result[day] = value
    for day in formats:
        if day not in titles:
            raise HTTPException(422, '提出形式は提出日にだけ指定できます。')
    return result


def remove_submission_day(db, school, frame_key, day):
    """提出がまだない期限前の提出日を解除する（提出があれば外せない）。"""
    where = 'school=? AND round_frame=? AND round_day=?'
    args = (school, frame_key, day)
    if db.execute(f'SELECT 1 FROM group_report_submission WHERE {where} AND {SUBMITTED_SQL} LIMIT 1', args).fetchone():
        raise HTTPException(409, f'{day_label(day)} の提出日は、すでに提出があるため外せません。')
    rows = db.execute(f'SELECT group_id,version_id FROM group_report_submission WHERE {where}', args).fetchall()
    groups = set()
    for group_id, version_id in rows:
        groups.add(group_id)
        if version_id:
            db.execute("UPDATE group_report_version SET kind='manual' WHERE id=? AND kind='submission'", (version_id,))
    for table in ('group_report_review_lock', 'group_report_review_editor', 'group_report_review'):
        db.execute(f'DELETE FROM {table} WHERE submission_id IN (SELECT id FROM group_report_submission WHERE {where})', args)
    db.execute(f'DELETE FROM group_report_submission WHERE {where}', args)
    for group_id in groups | {g['id'] for g in school_groups(db, school, frame_key_filter(frame_key))}:
        db.execute('DELETE FROM group_report_reminder_sent WHERE group_id=? AND round_day=?', (group_id, day))
    for group_id in groups:
        prune_manual_report_versions(db, group_id)
    db.execute('DELETE FROM group_report_submission_day WHERE school=? AND frame_id=? AND day=?', (school, frame_key, day))


def apply_submission_days(db, school, frame_key, today, titles, actor, now, formats=None):
    """枠の明日以降の提出日を titles（{日付: 名前}）と formats（{日付: 提出形式}）に置き換える。今日以前は変更しない。"""
    formats = normalize_submission_formats(formats, titles)
    current = {r[0]: (r[1], r[2]) for r in db.execute(
        'SELECT day,title,submit_format FROM group_report_submission_day WHERE school=? AND frame_id=? AND day>?', (school, frame_key, today.isoformat()))}
    for day in set(current) - set(titles):
        remove_submission_day(db, school, frame_key, day)
    for day, title in titles.items():
        if day in current:
            if current[day][0] != title:
                db.execute('UPDATE group_report_submission_day SET title=? WHERE school=? AND frame_id=? AND day=?', (title, school, frame_key, day))
            if current[day][1] != formats[day]:
                if db.execute(f'SELECT 1 FROM group_report_submission WHERE school=? AND round_frame=? AND round_day=? AND {SUBMITTED_SQL} LIMIT 1',
                              (school, frame_key, day)).fetchone():
                    raise HTTPException(409, f'{day_label(day)} の提出日は、すでに提出があるため提出形式を変更できません。')
                db.execute('UPDATE group_report_submission_day SET submit_format=? WHERE school=? AND frame_id=? AND day=?', (formats[day], school, frame_key, day))
        else:
            db.execute('INSERT INTO group_report_submission_day(school,frame_id,day,title,created_by,created_at,submit_format) VALUES(?,?,?,?,?,?,?)',
                       (school, frame_key, day, title, actor, now, formats[day]))


# ---------------------------------------------------------------- 生徒の提出

def _open_pieces(db, sub, fmt):
    """この提出回でまだ出せるもの（テンプレート版・PDF）。先生が返却したあとは出せない。"""
    if sub and review_returned(db, sub['id']):
        return False, False
    can_template = fmt in ('template', 'either') and not (sub and sub['version_id'] is not None)
    can_pdf = fmt in ('pdf', 'either') and not (sub and sub['pdf_path'])
    return can_template, can_pdf


def _target_dict(sub, round_day, attempt, title, due_day, frame_key, fmt, can_template, can_pdf):
    return {'round_day': round_day, 'attempt': attempt, 'title': title, 'due_day': due_day, 'round_frame': frame_key,
            'due_label': day_label(due_day), 'submitted_at': format_time(sub['submitted_at']) if sub and is_submitted(sub) else None,
            'format': fmt, 'format_label': FORMAT_LABELS[fmt], 'can_template': can_template, 'can_pdf': can_pdf,
            'has_template': bool(sub and sub['version_id'] is not None), 'has_pdf': bool(sub and sub['pdf_path'])}


def open_targets(db, school, group_id, today):
    """今提出できるもの。本提出はいちばん近い提出日だけ。再提出は期限前のものすべて。
    それぞれ、その提出日の提出形式に合わせて「テンプレートで提出」「PDFで提出」のどちらが出せるかを持つ。"""
    targets = []
    frame_key = group_frame_key(db, group_id)  # 本提出は、今所属している枠の提出日
    nearest = db.execute('''SELECT day,title,submit_format FROM group_report_submission_day
        WHERE school=? AND frame_id=? AND day>=? AND finalized_at IS NULL ORDER BY day LIMIT 1''', (school, frame_key, today.isoformat())).fetchone()
    if nearest:
        sub = submission_row(db, group_id, nearest[0], 0)
        can_template, can_pdf = _open_pieces(db, sub, nearest[2])  # 提出済みでも、もう片方（どちらでも）は出せる。先生が返却するまで出し直せない
        if can_template or can_pdf:
            targets.append(_target_dict(sub, nearest[0], 0, nearest[1], nearest[0], frame_key, nearest[2], can_template, can_pdf))
    for (submission_id,) in db.execute('''SELECT id FROM group_report_submission
            WHERE group_id=? AND attempt>0 AND due_day>=? ORDER BY due_day,round_day,attempt''', (group_id, today.isoformat())).fetchall():
        sub = submission_by_id(db, submission_id)
        can_template, can_pdf = _open_pieces(db, sub, sub['submit_format'])
        if can_template or can_pdf:
            targets.append(_target_dict(sub, sub['round_day'], sub['attempt'], sub['title'] or '', sub['due_day'], sub['round_frame'],
                                        sub['submit_format'], can_template, can_pdf))
    return targets


def submit_report(db, school, group_id, student_id, round_day, attempt, now=None):
    """生徒の「提出」（テンプレート）。今の下書きを提出版として保存し、その時点で確定する（先生が返却するまで出し直せない）。"""
    today = today_jst(now)
    if not isinstance(round_day, str) or not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 0:
        raise HTTPException(422, '提出先が正しくありません。')
    target = next((t for t in open_targets(db, school, group_id, today) if t['round_day'] == round_day and t['attempt'] == attempt), None)
    if target is None:
        raise HTTPException(409, 'この提出は受け付けていません。期限が過ぎたか、提出日が変更された可能性があります。')
    if not target['can_template']:
        raise HTTPException(409, 'この提出日はPDFで提出してください。' if target['format'] == 'pdf' else 'テンプレートではすでに提出済みです。')
    draft = db.execute('SELECT school_id,school_name,template_snapshot,report_data FROM group_report_draft WHERE group_id=?', (group_id,)).fetchone()
    if draft is None:
        raise HTTPException(409, 'レポートがまだありません。')
    if db.execute('SELECT 1 FROM group_report_lock WHERE group_id=? AND student_id<>? LIMIT 1', (group_id, student_id)).fetchone():
        raise HTTPException(423, '他のメンバーが項目を編集中です。編集完了後に提出してください。')
    saved_at = utc_text(now)
    version_id = insert_version(db, group_id, draft[0], draft[1], draft[2], draft[3], student_id, saved_at, 'submission', round_day)
    existing = submission_row(db, group_id, round_day, attempt)
    if existing and existing['pdf_path'] and existing['finalized_at']:
        # 先にPDFを提出している（どちらでも）。提出の時刻などはPDFのままにして、テンプレート版だけを足す
        db.execute('UPDATE group_report_submission SET version_id=? WHERE id=?', (version_id, existing['id']))
    elif existing:
        # 再提出の枠（提出待ちの行）に提出する。期限前に枠を変えた場合は、移った先の提出回の提出にする
        db.execute("""UPDATE group_report_submission SET version_id=?,method='manual',submitted_by=?,submitted_at=?,round_frame=?,finalized_at=? WHERE id=?""",
                   (version_id, student_id, saved_at, target['round_frame'], saved_at, existing['id']))
    else:
        db.execute("""INSERT INTO group_report_submission(school,group_id,round_day,attempt,due_day,version_id,method,submitted_by,submitted_at,finalized_at,created_at,round_frame)
            VALUES(?,?,?,?,?,?,'manual',?,?,?,?,?)""", (school, group_id, round_day, attempt, round_day, version_id, student_id, saved_at, saved_at, saved_at,
                                                      target['round_frame']))
    prune_manual_report_versions(db, group_id)
    return {'ok': True, 'submitted_at': format_time(saved_at)}


def pdf_submit_targets(db, school, group_id, today=None):
    """ワークスペースの「PDFを提出」で選べる提出先（PDFを出せるものだけ）。"""
    return [t for t in open_targets(db, school, group_id, today or today_jst()) if t['can_pdf']]


def submit_pdf(db, school, group_id, student_id, round_day, attempt, pdf_path, pdf_filename, now=None):
    """生徒の「PDFを提出」。保存済みのPDFファイルを提出として記録する。先生が返却するまで出し直せない。"""
    today = today_jst(now)
    if not isinstance(round_day, str) or not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 0:
        raise HTTPException(422, '提出先が正しくありません。')
    target = next((t for t in open_targets(db, school, group_id, today) if t['round_day'] == round_day and t['attempt'] == attempt), None)
    if target is None:
        raise HTTPException(409, 'この提出は受け付けていません。期限が過ぎたか、提出日が変更された可能性があります。')
    if not target['can_pdf']:
        raise HTTPException(409, 'この提出日はテンプレートで提出してください。' if target['format'] == 'template' else 'PDFではすでに提出済みです。')
    saved_at = utc_text(now)
    existing = submission_row(db, group_id, round_day, attempt)
    if existing and existing['version_id'] is not None and existing['finalized_at']:
        # 先にテンプレートで提出している（どちらでも）。PDFを足す
        db.execute('UPDATE group_report_submission SET pdf_path=?,pdf_filename=?,pdf_submitted_by=?,pdf_submitted_at=? WHERE id=?',
                   (pdf_path, pdf_filename, student_id, saved_at, existing['id']))
    elif existing:
        db.execute("""UPDATE group_report_submission SET pdf_path=?,pdf_filename=?,pdf_submitted_by=?,pdf_submitted_at=?,
            method='manual',submitted_by=?,submitted_at=?,round_frame=?,finalized_at=? WHERE id=?""",
                   (pdf_path, pdf_filename, student_id, saved_at, student_id, saved_at, target['round_frame'], saved_at, existing['id']))
    else:
        db.execute("""INSERT INTO group_report_submission(school,group_id,round_day,attempt,due_day,method,submitted_by,submitted_at,finalized_at,created_at,
            round_frame,pdf_path,pdf_filename,pdf_submitted_by,pdf_submitted_at) VALUES(?,?,?,?,?,'manual',?,?,?,?,?,?,?,?,?)""",
                   (school, group_id, round_day, attempt, round_day, student_id, saved_at, saved_at, saved_at, target['round_frame'],
                    pdf_path, pdf_filename, student_id, saved_at))
    return {'ok': True, 'submitted_at': format_time(saved_at)}


def student_submission_context(db, school, group_id, today=None):
    """生徒のレポート画面に出す、提出ボタンの対象・提出状況・返却されたレビュー。"""
    today = today or today_jst()
    records = []
    pending_reviews = []  # 「要再提出」で返却され、まだ再提出していないレビュー（記入欄の下に赤文字で出す）
    rows = db.execute('''SELECT s.id FROM group_report_submission s WHERE s.group_id=?
        ORDER BY s.round_day DESC,s.attempt DESC''', (group_id,)).fetchall()
    for (submission_id,) in rows:
        sub = submission_by_id(db, submission_id)
        review = review_row(db, submission_id)
        returned = review if review and review['result'] else None
        if not is_submitted(sub) and sub['finalized_at'] is None and sub['attempt'] == 0:
            continue
        if not is_submitted(sub):
            label = f"再提出待ち（期限 {day_label(sub['due_day'])} 23:59）" if sub['finalized_at'] is None else '提出なし'
        elif sub['finalized_at'] is None:
            label = '提出済み（期限前）'
        elif returned:
            label = '返却済み' if returned['result'] == 'returned' else '要再提出'
        else:
            label = '提出済み（レビュー待ち）'
        item = {'title': sub['title'] or '', 'attempt_label': attempt_label(sub['attempt']), 'status': label,
                'due_label': day_label(sub['due_day']), 'submitted_at': format_time(sub['submitted_at']),
                'method': sub['method'], 'version_id': sub['version_id'], 'feedback': None, 'submission_id': sub['id'],
                'pdf_filename': sub['pdf_filename'] if sub['pdf_path'] else None, 'submit_format': sub['submit_format']}
        if returned and is_submitted(sub):
            version = db.execute('SELECT template_snapshot FROM group_report_version WHERE id=?', (sub['version_id'],)).fetchone() if sub['version_id'] else None
            fields = json.loads(version[0]).get('fields', []) if version else []
            comments = [{'label': f['label'], 'comment': returned['field_comments'][f['stable_key']]}
                        for f in fields if returned['field_comments'].get(f.get('stable_key'))]
            next_row = db.execute('SELECT due_day,version_id FROM group_report_submission WHERE group_id=? AND round_day=? AND attempt=?',
                                  (group_id, sub['round_day'], sub['attempt'] + 1)).fetchone()
            item['feedback'] = {'result': returned['result'], 'overall': returned['overall_comment'], 'fields': comments,
                                'editors': returned['editors'], 'returned_at': format_time(returned['returned_at']),
                                'resubmit_due': day_label(next_row[0]) if next_row else None,
                                'pending': returned['result'] == 'resubmit' and bool(next_row) and next_row[1] is None,
                                'attachments': list_attachments(db, sub['id'])}
            if item['feedback']['pending']:
                # 再提出待ち：記入欄の下に赤文字で出すレビュー（項目のキー → ラベルとコメント）
                pending_reviews.append({
                    'title': sub['title'] or '', 'attempt_label': attempt_label(sub['attempt']), 'overall': returned['overall_comment'],
                    'fields': {f['stable_key']: {'label': f['label'], 'comment': returned['field_comments'][f['stable_key']]}
                               for f in fields if returned['field_comments'].get(f.get('stable_key'))}})
        records.append(item)
    return {'targets': open_targets(db, school, group_id, today), 'records': records, 'pending_reviews': pending_reviews}


def returned_review_ids(db, group_id):
    return [r[0] for r in db.execute('''SELECT s.id FROM group_report_submission s JOIN group_report_review r ON r.submission_id=s.id
        WHERE s.group_id=? AND r.result IS NOT NULL''', (group_id,))]


def mark_reviews_seen(db, group_id, student_id, now=None):
    """生徒がレポート画面を開いたら、そのワークスペースの返却済みレビューを既読にする。"""
    stamp = utc_text(now)
    for submission_id in returned_review_ids(db, group_id):
        db.execute('INSERT OR IGNORE INTO group_report_review_seen(submission_id,student_id,seen_at) VALUES(?,?,?)',
                   (submission_id, student_id, stamp))


def review_notice(db, group_id, student_id):
    """ワークスペースの「レポート記入」の横に出す知らせ。要再提出（再提出前）と、まだ見ていない返却済みレビュー。"""
    resubmit = db.execute('''SELECT due_day FROM group_report_submission
        WHERE group_id=? AND attempt>0 AND version_id IS NULL AND pdf_path IS NULL AND finalized_at IS NULL ORDER BY due_day LIMIT 1''', (group_id,)).fetchone()
    unseen = sum(1 for submission_id in returned_review_ids(db, group_id)
                 if not db.execute('SELECT 1 FROM group_report_review_seen WHERE submission_id=? AND student_id=?',
                                   (submission_id, student_id)).fetchone())
    return {'resubmit_due': day_label(resubmit[0]) if resubmit else None, 'unseen': unseen}


def review_pdf(database_path, image_dir, db, submission_id, school=None, group_id=None):
    """返却済みのレビュー付きPDF（レビューされた提出版の内容＋先生のレビューを赤文字で）。school か group_id で持ち主を確かめる。"""
    sub = submission_by_id(db, submission_id)
    if sub is None or (school is not None and sub['school'] != school) or (group_id is not None and sub['group_id'] != group_id):
        raise HTTPException(404, '提出が見つかりません。')
    review = review_row(db, submission_id)
    if not review or not review['result']:
        raise HTTPException(404, '返却されたレビューがありません。')
    if sub['version_id'] is None:
        raise HTTPException(404, 'PDFで提出されたものには、レビュー付きPDFはありません。')
    version = load_version(db, sub['group_id'], sub['version_id'])
    if version is None:
        raise HTTPException(404, '提出版が見つかりません。')
    sections = pdf_sections(db, image_dir, json.loads(version['template_snapshot']), json.loads(version['report_snapshot']),
                            version['header'], version['header_style'],
                            review={'overall': review['overall_comment'], 'fields': review['field_comments']})
    return render_study_pdf(sections)


# ---------------------------------------------------------------- 期限の処理・リマインド

def _existing_drafts(db, school, frame_key=NO_FRAME_KEY):
    """学校の下書きのうち、今その枠に所属しているワークスペースのもの（削除されたグループは除く）。"""
    columns = 'SELECT d.group_id,d.school_id,d.school_name,d.template_snapshot,d.report_data FROM group_report_draft d WHERE d.school_name=?'
    if not _table_exists(db, 'tekne_chat_group'):
        return [] if frame_key else db.execute(columns + ' ORDER BY d.group_id', (school,)).fetchall()
    if not has_frame_column(db):
        return [] if frame_key else db.execute(
            columns + ' AND EXISTS (SELECT 1 FROM tekne_chat_group g WHERE g.id=d.group_id) ORDER BY d.group_id', (school,)).fetchall()
    return db.execute(columns + ' AND EXISTS (SELECT 1 FROM tekne_chat_group g WHERE g.id=d.group_id AND COALESCE(g.frame_id,0)=?)'
                      ' ORDER BY d.group_id', (school, frame_key)).fetchall()


def run_report_deadlines(database_path, now=None):
    """期限（指定日の23:59）を過ぎた提出をまとめる。テンプレートで提出する提出日は、未提出のグループの下書きを自動で提出する。

    - テンプレートの提出日：その時点の下書きを自動で提出する。
    - PDFの提出日：自動提出はしない。出さなかったワークスペースは「提出なし」になる。
    - どちらでもの提出日：PDFを出していれば自動提出しない。何も出していなければ下書きを自動で提出する。
    本提出は定期自動保存（run_scheduled_snapshots）が済んでから、その日の自動保存版を提出版にする。
    何度実行しても同じ結果になる。
    """
    today = today_jst(now).isoformat()
    saved_at = utc_text(now)
    created = 0
    with closing(connect_studies(database_path)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        for school, frame_key, day, fmt in db.execute('SELECT school,frame_id,day,submit_format FROM group_report_submission_day WHERE finalized_at IS NULL AND day<? ORDER BY day,school,frame_id', (today,)).fetchall():
            snapshot = db.execute('SELECT processed_at FROM group_report_snapshot_day WHERE school=? AND frame_id=? AND day=?', (school, frame_key, day)).fetchone()
            if snapshot is not None and snapshot[0] is None:
                continue  # 定期自動保存がまだなので次の実行でまとめる
            for group_id, school_id, school_name, template_snapshot, report_data in (_existing_drafts(db, school, frame_key) if fmt != 'pdf' else []):
                sub = submission_row(db, group_id, day, 0)
                if sub and (sub['version_id'] or (fmt == 'either' and sub['pdf_path'])):
                    continue
                scheduled = db.execute("SELECT id FROM group_report_version WHERE group_id=? AND kind='scheduled' AND scheduled_date=?", (group_id, day)).fetchone()
                version_id = scheduled[0] if scheduled else insert_version(
                    db, group_id, school_id, school_name, template_snapshot, report_data, AUTO_SUBMITTED_BY, saved_at, 'submission', day)
                if sub:
                    db.execute("UPDATE group_report_submission SET version_id=?,method='auto',submitted_by=?,submitted_at=?,round_frame=? WHERE id=?",
                               (version_id, AUTO_SUBMITTED_BY, saved_at, frame_key, sub['id']))
                else:
                    db.execute("""INSERT INTO group_report_submission(school,group_id,round_day,attempt,due_day,version_id,method,submitted_by,submitted_at,created_at,round_frame)
                        VALUES(?,?,?,0,?,?,'auto',?,?,?,?)""", (school, group_id, day, day, version_id, AUTO_SUBMITTED_BY, saved_at, saved_at, frame_key))
                created += 1
            # 期限前に提出してから枠を変えたワークスペースの提出も、元の枠の提出回としてまとめる
            db.execute('UPDATE group_report_submission SET finalized_at=? WHERE school=? AND round_frame=? AND round_day=? AND attempt=0 AND finalized_at IS NULL',
                       (saved_at, school, frame_key, day))
            db.execute('UPDATE group_report_submission_day SET finalized_at=? WHERE school=? AND frame_id=? AND day=?', (saved_at, school, frame_key, day))
        for sub_id, group_id, round_day, version_id, pdf_path, fmt in db.execute('''SELECT s.id,s.group_id,s.round_day,s.version_id,s.pdf_path,COALESCE(d.submit_format,'template')
                FROM group_report_submission s LEFT JOIN group_report_submission_day d ON d.school=s.school AND d.frame_id=s.round_frame AND d.day=s.round_day
                WHERE s.attempt>0 AND s.finalized_at IS NULL AND s.due_day<?''', (today,)).fetchall():
            if version_id is None and not pdf_path and fmt != 'pdf':
                draft = db.execute('SELECT school_id,school_name,template_snapshot,report_data FROM group_report_draft WHERE group_id=?', (group_id,)).fetchone()
                exists = draft is not None and (not _table_exists(db, 'tekne_chat_group') or
                                                db.execute('SELECT 1 FROM tekne_chat_group WHERE id=?', (group_id,)).fetchone())
                if exists:
                    version_id = insert_version(db, group_id, draft[0], draft[1], draft[2], draft[3], AUTO_SUBMITTED_BY, saved_at, 'submission', round_day)
                    db.execute("UPDATE group_report_submission SET version_id=?,method='auto',submitted_by=?,submitted_at=? WHERE id=?",
                               (version_id, AUTO_SUBMITTED_BY, saved_at, sub_id))
                    created += 1
            db.execute('UPDATE group_report_submission SET finalized_at=? WHERE id=?', (saved_at, sub_id))
    return created


def run_report_reminders(database_path, now=None):
    """期限の3日前と前日に、まだ提出していないグループのメンバーへ通知する（それぞれ1回だけ）。"""
    today = today_jst(now)
    limit = (today + datetime.timedelta(days=REMINDER_DAYS)).isoformat()
    sent_at = utc_text(now)
    sent = 0
    with closing(connect_studies(database_path)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        targets = []
        for school, frame_key, day, title, fmt in db.execute('''SELECT school,frame_id,day,title,submit_format FROM group_report_submission_day
                WHERE finalized_at IS NULL AND day>=? AND day<=? ORDER BY day''', (today.isoformat(), limit)).fetchall():
            for group in school_groups(db, school, frame_key_filter(frame_key)):
                sub = submission_row(db, group['id'], day, 0)
                if is_submitted(sub):
                    continue
                targets.append((group['id'], group['name'], day, 0, day, title, fmt))
        if _table_exists(db, 'tekne_chat_group'):
            for group_id, name, round_day, attempt, due_day, title, fmt in db.execute('''SELECT s.group_id,g.name,s.round_day,s.attempt,s.due_day,COALESCE(d.title,''),
                        COALESCE(d.submit_format,'template')
                    FROM group_report_submission s JOIN tekne_chat_group g ON g.id=s.group_id
                    LEFT JOIN group_report_submission_day d ON d.school=s.school AND d.frame_id=s.round_frame AND d.day=s.round_day
                    WHERE s.attempt>0 AND s.finalized_at IS NULL AND s.version_id IS NULL AND s.pdf_path IS NULL AND s.due_day>=? AND s.due_day<=?''',
                    (today.isoformat(), limit)).fetchall():
                targets.append((group_id, name, round_day, attempt, due_day, title, fmt))
        for group_id, name, round_day, attempt, due_day, title, fmt in targets:
            left = (datetime.date.fromisoformat(due_day) - today).days
            offset = 1 if left <= 1 else REMINDER_DAYS
            if db.execute('SELECT 1 FROM group_report_reminder_sent WHERE group_id=? AND round_day=? AND attempt=? AND offset_days=?',
                          (group_id, round_day, attempt, offset)).fetchone():
                continue
            db.execute('INSERT INTO group_report_reminder_sent(group_id,round_day,attempt,offset_days,sent_at) VALUES(?,?,?,?,?)',
                       (group_id, round_day, attempt, offset, sent_at))
            kind = '再提出' if attempt else '提出'
            head = f"「{title}」の{kind}期限は {day_label(due_day)} 23:59 です。\nグループ：{name}\n"
            if fmt == 'pdf':
                body = (head + "この提出はPDFで行います。期限までに、ワークスペースの「PDFを提出」ボタンからPDFを提出してください"
                        "（提出すると、先生が返却するまで出し直せません）。期限までに提出しなかった場合は「提出なし」になります。")
            elif fmt == 'either':
                body = (head + "PDFでもテンプレートでも提出できます。PDFはワークスペースの「PDFを提出」ボタン、テンプレートは成果レポート画面の「提出」ボタンから提出してください"
                        "（提出すると、先生が返却するまで出し直せません）。期限までにPDFを出さなかった場合は、その時点のテンプレートの内容が自動で提出されます。")
            else:
                body = (head + "期限までは成果レポート画面の「提出」ボタンで提出できます（提出すると、先生が返却するまで出し直せません）。"
                        "期限を過ぎると、その時点の内容が自動で提出されます。")
            notify_group(db, group_id, f'成果レポートの{kind}期限が近づいています', body, 'report_reminder')
            sent += 1
    return sent


# ---------------------------------------------------------------- 先生のレビュー

def _check_comments(field_comments, template):
    keys = {f.get('stable_key') for f in template.get('fields', [])}
    if not isinstance(field_comments, dict):
        raise HTTPException(422, '項目別コメントが正しくありません。')
    clean = {}
    for key, value in field_comments.items():
        if key not in keys or not isinstance(value, str):
            raise HTTPException(422, '項目別コメントが正しくありません。')
        if len(value) > FIELD_COMMENT_MAX:
            raise HTTPException(422, f'項目別コメントは{FIELD_COMMENT_MAX}文字以内にしてください。')
        if value.strip():
            clean[key] = value.strip()
    return clean


def _reviewable(db, submission_id, school):
    sub = submission_by_id(db, submission_id)
    if sub is None or sub['school'] != school:
        raise HTTPException(404, '提出が見つかりません。')
    if not is_submitted(sub) or sub['finalized_at'] is None:
        raise HTTPException(409, '提出されたものだけレビューできます。')
    return sub


def acquire_review_lock(db, submission_id, school, teacher_id, now=None):
    _reviewable(db, submission_id, school)
    review = review_row(db, submission_id)
    if review and review['result']:
        raise HTTPException(409, '返却済みのレビューは編集できません。')
    db.execute('INSERT OR IGNORE INTO group_report_review_lock(submission_id,teacher_id,locked_at) VALUES(?,?,?)',
               (submission_id, teacher_id, utc_text(now)))
    holder = lock_holder(db, submission_id)
    if holder != teacher_id:
        raise HTTPException(423, f'{holder} 先生がレビュー中です。')
    return holder


def release_review_lock(db, submission_id, school, teacher_id):
    _reviewable(db, submission_id, school)
    db.execute('DELETE FROM group_report_review_lock WHERE submission_id=? AND teacher_id=?', (submission_id, teacher_id))


def force_release_review_lock(db, submission_id, school):
    _reviewable(db, submission_id, school)
    holder = lock_holder(db, submission_id)
    db.execute('DELETE FROM group_report_review_lock WHERE submission_id=?', (submission_id,))
    return holder


def save_review(db, submission_id, school, teacher_id, overall_comment, field_comments, now=None):
    sub = _reviewable(db, submission_id, school)
    holder = lock_holder(db, submission_id)
    if holder != teacher_id:
        raise HTTPException(423, f'{holder} 先生がレビュー中です。' if holder else 'レビューを開始してから保存してください。')
    review = review_row(db, submission_id)
    if review and review['result']:
        raise HTTPException(409, '返却済みのレビューは編集できません。')
    if not isinstance(overall_comment, str) or len(overall_comment) > OVERALL_COMMENT_MAX:
        raise HTTPException(422, f'全体コメントは{OVERALL_COMMENT_MAX}文字以内にしてください。')
    # PDFだけで提出されたものには項目がないので、項目別コメントは付けられない
    template = json.loads(db.execute('SELECT template_snapshot FROM group_report_version WHERE id=?', (sub['version_id'],)).fetchone()[0]) if sub['version_id'] else {'fields': []}
    comments = _check_comments(field_comments, template)
    if review is None and not overall_comment.strip() and not comments:
        return sub  # 何も書いていなければレビューを作らない（未レビューのまま）
    stamp = utc_text(now)
    db.execute('''INSERT INTO group_report_review(submission_id,overall_comment,field_comments,created_at,updated_at,updated_by)
        VALUES(?,?,?,?,?,?) ON CONFLICT(submission_id) DO UPDATE SET overall_comment=excluded.overall_comment,
        field_comments=excluded.field_comments,updated_at=excluded.updated_at,updated_by=excluded.updated_by''',
               (submission_id, overall_comment.strip(), json.dumps(comments, ensure_ascii=False), stamp, stamp, teacher_id))
    db.execute('''INSERT INTO group_report_review_editor(submission_id,teacher_id,first_edited_at,last_edited_at) VALUES(?,?,?,?)
        ON CONFLICT(submission_id,teacher_id) DO UPDATE SET last_edited_at=excluded.last_edited_at''', (submission_id, teacher_id, stamp, stamp))
    return sub


def return_review(db, submission_id, school, teacher_id, overall_comment, field_comments, result, due_day=None, now=None):
    """レビューを返却して生徒に公開する。返却後は編集できない。要再提出なら再提出期限つきの提出を作る。"""
    if result not in ('returned', 'resubmit'):
        raise HTTPException(422, '返却の種類が正しくありません。')
    today = today_jst(now)
    if result == 'resubmit':
        try:
            due = datetime.date.fromisoformat(due_day) if isinstance(due_day, str) else None
        except ValueError:
            due = None
        if due is None or due.isoformat() != due_day:
            raise HTTPException(422, '再提出期限を正しく入力してください。')
        if due < today:
            raise HTTPException(422, '再提出期限は今日以降の日付にしてください。')
        if due > today + datetime.timedelta(days=RESUBMIT_MAX_DAYS):
            raise HTTPException(422, '再提出期限は1年以内の日付にしてください。')
    sub = save_review(db, submission_id, school, teacher_id, overall_comment, field_comments, now)
    stamp = utc_text(now)
    # コメントなしで返却したときもレビューの行と編集者を残す
    db.execute('INSERT OR IGNORE INTO group_report_review(submission_id,created_at,updated_at,updated_by) VALUES(?,?,?,?)',
               (submission_id, stamp, stamp, teacher_id))
    db.execute('''INSERT INTO group_report_review_editor(submission_id,teacher_id,first_edited_at,last_edited_at) VALUES(?,?,?,?)
        ON CONFLICT(submission_id,teacher_id) DO UPDATE SET last_edited_at=excluded.last_edited_at''', (submission_id, teacher_id, stamp, stamp))
    db.execute('UPDATE group_report_review SET result=?,returned_at=?,returned_by=? WHERE submission_id=?', (result, stamp, teacher_id, submission_id))
    db.execute('DELETE FROM group_report_review_lock WHERE submission_id=?', (submission_id,))
    group = db.execute('SELECT name FROM tekne_chat_group WHERE id=?', (sub['group_id'],)).fetchone() if _table_exists(db, 'tekne_chat_group') else None
    group_name = group[0] if group else sub['group_id']
    title = sub['title'] or day_label(sub['round_day'])
    if result == 'resubmit':
        # 再提出は、もとの提出と同じ枠の提出回に入れる（枠を指定しないと「枠なし」になり、先生の一覧に出ない）
        db.execute("""INSERT INTO group_report_submission(school,group_id,round_day,attempt,due_day,created_at,round_frame) VALUES(?,?,?,?,?,?,?)""",
                   (sub['school'], sub['group_id'], sub['round_day'], sub['attempt'] + 1, due_day, stamp, sub['round_frame']))
        body = (f"「{title}」の{attempt_label(sub['attempt'])}が先生から返却されました。再提出が必要です。\n"
                f"グループ：{group_name}\n再提出期限：{day_label(due_day)} 23:59\n成果レポート画面でフィードバックを確認してください。")
        notify_group(db, sub['group_id'], '成果レポートの再提出をお願いします', body, 'report_resubmit', 'teacher', teacher_id)
    else:
        body = (f"「{title}」の{attempt_label(sub['attempt'])}に先生からフィードバックが届きました。\n"
                f"グループ：{group_name}\n成果レポート画面で確認してください。")
        notify_group(db, sub['group_id'], '成果レポートが返却されました', body, 'report_returned', 'teacher', teacher_id)
    return sub


# ---------------------------------------------------------------- レビューへの添付ファイル

def size_label(size):
    return f'{size / 1024 / 1024:.1f}MB' if size >= 1024 * 1024 else f'{max(1, round(size / 1024))}KB'


def list_attachments(db, submission_id):
    return [{'id': r[0], 'filename': r[1], 'size': r[2], 'size_label': size_label(r[2]), 'uploaded_by': r[3]} for r in db.execute(
        'SELECT id,filename,size,uploaded_by FROM group_report_review_attachment WHERE submission_id=? ORDER BY id', (submission_id,))]


def attachment_type(filename, head):
    """拡張子と、ファイルの先頭のバイト列から、添付できるファイルかを確かめて配信する種類を返す。"""
    ext = Path(filename).suffix.lower()
    if ext not in ATTACHMENT_TYPES:
        raise HTTPException(422, '添付できるのは、PDF・画像（PNG/JPEG/GIF）・Word/Excel/PowerPointのファイルです。')
    ole = head.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1')
    signatures = {
        '.pdf': head.startswith(b'%PDF-'), '.png': head.startswith(b'\x89PNG\r\n\x1a\n'),
        '.jpg': head.startswith(b'\xff\xd8\xff'), '.jpeg': head.startswith(b'\xff\xd8\xff'),
        '.gif': head[:6] in (b'GIF87a', b'GIF89a'),
        '.doc': ole, '.xls': ole, '.ppt': ole,
        '.docx': head.startswith(b'PK'), '.xlsx': head.startswith(b'PK'), '.pptx': head.startswith(b'PK'),
    }
    if not signatures[ext]:
        raise HTTPException(422, 'ファイルの中身が拡張子と合っていません。正しいファイルを選んでください。')
    return ATTACHMENT_TYPES[ext]


def clean_filename(value):
    name = (value or '').replace('\\', '/').rsplit('/', 1)[-1]
    return ''.join(char for char in name if ord(char) >= 32 and ord(char) != 127).strip()


def attachment_guard(db, submission_id, school, teacher_id):
    """添付の追加・削除ができるのは、レビューを開始（ロック）した先生が、返却する前に行うときだけ。"""
    sub = _reviewable(db, submission_id, school)
    holder = lock_holder(db, submission_id)
    if holder != teacher_id:
        raise HTTPException(423, f'{holder} 先生がレビュー中です。' if holder else 'レビューを開始してから添付してください。')
    if review_returned(db, submission_id):
        raise HTTPException(409, '返却済みのレビューは編集できません。')
    return sub


def add_review_attachment(db, attach_dir, submission_id, school, teacher_id, upload, now=None):
    from uuid import uuid4
    attachment_guard(db, submission_id, school, teacher_id)
    if db.execute('SELECT COUNT(*) FROM group_report_review_attachment WHERE submission_id=?', (submission_id,)).fetchone()[0] >= ATTACHMENT_MAX_COUNT:
        raise HTTPException(422, f'添付できるのは1つのレビューにつき{ATTACHMENT_MAX_COUNT}件までです。')
    filename = clean_filename(getattr(upload, 'filename', ''))
    if not filename:
        raise HTTPException(422, 'ファイル名を確認してください。')
    upload.file.seek(0, 2)
    size = upload.file.tell()
    upload.file.seek(0)
    if size == 0:
        raise HTTPException(422, '空のファイルは添付できません。')
    if size > ATTACHMENT_MAX_BYTES:
        raise HTTPException(413, f'添付できるのは1件{ATTACHMENT_MAX_BYTES // 1024 // 1024}MBまでです。')
    content_type = attachment_type(filename, upload.file.read(16))
    upload.file.seek(0)
    attach_dir.mkdir(parents=True, exist_ok=True)
    stored = uuid4().hex + Path(filename).suffix.lower()
    final = study_file_path(attach_dir, stored)
    temporary = final.with_suffix('.tmp')
    try:
        with temporary.open('xb') as output:
            import shutil
            shutil.copyfileobj(upload.file, output)
        temporary.replace(final)
        attachment_id = db.execute("""INSERT INTO group_report_review_attachment(submission_id,filename,stored_name,content_type,size,uploaded_by,uploaded_at)
            VALUES(?,?,?,?,?,?,?)""", (submission_id, filename, stored, content_type, size, teacher_id, utc_text(now))).lastrowid
    except Exception:
        temporary.unlink(missing_ok=True)
        final.unlink(missing_ok=True)
        raise
    return {'id': attachment_id, 'filename': filename, 'size': size, 'size_label': size_label(size), 'uploaded_by': teacher_id}


def delete_review_attachment(db, attach_dir, submission_id, attachment_id, school, teacher_id):
    attachment_guard(db, submission_id, school, teacher_id)
    row = db.execute('SELECT stored_name FROM group_report_review_attachment WHERE id=? AND submission_id=?', (attachment_id, submission_id)).fetchone()
    if row is None:
        raise HTTPException(404, '添付ファイルが見つかりません。')
    db.execute('DELETE FROM group_report_review_attachment WHERE id=?', (attachment_id,))
    study_file_path(attach_dir, row[0]).unlink(missing_ok=True)


# ---------------------------------------------------------------- 先生の一覧

def round_summaries(db, school, today, frame=FILTER_ALL):
    """提出回（枠＋提出日）の一覧。frame で枠を絞り込む（all＝すべての枠の提出回を枠名付きで並べる）。"""
    where, params = 'school=?', [school]
    if frame == FILTER_NONE:
        where += ' AND frame_id=0'
    elif frame != FILTER_ALL:
        where += ' AND frame_id=?'
        params.append(int(frame))
    rounds = []
    for frame_key, day, title, finalized, fmt in db.execute(
            f'SELECT frame_id,day,title,finalized_at,submit_format FROM group_report_submission_day WHERE {where} ORDER BY day DESC,frame_id', params).fetchall():
        detail = round_groups(db, school, frame_key, day, finalized is not None)
        counts = {}
        for item in detail:
            counts[item['status']] = counts.get(item['status'], 0) + 1
        phase = '受付中' if finalized is None and day >= today.isoformat() else '締切処理中' if finalized is None else '締切済み'
        rounds.append({'frame_key': frame_key, 'frame_label': frame_label(db, school, frame_key), 'day': day, 'label': day_label(day),
                       'title': title, 'phase': phase, 'finalized': finalized is not None, 'total': len(detail), 'counts': counts,
                       'format': fmt, 'format_label': FORMAT_LABELS.get(fmt, '')})
    return rounds


def round_group_list(db, school, frame_key, day):
    """提出回の対象：今その枠に所属しているワークスペースと、その提出回に提出が残っているワークスペース（あとで枠を変えたもの）。"""
    submitted = {r[0] for r in db.execute('SELECT DISTINCT group_id FROM group_report_submission WHERE school=? AND round_frame=? AND round_day=?',
                                          (school, frame_key, day))}
    return [g for g in school_groups(db, school)
            if g['id'] in submitted or matches_frame(g['frame_id'], frame_key_filter(frame_key))]


def round_groups(db, school, frame_key, day, round_finalized):
    """提出回のワークスペースごとの状況（削除されたグループは出さない）。"""
    result = []
    fmt = round_format(db, school, frame_key, day)
    for group in round_group_list(db, school, frame_key, day):
        attempts = []
        for (submission_id,) in db.execute('SELECT id FROM group_report_submission WHERE group_id=? AND round_frame=? AND round_day=? ORDER BY attempt',
                                           (group['id'], frame_key, day)):
            sub = submission_by_id(db, submission_id)
            review = review_row(db, submission_id)
            holder = lock_holder(db, submission_id)
            key, label = submission_status(sub, review, holder, round_finalized, fmt)
            attempts.append({**sub, 'status': key, 'status_label': label, 'lock_holder': holder,
                             'attempt_label': attempt_label(sub['attempt']), 'submitted_label': format_time(sub['submitted_at']),
                             'has_pdf': bool(sub['pdf_path']), 'has_template': sub['version_id'] is not None,
                             'reviewable': is_submitted(sub) and sub['finalized_at'] is not None})
        latest = attempts[-1] if attempts else None
        if latest:
            status, label = latest['status'], latest['status_label']
        else:
            status, label = submission_status(None, round_finalized=round_finalized, submit_format=fmt)
        draft = db.execute('SELECT template_snapshot,report_data,updated_at FROM group_report_draft WHERE group_id=?', (group['id'],)).fetchone()
        progress = {**input_status(draft[0], draft[1]), 'updated_at': format_time(draft[2])} if draft else None
        moved = not matches_frame(group['frame_id'], frame_key_filter(frame_key))
        result.append({**group, 'display_name': group_display_name(group), 'attempts': attempts, 'status': status,
                       'status_label': label, 'progress': progress, 'moved': moved})
    return result


def safe_filename(value):
    return re.sub(r'[\\/:*?"<>|\r\n\t]+', '_', value).strip(' .') or 'report'


def build_round_zip(db, database_path, image_dir, school, frame_key, day, pdf_dir=None):
    """提出回の各グループの最新の提出（再提出があればそれ）を PDF にして ZIP にまとめる。PDFで提出されたものはそのPDFを入れる。"""
    buffer = BytesIO()
    count = 0
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for index, group in enumerate(round_group_list(db, school, frame_key, day), 1):
            row = db.execute(f'''SELECT attempt,version_id,pdf_path FROM group_report_submission WHERE group_id=? AND round_frame=? AND round_day=?
                AND {SUBMITTED_SQL} AND finalized_at IS NOT NULL ORDER BY attempt DESC LIMIT 1''', (group['id'], frame_key, day)).fetchone()
            if not row:
                continue
            base = safe_filename(f"{index:02d}_{group_display_name(group)}_{attempt_label(row[0])}")
            version = load_version(db, group['id'], row[1]) if row[1] is not None else None
            if version:
                sections = pdf_sections(db, image_dir, json.loads(version['template_snapshot']), json.loads(version['report_snapshot']),
                                        version['header'], version['header_style'])
                archive.writestr(base + '.pdf', render_study_pdf(sections))
                count += 1
            if row[2] and pdf_dir is not None:
                try:
                    uploaded = study_file_path(pdf_dir, row[2])
                    if uploaded.is_file():
                        archive.write(uploaded, base + ('_提出PDF.pdf' if version else '.pdf'))
                        count += 1
                except ValueError:
                    pass
    return buffer.getvalue(), count


# ---------------------------------------------------------------- ルーター

def build_report_review_router(database_path, templates, chat_service, image_dir, file_dir=None):
    router = APIRouter()
    image_dir = Path(image_dir)
    file_dir = Path(file_dir) if file_dir else image_dir.parent
    pdf_dir = file_dir / 'report-submissions'  # 生徒が提出したPDF
    attach_dir = file_dir / 'review-attachments'  # 先生がレビューに添付したファイル

    def teacher_identity(request):
        if request.session.get('teacher_login') is not True:
            raise HTTPException(403, '先生としてログインしてください。')
        teacher_id = request.session.get('teacher_id')
        with closing(connect_studies(database_path)) as db:
            row = db.execute('SELECT school,role FROM teacher WHERE id=?', (teacher_id,)).fetchone()
        if row is None:
            raise HTTPException(403, '先生アカウントが確認できません。')
        return teacher_id, row[0], row[1]

    def teacher_csrf(request, value):
        expected = request.session.get(REVIEW_CSRF_KEY, '')
        if not expected or not isinstance(value, str) or not secrets.compare_digest(expected, value):
            raise HTTPException(403, '画面を再読み込みしてください。')

    def base_context(request, teacher_id, school, role):
        return {'request': request, 'school': school, 'teacher_id': teacher_id, 'teacher_role': role,
                'csrf_token': request.session.setdefault(REVIEW_CSRF_KEY, secrets.token_urlsafe(32))}

    def student_group(session, group_id):
        if session.get('user_login') is not True or not session.get('user_id'):
            raise HTTPException(401, '生徒としてログインしてください。')
        group = next((g for g in chat_service.groups(session) if g['id'] == group_id), None)
        if group is None:
            raise HTTPException(403, '所属グループのレポートのみ利用できます。')
        with closing(connect_studies(database_path)) as db:
            identity = db.execute('SELECT s.school_name FROM tekne_chat_group g JOIN tekne_school_identity s ON s.id=g.school_id WHERE g.id=?', (group_id,)).fetchone()
        if identity is None:
            raise HTTPException(404, 'グループが見つかりません。')
        return session['user_id'], identity[0]

    # ---- 生徒
    @router.post('/api/group-reports/{group_id}/submit')
    async def student_submit(request: Request, group_id: str):
        user, school = student_group(request.session, group_id)
        body = await request.json()
        expected = request.session.get('tekne_chat_csrf', '')
        token = body.get('csrf_token') if isinstance(body, dict) else None
        if not expected or not isinstance(token, str) or not secrets.compare_digest(expected, token):
            raise HTTPException(403, '画面を再読み込みしてください。')
        with closing(connect_studies(database_path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            return submit_report(db, school, group_id, user, body.get('round_day'), body.get('attempt'))

    def student_csrf(request, value):
        expected = request.session.get('tekne_chat_csrf', '')
        if not expected or not isinstance(value, str) or not secrets.compare_digest(expected, value):
            raise HTTPException(403, '画面を再読み込みしてください。')

    def file_response(path, filename, media_type, disposition):
        return FileResponse(path, media_type=media_type, filename=filename, content_disposition_type=disposition,
                            headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})

    @router.get('/api/group-reports/{group_id}/pdf-targets')
    async def student_pdf_targets(request: Request, group_id: str):
        _, school = student_group(request.session, group_id)
        with closing(connect_studies(database_path)) as db:
            return {'targets': pdf_submit_targets(db, school, group_id)}

    @router.post('/api/group-reports/{group_id}/submit-pdf')
    async def student_submit_pdf(request: Request, group_id: str):
        from uuid import uuid4
        user, school = student_group(request.session, group_id)
        temporary = final = None
        try:
            async with request.form(max_part_size=2**63 - 1) as form:
                def text(key):
                    value = form.get(key, '')
                    if not isinstance(value, str) or len(form.getlist(key)) > 1:
                        raise HTTPException(422, '入力形式が不正です。')
                    return value
                student_csrf(request, text('csrf_token'))
                try:
                    attempt = int(text('attempt'))
                except ValueError:
                    raise HTTPException(422, '提出先が正しくありません。')
                upload = form.get('pdf')
                if not isinstance(upload, StarletteUploadFile) or len(form.getlist('pdf')) != 1:
                    raise HTTPException(422, 'PDFファイルを1つ選択してください。')
                filename = clean_filename(upload.filename)
                if not filename:
                    raise HTTPException(422, 'ファイル名を確認してください。')
                upload.file.seek(0, 2)
                size = upload.file.tell()
                upload.file.seek(0)
                if size > PDF_MAX_BYTES:
                    raise HTTPException(413, 'ファイルは100MB以内にしてください。')
                try:
                    validate_pdf(upload)
                except ValueError as error:
                    raise HTTPException(422, str(error))
                pdf_dir.mkdir(parents=True, exist_ok=True)
                stored = uuid4().hex + '.pdf'
                final = study_file_path(pdf_dir, stored)
                temporary = final.with_suffix('.tmp')
                with temporary.open('xb') as output:
                    shutil.copyfileobj(upload.file, output)
                temporary.replace(final)
                with closing(connect_studies(database_path)) as db, db:
                    db.execute('BEGIN IMMEDIATE')
                    return submit_pdf(db, school, group_id, user, text('round_day'), attempt, stored, filename)
        except Exception:
            for path in (temporary, final):
                if path is not None:
                    path.unlink(missing_ok=True)
            raise

    def sub_for_group(db, group_id, submission_id):
        sub = submission_by_id(db, submission_id)
        if sub is None or sub['group_id'] != group_id:
            raise HTTPException(404, '提出が見つかりません。')
        return sub

    @router.get('/api/group-reports/{group_id}/submissions/{submission_id}/submitted.pdf')
    async def student_submitted_pdf(request: Request, group_id: str, submission_id: int):
        student_group(request.session, group_id)
        with closing(connect_studies(database_path)) as db:
            sub = sub_for_group(db, group_id, submission_id)
        if not sub['pdf_path']:
            raise HTTPException(404, '提出されたPDFがありません。')
        path = study_file_path(pdf_dir, sub['pdf_path'])
        if not path.is_file():
            raise HTTPException(404, 'PDFファイルが見つかりません。')
        return file_response(path, sub['pdf_filename'] or 'report.pdf', 'application/pdf', 'inline')

    @router.get('/api/group-reports/{group_id}/review-attachments/{attachment_id}')
    async def student_review_attachment(request: Request, group_id: str, attachment_id: int):
        student_group(request.session, group_id)
        with closing(connect_studies(database_path)) as db:
            row = db.execute('SELECT submission_id,filename,stored_name,content_type FROM group_report_review_attachment WHERE id=?', (attachment_id,)).fetchone()
            sub = sub_for_group(db, group_id, row[0]) if row else None
            returned = bool(sub) and review_returned(db, sub['id'])
        if row is None or not returned:
            raise HTTPException(404, '添付ファイルが見つかりません。')  # 返却前のレビューは生徒に見えない
        path = study_file_path(attach_dir, row[2])
        if not path.is_file():
            raise HTTPException(404, '添付ファイルが見つかりません。')
        return file_response(path, row[1], row[3], 'attachment')

    # ---- 先生
    @router.get('/teacher/report-reviews')
    async def rounds_page(request: Request, frame: str = FILTER_ALL):
        # 一覧は「成果レポート管理」の提出物確認タブに統合した。旧URLは枠の指定を保ったまま転送する
        school = teacher_identity(request)[1]
        with closing(connect_studies(database_path)) as db:
            query = frame_filter(db, school, frame)['query']
        return RedirectResponse('/teacher/reports?tab=submissions' + query.replace('?', '&'), status_code=307)

    def round_info(db, school, frame_key, day):
        info = db.execute('SELECT title,finalized_at,submit_format FROM group_report_submission_day WHERE school=? AND frame_id=? AND day=?',
                          (school, frame_key, day)).fetchone()
        if info is None:
            raise HTTPException(404, '提出日が見つかりません。')
        return info

    @router.get('/teacher/report-reviews/rounds/{frame_key}/{day}')
    async def round_page(request: Request, frame_key: int, day: str, frame: str = FILTER_ALL):
        teacher_id, school, role = teacher_identity(request)
        with closing(connect_studies(database_path)) as db:
            info = round_info(db, school, frame_key, day)
            groups = round_groups(db, school, frame_key, day, info[1] is not None)
            label = frame_label(db, school, frame_key)
            back_query = frame_filter(db, school, frame)['query']  # 一覧に戻るときの枠の絞り込み
            from report_archive import archived_ids, round_archive_status  # 循環importを避ける
            archived = archived_ids(db, school, [a['id'] for g in groups for a in g['attempts']])
            archive_status = round_archive_status(db, school, frame_key, day)
        today = today_jst().isoformat()
        phase = '受付中' if info[1] is None and day >= today else '締切処理中' if info[1] is None else '締切済み'
        return templates.TemplateResponse(request=request, name='teacher/report_review_round.html', context={
            **base_context(request, teacher_id, school, role), 'day': day, 'day_label': day_label(day), 'title': info[0],
            'frame_key': frame_key, 'frame_label': label, 'back_query': back_query, 'format_label': FORMAT_LABELS.get(info[2], ''),
            'finalized': info[1] is not None, 'phase': phase, 'groups': groups,
            'downloadable': any(a['reviewable'] for g in groups for a in g['attempts']),
            'can_archive': role == 'super_teacher', 'archived': archived, 'archive_status': archive_status,
            'archive_csrf': request.session.setdefault('report_archive_csrf', secrets.token_urlsafe(32))})

    @router.get('/teacher/report-reviews/rounds/{frame_key}/{day}/download.zip')
    async def round_zip(request: Request, frame_key: int, day: str):
        _, school, _ = teacher_identity(request)

        def build():
            with closing(connect_studies(database_path)) as db:
                info = round_info(db, school, frame_key, day)
                data, count = build_round_zip(db, database_path, image_dir, school, frame_key, day, pdf_dir)
                return data, count, f"{info[0]}_{frame_label(db, school, frame_key)}"
        data, count, title = await asyncio.to_thread(build)
        if count == 0:
            raise HTTPException(404, 'ダウンロードできる提出版がありません。')
        filename = quote(safe_filename(f'{title}_{day}') + '.zip')
        return Response(data, media_type='application/zip',
                        headers={'Content-Disposition': f"attachment; filename=\"report-{day}.zip\"; filename*=UTF-8''{filename}"})

    @router.get('/teacher/report-reviews/submissions/{submission_id}')
    async def review_page(request: Request, submission_id: int, frame: str = FILTER_ALL):
        teacher_id, school, role = teacher_identity(request)
        with closing(connect_studies(database_path)) as db:
            back_query = frame_filter(db, school, frame)['query']
            sub = submission_by_id(db, submission_id)
            if sub is None or sub['school'] != school:
                raise HTTPException(404, '提出が見つかりません。')
            if not is_submitted(sub):
                raise HTTPException(404, '提出がありません。')
            version = load_version(db, sub['group_id'], sub['version_id']) if sub['version_id'] is not None else None
            if sub['version_id'] is not None and version is None:
                raise HTTPException(404, '提出版が見つかりません。')
            group = next((g for g in school_groups(db, school) if g['id'] == sub['group_id']), None)
            if group is None:
                raise HTTPException(404, 'グループが見つかりません。')
            template = json.loads(version['template_snapshot']) if version else {'fields': []}
            values = json.loads(version['report_snapshot']) if version else {}
            attachments = list_attachments(db, submission_id)
            review = review_row(db, submission_id)
            holder = lock_holder(db, submission_id)
            previous = []
            for (prev_id,) in db.execute('SELECT id FROM group_report_submission WHERE group_id=? AND round_day=? AND attempt<? ORDER BY attempt',
                                         (sub['group_id'], sub['round_day'], sub['attempt'])):
                prev_sub = submission_by_id(db, prev_id)
                prev_review = review_row(db, prev_id)
                if prev_review and prev_review['result']:
                    labels = {f['stable_key']: f['label'] for f in template.get('fields', [])}
                    previous.append({'id': prev_id, 'attempt_label': attempt_label(prev_sub['attempt']), 'review': prev_review,
                                     'returned_at': format_time(prev_review['returned_at']), 'labels': labels})
            key, label = submission_status(sub, review, holder, True)
            round_frame_label = frame_label(db, school, sub['round_frame'])
        fields = []
        for field in template.get('fields', []):
            item = values.get(field.get('stable_key'), {}) or {}
            fields.append({'key': field.get('stable_key'), 'label': field.get('label', ''), 'type': field.get('field_type', 'text'),
                           'value': item.get('value', ''),
                           'images': [{'url': f"/teacher/reports/{quote(sub['group_id'])}/assets/{i.get('content_hash')}", 'caption': i.get('caption', '')}
                                      for i in item.get('images', []) if isinstance(i, dict) and i.get('content_hash')],
                           'comment': (review or {}).get('field_comments', {}).get(field.get('stable_key'), '')})
        return templates.TemplateResponse(request=request, name='teacher/report_review.html', context={
            **base_context(request, teacher_id, school, role), 'sub': sub, 'group': group, 'group_name': group_display_name(group),
            'title': sub['title'] or day_label(sub['round_day']), 'attempt_label': attempt_label(sub['attempt']),
            'day_label': day_label(sub['round_day']), 'submitted_at': format_time(sub['submitted_at']),
            'reviewable': sub['finalized_at'] is not None, 'review': review, 'lock_holder': holder, 'status': key, 'status_label': label,
            'fields': fields, 'previous': previous, 'returned_at': format_time(review['returned_at']) if review else '',
            'pdf_url': f"/teacher/reports/{quote(sub['group_id'])}/versions/{sub['version_id']}.pdf" if version else None,
            'submitted_pdf_url': f"/teacher/report-reviews/submissions/{submission_id}/submitted.pdf" if sub['pdf_path'] else None,
            'submitted_pdf_name': sub['pdf_filename'], 'attachments': attachments, 'attachment_max': ATTACHMENT_MAX_COUNT,
            'attachment_max_mb': ATTACHMENT_MAX_BYTES // 1024 // 1024, 'submit_format_label': FORMAT_LABELS.get(sub['submit_format'], ''),
            'review_pdf_url': f"/teacher/report-reviews/submissions/{submission_id}/review.pdf" if review and review['result'] else None,
            'today': today_jst().isoformat(), 'max_due': (today_jst() + datetime.timedelta(days=RESUBMIT_MAX_DAYS)).isoformat(),
            'overall_max': OVERALL_COMMENT_MAX, 'field_max': FIELD_COMMENT_MAX, 'back_query': back_query,
            'round_frame_label': round_frame_label})

    @router.get('/teacher/report-reviews/submissions/{submission_id}/submitted.pdf')
    async def teacher_submitted_pdf(request: Request, submission_id: int):
        _, school, _ = teacher_identity(request)
        with closing(connect_studies(database_path)) as db:
            sub = submission_by_id(db, submission_id)
        if sub is None or sub['school'] != school or not sub['pdf_path']:
            raise HTTPException(404, '提出されたPDFが見つかりません。')
        path = study_file_path(pdf_dir, sub['pdf_path'])
        if not path.is_file():
            raise HTTPException(404, 'PDFファイルが見つかりません。')
        return file_response(path, sub['pdf_filename'] or 'report.pdf', 'application/pdf', 'inline')

    @router.get('/teacher/report-reviews/attachments/{attachment_id}')
    async def teacher_attachment(request: Request, attachment_id: int):
        _, school, _ = teacher_identity(request)
        with closing(connect_studies(database_path)) as db:
            row = db.execute('SELECT submission_id,filename,stored_name,content_type FROM group_report_review_attachment WHERE id=?', (attachment_id,)).fetchone()
            sub = submission_by_id(db, row[0]) if row else None
        if row is None or sub is None or sub['school'] != school:
            raise HTTPException(404, '添付ファイルが見つかりません。')
        path = study_file_path(attach_dir, row[2])
        if not path.is_file():
            raise HTTPException(404, '添付ファイルが見つかりません。')
        return file_response(path, row[1], row[3], 'attachment')

    @router.post('/teacher/api/report-reviews/{submission_id}/attachments')
    async def teacher_add_attachment(request: Request, submission_id: int):
        teacher_id, school, _ = teacher_identity(request)
        async with request.form(max_part_size=2**63 - 1) as form:
            teacher_csrf(request, form.get('csrf_token'))
            upload = form.get('file')
            if not isinstance(upload, StarletteUploadFile) or len(form.getlist('file')) != 1:
                raise HTTPException(422, '添付するファイルを1つ選んでください。')
            with closing(connect_studies(database_path)) as db, db:
                db.execute('BEGIN IMMEDIATE')
                return {'ok': True, 'attachment': add_review_attachment(db, attach_dir, submission_id, school, teacher_id, upload)}

    @router.post('/teacher/api/report-reviews/{submission_id}/attachments/{attachment_id}/delete')
    async def teacher_delete_attachment(request: Request, submission_id: int, attachment_id: int):
        return await review_action(request, submission_id, lambda db, school, teacher, body: (
            delete_review_attachment(db, attach_dir, submission_id, attachment_id, school, teacher), {'ok': True})[1])

    @router.get('/teacher/report-reviews/submissions/{submission_id}/review.pdf')
    async def review_pdf_page(request: Request, submission_id: int):
        _, school, _ = teacher_identity(request)

        def build():
            with closing(connect_studies(database_path)) as db:
                return review_pdf(database_path, image_dir, db, submission_id, school=school)
        pdf = await asyncio.to_thread(build)
        return Response(pdf, media_type='application/pdf', headers={'Content-Disposition': 'inline; filename="report-review.pdf"'})

    @router.get('/teacher/reports/{group_id}/assets/{content_hash}')
    async def teacher_asset(request: Request, group_id: str, content_hash: str):
        _, school, _ = teacher_identity(request)
        if not re.fullmatch(r'[0-9a-f]{64}', content_hash):
            raise HTTPException(404, '画像が見つかりません。')
        with closing(connect_studies(database_path)) as db:
            referenced = db.execute("SELECT 1 FROM group_report_version WHERE group_id=? AND school_name=? AND instr(report_snapshot,?)>0 LIMIT 1",
                                    (group_id, school, content_hash)).fetchone()
            access = db.execute('SELECT 1 FROM group_report_asset_access WHERE group_id=? AND content_hash=?', (group_id, content_hash)).fetchone()
            row = db.execute('SELECT stored_name,content_type FROM group_report_asset WHERE content_hash=?', (content_hash,)).fetchone()
        if not referenced or not access or not row or not (image_dir / row[0]).is_file():
            raise HTTPException(404, '画像が見つかりません。')
        return Response((image_dir / row[0]).read_bytes(), media_type=row[1], headers={'Cache-Control': 'private, max-age=3600'})

    async def review_action(request, submission_id, action):
        teacher_id, school, _ = teacher_identity(request)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422, '送信内容が正しくありません。')
        teacher_csrf(request, body.get('csrf_token'))
        with closing(connect_studies(database_path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            return action(db, school, teacher_id, body)

    @router.post('/teacher/api/report-reviews/{submission_id}/lock')
    async def lock(request: Request, submission_id: int):
        return await review_action(request, submission_id, lambda db, school, teacher, body: {
            'ok': True, 'holder': acquire_review_lock(db, submission_id, school, teacher)})

    @router.post('/teacher/api/report-reviews/{submission_id}/force-unlock')
    async def force_unlock(request: Request, submission_id: int):
        return await review_action(request, submission_id, lambda db, school, teacher, body: {
            'ok': True, 'previous_holder': force_release_review_lock(db, submission_id, school)})

    @router.post('/teacher/api/report-reviews/{submission_id}/save')
    async def save(request: Request, submission_id: int):
        def action(db, school, teacher, body):
            save_review(db, submission_id, school, teacher, body.get('overall_comment', ''), body.get('field_comments', {}))
            if body.get('finish'):
                release_review_lock(db, submission_id, school, teacher)
            return {'ok': True, 'editors': review_editors(db, submission_id)}
        return await review_action(request, submission_id, action)

    @router.post('/teacher/api/report-reviews/{submission_id}/return')
    async def return_(request: Request, submission_id: int):
        def action(db, school, teacher, body):
            return_review(db, submission_id, school, teacher, body.get('overall_comment', ''), body.get('field_comments', {}),
                          body.get('result'), body.get('due_day'))
            return {'ok': True}
        return await review_action(request, submission_id, action)

    return router
