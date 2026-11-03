import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';

export const strip = (n) => n.replace(/^mixamorig:?/, '');
const rotAxis = (axis, ang) => new THREE.Quaternion().setFromAxisAngle(axis.clone().normalize(), ang);
const rad = (d) => (d * Math.PI) / 180;
// ruota la direzione d verso t di `ang`
const toward = (d, t, ang) => rotAxis(new THREE.Vector3().crossVectors(d, t), ang);

export class Viewer {
  constructor(host) {
    this.host = host;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    host.appendChild(this.renderer.domElement);
    this.renderer.domElement.style.touchAction = 'none';
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(35, 1, 0.05, 50);
    this.camera.position.set(0, 1.1, 4.2);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.target.set(0, 0.85, 0);
    this.controls.enableDamping = true;
    this.controls.screenSpacePanning = true;
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x667788, 1.3));
    const d = new THREE.DirectionalLight(0xffffff, 1.6); d.position.set(2, 4, 3); this.scene.add(d);
    const d2 = new THREE.DirectionalLight(0xffffff, 0.6); d2.position.set(-3, 2, -3); this.scene.add(d2);
    this.grid = new THREE.GridHelper(4, 40, 0x888888, 0x444444); this.scene.add(this.grid);
    this.axes = new THREE.AxesHelper(0.5); this.scene.add(this.axes);
    // indicatore degli assi nell'angolo
    this.gizmoScene = new THREE.Scene(); this.gizmoCam = new THREE.PerspectiveCamera(40, 1, 0.1, 10);
    this.gizmoScene.add(new THREE.AxesHelper(1));
    for (const [t, c, p] of [['X', '#e55', [1.25, 0, 0]], ['Y', '#5c5', [0, 1.25, 0]], ['Z', '#58f', [0, 0, 1.25]]]) {
      const cv = document.createElement('canvas'); cv.width = cv.height = 64; const x = cv.getContext('2d');
      x.fillStyle = c; x.font = 'bold 48px sans-serif'; x.textAlign = 'center'; x.textBaseline = 'middle'; x.fillText(t, 32, 34);
      const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(cv), depthTest: false })); s.position.set(...p); s.scale.setScalar(0.5);
      this.gizmoScene.add(s);
    }
    this.root = null; this.skinned = null; this.bones = {}; this.helper = null; this.markers = new THREE.Group(); this.scene.add(this.markers);
    this.state = { wire: false, neutral: false, skeleton: false, grid: true };
    this.anim = null; this.clock = new THREE.Clock(); this.mixer = null;
    this.ro = new ResizeObserver(() => this.resize()); this.ro.observe(host);
    this.resize();
    this.renderer.setAnimationLoop(() => this.tick());
  }
  resize() {
    const w = this.host.clientWidth || 300, h = this.host.clientHeight || 300;
    this.renderer.setSize(w, h, false); this.camera.aspect = w / h; this.camera.updateProjectionMatrix();
  }
  tick() {
    const dt = this.clock.getDelta();
    this.controls.update();
    if (this.anim) this.anim(dt);
    if (this.mixer) this.mixer.update(dt);
    const r = this.renderer, w = this.host.clientWidth, h = this.host.clientHeight;
    r.setViewport(0, 0, w, h); r.setScissorTest(false); r.setClearColor(0x1b2330);
    r.render(this.scene, this.camera);
    const s = Math.min(110, w * 0.3);
    r.setScissorTest(true); r.setViewport(8, 8, s, s); r.setScissor(8, 8, s, s); r.clearDepth();
    this.gizmoCam.position.copy(this.camera.position).sub(this.controls.target).setLength(3.2); this.gizmoCam.lookAt(0, 0, 0);
    r.render(this.gizmoScene, this.gizmoCam); r.setScissorTest(false);
  }
  clear() {
    if (this.root) { this.scene.remove(this.root); this.root.traverse((o) => { o.geometry?.dispose?.(); }); }
    if (this.helper) { this.scene.remove(this.helper); this.helper = null; }
    this.root = null; this.skinned = null; this.bones = {}; this.anim = null; this.mixer = null; this.clearMarkers();
  }
  async loadGLB(url) {
    this.clear();
    const gltf = await new GLTFLoader().loadAsync(url);
    this.gltf = gltf; this.root = gltf.scene; this.scene.add(this.root);
    this.root.traverse((o) => {
      if (o.isSkinnedMesh) this.skinned = this.skinned || o;
      if (o.isMesh) { o.frustumCulled = false; o.userData.mat = o.material; }
      if (o.isBone) this.bones[strip(o.name)] = o;
    });
    this.root.updateMatrixWorld(true);
    this.restQ = {}; for (const [n, b] of Object.entries(this.bones)) this.restQ[n] = b.quaternion.clone();
    this.restP = {}; for (const [n, b] of Object.entries(this.bones)) this.restP[n] = b.position.clone();
    if (this.skinned) { this.helper = new THREE.SkeletonHelper(this.skinned.skeleton.bones[0].parent || this.root); this.helper.visible = this.state.skeleton; this.scene.add(this.helper); }
    this.applyState();
    this.frame();
    return this.stats(gltf);
  }
  frame() {
    const box = new THREE.Box3().setFromObject(this.root); const sz = box.getSize(new THREE.Vector3()), c = box.getCenter(new THREE.Vector3());
    this.controls.target.copy(c); this.setView('three_quarter', sz.y);
  }
  stats(gltf) {
    let tris = 0, verts = 0; const mats = new Set(), texs = [];
    this.root.traverse((o) => {
      if (o.isMesh) {
        const g = o.geometry; tris += (g.index ? g.index.count : g.attributes.position.count) / 3; verts += g.attributes.position.count;
        const m = o.userData.mat; mats.add(m);
        for (const k of ['map', 'normalMap', 'roughnessMap', 'metalnessMap', 'emissiveMap', 'aoMap']) if (m[k]?.image) texs.push([m[k].image.width, m[k].image.height]);
      }
    });
    const box = new THREE.Box3().setFromObject(this.root);
    return { triangles: Math.round(tris), vertices: verts, materials: mats.size, textures: texs, bones: this.skinned?.skeleton.bones.length ?? 0,
      height: box.max.y - box.min.y, skinned: !!this.skinned };
  }
  setView(name, h = 1.7) {
    const t = this.controls.target, d = Math.max(2.6, h * 2.4);
    const dirs = { front: [0, 0, 1], back: [0, 0, -1], left: [-1, 0, 0], right: [1, 0, 0], three_quarter: [0.7, 0.18, 0.7] };
    const v = new THREE.Vector3(...dirs[name]).normalize().multiplyScalar(d);
    this.camera.position.copy(t).add(v); this.camera.lookAt(t); this.controls.update();
  }
  applyState() {
    this.grid.visible = this.axes.visible = this.state.grid;
    if (this.helper) this.helper.visible = this.state.skeleton;
    this.root?.traverse((o) => {
      if (!o.isMesh) return;
      if (this.state.neutral) { o.material = this._neutral ||= new THREE.MeshStandardMaterial({ color: 0xc8ccd2, roughness: 0.8 }); }
      else o.material = o.userData.mat;
      o.material.wireframe = this.state.wire;
    });
    if (this.state.neutral) this._neutral.wireframe = this.state.wire;
  }
  set(k, v) { this.state[k] = v; this.applyState(); }
  // ------------------------------------------------------------- pose
  resetPose() {
    this.anim = null; this.mixer = null;
    for (const [n, b] of Object.entries(this.bones)) { b.quaternion.copy(this.restQ[n]); b.position.copy(this.restP[n]); }
    this.root?.updateMatrixWorld(true);
  }
  worldRest(name) { // posizione di riposo del giunto (somma delle traslazioni locali)
    const b = this.bones[name]; const p = new THREE.Vector3(); let o = b; while (o && o.isBone) { p.add(this.restP[strip(o.name)] || o.position); o = o.parent; } return p;
  }
  poseTargets(name) {
    const W = (n) => this.worldRest(n), q = {};
    const dirs = (a, b) => W(b).clone().sub(W(a)).normalize();
    const Z = new THREE.Vector3(0, 0, 1), nZ = new THREE.Vector3(0, 0, -1), down = new THREE.Vector3(0, -1, 0);
    for (const [side, s] of [['Left', 1], ['Right', -1]]) {
      if (name === 'braccia_sollevate') q[`${side}Arm`] = rotAxis(new THREE.Vector3(0, 0, 1), s * rad(60));
      if (name === 'gomiti_piegati') q[`${side}ForeArm`] = toward(dirs(`${side}ForeArm`, `${side}Hand`), Z, rad(100));
      if (name === 'ginocchia_piegate') { q[`${side}Leg`] = toward(down, nZ, rad(70)); q[`${side}UpLeg`] = toward(down, Z, rad(35)); }
    }
    if (name === 'torsione_busto') { q.Spine = rotAxis(new THREE.Vector3(0, 1, 0), rad(15)); q.Spine1 = rotAxis(new THREE.Vector3(0, 1, 0), rad(15)); q.Spine2 = rotAxis(new THREE.Vector3(0, 1, 0), rad(10)); }
    if (name === 'passo') {
      q.LeftUpLeg = toward(down, Z, rad(28)); q.RightUpLeg = toward(down, nZ, rad(22)); q.RightLeg = toward(down, nZ, rad(30));
      q.LeftArm = rotAxis(new THREE.Vector3(1, 0, 0), rad(-20)); q.RightArm = rotAxis(new THREE.Vector3(1, 0, 0), rad(20));
    }
    return q;
  }
  playPose(name, secs = 0.7) {
    this.resetPose(); const tg = this.poseTargets(name); let t = 0;
    this.anim = (dt) => {
      t = Math.min(1, t + dt / secs); const e = t * t * (3 - 2 * t);
      for (const [n, q] of Object.entries(tg)) if (this.bones[n]) this.bones[n].quaternion.copy(this.restQ[n]).slerp(new THREE.Quaternion().copy(this.restQ[n]).premultiply(q), e);
      if (t >= 1) this.anim = null;
    };
  }
  playWalk() {
    this.resetPose(); let t = 0; const Z = new THREE.Vector3(0, 0, 1), nZ = new THREE.Vector3(0, 0, -1), down = new THREE.Vector3(0, -1, 0);
    this.anim = (dt) => {
      t += dt * 4.2; const a = Math.sin(t), b = Math.sin(t + Math.PI);
      const leg = (side, ph) => { const f = toward(down, ph > 0 ? Z : nZ, rad(30) * Math.abs(ph)); this.bones[`${side}UpLeg`].quaternion.copy(this.restQ[`${side}UpLeg`]).premultiply(f);
        const k = toward(down, nZ, rad(45) * Math.max(0, -ph)); this.bones[`${side}Leg`].quaternion.copy(this.restQ[`${side}Leg`]).premultiply(k); };
      leg('Left', a); leg('Right', b);
      this.bones.LeftArm.quaternion.copy(this.restQ.LeftArm).premultiply(rotAxis(new THREE.Vector3(1, 0, 0), rad(-22) * a));
      this.bones.RightArm.quaternion.copy(this.restQ.RightArm).premultiply(rotAxis(new THREE.Vector3(1, 0, 0), rad(-22) * b));
      this.bones.Hips.position.y = this.restP.Hips.y + 0.015 * Math.abs(Math.cos(t));
      this.bones.Spine1.quaternion.copy(this.restQ.Spine1).premultiply(rotAxis(new THREE.Vector3(0, 1, 0), rad(6) * a));
    };
  }
  async playReference(url, onInfo) {
    const ref = await new GLTFLoader().loadAsync(url);
    if (!ref.animations.length) throw new Error('Il file di riferimento non contiene animazioni.');
    let srcMesh = null; ref.scene.traverse((o) => { if (o.isSkinnedMesh) srcMesh = srcMesh || o; });
    if (!srcMesh) throw new Error('Il file di riferimento non ha uno scheletro.');
    const { retargetClip } = await import('three/examples/jsm/utils/SkeletonUtils.js');
    const names = {}; const srcNames = new Set(srcMesh.skeleton.bones.map((b) => b.name));
    const unmatched = [];
    for (const b of this.skinned.skeleton.bones) {
      const s = strip(b.name); const cand = [b.name, s, 'mixamorig' + s, 'mixamorig:' + s].find((c) => srcNames.has(c));
      if (cand) names[b.name] = cand; else unmatched.push(s);
    }
    const hip = this.skinned.skeleton.bones.find((b) => strip(b.name) === 'Hips')?.name;
    const clip = retargetClip(this.skinned, srcMesh, ref.animations[0], { names, hip: names[hip] ?? hip, useFirstFramePosition: true, preserveBoneMatrix: true, preserveBonePositions: true });
    this.resetPose(); this.mixer = new THREE.AnimationMixer(this.skinned); this.mixer.clipAction(clip).play();
    onInfo?.({ clip: ref.animations[0].name, mapped: Object.keys(names).length, unmatched, sourceBones: srcNames.size });
  }
  // ------------------------------------------------------------- marker giunti
  clearMarkers() { while (this.markers.children.length) { const c = this.markers.children.pop(); c.geometry?.dispose(); } }
  showJoints(J, parents, selected, onPick) {
    this.clearMarkers();
    const pts = {};
    for (const [n, p] of Object.entries(J)) {
      const m = new THREE.Mesh(new THREE.SphereGeometry(n === selected ? 0.022 : 0.014), new THREE.MeshBasicMaterial({ color: n === selected ? 0xffcc00 : 0x33ddff, depthTest: false }));
      m.position.set(...p); m.renderOrder = 10; m.userData.name = n; this.markers.add(m); pts[n] = m.position;
    }
    const lp = []; for (const [n, par] of Object.entries(parents)) if (par && pts[n] && pts[par]) lp.push(pts[par], pts[n]);
    const l = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(lp), new THREE.LineBasicMaterial({ color: 0xff8844, depthTest: false })); l.renderOrder = 9; this.markers.add(l);
    this._pick = onPick;
    if (!this._pickBound) {
      this._pickBound = true; const ray = new THREE.Raycaster(); const el = this.renderer.domElement; let down = null;
      el.addEventListener('pointerdown', (e) => { down = [e.clientX, e.clientY]; });
      el.addEventListener('pointerup', (e) => {
        if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 6 || !this.markers.children.length) return;
        const r = el.getBoundingClientRect(); ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), this.camera);
        ray.params.Line = { threshold: 0 };
        const hit = ray.intersectObjects(this.markers.children.filter((c) => c.isMesh), false)[0]; if (hit && this._pick) this._pick(hit.object.userData.name);
      });
    }
  }
  snapshot(w = 160, h = 240, az = 0) { // render offscreen per il controllo a 360°
    const r = new THREE.WebGLRenderer({ antialias: false, preserveDrawingBuffer: true }); r.setSize(w, h); r.setClearColor(0x000000);
    const cam = new THREE.PerspectiveCamera(30, w / h, 0.05, 50); const box = new THREE.Box3().setFromObject(this.root); const c = box.getCenter(new THREE.Vector3());
    cam.position.set(c.x + 4.2 * Math.sin(az), c.y, c.z + 4.2 * Math.cos(az)); cam.lookAt(c);
    const hid = [this.grid, this.axes, this.markers, this.helper].filter(Boolean).map((o) => [o, o.visible]); hid.forEach(([o]) => (o.visible = false));
    const oldBg = this.scene.background; this.scene.background = new THREE.Color(0x000000);
    r.render(this.scene, cam); const px = new Uint8Array(w * h * 4); const gl = r.getContext(); gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px);
    let lit = 0; for (let i = 0; i < px.length; i += 4) if (px[i] + px[i + 1] + px[i + 2] > 12) lit++;
    this.scene.background = oldBg; hid.forEach(([o, v]) => (o.visible = v)); r.dispose();
    return lit / (w * h);
  }
}
