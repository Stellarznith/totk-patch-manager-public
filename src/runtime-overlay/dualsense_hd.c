// USB DualSense audio haptics, using SDL's WASAPI path on the controller's own endpoint.
// Protocol behaviour: SDL release-3.4.18 SDL_hidapi_ps5.c, UpdateEffects and SendJoystickEffect.
// No game audio is analysed or routed here; only nn::hid vibration parameters are synthesized.
#include <stdlib.h>
#include <string.h>
#include <SDL3/SDL.h>
#include "host.h"
#include "input.h"
#include "cfg.h"
#include "dualsense_hd.h"

static SDL_AudioStream *stream;
static SDL_Mutex *mutex;
static HdSynth synth;
static HdVibration values[2];
static uint64_t updated[2];
static float gain;
static SDL_JoystickID controller;
static uint64_t next_try;
static SDL_AtomicInt output_failed;
static SDL_AtomicInt active;
static SDL_AtomicInt pcm_nonzero;
static int first_wave;

static void SDLCALL audio_get(void *unused, SDL_AudioStream *s, int additional, int total) {
    (void)unused; (void)total;
    HdVibration v[2];
    uint64_t at[2], now = SDL_GetTicksNS();
    float g;
    SDL_LockMutex(mutex);
    memcpy(v, values, sizeof v);
    memcpy(at, updated, sizeof at);
    g = (float)cfg_double("TOTK_DUALSENSE_HD_STRENGTH") / 100.0f;
    SDL_UnlockMutex(mutex);
    const char *rumble = cfg_get("TOTK_RUMBLE");
    if (!cfg_bool("TOTK_DUALSENSE_HD") || (rumble && !strcmp(rumble, "0"))) g = 0;
    for (int side = 0; side < 2; side++)
        if (!at[side] || now - at[side] > 250000000ull) v[side].amp_low = v[side].amp_high = 0;
    int frames = (additional + 15) / 16;
    while (frames > 0) {
        float pcm[256 * 4];
        int count = frames < 256 ? frames : 256;
        hd_synthesize(&synth, v, g, pcm, count);
        for (int i = 0; i < count; i++)
            if (pcm[4 * i + 2] > 0.00001f || pcm[4 * i + 2] < -0.00001f ||
                pcm[4 * i + 3] > 0.00001f || pcm[4 * i + 3] < -0.00001f) {
                SDL_SetAtomicInt(&pcm_nonzero, 1);
                break;
            }
        if (!SDL_PutAudioStreamData(s, pcm, count * 16)) { SDL_SetAtomicInt(&output_failed, 1); break; }
        frames -= count;
    }
}

static void close_stream(void) {
    SDL_SetAtomicInt(&active, 0);
    if (stream) { SDL_DestroyAudioStream(stream); stream = NULL; }
    controller = 0;
    memset(&synth, 0, sizeof synth);
}

static int open_stream(SDL_JoystickID id) {
    if (!SDL_InitSubSystem(SDL_INIT_AUDIO)) {
        rt_log("DualSense HD: audio init failed: %s; ordinary rumble fallback", SDL_GetError());
        return 0;
    }
    int n = 0, matches = 0;
    SDL_AudioDeviceID chosen = 0;
    SDL_AudioDeviceID *ids = SDL_GetAudioPlaybackDevices(&n);
    for (int i = 0; ids && i < n; i++) {
        const char *name = SDL_GetAudioDeviceName(ids[i]);
        SDL_AudioSpec actual;
        if (name && (strstr(name, "DualSense") || strstr(name, "Wireless Controller")) &&
            SDL_GetAudioDeviceFormat(ids[i], &actual, NULL) && actual.channels == 4 && actual.freq == 48000) {
            chosen = ids[i];
            matches++;
        }
    }
    SDL_free(ids);
    if (matches != 1) {
        rt_log("DualSense HD: need one unambiguous 4-channel 48000 Hz controller endpoint, found %d; ordinary rumble fallback", matches);
        SDL_QuitSubSystem(SDL_INIT_AUDIO);
        return 0;
    }
    SDL_AudioSpec spec = {.format = SDL_AUDIO_F32, .channels = 4, .freq = 48000};
    if (!mutex) mutex = SDL_CreateMutex();
    stream = mutex ? SDL_OpenAudioDeviceStream(chosen, &spec, audio_get, NULL) : NULL;
    if (stream) {
        SDL_AudioSpec actual;
        if (!SDL_GetAudioDeviceFormat(SDL_GetAudioStreamDevice(stream), &actual, NULL) || actual.channels != 4 || actual.freq != 48000)
            close_stream();
    }
    if (!stream || input_dualsense_audio_mode(1) != id || !SDL_ResumeAudioStreamDevice(stream)) {
        rt_log("DualSense HD: cannot start haptic stream: %s; ordinary rumble fallback", SDL_GetError());
        close_stream();
        SDL_QuitSubSystem(SDL_INIT_AUDIO);
        return 0;
    }
    controller = id;
    SDL_SetAtomicInt(&active, 1);
    SDL_SetAtomicInt(&output_failed, 0);
    rt_log("DualSense HD: active USB audio haptics on %s; 48000 Hz, 4 channels, haptics on 3/4, front 1/2 silent",
           SDL_GetAudioDeviceName(chosen));
    return 1;
}

int dualsense_hd_update(const HdVibration v[2], const uint64_t at[2], int allowed, float strength) {
    (void)strength;
    int enabled = cfg_bool("TOTK_DUALSENSE_HD");
    SDL_JoystickID id = enabled ? input_dualsense_audio_mode(0) : 0;
    if (stream && (!id || id != controller || SDL_GetAtomicInt(&output_failed) ||
                   SDL_AudioDevicePaused(SDL_GetAudioStreamDevice(stream)))) {
        close_stream();
        SDL_QuitSubSystem(SDL_INIT_AUDIO);
        rt_log("DualSense HD: stream stopped; ordinary rumble fallback until USB is ready");
    }
    if (!id) return 0;
    uint64_t now = SDL_GetTicksNS();
    if (!stream) {
        if (now < next_try) return 0;
        next_try = now + 3000000000ull;
        if (!open_stream(id)) return 0;
    }
    SDL_LockMutex(mutex);
    for (int side = 0; side < 2; side++) {
        values[side] = v[side];
        // at is a validity flag from the game's clock; expiry uses wall time even during a game freeze.
        // Refresh only the side that actually received an event, not its unchanged partner.
        static uint64_t seen[2];
        if (at[side] != seen[side]) updated[side] = now, seen[side] = at[side];
        if (!allowed) values[side].amp_low = values[side].amp_high = 0;
    }
    gain = (float)cfg_double("TOTK_DUALSENSE_HD_STRENGTH") / 100.0f;
    SDL_UnlockMutex(mutex);
    if (!first_wave && allowed && (v[0].amp_low > 0 || v[0].amp_high > 0 || v[1].amp_low > 0 || v[1].amp_high > 0)) {
        first_wave = 1;
        rt_log("DualSense HD: game frequency/amplitude values now feeding independent left/right PCM (linear gain %.2f)", (double)gain);
    }
    static int pcm_logged;
    if (!pcm_logged && SDL_GetAtomicInt(&pcm_nonzero)) {
        pcm_logged = 1;
        rt_log("DualSense HD: audio callback submitted non-zero left/right haptic PCM; front speaker channels remain zero");
    }
    return 1;
}

int dualsense_hd_active(void) { return SDL_GetAtomicInt(&active); }
