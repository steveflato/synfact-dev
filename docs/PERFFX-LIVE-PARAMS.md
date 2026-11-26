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

- Which slot is FILTER FREQ / RESONANCE, and the scale of each slot. The
  docs name AMP, BASE/WDTH and drive words only. A table is needed from the
  stock disassembly (`core_machine_defaults` writes the slots).
- Whether the working copy is rebuilt from the stored values every block
  (then a write in PUBLISH wins, and nothing needs restoring) or kept across
  blocks (then a held effect must restore it). PUBLISH runs after the
  modulation, so a write there should not be undone before the send, but this
  is not verified.
- The FX track (delay, reverb, chorus) values: `kit.h` covers the 12 Sounds
  only; where the FX track's values live is not recorded.
- Analog tracks 9..12 carry the same rows? Analog voices are on a different
  path (see `poly-st`); not recorded.

## What each idea needs

| Idea | Kind | Needs |
|---|---|---|
| Filter sweep, drive up, level duck | parameter write | the slot table, then a PUBLISH subscriber (a new native event handler, like `pf_native`) |
| Chorus, flanger, phaser, comb, BBD delay, reverb plate/spring | audio effect | GSM's bypass buffer and engine (CPU is the limit) |
| Beat repeat, stutter, reverse | audio effect | the same engine and a capture buffer |
| Bit crush, sample-rate reduction | audio effect, or the sample rate row words if a machine has one | engine; nothing in the row words found for it |
| Saved FX/LFO/filter states on key press | stored values | a snapshot of the working or stored values per track (copy at press, write back at release) through PUBLISH; needs the slot table and the FX track's location |

## Suggested order

1. Extract the slot table (names and scales) from the stock MAIN image and record it here.
2. Prove on the emulator that a PUBLISH subscriber's row writes reach AUDIO's
   rows and that the next block starts from the stock value again.
3. Then one parameter effect (a filter sweep) as a Perf FX kind, with the same
   hold/latch/include behaviour as the pitch effects.
