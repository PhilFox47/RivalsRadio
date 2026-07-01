"""RivalsRadio — play a Spotify playlist matched to your current Marvel Rivals hero.

Hero detection runs through the companion native Overwolf app (or the
ow-electron GEP bridge), which reports your current hero over localhost; you can
also switch heroes manually from the UI. RivalsRadio then switches Spotify
playback and drives a themed, audio-reactive Stage view.

No game memory is read or modified — hero data comes from Overwolf's supported
Game Events, so it does not interact with anti-cheat.
"""

__version__ = "0.4.0"
