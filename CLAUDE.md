# Fluke 19x ScopeMeter tools

`README.md` describes the tools, the remote commands, the flash layout and the cal-block format.

Meter-specific notes (`HANDOFF.md`), flash dumps, the programmer hardware/firmware and retired scripts live outside the repo in `..\fluke19x-backups\` (checksums in `MD5SUMS.before`).

## Firmware patch work in progress
Before doing anything else in a session, read `..\fluke19x-backups\RE-STATUS.md`. It has the goals, the state of each patch, what the user has decided, and the next step. Update it before the session ends. The RE scripts are in `..\fluke19x-backups\re\`. Don't work from session scratchpad copies.

## Hardware
- Flash programmer: COM11, 115200. Meter optical cable: COM13 (the meter drops to 1200 baud after every off/on; send `PC 115200` at 1200 to restore it).

## Safety rules
- Never send `EM` with `flukeuhm`; it appears to jump to the bootloader/update mode.
- `RC` writes the cal record and changes the cal date and counter. Only use it to save an intended change.
- Before any flash or cal write: take a backup. After it: read back and byte-compare.
- Don't write ST-chip modules (0x0020/0x225B) with the programmer; only the SST module was write-tested.
- Bootloader bytes 0x74-0x7F must be the 199C values on a converted 196B, or the meter won't boot.
- The `.bin` dumps are irreplaceable backups (one meter's calibration). Never overwrite or delete them, and don't commit them (`*.bin` is ignored).

## Conventions
- Python with pyserial, numpy, matplotlib. Run scripts that read untrusted dumps with `python -I`.
