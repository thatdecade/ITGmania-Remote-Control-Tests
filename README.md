# ITGmania-Remote-Control-Tests
Various ideas for remote controlling ITGmania's Simply Love interface.  Long term goal of creating a control-less auto-playing kiosk mode.

Tested with ITGmania 0.9.0 (build 5d4f9dcb07, 20240619)

---

## Tests

* Test 1 proves remote control is possible over a localhost WebSocket.
* Test 2 performs a theme-level boot override using a new screen.
* Test 3 turns the theme edits into a repeatable patch launcher.
* Test 4 adds a QR code image to song select via the patch launcher.
* Test 5 adds attract-style auto-advancing previews on the song select screen.

---

## Usage

For the patch-launcher-based tests (3, 4, 5):

1. Close ITGmania
2. Run the launcher script and point it at your ITGmania install directory.
3. The script applies the mod payload and then launches the game.
4. To undo changes, run the uninstall option.

---

## Notes

* Always close ITGmania before applying mods.
* The launcher-based tests are designed to be repeatable and to keep backups, but you should still keep a clean copy of your ITGmania install when iterating quickly.
* If something fails at boot or on song select, the first place to look is: `Save/Logs/log.txt`
