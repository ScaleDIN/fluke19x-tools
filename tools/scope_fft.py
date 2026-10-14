#!/usr/bin/python
# Grab a trace from a Fluke 19x ScopeMeter over the optical port and plot it with its FFT.
#
# usage: python tools/scope_fft.py COM13 [--avg 8] [--window hann] [--csv out.csv] [--png out.png] [--no-show]
#
# Uses "QW <trace>" (10 = input A). Reply: ACK, then an admin block (#0 0x80 ...) and a
# samples block (,#0 0x90 ...), each ending with an 8-bit sum checksum of its payload.

import argparse, sys, time
import numpy as np
import serial

UNITS = {0: '', 1: 'V', 2: 'A', 3: 'Ohm', 4: 'W', 5: 'F', 6: 'K', 7: 's', 8: 'h', 9: 'days', 10: 'Hz'}


def f3(b, i):
    m = int.from_bytes(b[i:i + 2], 'big', signed=True)
    e = int.from_bytes(b[i + 2:i + 3], 'big', signed=True)
    return m * 10.0 ** e


def read_reply(s, timeout=3.0):
    buf, t = b'', time.time()
    while time.time() - t < timeout:
        c = s.read(8192)
        if c:
            buf += c
            t = time.time() - timeout + 0.3
        elif buf:
            break
    return buf


def parse_qw(r):
    if not r.startswith(b'0\r'):
        raise ValueError(f'meter replied {r[:2]!r} (1 = syntax error, 2 = trace not available)')
    a = r[2:]
    if a[:3] != b'#0\x80':
        raise ValueError('no admin block')
    n = int.from_bytes(a[3:5], 'big')
    adm = a[5:5 + n]
    if sum(adm) & 0xff != a[5 + n]:
        raise ValueError('admin checksum mismatch')
    p = 5 + n + 1
    if a[p:p + 4] != b',#0\x90':
        raise ValueError('no samples block')
    m = int.from_bytes(a[p + 4:p + 8], 'big')
    blk = a[p + 8:p + 8 + m]
    if len(blk) != m or sum(blk) & 0xff != a[p + 8 + m]:
        raise ValueError('samples checksum mismatch (short or corrupted read)')

    info = dict(
        y_unit=UNITS.get(adm[1], '?'), x_unit=UNITS.get(adm[2], '?'),
        y_div=int.from_bytes(adm[3:5], 'big'), x_div=int.from_bytes(adm[5:7], 'big'),
        y_scale=f3(adm, 7), x_scale=f3(adm, 10),
        y_zero=f3(adm, 15), x_zero=f3(adm, 18), y_res=f3(adm, 21), x_res=f3(adm, 24),
        stamp=adm[33:47].decode('ascii', 'replace'),
    )

    fmt, over, under, inval = blk[0], blk[1], blk[2], blk[3]
    count = int.from_bytes(blk[4:6], 'big')
    width = fmt & 0x0f
    per_point = 2 if fmt & 0x40 else 1
    raw = blk[6:6 + count * width * per_point]
    dt = {1: 'u1', 2: '>u2'}[width] if not fmt & 0x80 else {1: 'i1', 2: '>i2'}[width]
    vals = np.frombuffer(raw, dtype=dt).astype(float).reshape(count, per_point)
    clipped = np.isin(vals, [over, under, inval]).any(axis=1) if width == 1 else np.zeros(count, bool)
    volts = vals.mean(axis=1) * info['y_res'] + info['y_zero']
    spread = (vals.max(axis=1) - vals.min(axis=1)) * info['y_res']
    t = info['x_zero'] + np.arange(count) * info['x_res']
    info.update(fmt=fmt, count=count, minmax=bool(fmt & 0x40), clipped=int(clipped.sum()),
                spread=float(spread.max()))
    return t, volts, info


def spectrum(v, dt, window):
    n = len(v)
    w = {'hann': np.hanning, 'hamming': np.hamming, 'rect': np.ones,
         'blackman': np.blackman}[window](n)
    x = (v - v.mean()) * w
    amp = np.abs(np.fft.rfft(x)) * 2 / w.sum()
    amp[0] /= 2
    return np.fft.rfftfreq(n, dt), amp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('port')
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--trace', type=int, default=10, help='QW trace number (10 = input A)')
    ap.add_argument('--avg', type=int, default=1, help='number of captures to power-average')
    ap.add_argument('--window', default='hann', choices=['hann', 'hamming', 'blackman', 'rect'])
    ap.add_argument('--csv', help='save the last waveform and averaged spectrum')
    ap.add_argument('--png', help='save the plot')
    ap.add_argument('--no-show', action='store_true')
    args = ap.parse_args()

    power, last = None, None
    with serial.Serial(args.port, args.baud, timeout=0.5) as s:
        for k in range(args.avg):
            for attempt in range(3):
                s.reset_input_buffer()
                s.write(b'QW %d\r' % args.trace)
                try:
                    t, v, info = parse_qw(read_reply(s))
                    break
                except ValueError as e:
                    if str(e).startswith('meter replied'):
                        sys.exit(str(e))
                    print(f'capture {k+1}: {e}, retrying', flush=True)
            else:
                sys.exit('giving up after 3 bad reads')
            f, a = spectrum(v, info['x_res'], args.window)
            power = a ** 2 if power is None else power + a ** 2
            last = (t, v, info)

    t, v, info = last
    amp = np.sqrt(power / args.avg)
    fs = 1 / info['x_res']
    yu = info['y_unit']
    print(f"{info['count']} points, {info['y_scale']:g} {yu}/div, {info['x_scale']:g} {info['x_unit']}/div, "
          f"fs = {fs:g} Hz, bin = {fs / info['count']:g} Hz, meter time {info['stamp']}")
    print(f"mean {v.mean():.4g} {yu}, rms (ac) {v.std():.4g} {yu}, p-p {np.ptp(v):.4g} {yu}")
    if info['clipped']:
        print(f"warning: {info['clipped']} samples overloaded/underloaded (off screen)")
    if info['minmax'] and info['spread'] > 4 * info['y_res']:
        print('warning: min/max envelope data; signal has content faster than the sample rate, '
              'so the spectrum is aliased. Use a faster timebase.')
    top = np.argsort(amp[1:])[::-1][:5] + 1
    print('largest peaks:')
    for i in sorted(top, key=lambda i: f[i]):
        print(f'  {f[i]:>12.6g} Hz  {amp[i]:.4g} {yu}  ({20*np.log10(max(amp[i],1e-12)):.1f} dB{yu})')

    if args.csv:
        with open(args.csv, 'w') as fh:
            fh.write(f'time_s,{yu}\n')
            fh.writelines(f'{a:.9g},{b:.6g}\n' for a, b in zip(t, v))
            fh.write(f'\nfreq_Hz,amplitude_{yu}\n')
            fh.writelines(f'{a:.9g},{b:.6g}\n' for a, b in zip(f, amp))
        print('wrote', args.csv)

    if args.png or not args.no_show:
        import matplotlib
        if args.no_show:
            matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7))
        ax1.plot(t * 1e3, v, lw=1)
        ax1.set_xlabel('time (ms)')
        ax1.set_ylabel(yu)
        ax1.grid(alpha=0.3)
        ax1.set_title(f"Fluke trace {args.trace}: {info['y_scale']:g} {yu}/div, {info['x_scale']:g} s/div")
        ax2.plot(f, 20 * np.log10(np.maximum(amp, 1e-9)), lw=1)
        ax2.set_xlabel('frequency (Hz)')
        ax2.set_ylabel(f'dB{yu}')
        ax2.grid(alpha=0.3)
        ax2.set_title(f'spectrum ({args.window} window, {args.avg} average{"s" if args.avg > 1 else ""})')
        fig.tight_layout()
        if args.png:
            fig.savefig(args.png, dpi=120)
            print('wrote', args.png)
        if not args.no_show:
            plt.show()


if __name__ == '__main__':
    main()
