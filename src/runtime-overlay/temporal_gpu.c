// Each kind has one recording thread and a ring large enough to avoid waiting for results.
#include "cfg.h"
#include "host.h"
#include "vk.h"
#include "temporal_gpu.h"

enum { SLOTS = 64 };
static struct { VkQueryPool pool; unsigned next, samples; double sum, maximum; } clocks[TEMP_GPU_COUNT];
static const char *names[] = {"reconstruction", "framegen capture", "framegen present copy"};
int temporal_gpu_begin(VkCommandBuffer cb, int kind) {
    if (!cfg_get("TOTK_TEMPORAL_BENCH") || kind < 0 || kind >= TEMP_GPU_COUNT) return -1;
    const VkCtx *c = vk_ctx();
    if (!c->props.limits.timestampComputeAndGraphics) return -1;
    if (!clocks[kind].pool) {
        VkQueryPoolCreateInfo ci = {.sType=VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO,.queryType=VK_QUERY_TYPE_TIMESTAMP,.queryCount=SLOTS*2};
        if (vkCreateQueryPool(c->device,&ci,NULL,&clocks[kind].pool)!=VK_SUCCESS) return -1;
    }
    unsigned slot=clocks[kind].next++ % SLOTS;
    if (clocks[kind].next > SLOTS) {
        uint64_t data[2][2] = {{0}};
        VkResult r=vkGetQueryPoolResults(c->device,clocks[kind].pool,slot*2,2,sizeof data,data,sizeof data[0],
                                        VK_QUERY_RESULT_64_BIT|VK_QUERY_RESULT_WITH_AVAILABILITY_BIT);
        if (r==VK_SUCCESS && data[0][1] && data[1][1]) {
            double ms=(data[1][0]-data[0][0])*c->props.limits.timestampPeriod/1e6;
            clocks[kind].sum+=ms;
            if(ms>clocks[kind].maximum)clocks[kind].maximum=ms;
            if(++clocks[kind].samples==120) {
                rt_log("temporal GPU: %s avg %.4f ms max %.4f ms, world %.3f s",names[kind],clocks[kind].sum/120,
                       clocks[kind].maximum,frame_step_world_us()/1e6);
                clocks[kind].samples=0;clocks[kind].sum=clocks[kind].maximum=0;
            }
        }
    }
    vkCmdResetQueryPool(cb,clocks[kind].pool,slot*2,2);
    vkCmdWriteTimestamp2(cb,VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,clocks[kind].pool,slot*2);
    return (int)slot;
}
void temporal_gpu_end(VkCommandBuffer cb, int kind, int slot) {
    if(slot>=0)vkCmdWriteTimestamp2(cb,VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,clocks[kind].pool,(unsigned)slot*2+1);
}
