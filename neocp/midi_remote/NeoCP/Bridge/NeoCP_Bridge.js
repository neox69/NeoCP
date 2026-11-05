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

var SCRIPT_VERSION = "1.3.4"
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
var COMMANDS = "AddTrack\tOpenDialog\nAddTrack\tArranger\nAddTrack\tAudio\nAddTrack\tAudio Mono\nAddTrack\tChord\nAddTrack\tFX Channel\nAddTrack\tFolder\nAddTrack\tGroup Channel\nAddTrack\tInstrument\nAddTrack\tMarker\nAddTrack\tMIDI\nAddTrack\tRuler\nAddTrack\tSampler\nAddTrack\tSignature\nAddTrack\tTempo\nAddTrack\tTransposeTrack\nAddTrack\tFrom Track Presets\nAddTrack\tVCA Fader\nAddTrack\tVideo\nADM Editor\tAdd Bed\nADM Editor\tAdd Object\nADM Editor\tADM Editor...\nADM Editor\tApply Source Track Name\nADM Editor\tCreate objects from selected tracks\nADM Editor\tOpen Group Editor\nADM Editor\tOpen Trim and Downmix Editor\nADM Editor\tRemove Selected Audio Object\nAnalyze\tSpectrum Analyzer\nAnalyze\tStatistics\nArranger\tActivate\nArranger\tFirst Chain Step\nArranger\tFirst Repeat\nArranger\tLast Chain Step\nArranger\tLast Repeat\nArranger\tNext Chain Step\nArranger\tPrevious Chain Step\nArranger\tTrigger Arranger Event 1\nArranger\tTrigger Arranger Event 2\nArranger\tTrigger Arranger Event 3\nArranger\tTrigger Arranger Event 4\nArranger\tTrigger Arranger Event 5\nArranger\tTrigger Arranger Event 6\nArranger\tTrigger Arranger Event 7\nArranger\tTrigger Arranger Event 8\nArranger\tTrigger Arranger Event 9\nArranger\tTrigger Arranger Event 10\nArranger\tTrigger Arranger Event 11\nArranger\tTrigger Arranger Event 12\nArranger\tTrigger Arranger Event 13\nArranger\tTrigger Arranger Event 14\nArranger\tTrigger Arranger Event 15\nArranger\tTrigger Arranger Event 16\nArranger\tTrigger Arranger Event 17\nArranger\tTrigger Arranger Event 18\nArranger\tTrigger Arranger Event 19\nArranger\tTrigger Arranger Event 20\nAudio\tAdjust Fades to Range\nAudio\tApply Standard Fade In\nAudio\tApply Standard Fade Out\nAudio\tAuto-Grid\nAudio\tBounce\nAudio\tClose Gaps (Crossfade)\nAudio\tClose Gaps\nAudio\tConstrain Delay Compensation\nAudio\tCopy Warp Markers from Selected Event\nAudio\tCreate Sampler Track\nAudio\tCrossfade\nAudio\tDecrement Event Volume\nAudio\tDecrement Fade In Length\nAudio\tDecrement Fade Out Length\nAudio\tDelete Overlaps\nAudio\tDetect Silence\nAudio\tDisable/Enable Track\nAudio\tDissolve Part\nAudio\tEvent or Range as Region\nAudio\tEvents from Regions\nAudio\tTo Origin\nAudio\tEvents to Part\nAudio\tFade In to Cursor\nAudio\tFade In to Range Start\nAudio\tFade Out to Cursor\nAudio\tFade Out from Range End\nAudio\tFind Selected in Pool\nAudio\tGenerate Harmony Voices\nAudio\tIncrement Event Volume\nAudio\tIncrement Fade In Length\nAudio\tIncrement Fade Out Length\nAudio\tInvert Phase On/Off\nAudio\tMake Extension from Selected Events Permanent\nAudio\tMinimize File\nAudio\tConvert Tracks: Mono to Multi-Channel\nAudio\tConvert Tracks: Multi-Channel to Mono\nAudio\tOpen Fade Editors\nAudio\tPaste Warp Markers to Selected Events\nAudio\tRemove Extension from Selected Events\nAudio\tRemove Fade In\nAudio\tRemove Fade Out\nAudio\tRemove Fades\nAudio\tRemove Volume Curve\nAudio\tSet Definition From Tempo\nAudio\tSet Tempo From Event\nAudio\tSnap Point to Cursor\nAudio\tStretch to Project Tempo\nAudio\tUpdate Origin\nAudio Alignment\tAdd Selection as Alignment Reference\nAudio Alignment\tAdd Selection as Alignment Target\nAudio Alignment\tAlign Audio\nAudio Alignment\tOpen Audio Alignment Panel\nAudio Alignment\tRemove Alignment Reference\nAudio Alignment\tRemove All Alignment Targets\nAudio Export\tIncrease Counter Start Value\nAudio Export\tPerform Audio Export\nAudio Export\tReset Counter Start Value\nAudio Export\tSync Channel Selection with Mixer\nAudio Performance\tReset Processing Overload Indicator\nAudio Realtime Processing\tCreate Warp Tabs from Hitpoints\nAudio Realtime Processing\tFlatten Realtime Processing\nAudio Realtime Processing\tUnstretch Audio\nAutomation\tAutomation Mode - Auto-Latch\nAutomation\tAutomation Mode - Cross-Over\nAutomation\tAutomation Mode - Touch\nAutomation\tAutomation Mode - Trim\nAutomation\tOpen Panel\nAutomation\tDelete All Automation in Project\nAutomation\tDelete Automation in Range\nAutomation\tDelete Automation of Selected Tracks\nAutomation\tFill Gaps\nAutomation\tFill Gaps on Selected Tracks\nAutomation\tFill Gaps with Current Value (Selected Tracks)\nAutomation\tFill Loop\nAutomation\tFill To End\nAutomation\tFill To Punch\nAutomation\tFill To Start\nAutomation\tFreeze All Trim Automation in Project\nAutomation\tFreeze Trim Automation of Selected Tracks\nAutomation\tHide All Automation\nAutomation\tHide Automation\nAutomation\tNext Automation Mode\nAutomation\tPunch Out of Latch Automation\nAutomation\tToggle Read Enable All Tracks\nAutomation\tToggle Read Enable Selected Tracks\nAutomation\tShow All - Used Only\nAutomation\tShow All EQ Automation\nAutomation\tShow All Insert Automation\nAutomation\tShow All Pan Automation\nAutomation\tShow All Send Automation\nAutomation\tShow All Used Automation\nAutomation\tShow All Volume Automation\nAutomation\tShow Automation\nAutomation\tShow Used Automation (Selected Tracks)\nAutomation\tSuspend Reading All\nAutomation\tSuspend Reading Dynamics\nAutomation\tSuspend Reading EQ\nAutomation\tSuspend Reading Inserts\nAutomation\tSuspend Reading Mute\nAutomation\tSuspend Reading Others\nAutomation\tSuspend Reading Pan\nAutomation\tSuspend Reading Sends\nAutomation\tSuspend Reading Volume\nAutomation\tSuspend Reading/Writing All\nAutomation\tSuspend Writing All\nAutomation\tSuspend Writing Dynamics\nAutomation\tSuspend Writing EQ\nAutomation\tSuspend Writing Inserts\nAutomation\tSuspend Writing Mute\nAutomation\tSuspend Writing Others\nAutomation\tSuspend Writing Pan\nAutomation\tSuspend Writing Sends\nAutomation\tSuspend Writing Volume\nAutomation\tUse Virgin Territories\nAutomation\tToggle Write Enable All Tracks\nAutomation\tToggle Write Enable Selected Tracks\nBeat Designer\tFill Loop with Pattern\nBeat Designer\tInsert Pattern at Cursor\nBeat Designer\tInsert Pattern at Left Locator\nBeat Designer\tInsert Subbank at Cursor\nBeat Designer\tInsert Subbank at Left Locator\nChannel & Track Visibility\tHideMuted\nChannel & Track Visibility\tHideSelected\nChannel & Track Visibility\tchannelOrganizerRedo\nChannel & Track Visibility\tShowAll\nChannel & Track Visibility\tShowConnected\nChannel & Track Visibility\tShowUsed\nChannel & Track Visibility\tShowPlaying\nChannel & Track Visibility\tShowCycle\nChannel & Track Visibility\tShowSelected\nChannel & Track Visibility\tShowMarked\nChannel & Track Visibility\tchannelOrganizerUndo\nChannel & Track Visibility\tCreate Track Visibility Configuration\nChannel & Track Visibility\tSync Visibility of Project and MixConsole: On/Off\nChannel & Track Visibility\tUpdate Track Visibility Configuration\nChannel & Track Visibility\tChannel and Rack Configuration 1\nChannel & Track Visibility\tChannel and Rack Configuration 2\nChannel & Track Visibility\tChannel and Rack Configuration 3\nChannel & Track Visibility\tChannel and Rack Configuration 4\nChannel & Track Visibility\tChannel and Rack Configuration 5\nChannel & Track Visibility\tChannel and Rack Configuration 6\nChannel & Track Visibility\tChannel and Rack Configuration 7\nChannel & Track Visibility\tChannel and Rack Configuration 8\nChannel Settings\tShow/Hide Direct Routing\nChannel Settings\tShow/Hide Output Chain\nChords\tNotes To Voices\nChords\tChord Editing - Add to Chord Track\nChords\tChord Editing - Drop 2\nChords\tChord Editing - Drop 2 + 4\nChords\tChord Editing - Drop 3\nChords\tChord Editing - Inversions: Move Down\nChords\tChord Editing - Inversions: Move Up\nChords\tChord Editing - Match with Chord Track\nChords\tChordPadSetup\nChords\tChord Pads - Fewer Tensions\nChords\tChord Pads - Latch Chords On/Off\nChords\tChord Pads - More Tensions\nChords\tChord Pads - Next Voicing\nChords\tChord Pads - Previous Voicing\nChords\tChord Pads - Show/Hide Editor/Assistant\nChords\tChord Pads - Transpose Down\nChords\tChord Pads - Transpose Up\nChords\tChords to MIDI\nChords\tMake Chords\nChords\tMap to Chord Track\nChords\tSet up Scales\nChords\tChordPad\nControl Room\tSwitch: AFL/PFL\nControl Room\tSwitch: Click Active\nControl Room\tSwitch: Activate Speakers\nControl Room\tSwitch: Activate Studio 1\nControl Room\tSwitch: Activate Studio 2\nControl Room\tSwitch: Activate Studio 3\nControl Room\tSwitch: Activate Studio 4\nControl Room\tSwitch: Listen Cancel\nControl Room\tSwitch: Dim Active\nControl Room\tSwitch: Listen Enable\nControl Room\tSwitch: Activate Phones\nControl Room\tSwitch: Reference Level Active\nControl Room\tSwitch: Source Select\nControl Room\tSwitch: Folddown Select 1\nControl Room\tSwitch: Folddown Select 2\nControl Room\tSwitch: Folddown Select 3\nControl Room\tSwitch: Folddown Select 4\nControl Room\tSwitch: Speakers Select 1\nControl Room\tSwitch: Speakers Select 2\nControl Room\tSwitch: Speakers Select 3\nControl Room\tSwitch: Speakers Select 4\nControl Room\tSwitch: Folddown Select\nControl Room\tSwitch: Speakers Select\nControl Room\tSpeaker Solo: Bottom Front Center\nControl Room\tSpeaker Solo: Bottom Front Left\nControl Room\tSpeaker Solo: Bottom Front Right\nControl Room\tSpeaker Solo: Cancel\nControl Room\tSpeaker Solo: Center\nControl Room\tSpeaker Solo: Front\nControl Room\tSpeaker Solo: Left\nControl Room\tSpeaker Solo: Left of Center\nControl Room\tSpeaker Solo: Left-Right\nControl Room\tSpeaker Solo: LFE\nControl Room\tSpeaker Solo: Rear\nControl Room\tSpeaker Solo: Rear to Front\nControl Room\tSpeaker Solo: Right\nControl Room\tSpeaker Solo: Right of Center\nControl Room\tSpeaker Solo: Side\nControl Room\tSpeaker Solo: Side Left\nControl Room\tSpeaker Solo: Side Right\nControl Room\tSpeaker Solo: Solo to Center\nControl Room\tSpeaker Solo: Surround Left\nControl Room\tSpeaker Solo: Surround Right\nControl Room\tSpeaker Solo: Top Back Center\nControl Room\tSpeaker Solo: Top Back Left\nControl Room\tSpeaker Solo: Top Back Right\nControl Room\tSpeaker Solo: Top Center\nControl Room\tSpeaker Solo: Top Front Center\nControl Room\tSpeaker Solo: Top Front Left\nControl Room\tSpeaker Solo: Top Front Right\nControl Room\tSpeaker Solo: Top Side Left\nControl Room\tSpeaker Solo: Top Side Right\nControl Room\tSwitch: Talkback Active\nCrossfade Editor\tPlay Crossfade\nCrossfade Editor\tPlay Fade In\nCrossfade Editor\tPlay Fade Out\nDevices\tVST Connections\nDevices\tVST Performance\nDevices\tControl Room Mixer\nDevices\tGeneric Remote\nDevices\tRecord Time Max\nDevices\tMIDI Device Manager\nDevices\tMIDI Remote Manager\nDevices\tMixer\nDevices\tMixer 2\nDevices\tMixer 3\nDevices\tMixer 4\nDevices\tMixConsole Lower Zone\nDevices\tMMC Master\nDevices\tVirtual Keyboard\nDevices\tShow Panel\nDevices\tSetup\nDevices\tPlug-in Information\nDevices\tTestMixerFDH\nDevices\tTime Display\nDevices\tVideo\nDevices\tVST Channel Load\nDevices\tVST Instruments\nDevices\tVST Network Monitor\nDevices\tPlug-in Manager\nDevices\tVST Routing\nDirect Offline Processing\tAdd Plug-in\nDirect Offline Processing\tAdd Process\nDirect Offline Processing\tApply\nDirect Offline Processing\tBank 1\nDirect Offline Processing\tBank 2\nDirect Offline Processing\tBank 3\nDirect Offline Processing\tBank 4\nDirect Offline Processing\tBypass Selected Process\nDirect Offline Processing\tDirect Offline Processing\nDirect Offline Processing\tDiscard\nDirect Offline Processing\tFavorite 1\nDirect Offline Processing\tFavorite 2\nDirect Offline Processing\tFavorite 3\nDirect Offline Processing\tFavorite 4\nDirect Offline Processing\tFavorite 5\nDirect Offline Processing\tFavorite 6\nDirect Offline Processing\tFavorite 7\nDirect Offline Processing\tFavorite 8\nDirect Offline Processing\tFavorite 9\nDirect Offline Processing\tMake All Permanent\nDirect Offline Processing\tToggle Auto Apply\nEdit\tAcoustic Feedback On/Off\nEdit\tActivate Next Part\nEdit\tActivate Previous Part\nEdit\tActivate/Deactivate\nEdit\tSelect All\nEdit\tSelect All on Tracks\nEdit\tClick Pattern Copy\nEdit\tApply Project Logical Preset\nEdit\tAuto Select Events under Cursor\nEdit\tAutoscroll\nEdit\tAutomation follows Events\nEdit\tClean Up Lanes\nEdit\tTo Real Copy\nEdit\tCopy\nEdit\tCopy A<->B Setting\nEdit\tCopy Click Pattern\nEdit\tCreate Tracks from Lanes\nEdit\tCrop Range\nEdit\tCut\nEdit\tCut Head\nEdit\tCut Tail\nEdit\tCut Time\nEdit\tUnmute All\nEdit\tDeactivate All Solo\nEdit\tDelete\nEdit\tDelete Time\nEdit\tDuplicate\nEdit\tEdit Channel Panner\nEdit\tEdit Channel Settings\nEdit\tEdit Info Line\nEdit\tEdit VST Instrument\nEdit\tEnlarge Range to Next Event\nEdit\tEnlarge Range to Next Hitpoint\nEdit\tEnlarge Range to Previous Event\nEdit\tEnlarge Range to Previous Hitpoint\nEdit\tEnlarge Selected Track\nEdit\tSelect Equal Pitch\nEdit\tSelect Same Pitch\nEdit\tMove End to Cursor\nEdit\tMove to Cursor\nEdit\tMove to Back\nEdit\tMove to Front\nEdit\tMove to Selected Track\nEdit\tSelect Events under Cursor\nEdit\tExpand/Reduce\nEdit\tFill Loop\nEdit\tFind Track/Channel\nEdit\tSelect from Cursor to End\nEdit\tSelect from Start to Cursor\nEdit\tGlobal Copy\nEdit\tGlue\nEdit\tGrid Type: Adapt to Zoom\nEdit\tGrid Type: Bar\nEdit\tGrid Type: Beat\nEdit\tGrid Type: Use Quantize\nEdit\tGroup\nEdit\tToggle Group Tracks\nEdit\tHistory\nEdit\tSelect in Loop\nEdit\tInsert Silence\nEdit\tInvert Selection\nEdit\tFlip/Invert\nEdit\tKey Commands\nEdit\tLeft Selection Side to Cursor\nEdit\tListen\nEdit\tLock\nEdit\tLock/Unlock Track\nEdit\tMonitor\nEdit\tMove Insert Cursor To Part Start\nEdit\tMute Events\nEdit\tMute\nEdit\tMute/Unmute Objects\nEdit\tSelect None\nEdit\tOpen\nEdit\tOpen Device Panel\nEdit\tPart/Clip Editing Mode: Active Part/Clip\nEdit\tPart/Clip Editing Mode: All Parts/Clips\nEdit\tPart/Clip Editing Mode: All Parts/Clips on Active Track\nEdit\tEdit Active Part Only\nEdit\tPaste\nEdit\tPaste at Origin\nEdit\tPaste Click Pattern to similar Selected\nEdit\tPaste Relative to Cursor\nEdit\tPaste Time\nEdit\tPaste Time at Origin\nEdit\tPaste to Matching Track Name\nEdit\tPreferences\nEdit\tPrimary Parameter: Decrease\nEdit\tPrimary Parameter: Fine Decrease\nEdit\tPrimary Parameter: Fine Increase\nEdit\tPrimary Parameter: Increase\nEdit\tInsert Bars\nEdit\tProcess Tempo\nEdit\tProfileManager\nEdit\tRange to Next Event\nEdit\tRange to Next Hitpoint\nEdit\tRange to Previous Event\nEdit\tRange to Previous Hitpoint\nEdit\tRead\nEdit\tRecord Enable\nEdit\tRedo\nEdit\tRename First Selected Track\nEdit\tRename\nEdit\tRename Selected Events\nEdit\tCreate Audio Click Track\nEdit\tCreate MIDI Click Track\nEdit\tRepeat\nEdit\tClick Pattern to Default\nEdit\tReset to Default\nEdit\tRight Selection Side to Cursor\nEdit\tRuler Display Format\nEdit\tRuler Mode: Bars+Beats Linear\nEdit\tRuler Mode: Time Linear\nEdit\tSave as Default\nEdit\tScale Assistant: Quantize Pitches\nEdit\tScale Assistant: Toggle Show Scale Note Guides\nEdit\tScale Assistant: Toggle Snap Live Input\nEdit\tScale Assistant: Toggle Snap Pitch Editing\nEdit\tSecondary Parameter: Decrease\nEdit\tSecondary Parameter: Fine Decrease\nEdit\tSecondary Parameter: Fine Increase\nEdit\tSecondary Parameter: Increase\nEdit\tSelect Controllers in Note Range\nEdit\tSelect Event\nEdit\tSelect Next Grid Type\nEdit\tSelect Next Snap Type\nEdit\tSelect Prev Grid Type\nEdit\tSelect Prev Snap Type\nEdit\tFreeze/Unfreeze Selected Tracks (with Current Settings)\nEdit\tFreeze/Unfreeze Selected Tracks\nEdit\tSet Spacer between Selected Events\nEdit\tProject Logical Editor\nEdit\tClick Pattern Visible\nEdit\tLane Display Type\nEdit\tSlip Event Content Left\nEdit\tSlip Event Content Right\nEdit\tSnap Off\nEdit\tSnap On\nEdit\tSnap On/Off\nEdit\tSnap Type: Events\nEdit\tSnap Type: Events + Cursor\nEdit\tSnap Type: Events + Grid + Cursor\nEdit\tSnap Type: Grid\nEdit\tSnap Type: Grid + Cursor\nEdit\tSnap Type: Grid Relative\nEdit\tSnap Type: Magnetic Cursor\nEdit\tSnap Type: Shuffle\nEdit\tSolo\nEdit\tSolo Defeat\nEdit\tSolo Editor Mode: Solo All Parts/Clips\nEdit\tSolo Editor Mode: Solo follows 'Part/Clip Editing Mode'\nEdit\tSolo Editor Mode: Toggle Modes\nEdit\tSolo Lane\nEdit\tSplit Range\nEdit\tSplit at Cursor\nEdit\tSplit Loop\nEdit\tStationary Cursor\nEdit\tSwitch MIDI Editor Grid Type\nEdit\tToggle A/B Setting\nEdit\tToggle Link Project and Lower Zone Editor Cursors\nEdit\tUndo\nEdit\tUngroup\nEdit\tUnlock\nEdit\tUnmute Events\nEdit\tWrite\nEditors\tEdit Inplace\nEditors\tOpen Audio Part Editor\nEditors\tOpen Audio Part Editor in Lower Zone\nEditors\tOpen Audio Part Editor in Window\nEditors\tOpen Drum Editor\nEditors\tOpen Drum Editor in Lower Zone\nEditors\tOpen Drum Editor in Window\nEditors\tOpen in separate Window/Lower Zone\nEditors\tOpen In-Place Editor\nEditors\tOpen Key Editor\nEditors\tOpen Key Editor in Lower Zone\nEditors\tOpen Key Editor in Window\nEditors\tOpen List Editor\nEditors\tOpen Sample Editor\nEditors\tOpen Sample Editor in Lower Zone\nEditors\tOpen Sample Editor in Window\nEditors\tOpen Score Editor\nEditors\tOpen Score Editor in Lower Zone\nEditors\tOpen Score Editor in Window\nEditors\tOpen/Close Editor\nEditors\tSet up Editor Preferences\nEditors\tShow/Hide Global Tracks in Editor\nEditors\tTrack Display Settings: Active Track Only\nEditors\tTrack Display Settings: All Visible Tracks\nEditors\tTrack Display Settings: Show/Hide Tracks in Editor\nEditors\tTrack Display Settings: Toggle Modes\nEditors\tVisibility: Keep Editor Contents on/off\nExport\tAAF\nFile\tImport from Audio CD\nFile\tImport Audio File\nFile\tImport Audio from Video\nFile\tExport Audio Mixdown\nFile\tBack up Project\nFile\tClose\nFile\tExport Pool\nFile\tImport Pool\nFile\tExport Key Command Assignments\nFile\tExport MIDI File\nFile\tImport MIDI File\nFile\tExport MIDI Loop\nFile\tImport MusicXML\nFile\tExport MusicXML\nFile\tNew\nFile\tNew Empty\nFile\tNew Library\nFile\tExport Notepad Data\nFile\tOpen\nFile\tOpen Library\nFile\tPageSetup\nFile\tPerform Export Selected Events\nFile\tPrint\nFile\tQuit\nFile\tRevert\nFile\tSave\nFile\tSave As\nFile\tSave as Template\nFile\tSave Pool\nFile\tSave New Version\nFile\tExport Scores\nFile\tExport Selected Events\nFile\tExport Selected Tracks\nFile\tExport Master Track\nFile\tImport Master Track\nFile\tImport Track Archive\nFile\tImport Tracks from Project\nFile\tImport Video File\nFile\tExport Video\nFile\tWrite MIDI File to Project Folder\nFocus\tBack\nFocus\tHit\nFocus\tEscape\nFocus\tFore\nGame Audio Connect\tTrigger Export to Game Audio Engine\nHeadTracking\tShow Dialog\nHitpoints\tCalculate\nHitpoints\tCreate Audio Slices from Hitpoints\nHitpoints\tCreate Markers from Hitpoints\nHitpoints\tDivide Audio Events at Hitpoints\nHitpoints\tRemove Hitpoints\nHub\tSteinberg Hub\nImport\tAAF\nInspector\tToggle Standard Section\nInspector\tToggle Chords Section\nInspector\tToggle Cue Sends Section\nInspector\tToggle User Panel Section\nInspector\tToggle Direct Routing Section\nInspector\tToggle EQs Section\nInspector\tToggle Expression Map Section\nInspector\tToggle Channel Section\nInspector\tToggle Inserts Section\nInspector\tToggle MIDI Inserts Section\nInspector\tToggle MIDI Modifiers Section\nInspector\tToggle MIDI Sends Section\nInspector\tToggle Note Expression Section\nInspector\tToggle Notepad Section\nInspector\tToggle Filters Section\nInspector\tToggle Quick Controls Section\nInspector\tToggle Routing Section\nInspector\tToggle Sends Section\nInspector\tToggle Strip Section\nInspector\tToggle Surround Pan Section\nInspector\tToggle Track Variations Section\nKey Commands\tOpen Key Commands Launcher\nMarker\tActivate Marker Track\nMarker\tAdd and Edit Cycle Marker\nMarker\tAdd and Edit Cycle Marker on Selected Track\nMarker\tAdd and Edit Position Marker\nMarker\tAdd and Edit Position Marker on Selected Track\nMarker\tInsert Cycle Marker\nMarker\tAdd Cycle Marker on Selected Track\nMarker\tInsert Marker\nMarker\tAdd Position Marker on Selected Track\nMedia\tAdd Favorite\nMedia\tInsert into Project at Cursor\nMedia\tInsert into Project at Left Locator\nMedia\tInsert into Project at Origin\nMedia\tBrowse: Back\nMedia\tBrowse: Forward\nMedia\tBrowse: Up\nMedia\tConverter\nMedia\tCreate Folder\nMedia\tCreate New Folder\nMedia\tCreate Sampler Track\nMedia\tOpen Debug Browser\nMedia\tEmpty Trash\nMedia\tCheck Files\nMedia\tGenerate Thumbnail Cache\nMedia\tHide Folders That Are Not Scanned\nMedia\tImport from Audio CD\nMedia\tImport Medium\nMedia\tOpen Loop Browser\nMedia\tOpen MediaBay\nMedia\tNew Version\nMedia\tOpen Remote Browser\nMedia\tOpen/Close Attribute Inspector\nMedia\tOpen/Close Favorites\nMedia\tOpen/Close File Browser\nMedia\tOpen/Close Filters\nMedia\tOpen/Close Previewer\nMedia\tPrepare Archive\nMedia\tPreview Active On/Off\nMedia\tPreview AutoPlay On/Off\nMedia\tPreview Cycle On/Off\nMedia\tPreview in Context On/Off\nMedia\tPreview Pause On/Off\nMedia\tPreview Start\nMedia\tPreview Stop\nMedia\tPreview Sync On/Off\nMedia\tReconstruct\nMedia\tRefresh Views\nMedia\tRemove Favorite\nMedia\tRemove Missing Files\nMedia\tRemove Unused Media\nMedia\tRescan Disk\nMedia\tReset All Filters\nMedia\tReset Result Filters\nMedia\tReset Search\nMedia\tResults: Include Folders and Subfolders\nMedia\tOpenInExplorer\nMedia\tRevert\nMedia\tSearch MediaBay\nMedia\tSelect In Project\nMedia\tSelect Media Types\nMedia\tSet Record Folder\nMedia\tShow Only Selected Folder\nMedia\tShuffle Results\nMedia\tOpen Sound Browser\nMedia\tStop Updating Results\nMedia\tUpdate Results\nMedia\tWrite Attributes to File\nMediaInspector\tRemoveAttribute\nMediaList\tCommitTags\nMediaList\tRemoveWriteProtection\nMediaList\tSetWriteProtection\nMIDI\tApply Logical Preset\nMIDI\tAuto Select Controllers\nMIDI\tBounce\nMIDI\tMerge Tempo from Tapping\nMIDI\tOpen CC Automation Setup Dialog\nMIDI\tController Lane Setup 1\nMIDI\tController Lane Setup 2\nMIDI\tController Lane Setup 3\nMIDI\tController Lane Setup 4\nMIDI\tController Lane Setup 5\nMIDI\tController Lane Setup 6\nMIDI\tController Lane Setup 7\nMIDI\tController Lane Setup 8\nMIDI\tController Lane Setup 9\nMIDI\tController Lane Setup 10\nMIDI\tController Lane Setup 11\nMIDI\tController Lane Setup 12\nMIDI\tController Lane Setup 13\nMIDI\tController Lane Setup 14\nMIDI\tController Lane Setup 15\nMIDI\tController Lane Setup 16\nMIDI\tDelete Continuous Controllers\nMIDI\tDelete Controllers\nMIDI\tDelete Doubles\nMIDI\tDelete Notes\nMIDI\tDelete Overlaps (mono)\nMIDI\tDelete Overlaps (poly)\nMIDI\tDissolve Part\nMIDI\tDrum Editor Length Display\nMIDI\tDrummap Setup\nMIDI\tInstrumentMap Setup\nMIDI\tExtract MIDI\nMIDI\tFixed Lengths\nMIDI\tFixed Velocity\nMIDI\tFreeze Length\nMIDI\tFreeze Track Params\nMIDI\tOpen Insert Velocities Dialog\nMIDI\tInsert Velocity 1\nMIDI\tInsert Velocity 2\nMIDI\tInsert Velocity 3\nMIDI\tInsert Velocity 4\nMIDI\tInsert Velocity 5\nMIDI\tLegato\nMIDI\tList Editor: Show/Hide Filters\nMIDI\tMerge MIDI\nMIDI\tMirror\nMIDI\tONote Conversion\nMIDI\tPedals to Note Length\nMIDI\tPitch Visibility: On/Off\nMIDI\tPitch Visibility: Select Next Option\nMIDI\tPitchbend: Snap Pitchbend Events On/Off\nMIDI\tProject Input Transformer\nMIDI\tRecord In Editor\nMIDI\tRepeat Loop\nMIDI\tReset\nMIDI\tRestrict Polyphony\nMIDI\tReverse\nMIDI\tSend All Notes Off Message\nMIDI\tLogical Editor\nMIDI\tShow Used Controllers\nMIDI\tShow/Hide Controller Lanes\nMIDI\tThin Out\nMIDI\tToggle Computer Keyboard Input\nMIDI\tToggle MIDI Input\nMIDI\tToggle Step Input\nMIDI\tTranspose Notes\nMIDI\tTranspose\nMIDI\tType of New Controller Events: Toggle Step/Ramp\nMIDI\tVelocity\nMIDI Remote\tOpen MIDI Remote Mapping Assistant\nMIDI Remote\tScripting Tools: Highlight Bounding Rects\nMIDI Remote\tScripting Tools: Open Console\nMIDI Remote\tScripting Tools: Open ReadMe\nMIDI Remote\tScripting Tools: Open Script Folder\nMIDI Remote\tScripting Tools: Reload Scripts\nMIDI Remote\tShow/Hide Functions Browser in MIDI Remote Mapping Assistant\nMixConsole History\tRedo MixConsole Step\nMixConsole History\tUndo MixConsole Step\nMixConsole Snapshots\tSave MixConsole Snapshot\nMixer\tArm All Audio Tracks\nMixer\tAdd Track To Selected: FX Channel\nMixer\tAdd Track To Selected: Group Channel\nMixer\tAdd Track To Selected: VCA Fader\nMixer\tBypass: Channel Strip\nMixer\tBypass: Channel Strip on Main Mix\nMixer\tBypass: EQs\nMixer\tBypass: EQs on Main Mix\nMixer\tBypass: Inserts\nMixer\tBypass: Inserts on Main Mix\nMixer\tBypass: Sends\nMixer\tDisarm All Audio Tracks\nMixer\tDirect Routing: Summing Mode On/Off\nMixer\tPanner: Open/Close Selected\nMixer\tSettings: Open/Close Selected\nMixer\tEQ/Filter Transition: Quick\nMixer\tEQ/Filter Transition: Soft\nMixer\tExpand: Channel Strip\nMixer\tExpand: Cue Sends\nMixer\tExpand: Device Panels\nMixer\tExpand: Direct Routing\nMixer\tExpand: EQs\nMixer\tExpand: Filters/Gain\nMixer\tExpand: Inserts\nMixer\tExpand: Quick Controls\nMixer\tExpand: Routing\nMixer\tExpand: Sends\nMixer\tExpand: VCA\nMixer\tFile: Load Selected Channels\nMixer\tFile: Save Selected Channels\nMixer\tHide: Audio\nMixer\tHide: Groups\nMixer\tHide: Inputs\nMixer\tHide: Instruments\nMixer\tHide: MIDI\nMixer\tHide: Outputs\nMixer\tHide: Returns\nMixer\tHide: VCAs\nMixer\tLink Channels\nMixer\tControl Link: Edit Control Link Settings\nMixer\tControl Link: Next Control Link Group\nMixer\tControl Link: Previous Control Link Group\nMixer\tChannels: Listen On/Off\nMixer\tLoudness: Enable\nMixer\tLoudness: Reset\nMixer\tLoudness: Switch between +9dB and +18dB Scale\nMixer\tLoudness: Switch between LU and LUFS\nMixer\tMaster Meter: AES17 (+3dB)\nMixer\tMeters: Hold Forever\nMixer\tMeters: Hold Peaks\nMixer\tMeters: Input\nMixer\tMeters: Post Fader\nMixer\tMeters: Post Panner\nMixer\tMeters: Reset\nMixer\tPre/Post\nMixer\tQ-Link\nMixer\tHide: Reveal All\nMixer\tShow/Hide Channel Name\nMixer\tShow/Hide Channel Overview\nMixer\tShow/Hide Equalizer Curve\nMixer\tShow/Hide Latency\nMixer\tShow/Hide Meter Bridge\nMixer\tShow/Hide Notepad\nMixer\tShow/Hide Pictures\nMixer\tUnlink Channels\nMixer\tViews: Channel Strip\nMixer\tViews: Cue Sends\nMixer\tViews: Device Panels\nMixer\tViews: Direct Routing\nMixer\tViews: EQs\nMixer\tViews: Filters/Gain\nMixer\tViews: Inserts\nMixer\tViews: Quick Controls\nMixer\tViews: Routing\nMixer\tViews: Sends\nMixer\tViews: VCA\nMixer\tWindows: Close All Plug-Ins\nMixer\tZoom In\nMixer\tZoom In Vertically\nMixer\tZoom Out\nMixer\tZoom Out Vertically\nNavigate\tAdd Down\nNavigate\tAdd Left\nNavigate\tAdd Right\nNavigate\tAdd Up\nNavigate\tBack\nNavigate\tBottom\nNavigate\tDown\nNavigate\tFore\nNavigate\tLeft\nNavigate\tLess\nNavigate\tMore\nNavigate\tRight\nNavigate\tFocus Previous Plug-in\nNavigate\tFocus Next Plug-in\nNavigate\tToggle Selection\nNavigate\tTop\nNavigate\tUp\nNote Expression\tConsolidate Note Expression Overlaps\nNote Expression\tConvert to Note Expression\nNote Expression\tDissolve Note Expression\nNote Expression\tResolve Note Expression\nNote Expression\tDouble-Click opens Note Expression Editor On/Off\nNote Expression\tEdit Next Parameter\nNote Expression\tEdit Previous Parameter\nNote Expression\tEditor Size: Decrease\nNote Expression\tEditor Size: Increase\nNote Expression\tNote Expression MIDI Setup\nNote Expression\tOpen/Close Editor\nNote Expression\tPaste Note Expression\nNote Expression\tRecord MIDI as Note Expression\nNote Expression\tRemove Note Expression\nNote Expression\tShow/Hide Note Expression Data\nNote Expression\tTrim Note Expression to Note Length\nNudge\tDown\nNudge\tDown More\nNudge\tGraphical Bottom\nNudge\tGraphical Left\nNudge\tGraphical Right\nNudge\tGraphical Top\nNudge\tLeft\nNudge\tLink to Grid On/Off\nNudge\tLoop Range Left\nNudge\tLoop Range Right\nNudge\tBottom Down\nNudge\tBottom Up\nNudge\tEnd Left\nNudge\tEnd Right\nNudge\tStart Left\nNudge\tStart Right\nNudge\tTop Down\nNudge\tTop Up\nNudge\tRight\nNudge\tSelect Next Grid Type Value\nNudge\tSelect Next Time Format\nNudge\tSelect Prev Grid Type Value\nNudge\tSelect Prev Time Format\nNudge\tUp\nNudge\tUp More\nPreset\tNext\nPreset\tOpen Browser\nPreset\tPrevious\nPreset\tTrack Preset: Next\nPreset\tTrack Preset: Open Browser\nPreset\tTrack Preset: Previous\nProcess\tEnvelope\nProcess\tFade In\nProcess\tFade Out\nProcess\tGain\nProcess\tInvert Phase\nProcess\tNormalize\nProcess\tPitch Shift\nProcess\tRemove DC Offset\nProcess\tResample\nProcess\tReverse\nProcess\tSilence\nProcess\tStereo Flip\nProcess\tTime Stretch\nProject\tAuto Fades Settings\nProject\tBring To Front\nProject\tSet Track/Event Color\nProject\tDivide Track List\nProject\tDuplicate Tracks\nProject\tFolding: Toggle Tracks\nProject\tFolding: Fold Tracks\nProject\tOpen Markers\nProject\tFolding: Tracks To Folder\nProject\tNotepad\nProject\tOpen Score Layout\nProject\tOpen Pool\nProject\tColors\nProject\tSetup\nProject\tRemove Empty Tracks\nProject\tRemove Selected Tracks\nProject\tSelect Track: Add Next\nProject\tSelect Track: Add Prev\nProject\tSelect Track: Next\nProject\tSelect Track: Prev\nProject\tSet Timecode\nProject\tTap Tempo\nProject\tTempo Detection\nProject\tOpen Tempo Track\nProject\tFolding: Toggle Selected Track\nProject\tTrack Controls Settings\nProject\tFolding: Unfold Tracks\nQuantize Category\tAudioWarp Quantize On/Off\nQuantize Category\tAuto Quantize On/Off\nQuantize Category\tCreate Groove Quantize Preset\nQuantize Category\tFreeze Quantize\nQuantize Category\tQuantize\nQuantize Category\tQuantize Ends\nQuantize Category\tQuantize Lengths\nQuantize Category\tQuantize Setup\nQuantize Category\tUndo Quantize\nQuantize Category\tSelect Next Quantize\nQuantize Category\tSelect Prev Quantize\nQuantize Category\tSet Quantize to 1th\nQuantize Category\tSet Quantize to 2th\nQuantize Category\tSet Quantize to 4th\nQuantize Category\tSet Quantize to 8th\nQuantize Category\tSet Quantize to 16th\nQuantize Category\tSet Quantize to 32th\nQuantize Category\tSet Quantize to 64th\nQuantize Category\tSet Quantize to 128th\nQuantize Category\tIterative Quantize\nQuantize Category\tToggle Quantize Dotted\nQuantize Category\tToggle Quantize Triplet\nRender in Place\tRender\nRender in Place\tRender Setup...\nSample Editor\tBypass Warping\nSample Editor\tVariAudio - Reanalyze\nSample Editor\tVariAudio - Bypass Pitching\nSample Editor\tVariAudio - Quantize Pitch\nSample Editor\tVariAudio - Extract MIDI (no Dialog)\nSample Editor\tVariAudio - Extract MIDI...\nSample Editor\tVariAudio - Reset Pitch + Warp Changes\nSample Editor\tVariAudio - Reset Formant Shift Changes\nSample Editor\tVariAudio - Reset Pitch Changes\nSample Editor\tVariAudio - Reset Pitch Curve Changes\nSample Editor\tVariAudio - Reset Volume Changes\nSample Editor\tVariAudio - Reset Warp Changes\nSample Editor\tVariAudio - Show MIDI Reference Track\nSample Editor\tVariAudio - Toggle Pitch Snap Mode\nSample Editor\tVariAudio - Toggle Smart Control Mode\nSample Editor\tZoom Mode: Clip-Based\nSample Editor\tZoom Mode: Global\nScore Align Elements\tbottom\nScore Align Elements\thorizontal\nScore Align Elements\tvertical\nScore Align Elements\tdynamics\nScore Align Elements\tleft\nScore Align Elements\tright\nScore Align Elements\ttop\nScore Functions\t1 pagemode zoom\nScore Functions\t2 pagemode zoom\nScore Functions\t3 pagemode zoom\nScore Functions\t4 pagemode zoom\nScore Functions\t5 pagemode zoom\nScore Functions\t6 pagemode zoom\nScore Functions\t7 pagemode zoom\nScore Functions\t8 pagemode zoom\nScore Functions\t1 set font set\nScore Functions\t2 set font set\nScore Functions\t3 set font set\nScore Functions\t4 set font set\nScore Functions\t5 set font set\nScore Functions\t6 set font set\nScore Functions\t7 set font set\nScore Functions\t8 set font set\nScore Functions\t9 set font set\nScore Functions\t10 set font set\nScore Functions\t11 set font set\nScore Functions\t12 set font set\nScore Functions\t13 set font set\nScore Functions\t14 set font set\nScore Functions\t15 set font set\nScore Functions\t16 set font set\nScore Functions\tauto dialog\nScore Functions\t8 auto layout\nScore Functions\t7 auto layout\nScore Functions\t6 auto layout\nScore Functions\t1 auto layout\nScore Functions\t3 auto layout\nScore Functions\t0 auto layout\nScore Functions\t2 auto layout\nScore Functions\t9 auto layout\nScore Functions\t5 auto layout\nScore Functions\t4 auto layout\nScore Functions\tmake accel\nScore Functions\ttuplet dialog\nScore Functions\tmake repeat\nScore Functions\tmake trill\nScore Functions\tCB-EmptyBar\nScore Functions\tgrace note\nScore Functions\tshow marker\nScore Functions\tEnharm Shift #\nScore Functions\tEnharm Shift ##\nScore Functions\tEnharm Shift ()\nScore Functions\tEnharm Shift ?\nScore Functions\tEnharm Shift b\nScore Functions\tEnharm Shift bb\nScore Functions\tEnharm Shift no\nScore Functions\tEnharm Shift off\nScore Functions\txplode\nScore Functions\textract voices\nScore Functions\tflip\nScore Functions\tforce update\nScore Functions\tget info\nScore Functions\tgroup\nScore Functions\thide\nScore Functions\tinsert slur\nScore Functions\t1 set insert\nScore Functions\t2 set insert\nScore Functions\t3 set insert\nScore Functions\t4 set insert\nScore Functions\t+ move insert\nScore Functions\t- move insert\nScore Functions\tmake chords\nScore Functions\tmarker to form\nScore Functions\tmerge staves\nScore Functions\t1 to string\nScore Functions\t2 to string\nScore Functions\t3 to string\nScore Functions\t4 to string\nScore Functions\t5 to string\nScore Functions\t6 to string\nScore Functions\t7 to string\nScore Functions\t8 to string\nScore Functions\t9 to string\nScore Functions\t10 to string\nScore Functions\t11 to string\nScore Functions\t12 to string\nScore Functions\t1 to voice\nScore Functions\t2 to voice\nScore Functions\t3 to voice\nScore Functions\t4 to voice\nScore Functions\t5 to voice\nScore Functions\t6 to voice\nScore Functions\t7 to voice\nScore Functions\t8 to voice\nScore Functions\topen numbars\nScore Functions\t- move one\nScore Functions\t+ move one\nScore Functions\tpaste attr\nScore Functions\tshow pos\nScore Functions\tclean layout\nScore Functions\tfreeze scores\nScore Functions\tCB-ShowRepeat\nScore Functions\tshow chordtrack\nScore Functions\tCB-RemoveRhythm\nScore Functions\tCB-ShowRhythm\nScore Functions\tspacer to layout\nScore Functions\t1 to verse\nScore Functions\t2 to verse\nScore Functions\t3 to verse\nScore Functions\t4 to verse\nScore Functions\t5 to verse\nScore Functions\t6 to verse\nScore Meter Scale\t1 pagemode zoom\nScore Meter Scale\t2 pagemode zoom\nScore Meter Scale\t3 pagemode zoom\nScore Meter Scale\t4 pagemode zoom\nScore Meter Scale\t5 pagemode zoom\nScore Meter Scale\t6 pagemode zoom\nScore Meter Scale\t7 pagemode zoom\nScore Meter Scale\t8 pagemode zoom\nScore Meter Scale\trulerCM\nScore Meter Scale\t50 edmode zoom\nScore Meter Scale\t80 edmode zoom\nScore Meter Scale\t100 edmode zoom\nScore Meter Scale\t150 edmode zoom\nScore Meter Scale\t200 edmode zoom\nScore Meter Scale\tfitPage\nScore Meter Scale\tfitWidth\nScore Meter Scale\tedmode hide\nScore Meter Scale\trulerIN\nScore Meter Scale\trulerOFF\nScore Meter Scale\trulerPT\nScore Symbol Editor\t100%\nScore Symbol Editor\t200%\nScore Symbol Editor\t400%\nScore Symbol Editor\t600%\nScore Symbol Editor\t800%\nScore Symbol Editor\talign bottom\nScore Symbol Editor\talign horizontal\nScore Symbol Editor\talign vertical\nScore Symbol Editor\talign left\nScore Symbol Editor\talign right\nScore Symbol Editor\talign top\nScore Symbol Editor\tmove front\nScore Symbol Editor\tmirror 90 +\nScore Symbol Editor\tmirror 90 -\nScore Symbol Editor\tgroup\nScore Symbol Editor\tmirror horizontal\nScore Symbol Editor\tmirror vertical\nScore Symbol Editor\tmove behind\nScore Symbol Editor\tungroup\nScore Symbol Palettes\tpalette chord\nScore Symbol Palettes\tpalette clefs\nScore Symbol Palettes\tpalette clef\nScore Symbol Palettes\tdynamic mapping\nScore Symbol Palettes\tpalette dyn\nScore Symbol Palettes\tpalette expressionmap\nScore Symbol Palettes\tpalette user\nScore Symbol Palettes\tform symbols\nScore Symbol Palettes\tpalette guitar\nScore Symbol Palettes\tpalette keys\nScore Symbol Palettes\tpalette linetr\nScore Symbol Palettes\tpalette notesyms\nScore Symbol Palettes\tpalette other\nScore Symbol Palettes\tpalette sign\nScore Symbol Palettes\tpalette picts\nScore Symbol Palettes\tpalette text\nScores\tauto group\nScores\tShow/Hide Computer Keyboard Input\nScores\tfind replace\nScores\tlyrics from clip\nScores\tmove next page\nScores\tmove prev page\nScores\tShow/Hide Length\nScores\tToggle Mode\nScores\ttoggle inpector\nScores\tglobal\nScores\tstaff set\nScores\ttext from clip\nScores\tuser symbols\nScores Statusbar\tbarhandles\nScores Statusbar\tcut\nScores Statusbar\tgroup\nScores Statusbar\thidden notes\nScores Statusbar\thide\nScores Statusbar\tlayout\nScores Statusbar\tquant\nScores Statusbar\tsplitrest\nScores Statusbar\tstems\nSet Insert Length\t1\nSet Insert Length\t2\nSet Insert Length\t4\nSet Insert Length\t8\nSet Insert Length\t16\nSet Insert Length\t32\nSet Insert Length\t64\nSet Insert Length\t128\nSet Insert Length\t.\nSet Insert Length\tT\nTool\tAudio Tempo Definition Tool\nTool\tColor Tool\nTool\tCombine Selection Tools On/Off\nTool\tComp Tool\nTool\tCurve Tool\nTool\tCut Tool\nTool\tDisplay Quantize Tool\nTool\tDraw Tool\nTool\tDrumstick Tool\nTool\tDelete Tool\nTool\tExport Range Tool\nTool\tFree Warp Tool\nTool\tGlue Tool\nTool\tHitpoint Tool\nTool\tInsert Note Tool\nTool\tLayout Tool\nTool\tMute Tool\nTool\tNext Tool\nTool\tSelect Tool\nTool\tObject Selection Tool: Normal Sizing\nTool\tObject Selection Tool: Sizing Applies Time Stretch\nTool\tObject Selection Tool: Sizing Moves Content\nTool\tPlay Tool\nTool\tPopup Tools\nTool\tPrevious Tool\nTool\tRange Tool\nTool\tScore Zoom Tool\nTool\tScrub Tool\nTool\tSplit Tool\nTool\tTimeWarp Tool\nTool\tTool 1\nTool\tTool 2\nTool\tTool 3\nTool\tTool 4\nTool\tTool 5\nTool\tTool 6\nTool\tTool 7\nTool\tTool 8\nTool\tTool 9\nTool\tTool 10\nTool\tTrim Tool\nTool\tEdit Pitch/Warp Tool\nTool\tZoom Tool\nTrackVersions\tAssign Common Version ID\nTrackVersions\tCreate Lanes from Versions\nTrackVersions\tCreate Versions from Lanes\nTrackVersions\tDelete Inactive Versions of All Tracks\nTrackVersions\tDelete Inactive Versions of Selected Tracks\nTrackVersions\tDelete Version\nTrackVersions\tDuplicate Version\nTrackVersions\tNew Version\nTrackVersions\tNext Version\nTrackVersions\tPrevious Version\nTrackVersions\tRename Version\nTrackVersions\tSelect Tracks with Same Version ID\nTransport\tUse External Sync\nTransport\tMetronome On\nTransport\tAuto Punch In\nTransport\tAuto Punch Out\nTransport\tAudio Record Mode\nTransport\tMIDI Record Auto Quantize\nTransport\tCycle\nTransport\tMIDI Retrospective Record: Empty All Buffers\nTransport\tInput Left Locator\nTransport\tInput Locator Duration\nTransport\tInput Position\nTransport\tInput Punch In Position\nTransport\tInput Punch Out Position\nTransport\tInput Right Locator\nTransport\tInput Tempo\nTransport\tInput Time Signature\nTransport\tExchange Locator Positions\nTransport\tExchange Time Formats\nTransport\tFast Forward\nTransport\tFast Rewind\nTransport\tForward\nTransport\tTo Left Locator\nTransport\tGoto End\nTransport\tReturn to Zero\nTransport\tTo Punch In Position\nTransport\tTo Punch Out Position\nTransport\tTo Right Locator\nTransport\tGlobal Retrospective Record\nTransport\tMIDI Retrospective Record: Insert from Track Input as Cycle Recording\nTransport\tMIDI Retrospective Record: Insert from Track Input as Linear Recording\nTransport\tJog Left\nTransport\tJog Right\nTransport\tLocate Next Event\nTransport\tLocate Next Hitpoint\nTransport\tLocate Next Marker\nTransport\tLocate Previous Event\nTransport\tLocate Previous Hitpoint\nTransport\tLocate Previous Marker\nTransport\tLocate Selection End\nTransport\tLocate Selection\nTransport\tSync Punch To Cycle\nTransport\tLock Record\nTransport\tLoop Selection\nTransport\tMetronome Setup\nTransport\tMIDI Cycle Record Mode\nTransport\tMIDI Record Mode\nTransport\tStep Bar\nTransport\tNudge +1 Frame\nTransport\tStep Back Bar\nTransport\tNudge -1 Frame\nTransport\tNudge Cursor +5 Seconds\nTransport\tNudge Cursor +10 Seconds\nTransport\tNudge Cursor +20 Seconds\nTransport\tNudge Cursor -5 Seconds\nTransport\tNudge Cursor -10 Seconds\nTransport\tNudge Cursor -20 Seconds\nTransport\tNudge Cursor Left\nTransport\tNudge Cursor Right\nTransport\tPlay from Selection End\nTransport\tPlay from Selection Start\nTransport\tPlay Selection Range\nTransport\tPlay until Next Marker\nTransport\tPlay until Selection End\nTransport\tPlay until Selection Start\nTransport\tPost-roll from Selection End\nTransport\tPost-roll from Selection Start\nTransport\tPre-roll to Selection End\nTransport\tPre-roll to Selection Start\nTransport\tProject Synchronization Setup\nTransport\tRecall Cycle Marker 1\nTransport\tRecall Cycle Marker 2\nTransport\tRecall Cycle Marker 3\nTransport\tRecall Cycle Marker 4\nTransport\tRecall Cycle Marker 5\nTransport\tRecall Cycle Marker 6\nTransport\tRecall Cycle Marker 7\nTransport\tRecall Cycle Marker 8\nTransport\tRecall Cycle Marker 9\nTransport\tRecall Cycle Marker X\nTransport\tRecord\nTransport\tRe-Record on/off\nTransport\tStart Record at Left Locator\nTransport\tRestart\nTransport\tReturn to Start Position\nTransport\tActivate Return to Start Position\nTransport\tRewind\nTransport\tSet Left Locator\nTransport\tLocators to Selection\nTransport\tSet Marker 1\nTransport\tSet Marker 2\nTransport\tSet Marker 3\nTransport\tSet Marker 4\nTransport\tSet Marker 5\nTransport\tSet Marker 6\nTransport\tSet Marker 7\nTransport\tSet Marker 8\nTransport\tSet Marker 9\nTransport\tSet Punch In Position\nTransport\tSet Punch Out Position\nTransport\tSet Punch Points To Selection\nTransport\tSet Right Locator\nTransport\tShuttle Play 1/2x\nTransport\tShuttle Play 1/4x\nTransport\tShuttle Play 1/8x\nTransport\tShuttle Play 1x\nTransport\tShuttle Play 2x\nTransport\tShuttle Play 4x\nTransport\tShuttle Play 8x\nTransport\tShuttle Play Reverse 1/2x\nTransport\tShuttle Play Reverse 1/4x\nTransport\tShuttle Play Reverse 1/8x\nTransport\tShuttle Play Reverse 1x\nTransport\tShuttle Play Reverse 2x\nTransport\tShuttle Play Reverse 4x\nTransport\tShuttle Play Reverse 8x\nTransport\tStart\nTransport\tStart Mode\nTransport\tStartStop\nTransport\tStartStop Preview\nTransport\tStop\nTransport\tTo Cycle Marker 1\nTransport\tTo Cycle Marker 2\nTransport\tTo Cycle Marker 3\nTransport\tTo Cycle Marker 4\nTransport\tTo Cycle Marker 5\nTransport\tTo Cycle Marker 6\nTransport\tTo Cycle Marker 7\nTransport\tTo Cycle Marker 8\nTransport\tTo Cycle Marker 9\nTransport\tTo Cycle Marker X\nTransport\tTo Marker 1\nTransport\tTo Marker 2\nTransport\tTo Marker 3\nTransport\tTo Marker 4\nTransport\tTo Marker 5\nTransport\tTo Marker 6\nTransport\tTo Marker 7\nTransport\tTo Marker 8\nTransport\tTo Marker 9\nTransport\tTo Marker X\nTransport\tToggle: Cycle follows when locating to Markers\nTransport\tPanel\nTransport\tUnlock Record\nTransport\tUse Post-roll\nTransport\tUse Pre-/Post-Roll\nTransport\tUse Pre-roll\nTransport\tPrecount On\nTransport\tTempo Track Rehearsal Mode On/Off\nTransport\tEdit Mode\nVideo\tMute all video tracks\nVRPlayerRemote\tShow Dialog\nWaveLab Exchange\tEdit in WaveLab\nWindow Zones\tShow Next Page\nWindow Zones\tShow Next Tab\nWindow Zones\tShow Previous Page\nWindow Zones\tShow Previous Tab\nWindow Zones\tShow/Hide Channel Left Zone\nWindow Zones\tShow/Hide Editor Visibility\nWindow Zones\tShow/Hide Extended Left Zone\nWindow Zones\tShow/Hide Infoview\nWindow Zones\tShow/Hide Left Zone\nWindow Zones\tShow/Hide Lower Zone\nWindow Zones\tShow/Hide Overview\nWindow Zones\tShow/Hide Regions\nWindow Zones\tShow/Hide Right Zone\nWindow Zones\tShow/Hide Status Line\nWindow Zones\tShow/Hide Transport Zone\nWindows\tClose All\nWindows\tClose All Plug-in Windows\nWindows\tMinimize\nWindows\tMinimize All\nWindows\tRestore All\nWindows\tSelect Next Plug-in Window\nWindows\tShow/Hide Plug-ins\nWindows\tMaximize\nWorkspaces\tNew\nWorkspaces\tNo Workspace\nWorkspaces\tOrganize\nWorkspaces\tUpdate Workspace\nWorkspaces\tWorkspace 1\nWorkspaces\tWorkspace 2\nWorkspaces\tWorkspace 3\nWorkspaces\tWorkspace 4\nWorkspaces\tWorkspace 5\nWorkspaces\tWorkspace 6\nWorkspaces\tWorkspace 7\nWorkspaces\tWorkspace 8\nWorkspaces\tWorkspace 9\nWorkspaces\tWorkspace X\nZoom\tRedo Zoom\nZoom\tUndo Zoom\nZoom\tZoom 4 Tracks\nZoom\tZoom 8 Tracks\nZoom\tZoom Cycle Marker 1\nZoom\tZoom Cycle Marker 2\nZoom\tZoom Cycle Marker 3\nZoom\tZoom Cycle Marker 4\nZoom\tZoom Cycle Marker 5\nZoom\tZoom Cycle Marker 6\nZoom\tZoom Cycle Marker 7\nZoom\tZoom Cycle Marker 8\nZoom\tZoom Cycle Marker 9\nZoom\tZoom Full\nZoom\tZoom In\nZoom\tZoom In On Waveform Vertically\nZoom\tZoom In Tracks\nZoom\tZoom In Vertically\nZoom\tZoom MEM\nZoom\tZoom N Tracks\nZoom\tZoom Out\nZoom\tZoom Out Of Waveform Vertically\nZoom\tZoom Out Tracks\nZoom\tZoom Out Vertically\nZoom\tZoom Preset 1\nZoom\tZoom Preset 2\nZoom\tZoom Preset 3\nZoom\tZoom Preset 4\nZoom\tZoom Preset 5\nZoom\tZoom Tracks Exclusive\nZoom\tZoom to Event\nZoom\tZoom to Locators\nZoom\tZoom to Selection\nZoom\tZoom to Selection Horizontally\nZoom\tZoom Tracks 1 Row\nZoom\tZoom Tracks 2 Rows\nZoom\tZoom Tracks 3 Rows\nZoom\tZoom Tracks 4 Rows\nZoom\tZoom Tracks Full\nZoom\tZoom Tracks Minimal\nZoom\tZoom Tracks N Rows\nZoom\tZoom ZAP"
var EXTRA_COMMANDS = ""
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
        for (var pr = 0; pr < pendingRelease.length; ++pr) {
            if (pendingRelease[pr] === s) { s.cv.setProcessValue(dev, 0); pendingRelease.splice(pr, 1); break }
        }
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
    if (!idleSeen && gMapping) {
        releasePending(activeDevice)
        processInbox(activeDevice, gMapping)
        emitEvents(activeDevice)
        flushOut(activeDevice, CFG.MAX_OUT_CHUNKS_PER_IDLE)
    }
}

function releasePending(dev) {
    if (!pendingRelease.length) return
    for (var i = 0; i < pendingRelease.length; ++i) pendingRelease[i].cv.setProcessValue(dev, 0)
    pendingRelease = []
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
    releasePending(activeDevice)
    if (inbox.length) processInbox(activeDevice, activeMapping)
    idleCounter++
    if (idleCounter % CFG.EVENT_INTERVAL_IDLES === 0) emitEvents(activeDevice)
    flushOut(activeDevice, CFG.MAX_OUT_CHUNKS_PER_IDLE)
}

handlers.events = function (p) { eventsEnabled = !!p.enabled; return { enabled: eventsEnabled } }
