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

- A write of Filter Frequency or Resonance: the slot is 28/29 and the row word
  should be 42/43, but no write was tried.
- Whether the working copy is rebuilt from the stored values every block
  (then a write in PUBLISH wins, and nothing needs restoring) or kept across
  blocks (then a held effect must restore it). PUBLISH runs after the
  modulation, so a write there should not be undone before the send, but this
  is not verified.
- The FX track (delay, reverb, chorus) values: `kit.h` covers the 12 Sounds
  only; where the FX track's values live is not recorded.
- Analog tracks 9..12 carry the same rows? Analog voices are on a different
  path (see `poly-st`); not recorded.

## Parameter names and slots (OS 1.42), read from the MAIN image

The machine's own SYN labels (Sample Rate Redux, Bit Redux, Overdrive ...) are
strings at `0x40253c77` to `0x4025413d`: sample-rate and bit reduction exist
as controls of some machines (SRR, BR), not as a track-wide effect, so a
track-wide crusher is an audio effect for the engine. The parameter names
themselves are in the master table below. (An earlier version of this note
described a 204-entry table at `0x4022e42c` with the name in word 0; that was
a misaligned view of this table and is withdrawn.)

**Slot to parameter (digital tracks), from the OS's own tables.** The master
table is 505 entries of 52 bytes at `0x4022d83c`, indexed by engine id: word 10
is the long name, 11 the group, 12 the short name. The start-up routine
`400a5ec4` fills RAM tables, one word per Sound slot, with the engine id of
that slot's parameter (the first one at `0x43bdf6e0`, 71 words). Running that
routine alone in Unicorn (the image mapped, `memset` stubbed, nothing else)
gives, for the digital tracks:

| Slots | Parameters |
|---|---|
| 1 to 8, 9 to 16 | LFO1 and LFO2: speed, multiplier, fade, destination, waveform, start phase, trig mode, depth |
| 27 to 38 | Filter: type 27, **frequency 28**, **resonance 29**, env depth 30, env delay 31, attack 32, decay 33, sustain 34, release 35, env reset 36, base 37, width 38 |
| 39 to 51 | Amp: env delay 39, attack 40, hold 41, decay 42, sustain 43, release 44, **delay send 45, reverb send 46, pan 47, volume 48**, pre/post FX 49, mode 50, env reset 51 |
| 52, 53 | Portamento time, portamento on |
| 54 to 70 | the machine's extended controls (core-owned) |

Slots 17 to 26 are the machine's own SYN controls. A second table
(`0x43bdf5c4`) lays out the other track class: Delay, Reverb and Ext-in on
slots 17 to 43, then Filter (with Filter Pan), Amp (with Drive).

**Slot to AUDIO row word: plus 14, for slots 27 to 53.** The row words that
the AUDIO routines read, named above, fall on slot + 14: Base (slot 37) is
word `0x33`, Width (38) `0x34`, the Amp envelope's first entry (39) `0x35`
where the AMP routine starts, the multimode filter's type (27) is row `+0x52`
(word 41), and the reset flag (Amp Env Reset, 51) is word `0x41` (65). So
Filter Frequency (slot 28) should be row word 42 and Resonance (29) word 43.
That is an inference from these five agreements; it has not been run
through a write.

## What each idea needs

| Idea | Kind | Needs |
|---|---|---|
| Filter sweep, drive up, level duck | parameter write | the slot table, then a PUBLISH subscriber (a new native event handler, like `pf_native`) |
| Chorus, flanger, phaser, comb, BBD delay, reverb plate/spring | audio effect | GSM's bypass buffer and engine (CPU is the limit) |
| Beat repeat, stutter, reverse | audio effect | the same engine and a capture buffer |
| Bit crush, sample-rate reduction | audio effect, or the sample rate row words if a machine has one | engine; nothing in the row words found for it |
| Saved FX/LFO/filter states on key press | stored values | a snapshot of the working or stored values per track (copy at press, write back at release) through PUBLISH; needs the slot table and the FX track's location |

## Suggested order

1. (Done in these notes) the slot of each parameter. Next, one write on the emulator to confirm the row word (slot + 14).
2. Prove on the emulator that a PUBLISH subscriber's row writes reach AUDIO's
   rows and that the next block starts from the stock value again.
3. Then one parameter effect (a filter sweep) as a Perf FX kind, with the same
   hold/latch/include behaviour as the pitch effects.
