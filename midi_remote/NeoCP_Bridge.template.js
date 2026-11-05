//-----------------------------------------------------------------------------
// NeoCP Bridge - MIDI Remote script
//
// Runs INSIDE Cubase / Nuendo (MIDI Remote API v1, ES5). It exposes the host
// to the external "neocp" Python server through two MIDI ports:
//
//   "NeoCP to Cubase"   (Python -> Cubase)  : SysEx JSON-RPC + NRPN writes
//   "NeoCP from Cubase" (Cubase -> Python)  : SysEx responses + events
//
// Protocol (SysEx, 7-bit clean):
//   F0 7D 43 4D <type> <id_hi> <id_lo> <seq_hi> <seq_lo> <tot_hi> <tot_lo> <ascii json...> F7
//   type 0x01 = request (py->cubase), 0x02 = response, 0x03 = event
//
// Value writes: every host value is bound to a custom value variable that is
// also bound to a 14-bit NRPN (slot index -> channel = idx >> 14, nrpn = idx & 0x3FFF).
// Python can therefore drive values exactly like a hardware controller would
// (most robust path), or ask the script to call setProcessValue via SysEx.
//
// GENERATED FILE: edit midi_remote/NeoCP_Bridge.template.js and run
//   python scripts/build_midi_remote.py
//-----------------------------------------------------------------------------

var SCRIPT_VERSION = '__VERSION__'
var PROTOCOL_VERSION = 1

// ---- configuration ----------------------------------------------------------
var CFG = {
    BANK_SIZE: 32,          // mixer bank channels mirrored at once
    SEND_SLOTS: 8,          // raised to the host's count below (Cubase 16 Pro: 16)
    INSERT_SLOTS: 16,
    QUICK_CONTROLS: 8,
    INSTRUMENT_PARAMS: 16,  // parameter bank values for the selected instrument
    CHUNK: 240,             // payload bytes per sysex chunk
    MAX_OUT_CHUNKS_PER_IDLE: 48,
    EVENT_INTERVAL_IDLES: 2,
    // command categories that are NOT bound (keeps the script lighter)
    SKIP_COMMAND_CATEGORIES: {
        'Score Functions': 1, 'Score Meter Scale': 1, 'Score Symbol Editor': 1,
        'Score Symbol Palettes': 1, 'Scores': 1, 'Scores Statusbar': 1,
        'Score Align Elements': 1, 'ADM Editor': 1, 'Game Audio Connect': 1,
        'VRPlayerRemote': 1, 'HeadTracking': 1
    }
}

// Cubase 16 Pro has 16 sends per channel: follow the host (capped to keep the script light)
try {
    var hostSends = midiremote_api_v1_defaults_sends()
    if (hostSends > CFG.SEND_SLOTS) CFG.SEND_SLOTS = Math.min(16, hostSends)
} catch (e) { }
function midiremote_api_v1_defaults_sends() {
    return require('midiremote_api_v1').mDefaults.getNumberOfSendSlots()
}

var PORT_IN_NAME = 'NeoCP to Cubase'
var PORT_OUT_NAME = 'NeoCP from Cubase'

// ---- minimal JSON fallback (only if host engine lacks JSON) ----------------
var J = (typeof JSON !== 'undefined') ? JSON : null
if (!J) {
    J = {}
    J.stringify = function (v) {
        var t = typeof v
        if (v === null || v === undefined) return 'null'
        if (t === 'number') return isFinite(v) ? String(v) : 'null'
        if (t === 'boolean') return v ? 'true' : 'false'
        if (t === 'string') return '"' + v.replace(/[\\"\u0000-\u001f]/g, function (c) {
            if (c === '"') return '\\"'
            if (c === '\\') return '\\\\'
            var h = c.charCodeAt(0).toString(16)
            return '\\u' + ('0000' + h).slice(-4)
        }) + '"'
        if (v instanceof Array) {
            var a = []
            for (var i = 0; i < v.length; ++i) a.push(J.stringify(v[i]))
            return '[' + a.join(',') + ']'
        }
        var o = []
        for (var k in v) if (v.hasOwnProperty(k) && v[k] !== undefined && typeof v[k] !== 'function')
            o.push(J.stringify(String(k)) + ':' + J.stringify(v[k]))
        return '{' + o.join(',') + '}'
    }
    J.parse = function (s) {
        var i = 0
        function ws() { while (i < s.length && ' \t\r\n'.indexOf(s.charAt(i)) >= 0) i++ }
        function val() {
            ws()
            var c = s.charAt(i)
            if (c === '{') { i++; var o = {}; ws(); if (s.charAt(i) === '}') { i++; return o }
                while (true) { ws(); var k = str(); ws(); i++; o[k] = val(); ws(); var d = s.charAt(i++); if (d === '}') return o } }
            if (c === '[') { i++; var a = []; ws(); if (s.charAt(i) === ']') { i++; return a }
                while (true) { a.push(val()); ws(); var e = s.charAt(i++); if (e === ']') return a } }
            if (c === '"') return str()
            if (s.substr(i, 4) === 'true') { i += 4; return true }
            if (s.substr(i, 5) === 'false') { i += 5; return false }
            if (s.substr(i, 4) === 'null') { i += 4; return null }
            var m = /^-?\d+(\.\d+)?([eE][+-]?\d+)?/.exec(s.substr(i))
            i += m[0].length
            return parseFloat(m[0])
        }
        function str() {
            i++; var r = ''
            while (true) {
                var c = s.charAt(i++)
                if (c === '"') return r
                if (c === '\\') {
                    var e = s.charAt(i++)
                    if (e === 'u') { r += String.fromCharCode(parseInt(s.substr(i, 4), 16)); i += 4 }
                    else if (e === 'n') r += '\n'
                    else if (e === 't') r += '\t'
                    else if (e === 'r') r += '\r'
                    else if (e === 'b') r += '\b'
                    else if (e === 'f') r += '\f'
                    else r += e
                } else r += c
            }
        }
        return val()
    }
}

function toAsciiJson(obj) {
    return J.stringify(obj).replace(/[\u007f-￿]/g, function (c) {
        return '\\u' + ('0000' + c.charCodeAt(0).toString(16)).slice(-4)
    })
}

function log(msg) {
    if (typeof console !== 'undefined' && console.log) console.log('[NeoCP] ' + msg)
}

function round4(v) { return (typeof v === 'number') ? Math.round(v * 10000) / 10000 : v }

// ---- driver / ports -----------------------------------------------------------
var midiremote_api = require('midiremote_api_v1')
var driver = midiremote_api.makeDeviceDriver('NeoCP', 'Bridge', 'neocp project')

var midiIn = driver.mPorts.makeMidiInput(PORT_IN_NAME)
var midiOut = driver.mPorts.makeMidiOutput(PORT_OUT_NAME)

driver.makeDetectionUnit().detectPortPair(midiIn, midiOut)
    .expectInputNameContains('NeoCP to Cubase')
    .expectOutputNameContains('NeoCP from Cubase')
// Windows (loopMIDI) users sometimes name ports "NeoCP_to_Cubase" etc.
driver.makeDetectionUnit().detectPortPair(midiIn, midiOut)
    .expectInputNameContains('NeoCP_to_Cubase')
    .expectOutputNameContains('NeoCP_from_Cubase')

var surface = driver.mSurface
var page = driver.mMapping.makePage('MCP')
var host = page.mHostAccess

// a tiny label so the surface is not empty in the MIDI Remote window
surface.makeLabelField(0, 0, 8, 1)

// ---- slot registry ------------------------------------------------------------
// slot = { id, key, kind: 'v' (value) | 'c' (command), cv, v, d, u, t, vt, meter }
var slots = []
var slotByKey = {}
var dirty = {}
var dirtyCount = 0
var gDevice = null
var gMapping = null

function makeSlot(key, kind) {
    var id = slots.length
    var cv = surface.makeCustomValueVariable('s' + id)
    cv.mMidiBinding.setInputPort(midiIn).bindToControlChange14BitNRPN(id >> 14, id & 0x3FFF)
    var s = { id: id, key: key, kind: kind, cv: cv, v: null, d: null, u: null, t: null, vt: null, meter: false }
    slots.push(s)
    slotByKey[key] = s
    return s
}

function markDirty(s) {
    if (s.meter) return
    if (!dirty[s.id]) { dirty[s.id] = true; dirtyCount++ }
}

function bindValue(key, hostValue, opts) {
    if (!hostValue) return null
    var s
    try {
        s = makeSlot(key, 'v')
        s.hv = hostValue
        var b = page.makeValueBinding(s.cv, hostValue)
        if (b && b.setValueTakeOverModeJump) b.setValueTakeOverModeJump()
    } catch (e) {
        log('bind failed ' + key + ': ' + e)
        return s || null
    }
    if (opts && opts.meter) s.meter = true
    s.cv.mOnProcessValueChange = function (activeDevice, value) {
        s.v = value; markDirty(s)
    }
    s.cv.mOnDisplayValueChange = function (activeDevice, value, units) {
        s.d = value; s.u = units; markDirty(s)
    }
    s.cv.mOnTitleChange = function (activeDevice, objectTitle, valueTitle) {
        s.t = objectTitle; s.vt = valueTitle; markDirty(s)
    }
    return s
}

var commandIndex = {} // "category\u0001command" -> slot
function bindCommand(category, command) {
    var k = category + '\u0001' + command
    if (commandIndex[k]) return commandIndex[k]
    try {
        var s = makeSlot('cmd:' + category + '/' + command, 'c')
        var cb = page.makeCommandBinding(s.cv, category, command)
        s.binding = cb
        commandIndex[k] = s
        return s
    } catch (e) {
        log('command bind failed ' + category + '/' + command + ': ' + e)
        return null
    }
}

// ---- host objects ---------------------------------------------------------------
var objects = {}     // name -> { title, color, plugin }
var objectsDirtySet = {}
function touchObject(name) { objectsDirty = true; objectsDirtySet[name] = true }
function trackObject(name, hostObject) {
    if (!hostObject) return
    var o = objects[name] = { title: null, color: null }
    try {
        hostObject.mOnTitleChange = function (activeDevice, activeMapping, title) {
            o.title = title; touchObject(name)
        }
        hostObject.mOnColorChange = function (activeDevice, activeMapping, r, g, b, a, isActive) {
            o.color = [round4(r), round4(g), round4(b), round4(a)]; o.active = isActive; touchObject(name)
        }
    } catch (e) { }
}
var objectsDirty = false

function trackPluginIdentity(name, slotObject) {
    if (!slotObject || !('mOnChangePluginIdentity' in slotObject)) return
    var o = objects[name] || (objects[name] = {})
    slotObject.mOnChangePluginIdentity = function (activeDevice, activeMapping, pluginName, pluginVendor, pluginVersion, formatVersion) {
        o.plugin = { name: pluginName, vendor: pluginVendor, version: pluginVersion, format: formatVersion }
        touchObject(name)
    }
}

function bindPluginSlot(prefix, slotObject) {
    if (!slotObject) return
    trackObject(prefix, slotObject)
    trackPluginIdentity(prefix, slotObject)
    bindValue(prefix + '.on', slotObject.mOn)
    bindValue(prefix + '.bypass', slotObject.mBypass)
    bindValue(prefix + '.edit', slotObject.mEdit)
    bindValue(prefix + '.aread', slotObject.mAutomationRead)
    bindValue(prefix + '.awrite', slotObject.mAutomationWrite)
}

function bindChannelBasics(prefix, ch) {
    trackObject(prefix, ch)
    var v = ch.mValue
    bindValue(prefix + '.volume', v.mVolume)
    bindValue(prefix + '.pan', v.mPan)
    bindValue(prefix + '.mute', v.mMute)
    bindValue(prefix + '.solo', v.mSolo)
    bindValue(prefix + '.rec', v.mRecordEnable)
    bindValue(prefix + '.monitor', v.mMonitorEnable)
    bindValue(prefix + '.selected', v.mSelected)
    bindValue(prefix + '.aread', v.mAutomationRead)
    bindValue(prefix + '.awrite', v.mAutomationWrite)
    bindValue(prefix + '.editor', v.mEditorOpen)
    bindValue(prefix + '.instrument', v.mInstrumentOpen)
    bindValue(prefix + '.meter', v.mVUMeter, { meter: true })
    bindValue(prefix + '.meterpeak', v.mVUMeterPeak, { meter: true })
    bindValue(prefix + '.meterclip', v.mVUMeterClip, { meter: true })
    for (var i = 0; i < CFG.SEND_SLOTS; ++i) {
        var snd = null
        try { snd = ch.mSends.getByIndex(i) } catch (e) { snd = null }
        if (!snd) continue
        trackObject(prefix + '.send' + i, snd)
        bindValue(prefix + '.send' + i + '.on', snd.mOn)
        bindValue(prefix + '.send' + i + '.level', snd.mLevel)
        bindValue(prefix + '.send' + i + '.prepost', snd.mPrePost)
    }
}

// Transport
var transport = host.mTransport
var tv = transport.mValue
bindValue('transport.play', tv.mStart)
bindValue('transport.stop', tv.mStop)
bindValue('transport.record', tv.mRecord)
bindValue('transport.rewind', tv.mRewind)
bindValue('transport.forward', tv.mForward)
bindValue('transport.cycle', tv.mCycleActive)
bindValue('transport.metronome', tv.mMetronomeActive)
bindValue('transport.clicklevel', tv.mMetronomeClickLevel)

var tstate = { tempo: null, cursor: null, cursorFormat: null, left: null, right: null, cursor2: null, cursor2Format: null }
var tdirty = false
var td = transport.mTimeDisplay
td.mOnChangeTempoBPM = function (activeDevice, activeMapping, bpm) { tstate.tempo = bpm; tdirty = true }
td.mPrimary.mTransportLocator.mOnChange = function (activeDevice, activeMapping, time, format) {
    tstate.cursor = time; tstate.cursorFormat = format; tdirty = true
}
td.mPrimary.mCycleLocatorLeft.mOnChange = function (activeDevice, activeMapping, time, format) {
    tstate.left = time; tdirty = true
}
td.mPrimary.mCycleLocatorRight.mOnChange = function (activeDevice, activeMapping, time, format) {
    tstate.right = time; tdirty = true
}
td.mSecondary.mTransportLocator.mOnChange = function (activeDevice, activeMapping, time, format) {
    tstate.cursor2 = time; tstate.cursor2Format = format; tdirty = true
}

// Mixer bank
var bank = host.mMixConsole.makeMixerBankZone('MCP Bank')
    .includeAudioChannels().includeInstrumentChannels().includeSamplerChannels()
    .includeMIDIChannels().includeFXChannels().includeGroupChannels().includeVCAChannels()
var bankChannels = []
for (var bi = 0; bi < CFG.BANK_SIZE; ++bi) {
    var bch = bank.makeMixerBankChannel()
    bankChannels.push(bch)
    bindChannelBasics('ch' + bi, bch)
}
// second bank for input/output busses (small)
var ioBank = host.mMixConsole.makeMixerBankZone('MCP IO').includeInputChannels().includeOutputChannels()
var ioChannels = []
for (var oi = 0; oi < 8; ++oi) {
    var och = ioBank.makeMixerBankChannel()
    ioChannels.push(och)
    trackObject('io' + oi, och)
    bindValue('io' + oi + '.volume', och.mValue.mVolume)
    bindValue('io' + oi + '.pan', och.mValue.mPan)
    bindValue('io' + oi + '.mute', och.mValue.mMute)
    bindValue('io' + oi + '.solo', och.mValue.mSolo)
    bindValue('io' + oi + '.meter', och.mValue.mVUMeter, { meter: true })
    bindValue('io' + oi + '.meterpeak', och.mValue.mVUMeterPeak, { meter: true })
}

// Selected track: full channel strip
var sel = host.mTrackSelection.mMixerChannel
bindChannelBasics('sel', sel)
// Pre filter
var pf = sel.mPreFilter
if (pf) {
    bindValue('sel.pre.bypass', pf.mBypass)
    bindValue('sel.pre.gain', pf.mGain)
    bindValue('sel.pre.phase', pf.mPhaseSwitch)
    bindValue('sel.pre.hcon', pf.mHighCutOn)
    bindValue('sel.pre.hcfreq', pf.mHighCutFreq)
    bindValue('sel.pre.hcslope', pf.mHighCutSlope)
    bindValue('sel.pre.lcon', pf.mLowCutOn)
    bindValue('sel.pre.lcfreq', pf.mLowCutFreq)
    bindValue('sel.pre.lcslope', pf.mLowCutSlope)
}
// Channel EQ
var eq = sel.mChannelEQ
if (eq) {
    var bands = [eq.mBand1, eq.mBand2, eq.mBand3, eq.mBand4]
    for (var eb = 0; eb < 4; ++eb) {
        var band = bands[eb]
        if (!band) continue
        var p = 'sel.eq' + (eb + 1)
        bindValue(p + '.on', band.mOn)
        bindValue(p + '.gain', band.mGain)
        bindValue(p + '.freq', band.mFreq)
        bindValue(p + '.q', band.mQ)
        bindValue(p + '.type', band.mFilterType)
    }
}
// Quick controls of the selected track
for (var qi = 0; qi < CFG.QUICK_CONTROLS; ++qi) {
    var qc = sel.mQuickControls.getByIndex(qi)
    if (qc) bindValue('sel.qc' + qi, qc)
}
// Focused quick controls
var fqc = host.mFocusedQuickControls
if (fqc) {
    for (var fi = 0; fi < CFG.QUICK_CONTROLS; ++fi) {
        var f = fqc.getByIndex(fi)
        if (f) bindValue('fqc' + fi, f)
    }
    bindValue('fqc.locked', fqc.mFocusLockedValue)
}
// Insert slots
var inserts = sel.mInsertAndStripEffects
var insertViewers = []
for (var ii = 0; ii < CFG.INSERT_SLOTS; ++ii) {
    var viewer = inserts.makeInsertEffectViewer('MCP Insert ' + ii).accessSlotAtIndex(ii)
    insertViewers.push(viewer)
    bindPluginSlot('sel.ins' + ii, viewer)
}
// Strip modules
var strip = inserts.mStripEffects
if (strip) {
    bindPluginSlot('sel.strip.gate', strip.mGate)
    bindPluginSlot('sel.strip.comp', strip.mCompressor)
    bindPluginSlot('sel.strip.tools', strip.mTools)
    bindPluginSlot('sel.strip.sat', strip.mSaturator)
    bindPluginSlot('sel.strip.limit', strip.mLimiter)
}
// Instrument slot (+ parameter bank)
var inst = sel.mInstrumentPluginSlot
if (inst) {
    bindPluginSlot('sel.inst', inst)
    if (inst.mParameterBankZone) {
        for (var pi = 0; pi < CFG.INSTRUMENT_PARAMS; ++pi)
            bindValue('sel.inst.p' + pi, inst.mParameterBankZone.makeParameterValue())
    }
}

// Control room (Cubase Pro / Nuendo)
var cr = host.mControlRoom
if (cr && cr.mMainChannel) {
    var m = cr.mMainChannel
    bindValue('cr.level', m.mLevelValue)
    bindValue('cr.mute', m.mMuteValue)
    bindValue('cr.dim', m.mDimActiveValue)
    bindValue('cr.click', m.mMetronomeClickActiveValue)
    bindValue('cr.clicklevel', m.mMetronomeClickLevelValue)
    bindValue('cr.reflevel', m.mReferenceLevelEnabledValue)
    bindValue('cr.talkback', cr.mTalkbackActiveValue)
}

// Mouse cursor value
if (host.mMouseCursor) {
    bindValue('mouse.value', host.mMouseCursor.mValueUnderMouse)
    bindValue('mouse.locked', host.mMouseCursor.mValueLocked)
}

// ---- actions ----------------------------------------------------------------------
var actions = {
    bank_next: bank.mAction.mNextBank,
    bank_prev: bank.mAction.mPrevBank,
    bank_reset: bank.mAction.mResetBank,
    bank_shift_left: bank.mAction.mShiftLeft,
    bank_shift_right: bank.mAction.mShiftRight,
    io_bank_next: ioBank.mAction.mNextBank,
    io_bank_prev: ioBank.mAction.mPrevBank,
    io_bank_reset: ioBank.mAction.mResetBank,
    track_next: host.mTrackSelection.mAction.mNextTrack,
    track_prev: host.mTrackSelection.mAction.mPrevTrack
}
if (inst && inst.mParameterBankZone && inst.mParameterBankZone.mAction) {
    actions.inst_params_next = inst.mParameterBankZone.mAction.mNextBank
    actions.inst_params_prev = inst.mParameterBankZone.mAction.mPrevBank
    actions.inst_params_reset = inst.mParameterBankZone.mAction.mResetBank
}
var bankOffset = 0

// ---- commands -----------------------------------------------------------------------
var COMMANDS = '__COMMANDS__'
var EXTRA_COMMANDS = '__EXTRA_COMMANDS__'
;(function () {
    var lines = COMMANDS.split('\n').concat(EXTRA_COMMANDS ? EXTRA_COMMANDS.split('\n') : [])
    for (var i = 0; i < lines.length; ++i) {
        if (!lines[i]) continue
        var parts = lines[i].split('\t')
        if (CFG.SKIP_COMMAND_CATEGORIES[parts[0]]) continue
        bindCommand(parts[0], parts[1])
    }
})()

// ---- DirectAccess ---------------------------------------------------------------------
var das = {}
function makeDA(name, hostObject) {
    try {
        if (host.makeDirectAccess && hostObject) das[name] = host.makeDirectAccess(hostObject)
    } catch (e) { log('DirectAccess ' + name + ' unavailable: ' + e) }
}
makeDA('selected', sel)
makeDA('mixconsole', host.mMixConsole)
makeDA('transport', transport)
makeDA('bank0', bankChannels[0])

var daChanges = {}
var daChangesCount = 0
function hookDA(name) {
    var da = das[name]
    da.mOnParameterChange = function (activeDevice, activeMapping, objectID, parameterTag) {
        var k = name + ':' + objectID
        if (!daChanges[k]) { daChanges[k] = {}; daChangesCount++ }
        daChanges[k][parameterTag] = 1
    }
    da.mOnObjectChange = function (activeDevice, activeMapping, objectID) {
        var k = name + ':' + objectID
        if (!daChanges[k]) { daChanges[k] = {}; daChangesCount++ }
        daChanges[k].obj = 1
    }
}
for (var dn in das) if (das.hasOwnProperty(dn)) hookDA(dn)

function getDA(name) {
    var da = das[name || 'selected']
    if (!da) throw new Error('DirectAccess "' + (name || 'selected') + '" not available (needs Cubase 13.0.50+ / API 1.2)')
    return da
}

function daNode(da, m, id) {
    var n = { id: id }
    try { n.title = da.getObjectTitle(m, id) } catch (e) { }
    try { if (da.getObjectTypeName) n.type = da.getObjectTypeName(m, id) } catch (e) { }
    try { n.name = da.getObjectUniqueName(m, id) } catch (e) { }
    try { n.uid = da.getObjectUniqueIDString(m, id) } catch (e) { }
    try { n.params = da.getNumberOfParameters(m, id) } catch (e) { }
    try { n.children = da.getNumberOfChildObjects(m, id) } catch (e) { }
    return n
}

function daTree(da, m, id, depth, budget) {
    var n = daNode(da, m, id)
    budget.n--
    if (depth > 0 && n.children > 0) {
        n.kids = []
        for (var i = 0; i < n.children && budget.n > 0; ++i) {
            var cid = da.getChildObjectID(m, id, i)
            n.kids.push(daTree(da, m, cid, depth - 1, budget))
        }
        if (n.kids.length < n.children) n.truncated = true
    }
    return n
}

function daParamInfo(da, m, id, tag, full) {
    var p = { tag: tag }
    try { p.title = da.getParameterTitle(m, id, tag, 128) } catch (e) { }
    try { p.value = round4(da.getParameterProcessValue(m, id, tag)) } catch (e) { }
    try { p.display = da.getParameterDisplayValue(m, id, tag) } catch (e) { }
    try { p.units = da.getParameterDisplayUnits(m, id, tag) } catch (e) { }
    if (full) {
        try { p.default = round4(da.getParameterDefaultProcessValue(m, id, tag)) } catch (e) { }
        try { if (da.getParameterProcessValueType) p.type = da.getParameterProcessValueType(m, id, tag) } catch (e) { }
        try { if (da.isParameterAutomatable) p.automatable = da.isParameterAutomatable(m, id, tag) } catch (e) { }
    }
    return p
}

function nonEmptyObjects() {
    var out = {}
    for (var k in objects) if (objects.hasOwnProperty(k)) {
        var o = objects[k]
        if (o.title || o.color || o.plugin) out[k] = o
    }
    return out
}

// ---- state snapshot helpers ---------------------------------------------------------
function slotState(s) {
    return [s.id, round4(s.v), s.d, s.u, s.t, s.vt]
}

function transportState() {
    return { tempo: tstate.tempo, cursor: tstate.cursor, cursor_format: tstate.cursorFormat,
        left: tstate.left, right: tstate.right, cursor2: tstate.cursor2, cursor2_format: tstate.cursor2Format }
}

function features() {
    var f = { direct_access: !!host.makeDirectAccess, idle: idleSeen, json_native: (typeof JSON !== 'undefined') }
    try { f.app = midiremote_api.mDefaults.getAppName() } catch (e) { }
    try { f.app_version = midiremote_api.mDefaults.mAppVersion.getVersionString() } catch (e) { }
    try { f.insert_slots = midiremote_api.mDefaults.getNumberOfInsertEffectSlots() } catch (e) { }
    try { f.send_slots = midiremote_api.mDefaults.getNumberOfSendSlots() } catch (e) { }
    try { f.quick_controls = midiremote_api.mDefaults.getNumberOfQuickControls() } catch (e) { }
    try { f.max_gain = midiremote_api.mDefaults.getMaxGainFactor() } catch (e) { }
    var dl = []
    for (var k in das) if (das.hasOwnProperty(k)) dl.push(k)
    f.direct_access_roots = dl
    return f
}

// ---- request handling ---------------------------------------------------------------
var pendingRelease = []   // command slots to set back to 0 on next idle

function findSlot(p) {
    var s = null
    if (typeof p.id === 'number') s = slots[p.id]
    else if (p.key) s = slotByKey[p.key]
    if (!s) throw new Error('unknown slot ' + (p.key || p.id))
    return s
}

var handlers = {
    ping: function (p) { return { pong: true, version: SCRIPT_VERSION } },

    hello: function (p) {
        return { script_version: SCRIPT_VERSION, protocol: PROTOCOL_VERSION, slots: slots.length,
            bank_size: CFG.BANK_SIZE, send_slots: CFG.SEND_SLOTS, insert_slots: CFG.INSERT_SLOTS,
            quick_controls: CFG.QUICK_CONTROLS, instrument_params: CFG.INSTRUMENT_PARAMS,
            bank_offset: bankOffset, features: features() }
    },

    // slot table: [key, kind] by id, paged
    slots: function (p) {
        var off = p.offset || 0, lim = p.limit || 400, out = []
        for (var i = off; i < Math.min(slots.length, off + lim); ++i) out.push([slots[i].key, slots[i].kind])
        return { offset: off, total: slots.length, slots: out }
    },

    get: function (p) {
        var out = []
        if (p.keys) for (var i = 0; i < p.keys.length; ++i) { var s = slotByKey[p.keys[i]]; if (s) out.push(slotState(s)) }
        if (p.prefix) for (var j = 0; j < slots.length; ++j) if (slots[j].kind === 'v' && slots[j].key.indexOf(p.prefix) === 0) out.push(slotState(slots[j]))
        return { slots: out }
    },

    dump: function (p) {
        var out = []
        for (var j = 0; j < slots.length; ++j) {
            var s = slots[j]
            if (s.kind !== 'v') continue
            if (s.meter && !p.meters) continue
            if (s.v === null && s.d === null && s.t === null) continue
            out.push(slotState(s))
        }
        return { slots: out, objects: nonEmptyObjects(), transport: transportState(), bank_offset: bankOffset }
    },

    meters: function (p) {
        var out = []
        for (var j = 0; j < slots.length; ++j) if (slots[j].meter) out.push([slots[j].id, round4(slots[j].v)])
        return { slots: out }
    },

    objects: function (p) { return { objects: nonEmptyObjects() } },

    transport: function (p) { return transportState() },

    set: function (p, dev, m) {
        var s = findSlot(p)
        var v = Math.max(0, Math.min(1, Number(p.value)))
        s.cv.setProcessValue(dev, v)
        return { id: s.id, value: v }
    },

    cmd: function (p, dev, m) {
        var s = commandIndex[p.category + '\u0001' + p.command]
        if (!s) throw new Error('command not bound: ' + p.category + '/' + p.command)
        if (p.check && s.binding && s.binding.canPerform) return { can_perform: s.binding.canPerform(m) }
        s.cv.setProcessValue(dev, 1)
        pendingRelease.push(s)
        return { id: s.id, triggered: true }
    },

    can_perform: function (p, dev, m) {
        var s = commandIndex[p.category + '\u0001' + p.command]
        if (!s) return { bound: false }
        if (!s.binding || !s.binding.canPerform) return { bound: true, can_perform: null }
        return { bound: true, can_perform: s.binding.canPerform(m) }
    },

    action: function (p, dev, m) {
        var a = actions[p.name]
        if (!a) throw new Error('unknown action ' + p.name)
        var n = p.times || 1
        for (var i = 0; i < n; ++i) a.trigger(m)
        if (p.name === 'bank_next') bankOffset += CFG.BANK_SIZE * n
        else if (p.name === 'bank_prev') bankOffset = Math.max(0, bankOffset - CFG.BANK_SIZE * n)
        else if (p.name === 'bank_reset') bankOffset = 0
        else if (p.name === 'bank_shift_right') bankOffset += n
        else if (p.name === 'bank_shift_left') bankOffset = Math.max(0, bankOffset - n)
        return { action: p.name, times: n, bank_offset: bankOffset }
    },

    tempo: function (p, dev, m) {
        td.setTempoBPM(m, Number(p.bpm))
        return { tempo: Number(p.bpm) }
    },

    time: function (p, dev, m) {
        var disp = (p.display === 'secondary') ? td.mSecondary : td.mPrimary
        var loc = p.locator || 'cursor'
        var target = loc === 'left' ? disp.mCycleLocatorLeft : (loc === 'right' ? disp.mCycleLocatorRight : disp.mTransportLocator)
        target.setTime(m, String(p.value))
        return { locator: loc, value: String(p.value) }
    },

    step: function (p, dev, m) {
        // increment / decrement a host value by its native step (e.g. +1 dB)
        var s = findSlot(p)
        if (!s.hv) throw new Error('slot has no host value')
        var n = Math.abs(p.steps || 1)
        for (var i = 0; i < n; ++i) { if ((p.steps || 1) > 0) s.hv.increment(m); else s.hv.decrement(m) }
        return { id: s.id, steps: p.steps || 1 }
    },

    runtime_ids: function (p, dev, m) {
        var out = { selected: null, channels: [] }
        try { out.selected = sel.getRuntimeID(m) } catch (e) { }
        try { out.selected_uid = sel.getUniqueIDString(m) } catch (e) { }
        for (var i = 0; i < bankChannels.length; ++i) {
            var r = {}
            try { r.rid = bankChannels[i].getRuntimeID(m) } catch (e) { }
            try { r.uid = bankChannels[i].getUniqueIDString(m) } catch (e) { }
            out.channels.push(r)
        }
        return out
    },

    // ---- DirectAccess ----
    da_roots: function (p, dev, m) {
        var out = {}
        for (var k in das) if (das.hasOwnProperty(k)) {
            try { out[k] = daNode(das[k], m, das[k].getBaseObjectID(m)) } catch (e) { out[k] = { error: String(e) } }
        }
        return out
    },

    da_tree: function (p, dev, m) {
        var da = getDA(p.root)
        var id = (typeof p.object_id === 'number') ? p.object_id : da.getBaseObjectID(m)
        if (id < 0) throw new Error('no base object (is a track selected?)')
        return daTree(da, m, id, (typeof p.depth === 'number') ? p.depth : 2, { n: p.max_nodes || 300 })
    },

    da_params: function (p, dev, m) {
        var da = getDA(p.root)
        var id = (typeof p.object_id === 'number') ? p.object_id : da.getBaseObjectID(m)
        var count = da.getNumberOfParameters(m, id)
        var off = p.offset || 0, lim = p.limit || 64, out = []
        var filt = p.filter ? String(p.filter).toLowerCase() : null
        for (var i = 0; i < count && out.length < lim; ++i) {
            var tag = da.getParameterTagByIndex(m, id, i)
            if (filt) {
                var t = ''
                try { t = String(da.getParameterTitle(m, id, tag, 128)).toLowerCase() } catch (e) { }
                if (t.indexOf(filt) < 0) continue
            }
            if (off > 0) { off--; continue }
            out.push(daParamInfo(da, m, id, tag, !!p.full))
        }
        return { object: daNode(da, m, id), count: count, params: out }
    },

    da_get: function (p, dev, m) {
        var da = getDA(p.root)
        return daParamInfo(da, m, p.object_id, p.tag, true)
    },

    da_set: function (p, dev, m) {
        var da = getDA(p.root)
        if (typeof p.display === 'string') da.setParameterDisplayValue(m, p.object_id, p.tag, p.display)
        else {
            var v = Number(p.value)
            if (p.plain && da.convertParameterPlainToProcessValue) v = da.convertParameterPlainToProcessValue(m, p.object_id, p.tag, v)
            da.setParameterProcessValue(m, p.object_id, p.tag, Math.max(0, Math.min(1, v)))
        }
        return daParamInfo(da, m, p.object_id, p.tag, false)
    },

    da_find: function (p, dev, m) {
        // breadth-first search for objects whose title/type/name contains text
        var da = getDA(p.root)
        var q = String(p.query || '').toLowerCase()
        var start = (typeof p.object_id === 'number') ? p.object_id : da.getBaseObjectID(m)
        var queue = [[start, 0]], seen = 0, found = []
        var maxDepth = p.depth || 6
        while (queue.length && seen < (p.max_nodes || 2000) && found.length < (p.limit || 50)) {
            var it = queue.shift(); seen++
            var n = daNode(da, m, it[0])
            var hay = ((n.title || '') + ' ' + (n.type || '') + ' ' + (n.name || '')).toLowerCase()
            if (!q || hay.indexOf(q) >= 0) { n.depth = it[1]; found.push(n) }
            if (it[1] < maxDepth) for (var i = 0; i < (n.children || 0); ++i) queue.push([da.getChildObjectID(m, it[0], i), it[1] + 1])
        }
        return { found: found, visited: seen }
    },

    da_plugins: function (p, dev, m) {
        var da = getDA(p.root)
        var pm = da.mPluginManager
        if (!pm || !pm.getNumberOfPluginCollections) throw new Error('plugin manager needs MIDI Remote API 1.3 (Cubase 15)')
        var n = pm.getNumberOfPluginCollections(m, p.object_id)
        var res = { collections: n, default: pm.getIndexOfDefaultPluginCollection(m, p.object_id),
            active: pm.getIndexOfActivePluginCollection(m, p.object_id), items: [] }
        var idx = (typeof p.collection === 'number') ? p.collection : res.active
        if (idx < 0) idx = res.default
        res.collection = idx
        if (idx >= 0 && idx < n) {
            var c = pm.getPluginCollectionByIndex(m, p.object_id, idx)
            res.name = c.mName
            var q = p.filter ? String(p.filter).toLowerCase() : null
            var off = p.offset || 0, lim = p.limit || 200
            for (var i = 0; i < c.mEntries.length && res.items.length < lim; ++i) {
                var e = c.mEntries[i], o = {}
                for (var k in e) if (e.hasOwnProperty(k) && typeof e[k] !== 'function') o[k.replace(/^m/, '').toLowerCase()] = e[k]
                if (q && J.stringify(o).toLowerCase().indexOf(q) < 0) continue
                if (off > 0) { off--; continue }
                res.items.push(o)
            }
            res.total = c.mEntries.length
        }
        return res
    },

    // plug-in slots below an object: nodes that expose plug-in collections
    da_plugin_slots: function (p, dev, m) {
        var da = getDA(p.root)
        var pm = da.mPluginManager
        if (!pm || !pm.getNumberOfPluginCollections) throw new Error('plugin manager needs MIDI Remote API 1.3 (Cubase 15)')
        var start = (typeof p.object_id === 'number') ? p.object_id : da.getBaseObjectID(m)
        var queue = [[start, 0]], out = [], seen = 0
        while (queue.length && seen < (p.max_nodes || 1500)) {
            var it = queue.shift(); seen++
            var id = it[0], n = 0
            try { n = pm.getNumberOfPluginCollections(m, id) } catch (e) { n = 0 }
            if (n > 0) {
                var node = daNode(da, m, id)
                node.depth = it[1]
                node.collections = n
                // sample a few entries to classify the slot (instrument vs effect)
                var sub = {}
                try {
                    var ci = pm.getIndexOfDefaultPluginCollection(m, id)
                    if (ci < 0) ci = 0
                    var c = pm.getPluginCollectionByIndex(m, id, ci)
                    for (var k = 0; k < Math.min(25, c.mEntries.length); ++k) sub[String(c.mEntries[k].mSubCategories || '').split('|')[0]] = 1
                } catch (e) { }
                var kinds = []
                for (var sk in sub) if (sub.hasOwnProperty(sk) && sk) kinds.push(sk)
                node.entry_kinds = kinds
                out.push(node)
            }
            if (it[1] < (p.depth || 5)) {
                var cc = 0
                try { cc = da.getNumberOfChildObjects(m, id) } catch (e) { }
                for (var i = 0; i < cc; ++i) queue.push([da.getChildObjectID(m, id, i), it[1] + 1])
            }
        }
        return { slots: out, visited: seen }
    },

    // search all collections of a slot for plug-ins matching a query
    da_search_plugins: function (p, dev, m) {
        var da = getDA(p.root)
        var pm = da.mPluginManager
        var q = String(p.query || '').toLowerCase()
        var kind = p.kind ? String(p.kind).toLowerCase() : null
        var n = pm.getNumberOfPluginCollections(m, p.object_id)
        var res = [], seenUID = {}
        for (var ci = 0; ci < n && res.length < (p.limit || 50); ++ci) {
            var c = pm.getPluginCollectionByIndex(m, p.object_id, ci)
            for (var i = 0; i < c.mEntries.length && res.length < (p.limit || 50); ++i) {
                var e = c.mEntries[i]
                var sub = String(e.mSubCategories || '')
                if (kind && sub.toLowerCase().indexOf(kind) < 0) continue
                var hay = (String(e.mPluginName) + ' ' + String(e.mPluginVendor) + ' ' + sub).toLowerCase()
                if (q && hay.indexOf(q) < 0) continue
                if (seenUID[e.mPluginUID]) continue
                seenUID[e.mPluginUID] = 1
                res.push({ name: e.mPluginName, vendor: e.mPluginVendor, version: e.mPluginVersion,
                    sub_categories: sub, uid: e.mPluginUID, collection: c.mName, path: e.mCollectionPath })
            }
        }
        return { items: res, collections: n }
    },

    // fire-and-forget parameter write used while recording automation
    da_set_fast: function (p, dev, m) {
        var da = getDA(p.root)
        da.setParameterProcessValue(m, p.object_id, p.tag, Math.max(0, Math.min(1, Number(p.value))))
        return null
    },

    da_load_plugin: function (p, dev, m) {
        var da = getDA(p.root)
        var ok = da.mPluginManager.trySetSlotPlugin(m, p.object_id, String(p.uid), p.dont_ask !== false)
        return { ok: ok }
    },

    da_reset_plugin: function (p, dev, m) {
        var da = getDA(p.root)
        da.mPluginManager.resetSlotPlugin(m, p.object_id)
        return { ok: true }
    },

    da_mixer: function (p, dev, m) {
        var da = getDA(p.root)
        var id = (typeof p.object_id === 'number') ? p.object_id : da.getBaseObjectID(m)
        var r = {}
        try { r.visible = da.isMixerChannelVisible(m, id) } catch (e) { }
        try { r.index = da.getMixerChannelIndex(m, id) } catch (e) { }
        try { r.zone = da.getMixerChannelZone(m, id) } catch (e) { }
        try { r.color = da.getObjectColor(m, id) } catch (e) { }
        return r
    },

    da_changes: function (p) {
        var out = daChanges
        daChanges = {}; daChangesCount = 0
        return { changes: out }
    }
}

// ---- SysEx framing --------------------------------------------------------------------
var inbox = []
var partial = {}
var outQueue = []
var idleSeen = false
var idleCounter = 0

function enqueueMessage(type, id, obj) {
    var txt = toAsciiJson(obj)
    var total = Math.max(1, Math.ceil(txt.length / CFG.CHUNK))
    for (var seq = 0; seq < total; ++seq) {
        var part = txt.substr(seq * CFG.CHUNK, CFG.CHUNK)
        var bytes = [0xF0, 0x7D, 0x43, 0x4D, type, (id >> 7) & 0x7F, id & 0x7F,
            (seq >> 7) & 0x7F, seq & 0x7F, (total >> 7) & 0x7F, total & 0x7F]
        for (var i = 0; i < part.length; ++i) bytes.push(part.charCodeAt(i) & 0x7F)
        bytes.push(0xF7)
        outQueue.push(bytes)
    }
}

function flushOut(dev, max) {
    var n = 0
    while (outQueue.length && n < max) {
        midiOut.sendMidi(dev, outQueue.shift())
        n++
    }
}

midiIn.mOnSysex = function (activeDevice, message) {
    if (message.length < 12 || message[1] !== 0x7D || message[2] !== 0x43 || message[3] !== 0x4D) return
    var type = message[4]
    if (type !== 0x01) return
    var id = (message[5] << 7) | message[6]
    var seq = (message[7] << 7) | message[8]
    var total = (message[9] << 7) | message[10]
    var txt = ''
    for (var i = 11; i < message.length - 1; ++i) txt += String.fromCharCode(message[i])
    var buf = partial[id]
    if (!buf || seq === 0) buf = partial[id] = { parts: [], got: 0, total: total }
    if (buf.parts[seq] === undefined) { buf.parts[seq] = txt; buf.got++ }
    if (buf.got < buf.total) return
    delete partial[id]
    var req
    try { req = J.parse(buf.parts.join('')) } catch (e) {
        enqueueMessage(0x02, id, { ok: false, error: 'bad json: ' + e }); flushOut(activeDevice, CFG.MAX_OUT_CHUNKS_PER_IDLE); return
    }
    inbox.push({ id: id, req: req })
    // Cubase 12 has no idle callback: process synchronously with the last known mapping
    if (!idleSeen && gMapping) processInbox(activeDevice, gMapping)
}

function processInbox(dev, m) {
    var budget = 48
    while (inbox.length && budget-- > 0) {
        var item = inbox.shift()
        var res
        try {
            var h = handlers[item.req.op]
            if (!h) throw new Error('unknown op ' + item.req.op)
            res = { ok: true, result: h(item.req, dev, m) }
        } catch (e) {
            res = { ok: false, error: String(e && e.message ? e.message : e) }
        }
        if (!item.req.nr || !res.ok) enqueueMessage(0x02, item.id, res)
    }
    flushOut(dev, CFG.MAX_OUT_CHUNKS_PER_IDLE)
}

var eventsEnabled = true
var eventSeq = 0

function emitEvents(dev) {
    if (!eventsEnabled) return
    if (!dirtyCount && !tdirty && !objectsDirty) return
    var ev = { ev: 'state' }
    if (dirtyCount) {
        var arr = []
        for (var k in dirty) if (dirty.hasOwnProperty(k)) arr.push(slotState(slots[Number(k)]))
        ev.slots = arr
        dirty = {}; dirtyCount = 0
    }
    if (tdirty) { ev.transport = transportState(); tdirty = false }
    if (objectsDirty) {
        var od = {}
        for (var on in objectsDirtySet) if (objectsDirtySet.hasOwnProperty(on)) od[on] = objects[on]
        ev.objects = od
        objectsDirty = false; objectsDirtySet = {}
    }
    eventSeq = (eventSeq + 1) & 0x3FFF
    enqueueMessage(0x03, eventSeq, ev)
}

page.mOnActivate = function (activeDevice, activeMapping) {
    gDevice = activeDevice; gMapping = activeMapping
    for (var k in das) if (das.hasOwnProperty(k)) { try { das[k].activate(activeMapping) } catch (e) { log('da activate ' + k + ': ' + e) } }
    log('activated, ' + slots.length + ' slots')
    enqueueMessage(0x03, 0, { ev: 'hello', script_version: SCRIPT_VERSION, slots: slots.length })
    flushOut(activeDevice, CFG.MAX_OUT_CHUNKS_PER_IDLE)
}

page.mOnDeactivate = function (activeDevice, activeMapping) {
    for (var k in das) if (das.hasOwnProperty(k)) { try { das[k].deactivate(activeMapping) } catch (e) { } }
}

page.mOnIdle = function (activeDevice, activeMapping) {
    idleSeen = true
    gDevice = activeDevice; gMapping = activeMapping
    // release momentary command triggers from the previous idle
    if (pendingRelease.length) {
        for (var i = 0; i < pendingRelease.length; ++i) pendingRelease[i].cv.setProcessValue(activeDevice, 0)
        pendingRelease = []
    }
    if (inbox.length) processInbox(activeDevice, activeMapping)
    idleCounter++
    if (idleCounter % CFG.EVENT_INTERVAL_IDLES === 0) emitEvents(activeDevice)
    flushOut(activeDevice, CFG.MAX_OUT_CHUNKS_PER_IDLE)
}

handlers.events = function (p) { eventsEnabled = !!p.enabled; return { enabled: eventsEnabled } }
