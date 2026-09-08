/* Robot Wars v2 — 3D arena renderer.
 * Pure THREE (global, r150 UMD, bundled). No modules, no CDN, no external assets:
 * the whole thing runs from file:// at an offline venue.
 *
 * Reads a v2 match JSONL. Per-frame schema (from engine/game.py _frame):
 *   tick, robots[].{id,name,x,y,heading,hp,max_hp,alive,dmg,r,rkt,trp,color,shape},
 *   fired[].{f,t,hit}, rockets[].{id,x,y,owner}, mines[].{id,x,y,owner,armed},
 *   explosions[].{x,y,r}, status.{alive,time_left,w,h,walls[[x,y,w,h]]}
 *
 * Arena coords: x in [0,w], y in [0,h], y-down (screen). heading deg, 0 = +x.
 * World mapping: X = x - w/2,  Z = y - h/2,  Y up (floor at 0).
 * A world heading h points (cos h, 0, sin h); a mesh whose "front" is local +X
 * faces that by rotation.y = -h.
 */
(function () {
"use strict";
const T = window.THREE;
if (!T) { document.body.innerHTML = "<p style='color:#fff;padding:40px'>Three.js failed to load.</p>"; return; }

// ----- palette (matches the 2D fallback so colors are familiar) -------------
const PALETTE = [0x3fd0c9,0xff6b6b,0xffd93d,0x6bcb77,0xb983ff,0xff9f43,0x4d96ff,0xff5d8f,
                 0x00d2a8,0xf78fb3,0xcddc39,0xff8a5c,0x7ec8e3,0xc44dff,0x2ec4b6,0xe84393];
const colorFor = (r) => (typeof r.color === "string" && /^#[0-9a-fA-F]{6}$/.test(r.color))
  ? parseInt(r.color.slice(1), 16) : PALETTE[r.id % PALETTE.length];
const SCORCH = new T.Color(0x20242c);

// ----- Blender GLB assets (robots + arenas), inlined by build_arena.py ------
// window.__RW_MODELS__ = { robots: {robot_<size>_<gun>_<eng>: b64},
//                          arenas: {name: {glb: b64, w, h, walls: [[x,y,w,h],..]}} }
// Missing payload or a failed parse falls back to the procedural meshes.
const ROBOT_TPL = {};   // name -> { scene, clip }   (template, cloned per robot)
const ARENA_TPL = {};   // walls fingerprint -> scene template
const SPINNER_TPL = {}; // shape -> scene template (melee spinner attachment)
const MODEL_SCALE = 10; // models are metres; 1 m = 10 game units
function b64buf(b64) {
  const bin = atob(b64), buf = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return buf.buffer;
}
// stable key for a wall layout: rects rounded + sorted (both sides derive from
// the same engine/maps.py floats, so integer rounding is collision-free)
function wallsKey(w, h, walls) {
  return [w | 0, h | 0].concat((walls || []).map((r) => r.map(Math.round).join(",")).sort()).join(";");
}
// inflate a gzipped+base64 payload with the browser's native decompressor
// (DecompressionStream: no library, works from file://, still fully offline).
// Without it neither the embedded models nor the embedded match can load;
// tell the operator instead of failing silently with no replay on screen.
let gzipWarned = false;
function warnNoGunzip() {
  if (gzipWarned) return; gzipWarned = true;
  const el = document.createElement("div");
  el.textContent = "This browser can't decompress the embedded assets -- use a current Chrome, Edge, or Firefox.";
  el.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:9999;background:#a11;color:#fff;" +
    "font:600 13px/1.6 sans-serif;text-align:center;padding:4px 0;";
  document.body.appendChild(el);
}
async function gunzipB64(b64) {
  if (typeof DecompressionStream === "undefined") { warnNoGunzip(); throw new Error("DecompressionStream unsupported"); }
  const ds = new DecompressionStream("gzip");
  const stream = new Blob([b64buf(b64)]).stream().pipeThrough(ds);
  return await new Response(stream).text();
}

function preloadModels(done) {
  const payload = window.__RW_MODELS__;
  if (!payload || !T.GLTFLoader) { done(); return; }
  const loader = new T.GLTFLoader();
  // force single-sided: the r150 double-sided lit path shades closed-box
  // interiors through the front faces (dark wedges on flat walls)
  const frontSide = (root) => root.traverse((o) => {
    if (o.isMesh) (Array.isArray(o.material) ? o.material : [o.material])
      .forEach((m) => { m.side = T.FrontSide; });
  });
  let pending = 1;
  const finish = () => { if (--pending === 0) done(); };
  Object.entries(payload.robots || {}).forEach(([name, b64]) => {
    pending++;
    loader.parse(b64buf(b64), "", (g) => {
      frontSide(g.scene);
      ROBOT_TPL[name] = { scene: g.scene, clip: g.animations.find((a) => a.name === "shoot") || g.animations[0] };
      finish();
    }, (e) => { console.warn("robot GLB failed:", name, e); finish(); });
  });
  Object.entries(payload.arenas || {}).forEach(([name, a]) => {
    pending++;
    loader.parse(b64buf(a.glb), "", (g) => {
      frontSide(g.scene);
      ARENA_TPL[wallsKey(a.w, a.h, a.walls)] = g.scene;
      finish();
    }, (e) => { console.warn("arena GLB failed:", name, e); finish(); });
  });
  Object.entries(payload.spinners || {}).forEach(([shape, b64]) => {
    pending++;
    loader.parse(b64buf(b64), "", (g) => {
      frontSide(g.scene);
      SPINNER_TPL[shape] = g.scene;
      finish();
    }, (e) => { console.warn("spinner GLB failed:", shape, e); finish(); });
  });
  finish();
}

// ----- DOM ------------------------------------------------------------------
const $ = (id) => document.getElementById(id);
const stage = $("stage"), hud = $("hud"), banner = $("banner");
const playBtn = $("play"), speed = $("speed");
const DEBUG = /[?&]debug/.test(location.search);   // ?debug shows the tick chip

// ----- match state ----------------------------------------------------------
let frames = [], AW = 1280, AH = 768, names = {}, voiceKey = {}, hpBand = {};
let jumboFeedScreen = null;   // backdrop board mirroring the broadcast
let jumboFeedFrame = null;    // its cyan housing, excluded from the feed pass

// Every pickup kind the engine can emit (engine/game.py _collect_pickups) and
// every hazard zone. Kept as tables so a new engine kind shows up as a missing
// entry here rather than silently falling through with no ticker and no voice.
const PICKUP_UI = {
  overdrive: "⚡ OVERDRIVE claimed", shield: "🛡 SHIELD up", haste: "💨 HASTE grabbed",
  rockets: "🚀 ROCKETS loaded", traps: "🕳 TRAPS armed", repair: "🔧 REPAIRS taken",
};
const HAZARD_UI = { lava: "🌋 INTO THE LAVA", pit: "🕳 INTO THE PIT", water: "💧 INTO THE WATER" };
// HP band -> clip stem. 40 is deliberately colour commentary rather than a number.
const BAND_CLIP = { 75: "hp75", 50: "hp50", 40: "trouble", 25: "hp25" };
let hazardSaid = {};
let simTime = 0;            // playback head, in ticks (float)
let playing = false, fps = 18, lastT = 0, raf = null;
let saidGo = false, saidWinner = false, lastTickSeen = -1, alivePrev = {};
let fightHold = 0;          // brief playhead hold so FIGHT lands before the first exchange

// ============================================================================
// THREE scene
// ============================================================================
const renderer = new T.WebGLRenderer({ canvas: $("cv"), antialias: true });
// Full retina. The 1.5 cap left visibly stair-stepped edges on the pit walls
// and hazard rims — the MSAA in the bloom target can't recover detail the
// drawing buffer never had.
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = T.PCFSoftShadowMap;
renderer.outputColorSpace = T.SRGBColorSpace;
renderer.toneMapping = T.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.95;        // broadcast: high clarity, not blown out

// Broadcast Combat palette: a brightly-lit TV pit against a cool studio slate,
// team-colored key spots, clean painted chassis. Not the molten foundry.
const scene = new T.Scene();
// Gradient skydome instead of a flat void: deep navy at the floor line, a faint
// warm horizon-glow band above the stands, near-black at the zenith. A giant
// inverted sphere with a canvas gradient — PMREM-free, vendored Three only.
(function () {
  const c = document.createElement("canvas"); c.width = 4; c.height = 512;
  const g = c.getContext("2d");
  const grd = g.createLinearGradient(0, 512, 0, 0);
  grd.addColorStop(0.0, "#141a26");
  grd.addColorStop(0.24, "#1b2130");
  grd.addColorStop(0.36, "#453629");   // warm horizon glow above the stands
  grd.addColorStop(0.52, "#141826");
  grd.addColorStop(1.0, "#0a0e18");
  g.fillStyle = grd; g.fillRect(0, 0, 4, 512);
  const tex = new T.CanvasTexture(c); tex.colorSpace = T.SRGBColorSpace;
  const dome = new T.Mesh(new T.SphereGeometry(6000, 24, 18),
    new T.MeshBasicMaterial({ map: tex, side: T.BackSide, depthWrite: false }));
  dome.renderOrder = -1; dome.frustumCulled = false;
  scene.add(dome);
})();
// No scene fog: the depth haze muddied the broadcast look on the big screen.

// Near plane 40, not 1. A near of 1 threw away almost the entire depth buffer
// on range this camera never occupies: at 2000 units out it left surfaces less
// than ~0.25 units apart inseparable, and the arena stacks its floor, markings,
// hazard pads and rims well inside that — so they interleaved per-pixel and
// read as dense stripes and saw-toothed edges. It looked exactly like aliasing,
// which is why four rounds of anti-aliasing work (8x MSAA, 4096 shadows,
// triangle rims, 2.5x supersampling) all failed to touch it: depth precision is
// not something more samples can fix. 40 buys ~40x the precision for free.
// 40 is a CEILING, not a safe margin with room to spare. The focus zoom and the
// victory framing can pull the camera to roughly 110 units from its target, and
// screen shake to about 75; long weapon FX geometry extends further still. Do
// not raise this without re-checking the closest approach in updateCam.
const camera = new T.PerspectiveCamera(48, 16 / 9, 40, 8000);

// lights: bright neutral ambient + white studio key (shadows) + soft accent fills.
// Intensities tuned so the floor sits ~0.6 luminance — bright TV pit, but safely
// BELOW the bloom threshold (blown-out white floor was the first-pass failure).
scene.add(new T.HemisphereLight(0xaec4e0, 0x3a3126, 0.55));
const key = new T.DirectionalLight(0xffffff, 1.25);
key.position.set(-400, 900, 300);
key.castShadow = true;
// 1024 over an 1800x1400 frustum is 1.8 world units per texel — every shadow
// edge staircased in ~2-unit steps. 2048 takes that to 0.9, still below a
// floor tile. 4096 was tuned against frame rate on one dev machine; the venue
// GPU is an unknown, so don't spend the extra texture budget on headroom that
// was never actually measured there.
const SHADOW_RES = Math.min(2048, renderer.capabilities.maxTextureSize);
key.shadow.mapSize.set(SHADOW_RES, SHADOW_RES);
key.shadow.camera.near = 100; key.shadow.camera.far = 2600;
const sc = key.shadow.camera;
sc.left = -900; sc.right = 900; sc.top = 700; sc.bottom = -700;
// normalBias offsets along the surface normal, which is what actually kills the
// acne banding on the big flat floor; the constant bias alone could not without
// detaching contact shadows.
key.shadow.bias = -0.0002;
key.shadow.normalBias = 1.2;
scene.add(key);
// broad soft fill from the opposite side so the pit stays bright and readable
const fillKey = new T.DirectionalLight(0xdfe8f5, 0.25); fillKey.position.set(500, 600, -400); scene.add(fillKey);
const fill1 = new T.PointLight(0x8fd8ff, 0.35, 2400); fill1.position.set(-700, 300, -500); scene.add(fill1);
const fill2 = new T.PointLight(0xffd8b0, 0.30, 2400); fill2.position.set(700, 300, 500); scene.add(fill2);

// Procedural studio environment (IBL) so painted metal reflects a clean broadcast
// room, not lava: a bright grey light-box band + white key strip + two team-tint
// rim lights, PMREM'd into scene.environment. Pure canvas, no external HDRI.
(function () {
  const c = document.createElement("canvas"); c.width = 512; c.height = 256;
  const g = c.getContext("2d");
  const grd = g.createLinearGradient(0, 0, 0, 256);
  grd.addColorStop(0.0, "#20293a"); grd.addColorStop(0.45, "#3a4658");
  grd.addColorStop(0.60, "#8a97ad"); grd.addColorStop(0.80, "#586479");
  grd.addColorStop(1.0, "#2a3242");
  g.fillStyle = grd; g.fillRect(0, 0, 512, 256);
  g.fillStyle = "rgba(255,255,255,0.85)"; g.fillRect(150, 150, 220, 26);   // bright floor bounce
  g.fillStyle = "rgba(240,248,255,0.9)";  g.fillRect(196, 22, 120, 20);    // white overhead key
  g.fillStyle = "rgba(90,200,255,0.5)";   g.fillRect(46, 60, 96, 30);      // cool team rim
  g.fillStyle = "rgba(255,160,90,0.45)";  g.fillRect(372, 56, 92, 28);     // warm team rim
  const tex = new T.CanvasTexture(c);
  tex.mapping = T.EquirectangularReflectionMapping; tex.colorSpace = T.SRGBColorSpace;
  const pmrem = new T.PMREMGenerator(renderer); pmrem.compileEquirectangularShader();
  scene.environment = pmrem.fromEquirectangular(tex).texture;
  tex.dispose(); pmrem.dispose();
})();

// broadcast key spotlights: bright white house lights + two team-tinted spots that
// track the pit. Colors get overridden per match to the two team colors (see
// setSpotTeams). No shadows for perf.
const spots = [];
[[0xffffff, -1], [0x9fdcff, 1], [0xffc79f, -1]].forEach(([c, dir], i) => {
  const s = new T.SpotLight(c, i === 0 ? 1.4 : 1.1, 3200, Math.PI / 6.5, 0.45, 1.1);
  s.position.set(dir * 560, 1120, (i - 1) * 380);
  s.target.position.set(0, 0, 0); scene.add(s, s.target);
  spots.push({ light: s, phase: i * 2.1, dir, base: c, team: i > 0 });
});
// tint the two accent spots to the live team colors (falls back to defaults for FFA)
function setSpotTeams(cols) {
  const accent = spots.filter((s) => s.team);
  accent.forEach((s, i) => s.light.color.setHex(cols[i] != null ? cols[i] : s.base));
}

// drifting arena haze (fine neutral dust caught in the house lights — broadcast
// atmosphere, not foundry embers). Slow, faint, cool-white.
const EMB = 220;
const ePos = new Float32Array(EMB * 3), eVel = new Float32Array(EMB * 3);
for (let i = 0; i < EMB; i++) {
  ePos[i * 3] = (Math.random() - 0.5) * 1600; ePos[i * 3 + 1] = Math.random() * 620; ePos[i * 3 + 2] = (Math.random() - 0.5) * 1100;
  eVel[i * 3] = (Math.random() - 0.5) * 5; eVel[i * 3 + 1] = 3 + Math.random() * 7; eVel[i * 3 + 2] = (Math.random() - 0.5) * 5;
}
const eGeo = new T.BufferGeometry(); eGeo.setAttribute("position", new T.BufferAttribute(ePos, 3));
const embers = new T.Points(eGeo, new T.PointsMaterial({ size: 5, map: null, color: 0xcdd8ea,
  transparent: true, opacity: 0.22, depthWrite: false, blending: T.AdditiveBlending }));
embers.frustumCulled = false; scene.add(embers);
function updateEmbers(dt) {
  for (let i = 0; i < EMB; i++) {
    const o = i * 3;
    ePos[o] += eVel[o] * dt; ePos[o + 1] += eVel[o + 1] * dt; ePos[o + 2] += eVel[o + 2] * dt;
    if (ePos[o + 1] > 680) { ePos[o + 1] = 0; ePos[o] = (Math.random() - 0.5) * 1600; ePos[o + 2] = (Math.random() - 0.5) * 1100; }
  }
  eGeo.attributes.position.needsUpdate = true;
}

// crowd camera-flashes: a pool of brief additive pops around the stands, fired at
// random — the single most "live broadcast" atmosphere cue. Cheap sprites.
// (populated after SPARK_TEX exists; see initCamFlashes below.)
const CAMFLASH = [];
function initCamFlashes() {
  for (let i = 0; i < 7; i++) {
    const sp = new T.Sprite(new T.SpriteMaterial({ map: SPARK_TEX, color: 0xffffff,
      transparent: true, opacity: 0, depthWrite: false, blending: T.AdditiveBlending }));
    sp.scale.set(46, 46, 1); scene.add(sp);
    CAMFLASH.push({ sp, life: 0 });
  }
}
function updateCamFlashes(dt) {
  const halfW = AW / 2, halfH = AH / 2;
  if (Math.random() < 0.14) {                     // fire a new flash occasionally
    const f = CAMFLASH.find((q) => q.life <= 0);
    if (f) {
      const side = (Math.random() * 4) | 0, off = 200 + Math.random() * 340;
      let x, z;
      if (side === 0) { x = (Math.random() - 0.5) * (AW + off * 2); z = -halfH - off; }
      else if (side === 1) { x = (Math.random() - 0.5) * (AW + off * 2); z = halfH + off; }
      else if (side === 2) { x = -halfW - off; z = (Math.random() - 0.5) * (AH + off * 2); }
      else { x = halfW + off; z = (Math.random() - 0.5) * (AH + off * 2); }
      f.sp.position.set(x, 70 + Math.random() * 120, z);
      f.life = f.max = 0.16 + Math.random() * 0.1;
    }
  }
  CAMFLASH.forEach((f) => {
    if (f.life > 0) { f.life -= dt; f.sp.material.opacity = Math.max(0, f.life / f.max); }
    else f.sp.material.opacity = 0;
  });
}

// ----- the crowd ------------------------------------------------------------
// Spectators are drawn, not downloaded. Both pages are already 10MB and 22MB
// self-contained bundles, so a photographic crowd sheet would have cost more
// than everything else on the boards put together; a canvas atlas costs bytes
// measured in the code that draws it.
//
// The atlas holds four poses side by side and carries MASKS, not colour: red
// channel = shirt, green = skin, blue = hair, alpha = coverage. The shader
// recolours each figure from per-instance attributes, so 4 drawn poses become
// thousands of visibly different people.
const CROWD_POSES = 4, CROWD_CELL_W = 128, CROWD_CELL_H = 192;
function crowdAtlas() {
  const c = document.createElement("canvas");
  c.width = CROWD_CELL_W * CROWD_POSES; c.height = CROWD_CELL_H;
  const g = c.getContext("2d");
  const SHIRT = "#ff0000", SKIN = "#00ff00", HAIR = "#0000ff";
  // arm angles per pose: sitting, leaning in, one arm up, both arms up
  const POSE = [
    { lean: 0.00, la: 0.18, ra: -0.18, rise: 0 },
    { lean: 0.16, la: 0.34, ra: -0.30, rise: 0 },
    { lean: 0.04, la: 0.20, ra: -2.30, rise: 6 },
    { lean: 0.00, la: 2.30, ra: -2.30, rise: 10 },
  ];
  const limb = (x, y, len, w, ang) => {
    g.save(); g.translate(x, y); g.rotate(ang);
    g.beginPath(); g.moveTo(0, 0); g.lineTo(0, len);
    g.lineWidth = w; g.lineCap = "round"; g.stroke(); g.restore();
  };
  for (let p = 0; p < CROWD_POSES; p++) {
    const P = POSE[p], ox = p * CROWD_CELL_W;
    g.save(); g.translate(ox + CROWD_CELL_W / 2, CROWD_CELL_H);
    g.rotate(P.lean);
    const shoulderY = -122 - P.rise, headY = -150 - P.rise;
    // torso: shoulders down to the seat line, slightly tapered
    g.fillStyle = SHIRT;
    g.beginPath();
    g.moveTo(-34, shoulderY); g.lineTo(34, shoulderY);
    g.quadraticCurveTo(42, shoulderY + 40, 30, 0);
    g.lineTo(-30, 0);
    g.quadraticCurveTo(-42, shoulderY + 40, -34, shoulderY);
    g.closePath(); g.fill();
    // arms, sleeved in the shirt colour
    g.strokeStyle = SHIRT;
    limb(-32, shoulderY + 10, 62, 20, P.la);
    limb(32, shoulderY + 10, 62, 20, P.ra);
    // hands at the end of each arm
    g.strokeStyle = SKIN;
    limb(-32 + Math.sin(P.la) * 56, shoulderY + 10 + Math.cos(P.la) * 56, 14, 18, P.la);
    limb(32 + Math.sin(P.ra) * 56, shoulderY + 10 + Math.cos(P.ra) * 56, 14, 18, P.ra);
    // neck + head
    g.strokeStyle = SKIN; limb(0, shoulderY - 6, 14, 22, 0);
    g.fillStyle = SKIN;
    g.beginPath(); g.ellipse(0, headY, 23, 26, 0, 0, Math.PI * 2); g.fill();
    // hair cap over the back of the skull
    g.fillStyle = HAIR;
    g.beginPath(); g.ellipse(0, headY - 5, 24, 22, 0, Math.PI, Math.PI * 2); g.fill();
    g.restore();
  }
  const tex = new T.CanvasTexture(c);
  tex.colorSpace = T.NoColorSpace;   // masks, not colour: no sRGB decode
  tex.minFilter = T.LinearMipmapLinearFilter; tex.magFilter = T.LinearFilter;
  tex.generateMipmaps = true; tex.anisotropy = MAX_ANISO;
  return tex;
}

// Seat positions come off the geometry, not off a guess: drop a ray onto the
// modelled `Seats` ring and keep where it lands. The five map presets share a
// bowl profile but not a footprint, so anything analytic here would be right
// for one arena and wrong for four.
// Derived from the board ring rather than written out again: the two numbers
// have to move together, and the first version of this hard-coded a clearance
// that quietly emptied four of the bowl's five tiers when the boards moved.
// Read at call time, not at load: AD_GLB is declared further down the file.
const crowdClear = () => adRing().outset + 15;
const CROWD_MIN_H = 46;   // shortest figure; also the head-clears-the-board test
// Nobody is seated where a hoarding would swallow them whole. A spectator whose
// head does not clear the top of the board in front is a spectator you never
// see — and the step they would have sat on is hidden behind that same board
// from every camera height, so leaving it empty costs nothing and reads right.
// A quarter of a figure showing above the board is the threshold for seating
// them at all — clearing it by a hair reads as a row of floating heads.
const crowdMinSeatY = () => adRing().base + AD_BH - CROWD_MIN_H * 0.75;
function crowdSeatPoints(group, count) {
  const pts = [];
  // The model is added and scaled x10 in the same tick this runs, so its world
  // matrices are still the unscaled ones until the next render. Without this
  // the seat ring measures a tenth of its real size, every sample lands inside
  // the pit filter, and the crowd comes out empty with nothing thrown.
  group.updateMatrixWorld(true);
  // Raycast the whole stepped structure, not the `Seats` mesh alone. `Seats`
  // only skins alternate tiers of the modelled bowl — sample it at four depths
  // and two of them miss it entirely, landing on `Stands` a full step higher.
  // Standing figures on `Seats` therefore sank them into the step in front and
  // the depth buffer sliced each row along a dead-straight line, which on screen
  // is a stand full of people cut off at the waist.
  //
  // Name-matched rather than "every mesh": the colonnade, lintel, banners and
  // floodlights all sit ABOVE the seating, so a ray dropped from overhead hits
  // those first and would seat the crowd on the roof.
  const surfaces = [];
  group.traverse((o) => {
    if (o.isMesh && !o.isSkinnedMesh && o.geometry && /stand|seat|tier|step/i.test(o.name)) surfaces.push(o);
  });
  const seats = surfaces.length ? surfaces : null;
  if (seats) {
    const box = new T.Box3();
    seats.forEach((s) => box.expandByObject(s));
    const rc = new T.Raycaster(), down = new T.Vector3(0, -1, 0);
    const from = new T.Vector3();
    // Seating starts BEHIND the hoardings. The bowl's front rows are inside the
    // board ring, and filling them puts a wall of spectators between the camera
    // and the advertising — which is the one thing the boards exist to avoid.
    const clear = crowdClear();
    const innerX = AW / 2 + clear, innerZ = AH / 2 + clear;
    const minSeatY = crowdMinSeatY();
    const depthX = box.max.x - innerX, depthZ = box.max.z - innerZ;
    if (depthX <= 0 && depthZ <= 0) return pts;
    for (let guard = 0; pts.length < count && guard < count * 6; guard++) {
      // aim straight at the seating band rather than at the whole bounding box:
      // most of that box is the pit, and rejection sampling it wastes the budget
      const side = (Math.random() * 4) | 0;
      let x, z;
      if (side < 2) {
        if (depthZ <= 0) continue;
        x = (Math.random() * 2 - 1) * box.max.x;
        z = (side ? 1 : -1) * (innerZ + Math.random() * depthZ);
      } else {
        if (depthX <= 0) continue;
        x = (side === 2 ? 1 : -1) * (innerX + Math.random() * depthX);
        z = (Math.random() * 2 - 1) * box.max.z;
      }
      from.set(x, box.max.y + 200, z);
      rc.set(from, down);
      const h = rc.intersectObjects(seats, false)[0];   // nearest from above = the step they stand on
      if (h && h.point.y >= minSeatY) pts.push(new T.Vector3(x, h.point.y, z));
    }
    return pts;
  }
  // procedural stands: stepped boxes, so the seat height is known outright
  const tiers = 5, step = 60, riseH = 46, halfW = AW / 2, halfH = AH / 2;
  // same rule as the modelled bowl: start at the first tier whose occupants
  // clear the boards, rather than filling rows the hoardings hide
  const minSeatY = crowdMinSeatY();
  const tier0 = Math.max(0, Math.ceil((minSeatY - riseH - 2) / riseH));
  if (tier0 >= tiers) return pts;
  for (let i = 0; i < count; i++) {
    const t = tier0 + ((Math.random() * (tiers - tier0)) | 0);
    // Tier t's box is centred half a step inside `120 + t * step`, so its tread
    // runs from 60 + t*step. Standing people on 120 + t*step put every row on
    // the NEXT tier's footprint at its own tier's height — buried to the neck
    // in the riser behind, which is what "only heads" looked like on screen.
    const off = 60 + t * step + Math.random() * step;
    const y = t * riseH + riseH + 2;
    const side = i % 4;
    if (side === 0) pts.push(new T.Vector3((Math.random() - 0.5) * (AW + off * 2), y, -halfH - off));
    else if (side === 1) pts.push(new T.Vector3((Math.random() - 0.5) * (AW + off * 2), y, halfH + off));
    else if (side === 2) pts.push(new T.Vector3(-halfW - off, y, (Math.random() - 0.5) * (AH + off * 2)));
    else pts.push(new T.Vector3(halfW + off, y, (Math.random() - 0.5) * (AH + off * 2)));
  }
  return pts;
}

// Broad enough that no two neighbours read as clones at broadcast distance,
// muted enough that the crowd stays a backdrop and the robots stay the subject.
const CROWD_SHIRTS = [
  0x2f3a4f, 0x3d4a63, 0x1f2733, 0x4a3f52, 0x5a4433, 0x334a3f, 0x53303a, 0x2b4257,
  0x6b6f78, 0x8a8f99, 0x3fd0c9, 0xff4d4d, 0xffd93d, 0x5fe0d8, 0xff9f43, 0xb983ff,
];
const CROWD_SKINS = [0xf0c8a0, 0xe0ac82, 0xc68863, 0x8d5524, 0x5c3317, 0xffdbac];
const CROWD_HAIRS = [0x1a1512, 0x2c2018, 0x4a3728, 0x6b4a2f, 0x8c6239, 0xa8a8a8, 0x1f1f24];

const CROWD_VERT = `
  attribute vec3 iPos; attribute vec3 iShirt; attribute vec3 iSkin; attribute vec3 iHair;
  attribute float iScale; attribute float iPose; attribute float iPhase; attribute float iShade;
  uniform float u_time; uniform float u_cellW;
  varying vec2 vUv; varying vec3 vShirt; varying vec3 vSkin; varying vec3 vHair; varying float vShade;
  void main() {
    vShirt = iShirt; vSkin = iSkin; vHair = iHair; vShade = iShade;
    vUv = vec2((uv.x + iPose) * u_cellW, uv.y);
    // idle bob, plus a narrow band travelling round the bowl: the wave is what
    // stops a still crowd from reading as wallpaper
    float ang = atan(iPos.z, iPos.x);
    float wave = pow(max(0.0, sin(ang - u_time * 0.55)), 12.0);
    float bob = sin(u_time * 2.3 + iPhase) * 0.018;
    float h = iScale * (1.0 + bob);
    vec4 mv = modelViewMatrix * vec4(iPos, 1.0);
    mv.y += wave * iScale * 0.42;
    mv.xy += vec2(position.x * h * 0.667, (position.y + 0.5) * h);
    gl_Position = projectionMatrix * mv;
  }`;
// ACES + sRGB written out longhand rather than pulled in as three's shader
// chunks. This build is r150, where the chunk is `encodings_fragment`; it was
// renamed `colorspace_fragment` in r152, and a raw ShaderMaterial does not get
// the matching `_pars_` chunk injected for it either way. Inlining the two
// conversions keeps the crowd graded like the rest of the scene across either.
const CROWD_FRAG = `
  uniform sampler2D u_map; uniform float u_exposure;
  varying vec2 vUv; varying vec3 vShirt; varying vec3 vSkin; varying vec3 vHair; varying float vShade;
  vec3 aces(vec3 x) {
    const float a = 2.51, b = 0.03, c = 2.43, d = 0.59, e = 0.14;
    return clamp((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0);
  }
  void main() {
    vec4 t = texture2D(u_map, vUv);
    if (t.a < 0.4) discard;
    vec3 col = (vShirt * t.r + vSkin * t.g + vHair * t.b) * vShade;
    col = aces(col * u_exposure);
    col = mix(col * 12.92,
              1.055 * pow(col, vec3(0.41666)) - 0.055,
              step(0.0031308, col));
    gl_FragColor = vec4(col, 1.0);
  }`;

// ~4600 figures in one draw call. InstancedBufferGeometry rather than
// InstancedMesh: every figure is a camera-facing quad, so a per-instance
// matrix would be 16 floats spent to store a position we billboard away.
function buildCrowdFigures(group) {
  const COUNT = 4600;
  const pts = crowdSeatPoints(group, COUNT);
  crowdSeats = pts;
  if (!pts.length) return null;
  const n = pts.length;
  const src = new T.PlaneGeometry(1, 1);
  const geo = new T.InstancedBufferGeometry();
  geo.setAttribute("position", src.getAttribute("position"));
  geo.setAttribute("uv", src.getAttribute("uv"));
  geo.setIndex(src.getIndex());
  geo.instanceCount = n;

  const iPos = new Float32Array(n * 3), iShirt = new Float32Array(n * 3);
  const iSkin = new Float32Array(n * 3), iHair = new Float32Array(n * 3);
  const iScale = new Float32Array(n), iPose = new Float32Array(n);
  const iPhase = new Float32Array(n), iShade = new Float32Array(n);
  const col = new T.Color();
  for (let i = 0; i < n; i++) {
    const p = pts[i];
    iPos[i * 3] = p.x; iPos[i * 3 + 1] = p.y; iPos[i * 3 + 2] = p.z;
    col.setHex(CROWD_SHIRTS[(Math.random() * CROWD_SHIRTS.length) | 0]);
    iShirt[i * 3] = col.r; iShirt[i * 3 + 1] = col.g; iShirt[i * 3 + 2] = col.b;
    col.setHex(CROWD_SKINS[(Math.random() * CROWD_SKINS.length) | 0]);
    iSkin[i * 3] = col.r; iSkin[i * 3 + 1] = col.g; iSkin[i * 3 + 2] = col.b;
    col.setHex(CROWD_HAIRS[(Math.random() * CROWD_HAIRS.length) | 0]);
    iHair[i * 3] = col.r; iHair[i * 3 + 1] = col.g; iHair[i * 3 + 2] = col.b;
    iScale[i] = CROWD_MIN_H + Math.random() * 10;
    // mostly seated; a scattering already on their feet
    const r = Math.random();
    iPose[i] = r < 0.5 ? 0 : r < 0.8 ? 1 : r < 0.93 ? 2 : 3;
    iPhase[i] = Math.random() * Math.PI * 2;
    iShade[i] = 0.5 + Math.random() * 0.22;   // house lights are down over the stands
  }
  const put = (name, arr, size) =>
    geo.setAttribute(name, new T.InstancedBufferAttribute(arr, size));
  put("iPos", iPos, 3); put("iShirt", iShirt, 3); put("iSkin", iSkin, 3);
  put("iHair", iHair, 3); put("iScale", iScale, 1); put("iPose", iPose, 1);
  put("iPhase", iPhase, 1); put("iShade", iShade, 1);

  const mat = new T.ShaderMaterial({
    uniforms: { u_map: { value: crowdAtlas() }, u_time: { value: 0 },
                u_cellW: { value: 1 / CROWD_POSES },
                u_exposure: { value: renderer.toneMappingExposure } },
    vertexShader: CROWD_VERT, fragmentShader: CROWD_FRAG,
  });
  const mesh = new T.Mesh(geo, mat);
  mesh.frustumCulled = false;   // instances live outside the source geometry's bounds
  group.add(mesh);
  crowdCount = n;
  return mat;
}
// surfaced on RW.debug: a crowd that silently fails to place is invisible in
// exactly the same way as a crowd that is simply behind the camera
let crowdCount = 0;
let crowdSeats = [];   // reused to put the phone lights in somebody's hands

// ----- procedural textures (no external files) -----------------------------
function floorTexture() {
  const c = document.createElement("canvas"); c.width = c.height = 1024;
  const g = c.getContext("2d");
  // painted concrete combat pit — darker structured base (subject pops off it)
  g.fillStyle = "#454c59"; g.fillRect(0, 0, 1024, 1024);
  // subtle panel seams (large 4x4 plates)
  g.strokeStyle = "rgba(20,24,32,0.55)"; g.lineWidth = 3;
  for (let i = 1; i < 4; i++) {
    const p = i / 4 * 1024;
    g.beginPath(); g.moveTo(p, 0); g.lineTo(p, 1024); g.stroke();
    g.beginPath(); g.moveTo(0, p); g.lineTo(1024, p); g.stroke();
  }
  // scuff / tyre blotches for grit (dark, low opacity)
  for (let i = 0; i < 180; i++) {
    const x = Math.random() * 1024, y = Math.random() * 1024, r = 6 + Math.random() * 50;
    g.fillStyle = `rgba(30,34,42,${0.03 + Math.random() * 0.06})`;
    g.beginPath(); g.arc(x, y, r, 0, 7); g.fill();
  }
  // faint fine grid
  g.strokeStyle = "rgba(210,222,240,0.06)"; g.lineWidth = 1;
  for (let i = 0; i <= 16; i++) {
    const p = i / 16 * 1024;
    g.beginPath(); g.moveTo(p, 0); g.lineTo(p, 1024); g.stroke();
    g.beginPath(); g.moveTo(0, p); g.lineTo(1024, p); g.stroke();
  }
  // painted pitch markings: center circle + team-colored end-zone stripes
  g.strokeStyle = "rgba(230,238,248,0.45)"; g.lineWidth = 6;
  g.beginPath(); g.arc(512, 512, 150, 0, 7); g.stroke();
  g.fillStyle = "rgba(230,238,248,0.45)";
  g.beginPath(); g.arc(512, 512, 9, 0, 7); g.fill();
  g.fillStyle = "rgba(63,208,201,0.28)"; g.fillRect(6, 60, 84, 904);
  g.fillStyle = "rgba(255,107,107,0.28)"; g.fillRect(934, 60, 84, 904);
  // hazard chevron border stripes (yellow/black) — classic combat-arena marking
  const cw = 26;
  for (let i = 0; i < 40; i++) {
    g.fillStyle = i % 2 ? "#f2c400" : "#1a1c22";
    // top & bottom bands
    g.save(); g.translate(i * cw - 8, 12); g.transform(1, 0, 0.6, 1, 0, 0); g.fillRect(0, 0, cw, 30); g.restore();
    g.save(); g.translate(i * cw - 8, 982); g.transform(1, 0, 0.6, 1, 0, 0); g.fillRect(0, 0, cw, 30); g.restore();
  }
  const tex = new T.CanvasTexture(c);
  tex.wrapS = tex.wrapT = T.RepeatWrapping; tex.colorSpace = T.SRGBColorSpace;
  return tex;
}

// Molten lava: dark basalt crust cracked by glowing veins of orange/yellow.
// Tiled + scrolled slowly in the loop so the surface looks like it's flowing.
function lavaTexture() {
  const c = document.createElement("canvas"); c.width = c.height = 512;
  const g = c.getContext("2d");
  g.fillStyle = "#1a0602"; g.fillRect(0, 0, 512, 512);
  for (let i = 0; i < 70; i++) {                       // molten cells (hot centres)
    const x = Math.random() * 512, y = Math.random() * 512, r = 24 + Math.random() * 70;
    const gr = g.createRadialGradient(x, y, 0, x, y, r);
    gr.addColorStop(0, "rgba(255,238,150,0.95)");
    gr.addColorStop(0.35, "rgba(255,120,20,0.8)");
    gr.addColorStop(1, "rgba(60,10,2,0)");
    g.fillStyle = gr; g.beginPath(); g.arc(x, y, r, 0, 7); g.fill();
  }
  g.strokeStyle = "rgba(20,4,2,0.85)"; g.lineWidth = 3 + Math.random() * 3;
  for (let i = 0; i < 26; i++) {                       // cracked crust over the top
    g.beginPath(); g.moveTo(Math.random() * 512, Math.random() * 512);
    for (let k = 0; k < 4; k++) g.lineTo(Math.random() * 512, Math.random() * 512);
    g.stroke();
  }
  const tex = new T.CanvasTexture(c);
  tex.wrapS = tex.wrapT = T.RepeatWrapping; tex.colorSpace = T.SRGBColorSpace;
  return tex;
}

// Water: deep blue with lighter caustic ripples. Two of these scroll in opposite
// directions in the loop to fake a shifting, refracting surface.
function waterTexture() {
  const c = document.createElement("canvas"); c.width = c.height = 512;
  const g = c.getContext("2d");
  g.fillStyle = "#062a4a"; g.fillRect(0, 0, 512, 512);
  for (let i = 0; i < 130; i++) {                      // caustic highlights
    const x = Math.random() * 512, y = Math.random() * 512, r = 6 + Math.random() * 26;
    const gr = g.createRadialGradient(x, y, 0, x, y, r);
    gr.addColorStop(0, "rgba(150,225,255,0.5)");
    gr.addColorStop(1, "rgba(150,225,255,0)");
    g.fillStyle = gr; g.beginPath(); g.arc(x, y, r, 0, 7); g.fill();
  }
  g.strokeStyle = "rgba(120,200,240,0.16)"; g.lineWidth = 2;
  for (let i = 0; i < 40; i++) {                       // wave streaks
    const y = Math.random() * 512;
    g.beginPath(); g.moveTo(0, y); g.bezierCurveTo(170, y + 18, 340, y - 18, 512, y); g.stroke();
  }
  const tex = new T.CanvasTexture(c);
  tex.wrapS = tex.wrapT = T.RepeatWrapping; tex.colorSpace = T.SRGBColorSpace;
  return tex;
}
function softSprite() {
  const c = document.createElement("canvas"); c.width = c.height = 64;
  const g = c.getContext("2d");
  const grd = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grd.addColorStop(0, "rgba(255,255,255,1)");
  grd.addColorStop(0.4, "rgba(255,255,255,0.6)");
  grd.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grd; g.fillRect(0, 0, 64, 64);
  const tex = new T.CanvasTexture(c); tex.colorSpace = T.SRGBColorSpace; return tex;
}
const SPARK_TEX = softSprite();
initCamFlashes();

// ----- pitch-side advertising hoardings -------------------------------------
// Football-style boards ringing the arena, angled back so they read from the
// broadcast cameras. Out of the box every board is a plain white plate with a
// coloured strip along its base, which is what an unbranded arena looks like.
//
// To put real marks on them: drop trimmed PNGs into src/assets/bu-logos/ named
// <slug>.png and point the slugs below at them. assets_js.py inlines whatever
// it finds as data URIs, so the page stays one offline file. A slug with no PNG
// draws a blank plate rather than failing, so a partial set is fine.
//
// Boards draw white immediately and the logo lands when it decodes. A map
// attached after a mesh's first render never recompiles, so the texture is
// built first and flagged in the image's onload.
//
// `accent` is the strip along the board's base. Grouping slugs by colour is
// what makes the ring read as one system rather than a row of loose plates.
const AD_UNITS = [
  { slug: "unit-01", accent: "#3fd0c9" },
  { slug: "unit-02", accent: "#3fd0c9" },
  { slug: "unit-03", accent: "#3fd0c9" },
  { slug: "unit-04", accent: "#3fd0c9" },
  { slug: "unit-05", accent: "#3fd0c9" },
  { slug: "unit-06", accent: "#3fd0c9" },
  { slug: "unit-07", accent: "#3fd0c9" },
  { slug: "unit-08", accent: "#3fd0c9" },
  { slug: "unit-09", accent: "#ff4d4d" },
  { slug: "unit-10", accent: "#ff4d4d" },
  { slug: "unit-11", accent: "#ff4d4d" },
  { slug: "unit-12", accent: "#ff4d4d" },
  { slug: "unit-13", accent: "#ff9f43" },
  { slug: "unit-14", accent: "#ff9f43" },
  { slug: "unit-15", accent: "#ff9f43" },
  { slug: "unit-16", accent: "#ffd93d" },
  { slug: "unit-17", accent: "#ffd93d" },
  { slug: "unit-18", accent: "#5fe0d8" },
  { slug: "unit-19", accent: "#5fe0d8" },
  { slug: "unit-20", accent: "#5fe0d8" },
  { slug: "unit-21", accent: "#6ee7a8" },
  { slug: "unit-22", accent: "#6ee7a8" },
  { slug: "unit-23", accent: "#b983ff" },
];

// Canvas is 512x208 (~2.5:1) to match the board's own proportions — art cut for
// a wider ratio arrives on the mesh visibly stretched.
const AD_W = 512, AD_H = 208;

// Anisotropic filtering + mipmaps. Without these, fine repeating detail (board
// rules, hatched panels) breaks into crawling moire once a surface is distant
// or steeply raked — the "lines on everything from a distance".
const MAX_ANISO = renderer.capabilities.getMaxAnisotropy();
// isColor: only colour maps carry sRGB. Forcing it on normal/roughness data
// would wash the lighting out.
function sharpTexture(tex, isColor) {
  tex.anisotropy = MAX_ANISO;
  tex.generateMipmaps = true;
  tex.minFilter = T.LinearMipmapLinearFilter;
  tex.magFilter = T.LinearFilter;
  if (isColor) tex.colorSpace = T.SRGBColorSpace;
  tex.needsUpdate = true;
  return tex;
}

// Accent strip along the base. Pure white plate, not the off-white the rest of
// the UI chrome uses: a good few of these marks ship with their own white
// background baked in, and against #f4f6f9 that box shows up as a visible
// rectangle sitting on the board.
const AD_STRIP = 16;

function adBoardTexture(spec) {
  const c = document.createElement("canvas"); c.width = AD_W; c.height = AD_H;
  const g = c.getContext("2d");
  g.fillStyle = "#ffffff"; g.fillRect(0, 0, AD_W, AD_H);
  if (spec.accent) { g.fillStyle = spec.accent; g.fillRect(0, AD_H - AD_STRIP, AD_W, AD_STRIP); }

  // Build the texture BEFORE the image resolves. A CanvasTexture whose map is
  // attached after the mesh's first render never recompiles, so the board would
  // stay blank; drawing into the same canvas and flagging needsUpdate is what
  // actually lands the logo on screen.
  const tex = sharpTexture(new T.CanvasTexture(c), true);
  const src = spec.brand
    ? window.__BRAND_LOGO_COLOR__
    : (window.__BU_LOGOS__ || {})[spec.slug];
  if (typeof src === "string") {
    const img = new Image();
    img.onload = () => {
      // Contain-fit inside the plate, clear of the accent strip. The source PNGs
      // are already trimmed to the ink, so this fills the board rather than
      // fitting a square of white padding to the board's height.
      const padX = 26, padY = 16;
      const boxW = AD_W - padX * 2, boxH = AD_H - AD_STRIP - padY * 2;
      const s = Math.min(boxW / img.width, boxH / img.height);
      const w = img.width * s, h = img.height * s;
      g.drawImage(img, (AD_W - w) / 2, padY + (boxH - h) / 2, w, h);
      tex.needsUpdate = true;
    };
    img.src = src;
  }
  return tex;
}

// A cursor rather than the board index, and module-level rather than per-build,
// for two reasons. Indexing the list by board index skipped every array entry
// whose position shared a factor with the every-3rd house board, which with
// twenty-one units meant seven of them never appeared at all. And one arena only
// has room for sixteen unit boards, so restarting the cursor each build would
// show the same sixteen units every match; letting it carry over gives a
// bracket's worth of matches enough rotations to put every unit on screen.
let adUnitCursor = 0;

// Every 3rd board carries the house mark so it reads on any camera angle.
function adBoardSpec(i) {
  if (i % 3 === 0) return { brand: true };
  const spec = AD_UNITS[adUnitCursor];
  adUnitCursor = (adUnitCursor + 1) % AD_UNITS.length;
  return spec;
}

// Where the hoardings hang on the modelled bowl. They used to stand 120 out,
// which put them a third of the way into the seating: the boards read fine but
// every spectator in the front tiers was either in front of the advertising or
// had to be left out to protect it. Measured off the model, the kerb's outer
// face ends at 14 past the pit and its top is 26 high, and the first seating
// tier does not begin until ~64 past the pit — so 30 out drops the boards into
// clear air on the kerb, exactly where trackside hoardings go, and hands the
// whole bowl back to the crowd.
//
// The base is NOT the kerb's 26. Raycasting a board at this outset from four
// camera heights across all five presets, the height at which a board first
// clears everything in front of it is 25.0 (classic), 25.4 (pillars), 25.8
// (colosseum), 38.0 (arena) and 40.9 (gauntlet) — the last two mount their
// kerbs higher. 46 clears the worst of them by 5.
const AD_GLB = { outset: 30, base: 46 };
// The procedural pit has the same shape of problem and had it worse: its boards
// were floor-mounted at 2 behind a kerb 26 high, so the kerb ate the bottom
// quarter of every board on that path. 30 clears it.
const AD_PROC = { outset: 26, base: 30 };
const AD_BH = 84;   // board height, shared with the crowd's seating cutoff
const adRing = () => (glbArenaActive ? AD_GLB : AD_PROC);
function buildAdBoards(group, aw, ah, outset, base) {
  // `base` lifts the boards clear of whatever ring sits in front of them: the
  // procedural kerb and the modelled one are both 26 tall, so both want a base
  // just above that rather than a floor mounting.
  const BH = AD_BH, BASE = base || 2, TILT = -0.14;   // ~8deg lean back
  // The walk-on drives each bot in through a tunnel at the MIDPOINT of a side
  // (see `gates` in the entrance code). Boarding straight across the span puts
  // a hoarding over every tunnel mouth, so leave the centre of each side open.
  const GATE_GAP = 200;
  let idx = 0;
  const side = (span, cx, cz, ry) => {
    const n = Math.max(3, Math.round(span / 210));
    const bw = span / n;
    for (let i = 0; i < n; i++) {
      const off = -span / 2 + bw * (i + 0.5);
      if (Math.abs(off) < GATE_GAP) continue;      // tunnel mouth
      const holder = new T.Group();
      holder.position.set(cx + Math.cos(ry) * off, BASE + BH / 2, cz - Math.sin(ry) * off);
      holder.rotation.y = ry;
      // FrontSide, not DoubleSide: a hoarding seen from behind renders its
      // artwork mirrored, and the camera is regularly outside the far boards.
      const m = new T.Mesh(new T.PlaneGeometry(bw * 0.96, BH),
        new T.MeshBasicMaterial({ map: adBoardTexture(adBoardSpec(idx++)), side: T.FrontSide }));
      m.rotation.x = TILT;
      holder.add(m);
      group.add(holder);
    }
  };
  // ry aims the plane's +Z normal INTO the arena: normal = (sin ry, 0, cos ry).
  side(aw, 0, ah / 2 + outset, Math.PI);        // near side  -> normal -z
  side(aw, 0, -ah / 2 - outset, 0);             // far side   -> normal +z
  side(ah, -aw / 2 - outset, 0, Math.PI / 2);   // left       -> normal +x
  side(ah, aw / 2 + outset, 0, -Math.PI / 2);   // right      -> normal -x
}

// Billboard screen mesh. The finished artwork gets a BRAND-NEW material built
// around its texture: this three build never recompiles an already-rendered
// material to include a later-attached map (the screens rendered their
// mapless base state forever), so we swap the whole material instead.
function jumboScreen(w, h, opts) {
  const mesh = new T.Mesh(new T.PlaneGeometry(w, h),
    new T.MeshBasicMaterial({ color: 0xf4f6f9, side: T.DoubleSide }));
  mesh.userData.staticMaterial = mesh.material;
  const attach = () => {
    const material = new T.MeshBasicMaterial({ map: new T.CanvasTexture(c), side: T.DoubleSide });
    mesh.userData.staticMaterial = material;
    if (!mesh.userData.liveFeed) mesh.material = material;
  };
  const enableFeed = () => {
    // Live board: mirror the completed broadcast into a dedicated texture,
    // rather than sampling a bloom working target or running a second camera.
    if (!(opts && opts.feed && bloom.ok && bloom.feedTexture)) return;
    const buf = renderer.getDrawingBufferSize(new T.Vector2());
    const srcAspect = buf.x / Math.max(1, buf.y), dstAspect = w / h;
    const repeat = new T.Vector2(1, 1), offset = new T.Vector2(0, 0);
    if (srcAspect < dstAspect) {
      repeat.y = srcAspect / dstAspect;
      offset.y = (1 - repeat.y) / 2;
    } else {
      repeat.x = dstAspect / srcAspect;
      offset.x = (1 - repeat.x) / 2;
    }
    mesh.userData.feedRepeat = repeat;
    mesh.userData.feedOffset = offset;
    jumboFeedScreen = mesh;
  };
  const c = document.createElement("canvas"); c.width = 512; c.height = 256;
  const g = c.getContext("2d");
  const colorLogo = typeof window.__BRAND_LOGO_COLOR__ === "string";
  if (colorLogo && opts && opts.brand) {
    // The corner televisions carry the event lock-up rather than a second copy
    // of the sponsor plate: mark on top, event name under it. Same white plate
    // and same teal base strip as the hoardings, so the ring and the screens
    // read as one dressing job instead of two.
    g.fillStyle = "#ffffff"; g.fillRect(0, 0, 512, 256);
    g.fillStyle = "#3fd0c9"; g.fillRect(0, 236, 512, 20);
    const img = new Image();
    img.onload = () => {
      const w = 250, h = w * img.height / img.width;
      g.drawImage(img, (512 - w) / 2, 30, w, h);
      g.textAlign = "center";
      g.fillStyle = "#12414f"; g.font = "900 46px ui-sans-serif, sans-serif";
      g.fillText("ROBOT WARS", 256, 178);
      // letterspacing by hand: canvas has no tracking control in this build
      g.fillStyle = "#5c6b78"; g.font = "700 21px ui-sans-serif, sans-serif";
      const word = "INTERNATIONAL", track = 7;
      const widths = [...word].map((ch) => g.measureText(ch).width);
      let x = 256 - (widths.reduce((a, b) => a + b, 0) + track * (word.length - 1)) / 2;
      g.textAlign = "left";
      [...word].forEach((ch, i) => { g.fillText(ch, x, 214); x += widths[i] + track; });
      attach();
    };
    img.src = window.__BRAND_LOGO_COLOR__;
    return mesh;
  }
  if (colorLogo) {
    // sponsor billboard: the full-colour company logo on a clean white board.
    // Pure white, not off-white, for the same reason the ad boards are: the
    // logo PNG carries its own white background, and against #f4f6f9 that
    // shows up as a rectangle sitting on the screen.
    g.fillStyle = "#ffffff"; g.fillRect(0, 0, 512, 256);
    g.fillStyle = "#dfe4ec"; g.fillRect(0, 246, 512, 10);   // subtle base strip
    const img = new Image();
    img.onload = () => {
      // A fixed share of the plate, not a fit to a padded box. The source PNG
      // used to carry a wide white margin and was trimmed to its ink so the ad
      // boards could fill properly; fitting the trimmed mark to the old box
      // would have blown it up by two thirds on this screen. 268px keeps the
      // jumbotron mark exactly the size it has been.
      const w = 268, h = w * img.height / img.width;
      g.drawImage(img, (512 - w) / 2, (256 - h) / 2, w, h);
      // LIVE bug in the corner so it still reads as a broadcast screen
      g.fillStyle = "#ff3b3b"; g.beginPath(); g.arc(446, 30, 8, 0, 7); g.fill();
      g.fillStyle = "#10141c"; g.font = "800 20px ui-sans-serif, sans-serif";
      g.textAlign = "left"; g.fillText("LIVE", 460, 37);
      attach();
    };
    img.src = window.__BRAND_LOGO_COLOR__;
    enableFeed();
    return mesh;
  }
  const hasLogo = typeof window.__BRAND_LOGO__ === "string";
  function draw() {
    g.fillStyle = "#070b14"; g.fillRect(0, 0, 512, 256);
    g.strokeStyle = "rgba(63,208,201,0.25)"; g.lineWidth = 1;
    for (let i = 0; i < 512; i += 24) { g.beginPath(); g.moveTo(i, 0); g.lineTo(i, 256); g.stroke(); }
    for (let i = 0; i < 256; i += 24) { g.beginPath(); g.moveTo(0, i); g.lineTo(512, i); g.stroke(); }
    g.textAlign = "center";
    g.fillStyle = "#3fd0c9"; g.font = "900 58px ui-sans-serif, sans-serif";
    g.fillText("ROBOT WARS", 256, hasLogo ? 132 : 110);
    g.fillStyle = "#7e8aa0"; g.font = "700 22px ui-sans-serif, sans-serif";
    g.fillText("ROBOT WARS", 256, hasLogo ? 168 : 150);
    g.fillStyle = "#ff5d5d"; g.beginPath(); g.arc(196, hasLogo ? 208 : 196, 9, 0, 7); g.fill();
    g.fillStyle = "#fff"; g.font = "800 26px ui-sans-serif, sans-serif"; g.textAlign = "left";
    g.fillText("LIVE", 214, hasLogo ? 217 : 205);
  }
  draw();
  if (hasLogo) {
    // house logo (data URI SVG: decodes offline, no fetch) across the top
    const img = new Image();
    img.onload = () => {
      const w = 190, h = w * (img.height / img.width) || 60;   // svg aspect ≈ 3.15
      g.drawImage(img, 256 - w / 2, 14, w, h);
      attach();
    };
    img.src = window.__BRAND_LOGO__;
  } else {
    attach();
  }
  enableFeed();
  return mesh;
}
// House logo in the page header (arena + tournament shells both have <header>)
(function () {
  if (typeof window.__BRAND_LOGO__ !== "string") return;
  const h = document.querySelector("header");
  if (!h) return;
  const img = document.createElement("img");
  img.src = window.__BRAND_LOGO__; img.alt = "Robot Wars";
  img.style.cssText = "height:20px;margin-right:10px;flex:none;";
  h.insertBefore(img, h.firstChild);
})();

// animated env refs (rebuilt with the arena)
let envAnim = { hazards: [], jumbos: [], crowd: null, people: null };
let glbArenaActive = false;   // Blender arena brings its own colosseum bowl
function buildEnvironment() {
  envAnim = { hazards: [], jumbos: [], crowd: null, people: null };
  const halfW = AW / 2, halfH = AH / 2;

  // tiered crowd stands around the arena (stepped dark boxes) — the Blender
  // colosseum replaces these with modelled stands/colonnade when active
  const tiers = 5, step = 60, riseH = 46;   // crowd points spread over this footprint
  if (!glbArenaActive) {
    const standMat = new T.MeshStandardMaterial({ color: 0x0c1018, roughness: 0.95, metalness: 0.05 });
    for (let t = 0; t < tiers; t++) {
      const off = 120 + t * step, y = t * riseH;
      const lenX = AW + off * 2, lenZ = AH + off * 2;
      const mk = (w, h, x, z) => { const m = new T.Mesh(new T.BoxGeometry(w, riseH + 4, h), standMat);
        m.position.set(x, y + riseH / 2, z); m.receiveShadow = true; arenaGroup.add(m); };
      mk(lenX, step, 0, -halfH - off + step / 2);
      mk(lenX, step, 0, halfH + off - step / 2);
      mk(step, lenZ, -halfW - off + step / 2, 0);
      mk(step, lenZ, halfW + off - step / 2, 0);
    }
  }
  // stand uplights: 4 low-intensity points washing the tiers; two carry the
  // live team colors when the match is team-based (global hemi stays down)
  const rs0 = (frames.length ? frames[0].robots : []).filter((r) => r.team !== "house");
  const ti0 = teamInfo(rs0);
  const tcols = (ti0.hasTeams && ti0.order.length >= 2)
    ? [colorFor(ti0.by[ti0.order[0]][0]), colorFor(ti0.by[ti0.order[1]][0])]
    : [0xffd0a0, 0xa0c8ff];
  [[-halfW - 260, -halfH - 260, tcols[0]], [halfW + 260, halfH + 260, tcols[1]],
   [halfW + 260, -halfH - 260, 0xffe0c0], [-halfW - 260, halfH + 260, 0xc0d4ff]].forEach(([x, z, cc]) => {
    const up = new T.PointLight(cc, 0.5, 1500, 1.6);
    up.position.set(x, 100, z);
    arenaGroup.add(up);
  });

  // the actual spectators
  envAnim.people = buildCrowdFigures(arenaGroup);

  // Phone screens, one in every ninth pair of hands. These used to be scattered
  // on their own footprint, which was derived from the procedural tier maths and
  // so landed them on bare steps of the modelled bowl — a scatter of lit dots
  // sitting on empty concrete. Hanging them off the crowd's own seats puts every
  // light in somebody's hands, in both arenas, for free.
  const seats = crowdSeats.length ? crowdSeats : null;
  const CN = seats ? Math.min(1100, (seats.length / 9) | 0) : 1100;
  const cpos = new Float32Array(CN * 3), ccol = new Float32Array(CN * 3);
  for (let i = 0; i < CN; i++) {
    let x, y, z;
    if (seats) {
      const s = seats[(Math.random() * seats.length) | 0];
      x = s.x; y = s.y + 26 + Math.random() * 10; z = s.z;   // held up at chest height
    } else {
      const side = i % 4, off = 150 + Math.random() * (tiers * step - 80);
      y = 30 + (off - 150) / (tiers * step) * (tiers * riseH) + Math.random() * 30;
      if (side === 0) { x = (Math.random() - 0.5) * (AW + off * 2); z = -halfH - off; }
      else if (side === 1) { x = (Math.random() - 0.5) * (AW + off * 2); z = halfH + off; }
      else if (side === 2) { x = -halfW - off; z = (Math.random() - 0.5) * (AH + off * 2); }
      else { x = halfW + off; z = (Math.random() - 0.5) * (AH + off * 2); }
    }
    cpos[i * 3] = x; cpos[i * 3 + 1] = y; cpos[i * 3 + 2] = z;
    // warm/cool audience mix (phone screens + house lights)
    const warm = Math.random() < 0.5, br = 0.35 + Math.random() * 0.55;
    ccol[i * 3] = br * (warm ? 1.0 : 0.72);
    ccol[i * 3 + 1] = br * 0.85;
    ccol[i * 3 + 2] = br * (warm ? 0.62 : 1.0);
  }
  const cgeo = new T.BufferGeometry(); cgeo.setAttribute("position", new T.BufferAttribute(cpos, 3)); cgeo.setAttribute("color", new T.BufferAttribute(ccol, 3));
  const crowd = new T.Points(cgeo, new T.PointsMaterial({ size: 10, vertexColors: true, transparent: true, opacity: 0.85, depthWrite: false }));
  crowd.frustumCulled = false; arenaGroup.add(crowd); envAnim.crowd = crowd;

  // jumbotron screens (corners, angled inward) + a big backdrop sign; the
  // Blender colosseum's stands are deeper/taller, so screens move out and up.
  // ry aims each plane's +Z normal INTO the arena (they faced outward before,
  // showing the arena camera their mirrored backs).
  jumboFeedScreen = null; jumboFeedFrame = null;   // arena is rebuilt per match
  const jOff = glbArenaActive ? 340 : 80, jY = glbArenaActive ? 330 : 230;
  const screens = [
    { x: -halfW - jOff, z: -halfH - jOff, ry: Math.PI * 0.25, w: 360, h: 180, y: jY, brand: true },
    { x: halfW + jOff, z: -halfH - jOff, ry: -Math.PI * 0.25, w: 360, h: 180, y: jY, brand: true },
    // the live jumbotron: mirrors the completed broadcast frame
    { x: 0, z: -halfH - (glbArenaActive ? 460 : 260), ry: 0, w: 900, h: 300, y: glbArenaActive ? 420 : 320, feed: true },
  ];
  screens.forEach((s) => {
    const m = jumboScreen(s.w, s.h, { feed: s.feed, brand: s.brand });
    m.position.set(s.x, s.y, s.z); m.rotation.y = s.ry; arenaGroup.add(m);
    const frame = new T.Mesh(new T.BoxGeometry(s.w + 16, s.h + 16, 8),
      new T.MeshStandardMaterial({ color: 0x10141c, roughness: 0.6, metalness: 0.7, emissive: 0x3fd0c9, emissiveIntensity: 0.3 }));
    frame.position.set(s.x, s.y, s.z); frame.rotation.y = s.ry;
    frame.position.add(new T.Vector3(-Math.sin(s.ry) * 6, 0, -Math.cos(s.ry) * 6));   // frame sits behind the screen
    if (s.feed) jumboFeedFrame = frame;
    arenaGroup.add(frame); envAnim.jumbos.push(frame.material);
  });

  // blinking hazard lights at the four corners
  [[-halfW, -halfH], [halfW, -halfH], [-halfW, halfH], [halfW, halfH]].forEach(([x, z]) => {
    const m = new T.Mesh(new T.SphereGeometry(9, 12, 10),
      new T.MeshStandardMaterial({ color: 0xffaa22, emissive: 0xffaa22, emissiveIntensity: 1.4 }));
    m.position.set(x, 60, z); arenaGroup.add(m); envAnim.hazards.push(m.material);
  });

  // glowing center floor logo ring
  const logo = new T.Mesh(new T.RingGeometry(70, 86, 48),
    new T.MeshBasicMaterial({ color: 0x3fd0c9, transparent: true, opacity: 0.35, side: T.DoubleSide, blending: T.AdditiveBlending, depthWrite: false }));
  logo.rotation.x = -Math.PI / 2; logo.position.y = 1.2; arenaGroup.add(logo);
}
function animateEnvironment(t) {
  const blink = (Math.sin(t * 0.004) > 0) ? 1.6 : 0.2;
  envAnim.hazards.forEach((m) => m.emissiveIntensity = blink);
  const pulse = 0.25 + 0.25 * (0.5 + 0.5 * Math.sin(t * 0.003));
  envAnim.jumbos.forEach((m) => m.emissiveIntensity = pulse);
  if (envAnim.crowd) envAnim.crowd.material.opacity = 0.7 + 0.25 * Math.sin(t * 0.01);
  if (envAnim.people) envAnim.people.uniforms.u_time.value = t * 0.001;
}

// ----- static arena (floor, perimeter) -------------------------------------
// ----- hazard shaders (real GLSL, tonemapped to match the ACES scene) -------
const HAZ_VERT = `varying vec2 vUv; void main(){ vUv=uv; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }`;
const HAZ_NOISE = `
  varying vec2 vUv; uniform float u_time; uniform vec2 u_scale;
  float hash(vec2 p){ p=fract(p*vec2(123.34,456.21)); p+=dot(p,p+34.23); return fract(p.x*p.y); }
  float noise(vec2 p){ vec2 i=floor(p),f=fract(p); f=f*f*(3.0-2.0*f);
    float a=hash(i),b=hash(i+vec2(1,0)),c=hash(i+vec2(0,1)),d=hash(i+vec2(1,1));
    return mix(mix(a,b,f.x),mix(c,d,f.x),f.y); }
  float fbm(vec2 p){ float v=0.0,a=0.5; for(int i=0;i<5;i++){ v+=a*noise(p); p*=2.02; a*=0.5; } return v; }`;
// molten flow: two-octave domain warp, dark basalt crust cracked by HDR veins,
// bubbling hotspots that swell and burst, and a cooled rim where the pool
// meets the arena floor
const LAVA_FRAG = HAZ_NOISE + `
  void main(){
    vec2 p=vUv*u_scale; float t=u_time*0.3;
    vec2 w1=vec2(fbm(p*1.2+t*0.7),fbm(p.yx*1.2-t*0.55));
    vec2 w2=vec2(fbm(p*2.6-w1*1.4+t*0.35),fbm(p.yx*2.6+w1*1.4-t*0.3));
    float n=fbm(p*1.1+w1*1.9+w2*0.8+vec2(0.0,t*0.7));
    float crust=smoothstep(0.38,0.6,n);
    vec3 basalt=vec3(0.035,0.014,0.010),hot=vec3(2.4,1.1,0.22),mid=vec3(1.3,0.30,0.035);
    vec3 col=mix(hot,mid,crust); col=mix(col,basalt,smoothstep(0.52,0.82,n));
    float veins=pow(1.0-abs(n-0.5)*2.0,7.0);
    col+=vec3(2.1,0.85,0.2)*veins*(1.0+0.5*sin(u_time*2.4+n*16.0));
    // bubbling hotspots: bright cells that swell then collapse
    float bub=noise(p*3.1+vec2(t*1.9,-t*1.4));
    float pulse=0.5+0.5*sin(u_time*3.1+bub*21.0);
    col+=vec3(2.0,0.75,0.16)*smoothstep(0.72,0.95,bub)*pulse;
    // slow whole-pool breathing
    col*=1.0+0.12*sin(u_time*1.3+n*7.0);
    // cooled crust ring where the pool meets the floor
    float ed=min(min(vUv.x,1.0-vUv.x),min(vUv.y,1.0-vUv.y));
    col*=mix(0.30,1.0,smoothstep(0.0,0.10,ed));
    gl_FragColor=vec4(col,1.0);
    #include <tonemapping_fragment>
    #include <encodings_fragment>
  }`;
// water: crossing wave trains over fbm ripple, caustic webbing, glinting
// specular sparkle, and animated foam breaking along the pool edge
const WATER_FRAG = HAZ_NOISE + `
  void main(){
    vec2 p=vUv*u_scale; float t=u_time*0.75;
    float h=sin(p.x*1.7+t)*0.5+sin((p.x+p.y)*1.15-t*0.72)*0.5;
    h+=sin(p.y*2.3-t*0.9)*0.35;
    h+=fbm(p*1.7+vec2(t*0.22,-t*0.18))*1.3;
    h*=0.45;
    float caust=pow(max(0.0,1.0-abs(h-0.32)*2.4),3.0);
    float e=0.045;
    float hx=sin((p.x+e)*1.7+t)*0.5+sin((p.x+e+p.y)*1.15-t*0.72)*0.5+fbm((p+vec2(e,0.0))*1.7+vec2(t*0.22,-t*0.18))*1.3;
    float hy=sin(p.x*1.7+t)*0.5+sin((p.x+p.y+e)*1.15-t*0.72)*0.5+fbm((p+vec2(0.0,e))*1.7+vec2(t*0.22,-t*0.18))*1.3;
    vec3 nrm=normalize(vec3(h-hx*0.45,h-hy*0.45,0.30));
    vec3 L=normalize(vec3(0.4,0.6,0.7));
    float spec=pow(max(dot(reflect(-L,nrm),vec3(0.0,0.0,1.0)),0.0),36.0);
    vec3 deep=vec3(0.012,0.13,0.30),shallow=vec3(0.07,0.48,0.72);
    vec3 col=mix(deep,shallow,clamp(caust*0.85+h*0.3+0.15,0.0,1.0));
    col+=vec3(0.60,0.92,1.0)*caust*0.55;
    col+=vec3(1.1,1.15,1.2)*spec;
    // drifting sparkle glints on wave crests
    float gl=noise(p*7.5+vec2(t*2.2,-t*1.7));
    col+=vec3(0.9,1.0,1.0)*smoothstep(0.90,1.0,gl)*smoothstep(0.1,0.4,h+0.3)*0.9;
    // foam breaking along the pool edge, chewed up by noise so it reads organic
    float ed=min(min(vUv.x,1.0-vUv.x),min(vUv.y,1.0-vUv.y));
    float foam=smoothstep(0.09,0.0,ed)*smoothstep(0.30,0.75,fbm(p*3.4+vec2(t*0.55,t*0.4)));
    foam+=smoothstep(0.035,0.0,ed)*0.55;
    col=mix(col,vec3(0.82,0.93,1.0),clamp(foam,0.0,1.0)*0.85);
    gl_FragColor=vec4(col,0.93);
    #include <tonemapping_fragment>
    #include <encodings_fragment>
  }`;
function hazardMat(frag, hz, transparent) {
  return new T.ShaderMaterial({
    vertexShader: HAZ_VERT, fragmentShader: frag,
    uniforms: { u_time: { value: 0 }, u_scale: { value: new T.Vector2(hz.w / 64, hz.h / 64) } },
    side: T.DoubleSide, transparent: !!transparent, depthWrite: !transparent,
  });
}

let arenaGroup = null;
let lavaFX = [];   // (unused now the lava glow lives in-shader; kept for the loop guard)
let shaderFX = []; // ShaderMaterials whose u_time is advanced each frame
let hazardZones = [];  // {type, cx, cz, w, h} world-space rects for particle FX
let flipperFX = [];    // floor flippers: {x, y (game), hinge, t} — t < 1.2 = animating
let turntableFX = [];  // spinning platters, rotated in lockstep with sim ticks
let lavaLights = [];   // point lights over lava pools (flickered per frame)
let collapseFX = null, sdAnnounced = false;  // SUDDEN DEATH molten ring (lazy-built)

// SUDDEN DEATH: dedicated shader for the encroaching floor — a white-hot,
// noise-chewed melt line at the advancing front (d=0), molten flow behind it,
// cooling to dark crust toward the wall (d=1). u_dir/u_off orient d per band.
const COLLAPSE_FRAG = HAZ_NOISE + `
  uniform vec2 u_dir; uniform float u_off;
  void main(){
    float d = clamp(u_off + dot(u_dir, vUv), 0.0, 1.0);
    vec2 p = vUv * u_scale; float t = u_time * 0.35;
    vec2 w1 = vec2(fbm(p*1.2 + t*0.6), fbm(p.yx*1.2 - t*0.5));
    float n = fbm(p*1.15 + w1*2.0 + vec2(0.0, t*0.8));
    float chew = (n - 0.5) * 0.22;              // ragged, advancing boundary
    float a = smoothstep(0.0, 0.05, d + chew);
    float crust = smoothstep(0.35, 0.62, n);
    vec3 hot = vec3(2.5, 1.1, 0.2), mid = vec3(1.3, 0.3, 0.04), basalt = vec3(0.05, 0.02, 0.015);
    vec3 col = mix(hot, mid, crust);
    col = mix(col, basalt, smoothstep(0.5, 0.85, n) * smoothstep(0.15, 0.7, d));
    float veins = pow(1.0 - abs(n - 0.5)*2.0, 6.0);
    col += vec3(2.0, 0.8, 0.2) * veins * (1.0 + 0.4*sin(u_time*3.0 + n*14.0));
    float heat = 1.0 - smoothstep(0.0, 0.5, d);  // hottest right behind the front
    col += vec3(2.2, 1.0, 0.25) * heat * heat * (0.85 + 0.3*sin(u_time*5.0 + n*9.0));
    float line = 1.0 - smoothstep(0.0, 0.06, abs(d + chew));
    col += vec3(3.2, 2.3, 1.1) * line;           // the melt line itself
    gl_FragColor = vec4(col, a * 0.6);           // ~40% lighter — keep robots/HUD readable through the melt
    #include <tonemapping_fragment>
    #include <encodings_fragment>
  }`;

// Four molten bands that creep in from the walls once status.collapse > 0,
// each with its own oriented material, plus firelight along the melt line.
function ensureCollapse() {
  if (collapseFX) return collapseFX;
  const grp = new T.Group();
  const mk = (dx, dy, off) => {
    const mat = new T.ShaderMaterial({
      vertexShader: HAZ_VERT, fragmentShader: COLLAPSE_FRAG,
      uniforms: { u_time: { value: 0 }, u_scale: { value: new T.Vector2(1, 1) },
                  u_dir: { value: new T.Vector2(dx, dy) }, u_off: { value: off } },
      side: T.DoubleSide, transparent: true, depthWrite: false });
    shaderFX.push(mat);
    const m = new T.Mesh(new T.PlaneGeometry(1, 1), mat);
    m.rotation.x = -Math.PI / 2; m.position.y = 1.5; m.renderOrder = 3;
    grp.add(m); return m;
  };
  const lights = [];
  for (let i = 0; i < 4; i++) {
    const l = new T.PointLight(0xff6a22, 0, 1100);
    l.position.y = 55; grp.add(l); lights.push(l);
  }
  // plane local +y maps to world -z after the -90° X tilt, so: top band's
  // front (inner, larger z) sits at vUv.y=0; bottom's at vUv.y=1; and the
  // side bands' fronts at vUv.x=1 (left) / vUv.x=0 (right)
  collapseFX = { grp, top: mk(0, 1, 0), bot: mk(0, -1, 1),
                 left: mk(-1, 0, 1), right: mk(1, 0, 0), lights };
  arenaGroup.add(grp);
  return collapseFX;
}
function updateCollapse(cw) {
  if (!(cw > 0)) { if (collapseFX) collapseFX.grp.visible = false; return; }
  const fx = ensureCollapse();
  fx.grp.visible = true;
  const inner = Math.max(1, AH - 2 * cw);
  fx.top.scale.set(AW, cw, 1);   fx.top.position.set(0, 1.5, -AH / 2 + cw / 2);
  fx.bot.scale.set(AW, cw, 1);   fx.bot.position.set(0, 1.5, AH / 2 - cw / 2);
  fx.left.scale.set(cw, inner, 1);  fx.left.position.set(-AW / 2 + cw / 2, 1.5, 0);
  fx.right.scale.set(cw, inner, 1); fx.right.position.set(AW / 2 - cw / 2, 1.5, 0);
  // keep the noise density constant as the bands grow
  fx.top.material.uniforms.u_scale.value.set(AW / 64, cw / 64);
  fx.bot.material.uniforms.u_scale.value.set(AW / 64, cw / 64);
  fx.left.material.uniforms.u_scale.value.set(cw / 64, inner / 64);
  fx.right.material.uniforms.u_scale.value.set(cw / 64, inner / 64);
  // firelight rides the melt line, flickering
  const t = performance.now() * 0.001;
  const fronts = [[0, -AH / 2 + cw], [0, AH / 2 - cw], [-AW / 2 + cw, 0], [AW / 2 - cw, 0]];
  fx.lights.forEach((l, i) => {
    l.position.set(fronts[i][0], 55, fronts[i][1]);
    l.intensity = 2.1 + 0.8 * Math.sin(t * 9.1 + i * 2.1) + 0.4 * Math.sin(t * 17.3 + i * 4.7);
  });
  // embers boil off the advancing front
  const spots = [
    [(Math.random() - 0.5) * AW, -AH / 2 + cw], [(Math.random() - 0.5) * AW, AH / 2 - cw],
    [-AW / 2 + cw, (Math.random() - 0.5) * inner], [AW / 2 - cw, (Math.random() - 0.5) * inner],
  ];
  for (const [x, z] of spots) if (Math.random() < 0.7)
    spawnParticle(x, 4, z, (Math.random() - 0.5) * 34, 55 + Math.random() * 80, (Math.random() - 0.5) * 34,
      0.7 + Math.random() * 0.6, 1.0, 0.55, 0.15, 0.96, -30);
  if (!sdAnnounced) {
    sdAnnounced = true;
    stinger("SUDDEN DEATH");
    ticker("☠ SUDDEN DEATH — the floor is closing in");
    playClip(["sudden_death", "final_blow"], "Sudden death! The floor is closing in!", { flush: true });
    screenFlash(0.35); shake(10);
  }
}
// living hazards: embers + molten bursts over lava, spray glints over water,
// and a firelight flicker on the lava point lights
function updateHazardFX(t, dt) {
  // turntables track the sim clock exactly (2.4 deg/tick, same as the engine),
  // so robots standing on the platter visually ride it through pause + slow-mo
  const ttAng = -0.1396263 * simTime;   // radians: -(8 deg -> rad) * ticks
  for (const p of turntableFX) p.rotation.y = ttAng;
  // fired flippers kick up fast, then ease back down
  for (const f of flipperFX) {
    if (f.t < 1.2) {
      f.t += dt;
      const k = f.t < 0.12 ? f.t / 0.12 : Math.max(0, 1 - (f.t - 0.12) / 0.9);
      f.hinge.rotation.x = -k * 1.15;
    } else if (f.hinge.rotation.x !== 0) f.hinge.rotation.x = 0;
  }
  for (const hz of hazardZones) {
    const rx = () => hz.cx + (Math.random() - 0.5) * hz.w * 0.85;
    const rz = () => hz.cz + (Math.random() - 0.5) * hz.h * 0.85;
    if (hz.type === "lava") {
      // embers drift up off the crust (bigger pools breathe more)
      const rate = Math.min(3, 1 + (hz.w * hz.h) / 30000);
      for (let i = 0; i < rate; i++) if (Math.random() < 0.5)
        spawnParticle(rx(), 4, rz(), (Math.random() - 0.5) * 14, 26 + Math.random() * 38, (Math.random() - 0.5) * 14,
          0.9 + Math.random() * 0.8, 1.0, 0.5 + Math.random() * 0.3, 0.12, 0.985, -14);
      // occasional molten burst
      if (Math.random() < 0.012) {
        const bx = rx(), bz = rz();
        for (let k = 0; k < 10; k++)
          spawnParticle(bx, 5, bz, (Math.random() - 0.5) * 140, 100 + Math.random() * 140, (Math.random() - 0.5) * 140,
            0.55, 1.0, 0.62, 0.16, 0.9, 260);
      }
    } else if (hz.type === "water") {
      // cool spray glints hopping off the chop
      if (Math.random() < 0.22)
        spawnParticle(rx(), 3, rz(), (Math.random() - 0.5) * 10, 8 + Math.random() * 14, (Math.random() - 0.5) * 10,
          0.5, 0.55, 0.82, 0.95, 0.92, 30);
    }
  }
  for (let i = 0; i < lavaLights.length; i++) {
    lavaLights[i].intensity = 1.7 + 0.45 * Math.sin(t * 7.3 + i * 2.7)
      + 0.3 * Math.sin(t * 12.1 + i * 5.1) + 0.15 * Math.sin(t * 23.7 + i);
  }
}

function buildArena(walls, hazards) {
  if (arenaGroup) { scene.remove(arenaGroup); disposeTree(arenaGroup); }
  arenaGroup = new T.Group(); scene.add(arenaGroup);
  lavaFX = []; shaderFX = []; hazardZones = []; lavaLights = []; flipperFX = []; turntableFX = [];
  collapseFX = null; sdAnnounced = false;   // ring was disposed with the old group

  // fit the key light's shadow frustum to this arena so the 1024 map stays sharp
  sc.left = -(AW / 2 + 220); sc.right = AW / 2 + 220;
  sc.top = AH / 2 + 200; sc.bottom = -(AH / 2 + 200);
  key.shadow.camera.updateProjectionMatrix();

  // Blender-built arena for the 5 map presets (fingerprint-matched to the
  // recorded wall layout); custom maps keep the procedural pit below.
  const arenaTpl = ARENA_TPL[wallsKey(AW, AH, walls)];
  glbArenaActive = !!arenaTpl;
  if (arenaTpl) {
    const model = arenaTpl.clone(true);
    model.traverse((o) => {
      if (!o.isMesh) return;
      const n = o.name + "|" + (o.parent ? o.parent.name : "");
      // floor-level surfaces catch shadows; verticals only cast (self-shadow acne)
      if (/Floor|Hazard|Pads|Markings/.test(n)) o.receiveShadow = true;
      if (/Walls|Kerbs|Pylons/.test(n)) o.castShadow = true;
      // The modelled arena ships its own textures, which never went through
      // sharpTexture — unfiltered, they alias into crawling stair-steps on
      // every raked surface at distance. Filter every map the model carries.
      const mats = Array.isArray(o.material) ? o.material : [o.material];
      mats.forEach((mat) => {
        if (!mat) return;
        ["map", "normalMap", "roughnessMap", "metalnessMap", "emissiveMap", "aoMap"]
          .forEach((slot) => { if (mat[slot]) sharpTexture(mat[slot], slot === "map"); });
      });
      // darken the modelled floor to match the graded procedural pit
      if (/Floor/.test(n) && o.material && o.material.color) {
        o.material = o.material.clone();
        o.material.color.multiplyScalar(0.8);
        o.material.envMapIntensity = 0.4;
      }
    });
    model.scale.setScalar(MODEL_SCALE);
    arenaGroup.add(model);
  } else {
    // (procedural pit — the modelled colosseum brings its own floor and kerbs)
    const ftex = floorTexture(); ftex.repeat.set(1, 1);   // painted markings map once across the pit
    const floor = new T.Mesh(
      new T.PlaneGeometry(AW, AH),
      new T.MeshStandardMaterial({ map: ftex, roughness: 0.82, metalness: 0.15, color: 0xaab2c0 }));
    floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true;
    arenaGroup.add(floor);

    // perimeter kerb walls
    const kerbMat = new T.MeshStandardMaterial({ color: 0x141a26, roughness: 0.7, metalness: 0.5 });
    const kerbH = 26, kt = 14;
    const mkKerb = (w, h, x, z) => {
      const m = new T.Mesh(new T.BoxGeometry(w, kerbH, h), kerbMat);
      m.position.set(x, kerbH / 2, z); m.castShadow = m.receiveShadow = true; arenaGroup.add(m);
    };
    mkKerb(AW + kt * 2, kt, 0, -AH / 2 - kt / 2);
    mkKerb(AW + kt * 2, kt, 0, AH / 2 + kt / 2);
    mkKerb(kt, AH, -AW / 2 - kt / 2, 0);
    mkKerb(kt, AH, AW / 2 + kt / 2, 0);

    // cover walls (status.walls rects: x,y,w,h in arena space, top-left origin)
    const wallMat = new T.MeshStandardMaterial({ color: 0x2a3346, roughness: 0.5, metalness: 0.6 });
    const trimMat = new T.LineBasicMaterial({ color: 0x5fe0d8, transparent: true, opacity: 0.7 });
    const wh = 64;
    (walls || []).forEach(([x, y, w, h]) => {
      const geo = new T.BoxGeometry(w, wh, h);
      const m = new T.Mesh(geo, wallMat);
      m.position.set(x + w / 2 - AW / 2, wh / 2, y + h / 2 - AH / 2);
      m.castShadow = m.receiveShadow = true; arenaGroup.add(m);
      const trim = new T.LineSegments(new T.EdgesGeometry(geo), trimMat);
      trim.position.copy(m.position); arenaGroup.add(trim);
    });
  }

  // glowing arena boundary line
  const edge = new T.LineSegments(
    new T.EdgesGeometry(new T.BoxGeometry(AW, 2, AH)),
    new T.LineBasicMaterial({ color: 0x3fd0c9, transparent: true, opacity: 0.5 }));
  edge.position.y = 1; arenaGroup.add(edge);

  // hazard floor zones (status.hazards: {type,x,y,w,h}, top-left origin)
  (hazards || []).forEach((hz) => {
    const cx = hz.x + hz.w / 2 - AW / 2, cz = hz.y + hz.h / 2 - AH / 2;
    hazardZones.push({ type: hz.type, cx, cz, w: hz.w, h: hz.h });
    const geo = new T.PlaneGeometry(hz.w, hz.h);
    const mkEdge = (col, op, y) => {
      // WebGL line primitives rasterise as hard binary steps on the arena's
      // shallow diagonals. A thin frame made from triangles receives sceneRT's
      // multisample coverage just like the robots and other mesh silhouettes.
      const t = Math.min(2, hz.w / 4, hz.h / 4);
      const mat = new T.MeshBasicMaterial({ color: col, transparent: op < 1, opacity: op, side: T.DoubleSide });
      const e = new T.Group();
      const strip = (w, h, x, z) => {
        const m = new T.Mesh(new T.PlaneGeometry(w, h), mat);
        m.rotation.x = -Math.PI / 2; m.position.set(x, 0, z); e.add(m);
      };
      strip(hz.w, t, 0, -(hz.h - t) / 2);
      strip(hz.w, t, 0, (hz.h - t) / 2);
      strip(t, hz.h - t * 2, -(hz.w - t) / 2, 0);
      strip(t, hz.h - t * 2, (hz.w - t) / 2, 0);
      e.position.set(cx, y, cz); arenaGroup.add(e);
    };
    if (hz.type === "lava") {
      // real molten GLSL surface (flow + HDR veins, tonemapped) + a warm glow light
      const mat = hazardMat(LAVA_FRAG, hz, false);
      const m = new T.Mesh(geo, mat); m.rotation.x = -Math.PI / 2; m.position.set(cx, 1.2, cz);
      m.renderOrder = 1; arenaGroup.add(m); shaderFX.push(mat);
      const glow = new T.PointLight(0xff5a1e, 1.8, Math.max(hz.w, hz.h) * 1.8);
      glow.position.set(cx, 46, cz); arenaGroup.add(glow); lavaLights.push(glow);
      mkEdge(0xffb347, 1.0, 3);
    } else if (hz.type === "water") {
      // real GLSL water (caustics + specular glints) over a blue depth gradient
      const mat = hazardMat(WATER_FRAG, hz, true);
      const m = new T.Mesh(geo, mat); m.rotation.x = -Math.PI / 2; m.position.set(cx, 1.0, cz);
      m.renderOrder = 2; arenaGroup.add(m); shaderFX.push(mat);
      const glow = new T.PointLight(0x3aa0ff, 0.6, Math.max(hz.w, hz.h) * 1.4);
      glow.position.set(cx, 40, cz); arenaGroup.add(glow);
      mkEdge(0x7fd4ff, 0.9, 2);
    } else if (hz.type === "ice") {
      // frosted sheet: pale translucent base + additive cyan sheen + crisp rim
      const base = new T.Mesh(geo, new T.MeshStandardMaterial({ color: 0xcfeeff, roughness: 0.15,
        metalness: 0.1, transparent: true, opacity: 0.5 }));
      base.rotation.x = -Math.PI / 2; base.position.set(cx, 0.9, cz); base.renderOrder = 1;
      arenaGroup.add(base);
      const sheen = new T.Mesh(geo, new T.MeshBasicMaterial({ color: 0x8fdcff, transparent: true,
        opacity: 0.28, blending: T.AdditiveBlending, side: T.DoubleSide, depthWrite: false }));
      sheen.rotation.x = -Math.PI / 2; sheen.position.set(cx, 1.1, cz); sheen.renderOrder = 2;
      arenaGroup.add(sheen);
      mkEdge(0xeaffff, 0.9, 2);
    } else if (hz.type === "flipper") {
      // the classic floor flipper: steel base plate + a warning-striped paddle
      // hinged on one edge; the paddle KICKS when the engine fires it
      const plateMat = new T.MeshStandardMaterial({ color: 0x8d96a6, roughness: 0.35, metalness: 0.9 });
      const base = new T.Mesh(new T.BoxGeometry(hz.w, 6, hz.h), plateMat);
      base.position.set(cx, 3, cz); base.castShadow = base.receiveShadow = true;
      arenaGroup.add(base);
      const hinge = new T.Group();
      hinge.position.set(cx, 6, cz - hz.h / 2);
      const flap = new T.Mesh(new T.BoxGeometry(hz.w * 0.94, 5, hz.h * 0.94),
        new T.MeshStandardMaterial({ color: 0xffd93d, roughness: 0.5, metalness: 0.6,
          emissive: 0xff9a00, emissiveIntensity: 0.18 }));
      flap.position.set(0, 2.5, hz.h * 0.47);
      flap.castShadow = true;
      hinge.add(flap); arenaGroup.add(hinge);
      flipperFX.push({ x: hz.x + hz.w / 2, y: hz.y + hz.h / 2, hinge, t: 9 });
      mkEdge(0xffd93d, 0.9, 8);
    } else if (hz.type === "turntable") {
      // spinning platter: painted chevrons make the rotation readable; it
      // turns in lockstep with sim ticks (mirrors engine TURNTABLE_DEG_PER_TICK)
      const rad = Math.min(hz.w, hz.h) / 2;
      const c = document.createElement("canvas"); c.width = c.height = 256;
      const g = c.getContext("2d");
      g.fillStyle = "#39404e"; g.fillRect(0, 0, 256, 256);
      g.translate(128, 128);
      for (let i = 0; i < 8; i++) {
        g.rotate(Math.PI / 4);
        g.fillStyle = i % 2 ? "#ffd93d" : "#2b313d";
        g.beginPath(); g.moveTo(0, 0); g.arc(0, 0, 122, -0.2, 0.2); g.closePath(); g.fill();
      }
      g.fillStyle = "#9aa3b2"; g.beginPath(); g.arc(0, 0, 24, 0, 7); g.fill();
      const ttex = new T.CanvasTexture(c);
      const platter = new T.Mesh(new T.CylinderGeometry(rad, rad, 5, 40),
        new T.MeshStandardMaterial({ map: ttex, roughness: 0.55, metalness: 0.5 }));
      platter.position.set(cx, 3, cz);
      platter.receiveShadow = true;
      arenaGroup.add(platter);
      turntableFX.push(platter);
      mkEdge(0x3fd0c9, 0.85, 8);
    } else if (hz.type === "pit") {
      const m = new T.Mesh(geo, new T.MeshBasicMaterial({ color: 0x01030a, side: T.DoubleSide }));
      m.rotation.x = -Math.PI / 2; m.position.set(cx, 0.5, cz); m.renderOrder = 1; arenaGroup.add(m);
      mkEdge(0xff3344, 0.95, 3);
      mkEdge(0xff3344, 0.5, 16);   // raised danger rim so the hole reads from afar
    }
  });

  // Pitch-side advertising. Built for BOTH arenas — it used to sit inside the
  // procedural branch, so the modelled colosseum (what the demo actually loads)
  // never got any boards at all. The modelled bowl's kerb sits slightly further
  // out than the procedural one, so it needs a slightly wider outset.
  buildAdBoards(arenaGroup, AW, AH, adRing().outset, adRing().base);

  buildEnvironment();
}

// ============================================================================
// Robot meshes (by shape, scaled by radius r)
// ============================================================================
// Weapon pod per GUN archetype — the turret that tracks enemies. Distinct
// silhouettes so a build reads from the stands: slim laser emitter / fat cannon
// tube / triple-muzzle shotgun cluster.
function makeBarrel(col, r, gun) {
  const mat = new T.MeshStandardMaterial({ color: 0x10141c, roughness: 0.4, metalness: 0.8 });
  const grp = new T.Group();
  if (gun === "cannon") {
    const tube = new T.Mesh(new T.CylinderGeometry(r * 0.24, r * 0.3, r * 1.3, 14), mat);
    tube.rotation.z = Math.PI / 2; tube.position.set(r * 0.75, r * 0.6, 0); tube.castShadow = true;
    const brake = new T.Mesh(new T.CylinderGeometry(r * 0.34, r * 0.34, r * 0.28, 14),
      new T.MeshStandardMaterial({ color: 0x2a3346, roughness: 0.35, metalness: 0.9 }));
    brake.rotation.z = Math.PI / 2; brake.position.set(r * 1.32, r * 0.6, 0);
    const breech = new T.Mesh(new T.BoxGeometry(r * 0.5, r * 0.42, r * 0.42), mat);
    breech.position.set(r * 0.1, r * 0.6, 0);
    grp.add(tube, brake, breech);
  } else if (gun === "shotgun") {
    [-1, 0, 1].forEach((s) => {
      const b = new T.Mesh(new T.CylinderGeometry(r * 0.1, r * 0.1, r * 0.95, 10), mat);
      b.rotation.z = Math.PI / 2;
      b.position.set(r * 0.7, r * 0.55 + (s === 0 ? r * 0.14 : 0), s * r * 0.18);
      if (s === 0) b.castShadow = true;
      grp.add(b);
    });
    const tip = new T.Mesh(new T.SphereGeometry(r * 0.12, 10, 8),
      new T.MeshStandardMaterial({ color: col, emissive: col, emissiveIntensity: 0.6, roughness: 0.4 }));
    tip.position.set(r * 1.2, r * 0.62, 0);
    grp.add(tip);
  } else {   // laser — the classic slim emitter
    const barrel = new T.Mesh(new T.CylinderGeometry(r * 0.12, r * 0.12, r * 1.4, 12), mat);
    barrel.rotation.z = Math.PI / 2;          // lay along +X
    barrel.position.set(r * 0.8, r * 0.55, 0);
    barrel.castShadow = true;
    const tip = new T.Mesh(new T.SphereGeometry(r * 0.16, 10, 8),
      new T.MeshStandardMaterial({ color: col, emissive: col, emissiveIntensity: 0.6, roughness: 0.4 }));
    tip.position.set(r * 1.5, r * 0.55, 0);
    grp.add(barrel, tip);
  }
  return grp;
}

// Broadcast Combat chassis: a clean painted armoured WEDGE with team livery, a
// drivetrain per ENGINE archetype (wheels / exhausts / treads / hover skirt), a
// weapon pod per GUN archetype, and a spinning weapon whose form is the shape's
// identity (drum / bar / buzzsaw / spiked drum). Front is local +X. Preserves
// the userData contract playback relies on. gun/eng/accent may be undefined
// (old recordings) — defaults reproduce the classic look.
function makeRobotMesh(shape, col, r, gun, eng, accent) {
  const grp = new T.Group();
  grp.userData.spinners = [];
  // painted livery (matte, lightly emissive so team colour pops on a projector)
  const bodyMat = new T.MeshStandardMaterial({ color: col, roughness: 0.5, metalness: 0.42,
    emissive: col, emissiveIntensity: 0.08, envMapIntensity: 0.85 });
  const darkMat = new T.MeshStandardMaterial({ color: 0x1b2029, roughness: 0.5, metalness: 0.7, envMapIntensity: 0.8 });
  const rubberMat = new T.MeshStandardMaterial({ color: 0x0b0d12, roughness: 0.85, metalness: 0.1 });
  const steelMat = new T.MeshStandardMaterial({ color: 0xdfe6ef, roughness: 0.18, metalness: 0.95, envMapIntensity: 1.1 });
  const accentCol = (accent != null) ? accent : 0xf4f7fb;   // secondary livery (stripe/roundel)
  const accentMat = new T.MeshStandardMaterial({ color: accentCol, roughness: 0.4, metalness: 0.1 });
  const litMat = new T.MeshStandardMaterial({ color: 0xffffff, emissive: col, emissiveIntensity: 1.3, roughness: 0.3 });
  const shadowMeshes = [];

  // --- shared chassis: low hull + sloped front glacis wedge -----------------
  const hoverLift = (eng === "hover") ? r * 0.22 : 0;   // hover floats the hull
  const hull = new T.Mesh(new T.BoxGeometry(r * 1.5, r * 0.6, r * 1.5), bodyMat);
  hull.position.set(-r * 0.1, r * 0.5 + hoverLift, 0);
  const glacis = new T.Mesh(new T.BoxGeometry(r * 1.05, r * 0.16, r * 1.5), bodyMat);
  glacis.position.set(r * 0.78, r * 0.3 + hoverLift, 0); glacis.rotation.z = -0.52;   // slopes down toward +X
  const skirtL = new T.Mesh(new T.BoxGeometry(r * 1.7, r * 0.34, r * 0.22), darkMat);
  const skirtR = skirtL.clone();
  skirtL.position.set(-r * 0.1, r * 0.34 + hoverLift, r * 0.78); skirtR.position.set(-r * 0.1, r * 0.34 + hoverLift, -r * 0.78);
  grp.add(hull, glacis, skirtL, skirtR);
  shadowMeshes.push(hull, glacis);

  // --- drivetrain per ENGINE archetype ---------------------------------------
  if (eng === "hover") {
    // no wheels: a rounded under-skirt + emissive underglow ring (reads "floating")
    const pad = new T.Mesh(new T.CylinderGeometry(r * 0.85, r * 0.95, r * 0.28, 18), darkMat);
    pad.position.set(-r * 0.1, r * 0.24, 0); grp.add(pad); shadowMeshes.push(pad);
    const glow = new T.Mesh(new T.RingGeometry(r * 0.55, r * 0.95, 24),
      new T.MeshBasicMaterial({ color: accentCol, transparent: true, opacity: 0.75,
        side: T.DoubleSide, blending: T.AdditiveBlending, depthWrite: false }));
    glow.rotation.x = -Math.PI / 2; glow.position.set(-r * 0.1, r * 0.1, 0); grp.add(glow);
  } else if (eng === "tank") {
    // full-length treads instead of wheels
    [[r * 0.78], [-r * 0.78]].forEach(([wz]) => {
      const tread = new T.Mesh(new T.BoxGeometry(r * 1.9, r * 0.55, r * 0.4), rubberMat);
      tread.position.set(-r * 0.05, r * 0.3, wz);
      grp.add(tread); shadowMeshes.push(tread);
      for (let i = 0; i < 3; i++) {   // roller hubs along the tread
        const hub = new T.Mesh(new T.CylinderGeometry(r * 0.14, r * 0.14, r * 0.44, 10), steelMat);
        hub.rotation.x = Math.PI / 2; hub.position.set(-r * 0.7 + i * r * 0.65, r * 0.3, wz);
        grp.add(hub);
      }
    });
  } else {
    // wheels (four, axis across Z) — standard AND sprint
    [[r * 0.55, r * 0.7], [r * 0.55, -r * 0.7], [-r * 0.7, r * 0.7], [-r * 0.7, -r * 0.7]].forEach(([wx, wz]) => {
      const w = new T.Mesh(new T.CylinderGeometry(r * 0.34, r * 0.34, r * 0.26, 16), rubberMat);
      w.rotation.x = Math.PI / 2; w.position.set(wx, r * 0.34, wz);
      const hubM = new T.Mesh(new T.CylinderGeometry(r * 0.13, r * 0.13, r * 0.28, 10), steelMat);
      hubM.rotation.x = Math.PI / 2; hubM.position.copy(w.position);
      grp.add(w, hubM); shadowMeshes.push(w);
    });
    if (eng === "sprint") {
      // twin rear exhausts with hot emissive tips — the "fast one"
      [r * 0.3, -r * 0.3].forEach((wz) => {
        const pipe = new T.Mesh(new T.CylinderGeometry(r * 0.09, r * 0.11, r * 0.6, 10), darkMat);
        pipe.rotation.z = Math.PI / 2; pipe.position.set(-r * 0.95, r * 0.55, wz);
        const tip = new T.Mesh(new T.SphereGeometry(r * 0.1, 8, 6),
          new T.MeshStandardMaterial({ color: 0xffaa44, emissive: 0xff8830, emissiveIntensity: 1.6, roughness: 0.3 }));
        tip.position.set(-r * 1.25, r * 0.55, wz);
        grp.add(pipe, tip);
      });
    }
  }

  // livery: accent racing stripe + roundel on the hull top
  const stripe = new T.Mesh(new T.BoxGeometry(r * 1.5, r * 0.04, r * 0.34), accentMat);
  stripe.position.set(-r * 0.1, r * 0.81 + hoverLift, 0); grp.add(stripe);
  const roundel = new T.Mesh(new T.CylinderGeometry(r * 0.3, r * 0.3, r * 0.04, 20), accentMat);
  roundel.position.set(-r * 0.35, r * 0.82 + hoverLift, 0); grp.add(roundel);
  const roundelC = new T.Mesh(new T.CylinderGeometry(r * 0.16, r * 0.16, r * 0.05, 18), bodyMat);
  roundelC.position.set(-r * 0.35, r * 0.83 + hoverLift, 0); grp.add(roundelC);

  // --- archetype weapon (the spinning identity) -----------------------------
  if (shape === "tank") {                 // heavy horizontal DRUM across the nose
    const pivot = new T.Group(); pivot.position.set(r * 1.15, r * 0.42, 0); pivot.rotation.x = Math.PI / 2;
    const drum = new T.Mesh(new T.CylinderGeometry(r * 0.42, r * 0.42, r * 1.35, 16), steelMat);
    for (let i = 0; i < 3; i++) {         // teeth
      const tooth = new T.Mesh(new T.BoxGeometry(r * 0.5, r * 0.18, r * 0.18), darkMat);
      tooth.position.set(0, 0, (i - 1) * r * 0.45); tooth.rotation.y = i * 0.6; drum.add(tooth);
    }
    pivot.add(drum); grp.add(pivot); shadowMeshes.push(drum);
    grp.userData.spinners.push({ obj: drum, prop: "y", rate: 18 });
  } else if (shape === "speeder") {       // overhead sweeping BAR spinner
    const post = new T.Mesh(new T.CylinderGeometry(r * 0.14, r * 0.16, r * 0.5, 10), darkMat);
    post.position.set(-r * 0.1, r * 1.0, 0); grp.add(post); shadowMeshes.push(post);
    const bar = new T.Mesh(new T.BoxGeometry(r * 2.5, r * 0.16, r * 0.26), steelMat);
    bar.position.set(-r * 0.1, r * 1.28, 0);
    [-1, 1].forEach((s) => { const wgt = new T.Mesh(new T.BoxGeometry(r * 0.3, r * 0.3, r * 0.34), darkMat);
      wgt.position.set(s * r * 1.15, 0, 0); bar.add(wgt); });
    grp.add(bar); shadowMeshes.push(bar);
    grp.userData.spinners.push({ obj: bar, prop: "y", rate: 22 });
  } else if (shape === "orb") {           // vertical BUZZSAW disc at the nose
    const pivot = new T.Group(); pivot.position.set(r * 1.2, r * 0.55, 0); pivot.rotation.z = Math.PI / 2;
    const disc = new T.Mesh(new T.CylinderGeometry(r * 0.85, r * 0.85, r * 0.1, 24), steelMat);
    for (let i = 0; i < 8; i++) {         // saw teeth around the rim
      const a = i / 8 * Math.PI * 2;
      const tooth = new T.Mesh(new T.ConeGeometry(r * 0.1, r * 0.28, 6), steelMat);
      tooth.position.set(Math.cos(a) * r * 0.9, 0, Math.sin(a) * r * 0.9);
      tooth.rotation.x = Math.PI / 2; tooth.rotation.z = -a; disc.add(tooth);
    }
    pivot.add(disc); grp.add(pivot); shadowMeshes.push(disc);
    grp.userData.spinners.push({ obj: disc, prop: "y", rate: 26 });
  } else {                                // spike: spinning SPIKED DRUM
    const pivot = new T.Group(); pivot.position.set(r * 1.1, r * 0.5, 0); pivot.rotation.x = Math.PI / 2;
    const drum = new T.Mesh(new T.CylinderGeometry(r * 0.46, r * 0.46, r * 1.2, 14), darkMat);
    for (let i = 0; i < 10; i++) {
      const a = i / 10 * Math.PI * 2;
      const sp = new T.Mesh(new T.ConeGeometry(r * 0.13, r * 0.4, 6), steelMat);
      sp.position.set(Math.cos(a) * r * 0.5, ((i % 3) - 1) * r * 0.35, Math.sin(a) * r * 0.5);
      sp.rotation.z = -Math.PI / 2; sp.rotation.y = a; drum.add(sp);
    }
    pivot.add(drum); grp.add(pivot); shadowMeshes.push(drum);
    grp.userData.spinners.push({ obj: drum, prop: "y", rate: 15 });
  }

  // glowing sensor eye + targeting pod (turret aims at enemies)
  const eye = new T.Mesh(new T.SphereGeometry(r * 0.14, 10, 8), litMat);
  eye.position.set(r * 0.2, r * 0.86 + hoverLift, 0); grp.add(eye);
  const turret = makeBarrel(col, r, gun);
  turret.position.set(-r * 0.3, r * 0.35 + hoverLift, 0);
  grp.add(turret);
  grp.userData.turret = turret;
  grp.userData.bodyMat = bodyMat;     // for hit-flash + damage tint
  grp.userData.baseCol = new T.Color(col);
  grp.userData.shadowMeshes = shadowMeshes;
  shadowMeshes.forEach((m) => { m.castShadow = true; });
  return grp;
}

// Blender-built robot: clone the matching GLB permutation, tint the body to
// team livery, and expose the same userData contract playback relies on
// (turret / spinners / bodyMat / baseCol). Returns null when the model is
// missing so the caller can fall back to the procedural mesh.
const sizeForR = (r) => (r <= 13 ? "small" : r <= 18 ? "medium" : "large");
function makeRobotMeshGLB(rf, col) {
  const name = `robot_${sizeForR(rf.r || 16)}_${rf.gun || "laser"}_${rf.eng || "standard"}`;
  const tpl = ROBOT_TPL[name];
  if (!tpl) return null;
  const grp = new T.Group();
  const model = tpl.scene.clone(true);
  // per-instance body material so tint + damage don't bleed across robots
  let bodyMat = null;
  model.traverse((o) => {
    if (!o.isMesh) return;
    o.castShadow = true;
    if (o.material && o.material.name && o.material.name.startsWith("body_")) {
      o.material = o.material.clone();
      o.material.color.setHex(col);
      o.material.emissive.setHex(col);
      o.material.emissiveIntensity = 0.28;
      bodyMat = o.material;
    }
  });
  // team-tint the second-largest material too so bots stop being monochrome
  // gray at distance (body_* is the largest; pick the biggest of the rest)
  const matSize = new Map();
  model.traverse((o) => {
    if (!o.isMesh || !o.material || !o.material.color) return;
    if ((o.material.name || "").startsWith("body_")) return;
    const nv = o.geometry && o.geometry.attributes.position ? o.geometry.attributes.position.count : 0;
    matSize.set(o.material, (matSize.get(o.material) || 0) + nv);
  });
  let second = null, secondN = 0;
  matSize.forEach((nv, m) => { if (nv > secondN) { secondN = nv; second = m; } });
  if (second) {
    const tinted = second.clone();
    tinted.color.lerp(new T.Color(col), 0.55);
    if (tinted.emissive) { tinted.emissive.setHex(col); tinted.emissiveIntensity = 0.1; }
    model.traverse((o) => { if (o.isMesh && o.material === second) o.material = tinted; });
  }
  // turret pivot: gun + muzzle effects yaw together to track the aim target
  const pivot = new T.Group();
  const turretNodes = [];
  model.traverse((o) => { if (/_(gun|flash|beam|spray)$/.test(o.name)) turretNodes.push(o); });
  if (turretNodes.length) {
    turretNodes[0].parent.add(pivot);
    turretNodes.forEach((n) => pivot.add(n));   // same parent frame; locals stay valid
  }
  // wheels are separate axle-origined nodes — playback rolls them by travel
  const wheels = [];
  model.traverse((o) => { if (/_wheel\d+$/.test(o.name)) wheels.push(o); });
  // shape spinner (the melee identity): attach + spin like the procedural bots
  const spinners = [];
  const SPIN_AXIS = { tank: ["x", 18], speeder: ["y", 22], orb: ["z", 26], spike: ["x", 15] };
  const spTpl = SPINNER_TPL[rf.shape || "tank"];
  if (spTpl) {
    const inst = spTpl.clone(true);
    inst.scale.setScalar((rf.r || 16) / 16);   // built at medium (r=16) reference
    inst.traverse((o) => { if (o.isMesh) o.castShadow = true; });
    model.add(inst);
    let spinNode = null;
    inst.traverse((o) => { if (o.name === "spin") spinNode = o; });
    const [prop, rate] = SPIN_AXIS[rf.shape] || ["x", 18];
    if (spinNode) spinners.push({ obj: spinNode, prop, rate });
  }
  model.rotation.y = -Math.PI / 2;   // GLB faces -Z; playback expects front = +X
  model.scale.setScalar(MODEL_SCALE);
  const lean = new T.Group();        // accel pitch / turn roll / hover bob rig
  lean.add(model);
  grp.add(lean);
  // baked per-weapon 'shoot' clip (recoil + muzzle flash/beam/spray)
  const mixer = new T.AnimationMixer(model);
  let fire = null;
  if (tpl.clip) { fire = mixer.clipAction(tpl.clip); fire.setLoop(T.LoopOnce); }
  grp.userData.spinners = spinners;
  grp.userData.shadowMeshes = [];
  grp.userData.turret = pivot;
  grp.userData.bodyMat = bodyMat;
  grp.userData.baseCol = new T.Color(col);
  grp.userData.mixer = mixer;
  grp.userData.fire = fire;
  grp.userData.wheels = wheels;
  grp.userData.lean = lean;
  grp.userData.hover = (rf.eng === "hover");
  // battle-damage bits: hidden wreckage that appears as HP falls — a torn
  // armour plate jutting off the deck, then a bent strut at critical damage
  const ru = rf.r || 16;
  const bitMat = new T.MeshStandardMaterial({ color: 0x171b24, roughness: 0.85, metalness: 0.55 });
  const plate = new T.Mesh(new T.BoxGeometry(ru * 0.55, ru * 0.07, ru * 0.42), bitMat);
  plate.position.set(-ru * 0.35, ru * 1.0, ru * 0.42);
  plate.rotation.set(0.55, 0.35, -0.4);
  const strut = new T.Mesh(new T.CylinderGeometry(ru * 0.04, ru * 0.04, ru * 0.75, 6), bitMat);
  strut.position.set(-ru * 0.5, ru * 1.05, -ru * 0.38);
  strut.rotation.set(0.3, 0, 0.95);
  plate.visible = strut.visible = false;
  grp.add(plate, strut);
  grp.userData.damageBits = [plate, strut];
  return grp;
}

// HP bar + name on one billboard sprite (canvas texture, redrawn on change).
// 512x160: readable from the stands; applyFrame keeps it screen-constant.
// Label geometry. LABEL_WORLD_H is world height per unit of camera distance —
// the trick that keeps a tag the same size on screen wherever the machine is —
// and LABEL_ASPECT matches the 512x160 canvas. The declutter pass below sizes
// its overlap test from these two, so a tag's real footprint and the footprint
// used to space tags apart cannot drift.
const LABEL_WORLD_H = 0.085, LABEL_ASPECT = 3.2;
// Below this many tags on screen there is nothing to declutter, and hiding one
// of two names in a bracket match would read as a bug rather than a tidy-up.
const LABEL_DECLUTTER_FROM = 8;
// Hysteresis band for the same pass: a tag already on screen keeps its place
// until the clash is this much tighter than the plain threshold, and one trying
// to claim a place needs this much more clearance than the plain threshold.
const LABEL_HOLD = 0.8, LABEL_CLAIM = 1.2;
// ...and how long a tag holds a state change before it may change again.
const LABEL_SETTLE_MS = 250;
function makeLabel() {
  const c = document.createElement("canvas"); c.width = 512; c.height = 160;
  const tex = new T.CanvasTexture(c); tex.colorSpace = T.SRGBColorSpace;
  const mat = new T.SpriteMaterial({ map: tex, transparent: true, depthTest: false });
  const sp = new T.Sprite(mat); sp.renderOrder = 10;
  sp.userData = { canvas: c, tex, last: "" };
  return sp;
}
// How long a dead machine's name tag survives it, in SECONDS OF PLAYBACK — the
// countdown is measured against the playhead, not the wall clock, so a paused
// or scrubbed timeline holds the tag instead of quietly dropping it while the
// operator is looking at a frozen frame.
const OUT_TAG_S = 2.5;
function drawLabel(sp, name, frac, alive, col) {
  const key = `${name}|${(frac * 50) | 0}|${alive}`;
  if (sp.userData.last === key) return;
  sp.userData.last = key;
  const c = sp.userData.canvas, g = c.getContext("2d");
  const colS = "#" + col.toString(16).padStart(6, "0");
  g.clearRect(0, 0, 512, 160);
  // team-colored plate behind the name
  g.fillStyle = "rgba(5,7,13,0.78)"; g.fillRect(12, 6, 488, 88);
  g.globalAlpha = 0.26; g.fillStyle = colS; g.fillRect(12, 6, 488, 88); g.globalAlpha = 1;
  g.strokeStyle = colS; g.lineWidth = 3; g.strokeRect(12, 6, 488, 88);
  // name: heavy weight, team color, black stroke
  const nm = alive ? name : name + " — OUT";
  g.font = "900 56px ui-sans-serif, sans-serif"; g.textAlign = "center"; g.textBaseline = "middle";
  g.lineWidth = 8; g.strokeStyle = "rgba(0,0,0,0.95)"; g.strokeText(nm, 256, 52);
  g.fillStyle = colS; g.fillText(nm, 256, 52);
  // hp bar: 28px tall, black outline, team-colored fill
  const bw = 440, bx = 36, by = 110, bh = 28;
  g.fillStyle = "#000"; g.fillRect(bx - 3, by - 3, bw + 6, bh + 6);
  g.fillStyle = "#1C1C1F"; g.fillRect(bx, by, bw, bh);
  g.fillStyle = colS; g.fillRect(bx, by, bw * Math.max(0, frac), bh);
  sp.userData.tex.needsUpdate = true;
}

const robotObjs = {};   // id -> { group, mesh, label, col, r, shape, dead }
function ensureRobot(rf) {
  let o = robotObjs[rf.id];
  if (o) return o;
  const col = colorFor(rf), r = rf.r || 16;
  const group = new T.Group();
  const accent = (typeof rf.accent === "string" && /^#[0-9a-fA-F]{6}$/.test(rf.accent))
    ? parseInt(rf.accent.slice(1), 16) : null;
  const mesh = makeRobotMeshGLB(rf, col)
    || makeRobotMesh(rf.shape || "tank", col, r, rf.gun || "laser", rf.eng || "standard", accent);
  // visual-only upscale (sim radius unchanged): robots read on a projector; the
  // house robot gets an extra bump so it LOOMS like the menace it is
  mesh.scale.setScalar(rf.team === "house" ? 2.0 : 1.8);
  const label = makeLabel(); label.position.y = r * 3.6; label.scale.set(124, 38, 1);
  // team ground-glow ring — each bot sits in a colored pool
  const ring = new T.Mesh(new T.RingGeometry(r * 1.6, r * 2.4, 36),
    new T.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.85, blending: T.AdditiveBlending, side: T.DoubleSide, depthWrite: false }));
  ring.rotation.x = -Math.PI / 2; ring.position.y = 1.5;
  group.add(mesh, label, ring);
  scene.add(group);
  o = robotObjs[rf.id] = { group, mesh, label, ring, col, r, shape: rf.shape,
    dead: false, prevPos: null, flash: 0 };
  return o;
}

// ============================================================================
// Rockets / mines (pooled by id)
// ============================================================================
const rocketObjs = {};   // id -> { group, prev:{x,y} }
function ensureRocket(rk) {
  let o = rocketObjs[rk.id];
  if (o) return o;
  const owner = robotObjs[rk.owner];
  const col = owner ? owner.col : 0xffaa33;
  const group = new T.Group();
  const head = new T.Mesh(new T.ConeGeometry(8, 30, 12),
    new T.MeshStandardMaterial({ color: 0xffffff, emissive: col, emissiveIntensity: 1.6, roughness: 0.3, metalness: 0.5 }));
  head.rotation.z = -Math.PI / 2;
  const body = new T.Mesh(new T.CylinderGeometry(6, 6, 16, 12),
    new T.MeshStandardMaterial({ color: 0xcfd6e2, roughness: 0.4, metalness: 0.7 }));
  body.rotation.z = Math.PI / 2; body.position.x = -14;
  const glow = new T.Sprite(new T.SpriteMaterial({ map: SPARK_TEX, color: col, blending: T.AdditiveBlending,
    transparent: true, depthWrite: false }));
  glow.scale.set(95, 95, 1);
  group.add(head, body, glow);
  scene.add(group);
  o = rocketObjs[rk.id] = { group, col, prev: null };
  return o;
}

const mineObjs = {};     // id -> { group, ring, armed }
function ensureMine(mn) {
  let o = mineObjs[mn.id];
  if (o) return o;
  const group = new T.Group();
  const dome = new T.Mesh(new T.SphereGeometry(11, 14, 8, 0, Math.PI * 2, 0, Math.PI / 2),
    new T.MeshStandardMaterial({ color: 0x333a48, roughness: 0.5, metalness: 0.7 }));
  dome.castShadow = true;
  const light = new T.Mesh(new T.SphereGeometry(4, 10, 8),
    new T.MeshStandardMaterial({ color: 0xff3344, emissive: 0xff3344, emissiveIntensity: 1.0 }));
  light.position.y = 11;
  const ring = new T.Mesh(new T.RingGeometry(26, 31, 36),
    new T.MeshBasicMaterial({ color: 0xff3344, transparent: true, opacity: 0.0, side: T.DoubleSide, blending: T.AdditiveBlending, depthWrite: false }));
  ring.rotation.x = -Math.PI / 2; ring.position.y = 1;
  group.add(dome, light, ring);
  scene.add(group);
  o = mineObjs[mn.id] = { group, ring, light };
  return o;
}

// ----- pickups (floating crates: rockets / traps / repair) -----------------
const PICKUP_COL = { rockets: 0xff9f43, traps: 0xb983ff, repair: 0x6bcb77,
                     overdrive: 0xff4455, shield: 0x4d96ff, haste: 0xffd93d };
const pickupObjs = {};   // id -> { group, core }
function ensurePickup(p) {
  let o = pickupObjs[p.id];
  if (o) return o;
  const col = PICKUP_COL[p.kind] || 0xffffff;
  const group = new T.Group();
  const core = new T.Mesh(new T.BoxGeometry(20, 20, 20),
    new T.MeshStandardMaterial({ color: col, emissive: col, emissiveIntensity: 0.7,
      roughness: 0.35, metalness: 0.5, transparent: true, opacity: 0.92 }));
  core.castShadow = true;
  const halo = new T.Mesh(new T.RingGeometry(20, 26, 28),
    new T.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.5,
      side: T.DoubleSide, blending: T.AdditiveBlending, depthWrite: false }));
  halo.rotation.x = -Math.PI / 2; halo.position.y = -16;
  const beam = new T.Mesh(new T.CylinderGeometry(2, 2, 60, 8),
    new T.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.25,
      blending: T.AdditiveBlending, depthWrite: false }));
  beam.position.y = 30;
  group.add(core, halo, beam);
  scene.add(group);
  o = pickupObjs[p.id] = { group, core };
  return o;
}

// ----- weather (global, from status.weather) -------------------------------
function applyWeather(kind) {
  // Scene fog is gone for good; weather only drives the badge now. "fog" can
  // still arrive from old recordings and keeps its badge (range effect is
  // engine-side), but nothing hazes the view.
  const el = document.getElementById("weather");
  if (kind === "fog") {
    if (el) { el.textContent = "🌫 FOG"; el.style.display = ""; }
  } else if (kind === "wind") {
    if (el) { el.textContent = "💨 WIND"; el.style.display = ""; }
  } else {
    if (el) el.style.display = "none";
  }
}

// ============================================================================
// FX: particle system (smoke + sparks), explosion shells, flash lights, beams
// ============================================================================
// Two pools: additive shrinking SPARKS + normal-alpha SMOKE that expands and
// darkens over its lifetime. The old single PointsMaterial never consumed the
// per-particle psize attribute, so everything rendered as one uniform blob.
// Local ShaderMaterials (vendored Three only): psize + normalized life drive
// gl_PointSize and opacity.
const PART_VERT = (sizeCurve) => `
  attribute float psize; attribute float plife; attribute vec3 pcol;
  varying float vLife; varying vec3 vCol;
  void main(){
    vLife = plife; vCol = pcol;
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = psize * (${sizeCurve}) * (280.0 / -mv.z);
    gl_Position = projectionMatrix * mv;
  }`;
const SPARK_FRAG = `
  uniform sampler2D uTex; varying float vLife; varying vec3 vCol;
  void main(){ vec4 t = texture2D(uTex, gl_PointCoord);
    gl_FragColor = vec4(vCol * vLife, t.a * vLife); }`;
const SMOKE_FRAG = `
  uniform sampler2D uTex; varying float vLife; varying vec3 vCol;
  void main(){ vec4 t = texture2D(uTex, gl_PointCoord);
    gl_FragColor = vec4(vCol * (0.35 + 0.65 * vLife), t.a * vLife * 0.6); }`;
function makePool(cap, sizeCurve, frag, blending) {
  const pos = new Float32Array(cap * 3), col = new Float32Array(cap * 3);
  const sz = new Float32Array(cap), lf = new Float32Array(cap);
  const items = new Array(cap);
  for (let i = 0; i < cap; i++) items[i] = { life: 0, max: 1, vx: 0, vy: 0, vz: 0,
    x: 0, y: 0, z: 0, drag: 0.9, grav: 0, r: 0, g: 0, b: 0, size: 22 };
  const geo = new T.BufferGeometry();
  geo.setAttribute("position", new T.BufferAttribute(pos, 3));
  geo.setAttribute("pcol", new T.BufferAttribute(col, 3));
  geo.setAttribute("psize", new T.BufferAttribute(sz, 1));
  geo.setAttribute("plife", new T.BufferAttribute(lf, 1));
  const mat = new T.ShaderMaterial({ uniforms: { uTex: { value: SPARK_TEX } },
    vertexShader: PART_VERT(sizeCurve), fragmentShader: frag,
    transparent: true, depthWrite: false, blending });
  const points = new T.Points(geo, mat);
  points.frustumCulled = false; scene.add(points);
  return { cap, items, pos, col, sz, lf, geo, head: 0 };
}
const SPARKS = makePool(1600, "0.35 + 0.65 * plife", SPARK_FRAG, T.AdditiveBlending);  // shrink as they die
const SMOKE = makePool(1000, "1.6 - 0.8 * plife", SMOKE_FRAG, T.NormalBlending);       // expand + darken

// size: optional birth size in world units (default 22; pass 2-3x for impacts).
// Dark particles route to the smoke pool, bright ones to the additive sparks.
function spawnParticle(x, y, z, vx, vy, vz, life, r, g, b, drag, grav, size) {
  const P = (r + g + b) < 1.05 ? SMOKE : SPARKS;
  const p = P.items[P.head]; P.head = (P.head + 1) % P.cap;
  p.x = x; p.y = y; p.z = z; p.vx = vx; p.vy = vy; p.vz = vz;
  p.life = life; p.max = life; p.drag = drag; p.grav = grav;
  p.r = r; p.g = g; p.b = b; p.size = size || 22;
}
function updatePool(P, dt) {
  for (let i = 0; i < P.cap; i++) {
    const p = P.items[i]; const o = i * 3;
    if (p.life > 0) {
      p.life -= dt;
      p.vy -= p.grav * dt;
      p.vx *= p.drag; p.vy *= p.drag; p.vz *= p.drag;
      p.x += p.vx * dt; p.y += p.vy * dt; p.z += p.vz * dt;
      const f = Math.max(0, p.life / p.max);
      P.pos[o] = p.x; P.pos[o + 1] = p.y; P.pos[o + 2] = p.z;
      P.col[o] = p.r; P.col[o + 1] = p.g; P.col[o + 2] = p.b;
      P.sz[i] = p.size; P.lf[i] = f;
    } else {
      P.pos[o + 1] = -99999; P.lf[i] = 0;
    }
  }
  P.geo.attributes.position.needsUpdate = true;
  P.geo.attributes.pcol.needsUpdate = true;
  P.geo.attributes.psize.needsUpdate = true;
  P.geo.attributes.plife.needsUpdate = true;
}
function updateParticles(dt) { updatePool(SPARKS, dt); updatePool(SMOKE, dt); }

// floating damage numbers: world-space sprites that pop, rise and fade.
// Size + colour scale with the hit; repairs float up green.
const dmgFloats = [];
function damageNumber(wx, wz, y, amount, heal) {
  const c = document.createElement("canvas"); c.width = 160; c.height = 80;
  const g = c.getContext("2d");
  const big = amount >= 25, mid = amount >= 10;
  const col = heal ? "#5dde84" : big ? "#ff5040" : mid ? "#ffd93d" : "#e8edf7";
  g.font = `900 ${big ? 58 : mid ? 46 : 36}px ui-sans-serif, sans-serif`;
  g.textAlign = "center"; g.textBaseline = "middle";
  g.lineWidth = 8; g.strokeStyle = "rgba(5,8,14,0.9)";
  const txt = (heal ? "+" : "-") + amount;
  g.strokeText(txt, 80, 40);
  g.fillStyle = col; g.fillText(txt, 80, 40);
  const tex = new T.CanvasTexture(c);
  const sp = new T.Sprite(new T.SpriteMaterial({ map: tex, transparent: true, depthTest: false }));
  sp.renderOrder = 11;
  // distance-scaled so big hits hold ~40px screen height at any zoom
  const camDist = camera.position.distanceTo(new T.Vector3(wx, y, wz));
  const s = (big ? 116 : mid ? 90 : 66) * Math.max(1, camDist / 900);
  sp.scale.set(s, s / 2, 1);
  sp.position.set(wx + (Math.random() - 0.5) * 22, y, wz + (Math.random() - 0.5) * 10);
  scene.add(sp);
  dmgFloats.push({ sp, life: 1.8, max: 1.8, vy: big ? 34 : 27 });
}
function updateDmgFloats(dt) {
  for (let i = dmgFloats.length - 1; i >= 0; i--) {
    const f = dmgFloats[i];
    f.life -= dt;
    f.sp.position.y += f.vy * dt;
    // hold at full strength, then fade over the last 40%
    f.sp.material.opacity = Math.max(0, Math.min(1, (f.life / f.max) / 0.4));
    if (f.life <= 0) {
      scene.remove(f.sp);
      f.sp.material.map.dispose(); f.sp.material.dispose();
      dmgFloats.splice(i, 1);
    }
  }
}

// explosion shells, ground shockwaves, debris chunks, scorch decals, flash lights
const shells = [];
const shockwaves = [];
const debris = [];
const scorches = [];
function spawnDebris(wx, wz, col, r) {
  for (let i = 0; i < 18; i++) {
    const m = new T.Mesh(new T.BoxGeometry(r * 0.3, r * 0.3, r * 0.3),
      new T.MeshStandardMaterial({ color: col, roughness: 0.5, metalness: 0.6, emissive: col, emissiveIntensity: 0.2 }));
    m.position.set(wx, r * 0.6, wz); m.castShadow = true; scene.add(m);
    const a = Math.random() * Math.PI * 2, sp = 120 + Math.random() * 220;
    debris.push({ mesh: m, life: 1.3, max: 1.3,
      vx: Math.cos(a) * sp, vy: 180 + Math.random() * 160, vz: Math.sin(a) * sp,
      rx: (Math.random() - 0.5) * 12, rz: (Math.random() - 0.5) * 12 });
  }
}
function scorchDecal(wx, wz, rad) {
  const m = new T.Mesh(new T.CircleGeometry(rad, 24),
    new T.MeshBasicMaterial({ color: 0x05070a, transparent: true, opacity: 0.55, depthWrite: false }));
  m.rotation.x = -Math.PI / 2; m.position.set(wx, 0.6, wz); scene.add(m);
  // decals persist for the whole match (a 2-minute war marks the floor); past
  // the cap the oldest get a 1.5s fade before eviction in updateFX
  scorches.push({ mesh: m, life: Infinity, max: Infinity });
  let over = scorches.length - 64;
  for (let i = 0; i < scorches.length && over > 0; i++)
    if (scorches[i].life === Infinity) { scorches[i].life = 1.5; over--; }
}
let fovPunch = 0;          // transient FOV kick on blasts
let introT = 0;            // cinematic intro sweep timer
let slowmoT = 0, slowmoFired = false;  // slow-mo on the deciding blow
const lastHp = {};         // id -> hp at last tick (hit-flash detection)
// A tick-rate DoT (sudden-death fire floor) fires a floating number every
// simulation tick (18-90/sec); each lives 1.8s on screen, so without a
// wall-clock floor between spawns they permanently overlap into a pile.
// Coalesce: skip the sprite if one just fired for this bot, fold the missed
// amount into the next one so no damage total is lost, only its granularity.
const lastDmgAt = {}, dmgPending = {};
const DMG_COALESCE_MS = 90;
let endFired = false;      // one-shot guard so RW.onMatchEnd fires once per match
let celebrate = null, bannerTimer = null;   // victory hero shot + delayed banner reveal
const lbEl = document.getElementById("letterbox");
function setLetterbox(on) { if (lbEl) lbEl.classList.toggle("show", on); }
const FLASHES = [];
for (let i = 0; i < 5; i++) {
  const l = new T.PointLight(0xffaa44, 0, 1400); scene.add(l); FLASHES.push({ light: l, life: 0 });
}
function flash(x, y, z, color, power) {
  let f = FLASHES.find((q) => q.life <= 0) || FLASHES[0];
  f.light.position.set(x, y, z); f.light.color.setHex(color);
  f.life = 0.45; f.max = 0.45; f.power = power; f.light.intensity = power;
}
function explode(x, z, blastR, col) {
  const wx = x - AW / 2, wz = z - AH / 2;
  // expanding shell
  const shell = new T.Mesh(new T.SphereGeometry(1, 18, 14),
    new T.MeshBasicMaterial({ color: col || 0xffdd88, transparent: true, opacity: 0.85, blending: T.AdditiveBlending, depthWrite: false }));
  shell.position.set(wx, blastR * 0.4, wz); scene.add(shell);
  shells.push({ mesh: shell, life: 0.5, max: 0.5, target: blastR });
  // fire + sparks
  const n = Math.min(160, 40 + (blastR | 0));
  for (let i = 0; i < n; i++) {
    const a = Math.random() * Math.PI * 2, el = Math.random() * Math.PI * 0.5;
    const sp = 120 + Math.random() * 320;
    const dx = Math.cos(a) * Math.cos(el), dy = Math.sin(el), dz = Math.sin(a) * Math.cos(el);
    const hot = Math.random();
    spawnParticle(wx, blastR * 0.3, wz, dx * sp, dy * sp + 80, dz * sp,
      0.3 + Math.random() * 0.5, 1.0, 0.5 + hot * 0.4, hot * 0.3, 0.86, 380);
  }
  // lingering smoke plume
  for (let i = 0; i < 18; i++)
    spawnParticle(wx + (Math.random() - 0.5) * blastR * 0.4, blastR * 0.3, wz + (Math.random() - 0.5) * blastR * 0.4,
      (Math.random() - 0.5) * 30, 20 + Math.random() * 40, (Math.random() - 0.5) * 30, 1.1, 0.22, 0.2, 0.2, 0.93, 30);
  // flat ground shockwave ring
  const sw = new T.Mesh(new T.RingGeometry(1, 1.25, 48),
    new T.MeshBasicMaterial({ color: col || 0xffd488, transparent: true, opacity: 0.8, side: T.DoubleSide, blending: T.AdditiveBlending, depthWrite: false }));
  sw.rotation.x = -Math.PI / 2; sw.position.set(wx, 3, wz); scene.add(sw);
  shockwaves.push({ mesh: sw, life: 0.55, max: 0.55, target: blastR * 2.2 });
  flash(wx, blastR * 0.6, wz, col || 0xffbb55, Math.min(6, 2 + blastR / 30));
  shake(Math.min(26, blastR * 0.22));
  fovPunch = Math.min(6, fovPunch + blastR * 0.05);
  scorchDecal(wx, wz, blastR * 0.7);
  if (blastR >= 70) screenFlash(0.5);
}
// Loop-driven white flash (no nested rAF: a stalled frame used to strand a
// white veil over everything). Alpha clamped so blasts never bleach the frame.
const flashEl = document.getElementById("flash");
let flashT = 0;
function screenFlash(a) { flashT = Math.max(flashT, Math.min(a, 0.3)); }

// gun tracers (pooled). One geometry, per-shot scale: laser = thin needle,
// cannon = fat white-hot slug, shotgun = a 3-beam fan.
const beams = [];
for (let i = 0; i < 12; i++) {
  const m = new T.Mesh(new T.CylinderGeometry(1, 1, 1, 6),
    new T.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0, blending: T.AdditiveBlending, depthWrite: false }));
  scene.add(m); beams.push({ mesh: m, life: 0 });
}
function beamOne(a, c, col, thick, life) {
  const b = beams.find((q) => q.life <= 0) || beams[0];
  const mid = a.clone().add(c).multiplyScalar(0.5);
  const len = a.distanceTo(c);
  b.mesh.position.copy(mid);
  b.mesh.scale.set(thick, len, thick);
  b.mesh.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), c.clone().sub(a).normalize());
  b.mesh.material.color.setHex(col);
  b.life = life; b.max = life;
}
// cannon slug: a glowing sprite that travels muzzle -> impact over ~80ms (same
// pattern as the rocket objects); the heavy burst lands when it arrives
const slugs = [];
function fireSlug(a, c, thickMul) {
  const sp = new T.Sprite(new T.SpriteMaterial({ map: SPARK_TEX, color: 0xfff2dd,
    blending: T.AdditiveBlending, transparent: true, depthWrite: false }));
  sp.scale.set(30 * Math.max(1, thickMul), 30 * Math.max(1, thickMul), 1);
  sp.position.copy(a); scene.add(sp);
  slugs.push({ sp, from: a.clone(), to: c.clone(), t: 0, dur: 0.08 });
}
function beam(ax, az, bx, bz, col, gun, miss) {
  const a = new T.Vector3(ax - AW / 2, 24, az - AH / 2);
  const c = new T.Vector3(bx - AW / 2, 24, bz - AH / 2);
  // tracers hold 2-3px at any zoom: thickness scales with camera distance
  const thickMul = camera.position.distanceTo(c) / 900;
  if (miss) {
    // the shot sails PAST — a dimmer tracer with no impact FX. The crowd groans.
    beamOne(a, c, col, (gun === "cannon" ? 3.2 : 1.1) * thickMul, 0.25);
    return;
  }
  if (gun === "cannon") {
    fireSlug(a, c, thickMul);                      // burst lands on arrival (updateFX)
    for (let i = 0; i < 6; i++)                    // muzzle smoke
      spawnParticle(a.x, a.y, a.z, (Math.random() - 0.5) * 40, 20 + Math.random() * 30, (Math.random() - 0.5) * 40,
        0.5, 0.3, 0.3, 0.3, 0.9, 20, 30);
  } else if (gun === "shotgun") {
    // 5-tracer cone + a muzzle smoke puff
    const dir = c.clone().sub(a);
    beamOne(a, c, col, 1.2 * thickMul, 0.14);
    [-0.3, -0.15, 0.15, 0.3].forEach((off) => {
      const spread = dir.clone().applyAxisAngle(new T.Vector3(0, 1, 0), off).multiplyScalar(0.82);
      beamOne(a, a.clone().add(spread), col, 1.2 * thickMul, 0.14);
    });
    for (let i = 0; i < 5; i++)
      spawnParticle(a.x, a.y, a.z, (Math.random() - 0.5) * 30, 15 + Math.random() * 25, (Math.random() - 0.5) * 30,
        0.45, 0.28, 0.28, 0.28, 0.9, 15, 26);
    for (let i = 0; i < 12; i++)
      spawnParticle(c.x, c.y, c.z, (Math.random() - 0.5) * 200, Math.random() * 130, (Math.random() - 0.5) * 200,
        0.22, 1, 0.85, 0.45, 0.85, 210, 44);
  } else {   // laser: white-hot core inside a team-color glow shell
    beamOne(a, c, 0xffffff, 0.7 * thickMul, 0.3);
    beamOne(a, c, col, 2.2 * thickMul, 0.3);
    for (let i = 0; i < 10; i++)
      spawnParticle(c.x, c.y, c.z, (Math.random() - 0.5) * 160, Math.random() * 120, (Math.random() - 0.5) * 160,
        0.25, 1, 0.9, 0.5, 0.85, 200, 40);
  }
}

// ----- camera: auto-follow + screen shake + manual orbit override -----------
const BASE_FOV = 48, INTRO_MAX = 2.8;
const cam = {
  tgt: new T.Vector3(0, 0, 0), pos: new T.Vector3(0, 1400, 1400),
  desiredTgt: new T.Vector3(), desiredPos: new T.Vector3(),
  shakeAmt: 0, auto: true, az: 0.7, el: 0.50, distMul: 1.0, dragging: false, px: 0, py: 0,
  focus: null,
  // auto-director state machine: OVERVIEW | ACTION | FINAL_BLOW | VICTORY | MANUAL
  state: "OVERVIEW", override: false, overrideUntil: 0, manualTgt: null, manualBaseDist: 900,
  clusterId: null, clusterHold: 0,   // free-for-all shot selection, see pickCluster
};
function shake(a) { cam.shakeAmt = Math.min(40, cam.shakeAmt + a); }

// Free-for-all cluster framing. The 41-entrant format puts the whole field on
// the floor at once, and framing the centroid of everything alive pins the
// camera at its far clamp for the entire match: forty dots, no fight to watch.
// Pick the densest knot of machines instead and frame only that.
//
// Density, not damage: the kill-cam and proximity focus below already own the
// individual moments, and a shot that chases every hit is unwatchable.
//
// The CHOICE is deterministic — no randomness, and ties break on the lower
// robot id, so the same field of machines always picks the same shot. The
// TIMING is not: the dwell counts down on wall-clock dt like every other
// camera easing here, so two playbacks at different frame rates can cut at
// slightly different moments. That is the existing behaviour of this camera
// and is separate from the engine's byte-identical replay guarantee, which
// covers the simulation and not the framing of it.
const CLUSTER_MIN = 3;        // fewer machines than this is not a scrum, read wide
const CLUSTER_DWELL = 2.5;    // seconds a chosen shot is held before reconsidering
function clusterRadius() { return Math.min(AW, AH) * 0.34; }   // "in the same fight"
function membersOf(pts, seed, r) {
  return pts.filter((p) => Math.hypot(p.x - seed.x, p.z - seed.z) <= r);
}
// the scan below only needs the count — allocating an array per candidate per
// frame is pure garbage in the render loop
function countNear(pts, seed, r) {
  let c = 0;
  for (const p of pts) if (Math.hypot(p.x - seed.x, p.z - seed.z) <= r) c++;
  return c;
}
function pickCluster(pts, dt) {
  const r = clusterRadius();
  cam.clusterHold -= dt;
  // hold the current shot while it is still a scrum and the dwell has not run
  // out — without this the camera ping-pongs between skirmishes every frame
  if (cam.clusterId != null) {
    const seed = pts.find((p) => p.id === cam.clusterId);
    const held = seed ? membersOf(pts, seed, r) : [];
    if (held.length >= CLUSTER_MIN && cam.clusterHold > 0) return held;
  }
  let best = null, bestN = 0;
  for (const p of pts) {
    const c = countNear(pts, p, r);
    if (c > bestN || (c === bestN && best && p.id < best.id)) { best = p; bestN = c; }
  }
  if (bestN < CLUSTER_MIN) { cam.clusterId = null; return pts; }   // nothing knotted up
  cam.clusterId = best.id; cam.clusterHold = CLUSTER_DWELL;
  return membersOf(pts, best, r);
}
// Auto-director state machine. Every frame resolves to exactly one owner of the
// camera and records it on cam.state:
//   VICTORY    — exclusive winner hero shot (bypasses centroid framing)
//   MANUAL     — operator control: auto off, or a live drag-override
//   FINAL_BLOW — kill-cam slow-mo punch-in on the deciding blow
//   ACTION     — tight tracking of a close engagement
//   OVERVIEW   — wide read (pre-fight, or robots far apart)
// State is chosen deterministically from playback signals; the only randomness
// in this path is the cosmetic screen-shake jitter in the shared tail below.
function updateCamera(dt, alivePts) {
  // expire a temporary drag-override back to the armed auto states
  if (cam.override && performance.now() >= cam.overrideUntil) { cam.override = false; syncCamBtn(); }
  const manual = !cam.auto || cam.override;
  let k;

  if (celebrate && cam.auto) {
    // VICTORY — frame the WINNER robot directly (not the survivor centroid).
    // The main loop's celebrate block drives the slow orbital drift (cam.az)
    // and the winner's ring/emissive pump; here we just own the framing.
    cam.state = "VICTORY";
    const wo = robotObjs[celebrate.id];
    const wx = wo ? wo.group.position.x : cam.tgt.x, wz = wo ? wo.group.position.z : cam.tgt.z;
    const vel = Math.max(0.30, cam.el * 0.82);         // slightly lower — heroic angle
    const vdist = Math.max(260, 360 * cam.distMul);    // ~0.5x the normal framing clamp
    const vhor = Math.cos(vel) * vdist;
    cam.desiredTgt.set(wx, 44, wz);
    cam.desiredPos.set(wx + Math.cos(cam.az) * vhor, Math.sin(vel) * vdist + 64, wz + Math.sin(cam.az) * vhor);
    cam.manualTgt = null;
    k = 1 - Math.pow(0.03, dt);
  } else if (manual) {
    // MANUAL — hold the operator's target, orbit (az/el) and zoom (distMul);
    // never recompute the centroid/framing. Freeze the target + base distance
    // at hand-off so auto framing stops fighting the drag.
    cam.state = "MANUAL";
    if (!cam.manualTgt) {
      cam.manualTgt = cam.desiredTgt.clone();
      cam.manualBaseDist = cam.pos.distanceTo(cam.tgt) / (cam.distMul || 1);
    }
    const tg = cam.manualTgt, mdist = cam.manualBaseDist * cam.distMul, mhor = Math.cos(cam.el) * mdist;
    cam.desiredTgt.copy(tg);
    cam.desiredPos.set(tg.x + Math.cos(cam.az) * mhor, Math.sin(cam.el) * mdist + 60, tg.z + Math.sin(cam.az) * mhor);
    k = 1 - Math.pow(0.02, dt);
  } else {
    cam.manualTgt = null;
    // ---- automatic framing (OVERVIEW / ACTION / FINAL_BLOW) ----
    // frame the action: centroid + spread of alive robots (tight base framing —
    // fill the frame with the fight)
    let cx = 0, cz = 0, spread = 300, maxR = 0;
    const n = alivePts.length;
    // Frame the chosen cluster, not the whole field. `n` stays the true alive
    // count below — the 1v1 tuning and the OVERVIEW/ACTION test both key off it.
    const framed = n > 2 ? pickCluster(alivePts, dt) : alivePts;
    framed.forEach((p) => { cx += p.x; cz += p.z; });
    if (framed.length) {
      cx /= framed.length; cz /= framed.length;
      framed.forEach((p) => { maxR = Math.max(maxR, Math.hypot(p.x - cx, p.z - cz)); });
      spread = Math.max(n <= 2 ? 220 : 300, maxR + 120);
    }
    // proximity action-cam: the last two machines closing to brawl range earn a
    // punch-in on their midpoint (kill-cam focus values are owned elsewhere)
    if (n === 2 && (!cam.focus || cam.focus.life <= 0 || cam.focus.prox)) {
      const pdx = alivePts[0].x - alivePts[1].x, pdz = alivePts[0].z - alivePts[1].z;
      if (Math.hypot(pdx, pdz) < 400)
        cam.focus = { x: (alivePts[0].x + alivePts[1].x) / 2, z: (alivePts[0].z + alivePts[1].z) / 2,
                      life: 2.5, max: 2.5, zoom: 0.75, prox: true };
    }
    // focus (kill-cam or proximity): bias toward the moment + punch in
    let focusZoom = 1, focused = false, killFocus = false;
    if (cam.focus && cam.focus.life > 0) {
      cam.focus.life -= dt;
      focused = true; killFocus = !cam.focus.prox;    // non-proximity focus == kill-cam
      const ff = cam.focus.life / cam.focus.max;       // 1 -> 0
      const bias = cam.focus.bias != null ? cam.focus.bias : 0.55;
      const zoom = cam.focus.zoom != null ? cam.focus.zoom : 0.6;
      cx += (cam.focus.x - cx) * bias * ff; cz += (cam.focus.z - cz) * bias * ff;
      focusZoom = 1 - (1 - zoom) * ff;
    }
    // classify OVERVIEW vs ACTION deterministically: pre-fight or robots far
    // apart read wide; a close engagement tracks tight. FINAL_BLOW owns the
    // kill-cam / deciding-blow slow-mo.
    const engaged = n === 2
      ? Math.hypot(alivePts[0].x - alivePts[1].x, alivePts[0].z - alivePts[1].z) < 520
      : n > 2 ? maxR < 520 : n === 1;
    cam.state = (slowmoT > 0 || killFocus) ? "FINAL_BLOW"
              : (introT > 0 || !engaged) ? "OVERVIEW" : "ACTION";
    // cinematic intro sweep: start LOW and CLOSE, crane up and settle
    let azOff = 0, introZoom = 1, kBoost = 0, el = cam.el;
    if (introT > 0) {
      introT = Math.max(0, introT - dt);
      const e = introT / INTRO_MAX;                    // 1 -> 0
      azOff = e * e * 0.9; introZoom = 1 - e * 0.25; kBoost = 0.02 * e;
      el = 0.30 + (cam.el - 0.30) * (1 - e);
    }
    cam.desiredTgt.set(cx, 28, cz);
    let dist = Math.max(650, Math.min(1800, spread * 1.5)) * cam.distMul * focusZoom * introZoom;
    if (n <= 2 && n > 0) dist *= 0.85;               // the bracket 1v1 format: tighter still
    if (cam.state === "OVERVIEW" && !focused) dist *= 1.12;   // ease back for the wide read
    const az = cam.az + azOff;
    const hor = Math.cos(el) * dist;
    cam.desiredPos.set(cx + Math.cos(az) * hor, Math.sin(el) * dist + 60, cz + Math.sin(az) * hor);
    // raised lerp while focused so the punch-in is felt
    k = 1 - Math.pow((focused ? 0.02 : 0.0025) + kBoost, dt);   // framerate-independent
  }

  cam.tgt.lerp(cam.desiredTgt, k);
  cam.pos.lerp(cam.desiredPos, k);
  const wantFov = BASE_FOV + fovPunch;
  if (Math.abs(camera.fov - wantFov) > 0.05) { camera.fov = wantFov; camera.updateProjectionMatrix(); }
  cam.shakeAmt *= Math.pow(0.0009, dt);
  const sxv = (Math.random() - 0.5) * cam.shakeAmt, syv = (Math.random() - 0.5) * cam.shakeAmt, szv = (Math.random() - 0.5) * cam.shakeAmt;
  camera.position.set(cam.pos.x + sxv, cam.pos.y + syv, cam.pos.z + szv);
  camera.lookAt(cam.tgt.x, cam.tgt.y, cam.tgt.z);
}

// ============================================================================
// playback
// ============================================================================
function angleLerp(a, b, t) {
  let d = ((b - a + 540) % 360) - 180;
  return a + d * t;
}
function frameAt(i) { return frames[Math.max(0, Math.min(frames.length - 1, i))]; }

// --- robot entrances: each machine drives in through the nearest gate tunnel
// before the fight, staggered, with a name stinger — then FIGHT! ------------
let entrance = null;   // { t, startedAt } while the walk-on is running
const ENTR_STAG = 1.5, ENTR_DUR = 2.6, ENTR_CAP = 8.0;
function startEntrance() {
  entrance = { t: -0.3, startedAt: performance.now() };
  Object.values(robotObjs).forEach((o) => { o.announced = false; });
  play(false);
}
function updateEntrance(dt) {
  if (playing) { entrance = null; return; }          // pressing ▶ skips the show
  const elapsed = (performance.now() - entrance.startedAt) / 1000;
  entrance.t = -0.3 + elapsed;
  if (elapsed >= ENTR_CAP) { entrance = null; play(true); return; }
  const bots = frames[0].robots.filter((r) => r.team !== "house");
  // Big rosters would take a minute to file in at the 1v1 pacing — compress
  // the stagger so the whole walk-on fits inside ENTR_CAP seconds. Small
  // fields (bracket matches) keep the full dramatic spacing.
  const stag = Math.min(ENTR_STAG, Math.max(0.08, (ENTR_CAP - ENTR_DUR) / Math.max(1, bots.length - 1)));
  let allDone = bots.length > 0;
  bots.forEach((rf, i) => {
    const o = robotObjs[rf.id];
    if (!o) { allDone = false; return; }
    const hx = rf.x - AW / 2, hz = rf.y - AH / 2;
    // nearest mid-side gate (the colosseum has a tunnel on each side)
    const gates = [[0, -AH / 2 - 170], [0, AH / 2 + 170], [-AW / 2 - 170, 0], [AW / 2 + 170, 0]];
    let g = gates[0], best = Infinity;
    gates.forEach((gt) => { const d = (gt[0] - hx) ** 2 + (gt[1] - hz) ** 2; if (d < best) { best = d; g = gt; } });
    const pg = (entrance.t - i * stag) / ENTR_DUR;
    if (pg >= 1) return;                             // parked on its spawn mark
    allDone = false;
    const e = pg <= 0 ? 0 : pg < 0.5 ? 4 * pg * pg * pg : 1 - Math.pow(-2 * pg + 2, 3) / 2; // ease-in-out drive
    const px = g[0] + (hx - g[0]) * e, pz = g[1] + (hz - g[1]) * e;
    o.group.position.set(px, 0, pz);
    // The gate sits 170 outside the floor, past the colosseum's wall — driving
    // the bot from there means it visibly crosses solid geometry. Keep it hidden
    // until it clears the boundary so it emerges from the tunnel mouth instead.
    o.group.visible = Math.abs(px) <= AW / 2 + 10 && Math.abs(pz) <= AH / 2 + 10;
    o.mesh.rotation.y = -Math.atan2(hz - g[1], hx - g[0]);
    if (pg > 0) {
      o.wheelRot = (o.wheelRot || 0) - dt * 8;       // wheels rolling on the way in
      (o.mesh.userData.wheels || []).forEach((w) => { w.rotation.x = o.wheelRot; });
      if (!o.announced) {
        o.announced = true;
        // a compressed walk-on would machine-gun the name cards — keep the
        // ticker roll but save the stinger for fields with room to breathe
        if (stag >= 0.5) stinger(botLabel(rf));
        ticker(`🎙 ${botLabel(rf)} enters the arena`);
        // comical walk-on roast, queued so 16 entrances read as a roll-call
        // instead of every bot talking over the last
        playClip(["enter_" + slug(rf.name)], null, { queue: true });
      }
    }
  });
  if (allDone) { entrance = null; play(true); }
}

function applyFrame(t) {
  const i = Math.floor(t), frac = t - i;
  const cur = frameAt(i), nxt = frameAt(i + 1);
  const nxtById = {}; nxt.robots.forEach((r) => nxtById[r.id] = r);
  const alivePts = [];

  // pre-pass: interpolated world positions (turret aim needs everyone's pos)
  const wp = {};
  cur.robots.forEach((rf) => {
    const r2 = nxtById[rf.id] || rf;
    wp[rf.id] = {
      wx: (rf.x + (r2.x - rf.x) * frac) - AW / 2,
      wz: (rf.y + (r2.y - rf.y) * frac) - AH / 2,
      hdg: angleLerp(rf.heading, r2.heading, frac) * Math.PI / 180,
      alive: rf.alive,
    };
  });

  const seen = {};
  cur.robots.forEach((rf) => {
    seen[rf.id] = 1;
    const o = ensureRobot(rf);
    const p = wp[rf.id], wx = p.wx, wz = p.wz;
    o.group.position.set(wx, 0, wz);
    if (o.airborne) {   // flipper launch: fly a shallow arc while the throw lands
      const pa = (performance.now() - o.airborne.start) / 620;
      if (pa >= 1) o.airborne = null;
      else o.group.position.y = Math.sin(Math.PI * pa) * 70;
    }
    o.mesh.rotation.y = -p.hdg;
    const frac_hp = Math.max(0, rf.hp / rf.max_hp);
    // damage-state callout on the way DOWN through each band (never on repair,
    // never twice for the same band), and never for the last band before death —
    // the elimination line owns that beat
    if (rf.alive) {
      const band = frac_hp > 0.75 ? 100 : frac_hp > 0.5 ? 75
        : frac_hp > 0.4 ? 50 : frac_hp > 0.25 ? 40 : 25;
      const prev = hpBand[rf.id];
      if (prev === undefined) hpBand[rf.id] = band;
      else if (band < prev) {
        hpBand[rf.id] = band;
        // 40 is the colour-commentary beat ("it's not looking good for X"); the
        // rest are the plain percentage calls
        const key = BAND_CLIP[band];
        playClip([key + "_" + voiceKey[rf.id], key]);
      }
    }
    const flipped = rf.alive && (rf.flip | 0) > 0, jammed = rf.alive && (rf.jam | 0) > 0;
    const venting = rf.alive && (rf.vent | 0) > 0;
    let nm = botLabel(rf);
    if (flipped) nm += " 🙃"; else if (jammed) nm += " ⚙"; else if (venting) nm += " ♨";
    if ((rf.od | 0) > 0) nm += " ⚡"; if ((rf.sh | 0) > 0) nm += " 🛡"; if ((rf.hs | 0) > 0) nm += " 💨";
    drawLabel(o.label, nm, frac_hp, rf.alive, o.col);
    // screen-constant label: world scale proportional to camera distance
    const lblS = camera.position.distanceTo(o.group.position) * LABEL_WORLD_H;
    o.label.scale.set(lblS * LABEL_ASPECT, lblS, 1);
    // A wreck keeps its "— OUT" tag for the elimination beat, then goes quiet.
    // The stinger and the lower third have already called the kill, and these
    // labels are screen-constant: in a free-for-all two dozen full-size corpse
    // tags bury the fight that is still running. Null deadAtSim means the bot
    // is alive or mid-death-animation, both of which keep the tag up. The beat
    // is counted in frames off the playhead (`fps` frames = one second of
    // playback), so pausing on an elimination holds the tag there.
    // Except once the match is won, where every tag but the winner's comes down
    // at once. The playhead stops at the final frame, so a beat measured against
    // it would never elapse and the loser's tag would sit on top of the hero
    // shot for as long as the trophy is up. Test saidWinner, not celebrate:
    // celebrate is a 4s object, and the victory card outlives it.
    o.label.visible = rf.alive || (!celebrate && !saidWinner && (o.deadAtSim == null
      || t - o.deadAtSim < fps * OUT_TAG_S));
    if (rf.alive) {
      o.group.visible = true; alivePts.push({ x: wx, z: wz, id: rf.id });
      if (o.dead || o.dyingAt) { o.dead = false; o.dyingAt = null; o.deadAtSim = null; o.mesh.rotation.z = 0; o.mesh.position.y = 0; }
      // wheels-up: flipped bots lie on their back and wobble helplessly
      if (flipped) {
        o.mesh.rotation.z = Math.PI + 0.1 * Math.sin(performance.now() * 0.01 + rf.id);
        o.mesh.position.y = o.r * 1.9;
        if (Math.random() < 0.25)   // distress smoke
          spawnParticle(wx, o.r * 1.6, wz, (Math.random() - 0.5) * 25, 35 + Math.random() * 25, (Math.random() - 0.5) * 25,
            0.6, 0.3, 0.3, 0.32, 0.92, 40);
      } else if (o.mesh.rotation.z !== 0 && !o.dead) {
        o.mesh.rotation.z = 0; o.mesh.position.y = 0;   // self-righted
      }
      // jammed gun coughs grey sparks from the turret
      if (jammed && Math.random() < 0.35)
        spawnParticle(wx + Math.cos(p.hdg) * o.r * 0.4, o.r * 1.1, wz + Math.sin(p.hdg) * o.r * 0.4,
          (Math.random() - 0.5) * 60, 40 + Math.random() * 50, (Math.random() - 0.5) * 60,
          0.35, 0.75, 0.65, 0.4, 0.88, 120);
      // overheated gun VENTS — pale steam rising off the weapon
      if (venting && Math.random() < 0.55)
        spawnParticle(wx + Math.cos(p.hdg) * o.r * 0.4, o.r * 1.2, wz + Math.sin(p.hdg) * o.r * 0.4,
          (Math.random() - 0.5) * 25, 65 + Math.random() * 45, (Math.random() - 0.5) * 25,
          0.55, 0.82, 0.87, 0.92, 0.92, 15);
      o.ring.visible = true;
      o.ring.material.opacity = 0.72 + 0.13 * Math.sin(performance.now() * 0.006 + rf.id);
      // turret tracks nearest living enemy
      const tur = o.mesh.userData.turret;
      if (tur) {
        let best = Infinity, tgt = null;
        cur.robots.forEach((e) => {
          if (e.id !== rf.id && wp[e.id].alive) {
            const d = (wp[e.id].wx - wx) ** 2 + (wp[e.id].wz - wz) ** 2;
            if (d < best) { best = d; tgt = wp[e.id]; }
          }
        });
        tur.rotation.y = tgt ? p.hdg - Math.atan2(tgt.wz - wz, tgt.wx - wx) : 0;
      }
      // engine thruster trail when moving
      if (o.prevPos) {
        const dd = Math.hypot(wx - o.prevPos.x, wz - o.prevPos.z);
        if (dd > 1.2) {
          const bx = wx - Math.cos(p.hdg) * o.r, bz = wz - Math.sin(p.hdg) * o.r;
          const cr = ((o.col >> 16) & 255) / 255, cg = ((o.col >> 8) & 255) / 255, cb = (o.col & 255) / 255;
          spawnParticle(bx, o.r * 0.5, bz, (Math.random() - 0.5) * 30, 12, (Math.random() - 0.5) * 30, 0.3, cr, cg, cb, 0.85, 20);
        }
      }
      // battle-damage smoke, tiered: wisps < 60% HP, steady grey < 34%, heavy
      // black + electrical sparks < 15%. Anchored to one hull corner (rotates
      // with the robot) so it reads as a specific broken part, not an aura.
      if (frac_hp < 0.6) {
        const heavy = frac_hp < 0.15;
        const rate = heavy ? 0.85 : frac_hp < 0.34 ? 0.45 : 0.16;
        if (Math.random() < rate) {
          const ang = p.hdg + 2.4;                     // rear-left hull corner
          const sx2 = wx + Math.cos(ang) * o.r * 0.7, sz2 = wz + Math.sin(ang) * o.r * 0.7;
          const shade = heavy ? 0.07 : 0.2;
          spawnParticle(sx2, o.r * 1.1, sz2, (Math.random() - 0.5) * 18,
            32 + Math.random() * (heavy ? 55 : 28), (Math.random() - 0.5) * 18,
            heavy ? 1.1 : 0.7, shade, shade, shade + 0.02, 0.93, 45);
        }
        if (heavy && Math.random() < 0.12)             // shorting out
          spawnParticle(wx, o.r * 1.2, wz, (Math.random() - 0.5) * 160,
            60 + Math.random() * 90, (Math.random() - 0.5) * 160, 0.3, 1.0, 0.9, 0.5, 0.86, 220);
      }
    } else {
      o.ring.visible = false;
      if (!o.dead) {
        // staged death: ~0.3s hull shudder + electrical sparks, THEN the blast
        if (!o.dyingAt) o.dyingAt = performance.now();
        const dp = (performance.now() - o.dyingAt) / 300;
        if (dp < 1) {
          o.mesh.position.y = Math.sin(dp * 42) * 2.2;
          o.mesh.rotation.z = Math.sin(dp * 57) * 0.06;
          if (Math.random() < 0.6)
            spawnParticle(wx, o.r * 1.2, wz, (Math.random() - 0.5) * 160, 60 + Math.random() * 90,
              (Math.random() - 0.5) * 160, 0.3, 1.0, 0.9, 0.5, 0.86, 220);
        } else {
          o.dead = true; o.dyingAt = null; o.deadAtSim = t;
          o.mesh.rotation.z = 0.35; o.mesh.position.y = -2;   // resting wreck
          for (let k = 0; k < 14; k++)
            spawnParticle(wx, 30, wz, (Math.random() - 0.5) * 40, 30 + Math.random() * 40, (Math.random() - 0.5) * 40,
              0.9, 0.25, 0.25, 0.28, 0.9, 60);
          spawnDebris(wx, wz, o.col, o.r);
          explode(wx + AW / 2, wz + AH / 2, 60);   // death blast (arena coords)
          fovPunch = Math.min(12, fovPunch + 6);
          screenFlash(0.35);                       // clamped to 0.3
        }
      } else {
        // burning wreck: embers + smoke keep rising off the corpse
        if (Math.random() < 0.5) {
          const ang2 = p.hdg + 2.4;                // same broken-part anchor as battle smoke
          spawnParticle(wx + Math.cos(ang2) * o.r * 0.7, o.r * 0.9, wz + Math.sin(ang2) * o.r * 0.7,
            (Math.random() - 0.5) * 18, 30 + Math.random() * 55, (Math.random() - 0.5) * 18,
            1.2, 0.07, 0.07, 0.09, 0.93, 45);
        }
        if (Math.random() < 0.35)
          spawnParticle(wx, o.r * 0.7, wz, (Math.random() - 0.5) * 40, 40 + Math.random() * 70,
            (Math.random() - 0.5) * 40, 0.7, 1.0, 0.45, 0.12, 0.94, -20);
      }
    }
    // spinning parts (alive + upright only — a flipped bot's weapon spins down)
    if (rf.alive && !flipped) {
      const st = performance.now() * 0.001;
      o.mesh.userData.spinners.forEach((s) => { s.obj.rotation[s.prop] = st * s.rate; });
    }
    // Blender bots: wheels roll with travel, body leans into speed and turns,
    // hover engines ride a gentle bob
    const ud = o.mesh.userData;
    if (ud.wheels && ud.wheels.length && o.prevPos && rf.alive && !flipped) {
      const mvx = wx - o.prevPos.x, mvz = wz - o.prevPos.z;
      const dd = Math.hypot(mvx, mvz);
      if (dd > 0.01) {
        const fwd = (Math.cos(p.hdg) * mvx + Math.sin(p.hdg) * mvz) >= 0 ? 1 : -1;
        o.wheelRot = (o.wheelRot || 0) - fwd * dd / (o.r * 0.4);
        ud.wheels.forEach((w) => { w.rotation.x = o.wheelRot; });
      }
    }
    if (ud.lean) {
      let spd = 0, dh = 0;
      if (o.prevPos && rf.alive && !flipped) {
        spd = Math.hypot(wx - o.prevPos.x, wz - o.prevPos.z);
        if (o.prevHdg != null) {
          dh = p.hdg - o.prevHdg;
          if (dh > Math.PI) dh -= Math.PI * 2; else if (dh < -Math.PI) dh += Math.PI * 2;
        }
      }
      o.leanP = (o.leanP || 0) * 0.85 - Math.min(0.09, spd * 0.010) * 0.15;   // nose dips at speed
      o.leanR = (o.leanR || 0) * 0.85 + Math.max(-0.14, Math.min(0.14, dh * 1.5)) * 0.15;  // bank into turns
      ud.lean.rotation.z = o.leanP;
      ud.lean.rotation.x = o.leanR;
      if (ud.hover && rf.alive)
        ud.lean.position.y = 1.4 + Math.sin(performance.now() * 0.0032 + rf.id * 2.1) * 1.3;
      o.prevHdg = p.hdg;
    }
    // hit-flash + progressive battle damage (body darkens + scorches as HP falls)
    const bm = o.mesh.userData.bodyMat, base = o.mesh.userData.baseCol;
    if (bm) {
      // true white pop for ~90ms on a hit, then back to the team-color glow
      if (o.flash > 0) { bm.emissive.setHex(0xffffff); bm.emissiveIntensity = 1.5; }
      else { bm.emissive.copy(base); bm.emissiveIntensity = 0.28; }
      const dmg = 1 - frac_hp;
      bm.color.copy(base).lerp(SCORCH, dmg * 0.7);
      bm.roughness = 0.45 + dmg * 0.4;
      // electrics shorting: random livery flicker at critical damage
      if (frac_hp < 0.22 && rf.alive && Math.random() < 0.08) bm.emissiveIntensity += 0.9;
    }
    // wreckage bits reveal as the machine comes apart
    const bits = o.mesh.userData.damageBits;
    if (bits) {
      bits[0].visible = frac_hp < 0.55;
      bits[1].visible = frac_hp < 0.28;
    }
    o.prevPos = { x: wx, z: wz };
  });
  Object.keys(robotObjs).forEach((id) => { if (!seen[id]) robotObjs[id].group.visible = false; });

  // Name tags are screen-constant, so a scrum of forty machines stacks forty
  // full-size tags into a wall nobody can read — including the two names the
  // shot is actually about. Drop any tag that would land on one already claimed
  // by a nearer machine: nearest wins, and the living outrank the wrecks. Only
  // arms once the field is big enough to pile up, so a bracket match keeps every
  // tag it has today.
  const upLabels = [];
  Object.keys(robotObjs).forEach((id) => {
    const o = robotObjs[id];
    if (o.group.visible && o.label.visible) upLabels.push(o);
  });
  if (upLabels.length > LABEL_DECLUTTER_FROM) {
    // A screen-constant sprite has a constant footprint in clip space, so the
    // spacing thresholds are computed once rather than per tag. 0.95/0.86 trim
    // the transparent margin off the 512x160 canvas, letting tags sit shoulder
    // to shoulder without either being dropped.
    const tanHalf = Math.tan(camera.fov * Math.PI / 360);
    const gapY = 0.86 * LABEL_WORLD_H / tanHalf;
    const gapX = 0.95 * LABEL_WORLD_H * LABEL_ASPECT / (tanHalf * camera.aspect);
    const v = new T.Vector3(), now = performance.now();
    const cand = upLabels.map((o) => {
      v.set(o.group.position.x, o.group.position.y + o.label.position.y, o.group.position.z);
      const d = camera.position.distanceTo(v);
      v.project(camera);
      return { o, d, x: v.x, y: v.y, behind: v.z > 1 };
    });
    cand.sort((a, b) => (!!a.o.dead === !!b.o.dead) ? a.d - b.d : (a.o.dead ? 1 : -1));
    const kept = [];
    cand.forEach((c) => {
      if (c.behind) return;
      // Hysteresis. Machines in a scrum jostle across the overlap threshold every
      // frame, and a bare test flickered two or three tags per frame — traded one
      // unreadable artefact for another. A tag that is already up has to be
      // clashed harder to lose its place than a tag that is trying to take one.
      const m = c.o.labelShown ? LABEL_HOLD : LABEL_CLAIM;
      let show = !kept.some((k) => Math.abs(k.x - c.x) < gapX * m
                                && Math.abs(k.y - c.y) < gapY * m);
      // The band alone still left a handful of tags strobing several times a
      // second, so a tag that has just changed state holds it for a beat. What
      // survives now reads as the pack opening and closing, not as a fault.
      if (show !== c.o.labelShown) {
        if (c.o.labelLockUntil > now) show = c.o.labelShown;
        else c.o.labelLockUntil = now + LABEL_SETTLE_MS;
      }
      c.o.label.visible = show; c.o.labelShown = show;
      if (show) kept.push(c);
    });
  }

  // rockets (interpolate by id)
  const nxtRk = {}; nxt.rockets.forEach((r) => nxtRk[r.id] = r);
  const seenRk = {};
  cur.rockets.forEach((rk) => {
    seenRk[rk.id] = 1;
    const o = ensureRocket(rk);
    const r2 = nxtRk[rk.id] || rk;
    const x = rk.x + (r2.x - rk.x) * frac, y = rk.y + (r2.y - rk.y) * frac;
    const wx = x - AW / 2, wz = y - AH / 2;
    o.group.position.set(wx, 22, wz);
    if (o.prev) {
      const ang = Math.atan2(wz - o.prev.z, wx - o.prev.x);
      o.group.rotation.y = -ang;
      // smoke trail
      spawnParticle(wx, 22, wz, (Math.random() - 0.5) * 20, (Math.random() - 0.5) * 20 + 10, (Math.random() - 0.5) * 20,
        0.45, 0.5, 0.45, 0.4, 0.88, 30);
    }
    o.prev = { x: wx, z: wz };
  });
  Object.keys(rocketObjs).forEach((id) => {
    if (!seenRk[id]) { scene.remove(rocketObjs[id].group); disposeTree(rocketObjs[id].group); delete rocketObjs[id]; }
  });

  // mines
  const seenMn = {};
  cur.mines.forEach((mn) => {
    seenMn[mn.id] = 1;
    const o = ensureMine(mn);
    o.group.position.set(mn.x - AW / 2, 0, mn.y - AH / 2);
    const pulse = mn.armed ? (0.35 + 0.35 * Math.sin(performance.now() * 0.012)) : 0.0;
    o.ring.material.opacity = pulse;
    o.ring.scale.setScalar(mn.armed ? 1 + 0.15 * Math.sin(performance.now() * 0.012) : 1);
    o.light.material.emissiveIntensity = mn.armed ? 1.2 : 0.3;
  });
  Object.keys(mineObjs).forEach((id) => {
    if (!seenMn[id]) { scene.remove(mineObjs[id].group); disposeTree(mineObjs[id].group); delete mineObjs[id]; }
  });

  // pickups (active crates only; bob + spin so they read as collectible)
  const seenPk = {};
  (cur.pickups || []).forEach((p) => {
    if (!p.active) return;
    seenPk[p.id] = 1;
    const o = ensurePickup(p);
    const bob = 28 + 5 * Math.sin(performance.now() * 0.004 + p.id);
    o.group.position.set(p.x - AW / 2, bob, p.y - AH / 2);
    o.core.rotation.y = performance.now() * 0.0016 + p.id;
    o.core.rotation.x = 0.4;
  });
  Object.keys(pickupObjs).forEach((id) => {
    if (!seenPk[id]) { scene.remove(pickupObjs[id].group); disposeTree(pickupObjs[id].group); delete pickupObjs[id]; }
  });

  // advance animated hazard shaders (lava flow, water caustics)
  if (shaderFX.length) {
    const ts = performance.now() * 0.001;
    for (let i = 0; i < shaderFX.length; i++) shaderFX[i].uniforms.u_time.value = ts;
  }

  // HUD + scoreboard + broadcast timer
  const s = cur.status || {};
  updateCollapse(s.collapse || 0);          // SUDDEN DEATH molten ring
  if (DEBUG) hud.textContent = `TICK ${String(cur.tick).padStart(4, "0")}`;
  updateScoreboard(cur.robots);
  updateTimer(s.time_left != null ? s.time_left : 0);
  return alivePts;
}

// per-tick discrete events (explosions, lasers, deaths, intro/outro voice)
function processTickEvents(tick) {
  if (tick === lastTickSeen) return;
  // Victims across this whole batch. The overlay shows one message at a time,
  // so calling killFeed per victim inside a single frame paints only the last
  // one — in a crowded free-for-all most of the field would die uncredited.
  const elims = [];
  // advance through any skipped ticks too
  for (let tk = lastTickSeen + 1; tk <= tick; tk++) {
    const fr = frames[tk]; if (!fr) continue;
    if (!saidGo) {
      saidGo = true; playClip("fight", "Fight!", { flush: true, rate: 1.1, pitch: 1.1 });
      stinger("FIGHT!"); ticker("⚔ Battle commences");
      // land the beat: kick + punch-in + flash, and hold the playhead until the
      // stinger's pop keyframe so FIGHT lands before the first exchange
      shake(12); fovPunch = Math.min(12, fovPunch + 4); screenFlash(0.18); fightHold = 0.06;
    }
    // engage slow-mo + kill-cam one tick EARLY: peek at the incoming frame so
    // the deciding blow plays in slow motion, not its aftermath smoke
    const peek = frames[tk + 1];
    if (peek && !slowmoFired && tk > 2) {
      const aliveNext = peek.status ? peek.status.alive : peek.robots.filter((r) => r.alive).length;
      const victim = peek.robots.find((r) => alivePrev[r.id] && !r.alive);
      if (victim && aliveNext <= 1) {
        slowmoFired = true; slowmoT = 2.0; setLetterbox(true);
        cam.focus = { x: victim.x - AW / 2, z: victim.y - AH / 2, life: 2.0, max: 2.0, bias: 0.85, zoom: 0.45 };
        ticker("⚡ THE FINAL BLOW");
        playClip("final_blow", "Finish him!", { flush: true });
      }
    }
    const pos = {}; fr.robots.forEach((r) => pos[r.id] = r);
    (fr.explosions || []).forEach((ex) => explode(ex.x, ex.y, ex.r));
    (fr.events || []).forEach((ev) => {
      if (ev.kind === "flip" && names[ev.id] != null) {
        ticker(`🙃 ${names[ev.id]} FLIPPED!`);
        playClip(["flip_" + voiceKey[ev.id], "flip"], `${names[ev.id]} is flipped!`);
      }
      else if (ev.kind === "jam" && names[ev.id] != null) {
        ticker(`⚙ ${names[ev.id]} — GUN JAMMED`);
        playClip(["jam_" + voiceKey[ev.id], "jam"], `${names[ev.id]}'s gun is jammed!`);
      }
      else if (ev.kind === "vent" && names[ev.id] != null) {
        ticker(`♨ ${names[ev.id]} — OVERHEATED, venting`);
        playClip(["overheat_" + voiceKey[ev.id], "overheat"], `${names[ev.id]} is overheating!`);
      }
      else if (ev.kind === "pickup" && PICKUP_UI[ev.type]) {
        ticker(PICKUP_UI[ev.type]);
        playClip(["pickup_" + ev.type]);
      }
      else if (HAZARD_UI[ev.kind]) {
        // lava/pit/water fire on EVERY tick a bot is in the zone — rate-limit
        // or the announcer machine-guns the same line for seconds on end
        const nowMs = performance.now();
        if (nowMs - (hazardSaid[ev.kind] || -1e9) > 7000) {
          hazardSaid[ev.kind] = nowMs;
          ticker(HAZARD_UI[ev.kind]);
          playClip(["hazard_" + ev.kind]);
        }
      }
      else if (ev.kind === "flipper") {
        const fx = flipperFX.find((q) => Math.abs(q.x - ev.x) < 2 && Math.abs(q.y - ev.y) < 2);
        if (fx) fx.t = 0;
        const wx = ev.x - AW / 2, wz = ev.y - AH / 2;
        for (let i = 0; i < 24; i++)
          spawnParticle(wx, 14, wz, (Math.random() - 0.5) * 260, 140 + Math.random() * 220,
            (Math.random() - 0.5) * 260, 0.5, 1.0, 0.85, 0.4, 0.88, 260);
        flash(wx, 50, wz, 0xffd93d, 2.6);
        shake(9);
        const vo = robotObjs[ev.id];
        if (vo) vo.airborne = { start: performance.now() };
        ticker(`🚀 FLOOR FLIPPER — ${names[ev.id] || "a robot"} takes flight!`);
        playClip(["flipper_" + voiceKey[ev.id], "flipper", "big_hit"], `${names[ev.id] || "Someone"} takes flight!`);
      }
      else if (ev.kind === "ram") {
        // CRUNCH — metal-on-metal spark burst + kick
        const wx = ev.x - AW / 2, wz = ev.y - AH / 2;
        for (let i = 0; i < 26; i++)
          spawnParticle(wx, 20, wz, (Math.random() - 0.5) * 300, Math.random() * 180, (Math.random() - 0.5) * 300,
            0.3, 1, 0.9, 0.55, 0.85, 240);
        flash(wx, 40, wz, 0xffeeaa, 2.2);
        shake(Math.min(14, 4 + (ev.dmg || 0) * 0.15));
        if ((ev.dmg || 0) >= 20) ticker(`💥 CRUNCH — ${ev.dmg} ram damage`);
        if ((ev.dmg || 0) >= 25) playClip(["big_hit"], "What a hit!");
      }
    });
    (fr.fired || []).forEach((f) => {
      const shooter = robotObjs[f.f];
      // baked per-weapon 'shoot' clip on the Blender robots (recoil + muzzle fx)
      if (shooter && shooter.mesh.userData.fire) shooter.mesh.userData.fire.reset().play();
      const col = shooter ? shooter.col : 0xffffff;
      if (f.hit && pos[f.f] && pos[f.t] != null) {
        const a = pos[f.f], b = pos[f.t];
        beam(a.x, a.y, b.x, b.y, col, f.gun || "laser");
      } else if (!f.hit && f.mx != null && pos[f.f]) {
        // a MISS — tracer flies past the target
        const a = pos[f.f];
        beam(a.x, a.y, f.mx, f.my, col, f.gun || "laser", true);
      }
    });
    fr.robots.forEach((r) => {
      const o = robotObjs[r.id];
      if (lastHp[r.id] != null && r.hp < lastHp[r.id] && r.alive && o && !o.dead && !o.dyingAt) {
        o.flash = 0.09;
        for (let i = 0; i < 8; i++)
          spawnParticle(r.x - AW / 2, o.r, r.y - AH / 2, (Math.random() - 0.5) * 120, Math.random() * 90, (Math.random() - 0.5) * 120, 0.3, 1, 0.95, 0.6, 0.85, 150);
        const now = performance.now(), amt = (dmgPending[r.id] || 0) + Math.round(lastHp[r.id] - r.hp);
        if (!lastDmgAt[r.id] || now - lastDmgAt[r.id] > DMG_COALESCE_MS) {
          damageNumber(r.x - AW / 2, r.y - AH / 2, o.r * 2.6, amt);
          lastDmgAt[r.id] = now; dmgPending[r.id] = 0;
        } else dmgPending[r.id] = amt;
      } else if (lastHp[r.id] != null && r.hp > lastHp[r.id] + 2 && r.alive && o && !o.dead && !o.dyingAt) {
        damageNumber(r.x - AW / 2, r.y - AH / 2, o.r * 2.6, Math.round(r.hp - lastHp[r.id]), true);
      }
      lastHp[r.id] = r.hp;
      if (alivePrev[r.id] && !r.alive) {
        playClip(["elim_" + slug(r.name), "eliminated"], `${botLabel(r)} is out!`);
        const colS = o ? "#" + o.col.toString(16).padStart(6, "0") : null;
        elims.push({ name: botLabel(r), col: colS });
        cam.focus = { x: r.x - AW / 2, z: r.y - AH / 2, life: 1.6, max: 1.6, zoom: 0.55 };
      }
      alivePrev[r.id] = r.alive;
    });
    // fallback: deciding blow on the very last frame (nothing left to peek at)
    const aliveN = fr.status ? fr.status.alive : fr.robots.filter((r) => r.alive).length;
    if (aliveN <= 1 && !slowmoFired && tk > 2) {
      slowmoFired = true; slowmoT = 2.0; setLetterbox(true); ticker("⚡ THE FINAL BLOW");
      playClip("final_blow", "Finish him!", { flush: true });
    }
  }
  if (elims.length) {
    killFeed(elims);
    // one victim keeps its own tint and name; a multi-kill names them on the
    // bar and counts them on the stinger, which has room for one line only
    stinger(elims.length === 1
      ? elims[0].name.toUpperCase() + " ELIMINATED"
      : elims.length + " ELIMINATED", elims.length === 1 ? elims[0].col : null);
  }
  lastTickSeen = tick;
}

// kill events reuse the lower-third event bar (see ticker below): red-edged
// variant, victim names in team color — the biggest text on screen for 4s
function killFeed(victims) {
  showBar((d) => {
    d.className = "tk kill";
    victims.forEach((v, i) => {
      if (i) d.append(", ");
      const b = document.createElement("b");
      b.textContent = v.name; if (v.col) b.style.color = v.col;
      d.append(b);
    });
    d.append(" ELIMINATED");
  });
}

// ----- team grouping --------------------------------------------------------
// A robot carries r.team (a label). Explicit labels identify team/bracket matches;
// "solo:<id>" identifies free-for-all entrants. teamOf() preserves that fallback.
const teamOf = (r) => (r.team != null ? r.team : "solo:" + r.id);
function teamInfo(robots) {
  const order = [], by = {};
  robots.forEach((r) => { const t = teamOf(r); if (!(t in by)) { by[t] = []; order.push(t); } by[t].push(r); });
  // A label on every robot with more than two sides is a free-for-all wearing
  // team labels (the event's 41-entrant field, and samples/ffa.jsonl): grouping
  // it draws a team header over every single machine and doubles the rail.
  // Two sides is the bracket format — a team match even at one machine each.
  const labelled = robots.some((r) => !String(teamOf(r)).startsWith("solo:"));
  return { order, by, hasTeams: labelled && (order.length <= 2 || order.length < robots.length) };
}
let teamMode = false, teamNames = [];
// Short per-bot label: drop the "Team:" prefix the bracket prepends, since the
// team header already names the side.
const botLabel = (r) => { const i = String(r.name).indexOf(":"); return i >= 0 ? r.name.slice(i + 1) : r.name; };

// live scoreboard (DOM; rows created once, updated each frame, CSS-order reflows).
// 1v1 team matches (the bracket format) get a top-center esports scorebug
// instead; the stacked rail survives only for >2 sides (FFA).
const sbEl = document.getElementById("scoreboard");
let sbRows = {}, sbTeamHdr = {}, sbgEls = null;
// 12x12 inline SVG glyphs (self-contained) replace the rocket/trap emoji
const ICO_RKT = '<svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor"><path d="M6 .5c1.7 1.1 2.5 2.9 2.5 4.9L6 11.5 3.5 5.4C3.5 3.4 4.3 1.6 6 .5z"/></svg>';
const ICO_TRP = '<svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor"><path d="M6 1l5 5-5 5-5-5z"/></svg>';
function buildScoreboard(robots) {
  if (!sbEl) return;
  robots = robots.filter((r) => r.team !== "house");   // the Gatekeeper isn't a contestant
  sbEl.innerHTML = ""; sbRows = {}; sbTeamHdr = {}; sbgEls = null;
  const oldBug = document.getElementById("scorebug"); if (oldBug) oldBug.remove();
  if (timerEl) timerEl.style.display = "";
  const ti = teamInfo(robots);
  teamMode = ti.hasTeams; teamNames = ti.order;
  sbEl.classList.toggle("teams", teamMode);
  if (teamMode && teamNames.length === 2) {
    // esports scorebug: [plate A][clock][plate B], top-center; absorbs #timer
    const bug = document.createElement("div"); bug.id = "scorebug";
    sbgEls = teamNames.map((t, i) => {
      const col = "#" + colorFor(ti.by[t][0]).toString(16).padStart(6, "0");
      const d = document.createElement("div");
      d.className = "sbg-plate" + (i ? " right" : "");
      d.style.setProperty("--team-col", col);
      d.innerHTML = '<span class="sbg-name"></span><span class="sbg-hp"><i class="ghost"></i><i class="fill"></i></span>';
      d.querySelector(".sbg-name").textContent = t;
      return { team: t, el: d, hp: d.querySelector(".sbg-hp"),
               ghost: d.querySelector(".ghost"), fill: d.querySelector(".fill") };
    });
    const mid = document.createElement("div"); mid.id = "sbg-mid";
    mid.innerHTML = '<div id="sbg-clock">00:00</div><div class="sbg-round"></div>';
    const mt = document.getElementById("mtitle");
    mid.querySelector(".sbg-round").textContent = (mt && mt.textContent) ? mt.textContent : "ROBOT WARS";
    bug.append(sbgEls[0].el, mid, sbgEls[1].el);
    stage.appendChild(bug);
    if (timerEl) timerEl.style.display = "none";
    return;
  }
  const mkRow = (r, useShortName) => {
    const col = "#" + colorFor(r).toString(16).padStart(6, "0");
    const row = document.createElement("div"); row.className = "sb-row";
    row.innerHTML = `<span class="sb-chip" style="background:${col}"></span>` +
      `<span class="sb-name"></span><span class="sb-bar"><i></i></span>` +
      `<span class="sb-ammo"></span><span class="sb-dmg" title="damage dealt"></span>`;
    sbEl.appendChild(row);
    const e = { row, name: row.querySelector(".sb-name"), fill: row.querySelector(".sb-bar i"),
      ammo: row.querySelector(".sb-ammo"), dmg: row.querySelector(".sb-dmg"), ammoLast: "" };
    e.fill.style.background = col;         // team color carries the bar, always
    e.name.textContent = useShortName ? botLabel(r) : r.name;
    sbRows[r.id] = e;
  };
  if (teamMode) {
    ti.order.forEach((t) => {
      const members = ti.by[t];
      const col = "#" + colorFor(members[0]).toString(16).padStart(6, "0");
      const hdr = document.createElement("div"); hdr.className = "sb-team";
      hdr.style.setProperty("--tc", col);
      // The team name arrives from the recording, so it is participant-controlled:
      // it goes in as text, never as markup. (`col` is a number from colorFor.)
      hdr.innerHTML = `<span class="sb-tdot" style="background:${col}"></span>` +
        `<span class="sb-tname"></span><span class="sb-tcount"></span>`;
      hdr.querySelector(".sb-tname").textContent = t;
      sbEl.appendChild(hdr);
      sbTeamHdr[t] = { el: hdr, count: hdr.querySelector(".sb-tcount"), total: members.length };
      members.forEach((r) => mkRow(r, true));
    });
  } else {
    robots.forEach((r) => mkRow(r, false));
  }
  // Past ~18 entries the full-fat row cannot fit the stage even in two columns,
  // so shed per-row detail rather than push names off the bottom edge.
  sbEl.classList.toggle("dense", sbEl.children.length > 18);
}
function updateScoreboard(robots) {
  if (!sbEl) return;
  if (sbgEls) {
    // scorebug: per-side aggregate HP; low HP pulses the bar (no traffic lights)
    const agg = {};
    robots.forEach((r) => {
      if (r.team === "house") return;
      const t = teamOf(r); const a = agg[t] || (agg[t] = [0, 0]);
      a[0] += Math.max(0, r.hp); a[1] += r.max_hp;
    });
    sbgEls.forEach((pl) => {
      const a = agg[pl.team] || [0, 1];
      const frac = Math.max(0, Math.min(1, a[0] / Math.max(1, a[1])));
      const w = (frac * 100) + "%";
      pl.fill.style.width = w;
      pl.ghost.style.width = w;              // white damage-lag ghost (CSS delay)
      pl.hp.classList.toggle("low", frac > 0 && frac < 0.25);
    });
    return;
  }
  if (!teamMode) {
    const order = robots.slice().sort((a, b) => (b.alive - a.alive) || (b.hp - a.hp));
    order.forEach((r, i) => { const e = sbRows[r.id]; if (e) e.row.style.order = i; });
  }
  robots.forEach((r) => {
    const e = sbRows[r.id]; if (!e) return;
    const frac = Math.max(0, r.hp / r.max_hp);
    e.fill.style.width = (frac * 100) + "%";
    e.fill.classList.toggle("low", frac > 0 && frac < 0.25);
    const ammoKey = r.rkt + "|" + r.trp;
    if (e.ammoLast !== ammoKey) { e.ammoLast = ammoKey; e.ammo.innerHTML = `${ICO_RKT}${r.rkt} ${ICO_TRP}${r.trp}`; }
    if (e.dmg) e.dmg.textContent = (r.dmg || 0) + "";
    e.row.classList.toggle("dead", !r.alive);
  });
  if (teamMode) {
    const alive = {}; robots.forEach((r) => { const t = teamOf(r); alive[t] = (alive[t] || 0) + (r.alive ? 1 : 0); });
    for (const t in sbTeamHdr) {
      const h = sbTeamHdr[t];
      h.count.textContent = `${alive[t] || 0}/${h.total}`;
      h.el.classList.toggle("out", (alive[t] || 0) === 0);
    }
  }
}

// ---- broadcast overlay: lower-third event bar, stinger, title card, timer --
// One broadcast surface: a single bar at a time, REPLACING (not stacking) the
// previous message.
const tickerEl = document.getElementById("ticker");
let barTimers = [];
function showBar(build) {
  if (!tickerEl) return;
  barTimers.forEach(clearTimeout); barTimers = [];
  tickerEl.innerHTML = "";
  const d = document.createElement("span");
  build(d);
  tickerEl.appendChild(d);
  barTimers = [
    setTimeout(() => d.classList.add("show"), 30),
    setTimeout(() => d.classList.remove("show"), 4200),
    setTimeout(() => d.remove(), 4700),
  ];
}
function ticker(msg) {
  showBar((d) => { d.className = "tk"; d.textContent = msg; });
}
const stingerEl = document.getElementById("stinger");
function stinger(text, col) {
  if (!stingerEl) return;
  stingerEl.textContent = text; stingerEl.classList.remove("go");
  stingerEl.style.fontSize = "";
  const range = document.createRange(); range.selectNodeContents(stingerEl);
  const textWidth = range.getBoundingClientRect().width, maxWidth = window.innerWidth * 0.4;
  if (textWidth > maxWidth)
    stingerEl.style.fontSize = `${parseFloat(getComputedStyle(stingerEl).fontSize) * maxWidth / textWidth}px`;
  // per-call tint (eliminations): longhand backgroundImage keeps background-clip:text
  stingerEl.style.backgroundImage = col
    ? `linear-gradient(180deg,#fff 10%,${col} 60%,${col} 100%)` : "";
  void stingerEl.offsetWidth; stingerEl.classList.add("go");
}
const titleEl = document.getElementById("titlecard");
function showTitleCard(n) {
  if (!titleEl) return;
  const sub = titleEl.querySelector(".tc-sub");
  // broadcast VS card: in the event's 1v1 format, introduce BOTH builds so the
  // room learns each machine at a glance (gun · engine · size, team colours).
  // Built with textContent (names are participant-controlled — no innerHTML).
  const old = titleEl.querySelector(".tc-vs"); if (old) old.remove();
  const oldWarn = titleEl.querySelector(".tc-house"); if (oldWarn) oldWarn.remove();
  const bots = (frames[0] ? frames[0].robots : []).filter((r) => r.team !== "house");
  if (bots.length === 2) {
    const SIZE_BY_R = { 12: "small", 16: "medium", 22: "large" };
    const GUN_ICO = { laser: "🔫", cannon: "💣", shotgun: "🧨" };
    const ENG_ICO = { standard: "⚙", sprint: "🏎", tank: "🛡", hover: "🛸" };
    const vs = document.createElement("div");
    vs.className = "tc-vs";
    vs.style.cssText = "display:flex;align-items:center;gap:38px;margin-top:26px;";
    const side = (r) => {
      const col = "#" + colorFor(r).toString(16).padStart(6, "0");
      const d = document.createElement("div");
      d.style.cssText = "text-align:center;min-width:220px;";
      const name = document.createElement("div");
      name.style.cssText = `font-size:34px;font-weight:900;letter-spacing:1px;color:${col}`;
      name.textContent = teamMode ? botLabel(r) : r.name;
      const team = document.createElement("div");
      team.style.cssText = "font-size:13px;color:#7e8aa0;letter-spacing:3px;text-transform:uppercase;margin-top:4px";
      team.textContent = String(r.team);
      const build = document.createElement("div");
      build.style.cssText = "font-size:16px;color:#cdd8ea;margin-top:10px";
      build.textContent = `${GUN_ICO[r.gun] || "🔫"} ${r.gun || "laser"} · ` +
        `${ENG_ICO[r.eng] || "⚙"} ${r.eng || "standard"}` +
        (SIZE_BY_R[r.r] ? ` · ${SIZE_BY_R[r.r]}` : "");
      d.append(name, team, build);
      return d;
    };
    const mid = document.createElement("div");
    mid.style.cssText = "font-size:44px;font-weight:900;color:#ffd93d";
    mid.textContent = "VS";
    vs.append(side(bots[0]), mid, side(bots[1]));
    titleEl.appendChild(vs);
    if (frames[0].robots.some((r) => r.team === "house")) {
      const warn = document.createElement("div");
      warn.className = "tc-house";
      warn.style.cssText = "font-size:13px;color:#ffb400;letter-spacing:3px;margin-top:16px;text-transform:uppercase";
      warn.textContent = "🚨 Gatekeeper patrols this arena";
      titleEl.appendChild(warn);
    }
  }
  if (sub) sub.textContent = (teamMode && teamNames.length === 2)
    ? `${teamNames[0]}  vs  ${teamNames[1]}`
    : `${n} robots enter · one leaves`;
  titleEl.classList.add("show");
  setTimeout(() => titleEl.classList.remove("show"), bots.length === 2 ? 3600 : 2800);
}
const timerEl = document.getElementById("timer");
function updateTimer(timeLeft) {
  const s = Math.max(0, Math.round(timeLeft / 10));   // ticks -> ~seconds for a broadcast clock
  const txt = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  const sc = document.getElementById("sbg-clock");    // scorebug clock absorbs #timer
  if (sc) { sc.textContent = txt; return; }
  if (timerEl) timerEl.textContent = txt;
}

function updateFX(dt) {
  updateParticles(dt);
  for (let i = shells.length - 1; i >= 0; i--) {
    const s = shells[i]; s.life -= dt;
    const f = s.life / s.max;
    if (f <= 0) { scene.remove(s.mesh); s.mesh.geometry.dispose(); s.mesh.material.dispose(); shells.splice(i, 1); continue; }
    const grow = (1 - f) * s.target;
    s.mesh.scale.setScalar(Math.max(1, grow));
    s.mesh.material.opacity = 0.85 * f;
  }
  // ground shockwaves
  for (let i = shockwaves.length - 1; i >= 0; i--) {
    const s = shockwaves[i]; s.life -= dt;
    const f = s.life / s.max;
    if (f <= 0) { scene.remove(s.mesh); s.mesh.geometry.dispose(); s.mesh.material.dispose(); shockwaves.splice(i, 1); continue; }
    s.mesh.scale.setScalar(Math.max(1, (1 - f) * s.target));
    s.mesh.material.opacity = 0.8 * f;
  }
  FLASHES.forEach((fl) => { if (fl.life > 0) { fl.life -= dt; fl.light.intensity = fl.power * Math.max(0, fl.life / fl.max); } });
  beams.forEach((b) => {
    if (b.life > 0) { b.life -= dt; b.mesh.material.opacity = Math.max(0, b.life / b.max); }
    else b.mesh.material.opacity = 0;
  });
  // cannon slugs in flight: burst lands on arrival
  for (let i = slugs.length - 1; i >= 0; i--) {
    const s = slugs[i]; s.t += dt;
    const q = Math.min(1, s.t / s.dur);
    s.sp.position.lerpVectors(s.from, s.to, q);
    if (q < 1) continue;
    for (let k = 0; k < 22; k++)
      spawnParticle(s.to.x, s.to.y, s.to.z, (Math.random() - 0.5) * 260, Math.random() * 200, (Math.random() - 0.5) * 260,
        0.35, 1, 0.8, 0.4, 0.85, 260, 50);
    flash(s.to.x, 40, s.to.z, 0xffcc88, 2.5);
    shake(6);
    scene.remove(s.sp); s.sp.material.dispose(); slugs.splice(i, 1);
  }
  // debris chunks (arc + tumble + fade)
  for (let i = debris.length - 1; i >= 0; i--) {
    const d = debris[i]; d.life -= dt;
    if (d.life <= 0) { scene.remove(d.mesh); d.mesh.geometry.dispose(); d.mesh.material.dispose(); debris.splice(i, 1); continue; }
    d.vy -= 520 * dt;
    d.mesh.position.x += d.vx * dt; d.mesh.position.y += d.vy * dt; d.mesh.position.z += d.vz * dt;
    if (d.mesh.position.y < 4) { d.mesh.position.y = 4; d.vy *= -0.4; d.vx *= 0.6; d.vz *= 0.6; }
    d.mesh.rotation.x += d.rx * dt; d.mesh.rotation.z += d.rz * dt;
    d.mesh.material.opacity = Math.min(1, d.life / 0.4); d.mesh.material.transparent = d.life < 0.4;
  }
  // scorch decals: persistent for the match; only cap-evicted ones fade
  for (let i = scorches.length - 1; i >= 0; i--) {
    const s = scorches[i];
    if (s.life === Infinity) continue;
    s.life -= dt;
    if (s.life <= 0) { scene.remove(s.mesh); s.mesh.geometry.dispose(); s.mesh.material.dispose(); scorches.splice(i, 1); continue; }
    s.mesh.material.opacity = 0.55 * Math.min(1, s.life / 1.5);
  }
  // robot hit-flash decay (~90ms white pop)
  for (const id in robotObjs) { const o = robotObjs[id]; if (o.flash > 0) o.flash = Math.max(0, o.flash - dt); }
  // atmosphere
  updateEmbers(dt);
  updateCamFlashes(dt);
  const tt = performance.now() * 0.0004;
  spots.forEach((sp, i) => {
    sp.light.target.position.set(Math.sin(tt * (1 + i * 0.3) + sp.phase) * 520 * sp.dir, 0, Math.cos(tt * 0.8 + sp.phase) * 360);
    sp.light.target.updateMatrixWorld();
  });
  fovPunch = Math.max(0, fovPunch - dt * 14);
}

// ===========================================================================
// Hand-rolled selective bloom — offline, r150 CORE only (UnrealBloomPass /
// EffectComposer are addons not vendored here). Restrained "broadcast" bloom:
// a high luminance threshold isolates only real highlights (steel weapon specular,
// emissive eyes/beams/flashes, lava veins) so the bright pit itself does NOT
// bloom -> high clarity. Bright-pass -> separable gaussian blur (half-res, 2x) ->
// additive composite. Display-referred (operates on the sRGB-encoded scene buffer;
// ShaderMaterial passes get no auto color conversion, so it's a clean passthrough).
// DEFENSIVE: any failure permanently falls back to direct render — a live event
// must never black-screen because a venue GPU rejected a render target.
// ===========================================================================
const POST_VERT = `varying vec2 vUv; void main(){ vUv=uv; gl_Position=vec4(position.xy,0.0,1.0); }`;
const BRIGHT_FRAG = `varying vec2 vUv; uniform sampler2D tDiffuse; uniform float uThresh, uKnee;
  void main(){ vec3 c=texture2D(tDiffuse,vUv).rgb; float l=dot(c,vec3(0.299,0.587,0.114));
    float f=smoothstep(uThresh, uThresh+uKnee, l); gl_FragColor=vec4(c*f, 1.0); }`;
const BLUR_FRAG = `varying vec2 vUv; uniform sampler2D tDiffuse; uniform vec2 uDir, uTexel;
  void main(){ vec2 o=uDir*uTexel; vec3 s=vec3(0.0);
    s+=texture2D(tDiffuse,vUv).rgb*0.227027;
    s+=texture2D(tDiffuse,vUv+o*1.3846).rgb*0.316216;
    s+=texture2D(tDiffuse,vUv-o*1.3846).rgb*0.316216;
    s+=texture2D(tDiffuse,vUv+o*3.2308).rgb*0.070270;
    s+=texture2D(tDiffuse,vUv-o*3.2308).rgb*0.070270;
    gl_FragColor=vec4(s,1.0); }`;
const COMP_FRAG = `varying vec2 vUv; uniform sampler2D tScene, tBloom; uniform float uStrength;
  void main(){ vec3 s=texture2D(tScene,vUv).rgb; vec3 b=texture2D(tBloom,vUv).rgb;
    gl_FragColor=vec4(s + b*uStrength, 1.0); }`;
const bloom = (function () {
  // The scene target stays multisampled for the broadcast. The jumbotron gets
  // a separate non-MSAA scene pass with its own screen + cyan frame hidden,
  // preventing the live mirror from recursively capturing its own housing.
  let ok = false, sceneRT, feedRT, brightRT, blurA, blurB, postScene, postCam, quad, matBright, matBlur, matComp;
  const HALF = 2;
  const dim = new T.Vector2();
  const size = () => { renderer.getDrawingBufferSize(dim); return dim; };
  // Dynamic resolution. The scene passes render into targets scaled by this and
  // the composite blits them back up to the full canvas, so the HUD, scorebug
  // and roster rail stay pixel-sharp while only the expensive 3D work gets
  // cheaper. The controller that moves it lives with the main loop below.
  let resScale = 1;
  const rtDim = new T.Vector2();
  const rtSize = () => {
    const d = size();
    return rtDim.set(Math.max(1, Math.round(d.x * resScale)), Math.max(1, Math.round(d.y * resScale)));
  };
  // This chain drops to `ok = false` (direct render, static jumbotron) from
  // three places below with nothing shown on screen; an operator running the
  // show must not learn about a degraded broadcast from a diff in picture
  // quality. Fires once per session, not once per frame.
  let warned = false;
  function warnDegraded(e) {
    console.error("post-processing failed -> degraded render", e);
    if (warned) return;
    warned = true;
    const banner = document.createElement("div");
    banner.textContent = "DEGRADED RENDER MODE -- post-processing disabled, see console";
    banner.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:9999;background:#a11;color:#fff;" +
      "font:600 13px/1.6 sans-serif;text-align:center;padding:4px 0;pointer-events:none;";
    document.body.appendChild(banner);
  }
  // Supersampling was removed: what looked like crawling silhouette aliasing
  // was actually the depth-precision bug fixed by the camera near plane above,
  // not a sample-count problem, and rendering ~2.5x the pixels doubled GPU
  // cost for no benefit.
  function makeRT(w, h, srgb, samples) {
    const rt = new T.WebGLRenderTarget(Math.max(1, w), Math.max(1, h),
      { minFilter: T.LinearFilter, magFilter: T.LinearFilter, type: T.UnsignedByteType });
    rt.texture.colorSpace = srgb ? T.SRGBColorSpace : T.NoColorSpace;
    rt.texture.generateMipmaps = false;
    // MSAA on WebGL2 (the canvas `antialias` flag never applies inside a target).
    // Only sceneRT (below) passes `samples`. 4x is the quality/cost knee there;
    // 8x was chasing a stair-stepping artifact that turned out to be the
    // depth-precision bug fixed by the camera near plane above, not sample
    // count, so it wasn't worth the doubled GPU cost.
    if (samples) rt.samples = Math.min(samples, renderer.capabilities.maxSamples || samples);
    return rt;
  }
  function doResize() {
    const d = rtSize(), bw = Math.max(1, Math.floor(d.x / HALF)), bh = Math.max(1, Math.floor(d.y / HALF));
    sceneRT.setSize(d.x, d.y); feedRT.setSize(d.x, d.y);
    brightRT.setSize(bw, bh); blurA.setSize(bw, bh); blurB.setSize(bw, bh);
  }
  try {
    const d = rtSize();
    const bw = Math.max(1, Math.floor(d.x / HALF)), bh = Math.max(1, Math.floor(d.y / HALF));
    sceneRT = makeRT(d.x, d.y, true, 4);     // native-res scene (sRGB, matches screen)
    feedRT = makeRT(d.x, d.y, true);         // resolved broadcast for the jumbotron
    brightRT = makeRT(bw, bh, false); blurA = makeRT(bw, bh, false); blurB = makeRT(bw, bh, false);
    postScene = new T.Scene(); postCam = new T.OrthographicCamera(-1, 1, 1, -1, 0, 1);
    quad = new T.Mesh(new T.PlaneGeometry(2, 2)); postScene.add(quad);
    matBright = new T.ShaderMaterial({ vertexShader: POST_VERT, fragmentShader: BRIGHT_FRAG,
      depthTest: false, depthWrite: false, uniforms: { tDiffuse: { value: null }, uThresh: { value: 0.85 }, uKnee: { value: 0.12 } } });
    matBlur = new T.ShaderMaterial({ vertexShader: POST_VERT, fragmentShader: BLUR_FRAG,
      depthTest: false, depthWrite: false, uniforms: { tDiffuse: { value: null }, uDir: { value: new T.Vector2() }, uTexel: { value: new T.Vector2() } } });
    matComp = new T.ShaderMaterial({ vertexShader: POST_VERT, fragmentShader: COMP_FRAG,
      depthTest: false, depthWrite: false, uniforms: { tScene: { value: null }, tBloom: { value: null }, uStrength: { value: 0.45 } } });
    ok = true;
  } catch (e) { warnDegraded(e); ok = false; }
  return {
    get ok() { return ok; },
    // QA handle: flipping this off drops to the direct canvas render, which is
    // the only way to A/B whether the post chain is costing us anti-aliasing.
    set ok(v) { ok = v; },
    get sizes() { return ok ? { scene: [sceneRT.width, sceneRT.height], samples: sceneRT.samples, scale: resScale } : null; },
    get scale() { return resScale; },
    setScale(s) {
      const v = Math.max(0.5, Math.min(1, s));
      if (!ok || Math.abs(v - resScale) < 1e-6) return;
      resScale = v;
      try { doResize(); } catch (e) { warnDegraded(e); ok = false; }
    },
    get feedTexture() { return ok && feedRT ? feedRT.texture : null; },
    resize() {
      if (!ok) return;
      try { doResize(); } catch (e) { warnDegraded(e); ok = false; }
    },
    render() {
      if (!ok) { renderer.setRenderTarget(null); renderer.render(scene, camera); return; }
      const feedScreen = jumboFeedScreen, feedFrame = jumboFeedFrame;
      try {
        if (feedScreen) feedScreen.visible = false;
        if (feedFrame) feedFrame.visible = false;
        renderer.setRenderTarget(feedRT); renderer.render(scene, camera);
        if (feedScreen && !feedScreen.userData.liveFeed) {
          feedScreen.userData.liveFeed = true;
          feedScreen.material = new T.ShaderMaterial({
            uniforms: {
              tFeed: { value: feedRT.texture },
              uRepeat: { value: feedScreen.userData.feedRepeat },
              uOffset: { value: feedScreen.userData.feedOffset },
            },
            vertexShader: "varying vec2 vUv; void main(){ vUv=uv; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }",
            fragmentShader: "varying vec2 vUv; uniform sampler2D tFeed; uniform vec2 uRepeat,uOffset; void main(){ gl_FragColor=texture2D(tFeed,vUv*uRepeat+uOffset); }",
            side: T.DoubleSide,
            toneMapped: false,
          });
        }
        if (feedScreen) feedScreen.visible = true;
        if (feedFrame) feedFrame.visible = true;
        renderer.setRenderTarget(sceneRT); renderer.render(scene, camera);
        matBright.uniforms.tDiffuse.value = sceneRT.texture; quad.material = matBright;
        renderer.setRenderTarget(brightRT); renderer.render(postScene, postCam);
        quad.material = matBlur; matBlur.uniforms.uTexel.value.set(1 / brightRT.width, 1 / brightRT.height);
        let src = brightRT;
        for (let i = 0; i < 2; i++) {
          matBlur.uniforms.tDiffuse.value = src.texture; matBlur.uniforms.uDir.value.set(1, 0);
          renderer.setRenderTarget(blurA); renderer.render(postScene, postCam);
          matBlur.uniforms.tDiffuse.value = blurA.texture; matBlur.uniforms.uDir.value.set(0, 1);
          renderer.setRenderTarget(blurB); renderer.render(postScene, postCam);
          src = blurB;
        }
        quad.material = matComp; matComp.uniforms.tScene.value = sceneRT.texture; matComp.uniforms.tBloom.value = blurB.texture;
        renderer.setRenderTarget(null); renderer.render(postScene, postCam);
      } catch (e) {
        if (feedScreen) feedScreen.visible = true;
        if (feedFrame) feedFrame.visible = true;
        warnDegraded(e); ok = false;
        renderer.setRenderTarget(null); renderer.render(scene, camera);
      }
    },
  };
})();

// ----- dynamic resolution ---------------------------------------------------
// Every quality number above was tuned against the frame rate of ONE developer
// laptop. The machine driving the venue is unknown, and the failure it would
// produce — a slideshow in front of the whole company — is far worse than a
// slightly soft picture. So watch the frame time and hand resolution back when
// the GPU cannot hold the pace, then earn it again when it can.
//
// Deliberately asymmetric: drop after ~0.7s of slow frames, but require ~3s of
// comfortable ones before climbing, so the picture settles instead of pumping
// up and down. Levels are integers to keep the scale off floating-point drift.
//
// Thresholds are RELATIVE to the display's own frame budget, never absolute
// milliseconds. requestAnimationFrame is locked to the refresh rate, so a
// healthy 60Hz projector reports ~16.7ms and a healthy 120Hz laptop ~8.3ms —
// any fixed "fast" threshold that works on one is unreachable on the other, and
// the level could then only ever ratchet downwards. The budget is learned as a
// running minimum that relaxes upward very slowly.
const drs = { on: true, level: 0, maxLevel: 4, slow: 0, fast: 0, cool: 0,
              budget: 0, samples: 0, retry: 0, lastClimb: false };
const drsScale = (lv) => 1 - lv * 0.1;          // level 0..4 -> 1.00 .. 0.60
const DRS_WARMUP = 60;      // frames of budget-learning before acting on anything
const DRS_SLOW = 1.6;       // × budget: consistently missing frames
const DRS_FAST = 1.15;      // × budget: riding the refresh cap with room to spare
// the ?diag corner readout, created at the bottom of this file; null without ?diag
let diagEl = null;
const diagText = () => "build " + (window.__RW_BUILD__ || "UNSTAMPED")
  + "  ·  res " + Math.round(drsScale(drs.level) * 100) + "%";
function updateResolution(dt, wallDt) {
  if (!drs.on || !bloom.ok) return;
  drs.cool = Math.max(0, drs.cool - dt);
  const ms = wallDt * 1000;
  // a genuine stall — tab hidden, a 10MB parse, the debugger — is not a slow
  // GPU, and must not be allowed to drag the picture down on its way past
  if (ms > 500) { drs.slow = 0; drs.fast = 0; return; }
  // learn the refresh interval: adopt any faster frame at once, drift upward
  // only glacially so one lucky frame cannot pin the budget too low forever
  if (!drs.budget || ms < drs.budget) drs.budget = ms;
  else drs.budget += (ms - drs.budget) * 0.0005;
  if (drs.samples++ < DRS_WARMUP) return;
  // Hold the counters at zero through the whole settle window. They used to
  // keep accumulating during it, so a level inherited a head start from the
  // frames that were still catching up right after the change and could step
  // again the instant the cooldown lapsed — under a sustained marginal frame
  // rate that slammed straight to the floor instead of finding the highest
  // level the machine can actually hold. Every level now has to prove itself
  // over a full fresh run.
  if (drs.cool > 0) { drs.slow = 0; drs.fast = 0; return; }
  if (ms > drs.budget * DRS_SLOW) { drs.slow++; drs.fast = 0; }
  else if (ms < drs.budget * DRS_FAST) { drs.fast++; drs.slow = 0; }
  else { drs.slow = 0; drs.fast = 0; }
  let lv = drs.level;
  // back off after a climb that did not stick, so a machine sitting right on
  // the edge settles at the level it can hold instead of pumping every few
  // seconds for the whole show
  if (drs.slow >= 40 && lv < drs.maxLevel) {
    if (drs.lastClimb) drs.retry = Math.min(4, drs.retry + 1);
    lv++; drs.lastClimb = false;
  } else if (drs.fast >= 180 * (1 + drs.retry) && lv > 0) {
    lv--; drs.lastClimb = true;
  }
  if (lv === drs.level) return;
  drs.level = lv; drs.slow = 0; drs.fast = 0; drs.cool = 1.5;
  bloom.setScale(drsScale(lv));
  console.info(`dynamic resolution -> ${Math.round(drsScale(lv) * 100)}%`);
  if (diagEl) diagEl.textContent = diagText();
}

// ----- main loop ------------------------------------------------------------
function loop(t) {
  raf = requestAnimationFrame(loop);
  // render suspension: while the match view is hidden (tournament shell showing
  // the bracket, so #stage has no layout box) skip the sim + the full-res MSAA
  // + bloom pipeline entirely. Keep the rAF alive so the next match resumes
  // cleanly; reset lastT so the resume frame doesn't inherit a huge wall delta.
  if (stage.offsetParent === null || stage.clientHeight === 0) { lastT = 0; return; }
  const now = t || 0;
  // TWO deltas: the unclamped wall clock drives the playhead (playback
  // speed independent of render framerate — no slow-motion below 20fps, no
  // freeze when rAF stalls; processTickEvents batch-replays skipped ticks).
  // The 0.05-clamped dt below is cosmetic-only: particles, debris, shake and
  // flash decay, camera lerp.
  const wallDt = lastT ? (now - lastT) / 1000 : 0.016;
  let dt = lastT ? (now - lastT) / 1000 : 0.016; lastT = now;
  dt = Math.min(dt, 0.05);
  updateResolution(dt, wallDt);

  // loop-driven white-flash decay (never strands a veil over the frame)
  if (flashEl) { flashT = Math.max(0, flashT - dt * 3); flashEl.style.opacity = flashT; }

  // slow-mo on the deciding blow
  if (slowmoT > 0) { slowmoT = Math.max(0, slowmoT - dt); if (slowmoT === 0) setLetterbox(false); }
  const speedMul = slowmoT > 0 ? 0.32 : 1;

  if (playing && frames.length) {
    const playDt = Math.max(0, wallDt - fightHold);
    fightHold = Math.max(0, fightHold - wallDt);       // FIGHT beat lands first
    simTime += fps * playDt * speedMul;
    if (simTime >= frames.length - 1) simTime = frames.length - 1;
    // fully drain tick events (incl. the deciding blow) BEFORE declaring the
    // winner — otherwise a big catch-up batch's damage numbers land after
    // playing/atEnd already flipped, spawning "post-victory" hit feedback
    processTickEvents(Math.floor(simTime));
    if (simTime >= frames.length - 1) {
      playing = false; setPlayLabel(); showWinner();
      if (!endFired) { endFired = true; if (window.RW && typeof window.RW.onMatchEnd === "function") window.RW.onMatchEnd(); }
    }
  }
  let alivePts = [{ x: 0, z: 0 }];
  if (frames.length) alivePts = applyFrame(simTime);
  if (entrance && frames.length) updateEntrance(wallDt);   // pre-fight walk-on (wall clock)
  animateEnvironment(now);
  updateHazardFX(now * 0.001, dt);
  updateDmgFloats(dt);
  // advance the Blender robots' baked shoot clips
  for (const id in robotObjs) {
    const mx = robotObjs[id].mesh.userData.mixer;
    if (mx) mx.update(dt * speedMul);
  }
  updateFX(dt);
  // victory hero shot: slow orbital drift + a pumped ring/emissive on the winner
  if (celebrate) {
    if (performance.now() < celebrate.until) {
      cam.az += dt * 0.35;
      const wo = robotObjs[celebrate.id];
      if (wo) {
        wo.ring.visible = true;
        wo.ring.material.opacity = 0.9 + 0.3 * Math.sin(performance.now() * 0.008);
        const bm = wo.mesh.userData.bodyMat;
        if (bm) bm.emissiveIntensity = 1.1 + 0.35 * Math.sin(performance.now() * 0.01);
      }
    } else {
      celebrate = null;
    }
  }
  updateCamera(dt, alivePts);
  bloom.render();
  if (jumboFeedScreen && !bloom.ok) {
    jumboFeedScreen.userData.liveFeed = false;
    jumboFeedScreen.material = jumboFeedScreen.userData.staticMaterial;
    jumboFeedScreen = null;
  }
}

// ----- winner / voice -------------------------------------------------------
const synth = window.speechSynthesis || null;
function speak(text, opts) {
  if (!voiceOn || !synth) return; opts = opts || {};
  if (opts.flush) synth.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.rate = opts.rate || 1.05; u.pitch = opts.pitch || 1.0; u.volume = 1.0; synth.speak(u);
}
let voiceOn = false;

// Pre-rendered announcer clips (P3). Plays a baked clip for the first matching
// key; falls back to browser speech when a clip is absent. MUST match
// tournament/voice/lines.py slug().
function slug(name) { return String(name).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, ""); }

// Serialised lane for lines that must not talk over each other (the walk-on
// roll-call). Everything else still fires the instant its event lands.
let clipQueue = [], clipPlaying = null;
function pumpClips() {
  if (clipPlaying || !clipQueue.length) return;
  const done = () => { clipPlaying = null; pumpClips(); };
  try {
    const a = new Audio(clipQueue.shift());
    a.volume = 1; clipPlaying = a;
    a.addEventListener("ended", done); a.addEventListener("error", done);
    a.play().catch(done);
  } catch (e) { clipPlaying = null; }
}
function flushClips() {
  clipQueue = [];
  if (clipPlaying) { try { clipPlaying.pause(); } catch (e) { /* ignore */ } clipPlaying = null; }
}

function playClip(keys, fallbackText, opts) {
  if (!voiceOn) return; opts = opts || {};
  const map = window.__VOICE_CLIPS__;
  if (map) {
    const k = (Array.isArray(keys) ? keys : [keys]).find((x) => map[x]);
    if (k) {
      if (opts.queue) { clipQueue.push(map[k]); pumpClips(); return; }
      if (opts.flush) flushClips();
      try {
        const a = new Audio(map[k]); a.volume = 1;
        // a rejected/undecodable clip must still speak the fallback text, not
        // go silent -- the old `.catch(() => {})` swallowed the failure here.
        a.play().catch(() => { if (fallbackText) speak(fallbackText, opts); });
        return;
      } catch (e) { /* fall through */ }
    }
  }
  if (fallbackText) speak(fallbackText, opts);
}

function tiebreakChampion() {
  const last = frames[frames.length - 1];
  const cands = last.robots.filter((r) => r.team !== "house");   // house can't be champion
  const death = {}; cands.forEach((r) => death[r.id] = Infinity);
  for (let i = 0; i < frames.length; i++)
    frames[i].robots.forEach((r) => { if (!r.alive && death[r.id] === Infinity && r.team !== "house") death[r.id] = frames[i].tick; });
  const ranked = cands.slice().sort((a, b) =>
    (death[b.id] - death[a.id]) || ((b.dmg || 0) - (a.dmg || 0)) || (a.id - b.id));
  const top = ranked[0];
  return { name: top.name, basis: `survived to tick ${death[top.id]}, ${top.dmg || 0} damage dealt`, robot: top };
}
function winningTeam() {
  // Sum surviving HP (then damage) per team across the final frame; the strongest
  // side wins. Works for a clean wipe, a time-cap, or a mutual KO.
  const last = frames[frames.length - 1];
  const agg = {};
  last.robots.forEach((r) => { if (r.team === "house") return;   // never the winner
    const t = teamOf(r); const a = agg[t] || (agg[t] = [0, 0, 0]);
    a[0] += r.alive ? 1 : 0; a[1] += Math.max(0, r.hp); a[2] += r.dmg || 0; });
  const teams = Object.keys(agg).sort((x, y) =>
    (agg[y][0] - agg[x][0]) || (agg[y][1] - agg[x][1]) || (agg[y][2] - agg[x][2]));
  const win = teams[0];
  const clean = agg[win][0] > 0 && teams.slice(1).every((t) => agg[t][0] === 0);
  return { team: win, clean };
}
// victory hero shot: punch the kill-cam focus onto the winner, let the main
// loop drift the camera and pump their ring/emissive (see celebrate above),
// and pop 4 staggered team-colored bursts — banner reveal is delayed
// separately (showBanner) so the hero shot lands first
function heroShot(champ) {
  if (!champ) return;
  const col = colorFor(champ);
  cam.focus = { x: champ.x - AW / 2, z: champ.y - AH / 2, life: 4.0, max: 4.0, zoom: 0.5 };
  celebrate = { id: champ.id, until: performance.now() + 4000 };
  for (let i = 0; i < 4; i++)
    setTimeout(() => explode(champ.x + (Math.random() - 0.5) * 90, champ.y + (Math.random() - 0.5) * 90,
      45 + Math.random() * 20, col), i * 450);
}
function showBanner(who, sub, col) {
  // lower-third victory card slides in AFTER the hero shot has had time to land
  // (arena stays visible behind it — see #banner in the template). col tints the
  // card accent + name to the winner's team.
  bannerTimer = setTimeout(() => {
    banner.querySelector(".who").textContent = who;
    const subEl = banner.querySelector(".sub");
    subEl.textContent = sub; subEl.style.display = sub ? "" : "none";
    if (col != null) banner.style.setProperty("--wincol", "#" + ((col >>> 0) & 0xffffff).toString(16).padStart(6, "0"));
    banner.classList.add("show");
  }, 2500);
}
function showWinner() {
  const last = frames[frames.length - 1];
  const alive = last.robots.filter((r) => r.alive && r.team !== "house");
  let who, sub = DEBUG ? `${frames.length} ticks` : "", say, keys, champ;
  if (teamMode) {
    const w = winningTeam();
    who = "TEAM " + w.team;
    sub = w.clean
      ? (DEBUG ? `${frames.length} ticks` : "")
      : (DEBUG ? `${frames.length} ticks · on health` : "on health");
    say = `Team ${w.team} wins!`;
    keys = ["win_" + slug(w.team), "winner"];
    const members = last.robots.filter((r) => teamOf(r) === w.team);
    const champM = members.find((r) => r.alive) || members[0];
    heroShot(champM);
    showBanner(who, sub, colorFor(champM));
    ticker(`🏆 Team ${w.team} takes it`);
    if (!saidWinner) { saidWinner = true; playClip(keys, say, { flush: true, rate: 0.98 }); }
    return;
  }
  if (alive.length === 1) {
    champ = alive[0]; who = champ.name; say = `${who} wins!`;
    const flawless = champ.hp >= champ.max_hp * 0.95;
    keys = [flawless ? "flawless" : null, "win_" + slug(who), "winner"].filter(Boolean);
    if (flawless) sub = "FLAWLESS VICTORY";
  } else if (alive.length === 0) {
    const tb = tiebreakChampion(); who = tb.name; sub = `mutual KO — tiebreak: ${tb.basis}`;
    say = `Double knockout! ${who} takes it on a tiebreak.`; keys = ["double_ko", "winner"];
    champ = tb.robot;
  } else {
    champ = alive.slice().sort((a, b) => b.hp - a.hp)[0]; who = champ.name + " (on HP)";
    say = `Time! ${champ.name} wins on health.`; keys = ["win_" + slug(champ.name), "winner"];
  }
  heroShot(champ);
  showBanner(who, sub, colorFor(champ));
  ticker(`🏆 ${who} is the champion`);
  if (!saidWinner) { saidWinner = true; playClip(keys, say, { flush: true, rate: 0.98 }); }
}

// ----- load / reset ---------------------------------------------------------
function disposeTree(obj) {
  obj.traverse((o) => {
    if (o.geometry) o.geometry.dispose();
    if (o.material) { (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => m.dispose()); }
  });
}
function clearMatch() {
  Object.values(robotObjs).forEach((o) => { scene.remove(o.group); disposeTree(o.group); });
  Object.values(rocketObjs).forEach((o) => { scene.remove(o.group); disposeTree(o.group); });
  Object.values(mineObjs).forEach((o) => { scene.remove(o.group); disposeTree(o.group); });
  Object.values(pickupObjs).forEach((o) => { scene.remove(o.group); disposeTree(o.group); });
  for (const k in robotObjs) delete robotObjs[k];
  for (const k in rocketObjs) delete rocketObjs[k];
  for (const k in mineObjs) delete mineObjs[k];
  for (const k in pickupObjs) delete pickupObjs[k];
  SPARKS.items.forEach((p) => { p.life = 0; });
  SMOKE.items.forEach((p) => { p.life = 0; });
  slugs.forEach((s) => { scene.remove(s.sp); s.sp.material.dispose(); });
  slugs.length = 0;
  [debris, scorches, shells, shockwaves].forEach((arr) => {
    arr.forEach((d) => { scene.remove(d.mesh); d.mesh.geometry.dispose(); d.mesh.material.dispose(); });
    arr.length = 0;
  });
  dmgFloats.forEach((f) => { scene.remove(f.sp); f.sp.material.map.dispose(); f.sp.material.dispose(); });
  dmgFloats.length = 0;
}
function loadText(txt) {
  // v2 recordings open with a metadata header line ({schema, seed, ...}); skip
  // it (and any future non-frame line) rather than crashing. A malformed line
  // (half-written file from a crash) truncates playback instead of tracebacking
  // on the broadcast surface.
  const fr = [];
  let meta = null;
  const lines = txt.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i].trim();
    if (!l) continue;
    let o;
    try { o = JSON.parse(l); } catch (e) {
      console.warn(`recording truncated at tick ${fr.length} (bad line ${i + 1}) — playing what loaded`);
      break;
    }
    if (o && typeof o === "object" && !o.robots) { if (o.schema) meta = o; continue; }
    fr.push(o);
  }
  window.RW_META = meta;
  if (!fr.length) return;
  clearMatch();
  frames = fr;
  // broadcast pacing: default playhead rate lands any match in a <=35s window
  // (playhead rate only — tick data and the recording untouched)
  fps = Math.min(80, Math.max(18, Math.round(frames.length / 35)));   // clamp into the slider's [10,80]
  if (speed) speed.value = fps;
  updateSpeedLabel();
  const s0 = frames[0].status || {}; AW = s0.w || 1280; AH = s0.h || 768;
  names = {}; alivePrev = {}; voiceKey = {}; hpBand = {}; hazardSaid = {};
  // names[] is the short display name; voiceKey[] slugs the FULL "Team:Bot"
  // label, which is what the baked per-bot clips are keyed on (voice/lines.py).
  frames[0].robots.forEach((r) => {
    names[r.id] = botLabel(r); voiceKey[r.id] = slug(r.name); alivePrev[r.id] = true;
  });
  buildArena(s0.walls || [], s0.hazards || []);
  applyWeather(s0.weather || "clear");
  buildScoreboard(frames[0].robots);
  // tint the accent house-spots to the two sides (teams, else top-2 bots)
  (function () {
    const rs = frames[0].robots;
    let cols;
    if (teamMode && teamNames.length >= 2) {
      cols = teamNames.slice(0, 2).map((t) => colorFor(rs.find((r) => teamOf(r) === t)));
    } else {
      cols = rs.slice(0, 2).map((r) => colorFor(r));
    }
    setSpotTeams(cols);
  })();
  for (const k in lastHp) delete lastHp[k];
  for (const k in dmgPending) delete dmgPending[k];   // ids restart at 0 each match —
  for (const k in lastDmgAt) delete lastDmgAt[k];     // a stale amount lands on the first hit
  if (tickerEl) tickerEl.innerHTML = "";
  simTime = 0; lastTickSeen = -1; saidGo = false; saidWinner = false; introT = INTRO_MAX;
  slowmoT = 0; slowmoFired = false; endFired = false; setLetterbox(false);
  celebrate = null; if (bannerTimer) { clearTimeout(bannerTimer); bannerTimer = null; }
  flashT = 0; if (flashEl) flashEl.style.opacity = 0;
  fightHold = 0;
  banner.classList.remove("show");
  if (frames[0].robots.some((r) => r.team === "house")) {
    ticker("🚨 HOUSE ROBOT ON PATROL — Gatekeeper is live. Stay clear.");
    playClip(["house_robot"], "House robot on patrol. Stay clear!");
  }
  showTitleCard(frames[0].robots.filter((r) => r.team !== "house").length);
  startEntrance();   // robots drive in through the gates, then playback begins
}

// ----- controls -------------------------------------------------------------
function setPlayLabel() { playBtn.textContent = playing ? "PAUSE" : "PLAY"; }
// honest AUTO-CAM/MANUAL button: reflects cam.auto plus a live drag-override
function syncCamBtn() {
  const b = $("autocam"); if (!b) return;
  const held = cam.auto && cam.override;
  b.textContent = cam.auto ? (held ? "MANUAL·HOLD" : "AUTO-CAM") : "MANUAL";
  b.setAttribute("aria-pressed", cam.auto);
}
// playback-rate readout next to the speed slider (ticks advanced per second)
function updateSpeedLabel() { const el = $("speedval"); if (el) el.textContent = fps + " fps"; }
function play(on) {
  playing = on; setPlayLabel(); if (on) banner.classList.remove("show");
  // rw-playing arms the auto-hiding chrome ("playing" is the tournament shell's
  // own view-switch class — don't collide with it)
  document.body.classList.toggle("rw-playing", on);
}
// video-player chrome: any mouse move shows the UI, 2.5s idle hides it again
let uiTimer = null;
function pokeUI() {
  document.body.classList.add("showui");
  clearTimeout(uiTimer);
  uiTimer = setTimeout(() => document.body.classList.remove("showui"), 2500);
}
window.addEventListener("mousemove", pokeUI);
pokeUI();
playBtn.onclick = () => { if (simTime >= frames.length - 1) restart(); else play(!playing); };
function restart() {
  simTime = 0; lastTickSeen = -1; saidGo = false; saidWinner = false; introT = INTRO_MAX;
  slowmoT = 0; slowmoFired = false; endFired = false; setLetterbox(false);
  celebrate = null; if (bannerTimer) { clearTimeout(bannerTimer); bannerTimer = null; }
  flashT = 0; if (flashEl) flashEl.style.opacity = 0;
  fightHold = 0;
  sdAnnounced = false;                      // replay re-announces sudden death
  hpBand = {}; hazardSaid = {};             // ...and the damage-state / hazard callouts
  frames.length && frames[0].robots.forEach((r) => alivePrev[r.id] = true);
  for (const k in lastHp) delete lastHp[k];
  for (const k in dmgPending) delete dmgPending[k];
  for (const k in lastDmgAt) delete lastDmgAt[k];
  if (tickerEl) tickerEl.innerHTML = "";
  banner.classList.remove("show");
  Object.values(robotObjs).forEach((o) => { o.dead = false; o.dyingAt = null; o.deadAtSim = null; o.label.visible = true; o.mesh.rotation.z = 0; o.mesh.position.y = 0; o.flash = 0; });
  startEntrance();   // replay gets the walk-on too
}
$("restart").onclick = restart;
speed.oninput = (e) => { fps = +e.target.value; updateSpeedLabel(); };
$("voice").onclick = (e) => {
  voiceOn = !voiceOn; const b = e.currentTarget;
  b.textContent = voiceOn ? "VOICE ON" : "VOICE"; b.setAttribute("aria-pressed", voiceOn);
  if (synth) { if (voiceOn) speak("Commentary on.", { flush: true }); else synth.cancel(); }
};
$("autocam").onclick = () => { cam.auto = !cam.auto; cam.override = false; cam.manualTgt = null; syncCamBtn(); };

$("file").onchange = (e) => { const f = e.target.files[0]; if (f) { const r = new FileReader(); r.onload = () => loadText(r.result); r.readAsText(f); } };
["dragover", "drop"].forEach((ev) => document.addEventListener(ev, (e) => e.preventDefault()));
document.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) { const r = new FileReader(); r.onload = () => loadText(r.result); r.readAsText(f); } });

// manual orbit (overrides auto-cam target framing's az/el while dragging)
const cv = $("cv");
cv.addEventListener("pointerdown", (e) => { cam.dragging = true; cam.px = e.clientX; cam.py = e.clientY; });
window.addEventListener("pointerup", () => cam.dragging = false);
window.addEventListener("pointermove", (e) => {
  if (!cam.dragging) return;
  cam.az -= (e.clientX - cam.px) * 0.005; cam.el += (e.clientY - cam.py) * 0.004;
  cam.el = Math.max(0.15, Math.min(1.45, cam.el)); cam.px = e.clientX; cam.py = e.clientY;
  // dragging while auto = temporary manual override: freeze framing, flip the
  // button to MANUAL·HOLD, and re-arm the ~2.6s idle timer back to auto
  if (cam.auto && !cam.override) { cam.override = true; cam.manualTgt = null; syncCamBtn(); }
  cam.overrideUntil = performance.now() + 2600;
});
cv.addEventListener("wheel", (e) => { e.preventDefault(); cam.distMul = Math.max(0.4, Math.min(3, cam.distMul * (1 + Math.sign(e.deltaY) * 0.1))); }, { passive: false });

// ----- resize ---------------------------------------------------------------
function resize() {
  const w = stage.clientWidth, h = stage.clientHeight;
  // Re-read the display density every resize: dragging the window to a monitor
  // with a different pixel density used to leave this pinned at whatever it was
  // on page load, so the arena rendered at the wrong resolution for the screen.
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
  bloom.resize();
}
window.addEventListener("resize", resize);
resize();
raf = requestAnimationFrame(loop);

// ----- background music (procedural, self-contained; no external asset) ------
// A low-key arena bed: a slow minor pad + a soft pulse. Built with WebAudio so
// it needs no file (the offline CSP blocks fetching audio). Starts on the first
// user gesture (autoplay policy) and is muted with the 🎵 button.
const music = (() => {
  // Default OFF. The synthesised bed below is a placeholder and Alex has judged
  // it not good enough to open an all-hands with, so it does not play unless
  // someone deliberately turns it on. Flip back to `true` once a real licensed
  // track replaces it.
  let ctx = null, master = null, timer = null, on = false, step = 0;
  // Driving industrial bed: 140bpm, four-on-the-floor kick, a distorted offbeat
  // bass, snare on 2 and 4, and a hat grid. The old version was a slow ambient
  // pad, which read as a hotel lobby under a robot fight.
  const BPM = 140, BEAT = 60 / BPM, BAR = BEAT * 4;
  const ROOTS = [55.0, 55.0, 73.42, 65.41];    // A1 A1 D2 C2 — riff, not wallpaper
  const hz = (semis, base) => base * Math.pow(2, semis / 12);

  let noiseBuf = null;
  function noise() {
    if (!noiseBuf) {
      noiseBuf = ctx.createBuffer(1, ctx.sampleRate * 0.5, ctx.sampleRate);
      const d = noiseBuf.getChannelData(0);
      for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
    }
    const src = ctx.createBufferSource(); src.buffer = noiseBuf; return src;
  }
  function kick(t) {
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = "sine";
    o.frequency.setValueAtTime(150, t);
    o.frequency.exponentialRampToValueAtTime(42, t + 0.11);   // pitch drop = punch
    g.gain.setValueAtTime(0.9, t);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.32);
    o.connect(g); g.connect(master); o.start(t); o.stop(t + 0.34);
  }
  function snare(t) {
    const n = noise(), g = ctx.createGain(), f = ctx.createBiquadFilter();
    f.type = "highpass"; f.frequency.value = 1400;
    g.gain.setValueAtTime(0.34, t);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.19);
    n.connect(f); f.connect(g); g.connect(master); n.start(t); n.stop(t + 0.21);
  }
  function hat(t, gain) {
    const n = noise(), g = ctx.createGain(), f = ctx.createBiquadFilter();
    f.type = "highpass"; f.frequency.value = 7000;
    g.gain.setValueAtTime(gain, t);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.05);
    n.connect(f); f.connect(g); g.connect(master); n.start(t); n.stop(t + 0.07);
  }
  function bass(freq, t, dur) {
    const o = ctx.createOscillator(), o2 = ctx.createOscillator();
    const g = ctx.createGain(), f = ctx.createBiquadFilter();
    o.type = "sawtooth"; o.frequency.value = freq;
    o2.type = "square"; o2.frequency.value = freq; o2.detune.value = -8;   // grit
    f.type = "lowpass"; f.Q.value = 9;
    f.frequency.setValueAtTime(1500, t);
    f.frequency.exponentialRampToValueAtTime(240, t + dur);                // filter sweep
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(0.24, t + 0.012);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(f); o2.connect(f); f.connect(g); g.connect(master);
    o.start(t); o2.start(t); o.stop(t + dur + 0.03); o2.stop(t + dur + 0.03);
  }
  function stab(root, t, dur) {
    [0, 7, 15].forEach((iv, i) => {         // minor-ish power stack
      const o = ctx.createOscillator(), g = ctx.createGain(), f = ctx.createBiquadFilter();
      o.type = "sawtooth"; o.frequency.value = hz(iv, root * 4); o.detune.value = (i - 1) * 11;
      f.type = "lowpass"; f.frequency.value = 2600;
      o.connect(f); f.connect(g); g.connect(master);
      g.gain.setValueAtTime(0, t);
      g.gain.linearRampToValueAtTime(0.05, t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
      o.start(t); o.stop(t + dur + 0.03);
    });
  }
  function tick() {
    if (!ctx) return;
    const t = ctx.currentTime + 0.05, root = ROOTS[step % ROOTS.length];
    for (let b = 0; b < 4; b++) {
      const tb = t + b * BEAT;
      kick(tb);                                     // four on the floor
      if (b % 2 === 1) snare(tb);                   // backbeat
      hat(tb + BEAT / 2, 0.12);
      hat(tb + BEAT / 4, 0.05);
      bass(root, tb + BEAT / 2, BEAT * 0.42);       // offbeat drive
    }
    bass(root, t, BEAT * 0.5);
    stab(root, t, BEAT * 0.9);
    if (step % 4 === 3) stab(hz(3, root), t + BEAT * 3, BEAT * 0.8);   // turnaround
    step++;
    timer = setTimeout(tick, BAR * 1000);
  }
  function ensure() {
    if (ctx) return;
    const AC = window.AudioContext || window.webkitAudioContext; if (!AC) return;
    ctx = new AC(); master = ctx.createGain();
    master.gain.value = on ? 0.085 : 0;   // drums are hot — sit under the announcer
    master.connect(ctx.destination);
    tick();
  }
  return {
    kick() { ensure(); if (ctx && ctx.state === "suspended") ctx.resume(); },
    toggle() { on = !on; if (master) master.gain.linearRampToValueAtTime(on ? 0.085 : 0, ctx.currentTime + 0.2); return on; },
    get on() { return on; },
  };
})();
const musicBtn = document.getElementById("music");
// the bed defaults on now, so the button has to say so from the start
if (musicBtn) {
  musicBtn.setAttribute("aria-pressed", music.on);
  musicBtn.textContent = music.on ? "MUSIC ON" : "MUSIC";
}
if (musicBtn) musicBtn.onclick = () => {
  const state = music.toggle();
  musicBtn.setAttribute("aria-pressed", state);
  musicBtn.textContent = state ? "MUSIC ON" : "MUSIC";
};
// start the bed on the first user gesture anywhere (autoplay policy)
["pointerdown", "keydown"].forEach((ev) =>
  window.addEventListener(ev, () => music.kick(), { once: true }));

// ----- programmatic API for the tournament shell ----------------------------
// The World-Cup tournament page (tournament.html) drives the arena match-by-match
// through this: load a match's JSONL, play it, and get a one-shot callback when
// the replay finishes so it can advance the bracket + standings.
window.RW = {
  loadText,                               // (jsonlText) -> load + auto-play a match
  play,                                   // (bool)
  restart,                                // replay the current match
  isPlaying: () => playing,
  atEnd: () => frames.length > 0 && simTime >= frames.length - 1,
  onMatchEnd: null,                       // set by the tournament shell
  debug: { scene, camera, renderer, robotObjs, cam, bloom, drs,
           crowdCount: () => crowdCount,
           // QA handle: feed the dynamic-resolution controller synthetic frame
           // times to watch it step, without needing a GPU slow enough to sag
           stepRes: updateResolution,
           get arenaGroup() { return arenaGroup; },
           get celebrate() { return celebrate; },
           seek: (t) => { simTime = Math.max(0, Math.min(t, frames.length - 1)); } },
};

// ----- build stamp (opt-in) ------------------------------------------------
// This page is ~10MB and browsers hold onto it. Open it with ?diag to show the
// build timestamp on screen, so "is this actually the build I just made" is a
// glance rather than an argument. Off by default: nothing extra on the big
// screen during the show.
if (/[?&]diag\b/.test(location.search)) {
  diagEl = document.createElement("div");
  diagEl.textContent = diagText();
  diagEl.style.cssText = "position:fixed;left:8px;bottom:6px;z-index:9999;opacity:.65;" +
    "font:11px ui-monospace,SFMono-Regular,Menlo,monospace;color:#8fb4d4;pointer-events:none";
  document.body.appendChild(diagEl);
}

// ----- autoload embedded demo match (instant picture; file/drop overrides) --
// payloads ship gzipped: inflate them, then parse models, then roll the match
(async () => {
  try {
    if (window.__RW_MODELS_GZ__ && !window.__RW_MODELS__)
      window.__RW_MODELS__ = JSON.parse(await gunzipB64(window.__RW_MODELS_GZ__));
    if (window.__EMBEDDED_MATCH_GZ__ && !window.__EMBEDDED_MATCH__)
      window.__EMBEDDED_MATCH__ = await gunzipB64(window.__EMBEDDED_MATCH_GZ__);
  } catch (e) { console.error("payload inflate failed", e); }
  preloadModels(() => {
    if (window.__EMBEDDED_MATCH__) { try { loadText(window.__EMBEDDED_MATCH__); } catch (e) { console.error("embedded match parse failed", e); } }
  });
})();
})();
