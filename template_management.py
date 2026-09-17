"""Admin-only template creation and visibility controls."""
import logging
import secrets
import sqlite3
from contextlib import closing
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

from studies import connect_studies, get_templates


class TemplateField(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: StrictStr
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

    @model_validator(mode='after')
    def image_caption_limit(self):
        if self.field_type == 'image' and self.max_length != 50:
            raise ValueError('画像キャプションは50文字以内に設定してください。')
        return self


class TemplateCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    csrf_token: StrictStr
    name: StrictStr
    fields: list[TemplateField] = Field(min_length=1)

    @field_validator('name')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('テンプレート名を入力してください。')
        return value.strip()


class Visibility(BaseModel):
    model_config = ConfigDict(extra='forbid')
    csrf_token: StrictStr
    active: StrictBool


def require_admin(request: Request):
    if request.session.get('admin_login') is not True:
        raise HTTPException(403, '管理者ログインが必要です。')


def check_csrf(request, value):
    expected = request.session.get('study_template_csrf_token', '')
    if not expected or not secrets.compare_digest(expected.encode(), value.encode()):
        raise HTTPException(403, '画面を再読み込みしてください。')


def create_template_router(database_path, templates):
    router = APIRouter(prefix='/admin/study-templates', dependencies=[Depends(require_admin)])

    def page(request, creating):
        token = request.session.setdefault('study_template_csrf_token', secrets.token_urlsafe(32))
        with closing(connect_studies(database_path())) as db:
            items = [] if creating else get_templates(db, include_inactive=True)
        return templates.TemplateResponse(request=request,
            name='admin/study_template_new.html' if creating else 'admin/study_templates.html',
            context={'request': request, 'admin_id': request.session.get('admin_id'),
                     'csrf_token': token, 'study_templates': items})

    @router.get('')
    async def list_templates(request: Request):
        return page(request, False)

    @router.get('/new')
    async def new_template(request: Request):
        return page(request, True)

    @router.post('')
    async def create_template(request: Request, data: TemplateCreate):
        check_csrf(request, data.csrf_token)
        try:
            with closing(connect_studies(database_path())) as db, db:
                template_id = db.execute('INSERT INTO study_template(name,active) VALUES (?,1)', (data.name,)).lastrowid
                db.executemany('''INSERT INTO study_template_field
                    (template_id,label,position,required,heading_font_size,body_font_size,hide_heading,max_length,heading_alignment,body_alignment,heading_bold,body_bold,field_type,image_size,image_alignment)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', [
                        (template_id, item.label, position, item.required, item.heading_font_size,
                         item.body_font_size, item.hide_heading, item.max_length, item.heading_alignment,
                         item.body_alignment, item.heading_bold, item.body_bold, item.field_type,
                         item.image_size, item.image_alignment)
                        for position, item in enumerate(data.fields)])
        except sqlite3.Error:
            logging.exception('Failed to create study template')
            raise HTTPException(500, '保存できませんでした。時間をおいて再度お試しください。')
        return JSONResponse({'ok': True, 'id': template_id}, status_code=201)

    @router.post('/{template_id}/visibility')
    async def set_visibility(request: Request, template_id: int, data: Visibility):
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
