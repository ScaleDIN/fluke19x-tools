# Fluke 19x ScopeMeter tools

Python tools for the Fluke 196/199 (B/C) ScopeMeters running firmware V08.04:

- enable the Medical and BusHealth options over the optical serial port,
- convert a 196B/196C memory module into a 199C while keeping the meter's own calibration,
- dump the 2 MB flash over the optical port,
- grab a trace and plot it with its FFT.

Requires Python 3 with `pyserial`; `scope_fft.py` also needs `numpy` and `matplotlib`.

```bash
pip install pyserial numpy matplotlib
```

The memory-module conversion needs the fluke19x flash programmer (an ATmega3290 board with a card-edge socket for the meter's memory module). Its hardware and firmware come from the original fluke19x-programmer project: [video](https://www.youtube.com/watch?v=klQPO-uYMEA), [EEVblog thread](https://www.eevblog.com/forum/repair/fluke-199c-scopemeter-repair/). The serial-port tools need only the meter's optical cable.

> **Back up first.** Every tool that writes saves a backup before it changes anything. Keep those `.bin` files: each one holds a meter's calibration, which can't be recreated without a recalibration.

## Tools

| Tool | Needs | What it does |
|---|---|---|
| `enable_all_options.bat` → `tools/enable_all_options.py` | optical cable | Turns on Medical + BusHealth |
| `convert_to_199c.bat` → `tools/convert_to_199c.py` | programmer + module | Converts a 196B/196C module to 199C V08.04, keeping its cal |
| `tools/serial_dump.py` | optical cable | Full 2 MB flash dump |
| `tools/scope_fft.py` | optical cable | Captures input A and plots waveform + FFT |

The `.bat` files are double-click wrappers for Windows; they pass any arguments through and keep the window open at the end.

### enable_all_options

```bash
python tools/enable_all_options.py [port] [--yes]
```

1. Tries every serial port (or the one given) at 115200, 1200 and the other standard speeds, sending `ID` until a meter answers.
2. Refuses anything that isn't a 19xC on V08.04, since the option codes are only known for that version.
3. Enters maintenance mode, reads both cal banks with `RB` and saves them as `cal_backup_<serial>_<time>.bin`.
4. If both options are already on, stops. Otherwise asks, then sends `CI 17,161027` and `RC`.
5. Reads the cal back and checks that the options are set and the checksum is good.

`RC` saves the cal record, and it also sets the cal date to today and adds 1 to the cal counter. No calibration actually takes place.

### convert_to_199c

```bash
python tools/convert_to_199c.py [--port COMx] [--donor donor_199c_v0804.bin] [--color-lcd] [--options all|keep] [--yes]
python tools/convert_to_199c.py --source readback.bin --out image.bin [--donor ...]   # offline build only
python tools/convert_to_199c.py --restore backup.bin [--port COMx]                    # write a backup back
```

The **donor** is a full 2 MB dump of any 199C running V08.04. Its bootloader and code are used, never its calibration. By default the tool looks for `donor_199c_v0804.bin` in the repo root.

1. Finds the programmer on any port by asking for flash chip IDs. Only SST (0x00BF/0x2781) is accepted for writing. ST M29W800 (0x0020/0x225B) chips are refused unless you pass `--force-st`, because writing them has never been tested.
2. Reads the module twice. If the reads differ (bad contact), it stops. Otherwise it saves `module_backup_<serial>_<model>_<time>.bin`.
3. Builds the image from the donor's bootloader and code plus the module's own cal banks, then patches both banks: model string `199C`, the 196→199 flag at 0xDA, and (with `--options all`, the default) both options. A mono 196B also gets the color-display values, but only if you pass `--color-lcd` to confirm a color LCD has been fitted. If any value isn't one it expects, it refuses to convert.
4. Prints exactly which cal offsets changed and asks before going on.
5. Erases the module, writes it (3–4 minutes) and verifies with a full readback.

`--restore` writes any 2 MB file back unchanged, with the same erase/write/verify steps.

### serial_dump

```bash
python tools/serial_dump.py <port> <out.bin> [baud]
```

Enters maintenance mode, reads 0x40000000–0x401FFFFF with `RB` in 16 KB blocks (retrying bad blocks), then sends `EO`. Nothing is saved to the meter. At 115200 baud it takes about 7 minutes. The meter has to be at that speed already. Set it from 1200 baud with `PC 115200`; `PC 1200` sets it back.

### scope_fft

```bash
python tools/scope_fft.py <port> [--baud 115200] [--trace 10] [--avg 8] [--window hann] [--csv out.csv] [--png out.png] [--no-show]
```

Captures a trace with `QW` (10 = input A), converts the samples to volts using the scale data the meter sends, and plots the waveform and a power-averaged FFT.

## How it works

### Optical serial port

8N1, every command ends in CR. The default speed is 1200 baud. Each command gets a one-character reply: `0` OK, `1` syntax error or not allowed right now, `2` bad value.

| Command | Use |
|---|---|
| `ID` | Model; firmware version |
| `PC <baud>` | Change the port speed |
| `EM`, then `maintenance` | Enter maintenance (calibration) mode |
| `EO` | Leave maintenance mode. Nothing is saved without `RC` |
| `RB <addr>,<count>` | Read memory (decimal arguments; flash starts at 1073741824 = 0x40000000). Reply is `#H` followed by the hex |
| `CI 17,<code>` | Option code: 271267 Medical, 110195 BusHealth, 161027 both, 0 none |
| `RC` | Record calibration: writes the cal record, sets cal date to today, adds 1 to the cal counter |
| `QW <trace>` | Waveform data |

**Never send `EM` with `flukeuhm`.** In the firmware it appears to jump to the bootloader/update mode.

The other commands the firmware accepts: AS AT CL CM CV DS EX FM GD GL GR IS PS QC QI QM QP QS RD RI RS RT RW SO SS ST TA VE WB WC WD WT WW RN HO CD DT RP IM CN PW CC KY. Each one is only allowed in certain meter states; the per-command masks are at ROM 0x401A4794.

The front-panel BusHealth install code for V08.04 is `3 × serial + 161072`.

### `QW` reply

`0\r`, then `#0` `0x80` u16 length, the admin block and its checksum, then `,#0` `0x90` u32 length, the samples block and its checksum. Each checksum is the 8-bit sum of its payload. Admin values are 3-byte numbers (i16 mantissa, i8 power of ten): V/div, s/div, y zero, x zero, V per count, s per sample. The samples block holds a format byte (`0x40` = min/max pairs, low nibble = bytes per sample), overload/underload/invalid codes, a u16 count, then the samples. Volts = count × V-per-count + y zero.

### Flash layout

2 MB, as 32-bit words across two x16 chips, mapped at 0x40000000.

| Range | Contents |
|---|---|
| 0x000000–0x007FFF | Bootloader |
| 0x008000–0x00BFFF | Cal/config bank A |
| 0x00C000–0x00FFFF | Cal/config bank B |
| 0x010000– | Firmware code |

The V08.04 code is identical for the 196C and 199C. The model and its options come from the cal block, plus bootloader bytes 0x74–0x7F: 199C = `0107 / 2000 / 2`, 196B/196C = `0002 / 4000 / 1`. **A converted 196B only boots with the 199C values.**

### Cal bank (offsets within a bank)

| Offset | Meaning |
|---|---|
| 0x20 | Format header `0x0204` |
| 0x24 | Length `0x1AB8` |
| 0x3E | Checksum: u16 sum of bytes 0x44..0x1AB7 |
| 0x68 | Model string (`196B`, `199C`, …) |
| 0x88 | Serial number string |
| 0x98, 0xA8 | Free text (instrument ID, cal operator) |
| 0xDA | `0100` = 196, `0200` = 199 (probable) |
| 0xDC | Options: bit 0 Medical, bit 1 BusHealth |
| 0x124, 0x154 | `0028` on a 196B, `0010` on color units |
| 0x184 | Language enable mask (one bit per language; *not* an option flag) |
| 0x1AA | `001E` mono, `011E` color |
| 0x3FFC | Write generation, counting down: the bank with the lower value is the live one |

The meter rewrites the cal banks itself when it saves settings, and the padding between records picks up leftover RAM values. Two dumps of the same meter can therefore differ slightly in the cal area.

### Options in firmware

- **Medical** only changes the label on the version screen. With no option installed, one three-way setting can't take its third value; either option lifts that.
- **BusHealth** is checked in about 12 places and enables the bus-health features.
- **Bonus Feature** (5 extra screen memories) has no code check.

## Notes

- Above 100 MHz, a converted 196 keeps its original 100 MHz calibration. Get a 199C calibration if accurate high-frequency readings matter.
- The programmer firmware uses SST-style unlock addresses (0x5555/0x2AAA). These work for chip ID on ST chips, but erase and write were only tested on SST.
- The programmer has no flow control, so the tools write one byte per serial call.
