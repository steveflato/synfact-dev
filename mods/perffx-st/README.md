# perffx-st: performance effects on the trig keys (prototype 0.5.3)

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

**Hide the bar.** Hold TRK and tap trig 16 to hide the PERF FX bar (trig 16
lights cyan while TRK is held); tap again to show it. The effects keep
working. It is there so SYSTEM INFO's CPU readout, which shares the bar's
place in the header, can be read while effects play. Closing the mode shows
the bar again next time.
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
red is left out. NO resets them. Trig 16 is cyan while the bar is hidden.

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

## CPU load

**Estimated in the emulator** (0.5.2 code; instructions counted per call,
converted with the project's model of 0.85 cycles an instruction plus 40 per
cold 16-byte line, at 250 MHz, 166,667 cycles a block; see
[STOCK-MAIN-CPU-PROFILE](../../docs/STOCK-MAIN-CPU-PROFILE.md)). "Cold" counts
every code and data line the call touches as a miss.

| active | called | instructions | warm | cold |
|---|---|---:|---:|---:|
| nothing (mode open or closed) | never | 0 | 0 % | 0 % |
| one jump (OCT UP), 12 tracks | each audio block | 412 | 0.21 % | 0.81 % |
| one ramp (TAPE STOP) | each audio block | 448 | 0.23 % | 0.92 % |
| one mute (MUTE ALL) | each note-on | 370 | 0.19 % | 0.67 % |
| bank 1's 12 effects at once | each block + each note | 678 + 484 | 0.35 % | 1.40 % |

The core only calls the module while an effect is on (checked: interest
goes to zero on release). Not counted: the core's own dispatch.

**On the unit.** Build the bank with SYSTEM INFO (`system-info-smz-st`), turn
SETTINGS > SYSTEM INFO on, open Perf FX, hide the bar (TRK + trig 16), and
compare `CPU M` with no effect held and with effects held or latched (FUNC
latches them, so the hands are free). Its value updates about once a second.

Measured by the owner on the unit (0.5.3, 27 Nov 2026), SYSTEM INFO's
readout, 1 % steps:

| state | CPU M | CPU A |
|---|---:|---:|
| sequencer stopped, no effect | 74 % | 59 % |
| sequencer playing, no effect | 81 % | 82 % |
| playing, OCT UP | 82 % | 82 % |
| playing, TAPE STOP | 82 % | 81 % |
| playing, OCT UP + TAPE STOP | 82 % | 81 % |
| playing, every effect but the mutes | 82 % | 80 % |

Perf FX adds about one point of MAIN, within the estimate; stacking more
effects shows no further change at this resolution. AUDIO does not change
(Perf FX does not run there); its 1–2 point dips are noise or the voices'
work moving with the pitch. The pattern played is the owner's; a heavier one
leaves less headroom.

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

