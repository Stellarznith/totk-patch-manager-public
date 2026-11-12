#include <math.h>
#include "dualsense_hd.h"

static float amplitude(float a) { return isfinite(a) && a > 0 ? (a < 1 ? a : 1) : 0; }
static float frequency(float f) { return isfinite(f) && f >= 40 && f <= 1253 ? f : 160; }

void hd_synthesize(HdSynth *s, const HdVibration v[2], float gain, float *out, int frames) {
    const double tau = 6.2831853071795864769;
    if (!isfinite(gain) || gain < 0) gain = 0;
    if (gain > 3) gain = 3;
    for (int n = 0; n < frames; n++) {
        out[4 * n] = out[4 * n + 1] = 0;
        for (int side = 0; side < 2; side++) {
            float a[2] = {amplitude(v[side].amp_low), amplitude(v[side].amp_high)};
            float f[2] = {frequency(v[side].freq_low), frequency(v[side].freq_high)};
            double sample = 0;
            for (int band = 0; band < 2; band++) {
                // A two-millisecond ramp avoids clicks without resetting phase between updates.
                s->amplitude[side][band] += (a[band] - s->amplitude[side][band]) / 96.0f;
                sample += s->amplitude[side][band] * sin(s->phase[side][band]);
                s->phase[side][band] += tau * f[band] / 48000.0;
                if (s->phase[side][band] >= tau) s->phase[side][band] -= tau;
            }
            sample *= 0.5 * gain;   // two unit-amplitude bands share one actuator's headroom
            out[4 * n + 2 + side] = (float)(sample < -1 ? -1 : sample > 1 ? 1 : sample);
        }
    }
}
