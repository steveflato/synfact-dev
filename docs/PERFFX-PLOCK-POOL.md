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

## Open

- Row words for the FX track and the filter (PERFFX-LIVE-PARAMS.md).
- Whether restore is free (working copy rebuilt each block) or needs a saved
  copy.
- Where a global pool is stored so it survives power-off.
- How the OS builds the Sound Pool list and the sound-lock picker, and how it
  exposes trig length, conditions and page speed, so the lane can reuse them.
