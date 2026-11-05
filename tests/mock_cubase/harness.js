// Minimal simulation of Cubase's MIDI Remote API, used to run the real bridge
// script under Node for end-to-end tests. stdin/stdout carry MIDI as text lines:
//   "sx <hex bytes>"  sysex      "cc <ch> <cc> <val>"   control change
'use strict'
const vm = require('vm'), fs = require('fs'), readline = require('readline')
const scriptPath = process.argv[2]
const log = (...a) => process.stderr.write(a.join(' ') + '\n')

const names = ['Drums', 'Bass', 'Keys']
const executed = []
const daWrites = []           // commands executed
const actionsTriggered = []
let tempo = 120
const valueBindings = []      // {cv, hv}
const nrpnMap = {}            // "ch:nrpn" -> cv
const hostValues = new Map()  // hv -> value

function makeCV(name) {
  const cv = { name, value: 0, mMidiBinding: null }
  cv.mMidiBinding = {
    setInputPort() { return cv.mMidiBinding }, setOutputPort() { return cv.mMidiBinding },
    bindToControlChange14BitNRPN(ch, n) { nrpnMap[ch + ':' + n] = cv; return {} },
  }
  cv.setProcessValue = (dev, v) => { cv.value = v; onSurfaceChange(cv, v) }
  cv.getProcessValue = () => cv.value
  return cv
}
function onSurfaceChange(cv, v) {
  for (const b of valueBindings) if (b.cv === cv) setHost(b.hv, v)
  if (cv.command && v > 0) executed.push(cv.command)
}
function setHost(hv, v) {
  hostValues.set(hv, v)
  const m = /\.ch(\d+)\.mValue\.mSelected$/.exec(String(hv.__path))
  if (m && v > 0.5) { const sel = page.mHostAccess.mTrackSelection.mMixerChannel; sel.mOnTitleChange && sel.mOnTitleChange(dev, mapping, names[+m[1] - 1] || 'ch') }
  for (const b of valueBindings) if (b.hv === hv) {
    if (b.cv.mOnProcessValueChange) b.cv.mOnProcessValueChange(dev, v)
    if (b.cv.mOnDisplayValueChange) b.cv.mOnDisplayValueChange(dev, (v * 100).toFixed(1), 'u')
  }
}

// generic host object: any property is a child host object, any call returns itself (chainable)
function hostObj(path) {
  const cache = {}, assigned = {}
  const target = function () {}
  const p = new Proxy(target, {
    get(t, k) {
      if (k in assigned) return assigned[k]
      if (k === 'getByIndex') return i => cache['#' + i] || (cache['#' + i] = hostObj(path + '[' + i + ']'))
      if (k === 'makeMixerBankChannel') { let n = 0; return () => { const c = hostObj(path + '.ch' + (cache.__n = (cache.__n || 0) + 1)); (p.__channels = p.__channels || []).push(c); return c } }
      if (k === 'makeInsertEffectViewer' || k === 'makeMixerBankZone') return name => cache['@' + name] || (cache['@' + name] = hostObj(path + '.' + name))
      if (k === '__path') return path
      if (k === 'accessSlotAtIndex' || /^(include|exclude|setFollow|setWrap)/.test(String(k))) return () => p
      if (k === 'makeParameterValue') return () => hostObj(path + '.param' + (cache.__pn = (cache.__pn || 0) + 1))
      if (k === 'trigger') return () => actionsTriggered.push(path)
      if (k === 'setTempoBPM') return (m, bpm) => { tempo = bpm; const f = assigned_td().mOnChangeTempoBPM; f && f(dev, m, bpm) }
      if (k === 'setTime') return (m, s) => { const f = p.mOnChange; f && f(dev, m, s, 'Bars+Beats') }
      if (k === 'getRuntimeID') return () => 7
      if (k === 'getUniqueIDString') return () => 'uid-' + path
      if (k === 'increment' || k === 'decrement') return () => {}
      if (k === 'makeDirectAccess') return () => makeDA()
      if (k === '__channels') return assigned.__channels
      if (typeof k === 'symbol') return undefined
      return cache[k] || (cache[k] = hostObj(path + '.' + String(k)))
    },
    set(t, k, v) { assigned[k] = v; return true },
    has(t, k) { return true },
    apply() { return p },
  })
  return p
}
let tdRef = null
function assigned_td() { return tdRef }

function makeDA() {
  const params = { 10: { t: 'Threshold', v: 0.5 }, 11: { t: 'Ratio', v: 0.25 } }
  const nodes = { 1: { t: 'Kick', type: 'MixerChannel', kids: [2, 3, 4] }, 2: { t: 'Compressor', type: 'InsertSlot', kids: [], params: [10, 11], slot: 'fx' }, 3: { t: 'EQ', type: 'ChannelEQ', kids: [] }, 4: { t: '', type: 'InstrumentSlot', kids: [], slot: 'inst' } }
  const fx = [{ mPluginName: 'REVerence', mPluginUID: 'ABC123', mPluginVendor: 'Steinberg', mSubCategories: 'Fx|Reverb' }, { mPluginName: 'Compressor', mPluginUID: 'CMP1', mPluginVendor: 'Steinberg', mSubCategories: 'Fx|Dynamics' }]
  const inst = [{ mPluginName: 'HALion Sonic', mPluginUID: 'HS7', mPluginVendor: 'Steinberg', mSubCategories: 'Instrument|Synth|Sampler' }, { mPluginName: 'Retrologue', mPluginUID: 'RETRO2', mPluginVendor: 'Steinberg', mSubCategories: 'Instrument|Synth' }]
  return {
    activate() {}, deactivate() {},
    getBaseObjectID: () => 1,
    getNumberOfChildObjects: (m, id) => nodes[id].kids.length,
    getChildObjectID: (m, id, i) => nodes[id].kids[i],
    getObjectTitle: (m, id) => nodes[id].t, getObjectTypeName: (m, id) => nodes[id].type,
    getObjectUniqueName: (m, id) => 'n' + id, getObjectUniqueIDString: (m, id) => 'u' + id,
    getNumberOfParameters: (m, id) => (nodes[id].params || []).length,
    getParameterTagByIndex: (m, id, i) => nodes[id].params[i],
    getParameterTitle: (m, id, tag) => params[tag].t,
    getParameterProcessValue: (m, id, tag) => params[tag].v,
    setParameterProcessValue: (m, id, tag, v) => { params[tag].v = v; daWrites.push([id, tag, v]) },
    setParameterDisplayValue: (m, id, tag, s) => { params[tag].v = parseFloat(s) / 100 },
    getParameterDisplayValue: (m, id, tag) => (params[tag].v * 100).toFixed(1),
    getParameterDisplayUnits: () => 'dB', getParameterDefaultProcessValue: () => 0.5,
    mPluginManager: {
      getNumberOfPluginCollections: (m, id) => (nodes[id] && nodes[id].slot ? 1 : 0), getIndexOfDefaultPluginCollection: () => 0, getIndexOfActivePluginCollection: () => 0,
      getPluginCollectionByIndex: (m, id) => ({ mName: 'All', mEntries: nodes[id].slot === 'inst' ? inst : fx }),
      trySetSlotPlugin: (m, id, uid) => { executed.push('load:' + id + ':' + uid); nodes[id].t = uid; return true }, resetSlotPlugin: () => {},
    },
  }
}

let midiIn, midiOut, page, driver
const dev = {}, mapping = {}
const api = {
  mDefaults: { getAppName: () => 'MockCubase', mAppVersion: { getVersionString: () => '15.0.20' },
    getNumberOfInsertEffectSlots: () => 16, getNumberOfSendSlots: () => 8, getNumberOfQuickControls: () => 8, getMaxGainFactor: () => 4 },
  makeDeviceDriver() {
    const det = { expectInputNameContains() { return det }, expectOutputNameContains() { return det } }
    driver = {
      mPorts: { makeMidiInput: () => (midiIn = {}), makeMidiOutput: () => (midiOut = { sendMidi: (d, bytes) => out('sx ' + Buffer.from(bytes).toString('hex')) }) },
      makeDetectionUnit: () => ({ detectPortPair: () => det }),
      mSurface: { makeCustomValueVariable: makeCV, makeLabelField: () => ({}) },
      mMapping: { makePage: () => (page = {
        mHostAccess: (() => { const h = hostObj('host'); tdRef = h.mTransport.mTimeDisplay; return h })(),
        makeValueBinding(cv, hv) { valueBindings.push({ cv, hv }); return { setValueTakeOverModeJump() {} } },
        makeCommandBinding(cv, cat, cmd) { cv.command = cat + '/' + cmd; return { canPerform: () => true } },
        makeActionBinding() { return {} },
      }) },
    }
    return driver
  },
}
function out(line) { process.stdout.write(line + '\n') }

const src = fs.readFileSync(scriptPath, 'utf8')
vm.runInNewContext(src, { require: () => api, console: { log: (...a) => log(...a) } }, { filename: scriptPath })

// activation + initial host state
page.mOnActivate(dev, mapping)
const host = page.mHostAccess
const bankZone = host.mMixConsole.makeMixerBankZone('MCP Bank')
;(bankZone.__channels || []).forEach((c, i) => { if (i < names.length && c.mOnTitleChange) c.mOnTitleChange(dev, mapping, names[i]) })
const ioZone = host.mMixConsole.makeMixerBankZone('MCP IO')
;(ioZone.__channels || []).forEach((c, i) => { if (i === 0 && c.mOnTitleChange) c.mOnTitleChange(dev, mapping, 'Stereo Out') })
const sel = host.mTrackSelection.mMixerChannel
sel.mOnTitleChange && sel.mOnTitleChange(dev, mapping, 'Drums')
tdRef.mOnChangeTempoBPM && tdRef.mOnChangeTempoBPM(dev, mapping, 120)
setInterval(() => page.mOnIdle(dev, mapping), 5)

const nrpnState = {}
const rl = readline.createInterface({ input: process.stdin })
rl.on('close', () => setTimeout(() => process.exit(0), 100))
rl.on('line', line => {
  const parts = line.trim().split(' ')
  if (parts[0] === 'sx') midiIn.mOnSysex(dev, Array.from(Buffer.from(parts[1], 'hex')))
  else if (parts[0] === 'cc') {
    const ch = +parts[1], cc = +parts[2], v = +parts[3]
    const st = nrpnState[ch] || (nrpnState[ch] = {})
    if (cc === 99) st.msb = v; else if (cc === 98) st.lsb = v; else if (cc === 6) st.dmsb = v
    else if (cc === 38) {
      const cv = nrpnMap[ch + ':' + ((st.msb << 7) | st.lsb)]
      if (cv) cv.setProcessValue(dev, ((st.dmsb << 7) | v) / 16383)
    }
  } else if (parts[0] === 'q') {
    out('info ' + JSON.stringify({ executed, actionsTriggered, tempo, daWrites: daWrites.length }))
  }
})
