# perffx-st: performance effects on the trig keys (prototype 0.1.0)

A test build for the design in Discussion #11. Not for a pull request yet.

**Open and close.** Double-tap TRK (two taps within 400 ms). The first tap
keeps its native action; the second is taken. While it is open a bar over the
header reads PERF FX and the name of the effect held. NO closes it. PLAY,
STOP, FUNC and the encoders stay native.

**The 16 trig keys** (hold to apply, release to resume):

| key | effect | | key | effect |
|---|---|---|---|---|
| 1 | OCT UP (+12 semitones) | | 9 | RISE (glides +24 in 0.5 s) |
| 2 | OCT DOWN (-12) | | 10 | (none) |
| 3 | FIFTH UP (+7) | | 11 | MUTE ALL |
| 4 | FIFTH DOWN (-7) | | 12 | MUTE ODD tracks |
| 5 | TAPE STOP (about 1 s) | | 13 | MUTE EVEN tracks |
| 6 | STOP FAST (about 0.35 s) | | 14-16 | (none) |
| 7 | HALF SPEED (glides -12 in 0.15 s) | | | |
| 8 | DIVE (glides -36 in 0.5 s) | | | |

Held effects add up. All 12 tracks are affected. A key with no effect is
still taken, so it does not play a step.

**How.** Pitch effects edit the 12 tracks' Q16 pitches at `ST_NATIVE_PITCH`
(core 2.9.0-mt1, host API 21), once per audio block, with the ramps counted in
blocks (1500 a second). The mutes skip note-ons of the gated tracks at
`ST_NATIVE_NOTE_ACCEPT`, as LIVE MUTE does, for the sequencer's notes and live
ones; a note already sounding rings out, and note-offs pass. Each engine is
declared to the core only while one of its keys is down.

**Not here yet:** LED colours, banks (PAGE does nothing), per-track
include/exclude, latch, stutter, bit crush, step effects, a view of its own.
Tape stop is linear in semitones, not a real tape curve.

`tests/test_st_perffx_module.py` runs the compiled module in Unicorn.
