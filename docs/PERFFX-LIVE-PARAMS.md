# Perf FX: live parameter writes (research notes)

What the repository already records about changing a track's sound
parameters while it plays, for the Perf FX ideas that are parameter changes
(a filter sweep, drive, stored FX/LFO/filter states) rather than audio
effects. Nothing here was tried on a unit. "Not recorded" means the docs and
the mods say nothing and a disassembly or a unit test is needed before a
change; ask before guessing an address.

## What is recorded

- MAIN keeps each track's values in the playing Kit (`<syntakt/kit.h>`): the
  Sound at `+2Ah + t * 123h`, values as halfwords at `+14h + 2 * slot`, slots
  0..70. These are the *stored* values.
- MAIN also keeps a *working* copy per track after modulation (LFOs, locks):
  142 bytes (71 halfwords) per track, which `core-st/machine-params.c` reads as
  `0x80006364 + t * 0x8e`, next to the stored mirror at `0x800021cc + t * 0x8e`.
  The mirror changes only when a track plays (`400a2e1a`).
- Each block the native PUBLISH (`ST_NATIVE_PUBLISH`, MAIN IRQ, host API 8)
  hands a subscriber the working block it is about to send: the types at
  `+2 * t` and the rows at `+0x1c + t * 0x8e` (POLY's `poly_publication.c`
  rewrites rows there). A module that subscribes can overwrite row halfwords in
  place before AUDIO sees them: the point for a live filter, drive or level
  change, for the cost of one subscription while an effect is on.
- On AUDIO the rows are the 142-byte rows at `0x80003c40`. Row words that the
  docs name: `0x19` drive gain, `0x28` drive mix, `0x33` BASE, `0x34` WDTH,
  `0x35` AMP envelope (byte `+0x6a`), `0x41` a flag; the multimode filter
  reads at row `+0x52`. Row word index = Sound slot.

## What is not recorded

- Which slot is FILTER FREQ / RESONANCE. The names and ranges are in the
  label table below; the slot of each is not established.
- Whether the working copy is rebuilt from the stored values every block
  (then a write in PUBLISH wins, and nothing needs restoring) or kept across
  blocks (then a held effect must restore it). PUBLISH runs after the
  modulation, so a write there should not be undone before the send, but this
  is not verified.
- The FX track (delay, reverb, chorus) values: `kit.h` covers the 12 Sounds
  only; where the FX track's values live is not recorded.
- Analog tracks 9..12 carry the same rows? Analog voices are on a different
  path (see `poly-st`); not recorded.

## The stock label table (OS 1.42), read from the MAIN image

The names of the engine parameters are a table of 52-byte entries (13 words)
starting at `0x4022e42c`, 204 entries to `0x40230d68`. In an entry: word 0 the
long name, 1 the group (Filter, Amp, LFO1, LFO2, Delay, Reverb, Ext-in ...),
2 the short name, 6 the maximum (`0x7f00` for most), 7 the default (Q8), and
words 9 to 12 further ids whose meaning is not recorded. The first entries, in
order: Frequency, Gain, Resonance, Filter Type (Filter); Env. Delay, Attack,
Decay, Sustain, Release, Env. Depth, Env. Reset, Base, Width (Filter);
Delay Time, Attack, Hold, Decay, Sustain, Release, Delay Send, Reverb Send,
Pan, Volume, Pre/Post FX, Mode (Amp); then LFO1, LFO2, Delay, Reverb and
Ext-in. The machine's own SYN labels (Sample Rate Redux, Bit Redux,
Overdrive ...) are strings in the same area (`0x40253c77` to `0x4025413d`), so a
sample-rate or bit reduction exists as a machine control of some machines,
not as a track-wide effect. The core's lane table (`fw_lane_ids`,
`0x40234344`, 54 words) lists engine ids `0..0x35`.

**Index to slot: not established.** The table index plus 39 matches three
known row words (Base index 12 = row word `0x33`, Width 13 = `0x34`, the Amp
envelope's first entry 14 = `0x35`, where the AMP routine starts), which is
why it is tempting. It cannot hold further: the core reserves slots `0x36` to
`0x46` for machine extended controls, and the Amp Attack would land on `0x36`.
So the slot of Filter Frequency, Resonance, Delay Send and the rest is still
to be read from the code that fills the Sound (`400a2e1a`, `core_machine_defaults`)
or proved on the emulator. A filter or send write must wait for that.

## What each idea needs

| Idea | Kind | Needs |
|---|---|---|
| Filter sweep, drive up, level duck | parameter write | the slot table, then a PUBLISH subscriber (a new native event handler, like `pf_native`) |
| Chorus, flanger, phaser, comb, BBD delay, reverb plate/spring | audio effect | GSM's bypass buffer and engine (CPU is the limit) |
| Beat repeat, stutter, reverse | audio effect | the same engine and a capture buffer |
| Bit crush, sample-rate reduction | audio effect, or the sample rate row words if a machine has one | engine; nothing in the row words found for it |
| Saved FX/LFO/filter states on key press | stored values | a snapshot of the working or stored values per track (copy at press, write back at release) through PUBLISH; needs the slot table and the FX track's location |

## Suggested order

1. Establish the slot of each parameter (the label table above gives names and ranges; the slot comes from the Sound-filling code) and record it here.
2. Prove on the emulator that a PUBLISH subscriber's row writes reach AUDIO's
   rows and that the next block starts from the stock value again.
3. Then one parameter effect (a filter sweep) as a Perf FX kind, with the same
   hold/latch/include behaviour as the pitch effects.
