
# Patch Launcher

This setup is a preflight launcher that installs a small kiosk mod into an existing ITGmania install, then launches the game.

---

## Folder layout

```
itgmania_kiosk_preflight_launcher2.py
mods/
  simplylove_screenkiosk/
    assets/
      metrics_screenkiosk_block.ini
    Themes/
      Simply Love/
        BGAnimations/
          ScreenKiosk overlay/
            default.lua
```

---

## Patching of two files

### metrics.ini

Target file:

* `Themes/Simply Love/metrics.ini`

The launcher makes two changes:

* Ensures `[Common] InitialScreen="ScreenKiosk"`
* Ensures a `[ScreenKiosk]` metrics block exists and matches the contents of:

  * `mods/simplylove_screenkiosk/assets/metrics_screenkiosk_block.ini`

### ScreenKiosk overlay/default.lua

Copied into the ITGmania folder:

* `Themes/Simply Love/BGAnimations/ScreenKiosk overlay/default.lua`

---

## Backup

`<itgmania_root>/preflight_backups/simplylove_screenkiosk/`
