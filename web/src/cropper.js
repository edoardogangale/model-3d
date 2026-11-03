// Ritaglio touch-friendly: rettangolo di ritaglio con maniglie + rettangoli di esclusione (scritte, etichette, cornici).
export function createCropper(host, { src, w, h, onChange, hint = '' }) {
  host.innerHTML = '';
  const wrap = document.createElement('div'); wrap.className = 'crop-wrap'; wrap.style.aspectRatio = `${w}/${h}`;
  const img = document.createElement('img'); img.src = src; img.draggable = false; wrap.appendChild(img);
  const sel = document.createElement('div'); sel.className = 'crop-sel'; wrap.appendChild(sel);
  const lab = document.createElement('div'); lab.className = 'crop-hint'; sel.appendChild(lab);
  const hs = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'].map((k) => { const d = document.createElement('div'); d.className = `crop-h ${k}`; d.dataset.k = k; sel.appendChild(d); return d; });
  host.appendChild(wrap);
  const S = { crop: [0, 0, w, h], excl: [], mode: 'crop' };
  const sc = () => w / wrap.clientWidth;
  const pct = (r) => ({ left: `${(r[0] / w) * 100}%`, top: `${(r[1] / h) * 100}%`, width: `${(r[2] / w) * 100}%`, height: `${(r[3] / h) * 100}%` });
  const draw = () => {
    Object.assign(sel.style, pct(S.crop)); sel.classList.toggle('off', S.mode !== 'crop');
    wrap.querySelectorAll('.crop-ex').forEach((n) => n.remove());
    S.excl.forEach((r, i) => { const d = document.createElement('div'); d.className = 'crop-ex'; Object.assign(d.style, pct(r)); d.dataset.i = i; d.title = 'Tocca per rimuovere'; wrap.appendChild(d); });
    lab.textContent = hint;
  };
  const emit = () => { draw(); onChange?.({ crop: S.crop.map(Math.round), exclude: S.excl.map((r) => r.map(Math.round)) }); };
  let drag = null;
  wrap.addEventListener('pointerdown', (e) => {
    const r = wrap.getBoundingClientRect(); const px = (e.clientX - r.left) * sc(), py = (e.clientY - r.top) * sc();
    if (e.target.classList.contains('crop-ex') && S.mode === 'exclude') { S.excl.splice(+e.target.dataset.i, 1); emit(); return; }
    wrap.setPointerCapture(e.pointerId);
    if (S.mode === 'crop') {
      const k = e.target.dataset?.k;
      if (k) drag = { k, start: [px, py], rect: [...S.crop] };
      else if (e.target === sel || sel.contains(e.target)) drag = { k: 'move', start: [px, py], rect: [...S.crop] };
      else { S.crop = [px, py, 8, 8]; drag = { k: 'se', start: [px, py], rect: [...S.crop] }; }
    } else { S.excl.push([px, py, 4, 4]); drag = { k: 'new', start: [px, py], rect: null, idx: S.excl.length - 1 }; }
    e.preventDefault();
  });
  wrap.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const r = wrap.getBoundingClientRect(); const px = Math.max(0, Math.min(w, (e.clientX - r.left) * sc())), py = Math.max(0, Math.min(h, (e.clientY - r.top) * sc()));
    const dx = px - drag.start[0], dy = py - drag.start[1];
    if (drag.k === 'new') { const [sx, sy] = drag.start; S.excl[drag.idx] = [Math.min(sx, px), Math.min(sy, py), Math.abs(px - sx), Math.abs(py - sy)]; emit(); return; }
    let [x, y, cw, ch] = drag.rect; const k = drag.k;
    if (k === 'move') { x = Math.max(0, Math.min(w - cw, x + dx)); y = Math.max(0, Math.min(h - ch, y + dy)); }
    else {
      let x2 = x + cw, y2 = y + ch;
      if (k.includes('w')) x = Math.min(x2 - 24, Math.max(0, x + dx)); if (k.includes('e')) x2 = Math.max(x + 24, Math.min(w, x2 + dx));
      if (k.includes('n')) y = Math.min(y2 - 24, Math.max(0, y + dy)); if (k.includes('s')) y2 = Math.max(y + 24, Math.min(h, y2 + dy));
      cw = x2 - x; ch = y2 - y;
    }
    S.crop = [x, y, cw, ch]; emit();
  });
  const up = () => { if (drag?.k === 'new') { const r = S.excl[drag.idx]; if (r[2] < 6 || r[3] < 6) S.excl.splice(drag.idx, 1); emit(); } drag = null; };
  wrap.addEventListener('pointerup', up); wrap.addEventListener('pointercancel', up);
  draw();
  return {
    set(c, ex = []) { S.crop = [...c]; S.excl = ex.map((r) => [...r]); draw(); },
    mode(m) { S.mode = m; wrap.dataset.mode = m; draw(); },
    hint(t) { hint = t; draw(); },
    get: () => ({ crop: S.crop.map(Math.round), exclude: S.excl.map((r) => r.map(Math.round)) }),
  };
}
