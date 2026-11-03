const j = async (r) => {
  if (r.status === 401) { const e = new Error('auth'); e.auth = true; throw e; }
  if (!r.ok) { let d = r.statusText; try { d = (await r.json()).detail || d; } catch {} const e = new Error(d); e.status = r.status; throw e; }
  return r.status === 204 ? null : r.json();
};
const opt = (m, b) => ({ method: m, credentials: 'same-origin', headers: b ? { 'content-type': 'application/json' } : undefined, body: b ? JSON.stringify(b) : undefined });
export const api = {
  health: () => fetch('/api/health').then(j),
  login: (token) => fetch('/api/login', opt('POST', { token })).then(j),
  caps: () => fetch('/api/capabilities', opt('GET')).then(j),
  jobs: () => fetch('/api/jobs', opt('GET')).then(j),
  newJob: (name) => fetch('/api/jobs', opt('POST', { name })).then(j),
  job: (id) => fetch(`/api/jobs/${id}`, opt('GET')).then(j),
  delJob: (id) => fetch(`/api/jobs/${id}`, opt('DELETE')).then(j),
  upload: (id, files) => { const f = new FormData(); [...files].forEach((x) => f.append('files', x)); return fetch(`/api/jobs/${id}/images`, { method: 'POST', body: f, credentials: 'same-origin' }).then(j); },
  putViews: (id, views) => fetch(`/api/jobs/${id}/views`, opt('PUT', views)).then(j),
  run: (id, body) => fetch(`/api/jobs/${id}/run`, opt('POST', body)).then(j),
  cancel: (id) => fetch(`/api/jobs/${id}/cancel`, opt('POST', {})).then(j),
  importModel: (id, file) => { const f = new FormData(); f.append('file', file); return fetch(`/api/jobs/${id}/import-model`, { method: 'POST', body: f, credentials: 'same-origin' }).then(j); },
  putJoints: (id, overrides) => fetch(`/api/jobs/${id}/joints`, opt('PUT', { overrides })).then(j),
  delJoints: (id) => fetch(`/api/jobs/${id}/joints`, opt('DELETE')).then(j),
  json: (id, path) => fetch(`/api/jobs/${id}/file/${path}`, opt('GET')).then(j),
  url: (id, path) => `/api/jobs/${id}/file/${path}`,
};
