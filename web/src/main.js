import './style.css';
import { api } from './api.js';
import { Viewer } from './viewer.js';
import { createCropper } from './cropper.js';
import { clientValidate } from './clientValidate.js';

const $ = (s, r = document) => r.querySelector(s);
const h = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'class') e.className = v; else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k === 'html') e.innerHTML = v; else if (v === true) e.setAttribute(k, ''); else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) e.append(c.nodeType ? c : document.createTextNode(c));
  return e;
};
const STEPS = ['Carica', 'Viste e ritagli', 'Controllo', 'Genera 3D', 'Posa e scheletro', 'Deformazioni', 'Ottimizza', 'Scarica'];
const ROLE_SIDE_HINT = {
  front: 'Fronte: il lato SINISTRO anatomico della persona è a DESTRA nell\'immagine', back: 'Retro: il lato sinistro anatomico è a SINISTRA nell\'immagine',
  profile_left: 'Profilo sinistro anatomico: si vede il fianco SINISTRO della persona', profile_right: 'Profilo destro anatomico: si vede il fianco DESTRO della persona',
};
const S = { caps: null, jobs: [], job: null, step: 0, sel: { img: null, view: null }, draft: [], engine: 'meshy', confirm: false, log: '', client: null, joints: null, jsel: 'LeftArm', ov: {}, poll: null };
const app = $('#app');
let viewer = null, vbox = null, mainEl = null, navEl = null, headEl = null;

// ----------------------------------------------------------------------------- avvio
async function boot() {
  try { S.caps = await api.caps(); } catch (e) { if (e.auth) return askToken(); return fatal(e); }
  S.jobs = await api.jobs();
  const last = localStorage.getItem('m3d_job');
  const id = (S.jobs.find((j) => j.id === last) || S.jobs[0])?.id;
  if (id) await openJob(id); else await newJob('babu');
  S.step = +(localStorage.getItem('m3d_step') || 0);
  layout(); render(); startPoll();
}
function fatal(e) { app.replaceChildren(h('main', {}, h('div', { class: 'banner err' }, 'Server non raggiungibile: ' + e.message + '. Avvia il backend (vedi README).'))); }
function askToken() {
  const i = h('input', { type: 'password', placeholder: 'Token di accesso' });
  app.replaceChildren(h('main', {}, h('div', { class: 'card' }, h('h2', {}, 'Accesso'), h('p', { class: 'mute' }, 'Il server richiede il token (MODEL3D_TOKEN).'), i,
    h('button', { class: 'primary', onclick: async () => { try { await api.login(i.value); location.reload(); } catch (e) { alert(e.message); } } }, 'Entra'))));
}
async function openJob(id) { S.job = await api.job(id); await attachVal(); localStorage.setItem('m3d_job', id); S.draft = (S.job.views || []).map((v) => ({ ...v })); S.sel = { img: S.job.images[0]?.id || null, view: null }; S.joints = null; S.client = null; await loadJointsMaybe(); }
async function newJob(name) { const j = await api.newJob(name); S.jobs = await api.jobs(); await openJob(j.id); }
async function attachVal() { const j = S.job; j.__val = null; j.__ready = false; if (j.files?.['validation.json']) { try { j.__val = await api.json(j.id, 'validation.json'); j.__ready = j.__val.ready; } catch {} } }
async function refresh() { S.job = await api.job(S.job.id); await attachVal(); S.jobs = await api.jobs(); }

// ----------------------------------------------------------------------------- layout
function layout() {
  headEl = h('header'); navEl = h('nav', { class: 'steps' }); mainEl = h('main');
  app.replaceChildren(headEl, navEl, mainEl);
  vbox = makeViewerBox();
  renderHeader(); renderNav();
}
function renderHeader() {
  const sel = h('select', { onchange: async (e) => { await openJob(e.target.value); render(); } }, S.jobs.map((j) => h('option', { value: j.id, selected: j.id === S.job.id }, j.name)));
  headEl.replaceChildren(h('h1', {}, 'Foto → Avatar 3D'), sel,
    h('button', { class: 'sm', onclick: async () => { const n = prompt('Nome del personaggio (es. babu)', 'babu'); if (n) { await newJob(n); render(); } } }, '+ Nuovo'),
    h('button', { class: 'sm', onclick: async () => { if (confirm(`Eliminare "${S.job.name}" e tutti i suoi file?`)) { await api.delJob(S.job.id); S.jobs = await api.jobs(); if (S.jobs[0]) await openJob(S.jobs[0].id); else await newJob('babu'); renderHeader(); render(); } } }, 'Elimina'),
    h('span', { class: 'mute' }, `ID ${S.job.id}`));
}
function stepDone(i) {
  const j = S.job, st = j.stages; const val = j.files?.['validation.json'];
  return [j.images.length > 0, j.views.length > 0, j.views.length > 0, st.reconstruct.state === 'done', st.rig.state === 'done', st.export.state === 'done', st.export.state === 'done', st.export.state === 'done' && !!j.outputs.length && j.__ready][i];
}
function renderNav() { navEl.replaceChildren(...STEPS.map((n, i) => h('button', { class: `${i === S.step ? 'cur' : ''} ${stepDone(i) ? 'done' : ''}`, onclick: () => go(i) }, `${i + 1}. ${n}`))); }
function go(i) { S.step = i; localStorage.setItem('m3d_step', i); renderNav(); render(); }
const busy = () => Object.values(S.job.stages).some((s) => s.state === 'queued' || s.state === 'running');

// ----------------------------------------------------------------------------- pannello stato reale
const STATE_IT = { pending: 'In attesa', queued: 'In coda', running: 'In corso', done: 'Completata', error: 'Errore', cancelled: 'Annullata', interrupted: 'Interrotta' };
function stagePanel() {
  const j = S.job; const wrap = h('div', { id: 'stagepanel' });
  for (const s of S.caps.stages) {
    const st = j.stages[s.id];
    wrap.append(h('div', { class: 'stage' }, h('span', { class: `dot ${st.state}` }), h('div', {}, h('b', {}, s.label), h('div', { class: `mute ${st.state === 'error' ? 'err' : ''}` }, `${STATE_IT[st.state]}${st.message && st.state !== 'done' ? ' — ' + st.message : ''}`))));
  }
  if (busy()) wrap.append(h('button', { class: 'sm', onclick: () => api.cancel(j.id) }, 'Annulla'));
  const last = (j.log || []).slice(-8).map((l) => l.m).join('\n'); if (last) wrap.append(h('pre', { class: 'log' }, last));
  return wrap;
}
function updatePanels() { const p = $('#stagepanel'); if (p) p.replaceWith(stagePanel()); renderNav(); }
function startPoll() {
  clearInterval(S.poll);
  S.poll = setInterval(async () => {
    const was = busy(); const prev = JSON.stringify(S.job.stages);
    if (!was && !S._force) return; S._force = false;
    await refresh();
    if (JSON.stringify(S.job.stages) !== prev || busy()) updatePanels();
    if (was && !busy()) { await onFinished(); }
  }, 1500);
}
async function onFinished() { await loadJointsMaybe(); renderHeader(); render(); }
async function runStages(start, end, extra = {}) {
  try { S.job = await api.run(S.job.id, { start, end, options: collectOpts(), ...extra }); S._force = true; updatePanels(); }
  catch (e) { alert(e.message); }
}
function collectOpts() { return S.opts || {}; }
S.opts = { rest_pose: 'A', arm_angle_deg: 40, leg_half_angle_deg: 5, target_height: 1.67, target_tris: 30000, max_texture: 2048, bone_prefix: '', rotate_y_deg: 0 };

// ----------------------------------------------------------------------------- viewer condiviso
function makeViewerBox() {
  const host = h('div', { class: 'viewer-box' }); viewer = new Viewer(host);
  const tog = (label, key) => { const b = h('button', { onclick: () => { viewer.set(key, !viewer.state[key]); b.classList.toggle('on', viewer.state[key]); } }, label); b.classList.toggle('on', viewer.state[key]); return b; };
  host.append(h('div', { class: 'viewer-bar' }, tog('Wireframe', 'wire'), tog('Scheletro', 'skeleton'), tog('Materiale neutro', 'neutral'), tog('Griglia/assi', 'grid')));
  const vb = (l, n) => h('button', { onclick: () => viewer.setView(n) }, l);
  host.append(h('div', { class: 'viewer-bottom' }, vb('Fronte', 'front'), vb('Retro', 'back'), vb('Sinistra', 'left'), vb('Destra', 'right'), vb('¾', 'three_quarter')));
  return host;
}
function photoCompare() {
  const views = S.job.views.filter((v) => v.enabled); if (!views.length) return null;
  const sel = h('select', {}, views.map((v) => h('option', { value: v.id }, `${S.caps.roles[v.role]}`)));
  const op = h('input', { type: 'range', min: 0, max: 100, value: 45 }); let ov = null;
  const show = h('button', { class: 'sm', onclick: () => { if (ov) { ov.remove(); ov = null; show.classList.remove('on'); return; } const v = views.find((x) => x.id === sel.value); ov = h('div', { class: 'photo-overlay' }, h('img', { src: api.url(S.job.id, v.file) })); ov.style.opacity = op.value / 100; vbox.append(ov); show.classList.add('on'); } }, 'Sovrapponi foto');
  op.oninput = () => { if (ov) ov.style.opacity = op.value / 100; };
  sel.onchange = () => { if (ov) { ov.remove(); ov = null; show.click(); } };
  return h('div', { class: 'row' }, h('b', {}, 'Confronto con le foto'), sel, show, h('label', { class: 'f mute' }, 'Opacità', op));
}
function statsGrid(st, file) {
  const items = [['Triangoli', st.triangles?.toLocaleString?.('it')], ['Materiali', st.materials], ['Texture', (st.textures || []).map((t) => (Array.isArray(t) ? t.join('×') : t)).join(', ') || '—'], ['Ossa', st.bones], ['Altezza', st.height != null ? st.height.toFixed(3) + ' m' : '—'], ['File', file != null ? (file / 1048576).toFixed(2) + ' MB' : '—']];
  return h('div', { class: 'stats' }, items.map(([k, v]) => h('div', { class: 'stat' }, h('b', {}, v ?? '—'), h('span', {}, k))));
}
function mountViewer(host) { host.append(vbox); viewer.resize(); }

// ----------------------------------------------------------------------------- render
function render() {
  renderNav(); mainEl.replaceChildren();
  ([step1, step2, step3, step4, step5, step6, step7, step8][S.step])();
}

// 1 — carica
function step1() {
  const inp = h('input', { type: 'file', accept: 'image/*', multiple: true, hidden: true, onchange: (e) => up(e.target.files) });
  const up = async (files) => { if (!files.length) return; try { await api.upload(S.job.id, files); await refresh(); S.sel.img ||= S.job.images[0]?.id; render(); } catch (e) { alert(e.message); } };
  const drop = h('div', { class: 'drop', ondragover: (e) => { e.preventDefault(); drop.classList.add('over'); }, ondragleave: () => drop.classList.remove('over'), ondrop: (e) => { e.preventDefault(); up(e.dataTransfer.files); } },
    h('p', {}, h('b', {}, 'Trascina qui le foto o la tavola di riferimento')), h('p', { class: 'mute' }, 'Una foto, più foto della stessa persona, oppure una tavola con fronte/retro/profilo e dettagli.'), h('button', { class: 'primary', onclick: () => inp.click() }, 'Scegli dalla libreria / file'), inp);
  mainEl.append(h('div', { class: 'card' }, h('h2', {}, '1. Carica immagini'), h('div', { class: 'banner info' }, 'Le foto restano sul tuo server. Vengono inviate a un servizio esterno SOLO se scegli un motore remoto nella fase 4, con tua conferma esplicita.'), drop),
    S.job.images.length ? h('div', { class: 'card' }, h('h3', {}, `${S.job.images.length} immagine/i caricate`), h('div', { class: 'thumbs' }, S.job.images.map((i) => h('div', { class: 'thumb' }, h('img', { src: api.url(S.job.id, i.file) }), `${i.name} (${i.size.join('×')})`))), h('div', { class: 'row' }, h('button', { class: 'primary', onclick: () => go(1) }, 'Avanti: assegna viste e ritagli →'))) : null);
}

// 2 — viste e ritagli
function step2() {
  const j = S.job;
  if (!j.images.length) return void mainEl.append(h('div', { class: 'card' }, 'Carica prima almeno un\'immagine (fase 1).'));
  const img = j.images.find((i) => i.id === S.sel.img) || j.images[0]; S.sel.img = img.id;
  const roleSel = h('select', {}, Object.entries(S.caps.roles).map(([k, v]) => h('option', { value: k }, v)));
  const bg = h('input', { type: 'checkbox', checked: true }); const tol = h('input', { type: 'range', min: 6, max: 80, value: 28 });
  const host = h('div'); let cur = { crop: [0, 0, img.size[0], img.size[1]], exclude: [] }; let editing = S.editing ?? null; if (editing != null && !S.draft[editing]) editing = null;
  const hintFor = () => (ROLE_SIDE_HINT[roleSel.value] || '');
  const cr = createCropper(host, { src: api.url(j.id, img.file), w: img.size[0], h: img.size[1], onChange: (c) => (cur = c), hint: hintFor() });
  roleSel.onchange = () => cr.hint(hintFor());
  const mode = (m) => { cr.mode(m); b1.classList.toggle('on', m === 'crop'); b2.classList.toggle('on', m === 'exclude'); };
  const b1 = h('button', { class: 'on', onclick: () => mode('crop') }, '▭ Ritaglio'), b2 = h('button', { onclick: () => mode('exclude') }, '✕ Escludi scritte/etichette');
  const list = h('div', { class: 'thumbs' });
  const drawList = () => list.replaceChildren(...S.draft.map((v, i) => h('div', { class: `thumb ${editing === i ? 'sel' : ''}` }, v.thumb ? h('img', { src: api.url(j.id, v.thumb) + '?' + (v._t || '') }) : h('div', { class: 'mute', style: 'height:130px;display:grid;place-items:center' }, 'ritaglio non ancora applicato'),
    h('b', {}, S.caps.roles[v.role]), h('div', { class: 'row' }, h('label', { class: 'f' }, h('input', { type: 'checkbox', checked: v.enabled, onchange: (e) => (v.enabled = e.target.checked) }), 'attiva'),
      h('button', { class: 'sm', onclick: () => { S.editing = i; S.sel.img = v.image_id; render(); } }, 'Modifica'), h('button', { class: 'sm', onclick: () => { S.draft.splice(i, 1); drawList(); } }, '🗑')))));
  const addBtn = h('button', { class: 'primary', onclick: () => {
    const v = { image_id: img.id, crop: cur.crop, exclude: cur.exclude, role: roleSel.value, remove_bg: bg.checked, tol: +tol.value, enabled: true, id: editing != null ? S.draft[editing].id : undefined, file: editing != null ? S.draft[editing].file : undefined, thumb: editing != null ? S.draft[editing].thumb : undefined };
    if (editing != null) { S.draft[editing] = v; editing = S.editing = null; } else S.draft.push(v); drawList(); addBtn.textContent = '＋ Aggiungi vista'; } }, editing != null ? '✓ Salva modifica vista' : '＋ Aggiungi vista');
  const apply = h('button', { class: 'primary', onclick: async () => {
    apply.disabled = true; apply.textContent = 'Elaboro i ritagli…';
    try { const out = await api.putViews(j.id, S.draft.map(({ file, thumb, size, foreground_ratio, ...v }) => v)); out.forEach((v) => (v._t = Date.now())); S.draft = out; await refresh(); go(2); } catch (e) { alert(e.message); apply.disabled = false; apply.textContent = 'Applica ritagli'; }
  } }, 'Applica ritagli →');
  const picker = j.images.length > 1 ? h('div', { class: 'thumbs' }, j.images.map((i) => h('div', { class: `thumb ${i.id === img.id ? 'sel' : ''}`, onclick: () => { S.sel.img = i.id; render(); } }, h('img', { src: api.url(j.id, i.file) }), i.name))) : null;
  mainEl.append(h('div', { class: 'grid2' },
    h('div', {}, h('div', { class: 'card' }, h('h2', {}, '2. Viste e ritagli'), picker,
      h('p', { class: 'mute' }, 'Trascina il riquadro azzurro (maniglie ai bordi). Una tavola con più viste: ritaglia UNA persona/vista per volta e assegna il ruolo. Usa "Escludi" per coprire scritte, etichette e cornici.'),
      h('div', { class: 'row' }, b1, b2), h('label', { class: 'f' }, 'Ruolo', roleSel),
      h('label', { class: 'f' }, bg, 'Rimuovi sfondo uniforme'), h('label', { class: 'f mute' }, 'Tolleranza sfondo', tol),
      h('div', { class: 'banner info' }, 'Lato anatomico ≠ lato dell\'immagine: nella vista frontale la SINISTRA della persona (es. smartwatch) compare a DESTRA nell\'immagine.'),
      h('div', { class: 'row' }, addBtn)), h('div', { class: 'card' }, h('h3', {}, 'Viste assegnate'), list, h('div', { class: 'row' }, apply))),
    h('div', { class: 'card' }, host)));
  drawList();
  if (editing != null) { const v = S.draft[editing]; cr.set(v.crop, v.exclude || []); cur = { crop: v.crop, exclude: v.exclude || [] }; roleSel.value = v.role; bg.checked = v.remove_bg; tol.value = v.tol; cr.hint(hintFor()); }
}

// 3 — controllo
function step3() {
  const j = S.job; if (!j.views.length) return void mainEl.append(h('div', { class: 'card' }, 'Assegna e applica almeno una vista (fase 2).'));
  const body = j.views.filter((v) => ['front', 'back', 'profile_left', 'profile_right', 'three_quarter'].includes(v.role) && v.enabled);
  mainEl.append(h('div', { class: 'card' }, h('h2', {}, '3. Controlla soggetto e dettagli'),
    h('p', { class: 'mute' }, 'Verifica che ogni ritaglio contenga SOLO la persona (niente scritte, cornici, altre figure) e che lo sfondo sia rimosso. Se serve, torna ai ritagli.'),
    !body.length ? h('div', { class: 'banner err' }, 'Nessuna vista del corpo attiva (fronte/retro/profilo/tre quarti): senza di esse non si può ricostruire il corpo.') : null,
    h('div', { class: 'thumbs' }, j.views.map((v) => h('div', { class: 'thumb' }, h('img', { src: api.url(j.id, v.thumb) + '?' + Date.now() }), h('b', {}, S.caps.roles[v.role]), h('div', { class: 'mute' }, `${v.size.join('×')} px · soggetto ${(v.foreground_ratio * 100).toFixed(0)}%`, v.enabled ? '' : ' · disattivata'),
      v.foreground_ratio < 0.08 ? h('div', { class: 'warn' }, 'Soggetto quasi assente: sfondo rimosso male?') : v.foreground_ratio > 0.97 && v.remove_bg ? h('div', { class: 'warn' }, 'Sfondo non rimosso: alza la tolleranza') : null))),
    h('div', { class: 'banner info' }, 'Dettagli (viso, capelli, vestiti, accessori) sono conservati come riferimento visivo e per il confronto. I motori integrati NON li usano come input geometrico: la geometria deriva dalle viste del corpo.'),
    h('div', { class: 'row' }, h('button', { onclick: () => go(1) }, '← Modifica ritagli'), h('button', { class: 'primary', onclick: () => go(3) }, 'Avanti: genera 3D →'))));
}

// 4 — genera
function step4() {
  const j = S.job; const engs = S.caps.engines;
  if (!engs.find((e) => e.id === S.engine)) S.engine = engs.find((e) => e.available)?.id || engs[0].id;
  const eng = engs.find((e) => e.id === S.engine);
  const cards = engs.map((e) => h('label', { class: `engine ${e.id === S.engine ? 'sel' : ''} ${e.available ? '' : 'na'}` }, h('input', { type: 'radio', name: 'eng', checked: e.id === S.engine, onchange: () => { S.engine = e.id; S.confirm = false; render(); } }), ' ', h('b', {}, e.label), ' ',
    h('span', { class: `badge ${e.reconstruction ? '' : 'draft'}` }, e.reconstruction ? (e.multiview ? 'multivista' : 'singola foto') : 'NON è una ricostruzione'),
    h('div', { class: 'mute' }, e.needs), e.cost_note ? h('div', { class: 'warn' }, '€ ' + e.cost_note) : null,
    e.uploads_photos ? h('div', { class: 'warn' }, '⚠ Le foto vengono inviate a: ' + e.destination) : h('div', { class: 'mute' }, 'Le foto non lasciano il tuo sistema.'),
    e.available ? h('div', { class: 'ok' }, 'Disponibile') : h('div', { class: 'err' }, 'Non disponibile: ' + e.missing.join('; '))));
  const conf = eng.uploads_photos ? h('label', { class: 'f' }, h('input', { type: 'checkbox', checked: S.confirm, onchange: (e) => { S.confirm = e.target.checked; run.disabled = !canRun(); } }), `Confermo l'invio delle foto a ${eng.destination} e che i costi sono a carico del mio account`) : null;
  const canRun = () => eng.available && !busy() && (!eng.uploads_photos || S.confirm) && (j.views.length || !eng.reconstruction);
  const run = h('button', { class: 'primary', disabled: !canRun(), onclick: () => runStages('reconstruct', 'reconstruct', { engine: eng.id, confirm_external: S.confirm }) }, 'Genera geometria e texture');
  const imp = h('input', { type: 'file', accept: '.glb', hidden: true, onchange: async (e) => { try { S.job = await api.importModel(j.id, e.target.files[0]); await refresh(); render(); } catch (er) { alert(er.message); } } });
  const usage = j.provenance?.usage; const left = h('div', {},
    h('div', { class: 'card' }, h('h2', {}, '4. Genera geometria e texture'), ...cards, conf, h('div', { class: 'row' }, run),
      !eng.reconstruction ? h('div', { class: 'banner warn' }, 'Il manichino di prova serve solo a collaudare l\'app. Il risultato sarà sempre una BOZZA, mai un "avatar pronto".') : null),
    h('div', { class: 'card' }, h('h3', {}, 'Oppure usa un GLB generato altrove'), h('p', { class: 'mute' }, 'Es. TRELLIS.2 eseguito su un altro PC. L\'app non può verificare da quali foto derivi: lo considera una tua dichiarazione.'), h('button', { onclick: () => imp.click() }, 'Importa GLB'), imp),
    h('div', { class: 'card' }, h('h3', {}, 'Stato reale'), stagePanel(),
      usage ? h('div', {}, h('h3', {}, 'Come sono state usate le foto'), ...(usage.notes || []).map((n) => h('div', { class: 'mute' }, '• ' + n))) : null));
  mainEl.append(h('div', { class: 'grid2' }, left, h('div', { id: 'vhost' }))); mountViewer($('#vhost'));
  if (j.files?.['raw/model.glb']) viewer.loadGLB(api.url(j.id, 'raw/model.glb') + '?' + Date.now()).catch((e) => console.warn(e)); else viewer.clear();
}

// 5 — posa e scheletro
async function loadJointsMaybe() {
  const j = S.job; if (!j?.files?.['joints_source.json']) { S.joints = null; return; }
  try { S.joints = await api.json(j.id, 'joints_source.json'); } catch { S.joints = null; }
}
const PARENTS = { Spine: 'Hips', Spine1: 'Spine', Spine2: 'Spine1', Neck: 'Spine2', Head: 'Neck', HeadTop_End: 'Head', LeftShoulder: 'Spine2', LeftArm: 'LeftShoulder', LeftForeArm: 'LeftArm', LeftHand: 'LeftForeArm', RightShoulder: 'Spine2', RightArm: 'RightShoulder', RightForeArm: 'RightArm', RightHand: 'RightForeArm', LeftUpLeg: 'Hips', LeftLeg: 'LeftUpLeg', LeftFoot: 'LeftLeg', LeftToeBase: 'LeftFoot', RightUpLeg: 'Hips', RightLeg: 'RightUpLeg', RightFoot: 'RightLeg', RightToeBase: 'RightFoot' };
function step5() {
  const j = S.job; const o = S.opts; const rr = j.files?.['rig_report.json'], pr = j.files?.['prepare_report.json'];
  const num = (k, label, min, max, step) => h('label', { class: 'f' }, label, h('input', { type: 'number', min, max, step, value: o[k], onchange: (e) => (o[k] = +e.target.value), style: 'width:100px' }));
  const rest = h('select', { onchange: (e) => (o.rest_pose = e.target.value) }, [['A', 'Posa A'], ['T', 'Posa T'], ['keep', 'Mantieni posa originale']].map(([v, l]) => h('option', { value: v, selected: o.rest_pose === v }, l)));
  const rot = h('div', { class: 'row' }, 'Orientamento: ', [0, 90, 180, 270].map((d) => h('button', { class: `sm ${o.rotate_y_deg === d ? 'on' : ''}`, onclick: () => { o.rotate_y_deg = d; render(); } }, `${d}°`)), h('span', { class: 'mute' }, '(il fronte deve guardare +Z; correzione automatica dai piedi se possibile)'));
  const run = h('button', { class: 'primary', disabled: busy() || j.stages.reconstruct.state !== 'done', onclick: () => runStages('prepare', 'rig') }, 'Prepara mesh, scheletro e posa');
  const joint = S.jsel; const J = S.joints;
  const ed = J ? h('div', { class: 'card' }, h('h3', {}, 'Correzione manuale dei giunti'), h('p', { class: 'mute' }, 'Tocca un puntino azzurro nel viewer (o scegli dall\'elenco) e sposta il giunto. Coordinate in metri sulla mesh preparata (+X = sinistra anatomica, +Y su, +Z avanti).'),
    h('select', { onchange: (e) => { S.jsel = e.target.value; render(); } }, Object.keys(J).map((n) => h('option', { value: n, selected: n === joint }, n + (S.ov[n] ? ' ✎' : '')))),
    h('div', { class: 'row' }, ...['x', 'y', 'z'].flatMap((a, ai) => [h('span', {}, a.toUpperCase()), h('button', { class: 'sm', onclick: () => nudge(ai, -0.01) }, '−1cm'), h('button', { class: 'sm', onclick: () => nudge(ai, 0.01) }, '+1cm')])),
    h('div', { class: 'mute' }, 'Posizione: ' + (S.ov[joint] || J[joint]).map((x) => x.toFixed(3)).join(', ')),
    h('div', { class: 'row' }, h('button', { class: 'primary', disabled: busy() || !Object.keys(S.ov).length, onclick: async () => { await api.putJoints(j.id, S.ov); runStages('rig', 'rig'); } }, 'Salva e ricalcola pesi'), h('button', { disabled: busy(), onclick: async () => { S.ov = {}; await api.delJoints(j.id); runStages('rig', 'rig'); } }, 'Annulla correzioni'))) : null;
  function nudge(ai, d) { const p = [...(S.ov[S.jsel] || J[S.jsel])]; p[ai] += d; S.ov[S.jsel] = p; showMarkers(); render(); }
  const warns = [...(pr?.warnings || []), ...(rr?.warnings || [])];
  mainEl.append(h('div', { class: 'grid2' }, h('div', {},
    h('div', { class: 'card' }, h('h2', {}, '5. Pulizia, posa e scheletro'), h('p', { class: 'mute' }, 'Pulisce pavimento/sfondo/frammenti, orienta, scala (altezza modificabile in fase 7), decima con UV, stima i giunti Mixamo, calcola i pesi e converte alla posa di riposo mantenendo coerenti bind pose e inverse bind matrices.'),
      h('label', { class: 'f' }, 'Posa di riposo', rest), num('arm_angle_deg', 'Angolo braccia (posa A, ° dalla verticale)', 20, 60, 1), num('leg_half_angle_deg', 'Apertura gambe (° per lato)', 0, 15, 1), rot, h('div', { class: 'row' }, run), stagePanel()),
    warns.length ? h('div', { class: 'card' }, h('h3', {}, 'Avvisi (da correggere prima dell\'esportazione)'), ...warns.map((w) => h('div', { class: 'banner warn' }, w))) : null,
    pr || rr ? h('div', { class: 'card' }, h('h3', {}, 'Rapporto'), h('pre', { class: 'log' }, JSON.stringify({ pulizia: pr?.clean, orientamento: pr?.facing_estimate, decimazione: pr?.decimation, posa: rr?.pose, stiramento_lati: rr?.stretch_after_pose }, null, 1))) : null, ed),
    h('div', { id: 'vhost' }))); mountViewer($('#vhost'));
  if (j.files?.['preview_prepared.glb']) viewer.loadGLB(api.url(j.id, 'preview_prepared.glb') + '?' + Date.now()).then(() => showMarkers()).catch(console.warn); else viewer.clear();
}
function showMarkers() { if (!S.joints) return viewer.clearMarkers(); const J = { ...S.joints, ...S.ov }; viewer.showJoints(J, PARENTS, S.jsel, (n) => { S.jsel = n; showMarkers(); if (S.step === 4) render(); }); }

// 6 — deformazioni
function step6() {
  const j = S.job; const ok = j.stages.rig.state === 'done';
  const run = h('button', { class: 'primary', disabled: !ok || busy(), onclick: () => runStages('export', 'export') }, 'Crea anteprima con scheletro e valida');
  const info = h('div', { class: 'mute', id: 'refinfo' });
  const refIn = h('input', { type: 'file', accept: '.glb', hidden: true, onchange: async (e) => { const f = e.target.files[0]; if (!f) return; try { await viewer.playReference(URL.createObjectURL(f), (i) => (info.textContent = `Animazione "${i.clip}": ${i.mapped} ossa mappate, non mappate: ${i.unmatched.join(', ') || 'nessuna'} (scheletro sorgente: ${i.sourceBones} ossa)`)); } catch (er) { info.textContent = 'Errore: ' + er.message; } } });
  const poses = [['braccia_sollevate', 'Braccia sollevate'], ['gomiti_piegati', 'Gomiti piegati'], ['torsione_busto', 'Torsione busto'], ['ginocchia_piegate', 'Ginocchia piegate']];
  const left = h('div', {}, h('div', { class: 'card' }, h('h2', {}, '6. Prova delle deformazioni'), h('p', { class: 'mute' }, 'Pose procedurali temporanee (non vengono esportate). Controlla spalle/ascelle, gomiti, bacino e cavallo dei jeans, ginocchia, capelli sulle spalle, smartwatch al polso.'),
    h('div', { class: 'row' }, run), stagePanel(),
    h('div', { class: 'row' }, ...poses.map(([k, l]) => h('button', { disabled: !viewer.skinned, onclick: () => viewer.playPose(k) }, l)), h('button', { disabled: !viewer.skinned, onclick: () => viewer.playWalk() }, 'Passo / camminata'), h('button', { onclick: () => viewer.resetPose() }, 'Torna alla posa di riposo')),
    h('h3', {}, 'Animazione di riferimento del tuo gioco'), h('p', { class: 'mute' }, 'Carica un GLB con skeleton e animazione (es. Mixamo): viene ritargettata per nome osso. È una prova, non una garanzia di compatibilità.'), h('button', { onclick: () => refIn.click() }, 'Carica animazione GLB'), refIn, info, photoCompare()));
  mainEl.append(h('div', { class: 'grid2' }, left, h('div', { id: 'vhost' }))); mountViewer($('#vhost'));
  if (j.outputs.length) viewer.loadGLB(`/api/jobs/${j.id}/glb?` + Date.now()).then((st) => { $('#vstat')?.replaceWith(h('div', { id: 'vstat' }, statsGrid(st))); render6stats(st); }).catch(console.warn); else viewer.clear();
  left.append(h('div', { id: 'vstat' }));
}
function render6stats(st) { const e = $('#vstat'); if (e) e.replaceChildren(statsGrid(st)); }

// 7 — ottimizza
function step7() {
  const j = S.job, o = S.opts; const rep = j.__val;
  const f = (k, label, min, max, step) => h('label', { class: 'f' }, label, h('input', { type: 'number', min, max, step, value: o[k], onchange: (e) => (o[k] = +e.target.value), style: 'width:110px' }));
  const apply = h('button', { class: 'primary', disabled: busy() || j.stages.reconstruct.state !== 'done', onclick: () => runStages('prepare', 'export') }, 'Applica e rigenera (preparazione → rig → export)');
  const st = rep?.stats || {};
  mainEl.append(h('div', { class: 'grid2' }, h('div', {}, h('div', { class: 'card' }, h('h2', {}, '7. Ottimizza'),
    h('p', { class: 'mute' }, 'La decimazione avviene PRIMA del rig (con UV preservate), così i pesi sono calcolati sulla mesh finale e non serve trasferirli. Obiettivo 30.000 triangoli, massimo 50.000.'),
    f('target_tris', 'Triangoli obiettivo', 5000, 50000, 1000), f('max_texture', 'Texture max (px/lato)', 256, 2048, 256), f('target_height', 'Altezza personaggio (m)', 1.2, 2.2, 0.01),
    h('label', { class: 'f' }, 'Prefisso ossa', h('select', { onchange: (e) => (o.bone_prefix = e.target.value) }, [['', 'Hips, Spine… (senza prefisso)'], ['mixamorig', 'mixamorigHips… (stile Mixamo)']].map(([v, l]) => h('option', { value: v, selected: o.bone_prefix === v }, l)))),
    h('div', { class: 'row' }, apply), stagePanel()),
    rep ? h('div', { class: 'card' }, h('h3', {}, 'Risultato misurato sul file'), statsGrid({ triangles: st.triangles, materials: st.materials, textures: st.textures, bones: st.bones, height: st.height_m }, st.file_bytes)) : null),
    h('div', { id: 'vhost' }))); mountViewer($('#vhost'));
  if (j.outputs.length) viewer.loadGLB(`/api/jobs/${j.id}/glb?` + Date.now()).catch(console.warn); else viewer.clear();
}

// 8 — scarica
const CAT = { structural: 'Errori strutturali (verificati automaticamente)', compat: 'Compatibilità con le animazioni (da provare)', visual: 'Qualità visiva e somiglianza (da controllare a occhio)' };
function step8() {
  const j = S.job; const rep = j.__val; const card = h('div', { class: 'card' }, h('h2', {}, '8. Validazione e download'));
  const left = h('div', {}, card);
  if (!j.outputs.length || !rep) card.append(h('div', { class: 'banner warn' }, 'Nessun GLB esportato: completa le fasi precedenti (fase 6 crea il file).'));
  else {
    const ready = rep.ready;
    card.append(h('div', { class: `banner ${ready ? 'info' : 'warn'}` }, h('span', { class: `badge ${rep.verdict}` }, ready ? 'AVATAR PRONTO (controlli strutturali superati)' : 'BOZZA'), ...(!ready ? [h('div', {}, 'Motivi:'), ...rep.reasons.map((r) => h('div', {}, '• ' + r))] : [])));
    for (const cat of ['structural', 'compat', 'visual']) card.append(h('h3', {}, CAT[cat]), ...rep.checks.filter((c) => c.category === cat).map((c) => h('div', { class: 'chk' }, h('i', { class: c.ok ? 'ok' : c.severity === 'error' ? 'err' : c.severity === 'warning' ? 'warn' : 'mute' }, c.ok ? '✓' : c.severity === 'error' ? '✗' : c.severity === 'warning' ? '!' : '?'), c.message)));
    const cc = h('div', { id: 'client' }); card.append(h('h3', {}, 'Riapertura nel browser con GLTFLoader'), cc, h('div', { class: 'row' }, h('button', { onclick: () => runClient() }, 'Riesegui controllo nel browser')));
    const dl = h('a', { href: `/api/jobs/${j.id}/download`, download: '' }, h('button', { class: 'primary', disabled: !ready }, `⬇ Scarica ${rep.file}`));
    const dd = h('a', { href: `/api/jobs/${j.id}/download-draft`, download: '' }, h('button', {}, '⬇ Scarica BOZZA (incompleta)'));
    card.append(h('div', { class: 'row' }, ready ? dl : dd), ready ? null : h('div', { class: 'mute' }, 'Il download come "avatar pronto" è bloccato finché i controlli strutturali non passano e il modello non deriva da una ricostruzione reale.'),
      h('div', { class: 'mute' }, `Destinazione nel gioco: public/avatars/models/${S.job.name.toLowerCase().replace(/\s+/g, '_')}.glb — l'inserimento e il collegamento a "Corpo completo 3D" sono un passaggio separato (repository del gioco non collegato).`));
    card.append(statsGrid({ triangles: rep.stats.triangles, materials: rep.stats.materials, textures: rep.stats.textures, bones: rep.stats.bones, height: rep.stats.height_m }, rep.stats.file_bytes));
    setTimeout(() => runClient(), 100);
    async function runClient() {
      cc.replaceChildren(h('div', { class: 'mute' }, 'Controllo in corso…'));
      try { const r = await clientValidate(`/api/jobs/${j.id}/glb?` + Date.now(), viewer); S.client = r; cc.replaceChildren(...r.checks.map((c) => h('div', { class: 'chk' }, h('i', { class: c.ok ? 'ok' : 'err' }, c.ok ? '✓' : '✗'), c.msg))); if (!r.checks.every((c) => c.ok)) dl.querySelector('button').disabled = true; }
      catch (e) { cc.replaceChildren(h('div', { class: 'banner err' }, 'Il GLB non si riapre con GLTFLoader: ' + e.message)); dl.querySelector('button').disabled = true; }
    }
  }
  mainEl.append(h('div', { class: 'grid2' }, left, h('div', { id: 'vhost' }))); mountViewer($('#vhost')); card.append(photoCompare());
  if (j.outputs.length && !rep) viewer.loadGLB(`/api/jobs/${j.id}/glb?` + Date.now());
}

boot();
