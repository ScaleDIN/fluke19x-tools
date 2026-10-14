#!/usr/bin/python
# Dump the 2 MB flash of a Fluke 19x over its optical serial port.
#
# usage: python tools/serial_dump.py <port> <output.bin> [baud]
#
# The meter must already be at <baud> (default 115200); set it with "PC 115200" at 1200 baud.
# Enters maintenance mode (EM + "maintenance"), reads with RB in 16 KB chunks, exits with EO.
# EO without RC saves nothing, so the meter's cal is not touched.

import serial, sys, time

port, out = sys.argv[1], sys.argv[2]
baud = int(sys.argv[3]) if len(sys.argv) > 3 else 115200
start, size, chunk = 0x40000000, 0x200000, 0x4000

s = serial.Serial(port, baud, timeout=5)

def tx(c):
    s.reset_input_buffer()
    s.write(c + b'\r')
    r = s.read_until(b'\r')
    time.sleep(0.1)
    return r

def read_block(addr, n):
    for attempt in range(5):
        s.reset_input_buffer()
        s.write(b'RB %d,%d\r' % (addr, n))
        ack = s.read_until(b'\r')
        data = s.read_until(b'\r')
        if ack == b'0\r' and data.startswith(b'#H') and len(data) == 2 * n + 3:
            try:
                return bytes.fromhex(data[2:-1].decode())
            except ValueError:
                pass
        print(f'  retry {addr:#x} attempt {attempt+1}: ack={ack!r} len={len(data)}', flush=True)
        time.sleep(1)
    raise RuntimeError(f'failed at {addr:#x}')

assert tx(b'EM') == b'0\r', 'EM rejected (meter off, wrong baud, or not allowed in this state)'
assert tx(b'maintenance') == b'0\r', 'maintenance password rejected'
t0 = time.time()
buf = bytearray()
try:
    for off in range(0, size, chunk):
        buf += read_block(start + off, chunk)
        if (off // chunk) % 16 == 15:
            print(f'{len(buf)//1024} KB  {time.time()-t0:.0f}s', flush=True)
finally:
    print('EO', tx(b'EO'))
    s.close()
    open(out, 'wb').write(buf)
    print('wrote', len(buf), 'bytes to', out)
