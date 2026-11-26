# perffx-st: performance effects on the trig keys (prototype 0.4.0)

A test build for the design in Discussion #11. Not for a pull request yet.

**Open and close.** Double-tap TRK (two taps within 400 ms). The first tap
keeps its native action; the second is taken. While it is open a bar over the
header reads PERF FX and the bank (1 or 2), then the name of the effect on,
an L and the count if any are latched, and a row of 12 marks: a block for each track the
effects touch, a dot for each one left out. NO closes it and turns every effect
off. PLAY, STOP, FUNC and the encoders stay native.

**Hold or latch.** An effect is on while its trig key is down. Press FUNC
while effect keys are held and they latch on, so the keys can go. Press a
latched effect's key once and it turns off. NO turns every effect off. Held
and latched effects add up, and the bar names the one you are holding over a
latched one. FUNC with no effect held stays native. (FUNC held makes the OS
put its quick-mute screen on top, so FUNC + a trig key is the OS's, not ours.)

**Banks.** PAGE switches between the two banks. A key held across a switch
keeps the effect it started.

| key | bank 1 | bank 2 |
|---|---|---|
| 1 | OCT UP (+12 semitones) | 2 OCT UP (+24) |
| 2 | OCT DOWN (-12) | 2 OCT DOWN (-24) |
| 3 | FIFTH UP (+7) | SEMI UP (+1) |
| 4 | FIFTH DOWN (-7) | SEMI DOWN (-1) |
| 5 | TAPE STOP (about 3 s) | LONG STOP (about 12 s) |
| 6 | STOP FAST (about 0.5 s) | STUTTER (audio engine; lit and reported only) |
| 7 | HALF SPEED (-12 in 0.3 s) | SLOW RISE (+24 in about 10 s) |
| 8 | DIVE (-36 in about 4 s) | |
| 9 | RISE (+24 in about 4 s) | |
| 10 | | |
| 11 | MUTE ALL | |
| 12 | MUTE ODD tracks | |
| 13 | MUTE EVEN tracks | |
| 14-16 | | |

**Which tracks.** All 12 by default. Hold TRK and tap a trig key 1 to 12 to
include or exclude that track for the pitch effects and the mutes. Closing the
mode (NO) puts all 12 back in. While the mode is open TRK is ours; FUNC + PAGE
and FUNC + trig keys stay the OS's. 
**How.** Pitch effects edit the 12 tracks' Q16 pitches at `ST_NATIVE_PITCH`
(core 2.9.0-mt1, host API 21), once per audio block, with the ramps counted in
blocks (1500 a second). The mutes skip note-ons of the gated tracks at
`ST_NATIVE_NOTE_ACCEPT`, as LIVE MUTE does, for the sequencer's notes and live
ones; a note already sounding rings out, and note-offs pass. Each engine is
declared to the core only while one of its effects is on.

**LEDs.** While it is open the 16 trig keys are lit by what they do:

| colour | meaning |
|---|---|
| blue | a pitch jump (octave, fifth, semitone) |
| orange | a pitch ramp (tape stop, dive, rise) |
| red | a mute |
| unlit | no effect on that key |
| white | the effect is held |
| yellow | the effect is latched |

With TRK held, keys 1 to 12 show the tracks instead: green is affected, dim
red is left out. NO resets them.

The LEDs come from an invisible view the module pushes on the UI stack when it
opens, made as arp-st makes its own: a View followed by a LedHandler, not
opaque, drawing nothing, so the MainScreen stays visible and keys it does not
take pass through. It opens only over the MainScreen (not over a menu or a
dialog). If another view is on top (a menu, the TRK track selection) the module
stands aside: its keys and LEDs are the other view's, and held effects end as
their keys are released. NO, or the OS letting go of the view, ends it and
turns every effect off.

**Not here yet:** stutter, bit crush, step effects, a kept track selection,
audio effects (the buffer and bypass work belongs to GSM). Tape stop is linear
in semitones, not a real tape curve.

`tests/test_st_perffx_module.py` runs the compiled module in Unicorn, with the
OS's view routines stubbed.

## Hand-off to an audio engine

`perffx.h` declares `pf_state`, which the key side keeps current: whether the
mode is open, which effects are on (`active`, split into `held` and `latched`),
the ones of kind `PF_KIND_AUDIO` (`audio`), the track include mask and the
effect the bar names (`shown`). It is written by the UI task only and read by
anything, including a MAIN IRQ engine: `pf_state_read()` gives a consistent
copy or 0 (a write was under way; keep the last copy). An effect's id is
`bank * 16 + key`; bit `id` of a mask is that effect. An audio effect is a row
of the effect table with kind `PF_KIND_AUDIO`: the key side lights its key
magenta and reports it, and the engine decides what the id does. The
sequencer is never touched, so it keeps running underneath.

