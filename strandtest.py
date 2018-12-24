#!/usr/bin/python3

# Simple strand test for Adafruit Dot Star RGB LED strip.
# This is a basic diagnostic tool, NOT a graphics demo...helps confirm
# correct wiring and tests each pixel's ability to display red, green
# and blue and to forward data down the line.  By limiting the number
# and color of LEDs, it's reasonably safe to power a couple meters off
# USB.  DON'T try that with other code!

import time
import board
import adafruit_dotstar as dotstar

numpixels = 30           # Number of LEDs in strip
order     = dotstar.BGR  # Might need GRB instead for older DotStar LEDs
strip     = dotstar.DotStar(board.SCK, board.MOSI, numpixels,
              brightness=0.25, auto_write=False, pixel_order=order)

# Runs 10 LEDs at a time along strip, cycling through red, green and blue.
# This requires about 200 mA for all the 'on' pixels + 1 mA per 'off' pixel.

head  = 0                    # Index of first 'on' pixel
tail  = -10                  # Index of last 'off' pixel
color = 0xFF0000             # 'On' color (starts red)

while True:                  # Loop forever

    strip[head] = color      # Turn on 'head' pixel
    if tail >= 0:
        strip[tail] = 0      # Turn off 'tail'
    strip.show()             # Update strip
    time.sleep(1.0 / 50)     # Pause 20 milliseconds (~50 fps)

    head += 1                # Advance head position
    if(head >= numpixels):   # Off end of strip?
        head    = 0          # Reset to start
        color >>= 8          # Red->green->blue->black
        if(color == 0):      # If black...
            color = 0xFF0000 # reset to red

    tail += 1                # Advance tail position
    if(tail >= numpixels):   # Off end?
        tail = 0             # Reset to start
