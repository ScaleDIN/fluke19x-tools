#!/usr/bin/python
# One-click: convert a Fluke 196B/196C memory module into a 199C V8.04 that keeps its own
# calibration, using the fluke19x programmer.
#
# usage: python tools/convert_to_199c.py [--port COM11] [--color-lcd] [--options keep|all]
#                                        [--donor donor_199c_v0804.bin] [--yes]
#        python tools/convert_to_199c.py --source readback.bin --out image.bin   (offline build only)
#        python tools/convert_to_199c.py --restore backup.bin                    (write a backup back)
#
# The donor is a full 2 MB dump of any 199C running V08.04; only its bootloader and code are used.
#
# Steps: find programmer, check chip IDs, read module twice (must match, saved as backup),
# build image, confirm, erase + write, read back and compare byte for byte.
# A mono 196B also needs a color LCD fitted (--color-lcd); without it the conversion is refused.

import argparse, os, struct, sys, time
import serial
from serial.tools import list_ports

SIZE = 0x200000
WORDS = SIZE // 4
BANKS = (0x8000, 0xC000)
BOOT_199C = bytes.fromhex('010700000020000002000000')   # bootloader bytes 0x74..0x7F
CHIPS = {(0x00BF, 0x2781): 'SST', (0x0020, 0x225B): 'ST M29W800'}
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DONOR = os.path.join(HERE, '..', 'donor_199c_v0804.bin')


def u16(b, o): return struct.unpack_from('<H', b, o)[0]


def live_bank(img):
    return min(BANKS, key=lambda b: u16(img, b + 0x3FFC))


def describe(img):
    b = live_bank(img)
    model = img[b + 0x68:b + 0x6C].decode('ascii', 'replace')
    sn = img[b + 0x88:b + 0x98].split(b'\0')[0].decode('ascii', 'replace')
    return model, sn, img[b + 0xDC]


def build(src, donor, color_lcd, options):
    assert len(src) == len(donor) == SIZE
    if donor[0x74:0x80] != BOOT_199C:
        raise SystemExit('donor bootloader is not the 199C one')
    img = bytearray(donor)
    img[0x8000:0x10000] = src[0x8000:0x10000]
    changes = []
    for base in BANKS:
        if u16(img, base + 0x20) != 0x0204 or u16(img, base + 0x24) != 0x1AB8:
            raise SystemExit(f'cal bank at {base:#x} has an unknown format; not converting')
        if sum(img[base + 0x44:base + 0x1AB8]) & 0xffff != u16(img, base + 0x3E):
            raise SystemExit(f'cal bank at {base:#x} has a bad checksum; not converting')
        model = img[base + 0x68:base + 0x6C]
        if model not in (b'196B', b'196C', b'199C'):
            raise SystemExit(f'source model {model!r} is not a 196B/196C; values for it are unknown')

        mono = u16(img, base + 0x1AA) == 0x001E
        if mono and not color_lcd:
            raise SystemExit('source is a mono unit; fit a color LCD and pass --color-lcd')
        patches = {0xDA: ((0x0100, 0x0200), 0x0200)}
        if mono:
            patches.update({0x1AA: ((0x001E,), 0x011E), 0x124: ((0x0028,), 0x0010),
                            0x154: ((0x0028,), 0x0010)})
        for off, (allowed, new) in patches.items():
            cur = u16(img, base + off)
            if cur not in allowed + (new,):
                raise SystemExit(f'unexpected value {cur:#06x} at cal {off:#x}; not converting')
            struct.pack_into('<H', img, base + off, new)
        img[base + 0x68:base + 0x6C] = b'199C'
        if options == 'all':
            img[base + 0xDC] |= 3
        struct.pack_into('<H', img, base + 0x3E, sum(img[base + 0x44:base + 0x1AB8]) & 0xffff)
    for base in BANKS:
        diff = [o for o in range(0x40, 0x1AB8) if src[base + o] != img[base + o] and o not in (0x3E, 0x3F)]
        changes.append((base, diff))
    return bytes(img), changes


class Programmer:
    def __init__(self, port):
        self.s = serial.Serial(port, 115200, timeout=20)

    def chip_ids(self):
        self.s.reset_input_buffer()
        self.s.write(b'i')
        v = self.s.read(8)
        if len(v) != 8:
            return None
        m1, m2, d1, d2 = struct.unpack('<4H', v)
        return (m1, d1), (m2, d2)

    def read(self, label):
        self.s.reset_input_buffer()
        self.s.write(b'r' + (0).to_bytes(4, 'little') + WORDS.to_bytes(4, 'little'))
        buf = bytearray()
        while len(buf) < SIZE:
            chunk = self.s.read(min(0x4000, SIZE - len(buf)))
            if not chunk:
                raise SystemExit(f'{label}: programmer stopped sending at {len(buf):#x}')
            buf += chunk
            if len(buf) % 0x40000 == 0:
                print(f'  {label} {len(buf) * 100 // SIZE}%', flush=True)
        return bytes(buf)

    def erase(self):
        self.s.write(b'e')
        self.s.timeout = 120
        if self.s.read(1) != b'\0':
            raise SystemExit('erase did not complete')
        self.s.timeout = 20

    def write(self, img):
        # The AVR has no flow control: send one byte per call, as the programmer firmware expects.
        self.s.write(b'w' + (0).to_bytes(4, 'little') + WORDS.to_bytes(4, 'little'))
        for i in range(SIZE):
            self.s.write(img[i:i + 1])
            if i % 0x40000 == 0x3FFFF:
                print(f'  write {(i + 1) * 100 // SIZE}%', flush=True)
        self.s.flush()
        time.sleep(1)


def find_programmer(ports):
    for p in ports:
        try:
            prog = Programmer(p)
        except serial.SerialException:
            continue
        prog.s.timeout = 2
        ids = prog.chip_ids()
        prog.s.timeout = 20
        if ids and all(i in CHIPS for i in ids):
            return prog, p, ids
        prog.s.close()
    return None


def ask(msg, auto):
    if auto:
        return True
    return input(msg + ' [y/N] ').strip().lower() == 'y'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port')
    ap.add_argument('--donor', default=DEFAULT_DONOR)
    ap.add_argument('--color-lcd', action='store_true', help='mono unit has had a color LCD fitted')
    ap.add_argument('--options', choices=['all', 'keep'], default='all')
    ap.add_argument('--force-st', action='store_true', help='allow writing ST chips (untested)')
    ap.add_argument('--source', help='offline: build from this 2 MB readback instead of the programmer')
    ap.add_argument('--out', help='offline: where to write the built image')
    ap.add_argument('--restore', help='write this 2 MB backup to the module as-is, then verify')
    ap.add_argument('--yes', action='store_true')
    args = ap.parse_args()

    if args.restore:
        img = open(args.restore, 'rb').read()
        if len(img) != SIZE:
            raise SystemExit(f'{args.restore} is {len(img)} bytes, expected {SIZE}')
        donor = None
    elif not os.path.exists(args.donor):
        raise SystemExit(f'Donor image not found: {args.donor}\n'
                         'Pass --donor <2 MB dump of a 199C running V08.04>.')
    else:
        donor = open(args.donor, 'rb').read()

    if args.source:
        src = open(args.source, 'rb').read()
        img, changes = build(src, donor, args.color_lcd, args.options)
        report(src, img, changes)
        if args.out:
            open(args.out, 'wb').write(img)
            print('wrote', args.out)
        return

    ports = [args.port] if args.port else [p.device for p in list_ports.comports()]
    found = find_programmer(ports)
    if not found:
        raise SystemExit('No programmer with a known module answered. Check the module is seated.')
    prog, port, ids = found
    kinds = {CHIPS[i] for i in ids}
    print(f'Programmer on {port}, chips: {", ".join(sorted(kinds))}')
    if kinds != {'SST'} and not args.force_st:
        raise SystemExit('Writing ST chips has never been tested with this programmer; '
                         'pass --force-st to try anyway (keep the backup!).')

    if args.restore:
        print(f'Module: {"%s, serial %s, options %s" % describe(prog.read("read"))}')
        if not ask(f'Erase the module and write {args.restore}?', args.yes):
            print('Cancelled; module not changed.')
            return
        write_verify(prog, img, port, None)
        return

    print('Reading module (1/2) ...')
    a = prog.read('read 1')
    print('Reading module (2/2) ...')
    b = prog.read('read 2')
    if a != b:
        raise SystemExit('The two reads differ: bad contact or a failing chip. Reseat the module and retry.')
    model, sn, opts = describe(a)
    backup = f'module_backup_{sn or "unknown"}_{model}_{time.strftime("%Y%m%d_%H%M%S")}.bin'
    open(backup, 'wb').write(a)
    print(f'Module: {model}, serial {sn}, options {opts}. Backup: {os.path.abspath(backup)}')

    if model == '199C' and a[0x74:0x80] == BOOT_199C and (args.options == 'keep' or opts & 3 == 3):
        print('Module is already a 199C with the requested options. Nothing to do.')
        return
    img, changes = build(a, donor, args.color_lcd, args.options)
    report(a, img, changes)
    if not ask('Erase the module and write this 199C image?', args.yes):
        print('Cancelled; module not changed.')
        return

    write_verify(prog, img, port, backup)
    print('Fit the module and check Version & Calibration shows 199C.')


def write_verify(prog, img, port, backup):
    print('Erasing ...')
    prog.erase()
    print('Writing (about 3-4 minutes) ...')
    prog.write(img)
    print('Verifying ...')
    v = prog.read('verify')
    if v != img:
        bad = next(i for i in range(SIZE) if v[i] != img[i])
        hint = f' Restore with: python tools/convert_to_199c.py --port {port} --restore {backup}' if backup else ''
        raise SystemExit(f'VERIFY FAILED at {bad:#x}. Do not fit this module.' + hint)
    print('Verified OK.')


def report(src, img, changes):
    m0, sn, o0 = describe(src)
    m1, _, o1 = describe(img)
    print(f'Conversion: {m0} -> {m1}, serial {sn}, options {o0} -> {o1}')
    print(f'  bootloader + code replaced: {src[:0x8000] != img[:0x8000] or src[0x10000:] != img[0x10000:]}')
    for base, diff in changes:
        print(f'  cal bank {base:#x}: changed offsets {[hex(o) for o in diff]}')


if __name__ == '__main__':
    try:
        main()
    finally:
        if sys.stdin.isatty() and os.environ.get('FLUKE_PAUSE'):
            input('Press Enter to close.')
