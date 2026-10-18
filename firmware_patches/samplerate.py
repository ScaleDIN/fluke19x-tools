# Sample-rate suffix for a timebase UI callback: #84 (bottom bar, "  10ms") from v2, #279 ("<timebase>/div") in v1.
# cb(r0, r1, r2=buf): calls the original builder, then appends " <rate>S/s" computed from
# the input-A trace's time per point (the value QW reports as x_res).
import sys
from armasm import Asm, listing

ORIG_CB = 0x40104858                             # cb #279
CB84 = 0x401348c8                                # cb #84, bottom-bar timebase
STRLEN, DIVMOD = 0x400303cc, 0x40185fa8          # DIVMOD(r0=d, r1=n) -> r0 = n/d, r1 = n%d
P_XY, P_A1, P_A2, P_B1, P_B2 = 0x4013dab4, 0x40103c28, 0x40143700, 0x400f477c, 0x401017c8
TRACE, TTYPE, XRES6, IDX, XRES1 = 0x40106c78, 0x4001dfac, 0x40077db4, 0x400ed364, 0x40021910


def pred(a, fn, arg):
    a.mov('r0', 0); a.mov('r1', 2); a.mov('r2', arg)
    a.call(fn)
    a.cmp('r0', 0)


ET_Q, ET_X = 250, 9                              # 2.5 GS/s = q 250, leading-digit exponent 9: the real-time ADC limit


def build_cb(base, a=None, orig=ORIG_CB, rate_only=False, suffix=True, et_mark=False):
    # rate_only: write just " <rate>S/s" at buf (the timebase text is only used to see if it's shown)
    # suffix=False: no "S/s", e.g. " 2.5G"
    # et_mark (v8): '*' after the unit above 2.5 GS/s (5 ns/div = 200 ps/point: not real-time samples), e.g. " 5G*"
    a = a or Asm(base)
    # frame: sp+0 arg slot, sp+0x08 handle, sp+0x10 Float10, sp+0x18 out ptr
    a.push(['r4', 'r5', 'r6', 'r7', 'r8', 'sb', 'sl', 'lr'])
    a.sub('sp', 'sp', 0x20)
    a.mov('r4', 'r2')                     # buf
    a.call(orig)
    a.mov('r5', 'r0')                     # keep original return value
    a.ldrb('r0', 'r4', 0)
    a.cmp('r0', 0)
    a.b('done', cond='eq')                # no timebase text shown -> add nothing
    if rate_only:
        a.mov('r0', 0); a.strb('r0', 'r4', 0)
    pred(a, P_XY, 1); a.b('done', cond='ne')
    a.mov('r7', 0xd)
    pred(a, P_A1, 0); a.b('gotid', cond='eq')
    pred(a, P_A2, 0); a.b('gotid', cond='eq')
    a.mov('r7', 2)
    pred(a, P_B1, 1); a.b('gotid', cond='eq')
    pred(a, P_B2, 0); a.b('gotid', cond='ne')
    a.mov('r7', 0x2a)
    a.label('gotid')
    a.mov('r0', 0); a.str('r0', 'sp', 8)
    a.mov('r1', 0); a.add('r2', 'sp', 8); a.mov('r3', 'r7')
    a.call(TRACE)
    a.ldr('r6', 'sp', 8)
    a.cmp('r6', 0); a.b('done', cond='eq')
    a.mov('r0', 'r6'); a.call(TTYPE)
    a.cmp('r0', 6); a.b('t1', cond='ne')
    a.add('r0', 'sp', 0x10); a.mov('r1', 'r6'); a.mov('r2', 0)
    a.call(XRES6)
    a.b('haveres')
    a.label('t1')
    a.cmp('r0', 1); a.b('done', cond='ne')
    a.call(IDX)
    a.mov('r2', 'r0')
    a.mov('r0', 0); a.str('r0', 'sp', 0)
    a.add('r0', 'sp', 0x10); a.mov('r1', 'r6')
    a.ldr('r3', a.lit('srcname'))
    a.call(XRES1)
    a.label('haveres')
    a.ldr('r6', 'sp', 0x14)               # m (mantissa)
    a.ldrsh('r7', 'sp', 0x12)             # e (exponent)
    a.cmp('r6', 0); a.b('done', cond='le')
    # normalise m < 100000
    a.label('norm')
    a.ldr('r0', a.lit(100000))
    a.cmp('r6', 'r0'); a.b('normdone', cond='lt')
    a.mov('r0', 10); a.mov('r1', 'r6'); a.call(DIVMOD)
    a.mov('r6', 'r0'); a.add('r7', 'r7', 1)
    a.b('norm')
    a.label('normdone')
    # num = 10^p with num >= 100*m ; q = num / m in [100, 999]
    a.mov('r8', 1); a.mov('sb', 0)        # r8 = num, sb = p
    a.add('r0', 'r6', 'r6', ('lsl', 2))   # 5m
    a.add('r0', 'r0', 'r0', ('lsl', 2))   # 25m
    a.mov('sl', 'r0', ('lsl', 2))         # 100m
    a.label('pl')
    a.cmp('r8', 'sl'); a.b('pdone', cond='ge')
    a.add('r8', 'r8', 'r8', ('lsl', 2)); a.mov('r8', 'r8', ('lsl', 1))
    a.add('sb', 'sb', 1)
    a.b('pl')
    a.label('pdone')
    a.mov('r0', 'r6'); a.mov('r1', 'r8'); a.call(DIVMOD)
    a.mov('r8', 'r0')                     # q
    # X = -e - p + 2 (exponent of the leading digit), clamp to [-3, 11]
    a.rsb('r7', 'r7', 2)
    a.sub('r7', 'r7', 'sb')
    a.cmp('r7', 11); a.mov('r7', 11, cond='gt')
    a.mvn('r0', 2); a.cmp('r7', 'r0'); a.mov('r7', 'r0', cond='lt')
    if et_mark:                           # flag = X > 9 or (X == 9 and q > 250); sp+0x18 is free after the Float10
        a.mov('r0', 0)
        a.cmp('r7', ET_X); a.mov('r0', 1, cond='gt')
        a.cmp('r8', ET_Q, cond='eq'); a.mov('r0', 1, cond='gt')
        a.str('r0', 'sp', 0x18)
    # u = floor(X/3) (in -1..3), intdigits = X - 3u + 1
    a.add('r1', 'r7', 3); a.mov('r0', 3); a.call(DIVMOD)
    a.sub('sb', 'r0', 1)                  # u
    a.add('r0', 'sb', 'sb', ('lsl', 1))   # 3u
    a.sub('sl', 'r7', 'r0'); a.add('sl', 'sl', 1)   # intdigits 1..3
    # digits d0 d1 d2 of q
    a.mov('r0', 10); a.mov('r1', 'r8'); a.call(DIVMOD)
    a.str('r1', 'sp', 0x1c)               # d2
    a.mov('r1', 'r0'); a.mov('r0', 10); a.call(DIVMOD)
    a.mov('r6', 'r1')                     # d1
    a.mov('r7', 'r0')                     # d0
    # out = buf + strlen(buf)
    a.mov('r0', 'r4'); a.call(STRLEN)
    a.add('r4', 'r4', 'r0')
    a.mov('r0', 0x20); a.strb('r0', 'r4', 0); a.add('r4', 'r4', 1)
    a.add('r0', 'r7', 0x30); a.strb('r0', 'r4', 0); a.add('r4', 'r4', 1)
    a.cmp('sl', 2); a.b('frac2', cond='lt')
    a.add('r0', 'r6', 0x30); a.strb('r0', 'r4', 0); a.add('r4', 'r4', 1)
    a.cmp('sl', 3); a.b('frac1', cond='lt')
    a.ldr('r0', 'sp', 0x1c); a.add('r0', 'r0', 0x30); a.strb('r0', 'r4', 0); a.add('r4', 'r4', 1)
    a.b('unit')
    a.label('frac2')                      # one int digit: ".d1d2" without trailing zeros
    a.ldr('r0', 'sp', 0x1c); a.orr('r0', 'r0', 'r6')
    a.cmp('r0', 0); a.b('unit', cond='eq')
    a.mov('r0', 0x2e); a.strb('r0', 'r4', 0); a.add('r4', 'r4', 1)
    a.add('r0', 'r6', 0x30); a.strb('r0', 'r4', 0); a.add('r4', 'r4', 1)
    a.ldr('r0', 'sp', 0x1c); a.cmp('r0', 0); a.b('unit', cond='eq')
    a.add('r0', 'r0', 0x30); a.strb('r0', 'r4', 0); a.add('r4', 'r4', 1)
    a.b('unit')
    a.label('frac1')                      # two int digits: ".d2" if nonzero
    a.ldr('r0', 'sp', 0x1c); a.cmp('r0', 0); a.b('unit', cond='eq')
    a.mov('r1', 0x2e); a.strb('r1', 'r4', 0); a.add('r4', 'r4', 1)
    a.add('r0', 'r0', 0x30); a.strb('r0', 'r4', 0); a.add('r4', 'r4', 1)
    a.label('unit')
    a.ldr('r1', a.lit('units'))
    a.add('r1', 'r1', 'sb')
    a.ldrb('r0', 'r1', 1)                 # units[u + 1]
    a.cmp('r0', 0x20)
    a.strb('r0', 'r4', 0, cond='ne'); a.add('r4', 'r4', 1, cond='ne')
    if et_mark:
        a.ldr('r0', 'sp', 0x18); a.cmp('r0', 0)
        a.mov('r0', ord('*'), cond='ne'); a.strb('r0', 'r4', 0, cond='ne'); a.add('r4', 'r4', 1, cond='ne')
    if suffix:
        a.mov('r0', ord('S')); a.strb('r0', 'r4', 0)
        a.mov('r0', ord('/')); a.strb('r0', 'r4', 1)
        a.mov('r0', ord('s')); a.strb('r0', 'r4', 2)
        a.mov('r0', 0); a.strb('r0', 'r4', 3)
    else:
        a.mov('r0', 0); a.strb('r0', 'r4', 0)
    a.label('done')
    a.mov('r0', 'r5')
    a.add('sp', 'sp', 0x20)
    a.pop(['r4', 'r5', 'r6', 'r7', 'r8', 'sb', 'sl', 'pc'])
    a.label('units'); a.bytes_(b'm kMG\0'); a.align()
    a.label('srcname'); a.bytes_(b'SRATE\0'); a.align()
    return a


def build_ram_test(base):
    """RN handler: run the callback into a buffer and send the buffer back. One Asm, one literal pool."""
    a = Asm(base)
    a.push(['r4', 'lr'])
    a.sub('sp', 'sp', 0x80)
    a.add('r0', 'sp', 0x7c); a.call(0x400f6548)
    a.mov('r0', 0); a.call(0x4004da68)
    a.mov('r0', 0); a.mov('r1', 0); a.add('r2', 'sp', 0x10)
    a.bl('cb')
    a.add('r0', 'sp', 0x10); a.call(STRLEN)
    a.mov('r2', 'r0'); a.mov('r0', 3); a.add('r1', 'sp', 0x10); a.mov('r3', 3)
    a.call(0x400b5904)
    a.add('sp', 'sp', 0x80)
    a.pop(['r4', 'pc'])
    a.label('cb')
    build_cb(base, a)
    return a


if __name__ == '__main__':
    base = 0x440fe000
    a = build_ram_test(base)
    code = a.assemble()
    open('ramtest.bin', 'wb').write(code)
    open('ramtest.lst', 'w').write(listing(code, base))
    print(len(code), 'bytes; callback at %#x' % a.labels['cb'])
