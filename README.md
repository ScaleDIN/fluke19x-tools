# Fluke 19x ScopeMeter tools

> [!CAUTION]
> **Use at your own risk.** These tools write to the meter's flash memory and calibration data. A failed or interrupted write, a bad contact, a wrong option or a bug in these tools can **brick the meter**, **destroy its factory calibration**, or leave it reading incorrectly.
>
> **Make a full backup before you change anything**, check that it reads back correctly, and keep it somewhere safe. A lost calibration can't be recreated without a professional recalibration.
>
> This software is provided **"as is", without warranty of any kind**, express or implied. The author accepts **no responsibility or liability** for bricked or damaged meters, lost calibration, inaccurate measurements, voided warranties, damage to other equipment, or any other direct or indirect loss resulting from the use or misuse of these tools or the information in this README. By using them you accept full responsibility for the outcome.
>
> This project is not affiliated with, endorsed by or supported by Fluke Corporation. Fluke and ScopeMeter are trademarks of Fluke Corporation. Modifying a meter may void its warranty and calibration certification; a converted meter's calibration is not traceable to any 199C calibration.

Python tools for the Fluke 196/199 (B/C) ScopeMeters running firmware V08.04:

- enable the Medical and BusHealth options over the optical serial port,
- convert a 196B/196C memory module into a 199C while keeping the meter's own calibration,
- dump the 2 MB flash over the optical port,
- grab a trace and plot it with its FFT.

Requires Python 3 with `pyserial`; `scope_fft.py` also needs `numpy` and `matplotlib`.

```bash
pip install pyserial numpy matplotlib
```

The serial-port tools need only the meter's optical cable. The memory-module conversion also needs the **fluke19x flash programmer** from [feedbackloopchannel/fluke19x-programmer](https://github.com/feedbackloopchannel/fluke19x-programmer): an ATmega3290 board with a card-edge socket for the meter's memory module. Build it and load its firmware as described there; these tools talk to it at 115200 baud.

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
python tools/convert_to_199c.py [--port COMx] [--donor donor_199c_v0804.bin] [--color-lcd] [--options all|keep] [--force-st] [--yes]
python tools/convert_to_199c.py --source readback.bin --out image.bin [--donor ...]   # offline build only
python tools/convert_to_199c.py --restore backup.bin [--port COMx] [--force-st]       # write a backup back
```

The **donor** is a full 2 MB dump of any 199C running V08.04. Its bootloader and code are used, never its calibration. By default the tool looks for `donor_199c_v0804.bin` in the repo root.

1. Finds the programmer on any port by asking for flash chip IDs. SST (0x00BF/0x2781) chips are written directly; ST M29W800 (0x0020/0x225B) chips need `--force-st` (see [Flash chips](#flash-chips)).
2. Reads the module twice. If the reads differ (bad contact), it stops. Otherwise it saves `module_backup_<serial>_<model>_<time>.bin`.
3. Builds the image from the donor's bootloader and code plus the module's own cal banks, then patches both banks: model string `199C`, the 196→199 flag at 0xDA, and (with `--options all`, the default) both options. A mono 196B also gets the color-display values, but only if you pass `--color-lcd` to confirm a color LCD has been fitted. If any value isn't one it expects, it refuses to convert.
4. Prints exactly which cal offsets changed and asks before going on.
5. Erases the module, writes it (3–4 minutes) and verifies with a full readback.

`--restore` writes any 2 MB file back unchanged, with the same erase/write/verify steps. Run long writes with `python -u` so the progress output isn't buffered.

### serial_dump

```bash
python tools/serial_dump.py <port> <out.bin> [baud]
```

Enters maintenance mode, reads 0x40000000–0x401FFFFF with `RB` in 16 KB blocks (retrying bad blocks), then sends `EO`. Nothing is saved to the meter. At 115200 baud it takes about 7 minutes. The meter has to be at that speed already (see [Optical serial port](#optical-serial-port)).

### scope_fft

```bash
python tools/scope_fft.py <port> [--baud 115200] [--trace 10] [--avg 8] [--window hann] [--csv out.csv] [--png out.png] [--no-show]
```

Captures a trace with `QW` (10 = input A), converts the samples to volts using the scale data the meter sends, and plots the waveform and a power-averaged FFT.

## After a 196B → 199C conversion

- **Boot values.** A converted 196B only boots with the 199C bootloader values at 0x74–0x7F (see [Flash layout](#flash-layout)). `convert_to_199c.py` takes them from the donor.
- **Don't update it with Fluke's FlashTool.** The official serial update rewrites 0x74–0x7C with 196-type values, so even a successful update leaves a converted 196B unbootable. See [Firmware updates over the optical port](#firmware-updates-over-the-optical-port).
- **Display contrast.** A color LCD fitted to a 196B starts with the mono LCD's contrast settings. Set them in the meter's cal mode, steps CL 0100 (max dark), CL 0110 (default) and CL 0120 (max bright), as in the service manual, section 5.4. To get there: hold USER, press and release CLEAR MENU, release USER. F1 steps to CL 0100; F3 starts and ends each step, the arrows adjust, F2 moves to the next step; F4 EXIT, YES saves. Per section 5.7, changing only the contrast doesn't change the cal date or counter. Do it on the charger, and don't press F3 at the WarmingUp step (CL 0200).
- **Bandwidth.** Above 100 MHz, a converted 196 keeps its original 100 MHz calibration. Get a 199C calibration if accurate high-frequency readings matter.

## How it works

### Optical serial port

8N1, every command ends in CR. Each command gets a one-character reply: `0` OK, `1` syntax error or not allowed right now, `2` bad value.

The meter starts at **1200 baud after every off/on**. Send `PC 115200` at 1200 and work at 115200: at 1200 the replies to back-to-back commands overlap and maintenance mode can be refused. `PC 1200` sets it back. If a script dies mid-transfer, wait a few seconds and send a bare CR before retrying.

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
| `QP` | Screen print: Epson ESC * mode-4 stripes, 352×240, 1 bit per pixel (colors are thresholded) |

**Never send `EM` with `flukeuhm`** unless you mean to flash the meter: it switches to the bootloader's update mode (see [below](#firmware-updates-over-the-optical-port)).

The other commands the firmware accepts: AS AT CL CM CV DS EX FM GD GL GR IS PS QC QI QM QP QS RD RI RS RT RW SO SS ST TA VE WB WC WD WT WW RN HO CD DT RP IM CN PW CC KY. Each one is only allowed in certain meter states; the per-command masks are at ROM 0x401A4794.

The front-panel BusHealth install code for V08.04 is `3 × serial + 161072`.

**If the port hangs,** reseat the memory module first: a slightly loose module caused repeated hangs on plain `RB` reads. Long bulk reads of RAM (around 0x4402D000) also hung the meter, which recovered only after both the charger and the battery were removed. Keep RAM reads small.

### `QW` reply

`0\r`, then `#0` `0x80` u16 length, the admin block and its checksum, then `,#0` `0x90` u32 length, the samples block and its checksum. Each checksum is the 8-bit sum of its payload. Admin values are 3-byte numbers (i16 mantissa, i8 power of ten): V/div, s/div, y zero, x zero, V per count, s per sample. The samples block holds a format byte (`0x40` = min/max pairs, low nibble = bytes per sample), overload/underload/invalid codes, a u16 count, then the samples. Volts = count × V-per-count + y zero.

### Flash layout

2 MB, as 32-bit words across two x16 chips, mapped at 0x40000000.

| Range | Contents |
|---|---|
| 0x000000–0x007FFF | Bootloader (boot header, magic 'TEIN', at 0x00–0x27) |
| 0x008000–0x00BFFF | Cal/config bank A |
| 0x00C000–0x00FFFF | Cal/config bank B |
| 0x010000–0x1E8667 | Firmware code (V08.04) |
| 0x1E8668–0x1EFFFF | Erased in the stock image |
| 0x1F0000–0x1FFFFF | Reserved; leave alone |

The V08.04 code is identical for the 196C and 199C. The model and its options come from the cal block, plus the bootloader bytes at 0x74–0x7F (raw bytes as read from flash):

| Source | 0x74–0x7F |
|---|---|
| 199C | `01 07 00 00  00 20 00 00  02 00 00 00` |
| 196B / 196C | `02 00 00 00  00 40 00 00  01 00 00 00` |
| After FlashTool's `CF` erase | `00 00 00 00  00 40 00 00  01 00 00 00` |

**A converted 196B only boots with the 199C values.** With the 196 values it is completely dead.

### Flash chips

Modules have either SST (0x00BF/0x2781) or ST M29W800 (0x0020/0x225B) chips. The programmer firmware uses SST's command sequences (unlock addresses 0x5555/0x2AAA). The M29W800 datasheet uses different unlock addresses, but the one ST module tried here accepted SST's sequences: it was erased, written and verified byte for byte five times with `--force-st`.

ST writes still need `--force-st` because that is one module in one programmer. The tool verifies every write, so a failed write is caught, but it leaves the module erased and in the programmer until you write it again from the backup. The programmer has no flow control, so the tools write one byte per serial call.

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

### Firmware updates over the optical port

Fluke's FlashTool updates the firmware over the optical cable. It only offers COM1–COM8 and runs at 19200 baud by default (an update takes 10–20 minutes). Its `data.bin` is plain text: an init header, a RAM extension (32 × 512 bytes loaded at 0x44000000 with `WB`), then 484 blocks of the form `\x1aDATA BLOCK\r\n,#H<addr>,#H<len>\r\n<len+1>\r\n<bytes><8-bit sum>\r\n`. The blocks cover the bootloader and code, never the cal; the boot header (0x40000000, 0x28 bytes) is the last block.

The flow is roughly: `ID`, `PC <baud>`, `EM FLUKEUHM` (into the bootloader), upload the extension, save the cal to `%temp%\<serial>.CAL`, then the extension's commands (each a `GO 0x44xxxxxx`):

| Command | Does |
|---|---|
| `CF` | Erases the **whole chip**, keeping only the cal banks, and writes a 16-byte struct at 0x70 from its own variables |
| `PF <addr>,<len>` | Programs `len` bytes streamed from the port (no erase) |
| `TI` | Checks the boot header: `0` valid, `1` not |
| `EO` | Exit and reboot |
| `FC`, `QF`, `FP` | Region report/query, port setup |

Before trying it:

- There is no sector erase, only `CF`. **Any failure after `CF` leaves a meter that won't boot** (the boot header comes last) and needs the programmer to recover.
- On a converted 196B, `CF` writes 196-type values at 0x74–0x7C (table above), so the meter won't boot even if the update succeeds. They can't be fixed afterwards with `PF`, because changing 0 bits to 1 needs an erase.
- Use the programmer to update a converted meter.

### Battery and charger (for pack replacements)

Useful if you fit a Li-ion pack in place of the NiMH one:

- The firmware charges with a NiMH profile: 1000 mA until a 6 h timer or dT/dt, 350 mA top-off for 2 h, then a 90 mA trickle. It has no voltage limit; the P-ASIC's VBATHIGH clamp sets one (47 µA into R4113, 38K3 for the stock ~11 V). A resistor in parallel with R4113 lowers it: roughly 0.285 V per kΩ of the combined resistance (120k in parallel → 29.0k → about 8.26 V measured).
- A full charge only happens with the meter off. With the meter on, it trickles.
- The gauge's capacity is 3500 mAh and only "learns" in 100 mAh steps, so a bigger pack reads empty long before it is. Charge is counted from the current **setpoint**, not the measured current, so a voltage-limited charge is overcounted.
- Low-battery warning at 6.9 V, empty at 6.0 V.
- The pack NTC is expected to be 10 kΩ at 20 °C. Charging starts only between 0 and 45 °C and stops at 50 °C. The pack's ID resistor (BATIDENT) sets the battery type.

## Acknowledgements

The memory-module programmer (hardware, AVR firmware and the original `programmer.py`) is the work of the **fluke19x-programmer** project: [github.com/feedbackloopchannel/fluke19x-programmer](https://github.com/feedbackloopchannel/fluke19x-programmer), with its [video](https://www.youtube.com/watch?v=klQPO-uYMEA) and the [EEVblog Fluke 199C repair thread](https://www.eevblog.com/forum/repair/fluke-199c-scopemeter-repair/). `convert_to_199c.py` speaks that programmer's serial protocol; none of the module work here would have been possible without it. Thank you.

Hardware details (resistor values, charger behaviour, cal steps) come from Fluke's 192B/196B-C/199B-C service manual.

## License

MIT, see [LICENSE](LICENSE). Provided as is, with no warranty; see the caution at the top of this README. The programmer hardware and firmware are not part of this repository and are covered by their own project.
