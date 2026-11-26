/* SPDX-License-Identifier: GPL-2.0-or-later
 * pitchbench-st: DIAGNOSTIC. Registers a host API 4 profile buffer, 192 bytes
 * long, and shows what the core 2.9.0-mt1-bench PITCH adapter writes past the
 * profiler's own 36 words (layout in mods/core-st/native.s). Ticks are the
 * profiler's timer (0xfc07000c). The audio IRQ only adds to the counters; the
 * rows are formatted when SETTINGS draws them, outside the timed path.
 *
 * SETTINGS rows: BENCH MODE (YES: patched path / stock instruction, clears),
 * HOOK (average, maximum ticks; YES refreshes), BLOCK (average, maximum ticks
 * between two hooks = one MAIN block), HOOK % (average hook / average block),
 * BENCH CLEAR.
 */
#include <syntakt/modules.h>
#include <syntakt/host.h>

#define INVALIDATE ((void (*)(void *))0x400ffe22u)

/* byte offsets / 4 in the buffer: 36 profiler words, then ours */
enum { PREV = 36, PLO, PMAX, PN, MODE, PHI, HSUM, HMAX, HN, SSUM, SMAX, SN, WORDS };
static volatile unsigned int bench[WORDS] __attribute__((section(".data"), aligned(4)));
static const struct st_machine_host_api *host __attribute__((section(".data"))) = 0;
static char line[26] __attribute__((section(".data")));

static unsigned int put_uint(unsigned int at, unsigned int v)
{
    char t[11];
    unsigned int n = 0;
    do { t[n++] = (char)('0' + v % 10u); v /= 10u; } while (v && n < 10);
    while (n && at < sizeof line - 1) line[at++] = t[--n];
    return at;
}
static unsigned int put(unsigned int at, const char *s)
{
    while (*s && at < sizeof line - 1) line[at++] = *s++;
    return at;
}
static void end(unsigned int at) { line[at < sizeof line ? at : sizeof line - 1] = 0; }

/* what the mode in use has accumulated: sum, max, n */
static void hook(unsigned int *sum, unsigned int *max, unsigned int *n)
{
    unsigned int m = bench[MODE] ? SSUM : HSUM;
    *sum = bench[m]; *max = bench[m + 1]; *n = bench[m + 2];
}
/* average block period in ticks: the 64-bit sum is squeezed into 32 bits */
static unsigned int block_avg(void)
{
    unsigned int lo = bench[PLO], hi = bench[PHI], n = bench[PN];
    while (hi && n > 1u) { lo = (lo >> 1) | (hi << 31); hi >>= 1; n >>= 1; }
    return n ? lo / n : 0;
}

const char *pb_mode_text(void)
{
    unsigned int at = put(0, bench[MODE] ? "BENCH: STOCK N" : "BENCH: PATCHED N");
    unsigned int sum, max, n;
    hook(&sum, &max, &n);
    at = put_uint(at, n); end(at);
    return line;
}
const char *pb_hook_text(void)
{
    unsigned int sum, max, n, a, at;
    hook(&sum, &max, &n);
    at = put(0, "HOOK a");
    a = n ? sum * 10u / n : 0;
    at = put_uint(at, a / 10u); at = put(at, "."); at = put_uint(at, a % 10u);
    at = put(at, " m"); at = put_uint(at, max); end(at);
    return line;
}
const char *pb_block_text(void)
{
    unsigned int at = put(0, "BLK a");
    at = put_uint(at, block_avg()); at = put(at, " m"); at = put_uint(at, bench[PMAX]); end(at);
    return line;
}
const char *pb_pct_text(void)
{
    unsigned int sum, max, n, a, b = block_avg(), p, at;
    hook(&sum, &max, &n);
    a = n ? sum * 10u / n : 0;                  /* ticks x 10 */
    p = b ? a * 1000u / b : 0;                  /* percent x 100 */
    at = put(0, "HOOK "); at = put_uint(at, p / 100u); at = put(at, ".");
    at = put_uint(at, p / 10u % 10u); at = put_uint(at, p % 10u); at = put(at, "%"); end(at);
    return line;
}
const char *pb_clear_text(void) { return "BENCH CLEAR"; }

#define PB_LABEL(name, text)                                   \
    __asm__(".text\n.globl " #name "\n" #name ":\n"             \
            "  move.l %a0,-(%sp)\n"                             \
            "  jsr " #text "\n"                                 \
            "  move.l (%sp),%a0\n"                              \
            "  pea 3(%sp)\n"                                    \
            "  move.l %d0,-(%sp)\n"                             \
            "  move.l %a0,-(%sp)\n"                             \
            "  jsr 0x401bf31e\n"                                \
            "  lea 12(%sp),%sp\n"                               \
            "  move.l (%sp)+,%d0\n"                             \
            "  rts\n")
PB_LABEL(pb_mode_label, pb_mode_text);
PB_LABEL(pb_hook_label, pb_hook_text);
PB_LABEL(pb_block_label, pb_block_text);
PB_LABEL(pb_pct_label, pb_pct_text);
PB_LABEL(pb_clear_label, pb_clear_text);

static void clear(void)
{
    unsigned int i;
    for (i = PLO; i < WORDS; i++) if (i != MODE) bench[i] = 0;
    bench[PREV] = 0;
}
static void redraw(const unsigned int *fn) { if (fn && fn[0]) INVALIDATE((char *)fn[0] + 0x38); }
void pb_mode_select(const unsigned int *fn) { bench[MODE] ^= 1u; clear(); redraw(fn); }
void pb_mode_change(const unsigned int *fn, int amount) { (void)amount; pb_mode_select(fn); }
void pb_refresh(const unsigned int *fn) { redraw(fn); }
void pb_refresh_change(const unsigned int *fn, int amount) { (void)amount; redraw(fn); }
void pb_clear_select(const unsigned int *fn) { clear(); redraw(fn); }
void pb_draw(const unsigned int *fn, void *item, void *bmp, int x, int y)
{ (void)fn; (void)item; (void)bmp; (void)x; (void)y; }

void pb_settings(void *menu)
{
    extern void pb_mode_label(void), pb_hook_label(void), pb_block_label(void), pb_pct_label(void),
        pb_clear_label(void);
    static const void *r[5][4] = {
        {(const void *)pb_mode_label, (const void *)pb_mode_select, (const void *)pb_draw, (const void *)pb_mode_change},
        {(const void *)pb_hook_label, (const void *)pb_refresh, (const void *)pb_draw, (const void *)pb_refresh_change},
        {(const void *)pb_block_label, (const void *)pb_refresh, (const void *)pb_draw, (const void *)pb_refresh_change},
        {(const void *)pb_pct_label, (const void *)pb_refresh, (const void *)pb_draw, (const void *)pb_refresh_change},
        {(const void *)pb_clear_label, (const void *)pb_clear_select, (const void *)pb_draw, (const void *)pb_refresh_change}};
    unsigned int i;
    if (!host) return;
    for (i = 0; i < 5; i++) host->additem(menu, r[i]);
}

static int initialize(const struct st_smz_api *api)
{
    const struct st_machine_host_api *h;
    if (!api || !api->ui || !(h = api->ui->host) || h->version < 21 || !h->native_profile) return 1;
    host = h;
    clear();
    h->native_profile((unsigned int *)bench);
    return 0;
}

static const struct st_smz_subscription subscriptions[] = {{ST_EV_SETTINGS, (void (*)(void))pb_settings}};
const struct st_smz_module pitchbench_module = {"PITCH BENCH", initialize, subscriptions, 1};
