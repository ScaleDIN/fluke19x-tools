# Sample rate in the top band, in front of the battery text: v4 "2.5G 8.23V<glyph>" / "1.25G 8.2V<glyph>",
# v3 "2.5GS/s 8.23V<glyph>" (wrapped: the box is only ~5 glyph widths).
#   top181 - UI callback #181: battery cb181 (battpatch), then prefix the rate (samplerate.build_cb rate_only)
#   cb84h  - UI callback #84 (bottom-bar timebase, stock text): when its text changes, redraw the
#            top-band item so the rate follows the timebase. REDRAW only marks dirty + sets an event flag.
from armasm import Asm
import samplerate as sr
import battpatch as bp

STRCPY = 0x400303f4
CACHE = bp.VARS + 0x20          # last #84 text, max 15 chars + NUL
MAXLEN = 78                     # the draw routine's text buffer is 80 bytes


def emit_short_volts(a):
    # buf (r4) ends in "X.YZV<glyph>": round to "X.YV<glyph>" (8.25 -> 8.3, 8.96 -> 9.0). Any other shape: untouched.
    a.mov('r0', 'r4'); a.call(sr.STRLEN)
    a.cmp('r0', 6); a.b('s_out', cond='lt')
    a.add('r7', 'r4', 'r0'); a.sub('r7', 'r7', 6)          # r7 -> X
    a.ldrb('r0', 'r7', 5)
    a.cmp('r0', 0x98); a.b('s_out', cond='lt')
    a.cmp('r0', 0x9e); a.b('s_out', cond='gt')
    a.ldrb('r1', 'r7', 4); a.cmp('r1', ord('V')); a.b('s_out', cond='ne')
    a.ldrb('r1', 'r7', 1); a.cmp('r1', ord('.')); a.b('s_out', cond='ne')
    a.ldrb('r1', 'r7', 3); a.cmp('r1', ord('5')); a.b('s_store', cond='lt')
    a.ldrb('r1', 'r7', 2); a.add('r1', 'r1', 1)
    a.cmp('r1', ord('9') + 1); a.strb('r1', 'r7', 2, cond='ne'); a.b('s_store', cond='ne')
    a.ldrb('r2', 'r7', 0); a.cmp('r2', ord('9')); a.b('s_store', cond='ge')   # no 10.0 V: truncate
    a.add('r2', 'r2', 1); a.strb('r2', 'r7', 0)
    a.mov('r1', ord('0')); a.strb('r1', 'r7', 2)
    a.label('s_store')
    a.mov('r1', ord('V')); a.strb('r1', 'r7', 3)
    a.strb('r0', 'r7', 4)
    a.mov('r1', 0); a.strb('r1', 'r7', 5)
    a.label('s_out')


REC_PRED = 0x4010208c           # (0, 2, 1) != 0: recorder screen (badge cb #182: RECORDING/WAITING/ZOOM/STOPPED)


def build(base, batt_cb181, adaptive=True, rec_check=False):
    # adaptive (v4): no "S/s", and one voltage decimal when the rate has 3+ digits (box is ~5 glyphs wide).
    # adaptive=False rebuilds v3 ("2.5GS/s 8.23V").
    a = Asm(base)
    a.label('top181')
    regs = ['r4', 'r5', 'r6', 'r7', 'r8'] if adaptive else ['r4', 'r5', 'r6', 'r8']
    a.push(regs + ['lr'])
    a.sub('sp', 'sp', 0x70)
    a.mov('r4', 'r2')
    if adaptive:
        # Only the plain scope screen has room: stock text exactly " <glyph>" AND a rate shown.
        # Anything else (meter mode's AUTO badge, "LOW AMPL", "RS-232", ...) stays 100% stock.
        a.call(bp.ORIG181)                                 # r0-r2 passed through
        a.mov('r5', 'r0')
        a.mov('r0', 'r4'); a.call(sr.STRLEN); a.cmp('r0', 2); a.b('t_out', cond='ne')
        a.ldrb('r0', 'r4', 0); a.cmp('r0', 0x20); a.b('t_out', cond='ne')
        a.ldrb('r0', 'r4', 1)
        a.cmp('r0', 0x98); a.b('t_out', cond='lt')
        a.cmp('r0', 0x9e); a.b('t_out', cond='gt')
        if rec_check:                                      # v7: Scope Record's badge sits on this line
            a.mov('r0', 0); a.mov('r1', 2); a.mov('r2', 1); a.call(REC_PRED)
            a.cmp('r0', 0); a.b('t_out', cond='ne')
    else:
        a.call(batt_cb181)                                 # r0-r2 passed through
        a.mov('r5', 'r0')
    a.mov('r0', 0); a.strb('r0', 'sp', 0)
    a.mov('r0', 0); a.mov('r1', 0); a.mov('r2', 'sp')      # (0, 0) as RAM-tested on #84
    a.bl('rate')
    a.ldrb('r0', 'sp', 0); a.cmp('r0', 0); a.b('t_out', cond='eq')
    if adaptive:
        a.mov('r0', 0); a.mov('r1', 0); a.mov('r2', 'r4')  # now add the voltage (draw routine passes 0, 0)
        a.call(batt_cb181)
        a.mov('r5', 'r0')
        a.mov('r0', 'sp'); a.mov('r6', 0)                  # count digits in the rate
        a.label('t_dig')
        a.ldrb('r1', 'r0', 0); a.cmp('r1', 0); a.b('t_digdone', cond='eq')
        a.add('r0', 'r0', 1)
        a.sub('r1', 'r1', 0x30); a.cmp('r1', 9); a.add('r6', 'r6', 1, cond='ls')
        a.b('t_dig')
        a.label('t_digdone')
        a.cmp('r6', 3); a.b('t_long', cond='lt')
        emit_short_volts(a)
        a.label('t_long')
    a.mov('r0', 'sp'); a.call(sr.STRLEN); a.mov('r8', 'r0')         # n, incl. leading space
    a.mov('r0', 'r4'); a.call(sr.STRLEN); a.add('r6', 'r0', 'r8')
    a.cmp('r6', MAXLEN - 1); a.b('t_out', cond='ge')                # room for a separator too
    a.add('r0', 'sp', 'r8')
    a.ldrb('r1', 'r4', 0); a.cmp('r1', 0x20)                         # separator unless buf starts with one
    a.mov('r1', 0x20, cond='ne'); a.strb('r1', 'r0', 0, cond='ne'); a.add('r0', 'r0', 1, cond='ne')
    a.mov('r1', 'r4'); a.call(STRCPY)                                # tmp = " 2.5GS/s" [+ " "] + buf
    a.mov('r0', 'r4'); a.add('r1', 'sp', 1); a.call(STRCPY)         # buf = tmp without the space
    a.label('t_out')
    a.mov('r0', 'r5')
    a.add('sp', 'sp', 0x70)
    a.pop(regs + ['pc'])

    a.label('cb84h')
    a.push(['r4', 'r5', 'r6', 'lr'])
    a.mov('r4', 'r2')
    a.call(sr.CB84)
    a.mov('r5', 'r0')
    a.mov('r2', 'r4'); a.ldr('r3', a.lit(CACHE)); a.mov('r6', 0)
    a.label('h_cmp')
    a.ldrb('r0', 'r2', 0); a.ldrb('r1', 'r3', 0)
    a.cmp('r0', 'r1'); a.b('h_new', cond='ne')
    a.cmp('r0', 0); a.b('h_out', cond='eq')
    a.add('r2', 'r2', 1); a.add('r3', 'r3', 1); a.add('r6', 'r6', 1)
    a.cmp('r6', 15); a.b('h_cmp', cond='lt')
    a.b('h_out')                                           # first 15 chars equal
    a.label('h_new')
    a.mov('r2', 'r4'); a.ldr('r3', a.lit(CACHE)); a.mov('r6', 0)
    a.label('h_cpy')
    a.ldrb('r0', 'r2', 0); a.strb('r0', 'r3', 0)
    a.cmp('r0', 0); a.b('h_redraw', cond='eq')
    a.add('r2', 'r2', 1); a.add('r3', 'r3', 1); a.add('r6', 'r6', 1)
    a.cmp('r6', 15); a.b('h_cpy', cond='lt')
    a.mov('r0', 0); a.strb('r0', 'r3', 0)
    a.label('h_redraw')
    a.ldr('r0', a.lit(bp.REDRAW_ID)); a.ldrb('r0', 'r0', 0)
    a.cmp('r0', 0); a.b('h_out', cond='eq')
    a.call(bp.REDRAW)
    a.label('h_out')
    a.mov('r0', 'r5')
    a.pop(['r4', 'r5', 'r6', 'pc'])

    a.label('rate')
    sr.build_cb(base, a, orig=sr.CB84, rate_only=True, suffix=not adaptive)
    return a
