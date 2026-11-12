// AMD FSR 3 frame generation and native Vulkan swapchain replacement.
#pragma once
#include <vulkan/vulkan.h>
#include "temporal_camera.h"
#ifdef __cplusplus
extern "C" {
#endif
int fsr_fg_requested(void);
int fsr_fg_queue_count(VkPhysicalDevice physical, unsigned family);
int fsr_fg_available(void);
int fsr_fg_failed(void);
void fsr_fg_device(VkPhysicalDevice physical, VkDevice device, unsigned family);
VkResult fsr_fg_create(VkDevice device, const VkSwapchainCreateInfoKHR *ci, const VkAllocationCallbacks *a, VkSwapchainKHR *out);
void fsr_fg_destroy(VkDevice device, VkSwapchainKHR sc, const VkAllocationCallbacks *a);
VkResult fsr_fg_images(VkDevice device, VkSwapchainKHR sc, unsigned *count, VkImage *images);
VkResult fsr_fg_acquire(VkDevice device, VkSwapchainKHR sc, uint64_t timeout, VkSemaphore sem, VkFence fence, unsigned *index);
VkResult fsr_fg_present(VkQueue queue, const VkPresentInfoKHR *present);
void fsr_fg_frame(VkCommandBuffer cb, VkImage depth, VkImage motion, VkImage hudless, unsigned w, unsigned h,
                  unsigned ow, unsigned oh, const TemporalCamera *camera, float jx, float jy, uint64_t id, int enabled);
#ifdef __cplusplus
}
#endif
