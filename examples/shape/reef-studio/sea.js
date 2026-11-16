// The reef template's sea, in three.js: everything the package's images are
// rendered from (bake.js renders it; see README.md).
//
// The scene is built from simple shapes, seeded numbers and small textures
// painted in code, and the `sea` sample's caustics and bubble: the water
// (fog, light from above, rays, snow), the floor (sand under moving
// caustics, rocks, branching and brain corals, kelp swaying), the life
// (three schools of fish, their tails beating, which draw away from a small
// shark going round far behind; jellyfish pulsing; two crabs going sideways
// on the sand) and the game's cover, a whirlpool falling to the disc's hole
// with MUN Collect's name.
//
// A nod to the 32-bit consoles, as the console's pixel look draws it: the
// scene is rendered small (640 x 360) with its vertices snapped to that
// grid (snapVertices), and the bake reduces its colour to 15 bits with an
// ordered dither.
//
// buildSea() makes the scene and returns its parts; seaAt() puts them where
// they are at time t (seconds). Everything that moves is a function of
// time, which is how the bake turns it into paths, frames and variations
// for the console's player. Same seed, same sea: the order in which things
// draw their random numbers is part of the look, so add new things after
// the others.

import * as THREE from 'three'
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js'

/** The `sea` sample's textures, beside this template. */
const SEA = '../sea/world'

/**
 * The 32-bit consoles' wobble, for every built-in material: each vertex
 * snapped to the pixel grid of the small picture (640 x 360; half that a
 * clip-space unit). Once per page.
 */
let snapped = false
export function snapVertices() {
  if (snapped) return
  snapped = true
  THREE.ShaderChunk.project_vertex += `
    gl_Position.xy = floor(gl_Position.xy / gl_Position.w * vec2(320.0, 180.0) + 0.5) / vec2(320.0, 180.0) * gl_Position.w;`
}

/** A small texture painted in code, its pixels kept whole up close. */
function painted(width, height, paint) {
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  paint(canvas.getContext('2d'))
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  texture.magFilter = THREE.NearestFilter
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping
  return texture
}

/** Seeded randomness, so the sea is the same each visit: mulberry32, by
 *  Tommy Ettinger (2017), dedicated by its author to the public domain. */
export function seeded(seed) {
  let a = seed
  return () => {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** The floor's height at (x, z): low dunes. */
export const floorY = (x, z) => 0.35 * Math.sin(x * 0.35) * Math.cos(z * 0.28) + 0.18 * Math.sin(x * 0.9 + z * 0.6)

function branchingCoral(rand, colour) {
  const parts = []
  const grow = (from, dir, len, radius, depth) => {
    const to = from.clone().addScaledVector(dir, len)
    const piece = new THREE.CylinderGeometry(radius * 0.72, radius, len, 6, 1)
    piece.translate(0, len / 2, 0)
    piece.applyQuaternion(new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir))
    piece.translate(from.x, from.y, from.z)
    parts.push(piece)
    if (depth <= 0) {
      const tip = new THREE.SphereGeometry(radius * 0.9, 6, 4)
      tip.translate(to.x, to.y, to.z)
      parts.push(tip)
      return
    }
    for (let k = 0; k < 2 + (rand() < 0.3 ? 1 : 0); k++) {
      const next = dir.clone().add(new THREE.Vector3((rand() - 0.5) * 1.1, 0.25 + rand() * 0.4, (rand() - 0.5) * 1.1)).normalize()
      grow(to, next, len * (0.66 + rand() * 0.14), radius * 0.7, depth - 1)
    }
  }
  grow(new THREE.Vector3(), new THREE.Vector3(0, 1, 0), 0.7 + rand() * 0.4, 0.09, 4)
  const geometry = mergeGeometries(parts)
  for (const p of parts) p.dispose()
  return new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color: colour, roughness: 0.75, metalness: 0.05, flatShading: true }))
}

function brainCoral(rand, colour) {
  const g = new THREE.IcosahedronGeometry(0.6, 3)
  const p = g.attributes.position
  const v = new THREE.Vector3()
  for (let i = 0; i < p.count; i++) {
    v.fromBufferAttribute(p, i)
    const ridge = 1 + 0.06 * Math.sin(v.x * 18 + Math.sin(v.z * 9) * 2) + 0.03 * rand()
    v.multiplyScalar(ridge)
    if (v.y < 0) v.y *= 0.35
    p.setXYZ(i, v.x, v.y, v.z)
  }
  g.computeVertexNormals()
  return new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: colour, roughness: 0.9, flatShading: true }))
}

function rockGeometry(rand) {
  const g = new THREE.IcosahedronGeometry(1, 3)
  const p = g.attributes.position
  const v = new THREE.Vector3()
  const bumps = [rand() * 6, rand() * 6, rand() * 6]
  for (let i = 0; i < p.count; i++) {
    v.fromBufferAttribute(p, i)
    v.multiplyScalar(0.85 + 0.12 * Math.sin(v.x * 3 + bumps[0]) * Math.sin(v.y * 2.5 + bumps[1]) + 0.08 * Math.sin(v.z * 4 + bumps[2]))
    if (v.y < 0) v.y *= 0.4
    p.setXYZ(i, v.x, v.y, v.z)
  }
  g.computeVertexNormals()
  return g
}

/**
 * A fish, nose towards +x: a body turned on a lathe (deep behind the head, tapering to the tail), pressed
 * flat from the sides; a dorsal fin; a forked tail. Its texture runs round the body (u) and along it (v);
 * the fins take the texture's fin colour.
 */
function fishGeometry() {
  const profile = []
  for (let i = 0; i <= 14; i++) {
    const u = i / 14
    const y = -0.42 + u * 0.94
    const r = u === 0 || u === 1 ? 0.001 : 0.28 * Math.pow(Math.sin(Math.PI * Math.pow(u, 0.72)), 0.8)
    profile.push(new THREE.Vector2(r, y))
  }
  const body = new THREE.LatheGeometry(profile, 14)
  body.rotateZ(-Math.PI / 2)
  body.scale(1, 1, 0.48)
  const fin = (pts) => {
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3))
    g.setAttribute('normal', new THREE.Float32BufferAttribute(Array.from({ length: pts.length / 3 }, () => [0, 0, 1]).flat(), 3))
    g.setAttribute('uv', new THREE.Float32BufferAttribute(Array.from({ length: pts.length / 3 }, () => [0.02, 0.02]).flat(), 2))
    return g
  }
  // the tail, forked: two blades from the root
  const tail = fin([-0.4, 0, 0, -0.72, 0.26, 0, -0.6, 0.02, 0, -0.4, 0, 0, -0.6, -0.02, 0, -0.72, -0.24, 0])
  const dorsal = fin([-0.16, 0.22, 0, 0.14, 0.26, 0, -0.1, 0.44, 0])
  const merged = mergeGeometries([body.toNonIndexed(), tail, dorsal])
  body.dispose()
  tail.dispose()
  dorsal.dispose()
  return merged
}

/** A flat triangle (or fan of them), for fins and legs: its normals facing +z. */
function flat(pts) {
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3))
  g.computeVertexNormals()
  return g
}

/**
 * A small shark, nose towards +x, 2.4 long: a lathed body, sleek and pressed a little flat, a tall dorsal fin,
 * a tail whose upper lobe is the longer, and two pectoral fins.
 */
function sharkGeometry() {
  const profile = []
  for (let i = 0; i <= 18; i++) {
    const u = i / 18
    const y = -1.2 + u * 2.4
    const r = u === 0 || u === 1 ? 0.001 : 0.3 * Math.pow(Math.sin(Math.PI * Math.pow(u, 0.62)), 0.9)
    profile.push(new THREE.Vector2(r, y))
  }
  const body = new THREE.LatheGeometry(profile, 12)
  body.rotateZ(-Math.PI / 2)
  body.scale(1, 0.92, 0.72)
  const dorsal = flat([-0.15, 0.24, 0, 0.3, 0.26, 0, -0.08, 0.72, 0])
  const tail = flat([-1.05, 0.02, 0, -1.55, 0.62, 0, -1.3, 0.02, 0, -1.05, -0.02, 0, -1.3, -0.02, 0, -1.42, -0.34, 0])
  const finR = flat([0.35, -0.12, 0.18, 0.62, -0.14, 0.2, 0.18, -0.36, 0.6])
  const finL = flat([0.35, -0.12, -0.18, 0.18, -0.36, -0.6, 0.62, -0.14, -0.2])
  const merged = mergeGeometries([body.toNonIndexed(), dorsal, tail, finR, finL].map((g) => (g.deleteAttribute('uv'), g)))
  ;[body, dorsal, tail, finR, finL].forEach((g) => g.dispose())
  return merged
}

/** A crab, about 0.6 across: a round, low shell, two eyes on stalks, two claws forward, three legs a side. */
function crabGeometry() {
  const parts = []
  const shell = new THREE.SphereGeometry(0.2, 9, 6)
  shell.scale(1.15, 0.5, 0.85)
  parts.push(shell.toNonIndexed())
  shell.dispose()
  for (const side of [-1, 1]) {
    const eye = new THREE.CylinderGeometry(0.016, 0.016, 0.09, 4)
    eye.translate(side * 0.06, 0.12, 0.13)
    parts.push(eye.toNonIndexed())
    eye.dispose()
    const claw = new THREE.BoxGeometry(0.1, 0.07, 0.12)
    claw.translate(side * 0.17, 0.02, 0.24)
    parts.push(claw.toNonIndexed())
    claw.dispose()
    for (const k of [-1, 0, 1]) {
      const leg = new THREE.BoxGeometry(0.2, 0.022, 0.022)
      leg.translate(side * 0.1, 0, 0)
      leg.rotateZ(side * -0.45)
      leg.rotateY(k * 0.38)
      leg.translate(side * 0.18, -0.03, k * 0.07)
      parts.push(leg.toNonIndexed())
      leg.dispose()
    }
  }
  const merged = mergeGeometries(parts.map((g) => (g.deleteAttribute('uv'), g)))
  parts.forEach((g) => g.dispose())
  return merged
}

/** The fish's skin, 32 × 32: a dark back and a pale belly round the body, stripes along it, an eye. */
function fishSkin() {
  return painted(32, 32, (g) => {
    for (let x = 0; x < 32; x++) {
      // round the body: x/32 is the angle; the back up, the belly down
      const up = Math.sin((x / 32) * Math.PI * 2)
      const shade = Math.round(150 + 105 * (0.5 - 0.5 * up))
      g.fillStyle = `rgb(${shade},${shade},${shade})`
      g.fillRect(x, 0, 1, 32)
    }
    // three dark stripes across the back, along the body
    g.fillStyle = 'rgba(30,40,50,.55)'
    for (const y of [10, 15, 20]) g.fillRect(0, y, 32, 2)
    g.fillStyle = 'rgba(255,255,255,.35)'
    g.fillRect(0, 12, 32, 1)
    // the eye, near the nose, on both sides
    g.fillStyle = '#0b0f12'
    g.fillRect(6, 26, 2, 2)
    g.fillRect(22, 26, 2, 2)
    // the fins' colour, in the corner the fins sample
    g.fillStyle = 'rgb(205,215,220)'
    g.fillRect(0, 0, 2, 2)
  })
}

/** Sand, 64 × 64: grains and soft ripples. */
function sandTexture(rand) {
  return painted(64, 64, (g) => {
    for (let y = 0; y < 64; y++)
      for (let x = 0; x < 64; x++) {
        const ripple = Math.sin((x + Math.sin(y * 0.3) * 3) * 0.4) * 10
        const v = 150 + ripple + (rand() - 0.5) * 38
        g.fillStyle = `rgb(${Math.round(v * 0.95)},${Math.round(v * 0.9)},${Math.round(v * 0.72)})`
        g.fillRect(x, y, 1, 1)
      }
  })
}

/** Stone, 32 × 32: mottled, with a few pale flecks. */
function stoneTexture(rand) {
  return painted(32, 32, (g) => {
    for (let y = 0; y < 32; y++)
      for (let x = 0; x < 32; x++) {
        const v = 110 + (rand() - 0.5) * 50 + Math.sin(x * 0.7) * Math.cos(y * 0.5) * 18
        g.fillStyle = `rgb(${Math.round(v * 0.75)},${Math.round(v * 0.92)},${Math.round(v * 0.9)})`
        g.fillRect(x, y, 1, 1)
      }
    g.fillStyle = 'rgb(200,215,205)'
    for (let i = 0; i < 14; i++) g.fillRect(Math.floor(rand() * 32), Math.floor(rand() * 32), 1, 1)
  })
}


/** 5 × 7 letters for the cover's title, the way the 32-bit era's games drew theirs. */
const GLYPHS = {
  '3': ['11110', '00001', '00001', '01110', '00001', '00001', '11110'],
  '2': ['01110', '10001', '00001', '00010', '00100', '01000', '11111'],
  B: ['11110', '10001', '10001', '11110', '10001', '10001', '11110'],
  I: ['11111', '00100', '00100', '00100', '00100', '00100', '11111'],
  T: ['11111', '00100', '00100', '00100', '00100', '00100', '00100'],
  U: ['10001', '10001', '10001', '10001', '10001', '10001', '01110'],
  N: ['10001', '11001', '10101', '10011', '10001', '10001', '10001'],
  V: ['10001', '10001', '10001', '10001', '01010', '01010', '00100'],
  E: ['11111', '10000', '10000', '11110', '10000', '10000', '11111'],
  R: ['11110', '10001', '10001', '11110', '10100', '10010', '10001'],
  S: ['01111', '10000', '10000', '01110', '00001', '00001', '11110'],
  M: ['10001', '11011', '10101', '10101', '10001', '10001', '10001'],
  C: ['01110', '10001', '10000', '10000', '10000', '10001', '01110'],
  O: ['01110', '10001', '10001', '10001', '10001', '10001', '01110'],
  L: ['10000', '10000', '10000', '10000', '10000', '10000', '11111'],
}

function pixelWord(g, word, cx, y, dot) {
  let x = cx - (word.length * 6 - 1) * dot * 0.5
  for (const ch of word) {
    GLYPHS[ch]?.forEach((row, r) => [...row].forEach((bit, c) => bit === '1' && g.fillRect(x + c * dot, y + r * dot, dot, dot)))
    x += 6 * dot
  }
}

/**
 * The game's cover, on the disc's ring label: the deep sea, a whirlpool falling to the label's hole, its
 * water in spiral streams, a few bubbles, and MUN Collect's name in 5 × 7 letters. Painted small, its pixels kept.
 */
export function coverTexture(rand) {
  const texture = painted(512, 512, (g) => {
    g.scale(2, 2)
    const c = 128
    const image = g.createImageData(512, 512)
    for (let y = 0; y < 512; y++)
      for (let x = 0; x < 512; x++) {
        const dx = x / 2 - c
        const dy = y / 2 - c
        const r = Math.hypot(dx, dy) / 128
        const a = Math.atan2(dy, dx)
        const stream = Math.sin(a * 5 + Math.log(Math.max(r, 0.02)) * 9)
        const lit = Math.max(0, (stream - 0.55) / 0.45) * Math.min(1, Math.max(0, (r - 0.3) / 0.5))
        const deep = Math.min(1, Math.max(0, (r - 0.28) / 0.7))
        const i = (y * 512 + x) * 4
        image.data[i] = Math.round(4 + 30 * deep + 150 * lit)
        image.data[i + 1] = Math.round(14 + 90 * deep + 200 * lit)
        image.data[i + 2] = Math.round(24 + 110 * deep + 205 * lit)
        image.data[i + 3] = 255
      }
    g.putImageData(image, 0, 0)
    g.fillStyle = 'rgba(230,251,255,.85)'
    for (let i = 0; i < 26; i++) {
      const a = rand() * Math.PI * 2
      const r = 70 + rand() * 50
      g.fillRect(Math.round(c + Math.cos(a) * r), Math.round(c + Math.sin(a) * r), 2, 2)
    }
    // the title across the label's lower half, its shadow under it
    g.fillStyle = '#02141d'
    pixelWord(g, 'MUN', c + 1, 183, 3)
    pixelWord(g, 'COLLECT', c + 1, 209, 2)
    g.fillStyle = '#ffb25c'
    pixelWord(g, 'MUN', c, 182, 3)
    g.fillStyle = '#e6fbff'
    pixelWord(g, 'COLLECT', c, 208, 2)
    // the label's printed edges, a hairline in and out
    g.strokeStyle = 'rgba(230,251,255,.35)'
    g.lineWidth = 0.6
    for (const r of [44.5, 120.5]) {
      g.beginPath()
      g.arc(c, c, r, 0, Math.PI * 2)
      g.stroke()
    }
  })
  // drawn sharp, so its whirlpool is smooth and its title's blocks keep clean edges
  texture.magFilter = THREE.LinearFilter
  texture.minFilter = THREE.LinearMipmapLinearFilter
  texture.anisotropy = 4
  return texture
}


/**
 * The sea, built in a fixed order (the seed's numbers are drawn in that
 * order). `small` is the lighter sea for small screens. Returns its
 * scene, camera and parts.
 */
export function buildSea({ small = false } = {}) {
  const rand = seeded(1969)
  snapVertices()
  const scene = new THREE.Scene()
  const deep = new THREE.Color(0x0a4a5e)
  scene.fog = new THREE.FogExp2(deep, 0.055)

  // The water: a dome, light above and deep below, the sun's glow through the surface.
  const dome = new THREE.Mesh(
    new THREE.SphereGeometry(80, 32, 16),
    new THREE.ShaderMaterial({
      side: THREE.BackSide,
      depthWrite: false,
      fog: false,
      uniforms: { top: { value: new THREE.Color(0x63d4e6) }, mid: { value: new THREE.Color(0x167f9a) }, bottom: { value: new THREE.Color(0x03222f) } },
      vertexShader: 'varying vec3 vDir; void main() { vDir = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
      fragmentShader: `
        uniform vec3 top; uniform vec3 mid; uniform vec3 bottom; varying vec3 vDir;
        void main() {
          float h = vDir.y;
          vec3 c = h > 0.0 ? mix(mid, top, smoothstep(0.0, 0.85, h)) : mix(mid, bottom, smoothstep(0.0, 0.5, -h));
          c += vec3(0.9, 1.0, 1.0) * pow(max(h, 0.0), 12.0) * 0.6;
          gl_FragColor = vec4(c, 1.0);
        }`,
    }),
  )
  scene.add(dome)

  scene.add(new THREE.HemisphereLight(0x9fe7f2, 0x02202e, 1.15))
  const sun = new THREE.DirectionalLight(0xe6fbff, 1.6)
  sun.position.set(2, 10, 3)
  scene.add(sun)

  const loader = new THREE.TextureLoader()
  const caustics = loader.load(`${SEA}/caustics.png`)
  caustics.wrapS = caustics.wrapT = THREE.RepeatWrapping
  caustics.repeat.set(14, 14)
  caustics.magFilter = THREE.NearestFilter
  const bubbleTexture = loader.load(`${SEA}/bubble.png`)
  bubbleTexture.magFilter = THREE.NearestFilter
  const sand = sandTexture(rand)
  sand.repeat.set(26, 26)
  const stone = stoneTexture(rand)
  const skin = fishSkin()

  // The floor: dunes, lit by the moving caustics.
  const floorGeometry = new THREE.PlaneGeometry(60, 60, 96, 96)
  floorGeometry.rotateX(-Math.PI / 2)
  {
    const p = floorGeometry.attributes.position
    for (let i = 0; i < p.count; i++) p.setY(i, floorY(p.getX(i), p.getZ(i)))
    floorGeometry.computeVertexNormals()
  }
  const floor = new THREE.Mesh(
    floorGeometry,
    new THREE.MeshStandardMaterial({ color: 0x8fb8b4, map: sand, roughness: 1, emissive: 0x9fe7f2, emissiveMap: caustics, emissiveIntensity: 0.32 }),
  )
  scene.add(floor)

  // Rocks and corals, in a crescent around the disc, more of them to the sides.
  const rockMaterial = new THREE.MeshStandardMaterial({ color: 0xa8c4c2, map: stone, roughness: 0.95 })
  const place = (object, x, z, scale) => {
    object.position.set(x, floorY(x, z) - 0.05, z)
    object.scale.setScalar(scale)
    object.rotation.y = rand() * Math.PI * 2
    scene.add(object)
    standing.push(object)
  }
  const standing = []
  const spots = []
  for (let i = 0; i < 26; i++) {
    const side = i % 2 ? 1 : -1
    spots.push([side * (2.4 + rand() * 7), -2 - rand() * 9])
  }
  spots.forEach(([x, z], i) => {
    if (i % 3 === 0) place(new THREE.Mesh(rockGeometry(rand), rockMaterial), x, z, 0.5 + rand() * 0.8)
    else if (i % 3 === 1) place(branchingCoral(rand, [0xe07a5f, 0xb5739d, 0xe2b45c, 0x4fb3a9][i % 4]), x, z, 0.9 + rand() * 0.9)
    else place(brainCoral(rand, [0xc98f6b, 0x8f6fa0, 0xd8b56a][i % 3]), x, z, 0.5 + rand() * 0.6)
  })
  // a few close to the camera, on both sides, large
  place(branchingCoral(rand, 0xe07a5f), -4.6, 4.2, 1.8)
  place(branchingCoral(rand, 0xe2b45c), 4.8, 3.6, 1.9)
  place(brainCoral(rand, 0x8f6fa0), 3.4, 4.8, 0.9)

  // Kelp: ribbons swaying from the floor, the sway in the vertex shader.
  const shared = { time: { value: 0 } }
  const kelpMaterial = new THREE.MeshStandardMaterial({ color: 0x2c7a54, roughness: 0.8, side: THREE.DoubleSide })
  kelpMaterial.onBeforeCompile = (shader) => {
    shader.uniforms.uTime = shared.time
    shader.vertexShader = 'uniform float uTime;\n' + shader.vertexShader.replace(
      '#include <begin_vertex>',
      `#include <begin_vertex>
       float k = position.y / 6.0;
       transformed.x += sin(uTime * 0.9 + position.y * 0.7 + modelMatrix[3][0]) * 0.45 * k;
       transformed.z += cos(uTime * 0.7 + position.y * 0.5 + modelMatrix[3][2]) * 0.25 * k;`,
    )
  }
  const kelp = []
  const kelpGeometry = new THREE.PlaneGeometry(0.35, 6, 1, 24)
  kelpGeometry.translate(0, 3, 0)
  for (let i = 0; i < 22; i++) {
    const side = i % 2 ? 1 : -1
    const strand = new THREE.Mesh(kelpGeometry, kelpMaterial)
    const x = side * (3.5 + rand() * 9)
    const z = -1 - rand() * 12
    strand.position.set(x, floorY(x, z) - 0.1, z)
    strand.scale.y = 0.6 + rand() * 0.8
    strand.rotation.y = rand() * Math.PI
    scene.add(strand)
    kelp.push(strand)
  }

  // Fish: one instanced mesh, three schools; each fish's tail beats in the vertex shader.
  const SCHOOLS = small ? [24, 18, 8] : [44, 34, 14]
  const total = SCHOOLS.reduce((a, b) => a + b, 0)
  const fishMaterial = new THREE.MeshStandardMaterial({ color: 0xffffff, map: skin, metalness: 0.12, roughness: 0.45, side: THREE.DoubleSide })
  fishMaterial.onBeforeCompile = (shader) => {
    shader.uniforms.uTime = shared.time
    shader.vertexShader = 'uniform float uTime;\nattribute float phase;\n' + shader.vertexShader.replace(
      '#include <begin_vertex>',
      `#include <begin_vertex>
       float tailward = clamp(-position.x * 1.6, 0.0, 1.2);
       transformed.z += sin(uTime * 9.0 + phase - position.x * 4.0) * 0.16 * tailward;`,
    )
  }
  const fishGeom = fishGeometry()
  const phases = new Float32Array(total)
  for (let i = 0; i < total; i++) phases[i] = rand() * Math.PI * 2
  fishGeom.setAttribute('phase', new THREE.InstancedBufferAttribute(phases, 1))
  const fish = new THREE.InstancedMesh(fishGeom, fishMaterial, total)
  fish.instanceMatrix.setUsage(THREE.DynamicDrawUsage)
  {
    // the far school silver-blue, the middle one teal, the near one the sample's amber accent
    const tones = [0x9fc9d6, 0x5fc0c9, 0xf2b85c]
    const tint = new THREE.Color()
    let n = 0
    SCHOOLS.forEach((count, s) => {
      for (let i = 0; i < count; i++) fish.setColorAt(n++, tint.set(tones[s]).offsetHSL((rand() - 0.5) * 0.04, 0, (rand() - 0.5) * 0.12))
    })
  }
  scene.add(fish)
  const schools = SCHOOLS.map((count, s) => ({
    count,
    radius: [7, 4.8, 3][s],
    height: [3.4, 2.4, 1.6][s],
    depth: [-6, -2.5, 1.4][s],
    speed: [0.07, 0.1, 0.13][s],
    size: [0.32, 0.42, 0.55][s],
    offset: s * 2.1,
    members: Array.from({ length: count }, () => new THREE.Vector3((rand() - 0.5) * 2.4, (rand() - 0.5) * 1, (rand() - 0.5) * 1.6)),
  }))
  const where = (school, t) =>
    new THREE.Vector3(
      Math.sin(t * school.speed + school.offset) * school.radius,
      school.height + Math.sin(t * school.speed * 1.7 + school.offset) * 0.5,
      school.depth + Math.cos(t * school.speed + school.offset) * school.radius * 0.35,
    )

  // A small shark, far behind, going round slowly: when it passes, the schools near it draw away a little.
  const shark = new THREE.Mesh(sharkGeometry(), new THREE.MeshStandardMaterial({ color: 0x4a6573, roughness: 0.85, flatShading: true, side: THREE.DoubleSide }))
  scene.add(shark)
  const sharkAt = (t) => new THREE.Vector3(Math.sin(t * 0.045) * 11, 4.7 + Math.sin(t * 0.13) * 0.4, -8.5 + Math.cos(t * 0.045) * 2.2)
  const flee = schools.map(() => new THREE.Vector3())

  // Two crabs on the sand, going sideways, a little way and back.
  const crabMaterial = new THREE.MeshStandardMaterial({ color: 0xc9603f, roughness: 0.8, flatShading: true })
  const crabGeom = crabGeometry()
  const crabs = [
    { x: -3.4, z: -3.6, phase: 0, turn: 0.5 },
    { x: 3.1, z: -2.4, phase: 2.2, turn: -0.4 },
  ].map((c) => {
    const mesh = new THREE.Mesh(crabGeom, crabMaterial)
    mesh.scale.setScalar(1.6)
    mesh.rotation.y = c.turn
    scene.add(mesh)
    return { ...c, mesh }
  })

  // Jellyfish: translucent bells that pulse and drift up.
  const jellies = []
  const bellMaterial = new THREE.MeshStandardMaterial({ color: 0xe6fbff, emissive: 0x9fe7f2, emissiveIntensity: 0.45, transparent: true, opacity: 0.38, side: THREE.DoubleSide, depthWrite: false })
  const tentacleMaterial = new THREE.LineBasicMaterial({ color: 0xbfeff5, transparent: true, opacity: 0.45 })
  for (let i = 0; i < 6; i++) {
    const group = new THREE.Group()
    group.add(new THREE.Mesh(new THREE.SphereGeometry(0.4, 20, 10, 0, Math.PI * 2, 0, Math.PI / 2), bellMaterial))
    for (let k = 0; k < 5; k++) {
      const a = (k / 5) * Math.PI * 2
      const pts = Array.from({ length: 10 }, (_, j) => new THREE.Vector3(Math.cos(a) * 0.22 + Math.sin(j * 0.8 + k) * 0.05, -j * 0.12, Math.sin(a) * 0.22))
      group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), tentacleMaterial))
    }
    const side = i % 2 ? 1 : -1
    const base = new THREE.Vector3(side * (2.8 + rand() * 4), 2 + rand() * 3, -2 - rand() * 6)
    group.position.copy(base)
    scene.add(group)
    jellies.push({ group, base, phase: rand() * Math.PI * 2 })
  }

  // Rays: tall additive planes from the surface, swaying, faint.
  const rayMaterial = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    uniforms: { uTime: shared.time },
    vertexShader: 'varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
    fragmentShader: `
      uniform float uTime; varying vec2 vUv;
      void main() {
        float edge = smoothstep(0.0, 0.5, vUv.x) * smoothstep(1.0, 0.5, vUv.x);
        float fall = smoothstep(0.0, 0.9, vUv.y);
        float flicker = 0.75 + 0.25 * sin(uTime * 0.8 + vUv.x * 6.0);
        gl_FragColor = vec4(vec3(0.85, 1.0, 1.0), edge * fall * flicker * 0.16);
      }`,
  })
  const rays = []
  for (let i = 0; i < 7; i++) {
    const ray = new THREE.Mesh(new THREE.PlaneGeometry(1.2 + rand() * 1.6, 18), rayMaterial)
    ray.position.set(-7 + i * 2.3 + rand(), 7, -4 - rand() * 4)
    ray.rotation.z = -0.25 + rand() * 0.1
    scene.add(ray)
    rays.push(ray)
  }

  // Bubbles rising, and the sea's snow drifting: two point clouds.
  const bubbleCount = small ? 60 : 120
  const bubblePositions = new Float32Array(bubbleCount * 3)
  for (let i = 0; i < bubbleCount; i++) bubblePositions.set([(rand() - 0.5) * 18, rand() * 10, -rand() * 10 + 2], i * 3)
  const bubbleGeometry = new THREE.BufferGeometry()
  bubbleGeometry.setAttribute('position', new THREE.BufferAttribute(bubblePositions, 3))
  const bubbles = new THREE.Points(bubbleGeometry, new THREE.PointsMaterial({ size: 0.16, map: bubbleTexture, transparent: true, depthWrite: false, opacity: 0.8 }))
  scene.add(bubbles)
  const snowCount = small ? 300 : 700
  const snowPositions = new Float32Array(snowCount * 3)
  for (let i = 0; i < snowCount; i++) snowPositions.set([(rand() - 0.5) * 26, rand() * 10, -rand() * 16 + 4], i * 3)
  const snowGeometry = new THREE.BufferGeometry()
  snowGeometry.setAttribute('position', new THREE.BufferAttribute(snowPositions, 3))
  const snow = new THREE.Points(snowGeometry, new THREE.PointsMaterial({ size: 0.035, color: 0xe6fbff, transparent: true, opacity: 0.55, depthWrite: false }))
  scene.add(snow)

  // the game's cover, drawn last, as it draws its own numbers
  const cover = coverTexture(rand)

  // The camera at rest: a little above the disc, looking
  // a little down past it. The disc's centre is at (0, 3, 0).
  const camera = new THREE.PerspectiveCamera(46, 16 / 9, 0.1, 200)
  const look = new THREE.Vector3(0, 2.6, -1)
  camera.position.set(0, 2.9, 9.5)
  camera.lookAt(look)
  const disc = new THREE.Vector3(0, 3.0, 0)

  return {
    scene, camera, look, disc, rand, shared, floor, caustics, dome, standing, kelp, kelpMaterial,
    fish, fishMaterial, SCHOOLS, schools, where, shark, sharkAt, flee, crabs, jellies, rays, rayMaterial,
    bubbles, snow, bubbleTexture, cover,
  }
}

/**
 * Where everything is at time t (seconds):
 * the light on the sand, the shark, the crabs, the schools (drawing away
 * from the shark), the jellyfish and the rays. The bubbles and the snow are
 * left where they are: the console's particles stand for them.
 */
export function seaAt(sea, t) {
  const { shared, caustics, floor, shark, sharkAt, crabs, schools, where, flee, fish, jellies, rays } = sea
  shared.time.value = t
  caustics.offset.set((t * 0.012) % 1, (t * 0.008) % 1)
  floor.material.emissiveIntensity = 0.3 + 0.05 * Math.sin(t * 1.3)

  // the shark, its body swaying as it swims, facing its way
  const forward = new THREE.Vector3(1, 0, 0)
  const q = new THREE.Quaternion()
  const sharkHere = sharkAt(t)
  const sharkAhead = sharkAt(t + 0.5)
  q.setFromUnitVectors(forward, sharkAhead.clone().sub(sharkHere).normalize())
  shark.position.copy(sharkHere)
  shark.quaternion.copy(q)
  shark.rotateY(Math.sin(t * 1.6) * 0.09)

  // the crabs, sideways a little way and back, rocking as they go
  for (const crab of crabs) {
    const step = Math.sin(t * 0.45 + crab.phase)
    const x = crab.x + Math.cos(crab.turn) * step * 0.9
    const z = crab.z - Math.sin(crab.turn) * step * 0.9
    crab.mesh.position.set(x, floorY(x, z) + 0.06 + Math.abs(Math.sin(t * 7 + crab.phase)) * 0.015, z)
    crab.mesh.rotation.z = Math.sin(t * 7 + crab.phase) * 0.05 * Math.abs(Math.cos(t * 0.45 + crab.phase))
  }

  // the schools: each fish at its place in the group, facing the way the
  // group swims; near the shark, the group draws away from it (flee is
  // eased frame by frame, so seaAt is called in order for it)
  const m = new THREE.Matrix4()
  const scale = new THREE.Vector3()
  let n = 0
  for (const [s, school] of schools.entries()) {
    const centre = where(school, t)
    const away = centre.clone().sub(sharkHere)
    const near = away.length()
    flee[s].lerp(near < 5.5 ? away.normalize().multiplyScalar((5.5 - near) * 0.75) : away.set(0, 0, 0), 0.04)
    const here = centre.add(flee[s])
    const ahead = where(school, t + 0.4)
    const dir = ahead.clone().sub(here).normalize()
    q.setFromUnitVectors(forward, dir)
    for (let i = 0; i < school.count; i++) {
      const p = school.members[i]
      const wobble = Math.sin(t * 1.3 + i) * 0.15
      m.compose(here.clone().add(new THREE.Vector3(p.x, p.y + wobble, p.z)), q, scale.setScalar(school.size))
      fish.setMatrixAt(n++, m)
    }
  }
  fish.instanceMatrix.needsUpdate = true

  for (const j of jellies) {
    const pulse = 0.5 + 0.5 * Math.sin(t * 1.6 + j.phase)
    j.group.scale.set(1 + pulse * 0.15, 1 - pulse * 0.12, 1 + pulse * 0.15)
    j.group.position.set(j.base.x + Math.sin(t * 0.2 + j.phase) * 0.4, j.base.y + ((t * 0.12 + j.phase) % 3) - 1.5, j.base.z)
  }
  for (const [i, ray] of rays.entries()) ray.rotation.z = -0.2 + Math.sin(t * 0.25 + i) * 0.05
}
