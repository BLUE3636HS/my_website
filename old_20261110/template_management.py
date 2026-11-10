"""Admin-only template creation and visibility controls."""
import logging
import re
import secrets
import sqlite3
import uuid
import json
from contextlib import closing
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

from studies import connect_studies, get_templates


class TemplateField(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: StrictStr
    stable_key: StrictStr = ''
    heading_font_size: StrictInt = Field(default=11, ge=6, le=36)
    body_font_size: StrictInt = Field(default=10, ge=6, le=36)
    heading_alignment: Literal['left', 'center', 'right'] = 'left'
    body_alignment: Literal['left', 'center', 'right'] = 'left'
    heading_bold: StrictBool = False
    body_bold: StrictBool = False
    hide_heading: StrictBool = False
    max_length: StrictInt = Field(default=0, ge=0, le=9007199254740991)
    required: StrictBool = False
    field_type: Literal['text', 'image'] = 'text'
    image_size: Literal['small', 'large'] = 'small'
    image_alignment: Literal['left', 'center', 'right'] = 'center'

    @field_validator('label')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('見出しを入力してください。')
        return value.strip()

    @field_validator('stable_key')
    @classmethod
    def valid_stable_key(cls, value):
        value = value.strip()
        if value and (len(value) > 64 or not re.fullmatch(r'[A-Za-z0-9_-]+', value)):
            raise ValueError('識別キーは半角英数字、ハイフン、アンダースコアで入力してください。')
        return value

    @model_validator(mode='after')
    def image_caption_limit(self):
        if self.field_type == 'image' and self.max_length != 50:
            raise ValueError('画像キャプションは50文字以内に設定してください。')
        return self


class TemplateCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    csrf_token: StrictStr
    name: StrictStr = Field(max_length=100)
    fields: list[TemplateField] = Field(min_length=1)

    @field_validator('name')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('テンプレート名を入力してください。')
        return value.strip()

    @model_validator(mode='after')
    def unique_field_keys(self):
        keys = [item.stable_key for item in self.fields if item.stable_key]
        if len(keys) != len(set(keys)):
            raise ValueError('項目識別キーは重複できません。')
        return self


class Visibility(BaseModel):
    model_config = ConfigDict(extra='forbid')
    csrf_token: StrictStr
    active: StrictBool


class SchoolReportTemplate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    csrf_token: StrictStr
    name: StrictStr = Field(default='学校成果レポート', max_length=100)
    fields: list[TemplateField] = Field(min_length=1)
    source_template_id: StrictInt | None = None

    @model_validator(mode='after')
    def unique_field_keys(self):
        keys = [item.stable_key for item in self.fields if item.stable_key]
        if len(keys) != len(set(keys)):
            raise ValueError('項目識別キーは重複できません。')
        return self


def require_admin(request: Request):
    if request.session.get('admin_login') is not True:
        raise HTTPException(403, '管理者ログインが必要です。')


def check_csrf(request, value):
    expected = request.session.get('study_template_csrf_token', '')
    if not expected or not secrets.compare_digest(expected.encode(), value.encode()):
        raise HTTPException(403, '画面を再読み込みしてください。')


def create_template_router(database_path, templates):
    router = APIRouter()

    def page(request, creating):
        token = request.session.setdefault('study_template_csrf_token', secrets.token_urlsafe(32))
        with closing(connect_studies(database_path())) as db:
            items = [] if creating else get_templates(db, include_inactive=True)
        return templates.TemplateResponse(request=request,
            name='admin/study_template_new.html' if creating else 'admin/study_templates.html',
            context={'request': request, 'admin_id': request.session.get('admin_id'),
                     'csrf_token': token, 'study_templates': items})

    def super_teacher_school(request):
        if request.session.get('teacher_login') is not True:
            raise HTTPException(403, '先生としてログインしてください。')
        teacher_id = request.session.get('teacher_id')
        with closing(connect_studies(database_path())) as db:
            row = db.execute('SELECT school,role FROM teacher WHERE id=?', (teacher_id,)).fetchone()
        if row is None or row[1] != 'super_teacher':
            raise HTTPException(403, 'super_teacherのみ利用できます。')
        return row[0]

    def apply_template(db, school, template_id, actor):
        import datetime
        from studies import get_templates
        target = db.execute('SELECT id,name FROM study_template WHERE id=? AND active=1 AND owner_school=?', (template_id,school)).fetchone()
        if target is None:
            raise HTTPException(422, '有効な学校用テンプレートを指定してください。')
        fields = next(item for item in get_templates(db) if item['id'] == template_id)['fields']
        affected=[]
        for draft in db.execute('SELECT group_id,report_data,template_snapshot FROM group_report_draft WHERE school_name=?', (school,)).fetchall():
            affected.append(draft[0])
            old_values = json.loads(draft[1])
            old_template = json.loads(draft[2])
            prior_by_key = {item['stable_key']: item for item in old_template['fields']}
            next_values = {}
            for item in fields:
                old_item = prior_by_key.get(item.get('stable_key'))
                if old_item:
                    prior = old_values.get(old_item['stable_key'], {})
                    if item.get('field_type') == 'image':
                        next_values[item['stable_key']] = {'value': '', 'images': prior.get('images', [])}
                    else:
                        next_values[item['stable_key']] = {'value': prior.get('value', ''), 'images': []}
                else:
                    next_values[item['stable_key']] = {'value': '', 'images': []}
            snapshot = {'id':template_id, 'name':target[1], 'fields':fields}
            db.execute('UPDATE group_report_draft SET template_id=?,template_snapshot=?,report_data=?,revision=revision+1,updated_at=? WHERE group_id=?',
                       (template_id,json.dumps(snapshot,ensure_ascii=False),json.dumps(next_values,ensure_ascii=False),datetime.datetime.now(datetime.timezone.utc).isoformat(),draft[0]))
            db.execute('DELETE FROM group_report_lock WHERE group_id=?',(draft[0],))
        db.execute('INSERT INTO school_report_template(school,template_id,updated_at,updated_by) VALUES(?,?,?,?) ON CONFLICT(school) DO UPDATE SET template_id=excluded.template_id,updated_at=excluded.updated_at,updated_by=excluded.updated_by',
                   (school,template_id,datetime.datetime.now(datetime.timezone.utc).isoformat(),actor))
        return affected

    @router.get('/teacher/school-report')
    async def school_report_page(request: Request, source_template_id: int | None = None):
        school = super_teacher_school(request)
        token = request.session.setdefault('school_report_template_csrf', secrets.token_urlsafe(32))
        with closing(connect_studies(database_path())) as db:
            items = [item for item in get_templates(db)
                     if db.execute('SELECT 1 FROM study_template WHERE id=? AND active=1 AND owner_school IS NULL',(item['id'],)).fetchone()]
            assigned = db.execute('SELECT template_id FROM school_report_template WHERE school=?',(school,)).fetchone()
            all_items = {item['id']: item for item in get_templates(db, include_inactive=True)}
            current = all_items.get(assigned[0]) if assigned else None
        source = next((item for item in items if item['id'] == source_template_id), None)
        if source_template_id is not None and source is None:
            raise HTTPException(404, '複製元の共通テンプレートが見つかりません。')
        initial = source or current
        return templates.TemplateResponse(request=request,name='admin/school_report_template.html',context={
            'request':request,'school':school,'csrf_token':token,'study_templates':items,
            'teacher_id':request.session.get('teacher_id'),'teacher_role':'super_teacher',
            'apply_mode':True, 'apply_endpoint':'/teacher/school-report/apply',
            'source_action':'/teacher/school-report', 'source_template_id':source_template_id,
            'initial_template_fields': [{key: value for key, value in field.items() if key not in ('id', 'required')}
                                       for field in initial['fields']] if initial else []})

    @router.post('/teacher/school-report/apply')
    async def school_report_apply(request: Request,data: SchoolReportTemplate):
        school=super_teacher_school(request)
        expected=request.session.get('school_report_template_csrf','')
        if not expected or not secrets.compare_digest(expected,data.csrf_token): raise HTTPException(403,'画面を再読み込みしてください。')
        from group_reports import notify_group_template_changed
        fields=[item.model_dump() for item in data.fields]
        try:
            with closing(connect_studies(database_path())) as db, db:
                db.execute('BEGIN IMMEDIATE')
                assigned=db.execute('SELECT template_id FROM school_report_template WHERE school=?',(school,)).fetchone()
                current_id=assigned[0] if assigned else None
                source_keys=set()
                if data.source_template_id is not None:
                    source=db.execute('SELECT id FROM study_template WHERE id=? AND active=1 AND owner_school IS NULL',(data.source_template_id,)).fetchone()
                    if source is None: raise HTTPException(404,'複製元の共通テンプレートが見つかりません。')
                    source_keys.update(row[0] for row in db.execute('SELECT stable_key FROM study_template_field WHERE template_id=?',(data.source_template_id,)))
                if current_id is not None:
                    source_keys.update(row[0] for row in db.execute('SELECT stable_key FROM study_template_field WHERE template_id=?',(current_id,)))
                for item in fields:
                    key=item['stable_key']
                    if key and key not in source_keys and db.execute('SELECT 1 FROM study_template_field WHERE stable_key=?',(key,)).fetchone():
                        raise HTTPException(422,'項目識別キーが既存テンプレートと重複しています。複製元を選び直してください。')
                    if not key: item['stable_key']=uuid.uuid4().hex
                template_id=db.execute('INSERT INTO study_template(name,active,owner_school) VALUES(?,1,?)',(data.name.strip(),school)).lastrowid
                db.executemany('''INSERT INTO study_template_field(template_id,label,position,required,heading_font_size,body_font_size,hide_heading,max_length,heading_alignment,body_alignment,heading_bold,body_bold,field_type,image_size,image_alignment,stable_key)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',[(template_id,f['label'],i,0,f['heading_font_size'],f['body_font_size'],f['hide_heading'],f['max_length'],f['heading_alignment'],f['body_alignment'],f['heading_bold'],f['body_bold'],f['field_type'],f['image_size'],f['image_alignment'],f['stable_key']) for i,f in enumerate(fields)])
                affected=apply_template(db,school,template_id,request.session.get('teacher_id'))
        except sqlite3.Error:
            logging.exception('Failed to apply school report template')
            raise HTTPException(500,'適用できませんでした。入力内容を確認して再試行してください。')
        await notify_group_template_changed(affected)
        return {'ok':True,'id':template_id}

    @router.get('/admin/study-templates')
    async def list_templates(request: Request):
        require_admin(request)
        return page(request, False)

    @router.get('/admin/study-templates/new')
    async def new_template(request: Request):
        require_admin(request)
        return page(request, True)

    @router.post('/admin/study-templates')
    async def create_template(request: Request, data: TemplateCreate):
        require_admin(request)
        check_csrf(request, data.csrf_token)
        try:
            with closing(connect_studies(database_path())) as db, db:
                template_id = db.execute('INSERT INTO study_template(name,active) VALUES (?,1)', (data.name,)).lastrowid
                db.executemany('''INSERT INTO study_template_field
                    (template_id,label,position,required,heading_font_size,body_font_size,hide_heading,max_length,heading_alignment,body_alignment,heading_bold,body_bold,field_type,image_size,image_alignment,stable_key)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', [
                        (template_id, item.label, position, 0, item.heading_font_size,
                         item.body_font_size, item.hide_heading, item.max_length, item.heading_alignment,
                         item.body_alignment, item.heading_bold, item.body_bold, item.field_type,
                         item.image_size, item.image_alignment, item.stable_key or uuid.uuid4().hex)
                        for position, item in enumerate(data.fields)])
        except sqlite3.Error:
            logging.exception('Failed to create study template')
            raise HTTPException(500, '保存できませんでした。時間をおいて再度お試しください。')
        return JSONResponse({'ok': True, 'id': template_id}, status_code=201)

    @router.post('/admin/study-templates/{template_id}/visibility')
    async def set_visibility(request: Request, template_id: int, data: Visibility):
        require_admin(request)
        check_csrf(request, data.csrf_token)
        try:
            with closing(connect_studies(database_path())) as db, db:
                updated = db.execute('UPDATE study_template SET active=? WHERE id=?', (data.active, template_id))
                if not updated.rowcount:
                    raise HTTPException(404, 'テンプレートが見つかりません。')
        except sqlite3.Error:
            logging.exception('Failed to update study template visibility')
            raise HTTPException(500, '公開状態を変更できませんでした。')
        return {'ok': True, 'active': data.active}

    return router
