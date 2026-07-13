// Robot Wars — permutation arena viewer.
// Loads all 36 Blender-exported GLB robots (base64-inlined by build.mjs as
// window.ROBOT_GLB) into a three.js arena styled after engine/config.py.

import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';

// --- game data (mirrors robot-wars/engine/config.py) ------------------------
const ARENA_W = 1280, ARENA_H = 768;
const MODEL_SCALE = 10; // models built at 1m = 10 game units (radius 12 -> 1.2m)

const CURVES = { hp: 200, speed: 7.0, damage: 8, range: 110, agility: 34.0 };
const WEAPON_ARC = 16.0, COOLDOWN = 16;

const SIZES = {
  small:  { radius: 12, hp_mult: 0.82, speed_mult: 1.18, note: 'hard to hit + nippy, but fragile' },
  medium: { radius: 16, hp_mult: 1.00, speed_mult: 1.00, note: 'the balanced chassis' },
  large:  { radius: 22, hp_mult: 1.26, speed_mult: 0.84, note: 'tanky, but a fat target' },
};
const GUNS = {
  laser:   { dmg: 1.0, range: 1.0, arc: 1.0, cd: 1.0, turn: 1.0, multi: false, note: 'the all-rounder — classic numbers' },
  cannon:  { dmg: 2.0, range: 1.15, arc: 0.5, cd: 2.0, turn: 0.88, multi: false, note: 'huge hits, half the arc, double reload' },
  shotgun: { dmg: 0.55, range: 0.45, arc: 2.75, cd: 1.25, turn: 1.0, multi: true, note: 'short reach, wide arc, hits every enemy in the cone' },
};
const ENGINES = {
  standard: { hp: 1.0, speed: 1.0, turn: 1.0, note: 'the classic drivetrain' },
  sprint:   { hp: 0.88, speed: 1.18, turn: 1.08, note: 'faster legs, thinner plating' },
  tank:     { hp: 1.18, speed: 0.85, turn: 0.85, note: 'slow + tough, burns off traps 2x faster' },
  hover:    { hp: 0.85, speed: 1.0, turn: 1.05, note: 'skims pits, water and ice — light frame costs HP' },
};
// walls from config.WALLS (x, y, w, h in game units, top-left origin)
const WALLS = [
  [ARENA_W * 0.5 - 100, ARENA_H * 0.5 - 18, 200, 36],
  [ARENA_W * 0.26 - 18, ARENA_H * 0.28 - 70, 36, 140],
  [ARENA_W * 0.74 - 18, ARENA_H * 0.72 - 70, 36, 140],
];

// resolve_stats with 0 loadout points — the baseline body every build shares
function resolveStats(size, gun, engine) {
  const sz = SIZES[size], gn = GUNS[gun], en = ENGINES[engine];
  return {
    'Max HP': Math.floor(CURVES.hp * sz.hp_mult * en.hp),
    'Speed': (CURVES.speed * sz.speed_mult * en.speed).toFixed(1) + ' u/tick',
    'Damage': Math.round(CURVES.damage * gn.dmg) + (gn.multi ? ' (per enemy in cone)' : ''),
    'Range': Math.round(CURVES.range * gn.range) + ' u',
    'Aim arc': '±' + (WEAPON_ARC * gn.arc).toFixed(1) + '°',
    'Reload': Math.round(COOLDOWN * gn.cd) + ' ticks',
    'Turn rate': (CURVES.agility * en.turn * gn.turn).toFixed(1) + '°/tick',
    'Radius': sz.radius + ' u',
  };
}

// game (x, y) top-left -> world (x, z) centred
const gx = (x) => x - ARENA_W / 2;
const gz = (y) => y - ARENA_H / 2;

// --- scene -------------------------------------------------------------------
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(innerWidth, innerHeight);
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.1;
document.getElementById('stage').appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0a0c12);
scene.fog = new THREE.Fog(0x0a0c12, 1400, 3000);
// metallic PBR materials from Blender need an environment to reflect
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
scene.environmentIntensity = 0.55;

const camera = new THREE.PerspectiveCamera(50, innerWidth / innerHeight, 1, 6000);
camera.position.set(0, 620, 720);

const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, 20, 0);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.maxPolarAngle = Math.PI * 0.49;
controls.minDistance = 80;
controls.maxDistance = 2400;

scene.add(new THREE.HemisphereLight(0xbdc8e8, 0x30343f, 1.4));
const sun = new THREE.DirectionalLight(0xfff4e0, 3.4);
sun.position.set(500, 900, 300);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
const scam = sun.shadow.camera;
scam.left = -900; scam.right = 900; scam.top = 700; scam.bottom = -700; scam.far = 2500;
scene.add(sun);
const rim = new THREE.DirectionalLight(0x4466ff, 0.8);
rim.position.set(-600, 400, -500);
scene.add(rim);

// --- arena -------------------------------------------------------------------
function gridTexture() {
  const c = document.createElement('canvas');
  c.width = 1280; c.height = 768;
  const g = c.getContext('2d');
  g.fillStyle = '#14161d';
  g.fillRect(0, 0, c.width, c.height);
  g.strokeStyle = '#232733';
  g.lineWidth = 2;
  for (let x = 0; x <= c.width; x += 64) { g.beginPath(); g.moveTo(x, 0); g.lineTo(x, c.height); g.stroke(); }
  for (let y = 0; y <= c.height; y += 64) { g.beginPath(); g.moveTo(0, y); g.lineTo(c.width, y); g.stroke(); }
  return new THREE.CanvasTexture(c);
}

const floor = new THREE.Mesh(
  new THREE.PlaneGeometry(ARENA_W, ARENA_H),
  new THREE.MeshStandardMaterial({ map: gridTexture(), roughness: 0.9, metalness: 0.1 }));
floor.rotation.x = -Math.PI / 2;
floor.receiveShadow = true;
scene.add(floor);

// outer apron so the arena doesn't float in the void
const apron = new THREE.Mesh(
  new THREE.PlaneGeometry(ARENA_W * 3, ARENA_H * 4),
  new THREE.MeshStandardMaterial({ color: 0x07080c, roughness: 1 }));
apron.rotation.x = -Math.PI / 2;
apron.position.y = -0.5;
scene.add(apron);

const wallMat = new THREE.MeshStandardMaterial({ color: 0x2a2f3d, roughness: 0.5, metalness: 0.7 });
const glowMat = new THREE.MeshStandardMaterial({
  color: 0x101418, emissive: 0xff3324, emissiveIntensity: 1.6 });

function addBox(w, h, d, x, y, z, mat) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
  m.position.set(x, y, z);
  m.castShadow = m.receiveShadow = true;
  scene.add(m);
  return m;
}
// perimeter walls with a glowing warning strip on top
const WT = 14, WH = 46;
addBox(ARENA_W + WT * 2, WH, WT, 0, WH / 2, -ARENA_H / 2 - WT / 2, wallMat);
addBox(ARENA_W + WT * 2, WH, WT, 0, WH / 2, ARENA_H / 2 + WT / 2, wallMat);
addBox(WT, WH, ARENA_H, -ARENA_W / 2 - WT / 2, WH / 2, 0, wallMat);
addBox(WT, WH, ARENA_H, ARENA_W / 2 + WT / 2, WH / 2, 0, wallMat);
addBox(ARENA_W + WT * 2, 3, WT, 0, WH + 1.5, -ARENA_H / 2 - WT / 2, glowMat);
addBox(ARENA_W + WT * 2, 3, WT, 0, WH + 1.5, ARENA_H / 2 + WT / 2, glowMat);
addBox(WT, 3, ARENA_H, -ARENA_W / 2 - WT / 2, WH + 1.5, 0, glowMat);
addBox(WT, 3, ARENA_H, ARENA_W / 2 + WT / 2, WH + 1.5, 0, glowMat);
// cover walls from config.WALLS
for (const [x, y, w, h] of WALLS) {
  addBox(w, 38, h, gx(x + w / 2), 19, gz(y + h / 2), wallMat);
}

// --- robots --------------------------------------------------------------------
const loader = new GLTFLoader();
const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
const robots = []; // { root, name, size, gun, engine, home, label }
let selected = null;

function labelSprite(text) {
  const c = document.createElement('canvas');
  c.width = 512; c.height = 96;
  const g = c.getContext('2d');
  g.font = 'bold 44px system-ui, sans-serif';
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  g.fillStyle = 'rgba(8,10,14,0.65)';
  const w = g.measureText(text).width + 40;
  g.beginPath();
  g.roundRect((512 - w) / 2, 12, w, 72, 16);
  g.fill();
  g.fillStyle = '#dfe6f5';
  g.fillText(text, 256, 50);
  const tex = new THREE.CanvasTexture(c);
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false }));
  s.scale.set(150, 28, 1);
  return s;
}

function base64ToBuffer(b64) {
  const bin = atob(b64);
  const buf = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return buf.buffer;
}

const sizes = Object.keys(SIZES), guns = Object.keys(GUNS), engines = Object.keys(ENGINES);
const COLS = 9, COL_GAP = 128, ROW_GAP = 168;

// spinner melee attachments (the game's `shape` axis) — cycled for variety
const SPINNER_TPL = {};
const SPIN_AXIS = { tank: ['x', 18], speeder: ['y', 22], orb: ['z', 26], spike: ['x', 15] };
const SHAPES = ['tank', 'speeder', 'orb', 'spike'];
function loadSpinners() {
  return Promise.all(Object.entries(window.SPINNER_GLB || {}).map(([shape, b64]) =>
    new Promise((res) => loader.parse(base64ToBuffer(b64), '',
      (g) => { SPINNER_TPL[shape] = g.scene; res(); }, () => res()))));
}

// simple autonomous drivers: everyone patrols the arena; sprint engines CHASE
const AGENT_SPEED = { standard: 62, sprint: 100, tank: 40, hover: 72 };   // u/s
const AGENT_TURN = { standard: 1.8, sprint: 2.6, tank: 1.2, hover: 2.0 }; // rad/s
function insideWall(x, z, pad) {
  const m = pad || 26;
  if (Math.abs(x) > ARENA_W / 2 - 70 || Math.abs(z) > ARENA_H / 2 - 70) return true;
  for (const [wx, wy, ww, wh] of WALLS) {
    const x0 = gx(wx) - m, z0 = gz(wy) - m;
    if (x > x0 && x < x0 + ww + 2 * m && z > z0 && z < z0 + wh + 2 * m) return true;
  }
  return false;
}
function pickWaypoint(a) {
  for (let i = 0; i < 12; i++) {
    const x = (Math.random() - 0.5) * (ARENA_W - 220);
    const z = (Math.random() - 0.5) * (ARENA_H - 220);
    if (!insideWall(x, z)) { a.tx = x; a.tz = z; return; }
  }
  a.tx = 0; a.tz = 0;
}

let loadedCount = 0;
const total = sizes.length * guns.length * engines.length;
function loadRobots() {
engines.forEach((engine, row) => {
  sizes.forEach((size, si) => {
    guns.forEach((gun, gi) => {
      const col = si * guns.length + gi;
      const name = `robot_${size}_${gun}_${engine}`;
      const home = new THREE.Vector3(
        (col - (COLS - 1) / 2) * COL_GAP, 0, (row - (engines.length - 1) / 2) * ROW_GAP);
      loader.parse(base64ToBuffer(window.ROBOT_GLB[name]), '', (gltf) => {
        const root = gltf.scene;
        // baked 'shoot' clip: gun recoil + muzzle flash
        const mixer = new THREE.AnimationMixer(root);
        let fireAction = null;
        const clip = gltf.animations.find((a) => a.name === 'shoot') || gltf.animations[0];
        if (clip) {
          fireAction = mixer.clipAction(clip);
          fireAction.setLoop(THREE.LoopOnce);
        }
        root.scale.setScalar(MODEL_SCALE);
        root.position.copy(home);
        root.rotation.y = Math.PI; // exported facing -Z; turn to face the camera
        const bodyMats = [];
        root.traverse((o) => {
          if (o.isMesh) {
            o.castShadow = o.receiveShadow = true;
            o.userData.robotName = name;
            const mats = Array.isArray(o.material) ? o.material : [o.material];
            for (const m of mats) if (m.name && m.name.startsWith('body_')) bodyMats.push(m);
          }
        });
        // motion rig: axle-origined wheels roll, a spinner attachment whirls,
        // and a lean group banks the hull into turns
        const wheels = [];
        root.traverse((o) => { if (/_wheel\d+$/.test(o.name)) wheels.push(o); });
        const shape = SHAPES[(si + gi + row) % SHAPES.length];
        let spin = null;
        const hullNode = root.children[0];
        if (SPINNER_TPL[shape] && hullNode) {
          const inst = SPINNER_TPL[shape].clone(true);
          inst.scale.setScalar(SIZES[size].radius / 16);   // built at medium reference
          inst.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.userData.robotName = name; } });
          hullNode.add(inst);
          let node = null;
          inst.traverse((o) => { if (o.name === 'spin') node = o; });
          const [prop, rate] = SPIN_AXIS[shape];
          if (node) spin = { obj: node, prop, rate };
        }
        const lean = new THREE.Group();
        if (hullNode) { root.add(lean); lean.add(hullNode); }
        // label lives under the x10-scaled root, so position/scale are in model metres
        const label = labelSprite(`${size} · ${gun} · ${engine}`);
        label.position.y = (SIZES[size].radius / 10) * 3.1 + 1.2;
        label.scale.multiplyScalar(1 / MODEL_SCALE);
        label.visible = labelsOn;
        root.add(label);
        scene.add(root);
        const agent = { x: home.x, z: home.z, hdg: Math.random() * Math.PI * 2,
          speed: AGENT_SPEED[engine], turn: AGENT_TURN[engine],
          mode: engine === 'sprint' ? 'chase' : 'wander',
          avoidDir: Math.random() < 0.5 ? -1 : 1, prey: null, preyUntil: 0, nextShot: 0 };
        pickWaypoint(agent);
        robots.push({ root, name, size, gun, engine, home, label, bodyMats, mixer, fireAction,
          wheels, spin, lean, agent, radius: SIZES[size].radius, wheelRot: 0, leanR: 0,
          baseColor: bodyMats[0] ? bodyMats[0].color.clone() : null });
        loadedCount++;
        document.getElementById('loading').textContent =
          loadedCount < total ? `loading robots… ${loadedCount}/${total}` : '';
      });
    });
  });
});
}
loadSpinners().then(loadRobots);

// --- selection / UI ------------------------------------------------------------
const panel = document.getElementById('panel');

function showPanel(r) {
  const stats = resolveStats(r.size, r.gun, r.engine);
  const rows = Object.entries(stats)
    .map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join('');
  panel.innerHTML = `
    <h2>${r.size} / ${r.gun} / ${r.engine}</h2>
    <div class="notes">
      <div><b>chassis:</b> ${SIZES[r.size].note}</div>
      <div><b>gun:</b> ${GUNS[r.gun].note}</div>
      <div><b>engine:</b> ${ENGINES[r.engine].note}</div>
    </div>
    <table>${rows}</table>
    <button id="fire-btn">FIRE (space)</button>
    <div class="hint">baseline stats at 0 loadout points — spend the 12-point budget on top</div>`;
  panel.classList.add('open');
  panel.querySelector('#fire-btn').addEventListener('click', () => fire(r));
}

function fire(r) {
  if (r.fireAction) r.fireAction.reset().play();
}
addEventListener('keydown', (e) => {
  if (e.code !== 'Space' || e.target.tagName === 'INPUT') return;
  e.preventDefault();
  (selected ? [selected] : robots.filter((r) => r.root.visible)).forEach(fire);
});

function select(r) {
  selected = r;
  if (r) showPanel(r); else panel.classList.remove('open');
}

renderer.domElement.addEventListener('pointerdown', (e) => {
  const downAt = [e.clientX, e.clientY];
  const up = (e2) => {
    renderer.domElement.removeEventListener('pointerup', up);
    if (Math.hypot(e2.clientX - downAt[0], e2.clientY - downAt[1]) > 6) return; // drag = orbit
    pointer.x = (e2.clientX / innerWidth) * 2 - 1;
    pointer.y = -(e2.clientY / innerHeight) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    const meshes = [];
    robots.forEach((r) => r.root.visible && r.root.traverse((o) => o.isMesh && meshes.push(o)));
    const hit = raycaster.intersectObjects(meshes, false)[0];
    if (!hit) { select(null); return; }
    const r = robots.find((rb) => rb.name === hit.object.userData.robotName);
    select(r);
    if (r) {
      // glide the camera toward the robot
      const target = r.root.position.clone().setY(18);
      const dir = camera.position.clone().sub(controls.target).normalize();
      camTween = {
        t: 0,
        fromT: controls.target.clone(), toT: target,
        fromP: camera.position.clone(), toP: target.clone().add(dir.multiplyScalar(260)).setY(170),
      };
    }
  };
  renderer.domElement.addEventListener('pointerup', up);
});

let camTween = null;

// --- custom colours (same scheme as match robots: any #rrggbb, palette fallback) --
const PALETTE = ['#3fd0c9', '#ff6b6b', '#ffd93d', '#6bcb77', '#b983ff', '#ff9f43', '#4d96ff', '#ff5d8f'];

function tint(r, hex) {
  for (const m of r.bodyMats) m.color.set(hex);
}
function applyTint(hex) {
  const targets = selected ? [selected] : robots.filter((r) => r.root.visible);
  targets.forEach((r) => tint(r, hex));
}
document.getElementById('tint').addEventListener('input', (e) => applyTint(e.target.value));
document.getElementById('reset-tint').addEventListener('click', () => {
  robots.forEach((r) => r.baseColor && r.bodyMats.forEach((m) => m.color.copy(r.baseColor)));
});
const swatches = document.getElementById('swatches');
for (const hex of PALETTE) {
  const s = document.createElement('i');
  s.style.background = hex;
  s.title = hex;
  s.addEventListener('click', () => {
    document.getElementById('tint').value = hex;
    applyTint(hex);
  });
  swatches.appendChild(s);
}

// label visibility toggle
let labelsOn = true;
document.getElementById('labels-toggle').addEventListener('change', (e) => {
  labelsOn = e.target.checked;
  robots.forEach((r) => { r.label.visible = labelsOn; });
});

// filters
const filters = { size: 'all', gun: 'all', engine: 'all' };
for (const axis of ['size', 'gun', 'engine']) {
  document.getElementById('f-' + axis).addEventListener('change', (e) => {
    filters[axis] = e.target.value;
    robots.forEach((r) => {
      r.root.visible = (filters.size === 'all' || r.size === filters.size)
        && (filters.gun === 'all' || r.gun === filters.gun)
        && (filters.engine === 'all' || r.engine === filters.engine);
    });
    if (selected && !selected.root.visible) select(null);
  });
}

addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

window.__DEBUG = { scene, camera, robots, raycaster, THREE };

// --- autonomous driving -----------------------------------------------------------
function driveAgent(r, dt, t) {
  const a = r.agent;
  // chasers pick a robot and hunt it; everyone else wanders waypoint to waypoint
  if (a.mode === 'chase') {
    if (!a.prey || !a.prey.root.visible || a.prey === r || t > a.preyUntil) {
      const others = robots.filter((o) => o !== r && o.root.visible);
      a.prey = others.length ? others[(Math.random() * others.length) | 0] : null;
      a.preyUntil = t + 6 + Math.random() * 5;
    }
    if (a.prey) { a.tx = a.prey.agent.x; a.tz = a.prey.agent.z; }
  }
  const dx = a.tx - a.x, dz = a.tz - a.z;
  const dist = Math.hypot(dx, dz);
  if (a.mode !== 'chase' && dist < 50) pickWaypoint(a);
  let want = Math.atan2(dz, dx);
  // veer around walls (probe a point ahead of the nose)
  if (insideWall(a.x + Math.cos(a.hdg) * 75, a.z + Math.sin(a.hdg) * 75))
    want = a.hdg + a.avoidDir * 2.3;
  // soft separation from nearby robots (footprints are ~50 units across)
  for (const o of robots) {
    if (o === r || !o.root.visible) continue;
    const sx = a.x - o.agent.x, sz = a.z - o.agent.z;
    const d = Math.hypot(sx, sz);
    if (d > 1 && d < 90) { want = Math.atan2(dz * 0.4 + sz * 3, dx * 0.4 + sx * 3); break; }
  }
  let dh = want - a.hdg;
  while (dh > Math.PI) dh -= Math.PI * 2;
  while (dh < -Math.PI) dh += Math.PI * 2;
  const applied = Math.max(-a.turn * dt, Math.min(a.turn * dt, dh));
  a.hdg += applied;
  const spd = a.mode === 'chase' && dist > 130 ? a.speed * 1.15 : a.speed;
  a.x += Math.cos(a.hdg) * spd * dt;
  a.z += Math.sin(a.hdg) * spd * dt;
  a.x = Math.max(-ARENA_W / 2 + 60, Math.min(ARENA_W / 2 - 60, a.x));
  a.z = Math.max(-ARENA_H / 2 + 60, Math.min(ARENA_H / 2 - 60, a.z));
  // chasers open fire when they close in
  if (a.mode === 'chase' && a.prey && dist < 150 && t > a.nextShot) {
    fire(r);
    a.nextShot = t + 1.5 + Math.random() * 1.2;
  }
  r.root.position.x = a.x;
  r.root.position.z = a.z;
  r.root.rotation.y = -a.hdg - Math.PI / 2;   // model faces -Z; heading 0 = +x
  // wheels roll with travel; hull banks into the turn and dips with speed
  r.wheelRot -= (spd * dt) / (r.radius * 0.4);
  for (const w of r.wheels) w.rotation.x = r.wheelRot;
  r.leanR = r.leanR * 0.88 + Math.max(-0.13, Math.min(0.13, dh * 1.1)) * 0.12;
  r.lean.rotation.z = r.leanR;
  r.lean.rotation.x = -Math.min(0.07, spd * 0.0007);
}

// --- loop ------------------------------------------------------------------------
const clock = new THREE.Clock();
function tick() {
  requestAnimationFrame(tick);
  const dt = Math.min(clock.getDelta(), 0.05);
  const t = clock.elapsedTime;
  for (const r of robots) {
    r.mixer.update(dt);
    if (r.spin) r.spin.obj.rotation[r.spin.prop] = t * r.spin.rate;   // melee spinner
    if (r.root.visible && r !== selected) driveAgent(r, dt, t);
    if (r.engine === 'hover') r.root.position.y = r.home.y + Math.sin(t * 2.2 + r.home.x) * 3 + 2;
    if (r === selected) r.root.rotation.y += 0.008;   // showcase spin-in-place
  }
  if (camTween) {
    camTween.t = Math.min(1, camTween.t + 0.035);
    const k = 1 - Math.pow(1 - camTween.t, 3);
    controls.target.lerpVectors(camTween.fromT, camTween.toT, k);
    camera.position.lerpVectors(camTween.fromP, camTween.toP, k);
    if (camTween.t >= 1) camTween = null;
  }
  controls.update();
  renderer.render(scene, camera);
}
tick();
