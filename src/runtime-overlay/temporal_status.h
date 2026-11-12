// Successful temporal dispatch metadata shared with the performance overlay.
#pragma once
typedef struct TemporalStatus {
    int state; // 0 off, 1 waiting, 2 active, 3 unavailable
    int engine; // 1 DLSS, 2 FSR 3.1
    int quality; // 0 native, 1 quality, 2 balanced, 3 performance
    int model; // requested NGX preset; 0 driver default
    unsigned width, height, output_width, output_height;
} TemporalStatus;
void vk_temporal_status(TemporalStatus *status);
