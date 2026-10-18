# Tiny ARM (ARMv4, A32) assembler for the handful of instructions the patches need.
# Every word is checked by disassembling it with capstone.
import struct, capstone

COND = dict(eq=0, ne=1, hs=2, lo=3, mi=4, pl=5, vs=6, vc=7, hi=8, ls=9, ge=10, lt=11, gt=12, le=13, al=14)
REG = {f'r{i}': i for i in range(16)}
REG.update(sb=9, sl=10, fp=11, ip=12, sp=13, lr=14, pc=15)


def rot_imm(v):
    v &= 0xffffffff
    for r in range(16):
        x = ((v << (2 * r)) | (v >> (32 - 2 * r))) & 0xffffffff if r else v
        if x < 256:
            return (r << 8) | x
    raise ValueError(f'immediate {v:#x} not encodable')


class Asm:
    def __init__(self, base):
        self.base = base
        self.items = []     # (kind, args)
        self.labels = {}
        self.lits = []      # (label, value)

    def label(self, name):
        self.items.append(('label', name))

    def __getattr__(self, name):
        def f(*a, cond='al'):
            self.items.append(('ins', (name, a, cond)))
        return f

    def call(self, target):
        # bl when in range, else ldr ip,=target; mov lr,pc; mov pc,ip
        if abs(target - self.base) < (1 << 25) - 0x10000:
            self.items.append(('ins', ('bl', (target,), 'al')))
        else:
            self.items.append(('ins', ('ldr', ('ip', self.lit(target)), 'al')))
            self.items.append(('ins', ('mov', ('lr', 'pc'), 'al')))
            self.items.append(('ins', ('mov', ('pc', 'ip'), 'al')))

    def word(self, v):
        self.items.append(('word', v))

    def bytes_(self, b):
        self.items.append(('bytes', b))

    def align(self):
        self.items.append(('align', None))

    def lit(self, value):
        name = f'_lit{len(self.lits)}'
        self.lits.append((name, value))
        return name

    def _layout(self):
        pc = self.base
        for k, a in self.items:
            if k == 'label':
                self.labels[a] = pc
            elif k in ('ins', 'word'):
                pc += 4
            elif k == 'bytes':
                pc += len(a)
            elif k == 'align':
                pc = (pc + 3) & ~3
        for name, _ in self.lits:
            self.labels[name] = pc
            pc += 4
        return pc

    def assemble(self):
        end = self._layout()
        out = bytearray()
        pc = self.base
        for k, a in self.items:
            if k == 'ins':
                out += struct.pack('<I', self.enc(pc, *a))
                pc += 4
            elif k == 'word':
                v = self.labels[a] if isinstance(a, str) else a
                out += struct.pack('<I', v)
                pc += 4
            elif k == 'bytes':
                out += a
                pc += len(a)
            elif k == 'align':
                while pc & 3:
                    out.append(0)
                    pc += 1
        for name, v in self.lits:
            v = self.labels[v] if isinstance(v, str) else v
            out += struct.pack('<I', v)
        assert self.base + len(out) == end
        return bytes(out)

    def tgt(self, x):
        return self.labels[x] if isinstance(x, str) else x

    def enc(self, pc, op, a, cond):
        c = COND[cond] << 28
        R = lambda r: REG[r]
        if op in ('b', 'bl'):
            off = (self.tgt(a[0]) - pc - 8) >> 2
            assert -(1 << 23) <= off < (1 << 23)
            return c | (0xa if op == 'b' else 0xb) << 24 | (off & 0xffffff)
        if op == 'push':
            return c | 0x092d0000 | sum(1 << R(r) for r in a[0])
        if op == 'pop':
            return c | 0x08bd0000 | sum(1 << R(r) for r in a[0])
        dp = dict(and_=0, eor=1, sub=2, rsb=3, add=4, adc=5, sbc=6, rsc=7, tst=8, teq=9, cmp=10, cmn=11, orr=12, mov=13, bic=14, mvn=15)
        if op in dp:
            opc = dp[op]
            s = 1 if op in ('tst', 'teq', 'cmp', 'cmn') else 0
            if op in ('mov', 'mvn'):
                rd, rn, src = R(a[0]), 0, a[1]
            elif s:
                rd, rn, src = 0, R(a[0]), a[1]
            else:
                rd, rn, src = R(a[0]), R(a[1]), a[2]
            if isinstance(src, int):
                op2 = (1 << 25) | rot_imm(src)
            else:
                op2 = R(src)
                if len(a) == (3 if op in ('mov', 'mvn') or s else 4):  # shift: ('lsl', n)
                    sh, n = a[-1]
                    op2 |= (n << 7) | {'lsl': 0, 'lsr': 1, 'asr': 2, 'ror': 3}[sh] << 5
            return c | op2 | opc << 21 | s << 20 | rn << 16 | rd << 12
        if op in ('ldr', 'str', 'ldrb', 'strb'):
            rd = R(a[0])
            if isinstance(a[1], str) and a[1] not in REG:     # literal / label
                off = self.tgt(a[1]) - pc - 8
                rn = 15
            else:
                rn, off = R(a[1]), (a[2] if len(a) > 2 else 0)
            u = 1 if off >= 0 else 0
            assert abs(off) < 4096
            L = 1 if op.startswith('ldr') else 0
            B = 1 if op.endswith('b') else 0
            return c | 0x05000000 | u << 23 | B << 22 | L << 20 | rn << 16 | rd << 12 | abs(off)
        if op in ('ldrh', 'strh', 'ldrsh', 'ldrsb'):
            rd, rn, off = R(a[0]), R(a[1]), (a[2] if len(a) > 2 else 0)
            u = 1 if off >= 0 else 0
            off = abs(off)
            assert off < 256
            L = 0 if op == 'strh' else 1
            sh = {'ldrh': 0xb, 'strh': 0xb, 'ldrsh': 0xf, 'ldrsb': 0xd}[op]
            return c | 0x01400000 | u << 23 | L << 20 | rn << 16 | rd << 12 | (off >> 4) << 8 | sh << 4 | (off & 0xf)
        if op == 'mul':
            rd, rm, rs = R(a[0]), R(a[1]), R(a[2])
            return c | rd << 16 | rs << 8 | 0x90 | rm
        raise ValueError(op)


def listing(code, base):
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
    lines = []
    for i in range(0, len(code) - len(code) % 4, 4):
        w = code[i:i + 4]
        ins = list(md.disasm(w, base + i))
        s = f'{ins[0].mnemonic} {ins[0].op_str}' if ins else '.word'
        lines.append(f'{base + i:08x}: {int.from_bytes(w, "little"):08x}  {s}')
    return '\n'.join(lines)
