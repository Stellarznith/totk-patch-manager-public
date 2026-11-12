// Optional nonblocking GPU timings for temporal integration diagnostics.
#pragma once
#include <vulkan/vulkan.h>
enum { TEMP_GPU_SR, TEMP_GPU_CAPTURE, TEMP_GPU_PRESENT, TEMP_GPU_COUNT };
#ifdef __cplusplus
extern "C" {
#endif
int temporal_gpu_begin(VkCommandBuffer cb, int kind);
void temporal_gpu_end(VkCommandBuffer cb, int kind, int slot);
#ifdef __cplusplus
}
#endif
