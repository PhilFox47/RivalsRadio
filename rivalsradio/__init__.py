"""RivalsRadio — play a Spotify playlist matched to your current Marvel Rivals hero.

The app captures a small region of the game screen (your HUD), matches it against
reference images you record per hero during calibration, and switches Spotify
playback when it detects you've changed heroes.

No game memory is read or modified — this is pure screen capture, the same
category of thing OBS/streaming software does, so it does not interact with
anti-cheat.
"""

__version__ = "0.1.0"
