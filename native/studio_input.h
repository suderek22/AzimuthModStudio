#include <roapi.h>
#include <winstring.h>
#include <xinput.h>
#include <vector>
#include <cmath>

namespace StudioInput {
struct Reading {
    UINT64 timestamp;
    UINT32 buttons;
    double lt, rt, lx, ly, rx, ry;
};
struct Frame {
    UINT32 ms, buttons;
    double lt, rt, lx, ly, rx, ry;
};
static_assert(sizeof(Frame) == 56, "CPSI frame layout");
static HRESULT (STDMETHODCALLTYPE* original)(void*, Reading*);
static DWORD (WINAPI* getState)(DWORD, XINPUT_STATE*);
static std::vector<Frame> frames;
static int mode = 0, slot = 0;
static size_t index = 0;
static ULONGLONG started = 0;
static std::atomic<unsigned long> reads{0};
static std::atomic<unsigned long> injected{0};
static std::atomic<bool> ready{false};
static void* selectedPad = nullptr;

static UINT32 Buttons(WORD b) {
    const WORD masks[] = {0x10,0x20,0x1000,0x2000,0x4000,0x8000,1,2,4,8,0x100,0x200,0x40,0x80};
    UINT32 result = 0;
    for (unsigned i = 0; i < 14; ++i) if (b & masks[i]) result |= 1u << i;
    return result;
}
static double Axis(SHORT value) { return value < 0 ? value / 32768.0 : value / 32767.0; }
static std::wstring Path() { return g_dir + L"\\CPSInput.bin"; }
static bool Save() {
    FILE* f = nullptr;
    if (_wfopen_s(&f, Path().c_str(), L"wb") || !f) return false;
    UINT32 header[] = {0x49535043, 1, (UINT32)frames.size()};
    bool ok = fwrite(header, sizeof header, 1, f) == 1 &&
        fwrite(frames.data(), sizeof(Frame), frames.size(), f) == frames.size();
    if (fclose(f)) ok = false;
    return ok;
}
static bool Load() {
    FILE* f = nullptr;
    if (_wfopen_s(&f, Path().c_str(), L"rb") || !f) return false;
    UINT32 header[3] = {};
    bool ok = fread(header, sizeof header, 1, f) == 1 &&
        header[0] == 0x49535043 && header[1] == 1 && header[2] >= 2 && header[2] <= 100000;
    std::vector<Frame> incoming;
    if (ok) {
        incoming.resize(header[2]);
        ok = fread(incoming.data(), sizeof(Frame), incoming.size(), f) == incoming.size() && fgetc(f) == EOF;
    }
    if (ferror(f)) ok = false;
    fclose(f);
    if (!ok) return false;
    UINT32 previous = 0;
    for (auto& frame : incoming) {
        if (frame.ms < previous || frame.ms > 1800000 || frame.buttons > 0x3fff ||
            !std::isfinite(frame.lt) || frame.lt < 0 || frame.lt > 1 ||
            !std::isfinite(frame.rt) || frame.rt < 0 || frame.rt > 1 ||
            !std::isfinite(frame.lx) || fabs(frame.lx) > 1 || !std::isfinite(frame.ly) || fabs(frame.ly) > 1 ||
            !std::isfinite(frame.rx) || fabs(frame.rx) > 1 || !std::isfinite(frame.ry) || fabs(frame.ry) > 1) return false;
        previous = frame.ms;
    }
    const Frame& last = incoming.back();
    if (incoming.front().ms != 0 || !last.ms || last.buttons || last.lt || last.rt ||
        last.lx || last.ly || last.rx || last.ry) return false;
    frames.swap(incoming);
    return true;
}
static HRESULT STDMETHODCALLTYPE Read(void* self, Reading* out) {
    HRESULT hr = original(self, out);
    ++reads;
    if (!out) return hr;
    EnterCriticalSection(&g_cs);
    if (mode && !selectedPad) selectedPad = self;
    if (mode && selectedPad == self) {
        UINT32 elapsed = (UINT32)(GetTickCount64() - started);
        if (mode == 1) {
            XINPUT_STATE state = {};
            if (getState(slot, &state) != ERROR_SUCCESS) {
                mode = 0;
                Log("input: ERROR controller %d disconnected; recording discarded", slot);
                frames.clear();
            } else if (elapsed >= 1800000 || frames.size() >= 99999) {
                frames.push_back({elapsed > 1800000 ? 1800000 : elapsed, 0, 0, 0, 0, 0, 0, 0});
                mode = 0;
                Log(Save() ? "input: recording limit reached; saved CPSInput.bin" :
                    "input: ERROR recording limit reached; cannot save CPSInput.bin");
            } else {
                auto& p = state.Gamepad;
                Frame sample = {frames.empty() ? 0u : elapsed, Buttons(p.wButtons),
                    p.bLeftTrigger / 255.0, p.bRightTrigger / 255.0,
                    Axis(p.sThumbLX), Axis(p.sThumbLY), Axis(p.sThumbRX), Axis(p.sThumbRY)};
                if (frames.empty() || memcmp((char*)&sample + 4, (char*)&frames.back() + 4, sizeof(Frame) - 4))
                    frames.push_back(sample);
            }
        } else {
            Frame neutral = {};
            const Frame* sample = &neutral;
            if (mode == 2 && elapsed < frames.back().ms) {
                while (index + 1 < frames.size() && frames[index + 1].ms <= elapsed) ++index;
                sample = &frames[index];
            } else if (mode == 2) {
                mode = 3;
                started = GetTickCount64();
                Log("input: playback complete; releasing all controls");
            } else if (elapsed >= 250) mode = 0;
            out->buttons = sample->buttons;
            out->lt = sample->lt; out->rt = sample->rt;
            out->lx = sample->lx; out->ly = sample->ly; out->rx = sample->rx; out->ry = sample->ry;
            // WGI timestamps are microseconds, independent of recorded timing.
            out->timestamp = GetTickCount64() * 1000;
            ++injected;
            hr = S_OK;
        }
    }
    LeaveCriticalSection(&g_cs);
    return hr;
}
static void Release(void* p) {
    if (p) ((ULONG(STDMETHODCALLTYPE*)(void*))(*(void***)p)[2])(p);
}
static DWORD WINAPI Install(void*) {
    HMODULE xi = LoadLibraryW(L"xinput1_4.dll");
    getState = xi ? (decltype(getState))GetProcAddress(xi, "XInputGetState") : nullptr;
    if (!getState) { Log("input: ERROR XInputGetState unavailable"); return 0; }
    HRESULT init = RoInitialize(RO_INIT_MULTITHREADED);
    if (FAILED(init)) { Log("input: ERROR RoInitialize hr=%08lx", init); return 0; }
    const GUID iid = {0x8bbce529,0xd49c,0x39e9,{0x95,0x60,0xe4,0x7d,0xde,0x96,0xb7,0xc8}};
    HSTRING name = nullptr;
    HRESULT named = WindowsCreateString(L"Windows.Gaming.Input.Gamepad", 28, &name);
    if (FAILED(named)) { Log("input: ERROR controller class string hr=%08lx", named); RoUninitialize(); return 0; }
    // Install after the bundled NoPause mod so playback is the outermost reading hook.
    Sleep(3000);
    for (int attempt = 0; attempt < 600 && !ready; ++attempt) {
        void* statics = nullptr; void* list = nullptr; void* pad = nullptr;
        if (SUCCEEDED(RoGetActivationFactory(name, iid, &statics)) && statics) {
            auto vt = *(void***)statics;
            HRESULT hr = ((HRESULT(STDMETHODCALLTYPE*)(void*,void**))vt[10])(statics, &list);
            if (SUCCEEDED(hr) && list) {
                UINT32 count = 0;
                auto lv = *(void***)list;
                if (SUCCEEDED(((HRESULT(STDMETHODCALLTYPE*)(void*,UINT32*))lv[7])(list, &count)) && count)
                    ((HRESULT(STDMETHODCALLTYPE*)(void*,UINT32,void**))lv[6])(list, 0, &pad);
            }
        }
        if (pad) {
            FH2Hook* hook = g_api->hook_function(g_mod, "Studio XInput playback (WGI)",
                (*(void***)pad)[8], (void*)Read, (void**)&original);
            ready = hook && g_api->enable_hooks(g_mod, hook);
            Log(ready ? "input: controller recording/playback hook ready" : "input: ERROR cannot enable controller hook");
        }
        Release(pad); Release(list); Release(statics);
        if (pad) break;
        Sleep(1000);
    }
    if (!ready) Log("input: ERROR no controller hook; connect a controller and restart the wrapper");
    WindowsDeleteString(name);
    RoUninitialize();
    return 0;
}
static void Command(const char* args, char* reply, size_t size) {
    EnterCriticalSection(&g_cs);
    if (!strcmp(args, "status")) {
        snprintf(reply, size, "input mode=%s ready=%d reads=%lu injected=%lu frames=%zu elapsed_ms=%llu",
            mode == 1 ? "recording" : mode == 2 ? "playing" : mode == 3 ? "releasing" : "idle",
            (int)ready.load(), reads.load(), injected.load(), frames.size(), mode ? GetTickCount64() - started : 0);
    } else if (!strcmp(args, "stop")) {
        bool recording = mode == 1;
        if (recording && !frames.empty()) {
            UINT32 elapsed = (UINT32)(GetTickCount64() - started);
            frames.push_back({elapsed > 1800000 ? 1800000 : elapsed ? elapsed : 1,0,0,0,0,0,0,0});
        }
        mode = recording || !mode ? 0 : 3;
        started = GetTickCount64();
        bool saved = recording && frames.size() >= 2 && Save();
        snprintf(reply, size, recording ? (saved ? "input recording saved: CPSInput.bin" :
            "error: no input samples or cannot save recording") : "input stopped; controls released");
    } else if (mode) {
        snprintf(reply, size, "error: input is busy; stop first");
    } else if (!ready) {
        snprintf(reply, size, "error: input hook unavailable; connect a controller and restart the wrapper");
    } else if (!strncmp(args, "record ", 7) && strlen(args) == 8 && args[7] >= '0' && args[7] <= '3') {
        slot = args[7] - '0';
        XINPUT_STATE state = {};
        if (getState(slot, &state) != ERROR_SUCCESS)
            snprintf(reply, size, "error: XInput controller %d is not connected", slot);
        else {
            frames.clear(); selectedPad = nullptr; started = GetTickCount64(); mode = 1;
            snprintf(reply, size, "input recording controller %d (maximum 30 minutes)", slot);
        }
    } else if (!strcmp(args, "play")) {
        if (!Load()) snprintf(reply, size, "error: invalid or missing CPSInput.bin; playback not started");
        else {
            selectedPad = nullptr; index = 0; started = GetTickCount64(); mode = 2;
            snprintf(reply, size, "input playing %zu frames for %u ms", frames.size(), frames.back().ms);
        }
    } else snprintf(reply, size, "error: usage: input record <0-3> | play | stop | status");
    LeaveCriticalSection(&g_cs);
}
}
