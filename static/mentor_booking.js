(() => {
  const form = document.querySelector('#mentor-book-form');
  if (!form) return;
  const load = document.querySelector('#mentor-load-slots');
  const start = form.elements.start_time;
  const end = form.elements.end_time;
  const submit = form.querySelector('[type=submit]');
  const error = document.querySelector('#mentor-book-error');
  let slots = [];
  const minutes = value => Number(value.slice(0, 2)) * 60 + Number(value.slice(3));
  const asTime = value => `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
  const showError = message => { error.textContent = message; error.hidden = !message; };
  const rebuildEnds = () => {
    end.innerHTML = '';
    if (!start.value) return;
    let cursor = minutes(start.value) + 30;
    const limit = minutes(start.value) + 180;
    while (cursor <= limit && slots.includes(asTime(cursor - 30))) {
      end.add(new Option(asTime(cursor), asTime(cursor))); cursor += 30;
    }
  };
  start.addEventListener('change', rebuildEnds);
  load.addEventListener('click', async () => {
    showError(''); submit.disabled = true; start.disabled = end.disabled = true;
    const type = form.querySelector('[name=meeting_type]:checked').value;
    const response = await fetch(`${location.pathname}/availability?day=${encodeURIComponent(form.elements.day.value)}&meeting_type=${type}`);
    const data = await response.json();
    if (!response.ok) { showError(data.detail || '空き時間を取得できませんでした。'); return; }
    slots = data.slots; start.innerHTML = '';
    slots.forEach(slot => start.add(new Option(slot, slot)));
    if (!slots.length) { showError('選択した条件に空き時間はありません。'); return; }
    start.disabled = end.disabled = false; rebuildEnds(); submit.disabled = false;
  });
  form.addEventListener('submit', async event => {
    event.preventDefault(); showError(''); submit.disabled = true;
    const response = await fetch(location.pathname, { method: 'POST', body: new FormData(form) });
    const data = await response.json();
    if (response.ok && data.result) { location.href = '/mypage'; return; }
    showError(data.message || '予約できませんでした。'); submit.disabled = false; load.click();
  });
})();
