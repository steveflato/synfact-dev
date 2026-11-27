# Perf FX: the FX plock pool (design)

A design note, not a build. Nothing here is implemented yet. It gathers the
idea from Vedanta (discussion #11 and Discord, 26 Nov 2026), GSM's reply, and
the decisions taken with the module's author. The parameter writes it needs
are blocked on the open questions in
[PERFFX-LIVE-PARAMS.md](PERFFX-LIVE-PARAMS.md).

## Why

The stock FX track already serves as a scene machine: players keep FX track
trigs at 0% probability and fire them with TRIG + YES, each trig holding a
set of locks (Elektronauts, "Syntakt FX Scenes + Techniques"). It is limited by
the pattern's lock budget (72 locked parameters per pattern) and by the
pattern's trigs. The pool moves the states out of the pattern, so they cost no
pattern locks and can be reused anywhere.

## Pieces

**State.** A named set of parameter → value pairs ("FX 01", renameable), for
the FX track's parameters first (drive, filter, amp, sends, delay, reverb,
LFOs), later any track parameter, stock or modded (Vedanta: "a user state").
Each state also has 2–4 user slots for extra hand-locked parameters.

**Pool.** A list of states, like the Sound Pool. **Global** for now: one pool
shared by every pattern and project (decided 26 Nov). Size to be set by
storage; 16 or 32 seems realistic.

**Keys.** In Perf FX mode a state sits on an effect key: hold to apply,
release to restore (GSM's "apply on press, restore on release"); FUNC latches
as for the pitch effects; the TRK-held track picks choose which tracks a
state affects, where that applies.

**Recording a state.** Hold a free effect key and turn knobs: what is turned
is recorded into that key's state; on release the sound returns and the state
is saved to the pool.

**Lane.** One page of 16 steps (more pages later), opened in the perf mode.
- Placing: FX mod lane trig + left bottom knob opens the pool list; turning
  picks a state for the trig, as trig + LEVEL/DATA picks a sound for a sound
  lock. (Trig + left bottom knob on a normal track stays the Sound Pool.)
- Trigs behave like normal trigs and use the normal trig page: length,
  probability, conditions. The amp and filter pages may work in the lane but
  need not.
- **Trig length decides the restore**: a state holds for the trig's length,
  then the original values return (Vedanta). This replaces the earlier
  "until the next empty step" idea.
- Page speed (scale) works as on any track: 1/2, 1/4, 2x, so a state can last
  longer than 8 bars.
- The normal sequence keeps playing underneath.

**Screen.** The list shows state names only. On the lane, holding a trig to
preview shows the state's changed parameters in a compact Machinedrum-style
grid, name and value per cell (Vedanta's photo: PTCH 16, DEC 26, HARD 127,
HAMR 95, TENS 29, DAMP 122), on the SYN page of the state. Where it helps,
show the original value next to the new one.

**Later.** "Add trig to FX plock pool": turn an existing trig's locks into a
pool state.

## Build order

1. A few hard-coded states on keys, to prove the writes (needs the row words
   and the restore answer).
2. Recording a state by holding a key and turning knobs.
3. The global pool, its storage and the list page.
4. The lane: placing states, trig length restore, trig page, page speed.
5. The preview grid and user slots.

## Firmware findings (OS 1.42, read from the MAIN image, not run)

**The FX track's values live in the Kit.** The Kit record (`kit.h`: 128 live
Kits at `0x41ad6134 + k * 0x1618`, the playing one at `*0x43bd843c`, also
`*0x800030b8`) holds the 12 Sounds at `+0x2A + t * 0x123`, and right after
them, at **`+0xDCE`**, the FX track: 71 halfwords, slot `s` at `+0xDCE + 2s`
(unlike a Sound, there is no `+0x14` header). `0x400a1d90` sends it to AUDIO as
track 12: the whole record (`0x8E` bytes) to `0x80002874`, which is
`0x800021cc + 12 * 0x8E`, the stored mirror's 13th row. So the FX track uses
the same mirror arrays as the Sounds, at index 12.

Slot layout (the table at `0x43bdf5c4`, names from the master table):

| slots | page | parameters |
|---|---|---|
| 1–8, 9–16 | LFO1, LFO2 | SPD, MULT, FADE, DEST, WAVE, SPH, MODE, DEP |
| 17–25 | Delay | TIME, X, WID, FDBK, HPF, LPF, REV, VOL, routing |
| 27–34 | Reverb | PRE, DEC, FREQ, GAIN, HPF, LPF, VOL, routing |
| 35–43 | Ext-in | IN LR, BAL, DEL, REV, DUAL, IN routing |
| 44–54 | Filter | TYPE, FREQ (45), RESO (46), ENV, DEL, ATK, DEC, SUS, REL, FENR, FPAN |
| 56–69 | Amp | DEL, ATK, HOLD, DEC, SUS, REL, DRIVE (62), DEL send, REV send, PAN, VOL, MODE, DEP, AENR |

**GLOBAL FX/MIX.** `0x400a1d90` checks a flag word at `0x43bdf444` per section
(LFOs bit 5, Delay bit 6, Reverb the next, Ext-in bit 1, Filter bit 3, Amp
bit 4) and, when a bit is set, sends a global copy (from `0x41b9e3ae` on)
instead of the Kit's. The OS has a "GLOBAL FX/MIX" setting; these bits are
most likely it. A state must write whichever copy is in use.

**Lists and pickers.** The Sound Pool is a C++ class (`SoundPool`, strings
"SOUND POOL", "(NO SOUND LOCK)"); copying its view is not needed. The bank
already has a list picker (`subtype-picker.h`, 7 rows, cursor, YES/NO,
encoder) and encoder events (`ST_EV_ENC`, with the parameter, before the
native edit, consumable), and `STP_TRIG` (`0x400c3582`) tells whether a trig is
held. Trig + knob can therefore open our own list.

**Trigs.** The trig page's parameters are ordinary lockable parameters
(master table: 23 LEN, 25 COND, 33 PROB, 24 uTM, 26–28 AMP.T/FLT.T/LFO.T), and
`pattern.h` reads and writes a step's locks (`st_pattern_lock_get/set`) and
asks whether a step has a trig. `trig-pages.h` lets a module add TRIG pages
(POLY and ARP do). Where the track's page speed (scale) is stored was not
found.

**Storage for a global pool.** Preferences hold 8 keys of 8 words: too small.
The Kit tail (as LFO3's `PLH1` record) is per Kit, not global. The HZFS store
(`storage.h`, 64-byte entries) is global and is the likely home.

## Two ways to build the lane

1. **The FX track is the lane.** A state is placed as a lock of one
   "STATE" value on an FX track trig. Trig length, conditions, probability,
   page speed and the trig page all come from the native sequencer, as
   Vedanta asked, and a pattern spends one lock parameter, not one per
   parameter of the state. Needs: a lockable ID the FX track accepts for
   "STATE" (not found yet), and reading the playing trig's lock at its step.
2. **The module's own 16 steps.** Independent of the FX track, but trig
   length, conditions and page speed would all have to be rebuilt.

Option 1 fits the design better; it is the next thing to look into.

## Open

- Row words for the filter (PERFFX-LIVE-PARAMS.md) and the FX track's rows on
  the working copy and AUDIO side (index 12 by the finding above; not run).
- Whether restore is free (working copy rebuilt each block) or needs a saved
  copy.
- A lockable ID for "STATE" on the FX track, and where page speed is stored.
- The HZFS store for the pool: format and when to write it.
