// FidelityFX frame generation via the Vulkan FSR SDK loader. AMD's headers are retained in third_party/fidelityfx-2.4.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <vector>
#include <cstring>
#include <algorithm>
#include "third_party/fidelityfx-2.4/framegeneration/include/ffx_framegeneration.h"
#include "third_party/fidelityfx-2.4/framegeneration/include/vk/ffx_api_framegeneration_vk.h"
#define FRAMEGEN_IMPLEMENTATION
extern "C" {
#include "cfg.h"
#include "host.h"
#include "vk.h"
#include "fsr_framegen.h"
#include "framegen.h"
}

static HMODULE library;
static PfnFfxCreateContext create_context;
static PfnFfxDestroyContext destroy_context;
static PfnFfxConfigure configure;
static PfnFfxDispatch dispatch;
static PfnFfxQuery query;
static ffxContext swap_context{},gen_context{};
static ffxQueryDescSwapchainReplacementFunctionsVK functions{};
static VkDevice device;
static VkPhysicalDevice physical;
static VkQueue queues[4];
static unsigned family,queue_count;
static VkSwapchainKHR swapchain;
static std::vector<VkSwapchainKHR> retired;
static unsigned display_w,display_h;
static VkFormat back_format;
static bool enabled, generator_failed, swapchain_failed;
static VkResult submit_game(unsigned count,const VkSubmitInfo *info,VkFence fence){
    vk_queue_lock();VkResult r=vkQueueSubmit(queues[0],count,info,fence);vk_queue_unlock();return r;
}

int fsr_fg_requested(void){return fg_startup_backend()==2;}
int fsr_fg_queue_count(VkPhysicalDevice p,unsigned f){
    queue_count=1;
    if(!fsr_fg_requested())return 1;
    unsigned n=0;vkGetPhysicalDeviceQueueFamilyProperties(p,&n,nullptr);
    std::vector<VkQueueFamilyProperties> props(n);vkGetPhysicalDeviceQueueFamilyProperties(p,&n,props.data());
    if(f<n && props[f].queueCount>=4)queue_count=4;
    else{
        rt_log("framegen: FSR 3 requires four independently synchronized queues in this integration; unavailable on this device");
        fg_note_unavailable(FG_REASON_QUEUES);
    }
    return queue_count;
}
int fsr_fg_available(void){return library && queue_count==4 && !swapchain_failed;}
int fsr_fg_failed(void){return generator_failed || swapchain_failed;}
void fsr_fg_device(VkPhysicalDevice p,VkDevice d,unsigned f){
    physical=p;device=d;family=f;
    if(!fsr_fg_requested()||queue_count!=4)return;
    wchar_t path[32768];DWORD n=GetModuleFileNameW(nullptr,path,32768);while(n&&path[n-1]!=L'\\')--n;
    wcscpy(path+n,L"amd_fidelityfx_loader_vk.dll");
    library=LoadLibraryExW(path,nullptr,LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR|LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
    if(!library){fg_note_unavailable(FG_REASON_FILES);return;}
#define GET(member,name) member=reinterpret_cast<decltype(member)>(GetProcAddress(library,name))
    GET(create_context,"ffxCreateContext");GET(destroy_context,"ffxDestroyContext");GET(configure,"ffxConfigure");
    GET(dispatch,"ffxDispatch");GET(query,"ffxQuery");
#undef GET
    if(!create_context||!destroy_context||!configure||!dispatch||!query){library=nullptr;fg_note_unavailable(FG_REASON_FILES);return;}
    for(unsigned i=0;i<4;i++)vkGetDeviceQueue(d,f,i,&queues[i]);
    rt_log("framegen: FSR 3 native API ready, four queues in family %u",f);
}
static void log_provider(ffxContext *context, const char *kind) {
    ffxQueryGetProviderVersion version{};
    version.header.type = FFX_API_QUERY_DESC_TYPE_GET_PROVIDER_VERSION;
    if (!query(context, &version.header) && version.versionName)
        rt_log("framegen: AMD %s provider: %s", kind, version.versionName);
}
static void wait_presents(){
    if(!swap_context)return;
    ffxDispatchDescFrameGenerationSwapChainWaitForPresentsVK d{};
    d.header.type=FFX_API_DISPATCH_DESC_TYPE_FGSWAPCHAIN_WAIT_FOR_PRESENTS_VK;dispatch(&swap_context,&d.header);
}
VkResult fsr_fg_create(VkDevice d,const VkSwapchainCreateInfoKHR *ci,const VkAllocationCallbacks *a,VkSwapchainKHR *out){
    if(!fsr_fg_available())return vkCreateSwapchainKHR(d,ci,a,out);
    if(swap_context){
        wait_presents();vkDeviceWaitIdle(d);
        if(gen_context){destroy_context(&gen_context,nullptr);gen_context=nullptr;}
        retired.push_back(swapchain);destroy_context(&swap_context,nullptr);swap_context=nullptr;swapchain=VK_NULL_HANDLE;
    }
    generator_failed = false;
    ffxCreateContextDescFrameGenerationSwapChainVersionVK version{};
    version.header.type = FFX_API_CREATE_CONTEXT_DESC_TYPE_FRAMEGENERATIONSWAPCHAIN_VERSION_VK;
    version.version = FFX_FRAMEGENERATION_SWAPCHAIN_VK_VERSION;
    ffxCreateContextDescFrameGenerationSwapChainVK desc{};
    desc.header.pNext = &version.header;
    desc.header.type=FFX_API_CREATE_CONTEXT_DESC_TYPE_FGSWAPCHAIN_VK;
    desc.physicalDevice=physical;desc.device=d;desc.swapchain=&swapchain;
    desc.allocator=const_cast<VkAllocationCallbacks *>(a);desc.createInfo=*ci;desc.createInfo.oldSwapchain=VK_NULL_HANDLE;
    desc.gameQueue={queues[0],family,submit_game};desc.asyncComputeQueue={queues[1],family,nullptr};
    desc.presentQueue={queues[2],family,nullptr};desc.imageAcquireQueue={queues[3],family,nullptr};
    auto r=create_context(&swap_context,&desc.header,nullptr);
    rt_log("framegen: FSR 3 swapchain creation result %u (%ux%u)",r,ci->imageExtent.width,ci->imageExtent.height);
    if (!r) {
        log_provider(&swap_context, "swapchain");
        functions = {};
        functions.header.type = FFX_API_QUERY_DESC_TYPE_FGSWAPCHAIN_FUNCTIONS_VK;
        r = query(&swap_context, &functions.header);
    }
    if (r) {
        if (swap_context) destroy_context(&swap_context, nullptr);
        swap_context = nullptr;
        swapchain = VK_NULL_HANDLE;
        swapchain_failed = true;
        fg_note_unavailable(FG_REASON_DEVICE);
        VkSwapchainCreateInfoKHR fallback = *ci;
        if (std::find(retired.begin(), retired.end(), ci->oldSwapchain) != retired.end())
            fallback.oldSwapchain = VK_NULL_HANDLE;
        return vkCreateSwapchainKHR(d, &fallback, a, out);
    }
    display_w=ci->imageExtent.width;display_h=ci->imageExtent.height;
    back_format=ci->imageFormat;
    *out=swapchain;return VK_SUCCESS;
}
void fsr_fg_destroy(VkDevice d,VkSwapchainKHR sc,const VkAllocationCallbacks *a){
    auto it=std::find(retired.begin(),retired.end(),sc);if(it!=retired.end()){retired.erase(it);return;}
    if(swap_context&&sc==swapchain){wait_presents();if(gen_context){destroy_context(&gen_context,nullptr);gen_context=nullptr;}
        destroy_context(&swap_context,nullptr);swap_context=nullptr;swapchain=VK_NULL_HANDLE;return;}
    vkDestroySwapchainKHR(d,sc,a);
}
VkResult fsr_fg_images(VkDevice d,VkSwapchainKHR sc,unsigned *n,VkImage *images){
    return swap_context?functions.pOutGetSwapchainImagesKHR(d,sc,n,images):vkGetSwapchainImagesKHR(d,sc,n,images);
}
VkResult fsr_fg_acquire(VkDevice d,VkSwapchainKHR sc,uint64_t t,VkSemaphore s,VkFence f,unsigned *idx){
    return swap_context?functions.pOutAcquireNextImageKHR(d,sc,t,s,f,idx):vkAcquireNextImageKHR(d,sc,t,s,f,idx);
}
VkResult fsr_fg_present(VkQueue q,const VkPresentInfoKHR *p){
    if(!swap_context)return vkQueuePresentKHR(q,p);
    VkResult r=functions.pOutQueuePresentKHR(q,p);
    static uint64_t previous;static unsigned count;
    uint64_t actual=functions.pOutGetLastPresentCountFFXAPI(swapchain);
    if(previous && actual>=previous)fg_record_presents(unsigned(actual-previous));
    if(++count%120==0)rt_log("framegen: FSR 3 %s, total presents %llu, recent presents %llu",enabled?"on":"off",
        static_cast<unsigned long long>(actual),static_cast<unsigned long long>(actual-previous));
    previous=actual;return r;
}
static FfxApiResource resource(VkImage image,VkFormat format,unsigned w,unsigned h){
    VkImageCreateInfo ci{};ci.imageType=VK_IMAGE_TYPE_2D;ci.format=format;ci.extent={w,h,1};ci.mipLevels=ci.arrayLayers=1;
    ci.usage=VK_IMAGE_USAGE_SAMPLED_BIT|VK_IMAGE_USAGE_TRANSFER_SRC_BIT|VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    return ffxApiGetResourceVK(reinterpret_cast<void *>(image),ffxApiGetImageResourceDescriptionVK(image,ci,0),FFX_API_RESOURCE_STATE_UNORDERED_ACCESS);
}
static ffxReturnCode_t interpolate(ffxDispatchDescFrameGeneration *p,void *){
    p->numGeneratedFrames=1;
    auto r=dispatch(&gen_context,&p->header);
    static unsigned frames;
    if(r||++frames==1||frames%120==0)rt_log("framegen: FSR 3 native interpolation result %u, generated calls %u",r,frames);
    return r;
}
void fsr_fg_frame(VkCommandBuffer cb,VkImage depth,VkImage motion,VkImage hudless,unsigned w,unsigned h,
                  unsigned ow,unsigned oh,const TemporalCamera *camera,float jx,float jy,uint64_t id,int on){
    if(!swap_context || generator_failed)return;
    if(!gen_context&&on){
        ffxCreateBackendVKDesc backend{};backend.header.type=FFX_API_CREATE_CONTEXT_DESC_TYPE_BACKEND_VK;
        backend.deviceContext = {device, physical, vkGetDeviceProcAddr};
        ffxCreateContextDescFrameGenerationVersion version{};
        version.header.type = FFX_API_CREATE_CONTEXT_DESC_TYPE_FRAMEGENERATION_VERSION;
        version.header.pNext = &backend.header;
        version.version = FFX_FRAMEGENERATION_VERSION;
        ffxCreateContextDescFrameGeneration d{};d.header.type=FFX_API_CREATE_CONTEXT_DESC_TYPE_FRAMEGENERATION;d.header.pNext=&version.header;
        d.displaySize={ow,oh};d.maxRenderSize={std::max(display_w,w),std::max(display_h,h)};d.backBufferFormat=ffxApiGetSurfaceFormatVK(back_format);
        auto r=create_context(&gen_context,&d.header,nullptr);rt_log("framegen: FSR 3 generator context result %u",r);
        if (r) {
            generator_failed = true;
            fg_note_unavailable(FG_REASON_DEVICE);
            return;
        }
        log_provider(&gen_context, "generator");
    }
    if(!gen_context)return;
    ffxConfigureDescFrameGeneration c{};c.header.type=FFX_API_CONFIGURE_DESC_TYPE_FRAMEGENERATION;
    c.swapChain=reinterpret_cast<void *>(swapchain);c.frameGenerationEnabled=on;c.allowAsyncWorkloads=false;
    c.frameGenerationCallback=interpolate;c.frameID=id;c.generationRect={0,0,int(ow),int(oh)};
    if(on)c.HUDLessColor=resource(hudless,VK_FORMAT_R8G8B8A8_UNORM,ow,oh);
    auto r=configure(&gen_context,&c.header);
    if(enabled!=bool(on)||r)rt_log("framegen: FSR 3 %s, configure result %u",on?"on":"off",r);
    enabled=on;if(!on||r)return;
    ffxDispatchDescFrameGenerationPrepareV2 d{};
    d.header.type = FFX_API_DISPATCH_DESC_TYPE_FRAMEGENERATION_PREPARE_V2;
    d.frameID=id;d.commandList=reinterpret_cast<void *>(cb);d.renderSize={w,h};d.jitterOffset={jx,jy};d.motionVectorScale={1,1};
    d.frameTimeDelta=16.67f;d.cameraNear=camera->near_plane;d.cameraFar=camera->far_plane;d.cameraFovAngleVertical=camera->fov;d.viewSpaceToMetersFactor=1;
    d.depth=resource(depth,VK_FORMAT_D32_SFLOAT,w,h);d.motionVectors=resource(motion,VK_FORMAT_R16G16_SFLOAT,w,h);
    const float *view = camera->inverse_view;
    for (int i = 0; i < 3; ++i) {
        d.cameraPosition[i] = view[4*i + 3];
        d.cameraRight[i] = view[4*i];
        d.cameraUp[i] = view[4*i + 1];
        d.cameraForward[i] = -view[4*i + 2];
    }
    r=dispatch(&gen_context,&d.header);
    static unsigned prepared;
    if(r||++prepared==1||prepared%120==0)rt_log("framegen: FSR 3 native prepare result %u, frames %u",r,prepared);
}
