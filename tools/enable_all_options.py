#!/usr/bin/python
# One-click: enable Medical + BusHealth on a Fluke 19xC (V08.04) over the optical serial port.
#
# usage: python tools/enable_all_options.py [port] [--yes]
#
# Finds the meter (any serial port, 115200 or 1200 baud), saves a backup of both cal banks,
# then runs: EM / maintenance / CI 17,161027 / RC / EO, and reads the cal back to verify.
# RC also sets the meter's calibration date to today and adds 1 to its calibration counter.

import os, struct, sys, time
import serial
from serial.tools import list_ports

OPT_CODE = {1: 271267, 2: 110195, 3: 161027}
OPT_NAMES = {0: 'None', 1: 'Medical', 2: 'BusHealth', 3: 'Medical + BusHealth'}
CAL_ADDR, CAL_LEN = 0x40008000, 0x8000
BAUDS = (115200, 1200, 9600, 19200, 38400, 57600, 2400, 4800)


def tx(s, cmd, timeout=3):
    s.reset_input_buffer()
    s.write(cmd + b'\r')
    s.timeout = timeout
    ack = s.read_until(b'\r')
    time.sleep(0.1)
    return ack


def find_meter(ports):
    for p in ports:
        for b in BAUDS:
            try:
                s = serial.Serial(p, b, timeout=1.5)
            except serial.SerialException:
                break
            s.write(b'\r')
            time.sleep(0.2)
            if tx(s, b'ID', 2) == b'0\r':
                ident = s.read_until(b'\r').decode('ascii', 'replace').strip()
                return s, p, b, ident
            s.close()
    return None


def read_mem(s, addr, n, chunk=0x4000):
    out = bytearray()
    for off in range(0, n, chunk):
        k = min(chunk, n - off)
        for _ in range(5):
            s.reset_input_buffer()
            s.write(b'RB %d,%d\r' % (addr + off, k))
            s.timeout = 5 + k * 20 / s.baudrate * 1.5
            ack, data = s.read_until(b'\r'), s.read_until(b'\r')
            if ack == b'0\r' and data.startswith(b'#H') and len(data) == 2 * k + 3:
                out += bytes.fromhex(data[2:-1].decode())
                break
            time.sleep(0.5)
        else:
            raise RuntimeError(f'read failed at {addr + off:#x}')
    return bytes(out)


def live_options(cal):
    banks = [(struct.unpack_from('<H', cal, b + 0x3FFC)[0], b) for b in (0, 0x4000)]
    base = min(banks)[1]
    body = cal[base + 0x44:base + 0x1AB8]
    ok = sum(body) & 0xffff == struct.unpack_from('<H', cal, base + 0x3E)[0]
    return cal[base + 0xDC], ok


def enter_maint(s):
    if tx(s, b'EM') != b'0\r' or tx(s, b'maintenance') != b'0\r':
        raise RuntimeError('meter refused maintenance mode (is it switched on and not in a menu?)')


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    auto_yes = '--yes' in sys.argv
    ports = args or [p.device for p in list_ports.comports()]
    print('Looking for the meter on', ', '.join(ports) or 'no serial ports', '...')
    found = find_meter(ports)
    if not found:
        sys.exit('No Fluke meter answered. Check the optical cable is seated and the meter is on.')
    s, port, baud, ident = found
    print(f'Found on {port} at {baud} baud: {ident}')

    model, ver = (ident.split(';') + ['', ''])[:2]
    if not model.startswith('FLUKE 19') or not model.endswith('C') or ver != 'V08.04':
        sys.exit(f'Unsupported meter/firmware ({model} {ver}). The option codes are only known for 19xC V08.04.')

    try:
        enter_maint(s)
        print('Reading calibration data (backup) ...')
        cal = read_mem(s, CAL_ADDR, CAL_LEN)
        tx(s, b'EO')
        opts, ok = live_options(cal)
        if not ok:
            sys.exit('Live cal bank checksum is bad; not touching this meter.')
        serial_no = cal[0x88:0x98].split(b'\0')[0].decode('ascii', 'replace') or 'unknown'
        backup = f'cal_backup_{serial_no}_{time.strftime("%Y%m%d_%H%M%S")}.bin'
        with open(backup, 'wb') as fh:
            fh.write(cal)
        print(f'Serial {serial_no}, options now: {OPT_NAMES.get(opts, opts)}. Backup saved to {os.path.abspath(backup)}')

        if opts == 3:
            print('All options are already enabled. Nothing to do.')
            return

        if not auto_yes:
            ans = input('Enable Medical + BusHealth? This also sets the cal date to today. [y/N] ')
            if ans.strip().lower() != 'y':
                print('Cancelled; meter not changed.')
                return

        enter_maint(s)
        for cmd in (b'CI 17,%d' % OPT_CODE[3], b'RC'):
            r = tx(s, cmd, 10)
            if r != b'0\r':
                tx(s, b'EO')
                sys.exit(f'{cmd.decode()} failed (reply {r!r}); meter left unchanged.')
        cal2 = read_mem(s, CAL_ADDR, CAL_LEN)
        tx(s, b'EO')
        opts2, ok2 = live_options(cal2)
        if opts2 == 3 and ok2:
            print('Done: Medical + BusHealth enabled and saved. Check Version & Calibration on the meter.')
        else:
            sys.exit(f'Verify failed: options read back as {opts2}, checksum ok={ok2}. Backup: {backup}')
    finally:
        s.close()


if __name__ == '__main__':
    try:
        main()
    finally:
        if sys.stdin.isatty() and os.environ.get('FLUKE_PAUSE'):
            input('Press Enter to close.')
