// One anchor follows the displayed order, including folders and filtered results.
let anchor = null;
export function resetSelectionAnchor() { anchor = null; }
export function mountSelection(panel, selected, commit) {
  if (!panel) return;
  const rows = [...panel.querySelectorAll('.file-row')];
  const ids = rows.map(row => row.dataset.id);
  function choose(id, event, checked) {
    if (event.shiftKey && ids.includes(anchor)) {
      const a = ids.indexOf(anchor), b = ids.indexOf(id);
      if (!event.ctrlKey && !event.metaKey) selected.clear();
      ids.slice(Math.min(a,b), Math.max(a,b)+1).forEach(value => selected.add(value));
    } else {
      if (checked === undefined) {
        if (!event.ctrlKey && !event.metaKey) selected.clear();
        checked = !selected.has(id);
      }
      checked ? selected.add(id) : selected.delete(id);
      anchor = id;
    }
    commit();
  }
  panel.addEventListener('click', event => {
    const row = event.target.closest('.file-row');
    if (!row) return;
    const checkbox = event.target.closest('[data-select]');
    if (checkbox) {
      event.stopImmediatePropagation();
      choose(row.dataset.id, event, checkbox.checked);
    } else if ((event.shiftKey || event.ctrlKey || event.metaKey) && !event.target.closest('[data-more]')) {
      event.preventDefault(); event.stopImmediatePropagation(); choose(row.dataset.id, event);
    } else if (!event.target.closest('button,a,input,label')) {
      choose(row.dataset.id, event, true);
    } else if (event.target.closest('[data-open]')) anchor = row.dataset.id;
  }, true);
  panel.addEventListener('pointerdown', event => {
    if (event.button !== 0 || event.pointerType !== 'mouse' || event.target.closest('input,label,[data-more],.table-head,.drop-hint') || (event.target.closest('button,a') && !event.target.closest('[data-open]'))) return;
    const start = { x:event.clientX, y:event.clientY + scrollY }, base = new Set(selected);
    let box, dragging = false, frame, latest = event;
    const additive = event.ctrlKey || event.metaKey || event.shiftKey;
    function paint() {
      if (!dragging) return;
      const end = {x:latest.clientX, y:latest.clientY + scrollY};
      const rect = {left:Math.min(start.x,end.x),right:Math.max(start.x,end.x),top:Math.min(start.y,end.y),bottom:Math.max(start.y,end.y)};
      Object.assign(box.style,{left:`${rect.left}px`,top:`${rect.top-scrollY}px`,width:`${rect.right-rect.left}px`,height:`${rect.bottom-rect.top}px`});
      selected.clear(); if(additive) base.forEach(id => selected.add(id));
      rows.forEach(row => {
        const r = row.getBoundingClientRect();
        if(r.right>rect.left && r.left<rect.right && r.bottom+scrollY>rect.top && r.top+scrollY<rect.bottom) selected.add(row.dataset.id);
        row.classList.toggle('selected',selected.has(row.dataset.id));
        row.querySelector('[data-select]').checked=selected.has(row.dataset.id);
      });
      box.setAttribute('aria-label',`Выбрано: ${selected.size}`);
    }
    function tick() {
      if (latest.clientY<60) scrollBy(0,-14);
      else if(latest.clientY>innerHeight-60) scrollBy(0,14);
      paint(); frame=requestAnimationFrame(tick);
    }
    function move(e) {
      latest=e;
      if(!dragging && Math.hypot(e.clientX-start.x,e.clientY+scrollY-start.y)>6) {
        dragging=true;box=document.createElement('div');box.className='selection-marquee';box.setAttribute('role','status');document.body.append(box);
        document.body.classList.add('selecting-files');frame=requestAnimationFrame(tick);
      }
      if(dragging){e.preventDefault();paint();}
    }
    function finish(e) {
      document.removeEventListener('pointermove',move);document.removeEventListener('pointerup',finish);document.removeEventListener('pointercancel',cancel);
      cancelAnimationFrame(frame);box?.remove();document.body.classList.remove('selecting-files');
      if(dragging) {
        // Consume the click generated after a drag before rebuilding the panel.
        const suppress=click=>{click.preventDefault();click.stopImmediatePropagation();};
        document.addEventListener('click',suppress,{capture:true,once:true});setTimeout(()=>document.removeEventListener('click',suppress,true),0);
        anchor=[...selected].at(-1)||anchor;commit();
      }
    }
    function cancel(e) { selected.clear();base.forEach(id=>selected.add(id));finish(e); }
    document.addEventListener('pointermove',move,{passive:false});document.addEventListener('pointerup',finish);document.addEventListener('pointercancel',cancel);
  });
}
