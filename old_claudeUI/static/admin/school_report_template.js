(() => {
  const root=document.getElementById('school-report-template'); if(!root)return;
  const csrf=root.dataset.csrf, status=document.getElementById('report-template-status');
  const select=document.getElementById('existing-template');
  const previewCard=document.getElementById('template-preview-card');
  const preview=document.getElementById('selected-template-preview');
  let previewTemplates=[];
  try { previewTemplates=JSON.parse(document.getElementById('template-preview-data').textContent); } catch (_) {}
  const renderPreview=()=>{
    const template=previewTemplates.find(item=>String(item.id)===select.value);
    preview.replaceChildren();
    previewCard.hidden=!template;
    if(!template)return;
    const userId=document.createElement('p');
    userId.textContent='ユーザーID：sample-student';
    userId.style.fontSize='11pt';
    preview.append(userId);
    for(const field of template.fields||[]){
      if(!field.hide_heading){
        const heading=document.createElement('h3');
        heading.textContent=field.label||'';
        heading.style.fontSize=`${Math.max(6,Math.min(36,Number(field.heading_font_size)||14))}pt`;
        heading.style.textAlign=field.heading_alignment||'left';
        heading.style.fontWeight=field.heading_bold?'bold':'normal';
        preview.append(heading);
      }
      if(field.type==='image'){
        const grid=document.createElement('div');
        grid.className=`preview-images preview-images-${field.image_size||'small'}`;
        grid.dataset.alignment=field.body_alignment||'left';
        for(let i=1;i<=3;i++){
          const item=document.createElement('div');item.className='preview-image-item';
          const placeholder=document.createElement('div');placeholder.className='preview-image-placeholder';
          placeholder.textContent='画像';
          const caption=document.createElement('p');caption.textContent=`画像${i}のキャプション`;
          caption.style.fontSize=`${Math.max(6,Math.min(36,Number(field.body_font_size)||11))}pt`;
          caption.style.textAlign=field.body_alignment||'left';
          caption.style.fontWeight=field.body_bold?'bold':'normal';
          item.append(placeholder,caption);grid.append(item);
        }
        preview.append(grid);
      }else{
        const body=document.createElement('p');
        let sample='ここに生徒が入力した文章が表示されます。';
        const max=Number(field.max_length);
        if(Number.isFinite(max)&&max>0)sample=Array.from(sample).slice(0,max).join('');
        body.textContent=sample;
        body.style.fontSize=`${Math.max(6,Math.min(36,Number(field.body_font_size)||11))}pt`;
        body.style.textAlign=field.body_alignment||'left';
        body.style.fontWeight=field.body_bold?'bold':'normal';
        preview.append(body);
      }
    }
  };
  select.addEventListener('change',renderPreview);
  renderPreview();
  document.getElementById('apply-template').onclick=async()=>{
    const templateId=Number(select.value);
    if(!templateId){status.textContent='適用するテンプレートを選択してください。';return;}
    const button=document.getElementById('apply-template');button.disabled=true;
    try{
      const response=await fetch('/teacher/school-report/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:csrf,template_id:templateId})});
      const result=await response.json();
      if(!response.ok)throw new Error(result.detail||'テンプレートを適用できませんでした。');
      status.textContent='学校用テンプレートを適用しました。';
      location.reload();
    }catch(error){status.textContent=error.message;}
    finally{button.disabled=false;}
  };
})();
