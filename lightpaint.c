/*------------------------------------------------------------------------
  Python/C module for Adafruit DotStar LED light painting on Raspberry Pi.

  The main light painter application code (in the DotStarPiPainter.py file)
  is written in Python.  The Python Imaging Library (or its more recent
  derivative, Pillow) has excellent support for image file decoding,
  scaling, etc., so it's a natural for the "high level" aspects of light
  painting.  But Python proved too slow for other operations -- namely the
  low-level pixel operations required for image prep and dithering.  This
  C code handles just those "seedy underbelly" functions.

  Written by Phil Burgess / Paint Your Dragon for Adafruit Industries.

  Adafruit invests time and resources providing this open source code,
  please support Adafruit and open-source hardware by purchasing products
  from Adafruit!
  ------------------------------------------------------------------------*/

#include <python2.7/Python.h>

// DotStar LED power estimates measured & divided from 100 pixels @ 5.1VDC.
#define mA0  1.25 // LED current when off (driver logic still needs some)
#define mAR 12.95 // + current for 100% red
#define mAG  9.90 // + current for 100% green
#define mAB  8.45 // + current for 100% blue

// A LightPaint object is requested for a Python image before painting:
typedef struct {
	PyObject_HEAD
	uint16_t  width, height; // Image dimensions in pixels
	uint8_t   offset[3];     // LED strip R,G,B offsets within pixel
	uint8_t  *pixels;        // -> Image data in RGB format
	uint8_t  *tables;        // Various dithering lookup tables
	double    px;            // Last x value passed to dither()
	Py_buffer pixelBuf;      // Python image pixel buffer
	uint8_t   vFlip;         // If >0, input at BOTTOM of strip
} LightPaintObject;

// CONSTRUCTOR: allocate a new LightPaint object for a given PIL Image and
// Adafruit_DotStar strip.  Required arguments are: Python image pixel data
// (using img.tostring()), pixel dimensions (img.size; w,h as tuple),
// R,G,B gamma values (tuple, 3 floats), R,G,B max values (tuple, 3 bytes),
// battery average and peak currents (milliamps).  The LED strip color order
// can optionally be passed as a keyword argument, e.g. append "order='gbr'"
// if using older DotStar pixels (BRG is default).  Optionally pass
// "vflip='true'" to flip image vertically if input end of strip is at the
// bottom rather than top.
static PyObject *LightPaint_new(
  PyTypeObject *type, PyObject *arg, PyObject *kw) {
        LightPaintObject *self = NULL;
	Py_buffer         pixelBuf;         // Python image pixel buffer
	uint32_t          width, height,    // Image size in pixels
	                  mAavg, mApeak;    // Average/peak current, mA
	uint8_t           offset[]={2,3,1}, // R,G,B indexes (BRG default)
	                  max[3];           // R,G,B max
	double            gamma[3];         // R,G,B gamma
	PyObject         *string;           // 'order' value as Python object
	char             *order;            // 'order' value as C string
	char             *vf;               // 'vflip' value as C string
        uint8_t           vFlip = 0;        // If set, input at strip bottom

	// See comments above re: required arguments
	if(!PyArg_ParseTuple(arg, "s*(II)(ddd)(bbb)(II)",
	  &pixelBuf, &width, &height,      // Pixel data, w,h in pixels
	  &gamma[0], &gamma[1], &gamma[2], // R,G,B gammas
	  &max[0]  , &max[1]  , &max[2],   // R,G,B maxes
	  &mAavg, &mApeak)) return NULL;   // Current average, peak

	if(kw) { // Optional keyword arguments passed?
		// Use keyword 'order' to specify R,G,B pixel order, e.g.
		// "order='rgb'" or similar (switch r/g/b around to match
		// strip).  Order string isn't much validated; nonsense
		// and mayhem could potentially occur.
		if((string = PyDict_GetItemString(kw, "order")) &&
		   (order = PyString_AsString(string))) {
			char *c, i;
			for(i=0; order[i]; i++) order[i] = tolower(order[i]);
			if((c = strchr(order, 'r'))) offset[0] = c - order + 1;
			if((c = strchr(order, 'g'))) offset[1] = c - order + 1;
			if((c = strchr(order, 'b'))) offset[2] = c - order + 1;
		}

		// Use keyword 'vflip' to indicate whether input end is
		// at top ("vflip='false'") or bottom ("vflip='true').
		// Can also use 0/1 for false/true.
		if((string = PyDict_GetItemString(kw, "vflip")) &&
		   (vf = PyString_AsString(string))) {
			vFlip = ((!strcasecmp(vf, "true")) ||
			  !strcmp(vf, "1"));
		}
	}

	// Allocate LightPaintObject...
	if((self = (LightPaintObject *)type->tp_alloc(type, 0))) {
		// Allocate space for conversion tables...
		if((self->tables = (uint8_t *)malloc(height * 3 + 256 * 9))) {
			// Success!  Save image parameters.
			self->pixels   = pixelBuf.buf;
			self->width    = width;
			self->height   = height;
			self->px       = 2.0;
			self->pixelBuf = pixelBuf; // Released in destructor
			self->vFlip    = vFlip;
			memcpy(self->offset, offset, sizeof(offset));

			// STEP 1 of 3: estimate average and max power at
			// given color balance settings.

			uint16_t x, y, c, i, j, n;
			uint8_t *in, r, g, b;
			double   colMaxC = 0.0, // Maximum column current
			         colAvgC = 0.0, // Average column current
			         colC,          // Current column current
			         mA[3];

			// Milliamp ratings for given R,G,B maximums
			mA[0] = mAR * (double)max[0] / 255.0;
			mA[1] = mAG * (double)max[1] / 255.0;
			mA[2] = mAB * (double)max[2] / 255.0;

			for(x=0; x<width; x++) { // For each column...
				colC = 0.0;      // Clear column sum
				for(y=0; y<height; y++) { // Each row...
					// Python image order is always
					// R,G,B; strip color order doesn't
					// matter at this stage.
					in    = &self->pixels[(y*width+x)*3];
					r     = *in++;
					g     = *in++;
					b     = *in;
					// Est. pixel mA, add to column sum
					colC += mA0 +
					  pow((double)r/255.0,gamma[0])*mA[0]+
					  pow((double)g/255.0,gamma[1])*mA[1]+
					  pow((double)b/255.0,gamma[2])*mA[2];
				}
				if(colC > colMaxC) colMaxC = colC;
				colAvgC += colC;
			}
			colAvgC /= (double)width;
			//printf("Avg current: %f mA\n", colAvgC);
			//printf("Peak current: %f mA\n", colMaxC);

			// STEP 2 of 3: Constrain max and average current

			double s1, s2;
			s1 = (double)mApeak / colMaxC; // Scale for peak mA
			s2 = (double)mAavg  / colAvgC; // Scale for avg mA
			if(s2 < s1) s1 = s2;   // Use smaller of two, and
			if(s1 > 1.0) s1 = 1.0; // never increase brightness
			// Adjust 'max' values by power scale factor
			for(x=0; x<3; x++) {
				max[x] = (uint8_t)((double)max[x] * s1 + 0.5);
			}

			// STEP 3 of 3: Compute dither tables based
			// on computed constraints.

			for(c=0; c<3; c++) { // R,G,B
				for(i=0; i<256; i++) {
					// Calc 16-bit gamma-corrected level
					n = (uint16_t)(pow((double)i / 255.0,
					  gamma[c]) * (double)max[c] * 256.0 +
					  0.5);
					// Store as 8-bit brightness level
					// and 'dither up' probability.
					self->tables[       c * 256 + i] =
					  n >> 8;
					self->tables[1536 + c * 256 + i] =
					  n & 0xFF;
				}
				// Second pass, calc 'next' level for each
				// 8-bit brightness (based on lower value)
				for(i=0; i<256; i++) {
					n = self->tables[c * 256 + i];
					for(j=i; (j<256) &&
					  (self->tables[c * 256 + j] <= n);
					  j++);
					self->tables[768 + c * 256 + i] =
					  self->tables[c * 256 + j];
				}
			}

			Py_INCREF(self); // Done, save object
		} else { // tables malloc failed
			free(self);
			self = NULL;
		}
	}

	// pixelBuf is NOT released here!  Needs to stay resident until
	// destructor is called.
	return (PyObject *)self;
}

// Process one column from source image to dest LED buffer.  Interpolates
// between image columns, reorders R,G,B, applies 16-bit gamma correction
// and diffusion dithering.
static PyObject *dither(LightPaintObject *self, PyObject *arg) {
	Py_buffer ledBuf;
	double    x;
	uint32_t  lCol, rCol, rowInc;
	uint16_t  lWeight, rWeight, e, y;
	uint8_t   n,
	         *ledPtr, *leftPtr, *rightPtr,
	         *rLo   = self->tables, // Gamma lookup tables for R,G,B
	         *gLo   = &rLo[256],    // First 3 are 8-bit lower brightness
	         *bLo   = &gLo[256],
	         *rHi   = &bLo[256],    // Next 3 are 8-bit upper brightness
	         *gHi   = &rHi[256],
	         *bHi   = &gHi[256],
	         *rFrac = &bHi[256],    // Next 3 are 8-bit gamma fraction
	         *gFrac = &rFrac[256],
	         *bFrac = &gFrac[256],
	         *ePtr  = &bFrac[256];  // Last is dither error accumulator

	if(!PyArg_ParseTuple(arg, "s*d", &ledBuf, &x)) return NULL;
	if(x < self->px) {
		// If starting new image, clear error accumulator
		memset(&self->tables[256 * 9], 0, self->height * 3);
	}
	self->px = x;

	x       *= (double)(self->width - 1); // 0.0 to image width-1
	lCol     = (int)x;
	rCol     = lCol + 1;
	if(rCol >= self->width) rCol = self->width - 1;
	// Left/right column weightings (1-256)
	rWeight  = 1 + (int)((x - (double)lCol) * 256.0);
	lWeight  = 257 - rWeight;
	ledPtr   = ledBuf.buf;              // -> Output data
	leftPtr  = &self->pixels[lCol * 3]; // -> Left column input
	rightPtr = &self->pixels[rCol * 3]; // -> Right column input
	rowInc   = self->width * 3;

	if(self->vFlip) {
		leftPtr  += rowInc * (self->height - 1);
		rightPtr += rowInc * (self->height - 1);
		rowInc   *= -1;
	}

	for(y = self->height; y--; ) {
		ledPtr[0] = 0xFF; // DotStar pixel header

		// Interpolate left/right column red values
		n = (leftPtr[0] * lWeight + rightPtr[0] * rWeight) >> 8;
		// Add dither probability for value to accumulated error
		if((e = (rFrac[n] + *ePtr)) < 256) { // <1.0 ?
			// Error term below 1.0; use dimmer color
			ledPtr[self->offset[0]] = rLo[n];
		} else {
			// Error >= 1.0; use brighter color...
			ledPtr[self->offset[0]] = rHi[n];
			e -= 256; // ...and reduce error by 1.0
		}
		*ePtr++ = e; // Store modified error term back in buffer

		// Green:
		n = (leftPtr[1] * lWeight + rightPtr[1] * rWeight) >> 8;
		if((e = (gFrac[n] + *ePtr)) < 256) {
			ledPtr[self->offset[1]] = gLo[n];
		} else {
			ledPtr[self->offset[1]] = gHi[n];
			e -= 256;
		}
		*ePtr++ = e;

		// Blue:
		n = (leftPtr[2] * lWeight + rightPtr[2] * rWeight) >> 8;
		if((e = (bFrac[n] + *ePtr)) < 256) {
			ledPtr[self->offset[2]] = bLo[n];
		} else {
			ledPtr[self->offset[2]] = bHi[n];
			e -= 256;
		}
		*ePtr++ = e;

		leftPtr  += rowInc; // Advance 1 row in src image
		rightPtr += rowInc;
		ledPtr   += 4;      // Advance 1 pixel in dest buffer
	}

	PyBuffer_Release(&ledBuf);
	Py_INCREF(Py_None);
	return Py_None;
}

static void LightPaint_dealloc(LightPaintObject *self) {
	if(self->tables) {
		free(self->tables);
		PyBuffer_Release(&self->pixelBuf);
	}
	self->ob_type->tp_free((PyObject *)self);
}

static PyMethodDef methods[] = {
  { "dither", (PyCFunction)dither, METH_VARARGS, NULL },
  { NULL, NULL, 0, NULL }
};

static PyTypeObject LightPaintObjectType = {
	PyObject_HEAD_INIT(NULL)
	0,                              // ob_size (not used, always set to 0)
	"lightpaint.LightPaint",        // tp_name (module name, object name)
	sizeof(LightPaintObject),       // tp_basicsize
	0,                              // tp_itemsize
	(destructor)LightPaint_dealloc, // tp_dealloc
	0,                              // tp_print
	0,                              // tp_getattr
	0,                              // tp_setattr
	0,                              // tp_compare
	0,                              // tp_repr
	0,                              // tp_as_number
	0,                              // tp_as_sequence
	0,                              // tp_as_mapping
	0,                              // tp_hash
	0,                              // tp_call
	0,                              // tp_str
	0,                              // tp_getattro
	0,                              // tp_setattro
	0,                              // tp_as_buffer
	Py_TPFLAGS_DEFAULT,             // tp_flags
	0,                              // tp_doc
	0,                              // tp_traverse
	0,                              // tp_clear
	0,                              // tp_richcompare
	0,                              // tp_weaklistoffset
	0,                              // tp_iter
	0,                              // tp_iternext
	methods,                        // tp_methods
	0,                              // tp_members
	0,                              // tp_getset
	0,                              // tp_base
	0,                              // tp_dict
	0,                              // tp_descr_get
	0,                              // tp_descr_set
	0,                              // tp_dictoffset
	0,                              // tp_init
	0,                              // tp_alloc
	LightPaint_new,                 // tp_new
	0,                              // tp_free
};

PyMODINIT_FUNC initlightpaint(void) { // Module initialization function
	PyObject* m;

	if((m = Py_InitModule("lightpaint", methods)) &&
	   (PyType_Ready(&LightPaintObjectType) >= 0)) {
		Py_INCREF((void *)&LightPaintObjectType);
		PyModule_AddObject(m, "LightPaint",
		  (PyObject *)&LightPaintObjectType);
	}
}
