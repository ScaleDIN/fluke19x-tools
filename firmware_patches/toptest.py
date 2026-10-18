# Emulator tests for toppatch.py (top181, cb84h) with unicorn, on the real image.
# Stubbed: stock #181, battery voltage, the rate formatter, stock #84, REDRAW. strlen/strcpy/DIVMOD
# and the battery cb181 run for real.
import struct, sys
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm_const import *
import battpatch as bp
import samplerate as sr
import toppatch as tp

IMG = sys.argv[1] if len(sys.argv) > 1 else sys.exit(f'usage: python {sys.argv[0]} <your 2 MB V08.04 dump>')
BATT, TOP, STOP = 0x401e9000, 0x401e9800, 0x40ff0000
SP0, BUF = 0x44100000 - 0x400, 0x440f0000
SAVED = [UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8,
         UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11]
fails = 0


def check(name, ok):
    global fails
    print(('PASS ' if ok else 'FAIL ') + name)
    fails += not ok


class M:
    def __init__(self, adaptive=False, rec_check=False, et_mark=False):
        b, bcode, _ = bp.build(BATT)
        t = tp.build(TOP, b.labels['cb181'], adaptive=adaptive, rec_check=rec_check, et_mark=et_mark)
        tcode = t.assemble()
        self.L = t.labels
        img = bytearray(open(IMG, 'rb').read())
        img[BATT - 0x40000000:BATT - 0x40000000 + len(bcode)] = bcode
        img[TOP - 0x40000000:TOP - 0x40000000 + len(tcode)] = tcode
        self.u = u = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        u.mem_map(0x40000000, 0x01000000); u.mem_write(0x40000000, bytes(img))
        u.mem_map(0x44000000, 0x00200000)
        u.mem_write(bp.REDRAW_ID, b'\x07')
        self.orig181, self.vf, self.rate, self.tb, self.redraws = b' \x9c', 8150, b' 2.5GS/s', b'  10ms', []
        self.stubs = {bp.ORIG181: lambda: self.fill(self.orig181, 0x55), bp.GETVF: lambda: self.ret(self.vf),
                      self.L['rate']: lambda: self.fill(self.rate, 0x66), sr.CB84: lambda: self.fill(self.tb, 0xc000),
                      bp.REDRAW: self.s_redraw}
        u.hook_add(UC_HOOK_CODE, lambda u, a, s, _: self.stubs[a]() if a in self.stubs else None)

    def ret(self, v):
        self.u.reg_write(UC_ARM_REG_R0, v); self.u.reg_write(UC_ARM_REG_PC, self.u.reg_read(UC_ARM_REG_LR))

    def fill(self, s, r):
        self.u.mem_write(self.u.reg_read(UC_ARM_REG_R2), s + b'\0'); self.ret(r)

    def s_redraw(self):
        self.redraws.append(self.u.reg_read(UC_ARM_REG_R0)); self.ret(0)

    def call(self, label, guard=b''):
        u = self.u
        u.mem_write(BUF - 16, b'\xaa' * 16 + b'\0' * 80 + b'\xbb' * 16)
        if guard: u.mem_write(BUF, guard + b'\0')
        u.reg_write(UC_ARM_REG_SP, SP0); u.reg_write(UC_ARM_REG_LR, STOP)
        u.reg_write(UC_ARM_REG_R2, BUF)
        for i, r in enumerate(SAVED): u.reg_write(r, 0x1000 + i)
        u.emu_start(self.L[label], STOP, count=50000)
        ok = u.reg_read(UC_ARM_REG_SP) == SP0 and all(u.reg_read(r) == 0x1000 + i for i, r in enumerate(SAVED))
        ok = ok and bytes(u.mem_read(BUF - 16, 16)) == b'\xaa' * 16 and bytes(u.mem_read(BUF + 80, 16)) == b'\xbb' * 16
        s = bytes(u.mem_read(BUF, 80)); return u.reg_read(UC_ARM_REG_R0), s[:s.index(0)], ok

    def cache(self):
        s = bytes(self.u.mem_read(tp.CACHE, 17)); return s[:s.index(0)]


print('--- v3 (adaptive=False)')
m = M()
r, s, ok = m.call('top181')
check(f'top181 rate + voltage: {s!r}', s == b'2.5GS/s 8.23V\x9c' and r == 0x55 and ok)
m.rate = b''
r, s, ok = m.call('top181')
check(f'top181 no rate shown -> battery text only: {s!r}', s == b' 8.23V\x9c' and r == 0x55 and ok)
m.rate, m.orig181 = b' 12.5kS/s', b'HOLD \x98'
r, s, ok = m.call('top181')
check(f'top181 with status text: {s!r}', s == b'12.5kS/s HOLD 8.23V\x98' and ok)
m.orig181 = b'x' * 68 + b' \x9a'
r, s, ok = m.call('top181')
check(f'top181 long status -> rate skipped, no overflow (len {len(s)})', s == b'x' * 68 + b' 8.23V\x9a' and ok)
m.orig181 = b'x' * 60 + b' \x9a'
r, s, ok = m.call('top181')
check(f'top181 fits under 78 chars (len {len(s)})', s == b'12.5kS/s ' + b'x' * 60 + b' 8.23V\x9a' and ok)

m.redraws = []
r, s, ok = m.call('cb84h')
check('cb84h first call: text passed through, redraw once', s == b'  10ms' and r == 0xc000 and m.redraws == [7] and m.cache() == b'  10ms' and ok)
r, s, ok = m.call('cb84h')
check('cb84h same text: no redraw', m.redraws == [7] and ok)
m.tb = b'  20ms'
r, s, ok = m.call('cb84h')
check('cb84h new timebase: redraw', m.redraws == [7, 7] and m.cache() == b'  20ms' and ok)
m.tb = b'A' * 20
r, s, ok = m.call('cb84h')
check('cb84h 20-char text: cache capped at 15, redraw', m.redraws == [7] * 3 and m.cache() == b'A' * 15 and s == b'A' * 20 and ok)
r, s, ok = m.call('cb84h')
check('cb84h same 20-char text: no redraw', m.redraws == [7] * 3 and ok)
m.tb = b''
r, s, ok = m.call('cb84h')
check('cb84h empty text: redraw, cache empty', m.redraws == [7] * 4 and m.cache() == b'' and ok)
m.u.mem_write(bp.REDRAW_ID, b'\x00'); m.tb = b'  1s'
r, s, ok = m.call('cb84h')
check('cb84h redraw id 0: no REDRAW call', m.redraws == [7] * 4 and ok)
print('--- v4 (adaptive)')
m = M(adaptive=True)


def v4(rate, vf, want, name, orig=b' \x9c'):
    m.rate, m.vf, m.orig181 = rate, vf, orig
    r, s, ok = m.call('top181')
    check(f'v4 {name}: {s!r}', s == want and r == 0x55 and ok)


v4(b' 2.5G', 8150, b'2.5G 8.23V\x9c', '2 digits keeps 2 decimals')
v4(b' 50k', 8150, b'50k 8.23V\x9c', '50k keeps 2 decimals')
v4(b' 5k', 8150, b'5k 8.23V\x9c', '1 digit keeps 2 decimals')
v4(b' 1.25G', 8150, b'1.25G 8.2V\x9c', '1.25G: 8.23 -> 8.2')
v4(b' 12.5k', 8170, b'12.5k 8.3V\x9c', '12.5k: 8.25 -> 8.3 (round half up)')
v4(b' 625M', 8164, b'625M 8.2V\x9c', '625M: 8.24 -> 8.2')
v4(b' 1.25G', 8880, b'1.25G 9.0V\x9c', 'carry 8.96 -> 9.0')
v4(b' 1.25G', 9900, b'1.25G 9.9V\x9c', '9.98: no 10.0, truncates to 9.9')
v4(b' 1.25G', 7920, b'1.25G 8.0V\x98', '8.00 -> 8.0, plug glyph', orig=b' \x98')
v4(b' 2.5G', 8150, b'2.5G 8.23V\x9a', 'plain scope, battery glyph', orig=b' \x9a')
v4(b'', 8150, b' \x98', 'meter mode (no rate) -> stock, no voltage', orig=b' \x98')
v4(b'', 8150, b'LOW AMPL \x98', 'spectrum: status, no rate -> stock', orig=b'LOW AMPL \x98')
v4(b' 5M', 8150, b'RS-232 \x98', 'bus health: status + rate -> stock', orig=b'RS-232 \x98')
v4(b' 1.25G', 8150, b'HOLD \x9e', 'any status text -> stock', orig=b'HOLD \x9e')
v4(b' 1.25G', 8150, b'abc', 'no glyph -> stock', orig=b'abc')
v4(b' 1.25G', 8150, b'', 'empty -> stock', orig=b'')
v4(b' 1.25G', 8150, b'  \x9c', 'two spaces + glyph (len 3) -> stock', orig=b'  \x9c')
print('--- v7 (adaptive + recorder check)')
m = M(adaptive=True, rec_check=True)
m.recorder, m.rec_args = 0, []
def s_rec():
    m.rec_args.append(tuple(m.u.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2)))
    m.ret(m.recorder)
m.stubs[tp.REC_PRED] = s_rec
v4(b' 2.5G', 8150, b'2.5G 8.23V\x9c', 'v7 plain scope (not recorder)')
check(f'v7 predicate called with (0, 2, 1): {m.rec_args}', m.rec_args == [(0, 2, 1)])
m.recorder = 1
v4(b' 2.5k', 8150, b' \x9c', 'v7 Scope Record -> stock')
m.recorder = 0x100
v4(b' 2.5k', 8150, b' \x98', 'v7 recorder (any non-zero) -> stock', orig=b' \x98')
m.recorder, m.rec_args = 1, []
v4(b' 2.5k', 8150, b'LOW AMPL \x98', 'v7 status text -> stock before the predicate', orig=b'LOW AMPL \x98')
check(f'v7 predicate not called when the stock text already rules it out: {m.rec_args}', m.rec_args == [])
print('--- v8 (real rate formatter, "*" above 2.5 GS/s)')


def rate_suite(et):
    m = M(adaptive=True, rec_check=True, et_mark=et)
    del m.stubs[m.L['rate']]
    m.tb = b'   5ns'
    res = {}
    def s_trace():
        m.u.mem_write(m.u.reg_read(UC_ARM_REG_R2), struct.pack('<I', 0x44123450)); m.ret(0)
    def s_xres6():
        p, (mant, e) = m.u.reg_read(UC_ARM_REG_R0), res['v']
        m.u.mem_write(p + 2, struct.pack('<hI', e, mant)); m.ret(0)
    m.stubs.update({sr.P_XY: lambda: m.ret(0), sr.P_A1: lambda: m.ret(0), sr.TRACE: s_trace,
                    sr.TTYPE: lambda: m.ret(6), sr.XRES6: s_xres6})
    tag = 'v8' if et else 'v7'
    # (mantissa, exponent) of s/point, text, '*' expected. 399 ps = 2.506 GS/s: shown as 2.5G, below the cut (q 250).
    for mant, e, want, star in [(2, -10, b' 5G', 1), (1, -10, b' 10G', 1), (25, -11, b' 4G', 1), (4, -10, b' 2.5G', 0),
                                (399, -12, b' 2.5G', 0), (8, -10, b' 1.25G', 0), (4, -4, b' 2.5k', 0),
                                (40, -9, b' 25M', 0), (2, -3, b' 500', 0)]:
        want += b'*' if et and star else b''
        res['v'] = (mant, e)
        r, s, ok = m.call('rate')
        check(f'{tag} {mant}e{e} s/pt -> {s!r}', s == want and r == 0xc000 and ok)


rate_suite(False)
rate_suite(True)
print(f'\n{fails} failures')
sys.exit(1 if fails else 0)
