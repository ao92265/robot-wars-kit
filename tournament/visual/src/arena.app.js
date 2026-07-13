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
// (DecompressionStream: no library, works from file://, still fully offline)
async function gunzipB64(b64) {
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

// ----- match state ----------------------------------------------------------
let frames = [], AW = 1280, AH = 768, names = {};
let simTime = 0;            // playback head, in ticks (float)
let playing = false, fps = 18, lastT = 0, raf = null;
let saidGo = false, saidWinner = false, lastTickSeen = -1, alivePrev = {};

// ============================================================================
// THREE scene
// ============================================================================
const renderer = new T.WebGLRenderer({ canvas: $("cv"), antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = T.PCFSoftShadowMap;
renderer.outputColorSpace = T.SRGBColorSpace;
renderer.toneMapping = T.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.12;        // broadcast: high clarity, not blown out

// Broadcast Combat palette: a brightly-lit TV pit against a cool studio slate,
// team-colored key spots, clean painted chassis. Not the molten foundry.
const BG_COL = 0x171d29;
const scene = new T.Scene();
scene.background = new T.Color(BG_COL);
// No scene fog: the depth haze muddied the broadcast look on the big screen.

const camera = new T.PerspectiveCamera(52, 16 / 9, 1, 8000);

// lights: bright neutral ambient + white studio key (shadows) + soft accent fills.
// Intensities tuned so the floor sits ~0.6 luminance — bright TV pit, but safely
// BELOW the bloom threshold (blown-out white floor was the first-pass failure).
scene.add(new T.HemisphereLight(0xaec4e0, 0x2a3140, 0.75));
const key = new T.DirectionalLight(0xffffff, 1.25);
key.position.set(-400, 900, 300);
key.castShadow = true;
key.shadow.mapSize.set(2048, 2048);
key.shadow.camera.near = 100; key.shadow.camera.far = 2600;
const sc = key.shadow.camera;
sc.left = -900; sc.right = 900; sc.top = 700; sc.bottom = -700;
key.shadow.bias = -0.0006;
scene.add(key);
// broad soft fill from the opposite side so the pit stays bright and readable
const fillKey = new T.DirectionalLight(0xdfe8f5, 0.4); fillKey.position.set(500, 600, -400); scene.add(fillKey);
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

// ----- procedural textures (no external files) -----------------------------
function floorTexture() {
  const c = document.createElement("canvas"); c.width = c.height = 1024;
  const g = c.getContext("2d");
  // painted concrete combat pit — mid-grey base (bright enough to read on a projector)
  g.fillStyle = "#59606e"; g.fillRect(0, 0, 1024, 1024);
  // subtle panel seams (large 4x4 plates)
  g.strokeStyle = "rgba(20,24,32,0.35)"; g.lineWidth = 3;
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

// Billboard screen mesh. The finished artwork gets a BRAND-NEW material built
// around its texture: this three build never recompiles an already-rendered
// material to include a later-attached map (the screens rendered their
// mapless base state forever), so we swap the whole material instead.
function jumboScreen(w, h) {
  const mesh = new T.Mesh(new T.PlaneGeometry(w, h),
    new T.MeshBasicMaterial({ color: 0xf4f6f9, side: T.DoubleSide }));
  const c = document.createElement("canvas"); c.width = 512; c.height = 256;
  const g = c.getContext("2d");
  const colorLogo = typeof window.__HARRIS_LOGO_COLOR__ === "string";
  const attach = () => {
    mesh.material = new T.MeshBasicMaterial({ map: new T.CanvasTexture(c), side: T.DoubleSide });
  };
  if (colorLogo) {
    // sponsor billboard: the full-colour company logo on a clean white board
    g.fillStyle = "#f4f6f9"; g.fillRect(0, 0, 512, 256);
    g.fillStyle = "#dfe4ec"; g.fillRect(0, 246, 512, 10);   // subtle base strip
    const img = new Image();
    img.onload = () => {
      const pad = 36;
      const s = Math.min((512 - pad * 2) / img.width, (256 - pad * 2) / img.height);
      const w = img.width * s, h = img.height * s;
      g.drawImage(img, (512 - w) / 2, (256 - h) / 2, w, h);
      // LIVE bug in the corner so it still reads as a broadcast screen
      g.fillStyle = "#ff3b3b"; g.beginPath(); g.arc(446, 30, 8, 0, 7); g.fill();
      g.fillStyle = "#10141c"; g.font = "800 20px ui-sans-serif, sans-serif";
      g.textAlign = "left"; g.fillText("LIVE", 460, 37);
      attach();
    };
    img.src = window.__HARRIS_LOGO_COLOR__;
    return mesh;
  }
  const hasLogo = typeof window.__HARRIS_LOGO__ === "string";
  function draw() {
    g.fillStyle = "#070b14"; g.fillRect(0, 0, 512, 256);
    g.strokeStyle = "rgba(63,208,201,0.25)"; g.lineWidth = 1;
    for (let i = 0; i < 512; i += 24) { g.beginPath(); g.moveTo(i, 0); g.lineTo(i, 256); g.stroke(); }
    for (let i = 0; i < 256; i += 24) { g.beginPath(); g.moveTo(0, i); g.lineTo(512, i); g.stroke(); }
    g.textAlign = "center";
    g.fillStyle = "#3fd0c9"; g.font = "900 58px ui-sans-serif, sans-serif";
    g.fillText("ROBOT WARS", 256, hasLogo ? 132 : 110);
    g.fillStyle = "#7e8aa0"; g.font = "700 22px ui-sans-serif, sans-serif";
    g.fillText(hasLogo ? "ALL HANDS WORKSHOP" : "HARRIS ALL HANDS WORKSHOP", 256, hasLogo ? 168 : 150);
    g.fillStyle = "#ff5d5d"; g.beginPath(); g.arc(196, hasLogo ? 208 : 196, 9, 0, 7); g.fill();
    g.fillStyle = "#fff"; g.font = "800 26px ui-sans-serif, sans-serif"; g.textAlign = "left";
    g.fillText("LIVE", 214, hasLogo ? 217 : 205);
  }
  draw();
  if (hasLogo) {
    // real Harris logo (data URI SVG — decodes offline, no fetch) across the top
    const img = new Image();
    img.onload = () => {
      const w = 190, h = w * (img.height / img.width) || 60;   // svg aspect ≈ 3.15
      g.drawImage(img, 256 - w / 2, 14, w, h);
      attach();
    };
    img.src = window.__HARRIS_LOGO__;
  } else {
    attach();
  }
  return mesh;
}
// Harris logo in the page header (arena + tournament shells both have <header>)
(function () {
  if (typeof window.__HARRIS_LOGO__ !== "string") return;
  const h = document.querySelector("header");
  if (!h) return;
  const img = document.createElement("img");
  img.src = window.__HARRIS_LOGO__; img.alt = "Harris";
  img.style.cssText = "height:20px;margin-right:10px;flex:none;";
  h.insertBefore(img, h.firstChild);
})();

// animated env refs (rebuilt with the arena)
let envAnim = { hazards: [], jumbos: [], crowd: null };
let glbArenaActive = false;   // Blender arena brings its own colosseum bowl
function buildEnvironment() {
  envAnim = { hazards: [], jumbos: [], crowd: null };
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
  // twinkling crowd (dim points along the stands)
  const CN = 1400, cpos = new Float32Array(CN * 3), ccol = new Float32Array(CN * 3);
  for (let i = 0; i < CN; i++) {
    const side = i % 4, off = 150 + Math.random() * (tiers * step - 80), y = 30 + (off - 150) / (tiers * step) * (tiers * riseH);
    let x, z;
    if (side === 0) { x = (Math.random() - 0.5) * (AW + off * 2); z = -halfH - off; }
    else if (side === 1) { x = (Math.random() - 0.5) * (AW + off * 2); z = halfH + off; }
    else if (side === 2) { x = -halfW - off; z = (Math.random() - 0.5) * (AH + off * 2); }
    else { x = halfW + off; z = (Math.random() - 0.5) * (AH + off * 2); }
    cpos[i * 3] = x; cpos[i * 3 + 1] = y + Math.random() * 30; cpos[i * 3 + 2] = z;
    const h = Math.random(); ccol[i * 3] = 0.4 + h * 0.6; ccol[i * 3 + 1] = 0.4 + Math.random() * 0.4; ccol[i * 3 + 2] = 0.5 + Math.random() * 0.5;
  }
  const cgeo = new T.BufferGeometry(); cgeo.setAttribute("position", new T.BufferAttribute(cpos, 3)); cgeo.setAttribute("color", new T.BufferAttribute(ccol, 3));
  const crowd = new T.Points(cgeo, new T.PointsMaterial({ size: 6, vertexColors: true, transparent: true, opacity: 0.85, depthWrite: false }));
  crowd.frustumCulled = false; arenaGroup.add(crowd); envAnim.crowd = crowd;

  // jumbotron screens (corners, angled inward) + a big backdrop sign; the
  // Blender colosseum's stands are deeper/taller, so screens move out and up.
  // ry aims each plane's +Z normal INTO the arena (they faced outward before,
  // showing the arena camera their mirrored backs).
  const jOff = glbArenaActive ? 340 : 80, jY = glbArenaActive ? 330 : 230;
  const screens = [
    { x: -halfW - jOff, z: -halfH - jOff, ry: Math.PI * 0.25, w: 360, h: 180, y: jY },
    { x: halfW + jOff, z: -halfH - jOff, ry: -Math.PI * 0.25, w: 360, h: 180, y: jY },
    { x: 0, z: -halfH - (glbArenaActive ? 460 : 260), ry: 0, w: 900, h: 300, y: glbArenaActive ? 420 : 320 },
  ];
  screens.forEach((s) => {
    const m = jumboScreen(s.w, s.h);
    m.position.set(s.x, s.y, s.z); m.rotation.y = s.ry; arenaGroup.add(m);
    const frame = new T.Mesh(new T.BoxGeometry(s.w + 16, s.h + 16, 8),
      new T.MeshStandardMaterial({ color: 0x10141c, roughness: 0.6, metalness: 0.7, emissive: 0x3fd0c9, emissiveIntensity: 0.3 }));
    frame.position.set(s.x, s.y, s.z); frame.rotation.y = s.ry;
    frame.position.add(new T.Vector3(-Math.sin(s.ry) * 6, 0, -Math.cos(s.ry) * 6));   // frame sits behind the screen
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
    gl_FragColor = vec4(col, a);
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
    playClip("final_blow", "Sudden death! The floor is closing in!", { flush: true });
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
    });
    model.scale.setScalar(MODEL_SCALE);
    arenaGroup.add(model);
  } else {
    const ftex = floorTexture(); ftex.repeat.set(1, 1);   // painted markings map once across the pit
    const floor = new T.Mesh(
      new T.PlaneGeometry(AW, AH),
      new T.MeshStandardMaterial({ map: ftex, roughness: 0.82, metalness: 0.15, color: 0xdfe4ec }));
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
      const e = new T.LineSegments(new T.EdgesGeometry(new T.BoxGeometry(hz.w, 1, hz.h)),
        new T.LineBasicMaterial({ color: col, transparent: true, opacity: op }));
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
      o.material.emissiveIntensity = 0.08;
      bodyMat = o.material;
    }
  });
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

// HP bar + name on one billboard sprite (canvas texture, redrawn on change)
function makeLabel() {
  const c = document.createElement("canvas"); c.width = 256; c.height = 80;
  const tex = new T.CanvasTexture(c); tex.colorSpace = T.SRGBColorSpace;
  const mat = new T.SpriteMaterial({ map: tex, transparent: true, depthTest: false });
  const sp = new T.Sprite(mat); sp.renderOrder = 10;
  sp.userData = { canvas: c, tex, last: "" };
  return sp;
}
function drawLabel(sp, name, frac, alive, col) {
  const key = `${name}|${(frac * 50) | 0}|${alive}`;
  if (sp.userData.last === key) return;
  sp.userData.last = key;
  const c = sp.userData.canvas, g = c.getContext("2d");
  g.clearRect(0, 0, 256, 80);
  // name
  g.font = "bold 26px ui-sans-serif, sans-serif"; g.textAlign = "center";
  g.fillStyle = "rgba(0,0,0,0.6)"; g.fillText(alive ? name : name + " — OUT", 129, 31);
  g.fillStyle = "#" + col.toString(16).padStart(6, "0"); g.fillText(alive ? name : name + " — OUT", 128, 30);
  // hp bar
  const bw = 200, bx = 28, by = 48, bh = 16;
  g.fillStyle = "rgba(0,0,0,0.65)"; g.fillRect(bx - 2, by - 2, bw + 4, bh + 4);
  g.fillStyle = frac > 0.5 ? "#6bcb77" : frac > 0.25 ? "#ffd93d" : "#ff6b6b";
  g.fillRect(bx, by, bw * Math.max(0, frac), bh);
  g.strokeStyle = "rgba(255,255,255,0.25)"; g.lineWidth = 1; g.strokeRect(bx, by, bw, bh);
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
  mesh.scale.setScalar(rf.team === "house" ? 1.6 : 1.35);
  const label = makeLabel(); label.position.y = r * 3.1; label.scale.set(124, 38, 1);
  // team ground-glow ring
  const ring = new T.Mesh(new T.RingGeometry(r * 1.2, r * 1.7, 36),
    new T.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.55, blending: T.AdditiveBlending, side: T.DoubleSide, depthWrite: false }));
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
const BASE_BG = BG_COL;
function applyWeather(kind) {
  // Scene fog is gone for good; weather only drives the badge now. "fog" can
  // still arrive from old recordings and keeps its badge (range effect is
  // engine-side), but nothing hazes the view.
  const el = document.getElementById("weather");
  scene.background = new T.Color(BASE_BG);
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
const MAXP = 2400;
const pPos = new Float32Array(MAXP * 3), pCol = new Float32Array(MAXP * 3), pSize = new Float32Array(MAXP);
const parts = new Array(MAXP);
for (let i = 0; i < MAXP; i++) parts[i] = { life: 0, max: 1, vx: 0, vy: 0, vz: 0, x: 0, y: 0, z: 0, drag: 0.9, grav: 0 };
let pHead = 0;
const pGeo = new T.BufferGeometry();
pGeo.setAttribute("position", new T.BufferAttribute(pPos, 3));
pGeo.setAttribute("color", new T.BufferAttribute(pCol, 3));
pGeo.setAttribute("psize", new T.BufferAttribute(pSize, 1));
const pMat = new T.PointsMaterial({ size: 22, map: SPARK_TEX, vertexColors: true, transparent: true,
  depthWrite: false, blending: T.AdditiveBlending, sizeAttenuation: true });
const pPoints = new T.Points(pGeo, pMat); pPoints.frustumCulled = false; scene.add(pPoints);

function spawnParticle(x, y, z, vx, vy, vz, life, r, g, b, drag, grav) {
  const p = parts[pHead]; pHead = (pHead + 1) % MAXP;
  p.x = x; p.y = y; p.z = z; p.vx = vx; p.vy = vy; p.vz = vz;
  p.life = life; p.max = life; p.drag = drag; p.grav = grav;
  p.r = r; p.g = g; p.b = b;
}
function updateParticles(dt) {
  for (let i = 0; i < MAXP; i++) {
    const p = parts[i]; const o = i * 3;
    if (p.life > 0) {
      p.life -= dt;
      p.vy -= p.grav * dt;
      p.vx *= p.drag; p.vy *= p.drag; p.vz *= p.drag;
      p.x += p.vx * dt; p.y += p.vy * dt; p.z += p.vz * dt;
      const f = Math.max(0, p.life / p.max);
      pPos[o] = p.x; pPos[o + 1] = p.y; pPos[o + 2] = p.z;
      pCol[o] = p.r * f; pCol[o + 1] = p.g * f; pCol[o + 2] = p.b * f;
      pSize[o] = f;
    } else {
      pPos[o + 1] = -99999; pCol[o] = pCol[o + 1] = pCol[o + 2] = 0;
    }
  }
  pGeo.attributes.position.needsUpdate = true;
  pGeo.attributes.color.needsUpdate = true;
}

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
  const s = big ? 116 : mid ? 90 : 66;
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
  for (let i = 0; i < 9; i++) {
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
  scorches.push({ mesh: m, life: 6, max: 6 });
  while (scorches.length > 22) { const s = scorches.shift(); scene.remove(s.mesh); s.mesh.geometry.dispose(); s.mesh.material.dispose(); }
}
let fovPunch = 0;          // transient FOV kick on blasts
let introT = 0;            // cinematic intro sweep timer
let slowmoT = 0, slowmoFired = false;  // slow-mo on the deciding blow
const lastHp = {};         // id -> hp at last tick (hit-flash detection)
let endFired = false;      // one-shot guard so RW.onMatchEnd fires once per match
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
function explode(x, z, blastR) {
  const wx = x - AW / 2, wz = z - AH / 2;
  // expanding shell
  const shell = new T.Mesh(new T.SphereGeometry(1, 18, 14),
    new T.MeshBasicMaterial({ color: 0xffdd88, transparent: true, opacity: 0.85, blending: T.AdditiveBlending, depthWrite: false }));
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
    new T.MeshBasicMaterial({ color: 0xffd488, transparent: true, opacity: 0.8, side: T.DoubleSide, blending: T.AdditiveBlending, depthWrite: false }));
  sw.rotation.x = -Math.PI / 2; sw.position.set(wx, 3, wz); scene.add(sw);
  shockwaves.push({ mesh: sw, life: 0.55, max: 0.55, target: blastR * 2.2 });
  flash(wx, blastR * 0.6, wz, 0xffbb55, Math.min(6, 2 + blastR / 30));
  shake(Math.min(26, blastR * 0.22));
  fovPunch = Math.min(6, fovPunch + blastR * 0.05);
  scorchDecal(wx, wz, blastR * 0.7);
  if (blastR >= 70) screenFlash(0.5);
}
const flashEl = document.getElementById("flash");
function screenFlash(a) {
  if (!flashEl) return;
  flashEl.style.transition = "none"; flashEl.style.opacity = a;
  requestAnimationFrame(() => { flashEl.style.transition = "opacity .3s"; flashEl.style.opacity = 0; });
}

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
function beam(ax, az, bx, bz, col, gun, miss) {
  const a = new T.Vector3(ax - AW / 2, 24, az - AH / 2);
  const c = new T.Vector3(bx - AW / 2, 24, bz - AH / 2);
  if (miss) {
    // the shot sails PAST — a dimmer tracer with no impact FX. The crowd groans.
    beamOne(a, c, col, gun === "cannon" ? 3.2 : 1.1, 0.12);
    return;
  }
  if (gun === "cannon") {
    beamOne(a, c, 0xfff2dd, 4.5, 0.22);           // white-hot slug trace
    for (let i = 0; i < 22; i++)                   // heavy impact burst
      spawnParticle(c.x, c.y, c.z, (Math.random() - 0.5) * 260, Math.random() * 200, (Math.random() - 0.5) * 260,
        0.35, 1, 0.8, 0.4, 0.85, 260);
    flash(c.x, 40, c.z, 0xffcc88, 2.5);
    shake(6);
  } else if (gun === "shotgun") {
    // fan: centre beam on the target + two spread pellets from the shooter
    const dir = c.clone().sub(a), len = dir.length();
    beamOne(a, c, col, 1.2, 0.14);
    [-0.22, 0.22].forEach((off) => {
      const spread = dir.clone().applyAxisAngle(new T.Vector3(0, 1, 0), off).multiplyScalar(0.82);
      beamOne(a, a.clone().add(spread), col, 1.2, 0.14);
    });
    for (let i = 0; i < 12; i++)
      spawnParticle(c.x, c.y, c.z, (Math.random() - 0.5) * 200, Math.random() * 130, (Math.random() - 0.5) * 200,
        0.22, 1, 0.85, 0.45, 0.85, 210);
  } else {   // laser
    beamOne(a, c, col, 1.6, 0.16);
    for (let i = 0; i < 10; i++)
      spawnParticle(c.x, c.y, c.z, (Math.random() - 0.5) * 160, Math.random() * 120, (Math.random() - 0.5) * 160,
        0.25, 1, 0.9, 0.5, 0.85, 200);
  }
}

// ----- camera: auto-follow + screen shake + manual orbit override -----------
const BASE_FOV = 52, INTRO_MAX = 2.8;
const cam = {
  tgt: new T.Vector3(0, 0, 0), pos: new T.Vector3(0, 1400, 1400),
  desiredTgt: new T.Vector3(), desiredPos: new T.Vector3(),
  shakeAmt: 0, auto: true, az: 0.7, el: 0.62, distMul: 1.0, dragging: false, px: 0, py: 0,
  focus: null,
};
function shake(a) { cam.shakeAmt = Math.min(40, cam.shakeAmt + a); }
function updateCamera(dt, alivePts) {
  // frame the action: centroid + spread of alive robots
  let cx = 0, cz = 0, n = 0, spread = 300;
  alivePts.forEach((p) => { cx += p.x; cz += p.z; n++; });
  if (n) {
    cx /= n; cz /= n; let m = 0;
    alivePts.forEach((p) => { m = Math.max(m, Math.hypot(p.x - cx, p.z - cz)); });
    spread = Math.max(300, m + 220);
  }
  // kill-cam: bias toward an elimination + punch in
  let focusZoom = 1;
  if (cam.focus && cam.focus.life > 0) {
    cam.focus.life -= dt;
    const ff = cam.focus.life / cam.focus.max;       // 1 -> 0
    cx += (cam.focus.x - cx) * 0.55 * ff; cz += (cam.focus.z - cz) * 0.55 * ff;
    focusZoom = 1 - 0.4 * ff;
  }
  // cinematic intro sweep (az offset + pull-back, eases out)
  let azOff = 0, introZoom = 1, kBoost = 0;
  if (introT > 0) {
    introT = Math.max(0, introT - dt);
    const e = introT / INTRO_MAX;                    // 1 -> 0
    azOff = e * e * 2.2; introZoom = 1 + e * 1.3; kBoost = 0.02 * e;
  }
  cam.desiredTgt.set(cx, 40, cz);
  const dist = Math.min(2600, spread * 2.1) * cam.distMul * focusZoom * introZoom;
  const az = cam.az + azOff;
  const hor = Math.cos(cam.el) * dist;
  cam.desiredPos.set(cx + Math.cos(az) * hor, Math.sin(cam.el) * dist + 60, cz + Math.sin(az) * hor);
  const k = 1 - Math.pow(0.0025 + kBoost, dt);   // smooth, framerate-independent
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
let entrance = null;   // { t } while the walk-on is running
const ENTR_STAG = 1.5, ENTR_DUR = 2.6, ENTR_CAP = 8.0;
function startEntrance() {
  entrance = { t: -0.3 };
  Object.values(robotObjs).forEach((o) => { o.announced = false; });
  play(false);
}
function updateEntrance(dt) {
  if (playing) { entrance = null; return; }          // pressing ▶ skips the show
  entrance.t += dt;
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
    o.group.position.set(g[0] + (hx - g[0]) * e, 0, g[1] + (hz - g[1]) * e);
    o.mesh.rotation.y = -Math.atan2(hz - g[1], hx - g[0]);
    if (pg > 0) {
      o.wheelRot = (o.wheelRot || 0) - dt * 8;       // wheels rolling on the way in
      (o.mesh.userData.wheels || []).forEach((w) => { w.rotation.x = o.wheelRot; });
      if (!o.announced) {
        o.announced = true;
        // a compressed walk-on would machine-gun the name cards — keep the
        // ticker roll but save the stinger for fields with room to breathe
        if (stag >= 0.5) stinger(teamMode ? botLabel(rf) : rf.name);
        ticker(`🎙 ${rf.name} enters the arena`);
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
    const flipped = rf.alive && (rf.flip | 0) > 0, jammed = rf.alive && (rf.jam | 0) > 0;
    const venting = rf.alive && (rf.vent | 0) > 0;
    let nm = teamMode ? botLabel(rf) : rf.name;
    if (flipped) nm += " 🙃"; else if (jammed) nm += " ⚙"; else if (venting) nm += " ♨";
    if ((rf.od | 0) > 0) nm += " ⚡"; if ((rf.sh | 0) > 0) nm += " 🛡"; if ((rf.hs | 0) > 0) nm += " 💨";
    drawLabel(o.label, nm, frac_hp, rf.alive, o.col);
    if (rf.alive) {
      o.group.visible = true; alivePts.push({ x: wx, z: wz });
      if (o.dead) { o.dead = false; o.mesh.rotation.z = 0; o.mesh.position.y = 0; }
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
      o.ring.material.opacity = 0.4 + 0.2 * Math.sin(performance.now() * 0.006 + rf.id);
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
        o.dead = true; o.mesh.rotation.z = 0.5; o.mesh.position.y = -6;
        for (let k = 0; k < 14; k++)
          spawnParticle(wx, 30, wz, (Math.random() - 0.5) * 40, 30 + Math.random() * 40, (Math.random() - 0.5) * 40,
            0.9, 0.25, 0.25, 0.28, 0.9, 60);
        spawnDebris(wx, wz, o.col, o.r);
        explode(wx + AW / 2, wz + AH / 2, 60);   // death blast (arena coords)
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
      bm.emissiveIntensity = 0.12 + o.flash;
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
  hud.textContent = `tick ${cur.tick}   alive ${s.alive}`;
  updateScoreboard(cur.robots);
  updateTimer(s.time_left != null ? s.time_left : 0);
  return alivePts;
}

// per-tick discrete events (explosions, lasers, deaths, intro/outro voice)
function processTickEvents(tick) {
  if (tick === lastTickSeen) return;
  // advance through any skipped ticks too
  for (let tk = lastTickSeen + 1; tk <= tick; tk++) {
    const fr = frames[tk]; if (!fr) continue;
    if (!saidGo) { saidGo = true; playClip("fight", "Fight!", { flush: true, rate: 1.1, pitch: 1.1 }); stinger("FIGHT!"); ticker("⚔ Battle commences"); }
    const pos = {}; fr.robots.forEach((r) => pos[r.id] = r);
    (fr.explosions || []).forEach((ex) => explode(ex.x, ex.y, ex.r));
    (fr.events || []).forEach((ev) => {
      if (ev.kind === "flip" && names[ev.id] != null) ticker(`🙃 ${names[ev.id]} FLIPPED!`);
      else if (ev.kind === "jam" && names[ev.id] != null) ticker(`⚙ ${names[ev.id]} — GUN JAMMED`);
      else if (ev.kind === "vent" && names[ev.id] != null) {
        ticker(`♨ ${names[ev.id]} — OVERHEATED, venting`);
        playClip(["overheat"], `${names[ev.id]} is overheating!`);
      }
      else if (ev.kind === "pickup" && ev.type === "overdrive") ticker("⚡ OVERDRIVE claimed");
      else if (ev.kind === "pickup" && ev.type === "shield") ticker("🛡 SHIELD up");
      else if (ev.kind === "pickup" && ev.type === "haste") ticker("💨 HASTE grabbed");
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
        playClip(["flipper", "big_hit"], `${names[ev.id] || "Someone"} takes flight!`);
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
      if (lastHp[r.id] != null && r.hp < lastHp[r.id] && r.alive && o) {
        o.flash = 0.7;
        damageNumber(r.x - AW / 2, r.y - AH / 2, o.r * 2.6, Math.round(lastHp[r.id] - r.hp));
        for (let i = 0; i < 8; i++)
          spawnParticle(r.x - AW / 2, o.r, r.y - AH / 2, (Math.random() - 0.5) * 120, Math.random() * 90, (Math.random() - 0.5) * 120, 0.3, 1, 0.95, 0.6, 0.85, 150);
      } else if (lastHp[r.id] != null && r.hp > lastHp[r.id] + 2 && r.alive && o) {
        damageNumber(r.x - AW / 2, r.y - AH / 2, o.r * 2.6, Math.round(r.hp - lastHp[r.id]), true);
      }
      lastHp[r.id] = r.hp;
      if (alivePrev[r.id] && !r.alive) {
        playClip(["elim_" + slug(r.name), "eliminated"], `${r.name} is out!`);
        killFeed(r.name); ticker(`☠ ${r.name} eliminated`);
        cam.focus = { x: r.x - AW / 2, z: r.y - AH / 2, life: 0.7, max: 0.7 };
      }
      alivePrev[r.id] = r.alive;
    });
    // slow-mo + letterbox on the deciding blow
    const aliveN = fr.status ? fr.status.alive : fr.robots.filter((r) => r.alive).length;
    if (aliveN <= 1 && !slowmoFired && tk > 2) {
      slowmoFired = true; slowmoT = 2.4; setLetterbox(true); ticker("⚡ THE FINAL BLOW");
      playClip("final_blow", "Finish him!", { flush: true });
    }
  }
  lastTickSeen = tick;
}

// scrolling kill feed (DOM)
const feedEl = document.getElementById("killfeed");
function killFeed(name) {
  if (!feedEl) return;
  const d = document.createElement("div");
  d.className = "kf"; d.textContent = "☠  " + name + " ELIMINATED";
  feedEl.appendChild(d);
  setTimeout(() => { d.classList.add("fade"); }, 50);
  setTimeout(() => { d.remove(); }, 4200);
  while (feedEl.children.length > 5) feedEl.removeChild(feedEl.firstChild);
}

// ----- team grouping --------------------------------------------------------
// A robot carries r.team (a label). When some team has >1 member we're in a team
// match and the scoreboard groups + colours by side; otherwise it's a free-for-all
// and rows stay flat (unchanged). teamOf() falls back to the robot id for soloists.
const teamOf = (r) => (r.team != null ? r.team : "solo:" + r.id);
function teamInfo(robots) {
  const order = [], by = {};
  robots.forEach((r) => { const t = teamOf(r); if (!(t in by)) { by[t] = []; order.push(t); } by[t].push(r); });
  return { order, by, hasTeams: order.length < robots.length };
}
let teamMode = false, teamNames = [];
// Short per-bot label: drop the "Team:" prefix the bracket prepends, since the
// team header already names the side.
const botLabel = (r) => { const i = String(r.name).indexOf(":"); return i >= 0 ? r.name.slice(i + 1) : r.name; };

// live scoreboard (DOM; rows created once, updated each frame, CSS-order reflows)
const sbEl = document.getElementById("scoreboard");
let sbRows = {}, sbTeamHdr = {};
function buildScoreboard(robots) {
  if (!sbEl) return;
  robots = robots.filter((r) => r.team !== "house");   // the Gatekeeper isn't a contestant
  sbEl.innerHTML = ""; sbRows = {}; sbTeamHdr = {};
  const ti = teamInfo(robots);
  teamMode = ti.hasTeams; teamNames = ti.order;
  sbEl.classList.toggle("teams", teamMode);
  const mkRow = (r, useShortName) => {
    const col = "#" + colorFor(r).toString(16).padStart(6, "0");
    const row = document.createElement("div"); row.className = "sb-row";
    row.innerHTML = `<span class="sb-chip" style="background:${col}"></span>` +
      `<span class="sb-name"></span><span class="sb-bar"><i></i></span>` +
      `<span class="sb-ammo"></span><span class="sb-dmg" title="damage dealt"></span>`;
    sbEl.appendChild(row);
    const e = { row, name: row.querySelector(".sb-name"), fill: row.querySelector(".sb-bar i"),
      ammo: row.querySelector(".sb-ammo"), dmg: row.querySelector(".sb-dmg") };
    e.name.textContent = useShortName ? botLabel(r) : r.name;
    sbRows[r.id] = e;
  };
  if (teamMode) {
    ti.order.forEach((t) => {
      const members = ti.by[t];
      const col = "#" + colorFor(members[0]).toString(16).padStart(6, "0");
      const hdr = document.createElement("div"); hdr.className = "sb-team";
      hdr.style.setProperty("--tc", col);
      hdr.innerHTML = `<span class="sb-tdot" style="background:${col}"></span>` +
        `<span class="sb-tname">${t}</span><span class="sb-tcount"></span>`;
      sbEl.appendChild(hdr);
      sbTeamHdr[t] = { el: hdr, count: hdr.querySelector(".sb-tcount"), total: members.length };
      members.forEach((r) => mkRow(r, true));
    });
  } else {
    robots.forEach((r) => mkRow(r, false));
  }
}
function updateScoreboard(robots) {
  if (!sbEl) return;
  if (!teamMode) {
    const order = robots.slice().sort((a, b) => (b.alive - a.alive) || (b.hp - a.hp));
    order.forEach((r, i) => { const e = sbRows[r.id]; if (e) e.row.style.order = i; });
  }
  robots.forEach((r) => {
    const e = sbRows[r.id]; if (!e) return;
    const frac = Math.max(0, r.hp / r.max_hp);
    e.fill.style.width = (frac * 100) + "%";
    e.fill.style.background = frac > 0.5 ? "#6bcb77" : frac > 0.25 ? "#ffd93d" : "#ff6b6b";
    e.ammo.textContent = `🚀${r.rkt} ◆${r.trp}`;
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

// ---- broadcast overlay: ticker, stinger, title card, round timer ----------
const tickerEl = document.getElementById("ticker");
function ticker(msg) {
  if (!tickerEl) return;
  const d = document.createElement("span"); d.className = "tk"; d.textContent = msg;
  tickerEl.appendChild(d);
  setTimeout(() => d.classList.add("show"), 30);
  setTimeout(() => d.classList.remove("show"), 5000);
  setTimeout(() => d.remove(), 5500);
  while (tickerEl.children.length > 4) tickerEl.removeChild(tickerEl.firstChild);
}
const stingerEl = document.getElementById("stinger");
function stinger(text) {
  if (!stingerEl) return;
  stingerEl.textContent = text; stingerEl.classList.remove("go");
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
  if (!timerEl) return;
  const s = Math.max(0, Math.round(timeLeft / 10));   // ticks -> ~seconds for a broadcast clock
  timerEl.textContent = `⏱ ${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
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
  // scorch decals (slow fade)
  for (let i = scorches.length - 1; i >= 0; i--) {
    const s = scorches[i]; s.life -= dt;
    if (s.life <= 0) { scene.remove(s.mesh); s.mesh.geometry.dispose(); s.mesh.material.dispose(); scorches.splice(i, 1); continue; }
    s.mesh.material.opacity = 0.55 * Math.min(1, s.life / 1.5);
  }
  // robot hit-flash decay
  for (const id in robotObjs) { const o = robotObjs[id]; if (o.flash > 0) o.flash = Math.max(0, o.flash - dt * 3.5); }
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
  let ok = false, sceneRT, brightRT, blurA, blurB, postScene, postCam, quad, matBright, matBlur, matComp;
  const HALF = 2;
  const dim = new T.Vector2();
  const size = () => { renderer.getDrawingBufferSize(dim); return dim; };
  function makeRT(w, h, srgb) {
    const rt = new T.WebGLRenderTarget(Math.max(1, w), Math.max(1, h),
      { minFilter: T.LinearFilter, magFilter: T.LinearFilter, type: T.UnsignedByteType });
    rt.texture.colorSpace = srgb ? T.SRGBColorSpace : T.NoColorSpace;
    rt.texture.generateMipmaps = false;
    return rt;
  }
  try {
    const d = size();
    const bw = Math.max(1, Math.floor(d.x / HALF)), bh = Math.max(1, Math.floor(d.y / HALF));
    sceneRT = makeRT(d.x, d.y, true);            // full-res scene (sRGB-encoded, matches screen)
    brightRT = makeRT(bw, bh, false); blurA = makeRT(bw, bh, false); blurB = makeRT(bw, bh, false);
    postScene = new T.Scene(); postCam = new T.OrthographicCamera(-1, 1, 1, -1, 0, 1);
    quad = new T.Mesh(new T.PlaneGeometry(2, 2)); postScene.add(quad);
    matBright = new T.ShaderMaterial({ vertexShader: POST_VERT, fragmentShader: BRIGHT_FRAG,
      depthTest: false, depthWrite: false, uniforms: { tDiffuse: { value: null }, uThresh: { value: 0.85 }, uKnee: { value: 0.12 } } });
    matBlur = new T.ShaderMaterial({ vertexShader: POST_VERT, fragmentShader: BLUR_FRAG,
      depthTest: false, depthWrite: false, uniforms: { tDiffuse: { value: null }, uDir: { value: new T.Vector2() }, uTexel: { value: new T.Vector2() } } });
    matComp = new T.ShaderMaterial({ vertexShader: POST_VERT, fragmentShader: COMP_FRAG,
      depthTest: false, depthWrite: false, uniforms: { tScene: { value: null }, tBloom: { value: null }, uStrength: { value: 0.7 } } });
    ok = true;
  } catch (e) { console.warn("bloom init failed -> direct render", e); ok = false; }
  return {
    get ok() { return ok; },
    resize() {
      if (!ok) return;
      try {
        const d = size(), bw = Math.max(1, Math.floor(d.x / HALF)), bh = Math.max(1, Math.floor(d.y / HALF));
        sceneRT.setSize(d.x, d.y); brightRT.setSize(bw, bh); blurA.setSize(bw, bh); blurB.setSize(bw, bh);
      } catch (e) { ok = false; }
    },
    render() {
      if (!ok) { renderer.setRenderTarget(null); renderer.render(scene, camera); return; }
      try {
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
      } catch (e) { console.warn("bloom render failed -> direct render", e); ok = false; renderer.setRenderTarget(null); renderer.render(scene, camera); }
    },
  };
})();

// ----- main loop ------------------------------------------------------------
function loop(t) {
  raf = requestAnimationFrame(loop);
  const now = t || 0;
  let dt = lastT ? (now - lastT) / 1000 : 0.016; lastT = now;
  dt = Math.min(dt, 0.05);

  // slow-mo on the deciding blow
  if (slowmoT > 0) { slowmoT = Math.max(0, slowmoT - dt); if (slowmoT === 0) setLetterbox(false); }
  const speedMul = slowmoT > 0 ? 0.32 : 1;

  if (playing && frames.length) {
    simTime += fps * dt * speedMul;
    if (simTime >= frames.length - 1) {
      simTime = frames.length - 1; playing = false; setPlayLabel(); showWinner();
      if (!endFired) { endFired = true; if (window.RW && typeof window.RW.onMatchEnd === "function") window.RW.onMatchEnd(); }
    }
    processTickEvents(Math.floor(simTime));
  }
  let alivePts = [{ x: 0, z: 0 }];
  if (frames.length) alivePts = applyFrame(simTime);
  if (entrance && frames.length) updateEntrance(dt);   // pre-fight walk-on
  animateEnvironment(now);
  updateHazardFX(now * 0.001, dt);
  updateDmgFloats(dt);
  // advance the Blender robots' baked shoot clips
  for (const id in robotObjs) {
    const mx = robotObjs[id].mesh.userData.mixer;
    if (mx) mx.update(dt * speedMul);
  }
  updateFX(dt);
  updateCamera(dt, alivePts);
  bloom.render();
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
function playClip(keys, fallbackText, opts) {
  if (!voiceOn) return;
  const map = window.__VOICE_CLIPS__;
  if (map) {
    const k = (Array.isArray(keys) ? keys : [keys]).find((x) => map[x]);
    if (k) { try { const a = new Audio(map[k]); a.volume = 1; a.play().catch(() => {}); return; } catch (e) { /* fall through */ } }
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
  return { name: top.name, basis: `survived to tick ${death[top.id]}, ${top.dmg || 0} damage dealt` };
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
function showWinner() {
  const last = frames[frames.length - 1];
  const alive = last.robots.filter((r) => r.alive && r.team !== "house");
  let who, sub = `${frames.length} ticks`, say, keys, champ;
  if (teamMode) {
    const w = winningTeam();
    who = "TEAM " + w.team;
    sub = w.clean ? `${frames.length} ticks` : `${frames.length} ticks · on health`;
    say = `Team ${w.team} wins!`;
    keys = ["win_" + slug(w.team), "winner"];
    banner.querySelector(".who").textContent = "🏆 " + who;
    banner.querySelector(".sub").textContent = sub;
    banner.classList.add("show");
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
  } else {
    champ = alive.slice().sort((a, b) => b.hp - a.hp)[0]; who = champ.name + " (on HP)";
    say = `Time! ${champ.name} wins on health.`; keys = ["win_" + slug(champ.name), "winner"];
  }
  banner.querySelector(".who").textContent = "🏆 " + who;
  banner.querySelector(".sub").textContent = sub;
  banner.classList.add("show");
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
  for (let i = 0; i < MAXP; i++) parts[i].life = 0;
  [debris, scorches, shells, shockwaves].forEach((arr) => {
    arr.forEach((d) => { scene.remove(d.mesh); d.mesh.geometry.dispose(); d.mesh.material.dispose(); });
    arr.length = 0;
  });
  dmgFloats.forEach((f) => { scene.remove(f.sp); f.sp.material.map.dispose(); f.sp.material.dispose(); });
  dmgFloats.length = 0;
}
function loadText(txt) {
  const fr = txt.split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l));
  if (!fr.length) return;
  clearMatch();
  frames = fr;
  const s0 = frames[0].status || {}; AW = s0.w || 1280; AH = s0.h || 768;
  names = {}; alivePrev = {};
  frames[0].robots.forEach((r) => { names[r.id] = r.name; alivePrev[r.id] = true; });
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
  if (feedEl) feedEl.innerHTML = "";
  simTime = 0; lastTickSeen = -1; saidGo = false; saidWinner = false; introT = INTRO_MAX;
  slowmoT = 0; slowmoFired = false; endFired = false; setLetterbox(false);
  banner.classList.remove("show");
  const nBots = frames[0].robots.filter((r) => r.team !== "house").length;
  $("hint").textContent = (teamMode && teamNames.length === 2)
    ? `${frames.length} ticks · ${teamNames[0]} vs ${teamNames[1]}`
    : `${frames.length} ticks · ${nBots} bots`;
  if (frames[0].robots.some((r) => r.team === "house"))
    ticker("🚨 HOUSE ROBOT ON PATROL — Gatekeeper is live. Stay clear.");
  showTitleCard(frames[0].robots.filter((r) => r.team !== "house").length);
  startEntrance();   // robots drive in through the gates, then playback begins
}

// ----- controls -------------------------------------------------------------
function setPlayLabel() { playBtn.textContent = playing ? "⏸ Pause" : "▶ Play"; }
function play(on) { playing = on; setPlayLabel(); if (on) banner.classList.remove("show"); }
playBtn.onclick = () => { if (simTime >= frames.length - 1) restart(); else play(!playing); };
function restart() {
  simTime = 0; lastTickSeen = -1; saidGo = false; saidWinner = false; introT = INTRO_MAX;
  slowmoT = 0; slowmoFired = false; endFired = false; setLetterbox(false);
  sdAnnounced = false;                      // replay re-announces sudden death
  frames.length && frames[0].robots.forEach((r) => alivePrev[r.id] = true);
  for (const k in lastHp) delete lastHp[k];
  if (feedEl) feedEl.innerHTML = "";
  banner.classList.remove("show");
  Object.values(robotObjs).forEach((o) => { o.dead = false; o.mesh.rotation.z = 0; o.mesh.position.y = 0; o.flash = 0; });
  startEntrance();   // replay gets the walk-on too
}
$("restart").onclick = restart;
speed.oninput = (e) => fps = +e.target.value;
$("voice").onclick = (e) => {
  voiceOn = !voiceOn; const b = e.currentTarget;
  b.textContent = voiceOn ? "🔊 Voice" : "🔇 Voice"; b.setAttribute("aria-pressed", voiceOn);
  if (synth) { if (voiceOn) speak("Commentary on.", { flush: true }); else synth.cancel(); }
};
$("autocam").onclick = (e) => { cam.auto = !cam.auto; e.currentTarget.setAttribute("aria-pressed", cam.auto);
  e.currentTarget.textContent = cam.auto ? "🎥 Auto-cam" : "🎥 Manual"; };

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
});
cv.addEventListener("wheel", (e) => { e.preventDefault(); cam.distMul = Math.max(0.4, Math.min(3, cam.distMul * (1 + Math.sign(e.deltaY) * 0.1))); }, { passive: false });

// ----- resize ---------------------------------------------------------------
function resize() {
  const w = stage.clientWidth, h = stage.clientHeight;
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
  let ctx = null, master = null, timer = null, on = false, step = 0;   // default OFF — opt-in via the 🔇 button
  const ROOTS = [110.0, 87.31, 130.81, 98.0];   // A2 F2 C3 G3 — a calm minor loop
  const hz = (semis, base) => base * Math.pow(2, semis / 12);
  function pluck(freq, t, dur, gain, type) {
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = type || "triangle"; o.frequency.value = freq;
    o.connect(g); g.connect(master);
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(gain, t + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.start(t); o.stop(t + dur + 0.05);
  }
  function pad(root, t, dur) {
    [0, 7, 12].forEach((iv, i) => {          // root + fifth + octave
      const o = ctx.createOscillator(), g = ctx.createGain(), f = ctx.createBiquadFilter();
      o.type = "sawtooth"; o.frequency.value = hz(iv, root); o.detune.value = (i - 1) * 6;
      f.type = "lowpass"; f.frequency.value = 640;
      o.connect(f); f.connect(g); g.connect(master);
      g.gain.setValueAtTime(0, t);
      g.gain.linearRampToValueAtTime(0.05, t + 0.6);
      g.gain.linearRampToValueAtTime(0.04, t + dur - 0.6);
      g.gain.linearRampToValueAtTime(0.0001, t + dur);
      o.start(t); o.stop(t + dur + 0.05);
    });
  }
  function tick() {
    if (!ctx) return;
    const t = ctx.currentTime + 0.05, bar = 2.0, root = ROOTS[step % ROOTS.length];
    pad(root, t, bar);
    pluck(root / 2, t, 0.5, 0.09);                       // downbeat pulse
    pluck(root / 2, t + bar / 2, 0.4, 0.06);
    pluck(hz(12, root), t + bar * 0.75, 0.3, 0.03, "square");  // off-beat sparkle
    step++;
    timer = setTimeout(tick, bar * 1000);
  }
  function ensure() {
    if (ctx) return;
    const AC = window.AudioContext || window.webkitAudioContext; if (!AC) return;
    ctx = new AC(); master = ctx.createGain(); master.gain.value = on ? 0.16 : 0; master.connect(ctx.destination);
    tick();
  }
  return {
    kick() { ensure(); if (ctx && ctx.state === "suspended") ctx.resume(); },
    toggle() { on = !on; if (master) master.gain.linearRampToValueAtTime(on ? 0.16 : 0, ctx.currentTime + 0.2); return on; },
    get on() { return on; },
  };
})();
const musicBtn = document.getElementById("music");
if (musicBtn) musicBtn.onclick = () => {
  const state = music.toggle();
  musicBtn.setAttribute("aria-pressed", state);
  musicBtn.textContent = state ? "🎵 Music" : "🔇 Music";
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
  debug: { scene, camera, renderer, robotObjs, get arenaGroup() { return arenaGroup; },
           seek: (t) => { simTime = Math.max(0, Math.min(t, frames.length - 1)); } },
};

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
