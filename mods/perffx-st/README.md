# perffx-st: performance effects on the trig keys (prototype 0.3.1)

A test build for the design in Discussion #11. Not for a pull request yet.

**Open and close.** Double-tap TRK (two taps within 400 ms). The first tap
keeps its native action; the second is taken. While it is open a bar over the
header reads PERF FX and the bank (1 or 2), then the name of the effect on,
an L if one is latched, and a row of 12 marks: a block for each track the
effects touch, a dot for each one left out. NO closes it and turns every effect
off. PLAY, STOP, FUNC and the encoders stay native.

**Hold or latch.** An effect is on while its trig key is down. Press FUNC
while one or more effect keys are held and they latch on: let the keys go and
they stay. Press a latched effect's key to turn it off. Held and latched
effects add up. FUNC with no effect held stays native, so FUNC then a trig key
still includes or excludes a track.

**Banks.** PAGE switches between the two banks. A key held across a switch
keeps the effect it started.

| key | bank 1 | bank 2 |
|---|---|---|
| 1 | OCT UP (+12 semitones) | 2 OCT UP (+24) |
| 2 | OCT DOWN (-12) | 2 OCT DOWN (-24) |
| 3 | FIFTH UP (+7) | SEMI UP (+1) |
| 4 | FIFTH DOWN (-7) | SEMI DOWN (-1) |
| 5 | TAPE STOP (about 1.3 s) | LONG STOP (about 4 s) |
| 6 | STOP FAST (about 0.5 s) | SLOW DIVE (-48 in about 3 s) |
| 7 | HALF SPEED (-12 in 0.3 s) | SLOW RISE (+24 in about 3 s) |
| 8 | DIVE (-36 in about 0.7 s) | |
| 9 | RISE (+24 in about 0.7 s) | |
| 10 | | |
| 11 | MUTE ALL | MUTE tracks 1-4 |
| 12 | MUTE ODD tracks | MUTE tracks 5-8 |
| 13 | MUTE EVEN tracks | MUTE tracks 9-12 |
| 14-16 | | |

**Which tracks.** All 12 by default. FUNC, then a trig key 1 to 12, includes or
excludes that track for the pitch effects and the mutes; FUNC + key 13
includes all again. Kept until power-off, not across it.

**How.** Pitch effects edit the 12 tracks' Q16 pitches at `ST_NATIVE_PITCH`
(core 2.9.0-mt1, host API 21), once per audio block, with the ramps counted in
blocks (1500 a second). The mutes skip note-ons of the gated tracks at
`ST_NATIVE_NOTE_ACCEPT`, as LIVE MUTE does, for the sequencer's notes and live
ones; a note already sounding rings out, and note-offs pass. Each engine is
declared to the core only while one of its effects is on.

**Not here yet:** LED colours, a kept track selection, stutter, bit crush,
step effects, a view of its own. Tape stop is linear in semitones, not a real
tape curve.

`tests/test_st_perffx_module.py` runs the compiled module in Unicorn.
