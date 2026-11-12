// Scene camera reconstructed from the same captured uniform block the motion-vector shader reads.
#pragma once
typedef struct {
    float view[16], inverse_view[16], projection[16], inverse_projection[16];
    float view_projection[16], inverse_view_projection[16];
    float near_plane, far_plane, fov, aspect;
} TemporalCamera;
#ifdef __cplusplus
extern "C" {
#endif
int temporal_camera(const float rows[64], TemporalCamera *out);
int temporal_camera_complete(float rows[64], float aspect);
int temporal_inverse(const float in[16], float out[16]);
void temporal_multiply(const float a[16], const float b[16], float out[16]);
#ifdef __cplusplus
}
#endif
