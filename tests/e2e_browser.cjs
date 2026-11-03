const { chromium } = require('playwright');
const S = require('os').tmpdir(); // richiede $TMPDIR/board.png: python tests/make_board.py
(async () => {
  const b = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const ctx = await b.newContext({ viewport: { width: 1180, height: 820 }, hasTouch: true });
  const p = await ctx.newPage(); const errs = [];
  p.on('console', (m) => { if (['error', 'warning'].includes(m.type())) errs.push(m.type() + ': ' + m.text()); }); p.on('pageerror', (e) => errs.push('PAGEERR ' + e.message));
  await p.goto('http://localhost:8000/'); await p.waitForSelector('nav.steps');
  await p.setInputFiles('input[type=file]', S + '/board.png'); await p.waitForSelector('.thumb img');
  await p.screenshot({ path: S + '/s1.png' });
  await p.click('nav.steps button:nth-child(2)'); await p.waitForSelector('.crop-wrap');
  // ritaglia la prima figura
  const box = await p.locator('.crop-wrap').boundingBox(); const k = box.width / 1200;
  await p.locator('.crop-h.nw').dragTo(p.locator('.crop-wrap'), { sourcePosition: { x: 15, y: 15 }, targetPosition: { x: 100 * k, y: 100 * k } });
  await p.locator('.crop-h.se').dragTo(p.locator('.crop-wrap'), { targetPosition: { x: 330 * k, y: 760 * k } });
  await p.selectOption('select >> nth=1', 'front').catch(()=>{});
  await p.click('text=＋ Aggiungi vista');
  await p.click('text=Applica ritagli'); await p.waitForSelector('text=soggetto', { timeout: 20000 });
  await p.screenshot({ path: S + '/s3.png' });
  await p.click('nav.steps button:nth-child(4)'); await p.waitForSelector('.engine');
  await p.locator('.engine', { hasText: 'Manichino' }).click();
  await p.getByRole('button', { name: 'Genera geometria e texture' }).click(); await p.waitForSelector('.dot.done', { timeout: 60000 }); await p.waitForTimeout(2500);
  await p.screenshot({ path: S + '/s4.png' });
  await p.click('nav.steps button:nth-child(5)'); await p.click('text=Prepara mesh, scheletro e posa');
  await p.waitForFunction(() => document.querySelectorAll('.dot.done').length >= 2, null, { timeout: 90000 }); await p.waitForTimeout(3000);
  await p.screenshot({ path: S + '/s5.png' });
  await p.click('nav.steps button:nth-child(6)'); await p.click('text=Crea anteprima con scheletro e valida');
  await p.waitForFunction(() => document.querySelectorAll('.dot.done').length >= 4, null, { timeout: 90000 }); await p.waitForTimeout(3000);
  await p.click('nav.steps button:nth-child(6)'); await p.waitForTimeout(2500);
  await p.screenshot({ path: S + '/s6a.png' });
  for (const t of ['Braccia sollevate', 'Gomiti piegati', 'Ginocchia piegate', 'Torsione busto']) { await p.click('text=' + t); await p.waitForTimeout(1200); await p.screenshot({ path: S + '/s6_' + t.split(' ')[0] + '.png' }); }
  await p.click('nav.steps button:nth-child(8)'); await p.waitForSelector('text=Riapertura nel browser'); await p.waitForTimeout(6000);
  await p.screenshot({ path: S + '/s8.png', fullPage: true });
  console.log('CLIENT:', await p.locator('#client').innerText());
  console.log('ERRS:', errs.slice(0, 8));
  await b.close();
})().catch((e) => { console.error('FAIL', e); process.exit(1); });
