// Native frame-generation integration; Vulkan proxies are enabled only when DLSS FG is requested at startup.
#pragma once
#include <vulkan/vulkan.h>
#ifdef __cplusplus
extern "C" {
#endif
int fg_requested(void);
int fg_startup_backend(void);
int fg_status(void); // 0 off, 1 restart, 2 unavailable, 3 generating, 4 waiting, 5 menu/HDR pause, 6 unfocused
// Why fg_status reports 2 (an unavailable frame generation), so the menu names the actual cause instead of listing all of
// them.
enum {
    FG_REASON_NONE,     // no reason recorded: unavailable for a cause the runtime does not classify
    FG_REASON_QUEUES,   // the queue family cannot give the four queues FSR 3 needs here
    FG_REASON_HDR,      // HDR is on, which blocks interpolation
    FG_REASON_FILES,    // the runtime files are missing or refused to load
    FG_REASON_VRAM,     // the device is out of VRAM for the interpolation targets
    FG_REASON_DEVICE,   // the device or driver does not support it
    FG_REASON_FENCE,    // the driver stopped answering, so interpolation was switched off for this run
};
int fg_unavailable_reason(void);
// Records why interpolation cannot start, for the backends that discover it themselves (fsr_framegen.cpp): the reason
// survives until the run ends and is what fg_unavailable_reason returns.
void fg_note_unavailable(int reason);
void fg_record_presents(unsigned count);
void fg_rates(double *render, double *display);
int fg_dlss_max_multiplier(void);
int fg_present_uses_private_queues(void);
void fg_begin_frame(void);
void fg_render_begin(void);
void fg_capture(VkCommandBuffer cb, VkImage color, unsigned ow, unsigned oh, VkImage depth,
                unsigned depth_level, unsigned depth_layer, VkImage motion, unsigned w, unsigned h,
                const float camera[64], float jx, float jy, int reset);
uint64_t fg_end_game_frame(void);
void fg_prepare_present(uint64_t frame);
void fg_tag_present(VkCommandBuffer cb, VkImage image, VkImageView view, VkFormat format,
                    unsigned w, unsigned h, int overlay, int hdr);
void fg_device_ready(VkInstance instance, VkPhysicalDevice physical, VkDevice device);
VkResult fg_vkCreateInstance(const VkInstanceCreateInfo *, const VkAllocationCallbacks *, VkInstance *);
VkResult fg_vkCreateDevice(VkPhysicalDevice, const VkDeviceCreateInfo *, const VkAllocationCallbacks *, VkDevice *);
VkResult fg_vkEnumeratePhysicalDevices(VkInstance, uint32_t *, VkPhysicalDevice *);
VkResult fg_vkCreateSwapchainKHR(VkDevice, const VkSwapchainCreateInfoKHR *, const VkAllocationCallbacks *, VkSwapchainKHR *);
void fg_vkDestroySwapchainKHR(VkDevice, VkSwapchainKHR, const VkAllocationCallbacks *);
VkResult fg_vkGetSwapchainImagesKHR(VkDevice, VkSwapchainKHR, uint32_t *, VkImage *);
VkResult fg_vkAcquireNextImageKHR(VkDevice, VkSwapchainKHR, uint64_t, VkSemaphore, VkFence, uint32_t *);
VkResult fg_vkQueuePresentKHR(VkQueue, const VkPresentInfoKHR *);
VkResult fg_vkDeviceWaitIdle(VkDevice);
void fg_vkDestroySurfaceKHR(VkInstance, VkSurfaceKHR, const VkAllocationCallbacks *);
PFN_vkVoidFunction fg_instance_proc(VkInstance instance, const char *name);
#ifdef __cplusplus
}
#endif
