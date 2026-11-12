#!/usr/bin/env node
// Bakes the reef: renders sea.js in a headless Chrome or Chromium and writes
// what the console draws into render/ (PNG images and layout.json), for
// make_package.py.
//
//   npm ci                 # three.js, once
//   node bake.mjs          # or: CHROME=/path/to/chrome node bake.mjs
//
// Needs Node 22 or later (its WebSocket) and a Chrome or Chromium; nothing
// else is installed. The browser is driven through its DevTools protocol:
// a page served from this folder's parent (so that ../sea/world is found)
// runs bake.js and hands back raw pixels, which are written here as PNG.

import { spawn } from 'node:child_process'
import { createServer } from 'node:http'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { homedir, tmpdir } from 'node:os'
import { dirname, extname, join, normalize, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { deflateSync } from 'node:zlib'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')          // examples/shape: the page and the `sea` sample's textures
const OUT = join(HERE, 'render')

function browser() {
  if (process.env.CHROME) return process.env.CHROME
  const candidates = [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium', '/usr/bin/chromium-browser',
  ]
  // a Chromium that a browser-testing tool downloaded
  for (const cache of [join(homedir(), 'Library/Caches/ms-playwright'), join(homedir(), '.cache/ms-playwright')]) {
    if (!existsSync(cache)) continue
    for (const dir of readdirSync(cache).filter((d) => /^chromium-\d+$/.test(d)).sort().reverse()) {
      candidates.push(join(cache, dir, 'chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'))
      candidates.push(join(cache, dir, 'chrome-mac/Chromium.app/Contents/MacOS/Chromium'))
      candidates.push(join(cache, dir, 'chrome-linux/chrome'))
    }
  }
  const found = candidates.find((c) => existsSync(c))
  if (!found) throw new Error('no Chrome or Chromium found: set CHROME to one')
  return found
}

const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.png': 'image/png', '.json': 'application/json' }
function serve() {
  const server = createServer((req, res) => {
    const path = normalize(join(ROOT, decodeURIComponent(new URL(req.url, 'http://x').pathname)))
    if (!path.startsWith(ROOT + sep) || !existsSync(path)) {
      res.writeHead(404).end()
      return
    }
    res.writeHead(200, { 'content-type': TYPES[extname(path)] ?? 'application/octet-stream' }).end(readFileSync(path))
  })
  return new Promise((done) => server.listen(0, '127.0.0.1', () => done(server)))
}

// ---- PNG, 8-bit RGBA, no filter (zlib does the rest)
const CRC = Array.from({ length: 256 }, (_, n) => {
  let c = n
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
  return c >>> 0
})
function crc(bytes) {
  let c = 0xffffffff
  for (const b of bytes) c = CRC[(c ^ b) & 0xff] ^ (c >>> 8)
  return (c ^ 0xffffffff) >>> 0
}
function chunk(type, data) {
  const head = Buffer.alloc(8)
  head.writeUInt32BE(data.length, 0)
  head.write(type, 4, 'ascii')
  const sum = Buffer.alloc(4)
  sum.writeUInt32BE(crc(Buffer.concat([head.subarray(4), data])), 0)
  return Buffer.concat([head, data, sum])
}
function png(w, h, rgba) {
  const header = Buffer.alloc(13)
  header.writeUInt32BE(w, 0)
  header.writeUInt32BE(h, 4)
  header.set([8, 6, 0, 0, 0], 8)
  const rows = Buffer.alloc((w * 4 + 1) * h)
  for (let y = 0; y < h; y++) rgba.copy(rows, y * (w * 4 + 1) + 1, y * w * 4, (y + 1) * w * 4)
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header),
                        chunk('IDAT', deflateSync(rows, { level: 9 })), chunk('IEND', Buffer.alloc(0))])
}

// ---- the DevTools protocol, one page
function devtools(url) {
  const socket = new WebSocket(url)
  let next = 1
  const waiting = new Map()
  socket.addEventListener('message', (event) => {
    const message = JSON.parse(event.data)
    if (message.id && waiting.has(message.id)) {
      const { done, fail } = waiting.get(message.id)
      waiting.delete(message.id)
      message.error ? fail(new Error(message.error.message)) : done(message.result)
    } else if (message.method === 'Runtime.exceptionThrown') {
      console.error('page:', message.params.exceptionDetails.exception?.description ?? message.params.exceptionDetails.text)
    } else if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') {
      console.error('page:', message.params.args.map((a) => a.value ?? a.description).join(' '))
    }
  })
  const send = (method, params = {}) => new Promise((done, fail) => {
    const id = next++
    waiting.set(id, { done, fail })
    socket.send(JSON.stringify({ id, method, params }))
  })
  return new Promise((done, fail) => {
    socket.addEventListener('open', () => done({ send, close: () => socket.close() }))
    socket.addEventListener('error', () => fail(new Error('cannot reach the browser')))
  })
}

async function main() {
  const server = await serve()
  const profile = mkdtempSync(join(tmpdir(), 'reef-bake-'))
  const chrome = spawn(browser(), ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`,
                                   // a throwaway profile: no keychain (macOS) or keyring (Linux) asked for
                                   '--use-mock-keychain', '--password-store=basic',
                                   '--no-first-run', '--no-default-browser-check', '--ignore-gpu-blocklist', '--enable-unsafe-swiftshader',
                                   '--window-size=640,360', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] })
  try {
    const endpoint = await new Promise((done, fail) => {
      let text = ''
      chrome.stderr.on('data', (data) => {
        text += data
        const match = text.match(/DevTools listening on (ws:\/\/[^\s]+)/)
        if (match) done(match[1])
      })
      chrome.on('exit', (code) => fail(new Error(`the browser stopped (${code}): ${text.slice(-400)}`)))
    })
    const port = new URL(endpoint).port
    // its first page may come a moment after it starts listening
    let blank
    for (let i = 0; !blank; i++) {
      if (i > 100) throw new Error('the browser opened no page')
      if (i) await new Promise((r) => setTimeout(r, 100))
      blank = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find((p) => p.type === 'page')
    }
    const page = await devtools(blank.webSocketDebuggerUrl)
    await page.send('Runtime.enable')
    await page.send('Page.navigate', { url: `http://127.0.0.1:${server.address().port}/reef-studio/bake.html` })
    for (let i = 0; ; i++) {
      const ready = await page.send('Runtime.evaluate', { expression: 'window.bakeReady === true', returnByValue: true })
      if (ready.result.value) break
      if (i > 300) throw new Error('the page did not load (is three.js installed? npm ci)')
      await new Promise((r) => setTimeout(r, 100))
    }
    const started = Date.now()
    const result = await page.send('Runtime.evaluate', { expression: 'window.runBake()', awaitPromise: true, returnByValue: true })
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text)
    const { images, layout } = result.result.value
    rmSync(OUT, { recursive: true, force: true })
    mkdirSync(OUT, { recursive: true })
    for (const [name, img] of Object.entries(images))
      writeFileSync(join(OUT, `${name}.png`), png(img.w, img.h, Buffer.from(img.rgba, 'base64')))
    writeFileSync(join(OUT, 'layout.json'), JSON.stringify(layout, null, 1) + '\n')
    console.log(`baked ${Object.keys(images).length} images into render/ in ${((Date.now() - started) / 1000).toFixed(1)} s`)
    page.close()
  } finally {
    // the profile goes once the browser has let go of it
    const gone = new Promise((done) => (chrome.exitCode !== null ? done() : chrome.once('exit', done)))
    chrome.kill()
    await gone
    server.close()
    rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
  }
}

main().catch((error) => {
  console.error(`bake: ${error.message}`)
  process.exit(1)
})
