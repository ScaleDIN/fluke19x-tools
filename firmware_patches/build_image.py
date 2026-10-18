# Build the combined V8.04 patch image (sample rate + Li-ion battery) from a full 2 MB dump.
#   python build_image.py <input.bin> <output.bin> [mode]   (mode: v7 = v6 + stock text in Scope Record (default), v6 = v5 + gauge start from voltage, top = v5, top3 = v3,
#                                                            84 = bottom bar (v2), 279 = v1)
# Input is only read. Output must not exist. Writes <output>.txt with the listing and patch list.
# Every patch site is checked against its expected original word, and the code area must be erased.
import hashlib, os, struct, sys
from armasm import Asm, listing
import battpatch as bp
import samplerate as sr
import toppatch as tp

FLASH = 0x40000000
BATT_BASE = 0x401e9000
SR_BASE = 0x401e9800
FREE = (0x401e8668, 0x401f0000)
TABLE = 0x401af29c                       # UI callback table (360 entries)

EXPECT = {                               # address -> original word
    TABLE + 181 * 4: 0x4013f99c,
    0x40049130: 0xeb000aef,              # bl 0x4004bcf4
    0x4004b47c: 0xe5983014,              # ldr r3, [r8, #0x14]
    0x4004b480: 0xe59fc11c,              # ldr ip, [pc, #0x11c]
    0x4004bb4c: 0xe588c010,              # str ip, [r8, #0x10]
    0x4004bb8c: 0xe588c010,              # str ip, [r8, #0x10]
    0x4004b3c4: 0xe588c014,              # str ip, [r8, #0x14]
    0x4004befc: 0xe3a00d87,              # mov r0, #0x21c0
    0x4004b4cc: 0xe3a0cfbd,              # mov ip, #0x2f4
    0x401b0228: 12600000,                # init capacity 3500 mAh
}


SR_ORIG = {84: sr.CB84, 279: sr.ORIG_CB}


def main(src, dst, mode='v7'):
    rec = mode == 'v7'
    soc = mode in ('v6', 'v7')
    if soc:
        mode = 'top'
        EXPECT.update(bp.BINIT_CALLS)
    top = mode in ('top', 'top3')
    sr_cb = 84 if top else int(mode)
    EXPECT[TABLE + sr_cb * 4] = SR_ORIG[sr_cb]
    if os.path.exists(dst) or os.path.exists(dst + '.txt'):
        sys.exit(f'{dst} (or its .txt) already exists; refusing to overwrite')
    img = bytearray(open(src, 'rb').read())
    assert len(img) == 0x200000, 'expected a 2 MB image'
    r32 = lambda a: struct.unpack_from('<I', img, a - FLASH)[0]

    batt, batt_code, patches = bp.build(BATT_BASE, soc)
    if top:
        sra = tp.build(SR_BASE, batt.labels['cb181'], adaptive=mode == 'top', rec_check=rec)
        sr_code = sra.assemble()
        patches[TABLE + 181 * 4] = sra.labels['top181']
        patches[TABLE + 84 * 4] = sra.labels['cb84h']
    else:
        sra = sr.build_cb(SR_BASE, orig=SR_ORIG[sr_cb])
        sr_code = sra.assemble()
        patches[TABLE + sr_cb * 4] = SR_BASE
    assert BATT_BASE + len(batt_code) <= SR_BASE and SR_BASE + len(sr_code) <= FREE[1]
    assert set(patches) == set(EXPECT), set(patches) ^ set(EXPECT)

    for a, want in EXPECT.items():
        have = r32(a)
        if have != want:
            sys.exit(f'patch site {a:#x}: found {have:#010x}, expected {want:#010x}; wrong image?')
    for base, code in ((BATT_BASE, batt_code), (SR_BASE, sr_code)):
        o = base - FLASH
        if img[o:o + len(code)] != b'\xff' * len(code):
            sys.exit(f'code area {base:#x} is not erased')
        img[o:o + len(code)] = code
    for a, v in patches.items():
        struct.pack_into('<I', img, a - FLASH, v)

    open(dst, 'wb').write(img)
    src_md5 = hashlib.md5(open(src, 'rb').read()).hexdigest()
    dst_md5 = hashlib.md5(img).hexdigest()
    with open(dst + '.txt', 'w') as f:
        f.write(f'source {os.path.basename(src)} md5 {src_md5}\noutput {os.path.basename(dst)} md5 {dst_md5}\n\n')
        f.write('word patches (address: old -> new)\n')
        for a in sorted(patches):
            f.write(f'  {a:08x}: {EXPECT[a]:08x} -> {patches[a]:08x}\n')
        f.write(f'\nbattery code at {BATT_BASE:#x} ({len(batt_code)} bytes), labels '
                + ', '.join(f'{k}={v:#x}' for k, v in batt.labels.items() if not k.startswith(('_', 'c_', 'i_', 'l_'))) + '\n')
        f.write(listing(batt_code, BATT_BASE) + '\n')
        labs = ', '.join(f'{k}={v:#x}' for k, v in sra.labels.items() if k in ('top181', 'cb84h', 'rate'))
        f.write(f'\nsample-rate code, mode {mode} ({labs}) at {SR_BASE:#x} ({len(sr_code)} bytes)\n')
        f.write(listing(sr_code, SR_BASE) + '\n')
    print(f'wrote {dst} md5 {dst_md5}')


if __name__ == '__main__':
    main(*sys.argv[1:4])
