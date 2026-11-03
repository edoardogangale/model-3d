// Riapre il GLB ESPORTATO con GLTFLoader nel browser e verifica struttura, skin, deformazione reale e visibilità a 360°.
import * as THREE from 'three';
import { Viewer, strip } from './viewer.js';

const REQUIRED = ['Hips','Spine','Spine1','Spine2','Neck','Head','LeftShoulder','LeftArm','LeftForeArm','LeftHand','RightShoulder','RightArm','RightForeArm','RightHand','LeftUpLeg','LeftLeg','LeftFoot','RightUpLeg','RightLeg','RightFoot'];

export async function clientValidate(url, viewer) {
  const out = [];
  const add = (id, ok, msg, sev = 'error') => out.push({ id, ok, msg, sev: ok ? 'info' : sev });
  const buf = await (await fetch(url)).arrayBuffer();
  add('size', buf.byteLength > 0, `Dimensione file: ${(buf.byteLength / 1048576).toFixed(2)} MB`);
  const stats = await viewer.loadGLB(url); // stesso caricamento dell'anteprima = GLTFLoader reale
  const m = viewer.skinned;
  add('skinned', !!m, m ? 'GLTFLoader: SkinnedMesh trovata' : 'GLTFLoader: nessuna SkinnedMesh (file statico)');
  if (!m) return { checks: out, stats };
  const sk = m.skeleton, g = m.geometry;
  add('bones', sk.bones.length >= 20, `Scheletro: ${sk.bones.length} ossa`);
  add('ibm', sk.boneInverses.length === sk.bones.length, `Inverse bind matrices: ${sk.boneInverses.length}/${sk.bones.length}`);
  // coerenza bind: world(bone) * inverse ≈ I alla posa di riposo
  viewer.resetPose(); viewer.root.updateMatrixWorld(true);
  let maxErr = 0; const I = new THREE.Matrix4();
  sk.bones.forEach((b, i) => { I.copy(b.matrixWorld).multiply(sk.boneInverses[i]); maxErr = Math.max(maxErr, ...I.elements.map((e, k) => Math.abs(e - (k % 5 === 0 ? 1 : 0)))); });
  add('bind', maxErr < 1e-4, `Bind pose ↔ IBM coerenti (errore ${maxErr.toExponential(1)})`);
  const names = new Set(sk.bones.map((b) => strip(b.name)));
  const miss = REQUIRED.filter((n) => !names.has(n));
  add('humanoid', !miss.length, miss.length ? `Ossa umanoidi mancanti: ${miss.join(', ')}` : 'Mappatura umanoide completa (20 ossa Mixamo obbligatorie)');
  const si = g.attributes.skinIndex, sw = g.attributes.skinWeight, pos = g.attributes.position;
  let bad = 0, nonfinite = 0, minSum = 9, maxIdx = 0;
  for (let i = 0; i < pos.count; i++) {
    let s = 0; for (let k = 0; k < 4; k++) { s += sw.getComponent(i, k); maxIdx = Math.max(maxIdx, si.getComponent(i, k)); }
    minSum = Math.min(minSum, s); if (Math.abs(s - 1) > 1e-3) bad++;
    if (!isFinite(pos.getX(i) + pos.getY(i) + pos.getZ(i))) nonfinite++;
  }
  add('weights', bad === 0 && minSum > 0.99, `Pesi: ${bad} vertici con somma ≠ 1 (min ${minSum.toFixed(3)}), indici ≤ ${maxIdx} su ${sk.bones.length} ossa`);
  add('finite', nonfinite === 0, nonfinite ? `${nonfinite} vertici non finiti` : 'Nessun valore non finito');
  add('limits', stats.triangles <= 50000 && stats.materials <= 3 && stats.textures.every((t) => Math.max(...t) <= 2048),
    `Triangoli ${stats.triangles}, materiali ${stats.materials}, texture ${stats.textures.map((t) => t.join('×')).join(', ') || '—'}`);
  // deformazione effettiva: ruota LeftArm e verifica che i vertici si muovano nel braccio e non nella testa
  const sample = []; const step = Math.max(1, Math.floor(pos.count / 400)); for (let i = 0; i < pos.count; i += step) sample.push(i);
  const read = () => sample.map((i) => m.applyBoneTransform(i, new THREE.Vector3(pos.getX(i), pos.getY(i), pos.getZ(i))).clone());
  const a = read(); viewer.bones.LeftArm.rotateZ(1.0); viewer.root.updateMatrixWorld(true); m.skeleton.update(); const b = read(); viewer.resetPose();
  let moved = 0, headMoved = 0, headN = 0; const hy = viewer.worldRest('Head').y;
  sample.forEach((vi, k) => { const d = a[k].distanceTo(b[k]); if (d > 0.02) moved++; if (a[k].y > hy + 0.05) { headN++; if (d > 0.01) headMoved++; } });
  add('deform', moved > 5, `Deformazione reale: ${moved}/${sample.length} vertici campione si muovono ruotando il braccio`);
  add('deform_local', headN === 0 || headMoved / headN < 0.05, 'La rotazione del braccio non sposta la testa (pesi localizzati)');
  // 360°
  const cov = []; for (let k = 0; k < 8; k++) cov.push(viewer.snapshot(96, 144, (k * Math.PI) / 4));
  add('view360', Math.min(...cov) > 0.03, `Visibile a 360°: copertura minima ${(Math.min(...cov) * 100).toFixed(1)}% su 8 angolazioni`);
  return { checks: out, stats };
}
