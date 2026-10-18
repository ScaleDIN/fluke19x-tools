# Li-ion battery patch for V8.04: learning gauge + voltage next to the battery glyph.
#
# New code (placed at `base`):
#   cb181  - wraps UI text callback #181 (status string ending in the battery glyph 0x98-0x9e)
#            and inserts "7.88V" in front of the glyph.
#   integ  - replaces the BAM integrator call at 0x40049130 (orig 0x4004bcf4). Counts the
#            MEASURED current both ways (charge eff 99%), keeps the discharge shortfall in
#            `deficit`, marks full on the CV taper (meter off, I < 100 mA and 2*I < setpoint,
#            V > 7.9 V for 30 passes ~5 min), and redraws the battery text when V moves 10 mV.
#   learn  - replaces the +100 mAh step at the empty event (0x4004b47c): if a full mark was
#            seen since, capacity = cap - rem + deficit, clamped to 1000..11000 mAh.
# Flash word patches are in PATCHES (address -> new word); `build(base)` returns (code, patches).
#
#   python battpatch.py ram   -> ramtest.bin: RN handler calling cb181 into a buffer (test only)
import struct, sys
from armasm import Asm, listing, rot_imm

ORIG181 = 0x4013f99c
STRLEN, DIVMOD = 0x400303cc, 0x40185fa8          # DIVMOD(r0=d, r1=n) -> r0 = n/d, r1 = n%d
AFEC, GETI, GETV, GETVF, ONMODE = 0x4004afec, 0x40016d98, 0x40016d80, 0x40016d8c, 0x40006668
REDRAW = 0x400e0d60
ST = 0x44096bf0                 # BAM state: +0 flags, +0x10 rem mA*s, +0x14 cap mA*s
SETPT = 0x44051b08              # u16 charge-current setpoint, 0xfff = charger off
REDRAW_ID = 0x44052204          # u8 redraw id of the battery status text
NOTIFY = 0x44051afc             # -> UI battery handler, called with 0xf
VARS = 0x440fff00               # +0 magic +4 deficit +8 anchored +0xc count +0x10 last mV +0x14 cap copy
MAGIC = 0x54544142              # 'BATT'
MAGIC2 = 0x32544142             # 'BAT2' (v6: new layout, forces a fresh start)
BAM_INIT = 0x4004b184           # firmware gauge reset: cap = init word, rem = cap/4
BINIT_CALLS = {0x400490b4: 0xeb000832, 0x4004b2f8: 0xebffffa1}   # its two bl sites (original words)
MAH = 3600

# ---- Pack-specific settings: tuned for a 2S2P NCR18650 pack (~6000 mAh) with the charge voltage
# ---- clamped in hardware at ~8.26 V. Check every value against your own pack and meter.
SOC_RLOAD = 5                   # load compensation: mV = |I| mA / 5 (0.2 ohm)
SOC_CURVE = [(8250, 100), (8150, 92), (8000, 80), (7840, 70), (7700, 60), (7580, 50), (7480, 40),
             (7400, 30), (7300, 20), (7100, 10), (6800, 5), (6000, 0), (0, 0)]   # pack mV (corrected) -> %
V_CORR = 80                     # this meter's ADC reads ~80 mV under a DMM; measure yours
MIN_CAP, MAX_CAP, INIT_CAP = 1000 * MAH, 11000 * MAH, 6000 * MAH   # MAX_CAP <= 11000: the firmware's 95 % compare overflows above ~12500
# Also in the code below: full = meter off, 0 <= I < 100 mA, 2*I < setpoint, V > 7900 mV for 30 passes (emit_integ);
# low-battery warning 6.9 V -> 6.6 V (0x4004b4cc in build); self-discharge 1 %/day -> ~0.1 %/day (0x4004befc).
# ---- End of pack-specific settings
NOP = 0xe1a00000


def call_ptr(a, ptr_addr, arg):
    a.ldr('r0', a.lit(ptr_addr)); a.ldr('ip', 'r0', 0)
    a.mov('r0', arg); a.mov('lr', 'pc'); a.mov('pc', 'ip')


def emit_cb181(a):
    a.label('cb181')
    a.push(['r4', 'r5', 'r6', 'r7', 'lr'])
    a.mov('r4', 'r2')
    a.call(ORIG181)
    a.mov('r5', 'r0')
    a.mov('r0', 'r4'); a.call(STRLEN)
    a.cmp('r0', 0); a.b('c_done', cond='eq')
    a.add('r6', 'r4', 'r0'); a.sub('r6', 'r6', 1)          # last char
    a.ldrb('r7', 'r6', 0)
    a.cmp('r7', 0x98); a.b('c_done', cond='lt')
    a.cmp('r7', 0x9e); a.b('c_done', cond='gt')
    a.call(GETVF)
    a.add('r1', 'r0', V_CORR)
    a.mov('r0', 0x3e8); a.call(DIVMOD)                      # r0 = volts, r1 = mV rest
    a.add('r0', 'r0', 0x30); a.strb('r0', 'r6', 0)
    a.mov('r0', 0x2e); a.strb('r0', 'r6', 1)
    a.mov('r0', 10); a.call(DIVMOD)                         # r1 still rest: r0 = rest/10
    a.mov('r1', 'r0'); a.mov('r0', 10); a.call(DIVMOD)      # r0 = tenths, r1 = hundredths
    a.add('r0', 'r0', 0x30); a.strb('r0', 'r6', 2)
    a.add('r1', 'r1', 0x30); a.strb('r1', 'r6', 3)
    a.mov('r0', 0x56); a.strb('r0', 'r6', 4)                # 'V'
    a.strb('r7', 'r6', 5)                                   # glyph
    a.mov('r0', 0); a.strb('r0', 'r6', 6)
    a.label('c_done')
    a.mov('r0', 'r5')
    a.pop(['r4', 'r5', 'r6', 'r7', 'pc'])


def emit_integ(a, soc=False):
    magic = MAGIC2 if soc else MAGIC
    a.label('integ')
    a.push(['r4', 'r5', 'r6', 'r7', 'r8', 'lr'])
    a.sub('sp', 'sp', 8)
    a.ldr('r8', a.lit(ST)); a.ldr('r7', a.lit(VARS))
    # validate our variables; reset them if RAM was lost or someone else changed cap
    a.ldr('r0', 'r7', 0); a.ldr('r1', a.lit(magic)); a.cmp('r0', 'r1')
    a.b('i_newmagic' if soc else 'i_reset', cond='ne')
    a.ldr('r0', 'r7', 0x14); a.ldr('r1', 'r8', 0x14); a.cmp('r0', 'r1'); a.b('i_vok', cond='eq')
    if soc:
        a.b('i_reset')
        a.label('i_newmagic')                              # no learned cap can exist yet: start at 6000 mAh
        a.ldr('r0', a.lit(INIT_CAP)); a.str('r0', 'r8', 0x14)
    a.label('i_reset')
    a.ldr('r1', a.lit(magic)); a.str('r1', 'r7', 0)
    a.mov('r0', 0)
    a.str('r0', 'r7', 4); a.str('r0', 'r7', 8); a.str('r0', 'r7', 0xc); a.str('r0', 'r7', 0x10)
    a.ldr('r0', 'r8', 0x14); a.str('r0', 'r7', 0x14)
    if soc:
        a.mov('r0', 1); a.str('r0', 'r7', 0x18)            # estimate rem from the voltage
    a.label('i_vok')
    # empty flag: rem = 0 and tell the UI (as the original does)
    a.ldr('r0', 'r8', 0); a.tst('r0', 4); a.b('i_noempty', cond='eq')
    a.mov('r0', 0); a.str('r0', 'r8', 0x10)
    call_ptr(a, NOTIFY, 0xf)
    a.label('i_noempty')
    a.add('r0', 'sp', 4); a.call(AFEC)
    a.cmp('r0', 1); a.b('i_out', cond='ne')
    a.call(GETI); a.mov('r4', 'r0')                         # I mA, signed (+ = charging)
    if soc:
        a.ldr('r0', 'r7', 0x18); a.cmp('r0', 0); a.b('i_noest', cond='eq')
        a.mov('r0', 'r4'); a.bl('estimate')
        a.label('i_noest')
    a.ldr('r5', 'sp', 4)                                    # dt
    a.cmp('r4', 0); a.b('i_dis', cond='le')
    a.mul('r0', 'r4', 'r5')
    a.mov('r1', 99); a.mul('r1', 'r0', 'r1')              # *99/100 (no overflow below ~12 h ticks at 1 A)
    a.mov('r0', 100); a.call(DIVMOD)
    a.ldr('r1', 'r8', 0x10); a.add('r1', 'r1', 'r0')
    a.ldr('r2', 'r8', 0x14); a.cmp('r1', 'r2'); a.mov('r1', 'r2', cond='hi')
    a.str('r1', 'r8', 0x10)
    a.b('i_taper')
    a.label('i_dis')
    a.rsb('r0', 'r4', 0); a.mul('r0', 'r5', 'r0')          # drawn = -I*dt (ARMv4: Rd != Rm)
    a.ldr('r1', 'r8', 0x10); a.cmp('r1', 'r0'); a.b('i_short', cond='ls')
    a.sub('r1', 'r1', 'r0'); a.str('r1', 'r8', 0x10)
    a.b('i_taper')
    a.label('i_short')
    a.sub('r0', 'r0', 'r1')
    a.ldr('r2', 'r7', 4); a.add('r2', 'r2', 'r0'); a.str('r2', 'r7', 4)
    a.mov('r1', 0); a.str('r1', 'r8', 0x10)
    a.label('i_taper')                                      # CV taper -> full
    a.call(ONMODE); a.cmp('r0', 0); a.b('i_notaper', cond='ne')
    a.ldr('r1', a.lit(SETPT)); a.ldrh('r6', 'r1', 0)
    a.ldr('r1', a.lit(0xfff)); a.cmp('r6', 'r1'); a.b('i_notaper', cond='eq')
    a.cmp('r4', 0); a.b('i_notaper', cond='lt')
    a.cmp('r4', 100); a.b('i_notaper', cond='ge')
    a.cmp('r6', 'r4', ('lsl', 1)); a.b('i_notaper', cond='le')
    a.call(GETVF); a.ldr('r1', a.lit(7900)); a.cmp('r0', 'r1'); a.b('i_notaper', cond='le')
    a.ldr('r0', 'r7', 0xc); a.add('r0', 'r0', 1); a.str('r0', 'r7', 0xc)
    a.cmp('r0', 30); a.b('i_vdisp', cond='lt')
    a.mov('r0', 30); a.str('r0', 'r7', 0xc)
    a.ldr('r0', 'r8', 0x14); a.str('r0', 'r8', 0x10)       # rem = cap
    a.mov('r0', 0); a.str('r0', 'r7', 4)                   # deficit = 0
    a.mov('r0', 1); a.str('r0', 'r7', 8)                   # anchored
    a.b('i_vdisp')
    a.label('i_notaper')
    a.mov('r0', 0); a.str('r0', 'r7', 0xc)
    a.label('i_vdisp')                                      # redraw the voltage text on change
    a.call(GETVF)
    a.ldr('r1', 'r7', 0x10); a.sub('r2', 'r0', 'r1'); a.cmp('r2', 0); a.rsb('r2', 'r2', 0, cond='lt')
    a.cmp('r2', 10); a.b('i_notify', cond='lt')
    a.str('r0', 'r7', 0x10)
    a.ldr('r0', a.lit(REDRAW_ID)); a.ldrb('r0', 'r0', 0)
    a.cmp('r0', 0); a.b('i_notify', cond='eq')
    a.call(REDRAW)
    a.label('i_notify')
    call_ptr(a, NOTIFY, 0xf)
    a.label('i_out')
    a.add('sp', 'sp', 8)
    a.pop(['r4', 'r5', 'r6', 'r7', 'r8', 'pc'])


def emit_soc(a):
    # estimate(r0 = I mA): when the voltage is valid and we're not charging hard, rem = cap * soc(V) / 100,
    # using a 2S Li-ion resting-voltage curve; under load, V + |I| * 0.2 ohm approximates the resting voltage.
    # Not a full mark: learning still needs a real taper-full.
    a.label('estimate')
    a.push(['r4', 'r5', 'r6', 'lr'])
    a.mov('r4', 'r0')
    a.cmp('r4', 100); a.b('e_out', cond='gt')                # charging: terminal V is above rest, wait
    a.call(GETVF); a.add('r5', 'r0', V_CORR)
    a.cmp('r4', 0); a.b('e_nocomp', cond='ge')
    a.rsb('r1', 'r4', 0); a.mov('r0', SOC_RLOAD); a.call(DIVMOD)   # |I| * 0.2 ohm = |I| / 5
    a.add('r5', 'r5', 'r0')
    a.label('e_nocomp')
    a.ldr('r1', a.lit(5000)); a.cmp('r5', 'r1'); a.b('e_out', cond='lt')  # ADC not settled yet
    a.ldr('r2', a.lit('soctab'))
    a.ldrh('r0', 'r2', 0); a.cmp('r5', 'r0'); a.mov('r6', 100, cond='ge'); a.b('e_set', cond='ge')
    a.label('e_loop')
    a.ldrh('r0', 'r2', 4); a.ldrh('r1', 'r2', 6)             # next (lower) point
    a.cmp('r5', 'r0'); a.b('e_interp', cond='ge')
    a.add('r2', 'r2', 4); a.b('e_loop')
    a.label('e_interp')                                     # p = lo.p + (V - lo.v) * (hi.p - lo.p) / (hi.v - lo.v)
    a.mov('r4', 'r1')
    a.ldrh('r3', 'r2', 0); a.ldrh('ip', 'r2', 2)
    a.sub('r5', 'r5', 'r0'); a.sub('ip', 'ip', 'r1')
    a.mul('r6', 'r5', 'ip')
    a.sub('r0', 'r3', 'r0'); a.mov('r1', 'r6'); a.call(DIVMOD)
    a.add('r6', 'r0', 'r4')
    a.label('e_set')
    a.ldr('r3', a.lit(ST)); a.ldr('r1', 'r3', 0x14); a.mov('r0', 100); a.call(DIVMOD)   # cap / 100
    a.mul('r1', 'r0', 'r6')
    a.ldr('r3', a.lit(ST)); a.str('r1', 'r3', 0x10)
    a.ldr('r3', a.lit(VARS)); a.mov('r0', 0); a.str('r0', 'r3', 0x18)
    a.label('e_out')
    a.pop(['r4', 'r5', 'r6', 'pc'])
    a.label('binit')                                        # firmware gauge reset (rem = cap/4) + estimate
    a.push(['r4', 'lr'])
    a.call(BAM_INIT); a.mov('r4', 'r0')
    a.ldr('r1', a.lit(VARS)); a.mov('r2', 1); a.str('r2', 'r1', 0x18)
    a.mov('r0', 'r4')
    a.pop(['r4', 'pc'])
    a.label('soctab')
    a.bytes_(struct.pack('<%dH' % (2 * len(SOC_CURVE)), *[x for p in SOC_CURVE for x in p]))
    a.align()


def emit_learn(a, soc=False):
    magic = MAGIC2 if soc else MAGIC
    a.label('learn')
    a.push(['r4', 'r7', 'r8', 'lr'])
    a.ldr('r8', a.lit(ST)); a.ldr('r7', a.lit(VARS))
    a.ldr('r0', 'r7', 0); a.ldr('r1', a.lit(magic)); a.cmp('r0', 'r1'); a.b('l_out', cond='ne')
    a.ldr('r0', 'r7', 8); a.cmp('r0', 1); a.b('l_out', cond='ne')
    a.ldr('r0', 'r8', 0x14); a.ldr('r1', 'r8', 0x10); a.sub('r0', 'r0', 'r1')
    a.ldr('r1', 'r7', 4); a.add('r0', 'r0', 'r1')         # cap - rem + deficit
    a.ldr('r1', a.lit(MIN_CAP)); a.cmp('r0', 'r1'); a.mov('r0', 'r1', cond='lt')
    a.ldr('r1', a.lit(MAX_CAP)); a.cmp('r0', 'r1'); a.mov('r0', 'r1', cond='gt')
    a.str('r0', 'r8', 0x14); a.str('r0', 'r7', 0x14)
    a.mov('r0', 0); a.str('r0', 'r7', 8); a.str('r0', 'r7', 4)
    a.label('l_out')
    a.pop(['r4', 'r7', 'r8', 'pc'])


def bl_word(src, dst, link=True):
    off = (dst - src - 8) >> 2
    assert -(1 << 23) <= off < (1 << 23)
    return (0xeb if link else 0xea) << 24 | (off & 0xffffff)


def build(base, soc=False):
    # soc (v6): voltage-based start for the gauge after any reset, cap 6000 mAh on first run
    a = Asm(base)
    emit_cb181(a); emit_integ(a, soc); emit_learn(a, soc)
    if soc:
        emit_soc(a)
    code = a.assemble()
    L = a.labels
    patches = {
        0x401af29c + 181 * 4: L['cb181'],                   # UI callback table entry #181
        0x40049130: bl_word(0x40049130, L['integ']),        # BAM loop: bl integrator
        0x4004b47c: bl_word(0x4004b47c, L['learn']),        # empty event: learn ...
        0x4004b480: bl_word(0x4004b480, 0x4004b4c8, False), # ... then skip the +100 mAh code
        0x4004bb4c: NOP, 0x4004bb8c: NOP,                   # no timer / dT/dt "full" marks
        0x4004b3c4: NOP,                                    # no -2 mAh on charger connect
        0x4004befc: 0xe3a00000 | rot_imm(0x15000),          # self-discharge 1%/day -> ~0.1%/day
        0x4004b4cc: 0xe3a0cf72,                             # low warning 6.9 V -> 6.6 V
        0x401b0228: INIT_CAP,                               # capacity after battery reconnect
    }
    if soc:
        for site in BINIT_CALLS:                            # gauge resets -> binit (reset + estimate)
            patches[site] = bl_word(site, L['binit'])
    return a, code, patches


if __name__ == '__main__':
    if sys.argv[1:] == ['ram']:
        base = 0x440fe000
        h = Asm(base)
        h.push(['r4', 'lr']); h.sub('sp', 'sp', 0x80)
        h.add('r0', 'sp', 0x7c); h.call(0x400f6548)
        h.mov('r0', 0); h.call(0x4004da68)
        h.mov('r0', 0); h.strb('r0', 'sp', 0x10)
        h.mov('r0', 0); h.mov('r1', 0); h.add('r2', 'sp', 0x10)
        h.bl('cb181')
        h.add('r0', 'sp', 0x10); h.call(STRLEN)
        h.mov('r2', 'r0'); h.mov('r0', 3); h.add('r1', 'sp', 0x10); h.mov('r3', 3)
        h.call(0x400b5904)
        h.add('sp', 'sp', 0x80); h.pop(['r4', 'pc'])
        emit_cb181(h)
        code = h.assemble()
        open('ramtest.bin', 'wb').write(code)
        open('ramtest.lst', 'w').write(listing(code, base))
        print(len(code), 'bytes')
    else:
        a, code, patches = build(0x401e9000)
        print(listing(code, 0x401e9000))
        print(len(code), 'bytes')
        for k, v in sorted(patches.items()):
            print(f'{k:08x} <- {v:08x}')
