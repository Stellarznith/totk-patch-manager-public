// Switch vibration parameters kept as two independent bands for each hand.
#pragma once
#include <stdint.h>
typedef struct { float amp_low, freq_low, amp_high, freq_high; } HdVibration;
typedef struct {
    double phase[2][2];
    float amplitude[2][2];
} HdSynth;
// Four interleaved channels: silent front pair, left/right haptic rear pair.
void hd_synthesize(HdSynth *s, const HdVibration v[2], float gain, float *out, int frames);
// Opt-in USB backend. Returns 1 only while a matching four-channel endpoint is open.
int dualsense_hd_update(const HdVibration v[2], const uint64_t at[2], int allowed, float strength);
int dualsense_hd_active(void);
