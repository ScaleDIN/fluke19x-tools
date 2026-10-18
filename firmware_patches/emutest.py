# Emulator tests for battpatch.py (integ, learn, cb181) with unicorn.
# The real flash image is mapped at 0x40000000 with the patch code at 0x401e9000; firmware
# helpers the code calls are replaced by Python stubs. DIVMOD and strlen run from the image.
import struct, sys
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm_const import *
import battpatch as bp

IMG = sys.argv[1] if len(sys.argv) > 1 else sys.exit(f'usage: python {sys.argv[0]} <your 2 MB V08.04 dump>')
BASE = 0x401e9000
STOP = 0x40ff0000
NOTIFY_STUB = 0x40fe0000
MAH = 3600


class Meter:
    def __init__(self, soc=False):
        a, code, self.patches = bp.build(BASE, soc)
        self.L = a.labels
        self.u = u = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        u.mem_map(0x40000000, 0x01000000)
        img = bytearray(open(IMG, 'rb').read())
        img[BASE - 0x40000000:BASE - 0x40000000 + len(code)] = code
        u.mem_write(0x40000000, bytes(img))
        u.mem_map(0x44000000, 0x00200000)
        self.I, self.dt, self.afec, self.vf, self.onmode = 0, 10, 1, 8000, 0
        self.notified = self.redrawn = 0
        self.stubs = {bp.AFEC: self.s_afec, bp.GETI: lambda: self.ret(self.I & 0xffffffff),
                      bp.GETVF: lambda: self.ret(self.vf), bp.ONMODE: lambda: self.ret(self.onmode),
                      bp.REDRAW: self.s_redraw, NOTIFY_STUB: self.s_notify,
                      bp.ORIG181: self.s_orig181}
        u.hook_add(UC_HOOK_CODE, self.hook)
        self.w32(bp.NOTIFY, NOTIFY_STUB)
        self.w8(bp.REDRAW_ID, 7)
        self.orig181 = b' \x9c'

    def w32(self, a, v): self.u.mem_write(a, struct.pack('<I', v & 0xffffffff))
    def r32(self, a): return struct.unpack('<I', self.u.mem_read(a, 4))[0]
    def w16(self, a, v): self.u.mem_write(a, struct.pack('<H', v))
    def w8(self, a, v): self.u.mem_write(a, bytes([v]))

    def ret(self, v):
        self.u.reg_write(UC_ARM_REG_R0, v)
        self.u.reg_write(UC_ARM_REG_PC, self.u.reg_read(UC_ARM_REG_LR))

    def s_afec(self):
        self.w32(self.u.reg_read(UC_ARM_REG_R0), self.dt); self.ret(self.afec)

    def s_redraw(self):
        assert self.u.reg_read(UC_ARM_REG_R0) == 7
        self.redrawn += 1; self.ret(0)

    def s_notify(self):
        assert self.u.reg_read(UC_ARM_REG_R0) == 0xf
        self.notified += 1; self.ret(0)

    def s_orig181(self):
        self.u.mem_write(self.u.reg_read(UC_ARM_REG_R2), self.orig181 + b'\0'); self.ret(0x55)

    def hook(self, u, addr, size, _):
        f = self.stubs.get(addr)
        if f: f()

    def call(self, label, r0=0, r1=0, r2=0):
        u = self.u
        u.reg_write(UC_ARM_REG_SP, 0x44100000 - 0x100)
        u.reg_write(UC_ARM_REG_LR, STOP)
        for r, v in ((UC_ARM_REG_R0, r0), (UC_ARM_REG_R1, r1), (UC_ARM_REG_R2, r2), (UC_ARM_REG_R8, 0xdead)):
            u.reg_write(r, v)
        u.emu_start(self.L[label], STOP, count=20000)
        assert u.reg_read(UC_ARM_REG_SP) == 0x44100000 - 0x100, 'stack not balanced'
        assert u.reg_read(UC_ARM_REG_R8) == 0xdead, 'r8 not preserved'
        return u.reg_read(UC_ARM_REG_R0)

    # state helpers
    flags = property(lambda s: s.r32(bp.ST), lambda s, v: s.w32(bp.ST, v))
    rem = property(lambda s: s.r32(bp.ST + 0x10), lambda s, v: s.w32(bp.ST + 0x10, v))
    cap = property(lambda s: s.r32(bp.ST + 0x14), lambda s, v: s.w32(bp.ST + 0x14, v))
    deficit = property(lambda s: s.r32(bp.VARS + 4), lambda s, v: s.w32(bp.VARS + 4, v))
    anchored = property(lambda s: s.r32(bp.VARS + 8), lambda s, v: s.w32(bp.VARS + 8, v))
    count = property(lambda s: s.r32(bp.VARS + 0xc))
    setpoint = property(None, lambda s, v: s.w16(bp.SETPT, v))


fails = 0
def check(name, cond, info=''):
    global fails
    print(('PASS ' if cond else 'FAIL ') + name + (f'  [{info}]' if info else ''))
    fails += not cond


def fresh(cap=6000, rem=3000):
    m = Meter()
    m.w32(bp.VARS, 0x12345678)                  # garbage, like power-on RAM
    m.cap, m.rem, m.flags = cap * MAH, rem * MAH, 0
    m.setpoint = 0xfff
    return m


# 1. discharge and variable reset
m = fresh(); m.I = -900; m.call('integ')
check('vars reset on bad magic', m.r32(bp.VARS) == bp.MAGIC and m.anchored == 0 and m.deficit == 0)
check('discharge -900 mA x 10 s', m.rem == 3000 * MAH - 9000, m.rem)
check('notify called once', m.notified == 1, m.notified)

# 2. charge with 99 % efficiency, measured current
m = fresh(); m.I = 500; m.call('integ')
check('charge +500 mA x 10 s x 0.99', m.rem == 3000 * MAH + 4950, m.rem - 3000 * MAH)

# 3. clamp at capacity
m = fresh(rem=5999); m.I = 1000; m.dt = 3600; m.call('integ')
check('charge clamps at cap', m.rem == m.cap)

# 4. discharge past zero -> deficit
m = fresh(rem=1); m.I = -1000; m.call('integ')
check('rem floors at 0', m.rem == 0)
check('shortfall goes to deficit', m.deficit == 10000 - MAH, m.deficit)
m.call('integ')
check('deficit accumulates', m.deficit == 20000 - MAH, m.deficit)

# 5. no afec tick -> nothing
m = fresh(); m.afec = 0; m.I = -900; m.call('integ')
check('no tick: rem unchanged, no notify', m.rem == 3000 * MAH and m.notified == 0)

# 6. taper detection
def taper(onmode=0, sp=1000, I=50, vf=8200, n=30):  # n qualifying passes
    m = fresh(rem=5000); m.deficit = 0
    m.call('integ')                              # first pass resets vars
    m.deficit = 777 * MAH
    m.onmode, m.setpoint, m.I, m.vf = onmode, sp, I, vf
    for _ in range(n): m.call('integ')
    return m
m = taper(n=29)
check('taper: not full after 29 passes', m.anchored == 0 and m.count == 29, m.count)
m = taper(n=30)
check('taper: full after 30 passes', m.anchored == 1 and m.rem == m.cap and m.deficit == 0)
check('taper: off-mode only', taper(onmode=1).anchored == 0)
check('taper: needs charger on', taper(sp=0xfff).anchored == 0)
check('taper: I must be < 100', taper(I=100).anchored == 0)
check('taper: I must be well below setpoint', taper(sp=90, I=50).anchored == 0)
check('taper: sp 90, I 40 counts', taper(sp=90, I=40).anchored == 1)
check('taper: V must be > 7.9 V', taper(vf=7900).anchored == 0)
check('taper: discharging never counts', taper(I=-5).anchored == 0)
m = fresh(rem=5000); m.call('integ'); m.setpoint, m.I, m.vf = 1000, 50, 8200
for _ in range(10): m.call('integ')
m.I = 300; m.call('integ')
check('taper: count resets when current rises', m.count == 0, m.count)

# 7. voltage redraw
m = fresh(); m.vf = 8000; m.call('integ'); r0 = m.redrawn
m.vf = 8005; m.call('integ'); r1 = m.redrawn
m.vf = 8015; m.call('integ'); r2 = m.redrawn
m.vf = 7990; m.call('integ'); r3 = m.redrawn
check('redraw on first value', r0 == 1, r0)
check('no redraw for 5 mV', r1 == 1, r1)
check('redraw for +15 mV', r2 == 2, r2)
check('redraw for -25 mV', r3 == 3, r3)
m = fresh(); m.w8(bp.REDRAW_ID, 0); m.stubs[bp.REDRAW] = lambda: (_ for _ in ()).throw(AssertionError('redraw with id 0'))
m.call('integ'); check('no redraw when id is 0', True)

# 8. empty flag
m = fresh(); m.flags = 4; m.afec = 0; m.call('integ')
check('empty flag: rem = 0 and notify', m.rem == 0 and m.notified == 1)

# 9. cap changed by firmware reinit -> anchor cleared
m = fresh(); m.call('integ'); m.anchored = 1
m.cap = 3500 * MAH; m.call('integ')
check('cap change clears anchor', m.anchored == 0)

# 10. learning
def learn(cap, rem, deficit, anchored=1):
    m = fresh(cap=cap, rem=rem); m.call('integ')
    m.anchored, m.deficit = anchored, deficit * MAH
    m.call('learn')
    return m
m = learn(6000, 500, 0)
check('learn: rem left -> smaller cap', m.cap == 5500 * MAH and m.r32(bp.VARS + 0x14) == m.cap, m.cap / MAH)
check('learn: clears anchor and deficit', m.anchored == 0 and m.deficit == 0)
check('learn: deficit -> bigger cap', learn(6000, 0, 1500).cap == 7500 * MAH)
check('learn: clamp high 11000', learn(6000, 0, 20000).cap == 11000 * MAH)
check('learn: clamp low 1000', learn(6000, 5800, 0).cap == 1000 * MAH)
check('learn: no anchor -> unchanged', learn(6000, 500, 0, anchored=0).cap == 6000 * MAH)
m = learn(6000, 0, 3000); m.call('integ')
check('integ keeps learned cap (no reset)', m.cap == 9000 * MAH and m.r32(bp.VARS) == bp.MAGIC)

# 11. full cycle: full mark, discharge 6500 mAh with clamp at 0, learn
m = fresh(cap=6000, rem=3000); m.call('integ')
m.setpoint, m.I, m.vf = 1000, 50, 8200
for _ in range(30): m.call('integ')
m.setpoint, m.onmode, m.I, m.dt = 0xfff, 1, -1000, 36   # 10 mAh per pass
for _ in range(650): m.call('integ')
m.call('learn')
check('full cycle learns 6500 mAh', m.cap == 6500 * MAH, m.cap / MAH)

# 12. cb181 voltage text
def cb(orig, vf):
    m = Meter(); m.orig181 = orig; m.vf = vf
    buf = 0x44080000
    rv = m.call('cb181', 0, 0, buf)
    return rv, bytes(m.u.mem_read(buf, 16)).split(b'\0')[0]
check('cb181: " 7.88V" + glyph', cb(b' \x9b', 7800) == (0x55, b' 7.88V\x9b'), cb(b' \x9b', 7800))
check('cb181: rounding 8.18 -> 8.26', cb(b'X \x98', 8180)[1] == b'X 8.26V\x98', cb(b'X \x98', 8180))
check('cb181: 6.00 V', cb(b' \x9a', 5920)[1] == b' 6.00V\x9a', cb(b' \x9a', 5920))
check('cb181: no glyph -> untouched', cb(b'AUTO', 8000)[1] == b'AUTO')
check('cb181: empty string -> untouched', cb(b'', 8000)[1] == b'')

# 13. v6: gauge start from the voltage
def soc_meter(cap=6000, rem=3000, magic_ok=True):
    m = Meter(soc=True)
    m.cap, m.rem, m.flags, m.setpoint = cap * MAH, rem * MAH, 0, 0xfff
    m.w32(bp.VARS, bp.MAGIC2 if magic_ok else bp.MAGIC)    # old v1-v5 magic = first run of v6
    m.w32(bp.VARS + 0x14, m.cap); m.w32(bp.VARS + 0x18, 0)
    def s_init():                                           # firmware reset: cap = init word, rem = cap/4
        m.cap = bp.INIT_CAP; m.rem = bp.INIT_CAP // 4; m.ret(0x77)
    m.stubs[bp.BAM_INIT] = s_init
    return m
pending = lambda m: m.r32(bp.VARS + 0x18)
pct = lambda m: round(m.rem * 100 / m.cap)

m = soc_meter(cap=3500, rem=878, magic_ok=False); m.I, m.vf = 0, 8150 - bp.V_CORR; m.call('integ')
check('v6 first run: cap forced to 6000 mAh', m.cap == 6000 * MAH, m.cap / MAH)
check('v6 first run: estimated from 8.15 V -> 92 %', pct(m) == 92 and pending(m) == 0, (pct(m), pending(m)))
check('v6 first run: not anchored', m.anchored == 0)
m = soc_meter(rem=3000); m.I, m.vf = 0, 8150 - bp.V_CORR; m.call('integ')
check('valid magic, no reset: no estimate', m.rem == 3000 * MAH, m.rem / MAH)
m = soc_meter(); rv = m.call('binit')
check('binit: calls firmware init, returns its r0, sets pending', rv == 0x77 and pending(m) == 1 and m.rem == 1500 * MAH)
m.I, m.vf = -900, 7400 - bp.V_CORR; m.call('integ')            # 7.40 + 0.9 A * 0.2 = 7.58 V -> 50 %
check('estimate under 0.9 A load: 7.40 V + 0.18 V -> 50 %', pct(m) == 50 and pending(m) == 0, pct(m))
m = soc_meter(); m.call('binit'); m.I, m.vf = 500, 8000; m.call('integ')
check('charging > 100 mA: wait', pending(m) == 1)
m.I, m.vf = 8, 8250 - bp.V_CORR; m.call('integ')
check('trickle 8 mA: estimate', pending(m) == 0 and pct(m) == 100, pct(m))
m = soc_meter(); m.call('binit'); m.I, m.vf = 0, 3000; m.call('integ')
check('ADC not settled (< 5 V): wait', pending(m) == 1 and m.rem == 1500 * MAH)
m = soc_meter(); m.call('binit'); m.afec = 0; m.I, m.vf = 0, 8000; m.call('integ')
check('no tick: no estimate', pending(m) == 1)
for v, want in ((8400, 100), (8250, 100), (8200, 96), (7700, 60), (7640, 55), (6900, 6), (6000, 0), (5500, 0)):
    m = soc_meter(); m.call('binit'); m.I, m.vf = 0, v - bp.V_CORR; m.call('integ')
    check(f'curve {v} mV -> {want} %', pct(m) == want, pct(m))
m = soc_meter(cap=9000); m.call('binit'); m.cap = 9000 * MAH; m.w32(bp.VARS + 0x14, m.cap)
m.I, m.vf = 0, 7580 - bp.V_CORR; m.call('integ')
check('learned 9000 mAh cap kept, 50 % -> 4500 mAh', m.cap == 9000 * MAH and m.rem == 4500 * MAH, m.rem / MAH)
m = soc_meter(); m.call('binit'); m.cap = 3500 * MAH; m.I, m.vf = 0, 8000 - bp.V_CORR; m.call('integ')
check('firmware changed cap: reset path also estimates', pct(m) == 80 and pending(m) == 0, pct(m))

print('\n%d failures' % fails)
sys.exit(1 if fails else 0)
