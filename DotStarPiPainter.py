#!/usr/bin/python3

# --------------------------------------------------------------------------
# DotStar Light Painter for Raspberry Pi.
#
# Hardware requirements:
# - Raspberry Pi computer (any model)
# - DotStar LED strip (any length, but 144 pixel/m is ideal):
#   www.adafruit.com/products/2242
# - Five momentary pushbuttons for controls, such as:
#   www.adafruit.com/products/1010
# - One 74AHCT125 logic level shifter IC:
#   www.adafruit.com/products/1787
# - High-current, high-capacity USB battery bank such as:
#   www.adafruit.com/products/1566
# - Perma-Proto HAT for Raspberry Pi:
#   www.adafruit.com/products/2310
# - Various bits and bobs to integrate the above parts.  Wire, Perma-Proto
#   PCB, 3D-printed enclosure, etc.  Your approach may vary...improvise!
#
# Software requirements:
# - Raspbian "Lite" operating system
# - Python 3
# - Adafruit Blinka Python library (CircuitPython for Raspberry Pi),
#   including DotStar module.
#   learn.adafruit.com/circuitpython-on-raspberrypi-linux
#   learn.adafruit.com/adafruit-dotstar-leds/python-circuitpython
# - usbmount:
#   sudo apt-get install usbmount
#   See file "99_lightpaint_mount" for add'l info.
#
# Written by Phil Burgess / Paint Your Dragon for Adafruit Industries.
#
# Adafruit invests time and resources providing this open source code,
# please support Adafruit and open-source hardware by purchasing products
# from Adafruit!
# --------------------------------------------------------------------------

import os
import select
import signal
import time
import board
import busio
import digitalio
import adafruit_dotstar as dotstar
from evdev import InputDevice, ecodes
from lightpaint import LightPaint
from PIL import Image

# CONFIGURABLE STUFF -------------------------------------------------------

num_leds   = 144         # Length of LED strip, in pixels
pin_go     = board.D22   # GPIO pin numbers for 'go' button,
pin_next   = board.D17   # previous image, next image and speed +/-.
pin_prev   = board.D4    # Buttons should connect from these pins to ground.
pin_faster = board.D23
pin_slower = board.D24
vflip      = 'true'      # 'true' if strip input at bottom, else 'false'
order      = dotstar.BGR # BGR for current DotStars, GBR for pre-2015 strips
order2     = 'bgr'       # lightpaint lib uses a different syntax for same
spispeed   = 12000000    # SPI clock rate...
# 12000000 (12 MHz) is the fastest I could reliably operate a 288-pixel
# strip without glitching. You can try faster, or may need to set it lower,
# no telling.

# DotStar strip data & clock connect to hardware SPI pins (GPIO 10 & 11).
strip     = dotstar.DotStar(board.SCK, board.MOSI, num_leds, brightness=1.0,
              auto_write=False, pixel_order=order)
# The DotStar library is used for status updates (loading progress, etc.),
# we pull shenanigans here and also access the SPI bus directly for ultra-
# fast strip updates with data out of the lightpaint library.
spi       = busio.SPI(board.SCK, MOSI=board.MOSI)
path      = '/media/usb'         # USB stick mount point
mousefile = '/dev/input/mouse0'  # Mouse device (as positional encoder)
eventfile = '/dev/input/event0'  # Mouse events accumulate here
dev       = None                 # None unless mouse is detected

gamma          = (2.8, 2.8, 2.8) # Gamma correction curves for R,G,B
color_balance  = (128, 255, 180) # Max brightness for R,G,B (white balance)
power_settings = (1450, 1550)    # Battery avg and peak current

# INITIALIZATION -----------------------------------------------------------

# Set control pins to inputs and enable pull-up resistors.
# Buttons should connect between these pins and ground.
button_go               = digitalio.DigitalInOut(pin_go)
button_go.direction     = digitalio.Direction.INPUT
button_go.pull          = digitalio.Pull.UP
button_prev             = digitalio.DigitalInOut(pin_prev)
button_prev.direction   = digitalio.Direction.INPUT
button_prev.pull        = digitalio.Pull.UP
button_next             = digitalio.DigitalInOut(pin_next)
button_next.direction   = digitalio.Direction.INPUT
button_next.pull        = digitalio.Pull.UP
button_slower           = digitalio.DigitalInOut(pin_slower)
button_slower.direction = digitalio.Direction.INPUT
button_slower.pull      = digitalio.Pull.UP
button_faster           = digitalio.DigitalInOut(pin_faster)
button_faster.direction = digitalio.Direction.INPUT
button_faster.pull      = digitalio.Pull.UP

ledBuf = bytearray(4 + (num_leds * 4) + ((num_leds + 15) // 16))
for i in range(4):
    ledBuf[i] = 0x00 # 4 header bytes
for i in range(4 + num_leds * 4, len(ledBuf)):
    ledBuf[i] = 0xFF # Footer bytes
imgNum     = 0    # Index of currently-active image
duration   = 2.0  # Image paint time, in seconds
filename   = None # List of image files (nothing loaded yet)
lightpaint = None # LightPaint object for currently-active image (none yet)

# If a mouse is plugged in, set up epoll for sensing position
if os.path.exists(mousefile):
    dev = InputDevice(eventfile)
    # Register mouse descriptor with epoll
    epoll = select.epoll()
    epoll.register(dev.fileno(), select.EPOLLIN)
    print('Using mouse for positional input')

# FUNCTIONS ----------------------------------------------------------------

# Signal handler when SIGUSR1 is received (USB flash drive mounted,
# triggered by usbmount and 99_lightpaint_mount script).
def sigusr1_handler(signum, frame):
    scandir()

# Ditto for SIGUSR2 (USB drive removed -- clears image file list)
def sigusr2_handler(signum, frame):
    global filename
    filename = None
    imgNum   = 0
    # Current LightPaint object is left resident

# Scan root folder of USB drive for viable image files.
def scandir():
    global imgNum, lightpaint, filename
    files     = os.listdir(path)
    num_files = len(files) # Total # of files, whether images or not
    filename  = []         # Filename list of valid images
    imgNum    = 0
    if num_files == 0:
        return
    for i, f in enumerate(files):
        lower =  i      * num_leds // num_files
        upper = (i + 1) * num_leds // num_files
        for n in range(lower, upper):
            strip[n] = (1, 0, 0) # Yellow
        strip.show()
        if f[0] == '.':
            continue
        try:
            Image.open(os.path.join(path, f))
        except:
            continue       # Is directory or non-image file; skip
        filename.append(f) # Valid image, add to list
        time.sleep(0.05)   # Tiny pause so progress bar is visible
    strip.fill(0)
    strip.show()
    if len(filename) > 0:              # Found some image files?
        filename.sort()                # Sort list alphabetically
        lightpaint = loadImage(imgNum) # Load first image

# Load image, do some conversion and processing as needed before painting.
def loadImage(index):
    num_images = len(filename)
    lower      =  index      * num_leds // num_images
    upper      = (index + 1) * num_leds // num_images
    for n in range(lower, upper):
        strip[n] = (1, 0, 0) # Red = loading
    strip.show()
    print("Loading '" + filename[index] + "'...")
    startTime = time.time()
    # Load image, convert to RGB if needed
    img = Image.open(os.path.join(path, filename[index])).convert("RGB")
    print('\t%dx%d pixels' % img.size)

    # If necessary, image is vertically scaled to match LED strip.
    # Width is NOT resized, this is on purpose.  Pixels need not be
    # square!  This makes for higher-resolution painting on the X axis.
    if img.size[1] != num_leds:
        print('\tResizing...',)
        img = img.resize((img.size[0], num_leds), Image.LANCZOS)
        print('now %dx%d pixels' % img.size)

    # Convert raw RGB pixel data to a 'bytes' buffer.
    # The C module can easily work with this format.
    pixels = img.tobytes() # Current/preferred PIL method
    print('\t%f seconds' % (time.time() - startTime))

    # Do external C processing on image; this provides 16-bit gamma
    # correction, diffusion dithering and brightness adjustment to
    # match power source capabilities.
    for n in range(lower, upper):
        strip[n] = (1, 1, 0) # Yellow
    strip.show()
    print('Processing...')
    startTime  = time.time()
    # Pixel buffer, image size, gamma, color balance and power settings
    # are REQUIRED arguments.  One or two additional arguments may
    # optionally be specified:  "order='rgb'" is to maintain compat.
    # (color reordering is done in DotStar lib now)
    # "vflip='true'" indicates that the
    # input end of the strip is at the bottom, rather than top (I
    # prefer having the Pi at the bottom as it provides some weight).
    # Returns a LightPaint object which is used later for dithering
    # and display.
    lightpaint = LightPaint(pixels, img.size, gamma, color_balance,
      power_settings, order=order2, vflip=vflip)
    print('\t%f seconds' % (time.time() - startTime))

    # Success!
    for n in range(lower, upper):
        strip[n] = (0, 1, 0) # Green
    strip.show()
    time.sleep(0.25) # Tiny delay so green 'ready' is visible
    print('Ready!')

    strip.fill(0)
    strip.show()
    return lightpaint

def btn():
    if not button_go.value:     return 1
    if not button_faster.value: return 2
    if not button_slower.value: return 3
    if not button_next.value:   return 4
    if not button_prev.value:   return 5
    return 0

# MAIN LOOP ----------------------------------------------------------------

# Init some stuff for speed selection...
max_time    = 10.0
min_time    =  0.1
time_range  = (max_time - min_time)
speed_pixel = int(num_leds * (duration - min_time) / time_range)
duration    = min_time + time_range * speed_pixel / (num_leds - 1)
prev_btn    = 0
rep_time    = 0.2

scandir() # USB drive might already be inserted
signal.signal(signal.SIGUSR1, sigusr1_handler) # USB mount signal
signal.signal(signal.SIGUSR2, sigusr2_handler) # USB unmount signal

try:
    while True:
        b = btn()
        if b == 1 and lightpaint != None:
            # Paint!
            spi.try_lock()
            spi.configure(baudrate=spispeed)

            if dev is None: # Time-based

                startTime = time.time()
                while True:
                    t1      = time.time()
                    elapsed = t1 - startTime
                    if elapsed > duration:
                        break
                    # dither() function is passed a destination buffer and
                    # a float from 0.0 to 1.0 indicating which column of
                    # the source image to render.  Interpolation happens.
                    lightpaint.dither(ledBuf, elapsed / duration)
                    spi.write(ledBuf)

            else: # Encoder-based

                mousepos = 0
                scale    = 0.01 / (speed_pixel + 1)
                while True:
                    input = epoll.poll(-1) # Non-blocking
                    for i in input: # For each pending...
                        try:
                            for event in dev.read():
                                if(event.type == ecodes.EV_REL and
                                   event.code == ecodes.REL_X):
                                    mousepos += event.value
                        except:
                            # If this occurs, usually power settings
                            # are too high for battery source.
                            # Voltage sags, Pi loses track of USB device.
                            print('LOST MOUSE CONNECTION')
                            continue

                    pos = abs(mousepos) * scale
                    if pos > 1.0: break
                    lightpaint.dither(ledBuf, pos)
                    spi.write(ledBuf)

            if btn() != 1: # Button released?
                spi.unlock()
                strip.fill(0)
                strip.show()

        elif b == 2:
            # Decrease paint duration
            if speed_pixel > 0:
                speed_pixel -= 1
                duration = (min_time + time_range *
                  speed_pixel / (num_leds - 1))
            strip[speed_pixel] = (0, 0, 128)
            strip.show()
            startTime = time.time()
            while (btn() == 2 and ((time.time() - startTime) <
              rep_time)): continue
            strip.fill(0)
            strip.show()
        elif b == 3:
            # Increase paint duration (up to 10 sec maximum)
            if speed_pixel < num_leds - 1:
                speed_pixel += 1
                duration = (min_time + time_range *
                  speed_pixel / (num_leds - 1))
                strip[speed_pixel] = (0, 0, 128)
                strip.show()
                startTime = time.time()
                while (btn() == 3 and ((time.time() - startTime) <
                  rep_time)): continue
                strip.fill(0)
                strip.show()
        elif b == 4 and filename != None:
            # Next image (if USB drive present)
            imgNum += 1
            if imgNum >= len(filename): imgNum = 0
            lightpaint = loadImage(imgNum)
            while btn() == 4: continue
        elif b == 5 and filename != None:
            # Previous image (if USB drive present)
            imgNum -= 1
            if imgNum < 0: imgNum = len(filename) - 1
            lightpaint = loadImage(imgNum)
            while btn() == 5: continue
        if b > 0 and b == prev_btn:
            # If button held, accelerate speed selection
            rep_time *= 0.92
            if rep_time < 0.01: rep_time = 0.01
        else:
            rep_time = 0.2
        prev_btn = b

except KeyboardInterrupt:
    print('Cleaning up')
    strip.fill(0)
    strip.show()
    print('Done!')
