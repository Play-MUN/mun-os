// The reef template's bake: renders the sea (sea.js) into what the
// console's player draws, and measures where everything goes.
//
// Runs in a browser page (bake.html), driven by bake.mjs. Every render is a
// whole 640 x 360 frame through the console's pixel look (tone mapped, 15
// bits a channel with a 4 x 4 ordered dither), so the vertices snap to the
// same grid in every image; pieces are cropped from those frames. Images
// come back as raw RGBA (straight alpha); bake.mjs writes them as PNG.
//
// What is made (bake() returns {images, layout}):
// - planes: the still sea in four slabs of depth, each a little wider than
//   the screen so the viewpoint's drift never shows an edge; the floor
//   fades out where each slab ends, over the farther one;
// - the light on the sand: the caustics alone, per slab, a loop of frames;
// - sheets: each school's fish (16 headings, the tail's beat, three tints),
//   the shark (16 headings, its body's sway), the crabs, the jellyfish
//   (their pulse), the kelp (each strand straight; the player bends it),
//   the rays, a bubble and a speck of snow;
// - the poster (everything at rest) and the game's cover (the card's
//   window);
// - layout: the camera and depth model, every place, path and size in the
//   canvas's pixels (1920 x 1080) and depths (0 near, 1 far), for
//   make_package.py.

import * as THREE from 'three'
import { buildSea, seaAt, floorY } from './sea.js'

const W = 640
const H = 360
const K = 3                       // canvas pixels per look pixel
const START = 40                  // the scene's clock when the console starts it
// The depth model: z 0 at NEAR, 1 at FAR (distance along the view);
// perspective scales by NEAR / distance.
const NEAR = 6
const FAR = 24
// Slabs of depth for the planes (distances along the view).
const SLABS = [
  { name: 'near', from: 0, to: 9 },
  { name: 'mid', from: 9, to: 13 },
  { name: 'far', from: 13, to: 17.5 },
  { name: 'back', from: 17.5, to: 1000 },
]
// How far the planes reach past the screen (look pixels), for the drift.
const MARGIN = [25, 8]
const HEADINGS = 16

const sea = buildSea()
const { scene, camera } = sea
const canvas = document.createElement('canvas')
const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, alpha: true, preserveDrawingBuffer: true })
renderer.setPixelRatio(1)
renderer.setSize(W, H, false)
renderer.toneMapping = THREE.ACESFilmicToneMapping
renderer.outputColorSpace = THREE.SRGBColorSpace
const target = new THREE.WebGLRenderTarget(W, H, { type: THREE.HalfFloatType, minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter })

// The pixel look. Transparency is dithered on or off per pixel, or, with
// uLevels, kept in so many steps (light and glass, whose dots would show).
// uLight: light alone on black (the rays), its strength becoming its
// opacity.
const passMaterial = new THREE.ShaderMaterial({
  uniforms: { tScene: { value: target.texture }, uOpaque: { value: 0 }, uLevels: { value: 0 }, uLight: { value: 0 } },
  depthTest: false,
  depthWrite: false,
  vertexShader: 'varying vec2 vUv; void main() { vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }',
  fragmentShader: `
    uniform sampler2D tScene; uniform float uOpaque; uniform float uLevels; uniform float uLight; varying vec2 vUv;
    // the ordered dither's thresholds: the 4 x 4 Bayer matrix (examples/shape/generate.py's BAYER), by the pixel's place
    const float BAYER[16] = float[16](0.0, 8.0, 2.0, 10.0, 12.0, 4.0, 14.0, 6.0, 3.0, 11.0, 1.0, 9.0, 15.0, 7.0, 13.0, 5.0);
    float bayer4(vec2 a) { ivec2 i = ivec2(mod(floor(a), 4.0)); return BAYER[i.y * 4 + i.x] / 16.0; }
    void main() {
      vec4 c = texture2D(tScene, vUv);
      float a = uOpaque > 0.5 ? 1.0 : clamp(c.a, 0.0, 1.0);
      vec3 rgb = uOpaque > 0.5 || uLight > 0.5 ? c.rgb : c.rgb / max(a, 0.0001);
      gl_FragColor = vec4(rgb, 1.0);
      #include <tonemapping_fragment>
      #include <colorspace_fragment>
      if (uLight > 0.5) {
        float m = max(max(gl_FragColor.r, gl_FragColor.g), gl_FragColor.b);
        a = m;
        gl_FragColor.rgb /= max(m, 0.0001);
      }
      float d = bayer4(gl_FragCoord.xy);
      gl_FragColor.rgb = floor(gl_FragColor.rgb * 31.0 + d) / 31.0;
      float on = uLevels > 0.5 ? floor(a * uLevels + 0.5) / uLevels : (a > d + 0.02 ? 1.0 : 0.0);
      gl_FragColor = vec4(gl_FragColor.rgb * on, on);
    }`,
})
const passScene = new THREE.Scene()
passScene.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), passMaterial))
const passCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1)

// The floor shows only a band of distance in a slab's render (x to y, its
// start softened by w, fading out over z after its end). Each slab's floor
// ends sharp where the next begins (a soft or dithered edge would show as a
// seam of dots over the floor beyond) and starts a little before its own
// slab, under the nearer one, so that the view's drift, moving slabs apart,
// never opens a gap between them; planes and light alike.
const band = { value: new THREE.Vector4(-1000, 1000, 1, 0.0001) }
sea.floor.material.onBeforeCompile = (shader) => {
  shader.uniforms.uBand = band
  shader.fragmentShader = 'uniform vec4 uBand;\n' + shader.fragmentShader.replace(
    '#include <dithering_fragment>',
    `#include <dithering_fragment>
     float dist = vViewPosition.z;
     gl_FragColor.a *= smoothstep(uBand.x - uBand.w, uBand.x + uBand.w, dist) * (1.0 - smoothstep(uBand.y, uBand.y + uBand.z, dist));`,
  )
}

// ------------------------------------------------------------ helpers

const f = 540 / Math.tan(THREE.MathUtils.degToRad(23))     // canvas pixels per unit at distance 1
camera.updateMatrixWorld()
const distance = (p) => -p.clone().applyMatrix4(camera.matrixWorldInverse).z
const depthOf = (p) => Math.min(1, Math.max(0, (distance(p) - NEAR) / (FAR - NEAR)))
/** A point on the canvas (1920 x 1080 pixels) and its depth. */
function project(p) {
  const v = p.clone().project(camera)
  return [((v.x + 1) / 2) * 1920, ((1 - v.y) / 2) * 1080, depthOf(p)]
}
const round = (v, n = 1) => Math.round(v * 10 ** n) / 10 ** n

const drawables = scene.children.filter((o) => !o.isLight)
function show(list) {
  for (const o of drawables) o.visible = list.includes(o)
}

/** One frame through the pixel look; offset in look pixels. */
function shot({ opaque = false, levels = 0, light = 0, offset = [0, 0] } = {}) {
  camera.setViewOffset(1920, 1080, offset[0] * K, offset[1] * K, 1920, 1080)
  renderer.setRenderTarget(target)
  renderer.setClearColor(0x000000, 0)
  renderer.clear()
  renderer.render(scene, camera)
  renderer.setRenderTarget(null)
  renderer.setClearColor(0x000000, 0)
  renderer.clear()
  passMaterial.uniforms.uOpaque.value = opaque ? 1 : 0
  passMaterial.uniforms.uLevels.value = levels
  passMaterial.uniforms.uLight.value = light
  renderer.render(passScene, passCamera)
  camera.clearViewOffset()
  const gl = renderer.getContext()
  const raw = new Uint8Array(W * H * 4)
  gl.readPixels(0, 0, W, H, gl.RGBA, gl.UNSIGNED_BYTE, raw)
  // rows bottom up, colour premultiplied: turned round and made straight
  const data = new Uint8ClampedArray(W * H * 4)
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const s = ((H - 1 - y) * W + x) * 4
      const d = (y * W + x) * 4
      const a = raw[s + 3]
      data[d + 3] = a
      if (a) for (let c = 0; c < 3; c++) data[d + c] = Math.min(255, Math.round((raw[s + c] * 255) / a))
    }
  }
  return { w: W, h: H, data }
}

function image(w, h) {
  return { w, h, data: new Uint8ClampedArray(w * h * 4) }
}
/** Copy the w x h part of `from` at (sx, sy) to (dx, dy) of `to` (clipped). */
function blit(from, sx, sy, w, h, to, dx, dy) {
  for (let y = 0; y < h; y++) {
    const fy = sy + y
    const ty = dy + y
    if (fy < 0 || fy >= from.h || ty < 0 || ty >= to.h) continue
    for (let x = 0; x < w; x++) {
      const fx = sx + x
      const tx = dx + x
      if (fx < 0 || fx >= from.w || tx < 0 || tx >= to.w) continue
      const s = (fy * from.w + fx) * 4
      const d = (ty * to.w + tx) * 4
      for (let c = 0; c < 4; c++) to.data[d + c] = from.data[s + c]
    }
  }
}
/** The box of a frame's pixels that are not see-through, or null. */
function extent(img) {
  let x0 = img.w
  let y0 = img.h
  let x1 = -1
  let y1 = -1
  for (let y = 0; y < img.h; y++)
    for (let x = 0; x < img.w; x++)
      if (img.data[(y * img.w + x) * 4 + 3]) {
        if (x < x0) x0 = x
        if (x > x1) x1 = x
        if (y < y0) y0 = y
        if (y > y1) y1 = y
      }
  return x1 < 0 ? null : { x0, y0, x1, y1 }
}
/** Frames of one thing, all centred on (cx, cy) (look pixels): the cell that holds them all. */
function cell(frames, cx, cy, pad = 1) {
  let hw = 1
  let hh = 1
  for (const img of frames) {
    const e = extent(img)
    if (!e) continue
    hw = Math.max(hw, cx - e.x0 + pad, e.x1 + 1 - cx + pad)
    hh = Math.max(hh, cy - e.y0 + pad, e.y1 + 1 - cy + pad)
  }
  return { w: 2 * hw, h: 2 * hh, x: cx - hw, y: cy - hh }
}
/** Frames in rows of `columns`, each its cell's crop. */
function sheet(frames, c, columns) {
  const rows = Math.ceil(frames.length / columns)
  const out = image(c.w * columns, c.h * rows)
  frames.forEach((img, i) => blit(img, c.x, c.y, c.w, c.h, out, (i % columns) * c.w, Math.floor(i / columns) * c.h))
  return out
}
/** A frame taken whole at the position of a look-pixel window (for planes wider than the screen). */
function wide(render) {
  const [mx, my] = MARGIN
  const out = image(W + 2 * mx, H + 2 * my)
  for (const [ox, oy] of [[-mx, -my], [mx, -my], [-mx, my], [mx, my]]) {
    const img = render([ox, oy])
    blit(img, 0, 0, W, H, out, ox + mx, oy + my)
  }
  return out
}

// ------------------------------------------------------------ the bake

export async function bake() {
  // the textures loaded from files must have arrived
  await new Promise((resolve) => {
    const wait = () => (sea.caustics.image && sea.bubbleTexture.image ? resolve() : setTimeout(wait, 50))
    wait()
  })
  const images = {}
  const layout = {
    canvas: [1920, 1080], look: [W, H], start: START,
    depth: { near: NEAR, far: FAR, share: round(NEAR / FAR, 4), span: round(((FAR - NEAR) * f) / NEAR, 0) },
    disc: null, planes: [], actors: {}, paths: {}, emitters: {},
  }
  seaAt(sea, START)
  const discAt = project(sea.disc)
  layout.disc = { at: [round(discAt[0]), round(discAt[1])], z: round(discAt[2], 3), radius: round((1.25 * f) / distance(sea.disc)) }

  // ---- the viewpoint's drift (the camera at rest moves 0.72 across and
  // 0.21 up and down over about a minute, keeping its eye on the look point)
  const pivot = depthOf(sea.look)
  const swayX = 0.72 * f * (1 / NEAR - 1 / distance(sea.look))
  const swayY = 0.21 * f * (1 / NEAR - 1 / distance(sea.look))
  layout.sway = { x: round(swayX), y: round(swayY), period: [round(2 * Math.PI * 9), round(2 * Math.PI * 11)], pivot: round(pivot, 3) }

  // ---- planes: the still sea in slabs of depth
  const standing = sea.standing.map((o) => ({ o, d: distance(o.position) }))
  const emissive = sea.floor.material.emissiveIntensity
  sea.floor.material.emissiveIntensity = 0          // the light on the sand is drawn apart
  for (const slab of SLABS) {
    const things = standing.filter((s) => s.d >= slab.from && s.d < slab.to).map((s) => s.o)
    show([sea.floor, ...things, ...(slab.name === 'back' ? [sea.dome] : [])])
    band.value.set(slab.from - 0.8, slab.to, 0.0001, 0.0001)
    const img = wide((offset) => shot({ opaque: slab.name === 'back', offset }))
    const name = `plane-${slab.name}`
    images[name] = img
    const mid = (Math.max(slab.from, NEAR) + Math.min(slab.to, 30)) / 2
    layout.planes.push({ name, z: round(Math.min(1, Math.max(0, (mid - NEAR) / (FAR - NEAR))), 3),
                         at: [-MARGIN[0] * K, -MARGIN[1] * K], size: [img.w, img.h] })
  }

  // ---- the light on the sand: the floor itself, lit by its caustics at
  // their brightest (0.35), per slab, hidden only by that slab's own
  // things; the plane beneath has the floor unlit, and the light's opacity
  // (0.71 to 1) breathes between 0.25 and 0.35 as the scene's does. Its
  // drift comes round in 17.857 s (3 of the texture's 14 repeats one way,
  // 2 the other).
  {
    const LOOP = 3 / (14 * 0.012)
    const N = 16
    const PEAK = 0.35
    const material = sea.floor.material
    material.emissiveIntensity = PEAK
    for (const slab of SLABS.filter((s) => s.name !== 'back')) {
      const own = standing.filter((s) => s.d >= slab.from && s.d < slab.to).map((s) => s.o)
      // they hide the floor by depth alone, so they are drawn before it
      const masks = []
      own.forEach((o) => o.traverse((m) => {
        if (m.material) masks.push(m.material)
        m.renderOrder = -1
      }))
      masks.forEach((m) => (m.colorWrite = false))
      show([sea.floor, ...own])
      band.value.set(slab.from - 0.8, slab.to, 0.0001, 0.0001)
      const frames = []
      for (let i = 0; i < N; i++) {
        const t = START + (i / N) * LOOP
        sea.caustics.offset.set((t * 0.012) % 1, (t * 0.008) % 1)
        frames.push(wide((offset) => shot({ offset })))
      }
      masks.forEach((m) => (m.colorWrite = true))
      own.forEach((o) => o.traverse((m) => (m.renderOrder = 0)))
      // cropped to the rows it lights
      const boxes = frames.map(extent).filter(Boolean)
      const y0 = Math.min(...boxes.map((b) => b.y0))
      const y1 = Math.max(...boxes.map((b) => b.y1)) + 1
      const fw = frames[0].w
      const fh = y1 - y0 + ((y1 - y0) % 2)
      const columns = Math.max(1, Math.floor(2048 / fw))
      const name = `light-${slab.name}`
      images[name] = sheet(frames, { x: 0, y: y0, w: fw, h: fh }, columns)
      const plane = layout.planes.find((p) => p.name === `plane-${slab.name}`)
      layout.actors[name] = {
        sheet: name, frame: [fw, fh], columns, z: plane.z,
        at: [round((fw / 2 - MARGIN[0]) * K), round((y0 + fh / 2 - MARGIN[1]) * K)],
        clips: [{ name: 'shimmer', first: 0, count: N, fps: round(N / LOOP, 3) }],
        // the floor's light breathes: 0.3 + 0.05 sin(1.3 t)
        pulse: { min: round(0.25 / PEAK, 3), max: 1, period: round((2 * Math.PI) / 1.3, 3) },
      }
    }
    sea.caustics.offset.set((START * 0.012) % 1, (START * 0.008) % 1)
    material.emissiveIntensity = 0
  }

  // ---- the schools: each fish rendered alone where its school swims,
  // facing each of 16 headings round (right as drawn, then turning away from
  // the viewer), its tail beating (8 frames), in three tints
  {
    const { fish, schools } = sea
    const phase = fish.geometry.getAttribute('phase')
    const tones = [0x9fc9d6, 0x5fc0c9, 0xf2b85c]
    const tints = [[-0.012, -0.05], [0, 0], [0.012, 0.05]]
    const BEAT = (2 * Math.PI) / 9
    const FRAMES = 8
    const right = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion).setY(0).normalize()
    const forward = new THREE.Vector3(1, 0, 0)
    const q = new THREE.Quaternion()
    const m = new THREE.Matrix4()
    const colour = new THREE.Color()
    const names = ['far', 'mid', 'near']
    for (const [s, school] of schools.entries()) {
      const home = new THREE.Vector3(0, school.height, school.depth)
      const away = home.clone().sub(camera.position).setY(0).normalize()
      fish.count = 1
      phase.setX(0, 0)
      phase.needsUpdate = true
      show([fish])
      const frames = []
      for (let v = 0; v < HEADINGS; v++) {
        const yaw = (v / HEADINGS) * Math.PI * 2
        const dir = right.clone().multiplyScalar(Math.cos(yaw)).addScaledVector(away, Math.sin(yaw)).normalize()
        q.setFromUnitVectors(forward, dir)
        m.compose(home, q, new THREE.Vector3().setScalar(school.size))
        fish.setMatrixAt(0, m)
        fish.instanceMatrix.needsUpdate = true
        for (const [dh, dl] of tints) {
          fish.setColorAt(0, colour.set(tones[s]).offsetHSL(dh, 0, dl))
          fish.instanceColor.needsUpdate = true
          for (let k = 0; k < FRAMES; k++) {
            sea.shared.time.value = START + (k / FRAMES) * BEAT
            frames.push(shot())
          }
        }
      }
      fish.count = sea.SCHOOLS.reduce((a, b) => a + b, 0)
      const [hx, hy, hz] = project(home)
      const c = cell(frames, Math.round(hx / K), Math.round(hy / K))
      const name = `fish-${names[s]}`
      images[name] = sheet(frames, c, FRAMES * tints.length)
      // the school's course, sampled evenly in time over one round (its rise
      // and fall made to come round with it: twice a round, for 1.7)
      const T = (2 * Math.PI) / school.speed
      const points = []
      const at = (t) => new THREE.Vector3(
        Math.sin(t * school.speed + school.offset) * school.radius,
        school.height + Math.sin(t * school.speed * 2 + school.offset) * 0.5,
        school.depth + Math.cos(t * school.speed + school.offset) * school.radius * 0.35,
      )
      for (let i = 0; i < 32; i++) {
        const [x, y, z] = project(at(START + (i / 32) * T))
        points.push([round(x), round(y), round(z, 4)])
      }
      const d = distance(home)
      const pxu = f / d                      // canvas pixels per unit at the school
      layout.paths[`${name}-round`] = { points, seconds: round(T, 2) }
      layout.actors[name] = {
        sheet: name, frame: [c.w, c.h], columns: FRAMES * tints.length, views: HEADINGS,
        clips: tints.map((_, i) => ({ name: `swim-${'abc'[i]}`, first: i * FRAMES, count: FRAMES, fps: round(FRAMES / BEAT, 3) })),
        at: points[0].slice(0, 2), z: round(hz, 4), path: `${name}-round`,
        places: school.members.map((p) => [round(p.x * pxu), round(-p.y * pxu), round(-p.z / (FAR - NEAR), 4)]),
        wobble: [round(0.15 * pxu), round((2 * Math.PI) / 1.3, 3)],
        avoid: { radius: round(5.5 * pxu), push: round(5.5 * 0.75 * pxu) },
      }
    }
    seaAt(sea, START)
  }

  // ---- the shark: 16 headings, its body's sway (12 frames), and its round
  {
    const { shark } = sea
    const SWAY = (2 * Math.PI) / 1.6
    const FRAMES = 12
    const home = new THREE.Vector3(0, 4.7, -8.5)
    const right = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion).setY(0).normalize()
    const away = home.clone().sub(camera.position).setY(0).normalize()
    const forward = new THREE.Vector3(1, 0, 0)
    show([shark])
    const frames = []
    for (let v = 0; v < HEADINGS; v++) {
      const yaw = (v / HEADINGS) * Math.PI * 2
      const dir = right.clone().multiplyScalar(Math.cos(yaw)).addScaledVector(away, Math.sin(yaw)).normalize()
      for (let k = 0; k < FRAMES; k++) {
        shark.position.copy(home)
        shark.quaternion.setFromUnitVectors(forward, dir)
        shark.rotateY(Math.sin((k / FRAMES) * Math.PI * 2) * 0.09)
        frames.push(shot())
      }
    }
    const [hx, hy, hz] = project(home)
    const c = cell(frames, Math.round(hx / K), Math.round(hy / K))
    images.shark = sheet(frames, c, FRAMES)
    const T = (2 * Math.PI) / 0.045
    const points = []
    for (let i = 0; i < 32; i++) {
      const t = START + (i / 32) * T
      // its rise and fall made to come round with its round (3 times, for 2.9)
      const p = new THREE.Vector3(Math.sin(t * 0.045) * 11, 4.7 + Math.sin(t * 0.135) * 0.4, -8.5 + Math.cos(t * 0.045) * 2.2)
      const [x, y, z] = project(p)
      points.push([round(x), round(y), round(z, 4)])
    }
    layout.paths['shark-round'] = { points, seconds: round(T, 2) }
    layout.actors.shark = { sheet: 'shark', frame: [c.w, c.h], columns: FRAMES, views: HEADINGS,
                            clips: [{ name: 'swim', first: 0, count: FRAMES, fps: round(FRAMES / SWAY, 3) }],
                            at: points[0].slice(0, 2), z: round(hz, 4), path: 'shark-round' }
    seaAt(sea, START)
  }

  // ---- the crabs: each where it stands, sideways a little way and back
  for (const [n, crab] of sea.crabs.entries()) {
    const placeAt = (step) => {
      const x = crab.x + Math.cos(crab.turn) * step * 0.9
      const z = crab.z - Math.sin(crab.turn) * step * 0.9
      return new THREE.Vector3(x, floorY(x, z) + 0.06, z)
    }
    crab.mesh.position.copy(placeAt(0))
    crab.mesh.rotation.z = 0
    show([crab.mesh])
    const img = shot()
    const [cx, cy, cz] = project(placeAt(0))
    const c = cell([img], Math.round(cx / K), Math.round(cy / K))
    const name = `crab-${n}`
    images[name] = sheet([img], c, 1)
    const a = project(placeAt(-1))
    const b = project(placeAt(1))
    // the scene's step is sin(0.45 t + phase): the player's sine curve
    // starts at its minimum, a quarter of a turn before
    const cycle = (((0.45 * START + crab.phase + Math.PI / 2) / (2 * Math.PI)) % 1 + 1) % 1
    layout.actors[name] = {
      sheet: name, frame: [c.w, c.h], at: [round(cx), round(cy)], z: round(cz, 4),
      walk: { x: [round(a[0] - cx), round(b[0] - cx)], y: [round(a[1] - cy), round(b[1] - cy)],
              period: round((2 * Math.PI) / 0.45, 3), phase: round(cycle, 4) },
      rock: { degrees: round(THREE.MathUtils.radToDeg(0.05), 2), period: round((2 * Math.PI) / 7, 3) },
    }
    seaAt(sea, START)
  }

  // ---- the jellyfish: each where it rests (the middle of its rise), its
  // pulse in 16 frames; its rise (3 units in 25 s) and its sway sideways
  {
    const FRAMES = 16
    const PULSE = (2 * Math.PI) / 1.6
    const all = []
    const homes = []
    for (const j of sea.jellies) {
      show([j.group])
      j.group.position.copy(j.base)
      const frames = []
      for (let k = 0; k < FRAMES; k++) {
        const pulse = 0.5 + 0.5 * Math.sin((k / FRAMES) * Math.PI * 2)
        j.group.scale.set(1 + pulse * 0.15, 1 - pulse * 0.12, 1 + pulse * 0.15)
        frames.push(shot({ levels: 8 }))
      }
      const [x, y, z] = project(j.base)
      all.push({ frames, x, y, z, d: distance(j.base) })
      homes.push([x, y, z])
    }
    // one cell for all, so they share a sheet
    let w = 2
    let h = 2
    for (const j of all) {
      const c = cell(j.frames, Math.round(j.x / K), Math.round(j.y / K))
      w = Math.max(w, c.w)
      h = Math.max(h, c.h)
    }
    const out = image(w * FRAMES, h * all.length)
    all.forEach((j, r) => j.frames.forEach((img, k) => blit(img, Math.round(j.x / K) - w / 2, Math.round(j.y / K) - h / 2, w, h, out, k * w, r * h)))
    images.jellies = out
    const z = all.reduce((a, j) => a + j.z, 0) / all.length
    const d = NEAR + z * (FAR - NEAR)
    const cx = all.reduce((a, j) => a + j.x, 0) / all.length
    const cy = all.reduce((a, j) => a + j.y, 0) / all.length
    layout.actors.jellies = {
      sheet: 'jellies', frame: [w, h], columns: FRAMES, at: [round(cx), round(cy)], z: round(z, 4),
      clips: all.map((_, r) => ({ name: `pulse-${r}`, first: r * FRAMES, count: FRAMES, fps: round(FRAMES / PULSE, 3) })),
      places: all.map((j) => [round(Math.round(j.x / K) * K - cx), round(Math.round(j.y / K) * K - cy), round(j.z - z, 4)]),
      rise: round((3 * f) / d), riseSeconds: 25, sway: round((0.4 * f) / d), swaySeconds: round((2 * Math.PI) / 0.2, 2),
    }
    seaAt(sea, START)
  }

  // ---- the kelp: each strand straight (the player bends it), at its foot
  {
    const material = new THREE.MeshStandardMaterial({ color: 0x2c7a54, roughness: 0.8, side: THREE.DoubleSide })
    const frames = []
    const strands = []
    for (const strand of sea.kelp) {
      const own = strand.material
      strand.material = material
      show([strand])
      const img = wide((offset) => shot({ offset }))
      strand.material = own
      const foot = project(strand.position)
      frames.push(img)
      strands.push({ img, foot, d: distance(strand.position) })
    }
    // a common cell, each strand's foot at its bottom middle
    let hw = 1
    let fh = 2
    // (in the wide renders, look pixels from MARGIN before the screen)
    const footX = (s) => Math.round(s.foot[0] / K) + MARGIN[0]
    const footY = (s) => Math.round(s.foot[1] / K) + MARGIN[1]
    for (const s of strands) {
      const e = extent(s.img)
      if (!e) continue
      const fx = footX(s)
      const fy = footY(s)
      hw = Math.max(hw, fx - e.x0 + 1, e.x1 + 2 - fx)
      fh = Math.max(fh, fy - e.y0 + 1)
    }
    fh += fh % 2
    const fw = 2 * hw
    const out = image(fw * strands.length, fh)
    strands.forEach((s, i) => blit(s.img, footX(s) - hw, footY(s) - fh + 1, fw, fh, out, i * fw, 0))
    images.kelp = out
    const z = strands.reduce((a, s) => a + s.foot[2], 0) / strands.length
    const d = NEAR + z * (FAR - NEAR)
    // the frame's centre: half its height above each foot
    const centres = strands.map((s) => [Math.round(s.foot[0] / K) * K, (Math.round(s.foot[1] / K) + 1 - fh / 2) * K])
    const cx = 960
    const cy = 540
    layout.actors.kelp = {
      sheet: 'kelp', frame: [fw, fh], columns: strands.length, at: [cx, cy], z: round(z, 4),
      clips: strands.map((_, i) => ({ name: `strand-${i}`, first: i, count: 1, fps: 1 })),
      places: strands.map((s, i) => [round(centres[i][0] - cx), round(centres[i][1] - cy), round(s.foot[2] - z, 4)]),
      // the scene's sway: sin(0.9 t + 0.7 y) * 0.45 * (y / 6), y up the strand
      bend: { amount: round((0.45 * f) / d), period: round((2 * Math.PI) / 0.9, 3), wave: round(-(0.7 * 6) / (2 * Math.PI), 3) },
    }
  }

  // ---- the rays: each alone, tilted as it rests, the light it adds
  {
    const frames = []
    // only the floor hides them (where they end); what stands in front is
    // drawn over them by depth, and moves apart from them as the view drifts
    const centres = []
    for (const [i, ray] of sea.rays.entries()) {
      ray.rotation.z = -0.2
      sea.floor.material.colorWrite = false
      show([ray, sea.floor])
      frames.push(wide((offset) => shot({ light: 1, levels: 32, offset })))
      sea.floor.material.colorWrite = true
      centres.push(project(ray.position))
      ray.rotation.z = -0.2 + Math.sin(START * 0.25 + i) * 0.05
    }
    let hw = 2
    let hh = 2
    const cxOf = (i) => Math.round(centres[i][0] / K) + MARGIN[0]
    const cyOf = (i) => Math.round(centres[i][1] / K) + MARGIN[1]
    frames.forEach((img, i) => {
      const e = extent(img)
      if (!e) return
      const cx = cxOf(i)
      const cy = cyOf(i)
      hw = Math.max(hw, cx - e.x0 + 1, e.x1 + 2 - cx)
      hh = Math.max(hh, cy - e.y0 + 1, e.y1 + 2 - cy)
    })
    const out = image(2 * hw * frames.length, 2 * hh)
    frames.forEach((img, i) => blit(img, cxOf(i) - hw, cyOf(i) - hh, 2 * hw, 2 * hh, out, i * 2 * hw, 0))
    images.rays = out
    const z = centres.reduce((a, c) => a + c[2], 0) / centres.length
    layout.actors.rays = {
      sheet: 'rays', frame: [2 * hw, 2 * hh], columns: frames.length, at: [960, 540], z: round(z, 4),
      clips: frames.map((_, i) => ({ name: `ray-${i}`, first: i, count: 1, fps: 1 })),
      places: centres.map((c) => [round(Math.round(c[0] / K) * K - 960), round(Math.round(c[1] / K) * K - 540), round(c[2] - z, 4)]),
      swing: { degrees: round(THREE.MathUtils.radToDeg(0.05), 2), period: round((2 * Math.PI) / 0.25, 2) },
      flicker: { min: 0.75, max: 1, period: round((2 * Math.PI) / 0.8, 2) },
    }
  }

  // ---- particles: a bubble as the sea's are drawn where they rise, and a
  // speck of snow
  {
    // three.js sizes a point by half the picture's height (180 rows) per
    // unit at distance 1: a bubble at its usual distance is 2 or 3 pixels
    const d = 12
    const size = Math.max(2, Math.round((0.16 * (H / 2)) / d))
    const bubble = image(size, size)
    const src = sea.bubbleTexture.image
    const c2 = document.createElement('canvas')
    c2.width = size
    c2.height = size
    const g = c2.getContext('2d')
    g.imageSmoothingEnabled = true
    g.drawImage(src, 0, 0, size, size)
    const px = g.getImageData(0, 0, size, size).data
    for (let i = 0; i < px.length; i += 4) {
      const a = (px[i + 3] / 255) * 0.8
      const on = a > 0.12 ? 1 : 0
      bubble.data.set([px[i], px[i + 1], px[i + 2], Math.round(on * Math.min(1, a * 1.6) * 255)], i)
    }
    images.bubble = bubble
    const snow = image(1, 1)
    snow.data.set([230, 251, 255, Math.round(0.55 * 255)])
    images.snow = snow
    // the bubbles rise 0.012 to 0.019 units a frame (60 a second), the snow
    // sinks 0.004 to 0.006; in canvas pixels at their usual distance
    layout.emitters = {
      bubbles: { rise: [round(0.72 * f / d), round(1.15 * f / d)], z: round((d - NEAR) / (FAR - NEAR), 3) },
      snow: { sink: [round(0.24 * f / 10), round(0.38 * f / 10)], z: round((10 - NEAR) / (FAR - NEAR), 3) },
      pull: { radius: round((5.5 * f) / distance(sea.disc)), swallow: round((0.45 * f) / distance(sea.disc)),
              speed: round((0.035 * 0.36 * 3 * 60 * f) / distance(sea.disc)), swirl: 0.48 },
    }
  }

  // ---- the poster: everything at rest (the floor's light as it was)
  seaAt(sea, START)
  sea.floor.material.emissiveIntensity = 0.3 + 0.05 * Math.sin(START * 1.3)
  band.value.set(-1000, 1000, 1, 0.0001)
  show(drawables.filter((o) => o !== sea.bubbles && o !== sea.snow))
  images.poster = shot({ opaque: true })

  // ---- the cover, the card's window: the label's square fills the disc
  // to 28.5 of its 30 mm, as on the disc; the rest the cover's deep
  {
    const src = sea.cover.image
    const size = 512
    const c2 = document.createElement('canvas')
    c2.width = size
    c2.height = size
    const g = c2.getContext('2d')
    g.fillStyle = '#04121b'
    g.fillRect(0, 0, size, size)
    const inner = Math.round(size * (28.5 / 30))
    g.imageSmoothingEnabled = true
    g.drawImage(src, (size - inner) / 2, (size - inner) / 2, inner, inner)
    const px = g.getImageData(0, 0, size, size)
    images.window = { w: size, h: size, data: px.data }
  }

  return { images, layout }
}
