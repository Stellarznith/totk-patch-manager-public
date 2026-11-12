// Streamline Vulkan integration. The SDK headers retain their MIT license in third_party/streamline.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <cstdio>
#include <cstring>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <mutex>
#include <condition_variable>
#define FRAMEGEN_IMPLEMENTATION
#include "third_party/streamline/sl.h"
#include "third_party/streamline/sl_dlss_g.h"
#include "third_party/streamline/sl_reflex.h"
#include "third_party/streamline/sl_pcl.h"
extern "C" {
#include "cfg.h"
#include "host.h"
#include "vk.h"
#include "framegen.h"
#include "temporal_camera.h"
#include "temporal_gpu.h"
#include "input_wsys.h"
#include "gfx_wsys.h"
#include "fsr_framegen.h"
}

static HMODULE module;
static PFN_vkGetInstanceProcAddr instance_proc;
static PFN_vkGetDeviceProcAddr device_proc;
static VkInstance instance;
static VkDevice device;
static PFun_slGetFeatureFunction *feature_function;
static PFun_slIsFeatureSupported *supported;
static PFun_slDLSSGGetState *get_state;
static PFun_slDLSSGSetOptions *set_options;
static int maximum_multiplier;
static PFun_slGetNewFrameToken *new_token;
static PFun_slSetConstants *set_constants;
static PFun_slSetTagForFrame *set_tags;
static PFun_slReflexSetOptions *reflex_options;
static PFun_slReflexSleep *reflex_sleep;
static PFun_slPCLSetMarker *marker;
struct FgImage { VkImage image{}; VkImageView view{}; VmaAllocation memory{}; unsigned w{},h{}; VkFormat format{}; };
struct FgFrame {
    uint64_t id{};
    sl::FrameToken *token{};
    FgImage color,depth,motion,scaled;
    TemporalCamera camera{};
    float jx{},jy{};
    bool reset{},captured{},rendering{};
    VkSemaphore inputs_done{};
    uint64_t inputs_value{};
    bool pending_inputs{};
};
static FgFrame frames[3];
static uint64_t current_frame=1;
static std::mutex frame_lock;
static std::condition_variable inputs_ready;
static FgFrame *present_frame;
static sl::FrameToken *present_token;
static bool fg_on;
static bool hdr_blocked;
static int last_status;
// Why frame generation cannot run (FG_REASON_* in framegen.h): a device or driver that cannot follow the interpolation,
// or a setting that blocks it, is a different problem for the player than a missing runtime file.
static int unavailable_reason;
// Set once the driver stops answering inside a present or a capture: interpolation is switched off for the rest of the
// run rather than taking the whole process down, which is what the old rt_fatal calls did.
static bool gave_up;
// How long a frame may wait for the driver's input-completion fence before it is drawn without interpolation. A driver
// that never signals it used to block the render thread forever.
enum { FG_WAIT_MS = 100, FG_FENCE_NS = 1000000000ull };
// The driver stopped answering (no completion fence, a failing state query, a present that timed out): interpolation is
// switched off and the game keeps rendering its own frames. Ending the process from a present or a capture is what made
// frame generation look like a crash on the machines that hit it.
static void give_up(int reason,const char *why,unsigned code) {
    if(!gave_up)rt_log("framegen: %s (%u); frame generation off for this run, native rendering",why,code);
    gave_up=true;unavailable_reason=reason;
    if(set_options){sl::DLSSGOptions off{};off.mode=sl::DLSSGMode::eOff;off.numFramesToGenerate=0;set_options(sl::ViewportHandle(0),off);}
    fg_on=false;
}
static double render_rate,display_rate;
static std::atomic<uint64_t> last_generated_at;
enum { B_BEGIN_LOCK, B_REFLEX, B_CAPTURE_LOCK, B_READY, B_INPUT, B_COUNT };
static std::atomic<uint64_t> bench_ns[B_COUNT], bench_frames;
static uint64_t bench_start() {
    static const bool on=cfg_get("TOTK_TEMPORAL_BENCH")!=nullptr;
    return on?kern_now_ns():0;
}
static void bench_end(unsigned kind,uint64_t at) {
    if(at)bench_ns[kind].fetch_add(kern_now_ns()-at,std::memory_order_relaxed);
}
void fg_rates(double *render,double *display){*render=render_rate;*display=display_rate;}
void fg_record_presents(unsigned count){
    static uint64_t at,rendered,presented;
    uint64_t now=kern_now_ns();if(!at)at=now;
    rendered++;presented+=count;
    if(now-at>=1000000000ull){
        double seconds=(now-at)/1e9;render_rate=rendered/seconds;display_rate=presented/seconds;
        if(cfg_get("TOTK_TEMPORAL_BENCH"))rt_log("temporal bench: world %.3f s render %.3f fps display %.3f fps",frame_step_world_us()/1e6,render_rate,display_rate);
        if(uint64_t n=bench_frames.exchange(0,std::memory_order_relaxed)) {
            double ms[B_COUNT];for(unsigned i=0;i<B_COUNT;i++)ms[i]=bench_ns[i].exchange(0,std::memory_order_relaxed)/1e6/n;
            rt_log("framegen waits: world %.3f s begin lock %.3f ms Reflex %.3f ms capture lock %.3f ms inputs ready %.3f ms input fence %.3f ms (%llu frames)",
                   frame_step_world_us()/1e6,ms[0],ms[1],ms[2],ms[3],ms[4],(unsigned long long)n);
        }
        at=now;rendered=presented=0;
    }
    if(count>=2)last_generated_at=now;
}
int fg_status(void){
    const char *s=cfg_value("TOTK_FRAMEGEN");int desired=!strcmp(s,"dlss")?1:!strcmp(s,"fsr3")?2:0;
    int startup=fg_startup_backend();
    if(desired && hdr_blocked)return 5;
    if(desired!=startup)return 1;
    if(!desired)return 0;
    if(gave_up)return 2;
    if((desired==1&&!set_options)||(desired==2&&(!fsr_fg_available()||fsr_fg_failed())))return 2;
    if(last_status==5)return 5;
    if(last_status==6)return 6;
    uint64_t now=kern_now_ns();
    uint64_t generated=last_generated_at.load(std::memory_order_relaxed);
    return generated && now>=generated && now-generated<1500000000ull ? 3 : 4;
}
int fg_unavailable_reason(void) { return unavailable_reason; }
void fg_note_unavailable(int reason) { if(!unavailable_reason)unavailable_reason=reason; }
int fg_startup_backend(void) {
    static int selected=-1;
    if(selected<0){const char *s=cfg_value("TOTK_FRAMEGEN");selected=!strcmp(s,"dlss")?1:!strcmp(s,"fsr3")?2:0;
        if(selected && cfg_bool("TOTK_HDR")){selected=0;hdr_blocked=true;unavailable_reason=FG_REASON_HDR;}}
    return selected;
}
int fg_requested(void) { return fg_startup_backend()!=0; }
int fg_dlss_max_multiplier(void) { return maximum_multiplier; }
int fg_present_uses_private_queues(void) { return fsr_fg_available(); }
static void log_message(sl::LogType type, const char *message) {
    if (type != sl::LogType::eInfo) rt_log("framegen SL[%u]: %s", unsigned(type), message);
}
static void load(void) {
    static bool tried;
    if (tried || fg_startup_backend()!=1) return;
    tried = true;
    wchar_t path[32768];
    DWORD n = GetModuleFileNameW(nullptr,path,32768);
    while(n && path[n-1] != L'\\') --n;
    path[n] = 0;
    std::wstring directory(path);
    directory += L"framegen";
    std::wstring dll = directory + L"\\sl.interposer.dll";
    module = LoadLibraryExW(dll.c_str(),nullptr,LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR|LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
    if (!module) { rt_log("framegen: sl.interposer.dll could not be loaded (%lu); native rendering",GetLastError());
        unavailable_reason=FG_REASON_FILES; return; }
    auto init = reinterpret_cast<PFun_slInit *>(GetProcAddress(module,"slInit"));
    feature_function = reinterpret_cast<PFun_slGetFeatureFunction *>(GetProcAddress(module,"slGetFeatureFunction"));
    supported = reinterpret_cast<PFun_slIsFeatureSupported *>(GetProcAddress(module,"slIsFeatureSupported"));
    if (!init || !feature_function || !supported) { module = nullptr; unavailable_reason=FG_REASON_FILES; return; }
    std::wstring executable_directory(path);
    const wchar_t *paths[] = {directory.c_str(), executable_directory.c_str()};
    sl::Feature features[] = {sl::kFeatureDLSS_G,sl::kFeatureReflex,sl::kFeaturePCL};
    sl::Preferences p{};
    p.pathsToPlugins=paths; p.numPathsToPlugins=2; p.pathToLogsAndData=directory.c_str();
    p.showConsole=false; p.logMessageCallback=log_message;
    p.featuresToLoad=features; p.numFeaturesToLoad=3;
    p.renderAPI=sl::RenderAPI::eVulkan;
    p.flags=sl::PreferenceFlags::eDisableCLStateTracking|sl::PreferenceFlags::eUseManualHooking|sl::PreferenceFlags::eUseFrameBasedResourceTagging;
    p.engine=sl::EngineType::eCustom; p.engineVersion="TotK Recomp 0.3.4 PS5";
    p.projectId="81d82534-436c-48e0-aa47-0f14ee42a669";
    sl::Result r=init(p,sl::kSDKVersion);
    rt_log("framegen: slInit result %u",unsigned(r));
    if(r!=sl::Result::eOk) { module=nullptr; unavailable_reason=FG_REASON_FILES; return; }
    new_token=reinterpret_cast<PFun_slGetNewFrameToken *>(GetProcAddress(module,"slGetNewFrameToken"));
    set_constants=reinterpret_cast<PFun_slSetConstants *>(GetProcAddress(module,"slSetConstants"));
    set_tags=reinterpret_cast<PFun_slSetTagForFrame *>(GetProcAddress(module,"slSetTagForFrame"));
    instance_proc=reinterpret_cast<PFN_vkGetInstanceProcAddr>(GetProcAddress(module,"vkGetInstanceProcAddr"));
    device_proc=reinterpret_cast<PFN_vkGetDeviceProcAddr>(GetProcAddress(module,"vkGetDeviceProcAddr"));
}
PFN_vkVoidFunction fg_instance_proc(VkInstance i,const char *name) {
    return instance_proc ? instance_proc(i,name) : vkGetInstanceProcAddr(i,name);
}
VkResult fg_vkCreateInstance(const VkInstanceCreateInfo *ci,const VkAllocationCallbacks *a,VkInstance *out) {
    load();
    auto fn=reinterpret_cast<PFN_vkCreateInstance>(fg_instance_proc(nullptr,"vkCreateInstance"));
    VkResult r=fn(ci,a,out); if(r==VK_SUCCESS)instance=*out; return r;
}
VkResult fg_vkCreateDevice(VkPhysicalDevice physical,const VkDeviceCreateInfo *ci,const VkAllocationCallbacks *a,VkDevice *out) {
    auto fn=reinterpret_cast<PFN_vkCreateDevice>(fg_instance_proc(instance,"vkCreateDevice"));
    VkResult r=fn(physical,ci,a,out);
    if(r==VK_SUCCESS)device=*out; return r;
}
VkResult fg_vkEnumeratePhysicalDevices(VkInstance i,uint32_t *n,VkPhysicalDevice *devices) {
    auto fn=reinterpret_cast<PFN_vkEnumeratePhysicalDevices>(fg_instance_proc(i,"vkEnumeratePhysicalDevices"));
    return fn(i,n,devices);
}
void fg_device_ready(VkInstance i,VkPhysicalDevice physical,VkDevice d) {
    instance=i; device=d;
    if(!module)return;
    sl::AdapterInfo adapter{}; adapter.vkPhysicalDevice=physical;
    sl::Result r=supported(sl::kFeatureDLSS_G,adapter);
    rt_log("framegen: device DLSS FG support result %u",unsigned(r));
    if(r!=sl::Result::eOk){unavailable_reason=FG_REASON_DEVICE;return;}
    void *fn=nullptr;
    feature_function(sl::kFeatureDLSS_G,"slDLSSGGetState",fn); get_state=reinterpret_cast<PFun_slDLSSGGetState *>(fn);
    fn=nullptr; feature_function(sl::kFeatureDLSS_G,"slDLSSGSetOptions",fn); set_options=reinterpret_cast<PFun_slDLSSGSetOptions *>(fn);
    fn=nullptr; feature_function(sl::kFeatureReflex,"slReflexSetOptions",fn);reflex_options=reinterpret_cast<PFun_slReflexSetOptions *>(fn);
    fn=nullptr; feature_function(sl::kFeatureReflex,"slReflexSleep",fn);reflex_sleep=reinterpret_cast<PFun_slReflexSleep *>(fn);
    fn=nullptr; feature_function(sl::kFeaturePCL,"slPCLSetMarker",fn);marker=reinterpret_cast<PFun_slPCLSetMarker *>(fn);
    if(reflex_options){sl::ReflexOptions options{};options.mode=sl::ReflexMode::eLowLatency;reflex_options(options);}
    if(!get_state || !set_options){unavailable_reason=FG_REASON_FILES;return;}
    sl::DLSSGState state{};
    r=get_state(sl::ViewportHandle(0),state,nullptr);
    if(r==sl::Result::eOk)maximum_multiplier=int(state.numFramesToGenerateMax)+1;
    rt_log("framegen: native DLSS FG state result %u, maximum multiplier %d, status %u",unsigned(r),maximum_multiplier,unsigned(state.status));
}
#define DEVICE_FN(name) auto fn=device_proc ? reinterpret_cast<PFN_##name>(device_proc(device,#name)) : name
VkResult fg_vkCreateSwapchainKHR(VkDevice d,const VkSwapchainCreateInfoKHR *ci,const VkAllocationCallbacks *a,VkSwapchainKHR *out) {
    if(fsr_fg_requested())return fsr_fg_create(d,ci,a,out);
    DEVICE_FN(vkCreateSwapchainKHR); return fn(d,ci,a,out);
}
void fg_vkDestroySwapchainKHR(VkDevice d,VkSwapchainKHR sc,const VkAllocationCallbacks *a) {
    if(fsr_fg_requested()){fsr_fg_destroy(d,sc,a);return;}
    DEVICE_FN(vkDestroySwapchainKHR); fn(d,sc,a);
    std::lock_guard<std::mutex> lock(frame_lock);
    for(auto &f:frames){f.inputs_done=VK_NULL_HANDLE;f.inputs_value=0;f.pending_inputs=false;}
    inputs_ready.notify_all();
}
VkResult fg_vkGetSwapchainImagesKHR(VkDevice d,VkSwapchainKHR sc,uint32_t *n,VkImage *images) {
    if(fsr_fg_requested())return fsr_fg_images(d,sc,n,images);
    DEVICE_FN(vkGetSwapchainImagesKHR);
    VkResult r=fn(d,sc,n,images); return r;
}
VkResult fg_vkAcquireNextImageKHR(VkDevice d,VkSwapchainKHR sc,uint64_t t,VkSemaphore s,VkFence f,uint32_t *idx) {
    if(fsr_fg_requested())return fsr_fg_acquire(d,sc,t,s,f,idx);
    DEVICE_FN(vkAcquireNextImageKHR); return fn(d,sc,t,s,f,idx);
}
VkResult fg_vkQueuePresentKHR(VkQueue q,const VkPresentInfoKHR *pi) {
    if(fsr_fg_requested())return fsr_fg_present(q,pi);
    DEVICE_FN(vkQueuePresentKHR);
    if(marker && present_token){marker(sl::PCLMarker::eRenderSubmitEnd,*present_token);marker(sl::PCLMarker::ePresentStart,*present_token);}
    VkResult r=fn(q,pi);
    if(marker && present_token)marker(sl::PCLMarker::ePresentEnd,*present_token);
    if(get_state && present_token) {
        sl::DLSSGState state{};sl::Result sr=get_state(sl::ViewportHandle(0),state,nullptr);
        if(sr==sl::Result::eOk)fg_record_presents(state.numFramesActuallyPresented);
        if(present_frame){
            int fence_missing=0,state_failed=0;
            {
                std::lock_guard<std::mutex> lock(frame_lock);
                if(present_frame->pending_inputs){
                    if(sr!=sl::Result::eOk){
                        // The state query failed: this frame's fence is unknown, so release the waiting capture and let
                        // the next frames run without interpolation instead of ending the process here.
                        state_failed=1;
                        present_frame->inputs_done=VK_NULL_HANDLE;present_frame->inputs_value=0;
                    }else{
                        // Streamline Vulkan::createFence returns a native timeline VkSemaphore.
                        present_frame->inputs_done=reinterpret_cast<VkSemaphore>(state.inputsProcessingCompletionFence);
                        present_frame->inputs_value=state.lastPresentInputsProcessingCompletionFenceValue;
                        if(state.numFramesActuallyPresented>=2 && !present_frame->inputs_done)fence_missing=1;
                    }
                    present_frame->pending_inputs=false;inputs_ready.notify_all();
                }
            }
            // The driver call in give_up happens outside frame_lock: a present that waits on the render thread would
            // otherwise deadlock against this one.
            if(state_failed){give_up(FG_REASON_FENCE,"input completion state failed",unsigned(sr));present_token=nullptr;return r;}
            if(fence_missing)give_up(FG_REASON_FENCE,"missing input completion fence",state.numFramesActuallyPresented);
        }
        static unsigned count;
        if(++count%120==0)rt_log("framegen: DLSS FG %s, state result %u status %u, actually presented %u, max %u",
            fg_on?"on":"off",unsigned(sr),unsigned(state.status),state.numFramesActuallyPresented,state.numFramesToGenerateMax+1);
    }
    present_token=nullptr;
    return r;
}
VkResult fg_vkDeviceWaitIdle(VkDevice d) { DEVICE_FN(vkDeviceWaitIdle); return fn(d); }
void fg_vkDestroySurfaceKHR(VkInstance i,VkSurfaceKHR s,const VkAllocationCallbacks *a) {
    auto fn=reinterpret_cast<PFN_vkDestroySurfaceKHR>(fg_instance_proc(i,"vkDestroySurfaceKHR")); fn(i,s,a);
}

static void barrier(VkCommandBuffer cb) {
    VkMemoryBarrier2 b{VK_STRUCTURE_TYPE_MEMORY_BARRIER_2};
    b.srcStageMask=b.dstStageMask=VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT;
    b.srcAccessMask=VK_ACCESS_2_MEMORY_WRITE_BIT;b.dstAccessMask=VK_ACCESS_2_MEMORY_READ_BIT|VK_ACCESS_2_MEMORY_WRITE_BIT;
    VkDependencyInfo d{VK_STRUCTURE_TYPE_DEPENDENCY_INFO};d.memoryBarrierCount=1;d.pMemoryBarriers=&b;vkCmdPipelineBarrier2(cb,&d);
}
static bool image(FgImage &i,unsigned w,unsigned h,VkFormat format,VkCommandBuffer cb) {
    if(i.image && i.w==w && i.h==h)return true;
    const VkCtx *c=vk_ctx();
    if(i.image){fg_vkDeviceWaitIdle(c->device);vkDestroyImageView(c->device,i.view,nullptr);vmaDestroyImage(c->vma,i.image,i.memory);i={};}
    bool color=format==VK_FORMAT_R8G8B8A8_SRGB;
    VkImageCreateInfo ci{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};ci.flags=color?VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT:0;
    ci.imageType=VK_IMAGE_TYPE_2D;ci.format=format;ci.extent={w,h,1};ci.mipLevels=ci.arrayLayers=1;
    ci.samples=VK_SAMPLE_COUNT_1_BIT;ci.tiling=VK_IMAGE_TILING_OPTIMAL;
    ci.usage=VK_IMAGE_USAGE_TRANSFER_DST_BIT|VK_IMAGE_USAGE_TRANSFER_SRC_BIT|VK_IMAGE_USAGE_SAMPLED_BIT;
    VmaAllocationCreateInfo ai{};ai.usage=VMA_MEMORY_USAGE_AUTO_PREFER_DEVICE;
    if(vmaCreateImage(c->vma,&ci,&ai,&i.image,&i.memory,nullptr)!=VK_SUCCESS)return false;
    i.w=w;i.h=h;i.format=color?VK_FORMAT_R8G8B8A8_UNORM:format;
    VkImageAspectFlags aspect=format==VK_FORMAT_D32_SFLOAT?VK_IMAGE_ASPECT_DEPTH_BIT:VK_IMAGE_ASPECT_COLOR_BIT;
    VkImageViewCreateInfo vi{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};vi.image=i.image;vi.viewType=VK_IMAGE_VIEW_TYPE_2D;vi.format=i.format;
    vi.subresourceRange={aspect,0,1,0,1};
    if(vkCreateImageView(c->device,&vi,nullptr,&i.view)!=VK_SUCCESS)return false;
    VkImageMemoryBarrier2 b{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2};b.oldLayout=VK_IMAGE_LAYOUT_UNDEFINED;b.newLayout=VK_IMAGE_LAYOUT_GENERAL;
    b.dstStageMask=VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT;b.dstAccessMask=VK_ACCESS_2_MEMORY_READ_BIT|VK_ACCESS_2_MEMORY_WRITE_BIT;
    b.srcQueueFamilyIndex=b.dstQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED;b.image=i.image;b.subresourceRange={aspect,0,1,0,1};
    VkDependencyInfo d{VK_STRUCTURE_TYPE_DEPENDENCY_INFO};d.imageMemoryBarrierCount=1;d.pImageMemoryBarriers=&b;vkCmdPipelineBarrier2(cb,&d);
    return true;
}
static sl::Resource resource(const FgImage &i) {
    sl::Resource r(sl::ResourceType::eTex2d,reinterpret_cast<void *>(i.image),nullptr,reinterpret_cast<void *>(i.view),VK_IMAGE_LAYOUT_GENERAL);
    r.width=i.w;r.height=i.h;r.nativeFormat=i.format;r.mipLevels=r.arrayLayers=1;r.flags=0;
    r.usage=VK_IMAGE_USAGE_SAMPLED_BIT|VK_IMAGE_USAGE_TRANSFER_SRC_BIT|VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    return r;
}
void fg_begin_frame(void) {
    if(!new_token || !set_options || !fg_requested())return;
    uint64_t bt=bench_start();
    std::lock_guard<std::mutex> lock(frame_lock);
    bench_end(B_BEGIN_LOCK,bt);
    FgFrame &f=frames[current_frame%3];
    if(f.id==current_frame && f.token)return;
    f.id=current_frame;f.token=nullptr;f.captured=f.rendering=false;
    uint32_t index=uint32_t(current_frame);
    if(new_token(f.token,&index)!=sl::Result::eOk)return;
    bt=bench_start();if(reflex_sleep)reflex_sleep(*f.token);bench_end(B_REFLEX,bt);
    if(bt)bench_frames.fetch_add(1,std::memory_order_relaxed);
    if(marker)marker(sl::PCLMarker::eSimulationStart,*f.token);
}
void fg_render_begin(void) {
    if(!module || !fg_requested())return;
    fg_begin_frame();
    FgFrame &f=frames[current_frame%3];
    if(f.token && !f.rendering){
        if(marker){marker(sl::PCLMarker::eSimulationEnd,*f.token);marker(sl::PCLMarker::eRenderSubmitStart,*f.token);}
        f.rendering=true;
    }
}
void fg_capture(VkCommandBuffer cb,VkImage color,unsigned ow,unsigned oh,VkImage depth,unsigned level,unsigned layer,
                VkImage motion,unsigned w,unsigned h,const float rows[64],float jx,float jy,int reset) {
    if(!fg_requested() || gave_up || (!set_options && !fsr_fg_available()))return;
    FgFrame &f=frames[current_frame%3];
    if(f.id!=current_frame){f.id=current_frame;f.token=nullptr;f.captured=f.rendering=false;}
    if(module){
        uint64_t bt=bench_start();
        std::unique_lock<std::mutex> lock(frame_lock);
        bench_end(B_CAPTURE_LOCK,bt);bt=bench_start();
        // The present that clears pending_inputs can be dropped by the driver (a lost surface, a disabled swapchain): the
        // frame is then drawn without waiting for that fence instead of stalling the render thread for the whole run.
        if(!inputs_ready.wait_for(lock,std::chrono::milliseconds(FG_WAIT_MS),[&f]{return !f.pending_inputs;})){
            f.inputs_done=VK_NULL_HANDLE;f.inputs_value=0;
            f.pending_inputs=false;
            bench_end(B_READY,bt);
            static unsigned stalls;
            if(stalls++<8)rt_log("framegen: inputs not completed in %d ms; drawing this frame without interpolation",FG_WAIT_MS);
            lock.unlock();
            give_up(FG_REASON_FENCE,"inputs never completed",FG_WAIT_MS);
            return;
        }
        bench_end(B_READY,bt);
        if(f.inputs_done && f.inputs_value){
            VkSemaphoreWaitInfo wait{VK_STRUCTURE_TYPE_SEMAPHORE_WAIT_INFO};
            wait.semaphoreCount=1;wait.pSemaphores=&f.inputs_done;wait.pValues=&f.inputs_value;
            bt=bench_start();VkResult result=vkWaitSemaphores(device,&wait,FG_FENCE_NS);bench_end(B_INPUT,bt);
            if(result!=VK_SUCCESS){
                lock.unlock();
                give_up(FG_REASON_FENCE,"input completion wait failed",unsigned(result));
                return;
            }
        }
        f.inputs_done=VK_NULL_HANDLE;f.inputs_value=0;
    }
    if(!temporal_camera(rows,&f.camera))return;
    int drawable_w=0,drawable_h=0;
    wsys_client_size(&drawable_w,&drawable_h);
    // Capture at the actual presentation size: a window below the game's output previously kept both a full-size
    // copy and a second scaled copy in every ring slot. The resize fallback in present_color remains available.
    unsigned cw=drawable_w>0?unsigned(drawable_w):ow,ch=drawable_h>0?unsigned(drawable_h):oh;
    // gfx_present.c uses UNORM: the game's display values must not get another sRGB encode.
    // The HUDless image must match that transfer function for the provider's UI extraction.
    if(!image(f.color,cw,ch,VK_FORMAT_R8G8B8A8_UNORM,cb)||!image(f.depth,w,h,VK_FORMAT_D32_SFLOAT,cb)||
       !image(f.motion,w,h,VK_FORMAT_R16G16_SFLOAT,cb))return;
    int gpu_timer=temporal_gpu_begin(cb,TEMP_GPU_CAPTURE);
    barrier(cb);
    VkImageBlit blit{};blit.srcSubresource=blit.dstSubresource={VK_IMAGE_ASPECT_COLOR_BIT,0,0,1};
    blit.srcOffsets[1]={int(ow),int(oh),1};blit.dstOffsets[1]={int(cw),int(ch),1};
    vkCmdBlitImage(cb,color,VK_IMAGE_LAYOUT_GENERAL,f.color.image,VK_IMAGE_LAYOUT_GENERAL,1,&blit,cw==ow && ch==oh?VK_FILTER_NEAREST:VK_FILTER_LINEAR);
    VkImageCopy copy{};copy.srcSubresource={VK_IMAGE_ASPECT_DEPTH_BIT,level,layer,1};copy.dstSubresource={VK_IMAGE_ASPECT_DEPTH_BIT,0,0,1};copy.extent={w,h,1};
    vkCmdCopyImage(cb,depth,VK_IMAGE_LAYOUT_GENERAL,f.depth.image,VK_IMAGE_LAYOUT_GENERAL,1,&copy);
    copy.srcSubresource=copy.dstSubresource={VK_IMAGE_ASPECT_COLOR_BIT,0,0,1};
    vkCmdCopyImage(cb,motion,VK_IMAGE_LAYOUT_GENERAL,f.motion.image,VK_IMAGE_LAYOUT_GENERAL,1,&copy);
    barrier(cb);temporal_gpu_end(cb,TEMP_GPU_CAPTURE,gpu_timer);f.jx=jx;f.jy=jy;f.reset=reset;f.captured=true;
}
uint64_t fg_end_game_frame(void) {
    if((!module && !fsr_fg_available()) || !fg_requested())return 0;
    std::lock_guard<std::mutex> lock(frame_lock);
    return current_frame++;
}
void fg_prepare_present(uint64_t id) {
    present_frame=id && frames[id%3].id==id?&frames[id%3]:nullptr;
    present_token=present_frame?present_frame->token:nullptr;
}
static const FgImage *present_color(FgFrame &f,unsigned w,unsigned h,VkCommandBuffer cb) {
    if(f.color.w==w && f.color.h==h)return &f.color;
    if(!image(f.scaled,w,h,VK_FORMAT_R8G8B8A8_UNORM,cb))return nullptr;
    int timer=temporal_gpu_begin(cb,TEMP_GPU_PRESENT);
    VkImageBlit b{};b.srcSubresource=b.dstSubresource={VK_IMAGE_ASPECT_COLOR_BIT,0,0,1};
    b.srcOffsets[1]={int(f.color.w),int(f.color.h),1};b.dstOffsets[1]={int(w),int(h),1};
    vkCmdBlitImage(cb,f.color.image,VK_IMAGE_LAYOUT_GENERAL,f.scaled.image,VK_IMAGE_LAYOUT_GENERAL,1,&b,VK_FILTER_LINEAR);
    barrier(cb);temporal_gpu_end(cb,TEMP_GPU_PRESENT,timer);
    return &f.scaled;
}
static sl::float4x4 transpose(const float *m) {
    sl::float4x4 r;for(int y=0;y<4;y++)for(int x=0;x<4;x++)reinterpret_cast<float *>(&r[y])[x]=m[x*4+y];return r;
}
void fg_tag_present(VkCommandBuffer cb,VkImage backbuffer,VkImageView view,VkFormat format,unsigned w,unsigned h,int overlay,int hdr) {
    if(fsr_fg_available() && present_frame) {
        FgFrame &f=*present_frame;
        bool on=f.captured && !overlay && !hdr;
        last_status=(overlay||hdr)?5:4;
        const FgImage *color=on?present_color(f,w,h,cb):nullptr;
        if(on && !color)on=false;
        fsr_fg_frame(cb,f.depth.image,f.motion.image,color?color->image:VK_NULL_HANDLE,f.depth.w,f.depth.h,w,h,&f.camera,f.jx,f.jy,f.id,on);
        barrier(cb);return;
    }
    if(!set_options || !present_frame || !present_token)return;
    FgFrame &f=*present_frame;
    bool on=f.captured && !overlay && !hdr && ikb_foreground();
    bool changed=on!=fg_on;
    if(changed && on){vk_queue_lock();fg_vkDeviceWaitIdle(device);vk_queue_unlock();}
    last_status=(overlay||hdr)?5:f.captured&&!ikb_foreground()?6:4;
    sl::DLSSGOptions options{};options.mode=on?sl::DLSSGMode::eOn:sl::DLSSGMode::eOff;
    options.numFramesToGenerate=uint32_t(std::max(1,std::min(cfg_int("TOTK_FRAMEGEN_MULTIPLIER"),maximum_multiplier)-1));
    options.colorWidth=w;options.colorHeight=h;options.colorBufferFormat=format;
    options.mvecDepthWidth=f.depth.w;options.mvecDepthHeight=f.depth.h;
    options.depthBufferFormat=VK_FORMAT_D32_SFLOAT;options.mvecBufferFormat=VK_FORMAT_R16G16_SFLOAT;options.hudLessBufferFormat=VK_FORMAT_R8G8B8A8_UNORM;
    sl::Result r=set_options(sl::ViewportHandle(0),options);
    if(changed && !on){vk_queue_lock();fg_vkDeviceWaitIdle(device);vk_queue_unlock();}
    if(fg_on!=on || r!=sl::Result::eOk){rt_log("framegen: native DLSS FG %s, options result %u",on?"on":"off",unsigned(r));fg_on=on;}
    // eWarnOutOfVRAM (39) is what the machines that "crash with frame generation on" log: the driver cannot allocate the
    // interpolation targets. Keeping the request on repeats it every frame, so interpolation is dropped once it is clear
    // the device will not take it, and the menu then says why.
    static unsigned starved;
    if(r==sl::Result::eWarnOutOfVRAM){
        if(++starved>=8){give_up(FG_REASON_VRAM,"out of VRAM for frame generation",unsigned(r));return;}
    }else if(r==sl::Result::eOk){
        starved=0;
    }
    if(!on || r!=sl::Result::eOk)return;
    const FgImage *color=present_color(f,w,h,cb);
    if(!color)return;
    sl::Constants constants{};
    constants.cameraViewToClip=transpose(f.camera.projection);constants.clipToCameraView=transpose(f.camera.inverse_projection);
    static float prev_vp[16];static bool prev_valid;
    float previous[16],forward[16];
    temporal_multiply(prev_valid?prev_vp:f.camera.view_projection,f.camera.inverse_view_projection,previous);
    temporal_inverse(previous,forward);
    constants.clipToPrevClip=transpose(previous);constants.prevClipToClip=transpose(forward);
    memcpy(prev_vp,f.camera.view_projection,sizeof prev_vp);prev_valid=true;
    const float *iv=f.camera.inverse_view;
    constants.cameraPos={iv[3],iv[7],iv[11]};constants.cameraRight={iv[0],iv[4],iv[8]};
    constants.cameraUp={iv[1],iv[5],iv[9]};constants.cameraFwd={-iv[2],-iv[6],-iv[10]};
    constants.cameraNear=f.camera.near_plane;constants.cameraFar=f.camera.far_plane;constants.cameraFOV=f.camera.fov;constants.cameraAspectRatio=f.camera.aspect;
    constants.jitterOffset={f.jx,f.jy};constants.mvecScale={1.0f/f.motion.w,1.0f/f.motion.h};
    constants.cameraPinholeOffset={0,0};
    constants.depthInverted=sl::Boolean::eFalse;constants.cameraMotionIncluded=sl::Boolean::eTrue;
    constants.motionVectors3D=sl::Boolean::eFalse;constants.reset=(f.reset||changed)?sl::Boolean::eTrue:sl::Boolean::eFalse;
    r=set_constants(constants,*present_token,sl::ViewportHandle(0));
    sl::Resource z=resource(f.depth),mv=resource(f.motion),hudless=resource(*color);
    sl::Resource back(sl::ResourceType::eTex2d,reinterpret_cast<void *>(backbuffer),nullptr,reinterpret_cast<void *>(view),VK_IMAGE_LAYOUT_GENERAL);
    back.width=w;back.height=h;back.nativeFormat=format;back.mipLevels=back.arrayLayers=1;back.flags=0;
    sl::Extent depth_extent{0,0,f.depth.w,f.depth.h}, color_extent{0,0,w,h};
    sl::ResourceTag tags[]={
        {&z,sl::kBufferTypeDepth,sl::ResourceLifecycle::eValidUntilPresent,&depth_extent},
        {&mv,sl::kBufferTypeMotionVectors,sl::ResourceLifecycle::eValidUntilPresent,&depth_extent},
        {&hudless,sl::kBufferTypeHUDLessColor,sl::ResourceLifecycle::eValidUntilPresent,&color_extent},
        {nullptr,sl::kBufferTypeUIColorAndAlpha,sl::ResourceLifecycle::eValidUntilPresent},
        {&back,sl::kBufferTypeBackbuffer,sl::ResourceLifecycle::eValidUntilPresent,&color_extent}};
    sl::Result tr=set_tags(*present_token,sl::ViewportHandle(0),tags,5,reinterpret_cast<sl::CommandBuffer *>(cb));
    // Only a frame that is still the one being presented may be marked: a slot recycled by a later game frame would
    // otherwise wait for a fence this present never sets.
    if(tr==sl::Result::eOk && present_frame==&f && f.id==current_frame){
        std::lock_guard<std::mutex> lock(frame_lock);f.pending_inputs=true;
    }
    static unsigned errors;
    if((r!=sl::Result::eOk||tr!=sl::Result::eOk)&&errors++<8)rt_log("framegen: constants result %u, tags result %u",unsigned(r),unsigned(tr));
}
