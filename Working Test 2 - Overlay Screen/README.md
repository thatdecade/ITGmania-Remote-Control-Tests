# Add ScreenKiosk Overlay

1. Create this folder: 

   **Themes/Simply Love/BGAnimations/ScreenKiosk overlay/**

2. Copy default.lua into that folder

3. Edit Themes/Simply Love/metrics.ini:
   * Change `InitialScreen="ScreenKiosk"`
   * Add `[ScreenKiosk]`

```bash
  [Common]
  InitialScreen="ScreenKiosk"
  ImageCache=ThemePrefs.Get("UseImageCache") and "Banner,Background,Jacket" or "Banner"
  DefaultNoteSkinName="cel"
  
  [ScreenKiosk]
  Class="ScreenAttract"
  Fallback="ScreenAttract"
  NextScreen="ScreenSelectMusicCasual"
  AllowStartToSkip=false
  PrepareScreen=true
  TimerSeconds=0.5
  ForceTimer=true
  StopMusicOnBack=false
  MemoryCardIcons=false
  HeaderOnCommand=visible,false
  FooterOnCommand=visible,false
```

---

## How to test ScreenKiosk

1. Launch ITGmania and select the Simply Love theme (if it is not already selected).
2. Restart the game to confirm the initial screen path is used.
3. Expected behavior on boot:

   * Boot screen appears briefly (about 0.5 seconds).
   * It automatically advances to ScreenSelectMusicCasual.
4. Confirm P1 auto-joined:

   * You should see P1 UI and be able to navigate immediately without a join prompt.
   * P2 should not be joined.
5. Confirm style is valid:

   * You can enter a song and it loads single play normally.
6. Confirm Casual mode actually took effect:

   * Start a song and verify gameplay behavior matches Casual (notably the reduced judgment window set and Casual pruning behavior on the Casual wheel).
7. Regression check:

   * Play through to evaluation and allow the normal flow to proceed to ScreenGameOver and beyond, ensuring nothing crashes and ScreenGameOver still behaves normally.

If anything fails before reaching ScreenSelectMusicCasual, check **Logs/log.txt** for a Lua error.