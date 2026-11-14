"""Group research report drafts, locks, immutable snapshots and PDF output."""
import asyncio, datetime, json, secrets, hashlib, sqlite3
from contextlib import closing
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.responses import Response

from studies import connect_studies, get_templates, prune_manual_report_versions
from report_header import DEFAULT_HEADER_ITEMS, group_context_snapshot, header_text, normalize_header_style, school_header_items, school_header_style
from study_pdf import render_study_pdf
from study_images import normalize_study_image
from tekne_chat import _same_origin

JST = datetime.timezone(datetime.timedelta(hours=9))
SCHEDULED_SAVED_BY = '自動保存'


def run_scheduled_snapshots(database_path, now=None):
    """終わった自動保存日（指定日の23:59を過ぎた日）について、学校の全グループの下書きを永久保存する。

    何度実行しても同じグループ・同じ日の保存は1件だけになる。サーバー停止で取りこぼした日は次の実行時に保存する。
    """
    now = now or datetime.datetime.now(JST)
    today = now.astimezone(JST).date().isoformat()
    saved_at = now.astimezone(datetime.timezone.utc).isoformat(timespec='seconds')
    created = 0
    with closing(connect_studies(database_path)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        days = db.execute('SELECT school,day FROM group_report_snapshot_day WHERE processed_at IS NULL AND day<? ORDER BY day,school', (today,)).fetchall()
        chat_ready = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tekne_chat_group'").fetchone() is not None
        for school, day in days:
            # 削除されたグループの下書きは残っていても保存しない（チャット未初期化のDBでは絞り込まない）
            only_existing = ' AND EXISTS (SELECT 1 FROM tekne_chat_group g WHERE g.id=d.group_id)' if chat_ready else ''
            drafts = db.execute('SELECT d.group_id,d.school_id,d.school_name,d.template_snapshot,d.report_data FROM group_report_draft d '
                                'WHERE d.school_name=?' + only_existing + ' ORDER BY d.group_id', (school,)).fetchall()
            for group_id, school_id, school_name, template_snapshot, report_data in drafts:
                if db.execute("SELECT 1 FROM group_report_version WHERE group_id=? AND kind='scheduled' AND scheduled_date=?", (group_id, day)).fetchone():
                    continue
                version = db.execute('SELECT COALESCE(MAX(version_number),0)+1 FROM group_report_version WHERE group_id=?', (group_id,)).fetchone()[0]
                context = json.dumps(group_context_snapshot(db, group_id, school_name), ensure_ascii=False)
                db.execute("""INSERT INTO group_report_version(group_id,school_id,school_name,version_number,template_snapshot,report_snapshot,saved_by,saved_at,kind,scheduled_date,context_snapshot)
                    VALUES(?,?,?,?,?,?,?,?,'scheduled',?,?)""", (group_id, school_id, school_name, version, template_snapshot, report_data, SCHEDULED_SAVED_BY, saved_at, day, context))
                created += 1
            db.execute('UPDATE group_report_snapshot_day SET processed_at=? WHERE school=? AND day=?', (saved_at, school, day))
    return created


def draft_state(row):
    """下書きの行を画面用の形にする。template_key は表示中のテンプレートが古くないかを確かめるための値。"""
    return {'template_id': row[0], 'template': json.loads(row[1]), 'data': json.loads(row[2]),
            'revision': row[3], 'updated_at': row[4],
            'template_key': hashlib.sha256(row[1].encode('utf-8')).hexdigest()[:16]}


def version_rows(db, group_id):
    # saved_by_name: 保存した生徒の名前（自動保存や退会済みの生徒は saved_by の値をそのまま使う）
    return [dict(zip(('id', 'version_number', 'saved_at', 'saved_by', 'kind', 'scheduled_date', 'saved_by_name'), r)) for r in db.execute(
        '''SELECT v.id,v.version_number,v.saved_at,v.saved_by,v.kind,v.scheduled_date,COALESCE(s.name,v.saved_by)
           FROM group_report_version v LEFT JOIN student s ON s.id=v.saved_by
           WHERE v.group_id=? ORDER BY v.version_number DESC''', (group_id,))]

_SOCKETS = {}
_SOCKET_LOCK = asyncio.Lock()

async def notify_group_template_changed(group_ids):
    async with _SOCKET_LOCK:
        sockets=[ws for group_id in group_ids for ws in _SOCKETS.get(group_id,{})]
    for ws in sockets:
        try: await ws.send_json({'type':'template.updated'})
        except Exception: pass

def load_version(db,group_id,version_id):
    """保存履歴1件と、PDFの先頭に印字する文字列（テンプレートに保存された設定＋保存時点のメンバー情報）を返す。"""
    cursor=db.execute('SELECT template_snapshot,report_snapshot,version_number,saved_by,saved_at,school_name,kind,scheduled_date,context_snapshot FROM group_report_version WHERE id=? AND group_id=?',(version_id,group_id))
    row=cursor.fetchone()
    if row is None: return None
    row=dict(zip([c[0] for c in cursor.description],row))
    template=json.loads(row['template_snapshot'])
    items=template.get('header_items',DEFAULT_HEADER_ITEMS)
    row['header_style']=normalize_header_style(template.get('header_style'))
    # 以前の履歴には保存時点のメンバー情報がないので、今のメンバーで代用する
    context=json.loads(row['context_snapshot']) if row['context_snapshot'] else group_context_snapshot(db,group_id,row['school_name'])
    row['header']=header_text(items,{**row,'group_id':group_id},context)
    return row


def pdf_sections(database_path,image_dir,template,values,metadata,header_style=None):
    # 先頭の情報（先生が成果レポート設定で選んだもの）。見出しは付けず、何も選ばれていなければ印字しない
    style=normalize_header_style(header_style)
    sections=[('保存情報',metadata,11,style['font_size'],True,'left',style['alignment'],False,style['bold'])] if metadata else []
    if isinstance(database_path,sqlite3.Connection):
        assets={r[0]:r[1] for r in database_path.execute('SELECT content_hash,stored_name FROM group_report_asset')}
    else:
        with closing(connect_studies(database_path)) as db:
            assets={r[0]:r[1] for r in db.execute('SELECT content_hash,stored_name FROM group_report_asset')}
    for field in template['fields']:
        data=values.get(field['stable_key'],{})
        kind=field.get('field_type','text')
        style=(field.get('heading_font_size',11),field.get('body_font_size',10))
        align=(field.get('heading_alignment','left'),field.get('body_alignment','left'),field.get('heading_bold',False),field.get('body_bold',False))
        # 文字（文字だけ／文字と画像）。空欄なら「(未入力)」と見出しを出す
        if kind in ('text','both'):
            value=data.get('value') or ''
            empty=not value.strip()
            sections.append((field['label'],'(未入力)' if empty else value,*style,False if empty else field.get('hide_heading',False),*align))
        # 画像（画像だけ／文字と画像）。文字と画像のときは見出しを文字側で出しているので、画像側には付けない
        if kind in ('image','both'):
            images=[]
            for item in data.get('images',[]):
                stored=assets.get(item.get('content_hash'))
                if stored and (Path(image_dir)/stored).is_file(): images.append((str(Path(image_dir)/stored),item.get('caption','')[:50]))
            if images:
                sections.append({'type':'image','label':field['label'],'images':images,'heading_font_size':style[0],'body_font_size':style[1],'heading_alignment':align[0],'body_alignment':align[1],'heading_bold':align[2],'body_bold':align[3],'hide_heading':True if kind=='both' else field.get('hide_heading',False),'image_size':field.get('image_size','small'),'image_alignment':field.get('image_alignment','center')})
            elif kind=='image':
                sections.append((field['label'],'(未入力)',*style,False,*align))
    return sections


def build_group_report_router(database_path, templates, chat_service, image_dir):
    router=APIRouter()
    sockets=_SOCKETS
    socket_lock=_SOCKET_LOCK
    image_dir=Path(image_dir); image_dir.mkdir(parents=True,exist_ok=True)
    def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
    def student_identity(session):
        if session.get('user_login') is not True or not session.get('user_id'):
            raise HTTPException(401,'生徒としてログインしてください。')
        return session['user_id']
    def group_access(session,group_id):
        user=student_identity(session)
        groups=chat_service.groups(session)
        group=next((g for g in groups if g['id']==group_id),None)
        if group is None: raise HTTPException(403,'所属グループのレポートのみ利用できます。')
        with closing(connect_studies(database_path)) as db:
            identity=db.execute('SELECT g.school_id,s.school_name FROM tekne_chat_group g JOIN tekne_school_identity s ON s.id=g.school_id WHERE g.id=?',(group_id,)).fetchone()
        if identity is None: raise HTTPException(404,'グループが見つかりません。')
        group['school_id'],group['school_name']=identity[0],identity[1]
        return user,group
    def draft(db,group):
        row=db.execute('SELECT template_id,template_snapshot,report_data,revision,updated_at FROM group_report_draft WHERE group_id=?',(group['id'],)).fetchone()
        if row: return draft_state(row)
        setting=db.execute('SELECT s.template_id,t.name FROM school_report_template s JOIN study_template t ON t.id=s.template_id WHERE s.school=? AND t.active=1',(group['school_name'],)).fetchone()
        if not setting: return None
        template=next(x for x in get_templates(db) if x['id']==setting[0])
        for field in template['fields']:
            if not field.get('stable_key'): field['stable_key']=f"legacy-{field['id']}"
        snapshot={'id':template['id'],'name':template['name'],'fields':template['fields'],'header_items':school_header_items(db,group['school_name']),'header_style':school_header_style(db,group['school_name'])}
        data={f['stable_key']:{'value':'','images':[]} for f in template['fields']}
        db.execute('INSERT OR IGNORE INTO group_report_draft(group_id,school_id,school_name,template_id,template_snapshot,report_data,updated_at,revision) VALUES(?,?,?,?,?,?,?,0)',
                   (group['id'],group['school_id'],group['school_name'],template['id'],json.dumps(snapshot,ensure_ascii=False),json.dumps(data,ensure_ascii=False),now()))
        row=db.execute('SELECT template_id,template_snapshot,report_data,revision,updated_at FROM group_report_draft WHERE group_id=?',(group['id'],)).fetchone()
        return draft_state(row)
    def validate_patch(db,incoming,state,group_id):
        fields={f['stable_key']:f for f in state['template']['fields']}
        if not isinstance(incoming,dict) or not set(incoming)<=set(fields): raise HTTPException(422,'レポート項目が不正です。')
        for key,item in incoming.items():
            if not isinstance(item,dict): raise HTTPException(422,'レポート項目が不正です。')
            value=item.get('value','')
            if not isinstance(value,str): raise HTTPException(422,'入力内容が不正です。')
            maximum=fields[key].get('max_length',0)
            if maximum and len(value)>maximum: raise HTTPException(422,'入力文字数が上限を超えています。')
            images=item.get('images',[])
            if not isinstance(images,list) or len(images)>4: raise HTTPException(422,'画像の数が不正です。')
            if images and fields[key].get('field_type') not in ('image','both'): raise HTTPException(422,'画像を添付できない項目です。')
            for image in images:
                if (not isinstance(image,dict) or not isinstance(image.get('content_hash'),str)
                        or not isinstance(image.get('caption',''),str) or len(image.get('caption',''))>50
                        or not db.execute('SELECT 1 FROM group_report_asset_access WHERE group_id=? AND content_hash=?',(group_id,image['content_hash'])).fetchone()):
                    raise HTTPException(422,'添付画像が確認できません。')
    def csrf(request,value):
        expected=request.session.get('tekne_chat_csrf','')
        if not expected or not value or not secrets.compare_digest(expected,value): raise HTTPException(403,'画面を再読み込みしてください。')
    async def broadcast(group_id,message):
        async with socket_lock: targets=list(sockets.get(group_id,{}))
        for ws in targets:
            try: await ws.send_json(message)
            except Exception: pass
    @router.get('/tekne-chat/groups/{group_id}/report')
    async def page(request:Request,group_id:str):
        _,group=group_access(request.session,group_id)
        with closing(connect_studies(database_path)) as db: state=draft(db,group); db.commit()
        return templates.TemplateResponse(request=request,name='group_report.html',context={'request':request,'group':group,'state':state,'csrf_token':request.session.get('tekne_chat_csrf','')})
    @router.get('/api/group-reports/{group_id}')
    async def get_draft(request:Request,group_id:str):
        _,group=group_access(request.session,group_id)
        with closing(connect_studies(database_path)) as db: state=draft(db,group); db.commit()
        if state is None:return {'unconfigured':True}
        with closing(connect_studies(database_path)) as db:
            locks=[{'field_key':r[0],'student_id':r[1],'student_name':r[2]} for r in db.execute('SELECT field_key,student_id,student_name FROM group_report_lock WHERE group_id=?',(group_id,))]
            versions=version_rows(db,group_id)
        return {'state':state,'locks':locks,'versions':versions}
    @router.post('/api/group-reports/{group_id}/save')
    async def save(request:Request,group_id:str):
        user,group=group_access(request.session,group_id); body=await request.json(); csrf(request,body.get('csrf_token'))
        with closing(connect_studies(database_path)) as db, db:
            db.execute('BEGIN IMMEDIATE'); state=draft(db,group)
            if state is None: raise HTTPException(409,'学校用テンプレートが設定されていません。')
            # 個人スペースはWebSocketでテンプレート変更の通知を受けないので、保存時に表示中のテンプレートと比べる
            sent_key=body.get('template_key')
            if sent_key is not None and sent_key!=state['template_key']:
                raise HTTPException(412,'先生がテンプレートを更新しました。書きかけの内容は残したまま画面を読み込み直します。')
            incoming=body.get('data')
            validate_patch(db,incoming,state,group_id)
            locks={r[0]:r[1] for r in db.execute('SELECT field_key,student_id FROM group_report_lock WHERE group_id=?',(group_id,))}
            if any(owner!=user for key,owner in locks.items() if key in incoming): raise HTTPException(423,'他のメンバーが編集中の項目があります。')
            merged={**state['data'],**incoming}
            db.execute('UPDATE group_report_draft SET report_data=?,updated_at=?,revision=revision+1 WHERE group_id=?',(json.dumps(merged,ensure_ascii=False),now(),group_id))
            # レポートの記入も「更新」として扱い、チャットの一覧で新しい順に並ぶようにする
            db.execute('UPDATE tekne_chat_group SET updated_at=? WHERE id=?',(now(),group_id))
            state['data']=merged; state['revision']+=1
        await broadcast(group_id,{'type':'draft.updated','state':state,'by':user})
        return {'ok':True,'revision':state['revision']}
    @router.post('/api/group-reports/{group_id}/versions')
    async def create_version(request:Request,group_id:str):
        user,group=group_access(request.session,group_id); body=await request.json(); csrf(request,body.get('csrf_token'))
        with closing(connect_studies(database_path)) as db, db:
            db.execute('BEGIN IMMEDIATE'); state=draft(db,group)
            if state is None: raise HTTPException(409,'学校用テンプレートが設定されていません。')
            # 個人スペースはWebSocketでテンプレート変更の通知を受けないので、保存時に表示中のテンプレートと比べる
            sent_key=body.get('template_key')
            if sent_key is not None and sent_key!=state['template_key']:
                raise HTTPException(412,'先生がテンプレートを更新しました。書きかけの内容は残したまま画面を読み込み直します。')
            incoming=body.get('data')
            validate_patch(db,incoming,state,group_id)
            if db.execute('SELECT 1 FROM group_report_lock WHERE group_id=? AND student_id<>? LIMIT 1',(group_id,user)).fetchone():
                raise HTTPException(423,'他のメンバーが項目を編集中です。編集完了後に保存してください。')
            locks={r[0]:r[1] for r in db.execute('SELECT field_key,student_id FROM group_report_lock WHERE group_id=?',(group_id,))}
            if any(owner!=user for key,owner in locks.items() if key in incoming): raise HTTPException(423,'他のメンバーが編集中の項目があります。')
            merged={**state['data'],**incoming}
            db.execute('UPDATE group_report_draft SET report_data=?,updated_at=?,revision=revision+1 WHERE group_id=?',(json.dumps(merged,ensure_ascii=False),now(),group_id))
            # レポートの記入も「更新」として扱い、チャットの一覧で新しい順に並ぶようにする
            db.execute('UPDATE tekne_chat_group SET updated_at=? WHERE id=?',(now(),group_id))
            state['data']=merged
            version=db.execute('SELECT COALESCE(MAX(version_number),0)+1 FROM group_report_version WHERE group_id=?',(group_id,)).fetchone()[0]
            context=json.dumps(group_context_snapshot(db,group_id,group['school_name']),ensure_ascii=False)
            saved=now(); db.execute('INSERT INTO group_report_version(group_id,school_id,school_name,version_number,template_snapshot,report_snapshot,saved_by,saved_at,context_snapshot) VALUES(?,?,?,?,?,?,?,?,?)',
                (group_id,group['school_id'],group['school_name'],version,json.dumps(state['template'],ensure_ascii=False),json.dumps(state['data'],ensure_ascii=False),user,saved,context))
            prune_manual_report_versions(db,group_id)
        return {'ok':True,'version_number':version,'saved_at':saved}
    @router.post('/api/group-reports/{group_id}/assets')
    async def upload_asset(request:Request,group_id:str,file:UploadFile=File(...),csrf_token:str=Form(...),field_key:str=Form(...)):
        user,group=group_access(request.session,group_id); csrf(request,csrf_token)
        try: content,image_format,_,_,suffix,original=normalize_study_image(file)
        except ValueError as exc: raise HTTPException(422,str(exc))
        content_type='image/jpeg' if image_format=='JPEG' else 'image/png'
        digest=hashlib.sha256(content).hexdigest(); stored=digest+suffix
        target=image_dir/stored
        if not target.exists(): target.write_bytes(content)
        with closing(connect_studies(database_path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            state=draft(db,group)
            field=next((f for f in state['template']['fields'] if f['stable_key']==field_key),None) if state else None
            if field is None or field.get('field_type') not in ('image','both'): raise HTTPException(422,'画像項目が不正です。')
            if len(state['data'].get(field_key,{}).get('images',[]))>=4: raise HTTPException(422,'画像は1項目につき4枚までです。')
            lock=db.execute('SELECT student_id FROM group_report_lock WHERE group_id=? AND field_key=?',(group_id,field_key)).fetchone()
            if not lock or lock[0]!=user: raise HTTPException(423,'画像を追加するには項目の編集ロックが必要です。')
            db.execute('INSERT OR IGNORE INTO group_report_asset VALUES(?,?,?,?,?)',(digest,stored,content_type,len(content),now()))
            db.execute('INSERT OR IGNORE INTO group_report_asset_access(group_id,content_hash) VALUES(?,?)',(group_id,digest))
        return {'content_hash':digest,'stored_name':stored,'original_name':original,'url':f'/api/group-reports/{group_id}/assets/{digest}'}
    @router.get('/api/group-reports/{group_id}/assets/{content_hash}')
    async def get_asset(request:Request,group_id:str,content_hash:str):
        group_access(request.session,group_id)
        with closing(connect_studies(database_path)) as db:
            row=db.execute('SELECT stored_name,content_type FROM group_report_asset WHERE content_hash=?',(content_hash,)).fetchone()
            references=[r[0] for r in db.execute('SELECT report_data FROM group_report_draft WHERE group_id=? UNION ALL SELECT report_snapshot FROM group_report_version WHERE group_id=?',(group_id,group_id))]
            access=db.execute('SELECT 1 FROM group_report_asset_access WHERE group_id=? AND content_hash=?',(group_id,content_hash)).fetchone()
        if not access or not any(content_hash in value for value in references): raise HTTPException(404,'画像が見つかりません。')
        if not row: raise HTTPException(404,'画像が見つかりません。')
        path=image_dir/row[0]
        if not path.is_file(): raise HTTPException(404,'画像が見つかりません。')
        return Response(path.read_bytes(),media_type=row[1],headers={'Cache-Control':'private, max-age=3600'})
    @router.get('/api/group-reports/{group_id}/versions/{version_id}.pdf')
    async def version_pdf(request:Request,group_id:str,version_id:int):
        _,group=group_access(request.session,group_id)
        with closing(connect_studies(database_path)) as db:
            row=load_version(db,group_id,version_id)
        if not row: raise HTTPException(404,'保存履歴が見つかりません。')
        sections=pdf_sections(database_path,image_dir,json.loads(row['template_snapshot']),json.loads(row['report_snapshot']),row['header'],row['header_style'])
        pdf=await asyncio.to_thread(render_study_pdf,sections)
        return Response(pdf,media_type='application/pdf',headers={'Content-Disposition':f'inline; filename="report-v{row["version_number"]}.pdf"'})
    @router.get('/teacher/reports')
    async def teacher_reports(request:Request):
        if request.session.get('teacher_login') is not True: raise HTTPException(403,'先生としてログインしてください。')
        teacher_id=request.session.get('teacher_id')
        with closing(connect_studies(database_path)) as db:
            teacher=db.execute('SELECT school,role FROM teacher WHERE id=?',(teacher_id,)).fetchone()
            if teacher is None: raise HTTPException(403,'先生アカウントが確認できません。')
            # 個人スペースは作成者（本人）の名前も表示して、グループと区別する
            groups=db.execute('''SELECT DISTINCT v.group_id,v.school_name,g.name,COALESCE(g.kind,'group'),COALESCE(st.name,i.login_id)
                FROM group_report_version v JOIN tekne_chat_group g ON g.id=v.group_id  -- 削除されたグループは表示しない
                LEFT JOIN tekne_student_identity i ON i.id=g.created_by LEFT JOIN student st ON st.id=i.login_id
                WHERE v.school_name=? ORDER BY COALESCE(g.kind,'group'),g.name,v.group_id''',(teacher[0],)).fetchall()
            records=[]
            for item in groups:
                records.append({'id':item[0],'name':item[2] or item[0],'kind':item[3],'owner':item[4] or '',
                                'versions':version_rows(db,item[0])})
        return templates.TemplateResponse(request=request,name='teacher/group_reports.html',context={'request':request,'groups':records,'school':teacher[0],'teacher_id':teacher_id,'teacher_role':teacher[1]})
    @router.get('/teacher/reports/{group_id}/versions/{version_id}.pdf')
    async def teacher_version_pdf(request:Request,group_id:str,version_id:int):
        if request.session.get('teacher_login') is not True: raise HTTPException(403,'先生としてログインしてください。')
        with closing(connect_studies(database_path)) as db:
            teacher=db.execute('SELECT school FROM teacher WHERE id=?',(request.session.get('teacher_id'),)).fetchone()
            row=load_version(db,group_id,version_id)
            group_exists=db.execute('SELECT 1 FROM tekne_chat_group WHERE id=?',(group_id,)).fetchone()
        if teacher is None or row is None or not group_exists or row['school_name']!=teacher[0]: raise HTTPException(404,'保存履歴が見つかりません。')
        sections=pdf_sections(database_path,image_dir,json.loads(row['template_snapshot']),json.loads(row['report_snapshot']),row['header'],row['header_style'])
        return Response(await asyncio.to_thread(render_study_pdf,sections),media_type='application/pdf',headers={'Content-Disposition':f'inline; filename="report-v{row["version_number"]}.pdf"'})
    @router.websocket('/ws/group-reports/{group_id}')
    async def socket(websocket:WebSocket,group_id:str):
        if not _same_origin(websocket): await websocket.close(code=4403); return
        session=websocket.scope.get('session',{})
        try: user,group=await asyncio.to_thread(group_access,session,group_id)
        except HTTPException: await websocket.close(code=4403); return
        # 個人スペースのレポートはHTTPだけで保存する（編集ロックや同時編集の反映が不要なため）
        if group.get('kind')=='personal': await websocket.close(code=4403); return
        await websocket.accept()
        async with socket_lock:
            first=not sockets.get(group_id)
            sockets.setdefault(group_id,{})[websocket]=user
        if first:
            with closing(connect_studies(database_path)) as db, db: db.execute('DELETE FROM group_report_lock WHERE group_id=?',(group_id,))
        await broadcast(group_id,{'type':'presence','count':len(sockets.get(group_id,{}))})
        try:
            while True:
                try: event=await asyncio.wait_for(websocket.receive_json(),timeout=30)
                except asyncio.TimeoutError:
                    try: await asyncio.to_thread(group_access,session,group_id)
                    except HTTPException: await websocket.close(code=4403); return
                    await websocket.send_json({'type':'ping'})
                    continue
                try: await asyncio.to_thread(group_access,session,group_id)
                except HTTPException: await websocket.close(code=4403); return
                if not isinstance(event,dict): continue
                key=event.get('field_key')
                if not isinstance(key,str):continue
                with closing(connect_studies(database_path)) as db, db:
                    state=draft(db,group)
                    if state is None:continue
                    valid={x['stable_key'] for x in state['template']['fields']}
                    if key not in valid:continue
                    if event.get('type')=='lock.acquire':
                        db.execute('BEGIN IMMEDIATE'); existing=db.execute('SELECT student_id FROM group_report_lock WHERE group_id=? AND field_key=?',(group_id,key)).fetchone()
                        if existing and existing[0]!=user: result={'type':'lock.denied','field_key':key}
                        else:
                            name=db.execute('SELECT name FROM student WHERE id=?',(user,)).fetchone(); db.execute('INSERT OR REPLACE INTO group_report_lock VALUES(?,?,?,?,?)',(group_id,key,user,name[0] if name else user,now())); result={'type':'lock.changed','field_key':key,'student_id':user,'student_name':name[0] if name else user}
                    elif event.get('type')=='lock.release':
                        released=db.execute('DELETE FROM group_report_lock WHERE group_id=? AND field_key=? AND student_id=?',(group_id,key,user)).rowcount
                        if not released: result=None
                        else: result={'type':'lock.released','field_key':key}
                    else:continue
                if result: await broadcast(group_id,result)
        except WebSocketDisconnect:pass
        finally:
            async with socket_lock:
                sockets.get(group_id,{}).pop(websocket,None)
                if not sockets.get(group_id):sockets.pop(group_id,None)
                user_still_connected=user in sockets.get(group_id,{}).values()
            released=[]
            if not user_still_connected:
                with closing(connect_studies(database_path)) as db, db:
                    released=[row[0] for row in db.execute('SELECT field_key FROM group_report_lock WHERE group_id=? AND student_id=?',(group_id,user))]
                    db.execute('DELETE FROM group_report_lock WHERE group_id=? AND student_id=?',(group_id,user))
            for key in released: await broadcast(group_id,{'type':'lock.released','field_key':key})
            await broadcast(group_id,{'type':'presence','count':len(sockets.get(group_id,{}))})
    return router
