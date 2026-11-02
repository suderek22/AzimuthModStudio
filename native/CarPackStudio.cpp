// CarPackStudio - test-bench mod for the Car Pack Studio wrapper (FH2 Mod Platform 2.8.0).
// Not shipped with car packs. Provides: crash guard (critical log instead of a dead game),
// title-screen skip, time-of-day / weather control and wrapper console commands.
// Time and weather offsets follow conner-mcvay/fh2-mods tools/weather-menu/FH2WeatherMenu.cs.
#include "fh2mod.h"
#include <windows.h>
#include <string>
#include <atomic>
#include <cstdio>
#include <cstdarg>
#include <cstring>
#include <winsqlite\winsqlite3.h>
#include <cerrno>
#include <climits>
#include <cstdlib>

static const FH2ModApi* g_api;
static FH2Mod* g_mod;
static std::wstring g_dir, g_logPath;
static std::atomic<int> g_crashCount{0};
static bool g_recover = true;
static int g_maxRecover = 25;
static CRITICAL_SECTION g_cs;

static void Log(const char* fmt, ...) {
    char buf[1024];
    va_list va; va_start(va, fmt); vsnprintf(buf, sizeof buf, fmt, va); va_end(va);
    if (g_api && g_mod) g_api->log(g_mod, buf);
}

// ---------------------------------------------------------------- crash guard
static void CriticalLog(const char* text) {
    EnterCriticalSection(&g_cs);
    HANDLE h = CreateFileW(g_logPath.c_str(), FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE, 0, OPEN_ALWAYS, 0, 0);
    if (h != INVALID_HANDLE_VALUE) {
        DWORD w; WriteFile(h, text, (DWORD)strlen(text), &w, 0); CloseHandle(h);
    }
    LeaveCriticalSection(&g_cs);
}

static LONG CALLBACK VectoredHandler(EXCEPTION_POINTERS* ep) {
    DWORD code = ep->ExceptionRecord->ExceptionCode;
    bool fatal = code == EXCEPTION_ACCESS_VIOLATION || code == EXCEPTION_ILLEGAL_INSTRUCTION ||
                 code == EXCEPTION_INT_DIVIDE_BY_ZERO || code == EXCEPTION_PRIV_INSTRUCTION ||
                 code == EXCEPTION_STACK_OVERFLOW || code == EXCEPTION_IN_PAGE_ERROR ||
                 code == EXCEPTION_ARRAY_BOUNDS_EXCEEDED;
    if (!fatal) return EXCEPTION_CONTINUE_SEARCH;
    static thread_local bool inHandler = false;
    if (inHandler) return EXCEPTION_CONTINUE_SEARCH;
    inHandler = true;

    CONTEXT* c = ep->ContextRecord;
    HMODULE mod = 0; char modName[MAX_PATH] = "?";
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       (LPCSTR)ep->ExceptionRecord->ExceptionAddress, &mod);
    if (mod) GetModuleFileNameA(mod, modName, MAX_PATH);
    unsigned long long rva = (unsigned long long)((char*)ep->ExceptionRecord->ExceptionAddress - (char*)mod);
    SYSTEMTIME st; GetLocalTime(&st);
    char buf[2048];
    int n = snprintf(buf, sizeof buf,
        "[%04d-%02d-%02d %02d:%02d:%02d] CRITICAL ERROR 0x%08lX at %p (%s+0x%llX) thread %lu\r\n"
        "  rax=%016llX rcx=%016llX rdx=%016llX rbx=%016llX rsp=%016llX rbp=%016llX\r\n"
        "  rsi=%016llX rdi=%016llX r8=%016llX r9=%016llX rip=%016llX\r\n",
        st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond, code,
        ep->ExceptionRecord->ExceptionAddress, modName, rva, GetCurrentThreadId(),
        c->Rax, c->Rcx, c->Rdx, c->Rbx, c->Rsp, c->Rbp, c->Rsi, c->Rdi, c->R8, c->R9, c->Rip);
    if (code == EXCEPTION_ACCESS_VIOLATION && ep->ExceptionRecord->NumberParameters >= 2)
        n += snprintf(buf + n, sizeof buf - n, "  access violation: %s address %p\r\n",
                      ep->ExceptionRecord->ExceptionInformation[0] ? "write" : "read",
                      (void*)ep->ExceptionRecord->ExceptionInformation[1]);

    int count = ++g_crashCount;
    bool recover = g_recover && count <= g_maxRecover && code != EXCEPTION_STACK_OVERFLOW;
    if (recover) {
        // Unwind one frame: behave as if the faulting function returned 0 to its caller.
        DWORD64 imageBase = 0, ctrl = 0;
        RUNTIME_FUNCTION* rf = RtlLookupFunctionEntry(c->Rip, &imageBase, nullptr);
        if (rf) {
            PVOID handlerData = nullptr;
            CONTEXT copy = *c;
            RtlVirtualUnwind(UNW_FLAG_NHANDLER, imageBase, copy.Rip, rf, &copy, &handlerData, &ctrl, nullptr);
            copy.Rax = 0;
            *c = copy;
            n += snprintf(buf + n, sizeof buf - n, "  recovered (#%d): frame unwound, returning to %p\r\n", count, (void*)c->Rip);
        } else {
            recover = false;
            n += snprintf(buf + n, sizeof buf - n, "  no unwind info: cannot recover\r\n");
        }
    } else {
        n += snprintf(buf + n, sizeof buf - n, "  not recovered (limit/mode); handing over to platform crash report\r\n");
    }
    CriticalLog(buf);
    Log("critical error 0x%08lX at %s+0x%llX (%s)", code, modName, rva, recover ? "recovered" : "fatal");
    inHandler = false;
    return recover ? EXCEPTION_CONTINUE_EXECUTION : EXCEPTION_CONTINUE_SEARCH;
}

// ---------------------------------------------------------------- memory helpers
// ReadProcessMemory/WriteProcessMemory fail with an error code instead of raising an exception,
// so the crash guard never sees our own probing.
static bool Rd(uintptr_t a, void* out, size_t n) {
    SIZE_T got = 0;
    return ReadProcessMemory(GetCurrentProcess(), (LPCVOID)a, out, n, &got) && got == n;
}
static bool Wr(uintptr_t a, const void* in, size_t n) {
    SIZE_T put = 0;
    return WriteProcessMemory(GetCurrentProcess(), (LPVOID)a, in, n, &put) && put == n;
}
static bool Ptr(uintptr_t a, uintptr_t* out) {
    uintptr_t p = 0;
    if (!Rd(a, &p, 8) || p < 0x10000 || p > 0x7fffffffffffULL) return false;
    *out = p;
    return true;
}
static uintptr_t GameBase() { return (uintptr_t)g_api->wait_for_code(10); }

// ---------------------------------------------------------------- time and weather
struct World { uintptr_t clock, weather; };

static bool Signature(uintptr_t base, uintptr_t rva, const unsigned char* bytes, size_t n) {
    unsigned char buf[8];
    return Rd(base + rva, buf, n) && !memcmp(buf, bytes, n);
}

static const char* GetWorld(World* w) {
    uintptr_t base = GameBase();
    if (!base) return "game code is not ready";
    static const unsigned char s1[] = {0x48,0x8b,0x81,0x98,0,0,0,0xc3}, s2[] = {0x48,0x8b,0x81,0x80,4,0,0,0xc3},
                               s3[] = {0x48,0x8b,0x81,0x88,4,0,0,0xc3};
    if (!Signature(base, 0x2e1520, s1, 8) || !Signature(base, 0x2e1f90, s2, 8) || !Signature(base, 0x2e2e90, s3, 8))
        return "unsupported game build";
    uintptr_t a, b, ctx;
    if (!Ptr(base + 0x34aba60, &a) || !Ptr(a + 0x48, &b) || !Ptr(b + 0x98, &ctx) ||
        !Ptr(ctx + 0x480, &w->clock) || !Ptr(ctx + 0x488, &w->weather))
        return "not in free roam yet";
    float sec = 0;
    if (!Rd(w->clock + 0x10, &sec, 4) || !(sec >= -1 && sec <= 86401)) return "unexpected clock state";
    return nullptr;
}

static std::atomic<bool> g_hold{false};
static std::atomic<float> g_wa{0}, g_wr{0}, g_we{0};
static bool g_savedWeather = false;
static unsigned char g_origFlags[3];

static bool PutWeather(const World& w, float atm, float rain, float env) {
    const unsigned char one = 1;
    bool ok = true;
    ok &= Wr(w.weather + 0xf4, &atm, 4);  ok &= Wr(w.weather + 0xf0, &one, 1);
    ok &= Wr(w.weather + 0x104, &rain, 4); ok &= Wr(w.weather + 0x100, &one, 1);
    ok &= Wr(w.weather + 0xfc, &env, 4);  ok &= Wr(w.weather + 0xf8, &one, 1);
    float zero = 0;
    ok &= Wr(w.weather + 8, &atm, 4); ok &= Wr(w.weather + 0x10, &zero, 4);
    ok &= Wr(w.weather + 0x5c, &rain, 4); ok &= Wr(w.weather + 0x18, &env, 4);
    return ok;
}

static DWORD WINAPI HoldThread(LPVOID) {
    for (;;) {
        Sleep(150);
        if (!g_hold) continue;
        World w;
        if (!GetWorld(&w)) PutWeather(w, g_wa, g_wr, g_we);
    }
}

static void Reply(char* reply, size_t size, const char* fmt, ...) {
    va_list va; va_start(va, fmt); vsnprintf(reply, size, fmt, va); va_end(va);
}

static void CmdTime(const char* args, char* reply, size_t size) {
    World w;
    if (const char* e = GetWorld(&w)) return Reply(reply, size, "error: %s", e);
    float h;
    if (sscanf_s(args, "%f", &h) != 1) {
        float s; Rd(w.clock + 0x10, &s, 4);
        return Reply(reply, size, "time %02d:%02d", (int)(s / 3600), (int)((int)s % 3600 / 60));
    }
    if (h < 0 || h >= 24) return Reply(reply, size, "error: hours must be 0..24");
    float sec = h * 3600; unsigned char zero = 0;
    bool ok = Wr(w.clock + 0x10, &sec, 4) && Wr(w.clock + 0xc, &sec, 4) && Wr(w.clock + 0x361, &zero, 1);
    Reply(reply, size, ok ? "time set to %.2f h" : "error: write failed", h);
}

static void CmdFreeze(const char* args, char* reply, size_t size) {
    World w;
    if (const char* e = GetWorld(&w)) return Reply(reply, size, "error: %s", e);
    unsigned char f = atoi(args) ? 1 : 0;
    Reply(reply, size, Wr(w.clock + 0x24, &f, 1) ? (f ? "time frozen" : "time running") : "error: write failed");
}

static void CmdWeather(const char* args, char* reply, size_t size) {
    World w;
    if (const char* e = GetWorld(&w)) return Reply(reply, size, "error: %s", e);
    if (!strncmp(args, "off", 3)) {
        g_hold = false;
        if (g_savedWeather) {
            Wr(w.weather + 0xf0, &g_origFlags[0], 1); Wr(w.weather + 0xf8, &g_origFlags[1], 1);
            Wr(w.weather + 0x100, &g_origFlags[2], 1);
        }
        return Reply(reply, size, "weather override released");
    }
    float a, r, e;
    if (sscanf_s(args, "%f %f %f", &a, &r, &e) != 3 || a < 0 || a > 100 || r < 0 || r > 100 || e < 0 || e > 100)
        return Reply(reply, size, "usage: weather <atmosphere 0-100> <rain 0-100> <environment 0-100> | weather off");
    if (!g_savedWeather) {
        Rd(w.weather + 0xf0, &g_origFlags[0], 1); Rd(w.weather + 0xf8, &g_origFlags[1], 1);
        Rd(w.weather + 0x100, &g_origFlags[2], 1);
        g_savedWeather = true;
    }
    g_wa = a / 100; g_wr = r / 100; g_we = e / 100;
    bool ok = PutWeather(w, g_wa, g_wr, g_we);
    g_hold = ok;
    Reply(reply, size, ok ? "weather set (atmosphere %.0f rain %.0f environment %.0f), held" : "error: write failed", a, r, e);
}

// ---------------------------------------------------------------- boot
// Title-screen skip experiment: captures the title scene (Splash, ctor rva 0xe774e0) and calls
// its A-button handler (rva 0xcec3d0) with the pad code the scene's input map actually knows (0x12).
typedef unsigned char (*ButtonFn)(void* self, unsigned code, unsigned char down);
typedef void* (*CtorFn)(void* self);
static const uintptr_t RVA_SPLASH_CTOR = 0xe774e0, RVA_SPLASH_BUTTON = 0xcec3d0, RVA_SPLASH_SLOT = 0x2e53940;
static const unsigned PAD_A = 0x2d;   // the code Splash::OnButton tests for "accept"
static std::atomic<void*> g_splash{nullptr};
static CtorFn g_origCtor;
static ButtonFn g_origButtonSlot;
static int g_bootDelay = 2000;
static bool g_autoStart = true;

// A usable title scene has a non-null node list head at +0x2c0 whose first node is readable.
static bool SceneReady(void* s, uintptr_t* head, uintptr_t* first) {
    uintptr_t h = 0, f = 0;
    if (!Ptr((uintptr_t)s + 0x2c0, &h) || !Ptr(h, &f)) return false;
    *head = h; *first = f;
    return true;
}

static void* SplashCtorHook(void* self) {
    void* r = g_origCtor(self);
    g_splash = self;
    uintptr_t base = GameBase(), vt = 0, h = 0, f = 0;
    Ptr((uintptr_t)self, &vt);
    Log("boot: title scene constructed (%p) vtable=game+0x%llx ready=%d", self,
        (unsigned long long)(vt - base), (int)SceneReady(self, &h, &f));
    return r;
}
static unsigned char SplashButtonHook(void* self, unsigned code, unsigned char down) {
    g_splash = self;
    Log("boot: game sent button code=0x%x down=%d to scene %p", code, down, self);
    return g_origButtonSlot(self, code, down);
}
static LONG BootFilter(EXCEPTION_POINTERS* ep, uintptr_t base) {
    Log("boot: fault rip=game+0x%llx rcx=%p", (unsigned long long)(ep->ContextRecord->Rip - base), (void*)ep->ContextRecord->Rcx);
    return EXCEPTION_EXECUTE_HANDLER;
}
static bool PressStart(void* splash, uintptr_t base, unsigned char* result) {
    __try {
        *result = ((ButtonFn)(base + RVA_SPLASH_BUTTON))(splash, PAD_A, 1);
        return true;
    } __except (BootFilter(GetExceptionInformation(), base)) { return false; }
}
static DWORD WINAPI BootThread(LPVOID) {
    void* b = g_api->wait_for_code(180000);
    if (!b) { Log("boot: game code not ready, title skip disabled"); return 0; }
    uintptr_t base = (uintptr_t)b;
    FH2Hook* h1 = g_api->hook_function(g_mod, "Splash::Splash", (void*)(base + RVA_SPLASH_CTOR), (void*)SplashCtorHook, (void**)&g_origCtor);
    FH2Hook* h2 = g_api->hook_pointer(g_mod, "Splash::OnButton", (void**)(base + RVA_SPLASH_SLOT), (void*)SplashButtonHook, (void**)&g_origButtonSlot);
    if (h1 || h2) g_api->enable_hooks(g_mod, nullptr);
    Log("boot: title hooks ctor=%d button=%d", h1 != nullptr, h2 != nullptr);
    DWORD t0 = GetTickCount(), seenAt = 0;
    int tries = 0;
    while (GetTickCount() - t0 < 600000 && tries < 3) {
        Sleep(250);
        void* s = g_splash;
        if (!s) continue;
        if (!seenAt) seenAt = GetTickCount();
        if ((int)(GetTickCount() - seenAt) < g_bootDelay) continue;
        uintptr_t h, f;
        if (!SceneReady(s, &h, &f)) {
            // the scene is torn down once the title screen has been accepted
            if (tries) { Log("boot: title scene %p released, title screen left", s); break; }
            Log("boot: scene %p not ready (list head missing)", s); Sleep(1000); continue;
        }
        unsigned char res = 0;
        bool ok = PressStart(s, base, &res);
        Log("boot: start request #%d ok=%d result=%d", ++tries, ok, res);
        if (ok && res) { Log("boot: left the title screen"); break; }
        Sleep(1500);
    }
    return 0;
}

// ---------------------------------------------------------------- car choice override
// States::SetPlayerCarChoice (vtable rva 0x2d21710, execute 0x921a60) fetches the player-car-choice object
// through the singleton at rva 0x327270 -> [+0x48] -> [+0x100] and calls its slot 0x1b8 with the car id.
// Hooking that slot forces any car id (owned or not) into every choice the game makes.
typedef void (*ChoiceFn)(void* self, int id);
static const uintptr_t RVA_SINGLETON_PTR = 0x34b3e18;
static ChoiceFn g_origChoice;
static std::atomic<int> g_forceCar{-1};

static void ChoiceHook(void* self, int id) {
    Log("car: choice set by game id=%d", id);
    g_origChoice(self, id);
}
static bool VSlot(uintptr_t obj, uintptr_t slot, uintptr_t* fn) {
    uintptr_t vt;
    return Ptr(obj, &vt) && Ptr(vt + slot, fn);
}
// Walks singleton -> [+0x48] -> [+0x100] by calling the game's own virtuals; reads only, no state change.
static void* FindChoiceObject(uintptr_t base) {
    typedef void* (*Fn)(void*);
    uintptr_t s = 0, f48 = 0, f100 = 0;
    if (!Ptr(base + RVA_SINGLETON_PTR, &s) || !VSlot(s, 0x48, &f48)) return nullptr;
    void* a = ((Fn)f48)((void*)s);
    if (!a || !VSlot((uintptr_t)a, 0x100, &f100)) return nullptr;
    return ((Fn)f100)(a);
}
// Player-car lifecycle: execute functions of States::LoadPlayerCar / UnloadPlayerCar / SetPlayerCarChoice.
// On load, the player's choice object (same class as the spawn config above) holds the car id at +8.
typedef uintptr_t (*StateFn)(void* self);
static StateFn g_origLoad, g_origUnload, g_origSetChoice;
static std::atomic<bool> g_haveLoad{false};
static std::atomic<int> g_swapPending{-1};
static DWORD g_stateTid = 0;
static const uintptr_t RVA_LOAD_CAR = 0x8ae8e0, RVA_UNLOAD_CAR = 0x946af0, RVA_SETCHOICE = 0x921a60;

static bool ReadChoiceId(int* id, uintptr_t* objOut) {
    uintptr_t base = GameBase(), obj = 0;
    *id = -1;
    __try { obj = (uintptr_t)FindChoiceObject(base); } __except (EXCEPTION_EXECUTE_HANDLER) { obj = 0; }
    if (!obj) return false;
    *objOut = obj;
    return Rd(obj + 8, id, 4);
}
static uintptr_t LoadHook(void* self) {
    int id; uintptr_t obj = 0;
    bool ok = ReadChoiceId(&id, &obj);
    Log("car: LoadPlayerCar choice=%p id=%d force=%d thread=%lu", (void*)obj, ok ? id : -1, g_forceCar.load(), GetCurrentThreadId());
    if (ok && g_forceCar >= 0 && id != g_forceCar) {
        int v = g_forceCar;
        __try { g_origChoice((void*)obj, v); Log("car: LoadPlayerCar id %d -> %d via setter", id, v); }
        __except (EXCEPTION_EXECUTE_HANDLER) { Log("car: setter faulted for id %d", v); }
    }
    g_forceCar = -1;   // only the first load is forced; later menu swaps stay untouched
    g_stateTid = GetCurrentThreadId();
    g_haveLoad = true;
    uintptr_t result = g_origLoad(self);
    Log("car: LoadPlayerCar returned %llx", (unsigned long long)result);
    return result;
}
static uintptr_t UnloadHook(void* self) {
    Log("car: UnloadPlayerCar");
    return g_origUnload(self);
}
static uintptr_t SetChoiceHook(void* self) {
    int id; uintptr_t obj = 0;
    bool ok = ReadChoiceId(&id, &obj);
    Log("car: SetPlayerCarChoice before id=%d", ok ? id : -1);
    uintptr_t r = g_origSetChoice(self);
    ok = ReadChoiceId(&id, &obj);
    Log("car: SetPlayerCarChoice after id=%d", ok ? id : -1);
    return r;
}
typedef uintptr_t (*Fn4)(void*, void*, void*, void*);
static Fn4 g_origWorld;
static std::atomic<int> g_padInject{-1}, g_padFrames{0}, g_swapAction{12};
static std::atomic<bool> g_reloadFlowReady{false};
static std::atomic<int> g_swapActive{-1}, g_swapCompleted{-1}, g_swapFailed{-1};
static bool PatchReloadFlow() {
    std::wstring path = std::wstring(g_api->game_dir) + L"\\mediaoverride\\stateflow\\pause.xml";
    FILE* f = nullptr;
    if (_wfopen_s(&f, path.c_str(), L"rb") || !f) {
        Log("car: cannot read pause flow for reload");
        return false;
    }
    std::string xml;
    char buf[4096];
    size_t n;
    while ((n = fread(buf, 1, sizeof buf, f)) != 0) xml.append(buf, n);
    bool readOk = !ferror(f);
    fclose(f);
    const char* begin = "<state id=\"radio_left_flow\">";
    size_t start = xml.find(begin), end = xml.find("</state>", start);
    if (!readOk || start == std::string::npos || end == std::string::npos) {
        Log("car: reload flow patch failed: radio-left state not found");
        return false;
    }
    xml.replace(start, end + strlen("</state>") - start,
        "<state id=\"radio_left_flow\">\n"
        "<behavior id=\"GameControlWrapper\" />\n"
        "<behavior id=\"BlockPauseEntryWrapper\" />\n"
        "<node state=\"_cps_store_swap_position\" />\n"
        "<node state=\"global.enable_mixer_snapshot\" snapshot=\"BASE_Loading\" />\n"
        "<node state=\"ui_screens.show_fmv_loading_screen_and_continue\" loading_reason=\"LoadingMessages.IDS_FastTravel\" />\n"
        "<node state=\"global.wait_one_frame\" />\n"
        "<node state=\"global.pause_game\" />\n"
        "<node state=\"global.clear_mulligan_key_frames\" start_recording_replay=\"false\" />\n"
        "<node state=\"global.unload_player_car\" />\n"
        "<node state=\"global.load_player_car\" />\n"
        "<node state=\"global.restore_car_position\" />\n"
        "<node state=\"global.disable_car_control\" />\n"
        "<node state=\"global.disable_rumble\" />\n"
        "<node state=\"global.stop_car\" />\n"
        "<node state=\"global.apply_parking_brake_human\" />\n"
        "<node state=\"global.reset_camera_to_player\" />\n"
        "<node state=\"global.load_cutscene\" cutscene_component_id=\"fast_travel_cutscene\" parent_entity_id=\"global/pause_flow\" is_sibling=\"false\" />\n"
        "<node state=\"global.wait_for_cutscene_loaded\" cutscene_component_id=\"fast_travel_cutscene\" parent_entity_id=\"global/pause_flow\" is_sibling=\"false\" />\n"
        "<node state=\"global.disable_mixer_snapshot\" snapshot=\"BASE_Loading\" />\n"
        "<node state=\"global.unpause_game\" />\n"
        "<node state=\"global.play_cutscene\" cutscene_component_id=\"fast_travel_cutscene\" parent_entity_id=\"global/pause_flow\" is_sibling=\"false\" />\n"
        "<node state=\"ui_screens.show_empty_scene_and_continue\" />\n"
        "<node state=\"global.wait_for_cutscene\" cutscene_component_id=\"fast_travel_cutscene\" parent_entity_id=\"global/pause_flow\" is_sibling=\"false\" />\n"
        "<node state=\"global.unload_cutscene\" cutscene_component_id=\"fast_travel_cutscene\" parent_entity_id=\"global/pause_flow\" is_sibling=\"false\" />\n"
        "<node state=\"global.reset_camera_to_player\" show_white_out=\"true\" />\n"
        "<node state=\"global.clear_mulligan_key_frames\" start_recording_replay=\"true\" />\n"
        "<node state=\"global.enable_car_control\" />\n"
        "<node state=\"global.enable_rumble\" />\n"
        "<node state=\"global.release_parking_brake_human\" />\n"
        "<node state=\"ui_screens.show_hud_and_continue\" />\n"
        "</state>\n"
        "<state id=\"_cps_store_swap_position\"><behavior id=\"StoreCarPosition\" entity_id=\"_cps_swap_position\" locator_name=\"\" restore_facing_next_event=\"false\" /></state>");
    if (_wfopen_s(&f, path.c_str(), L"wb") || !f) {
        Log("car: cannot write reload flow");
        return false;
    }
    bool ok = fwrite(xml.data(), 1, xml.size(), f) == xml.size();
    if (fclose(f)) ok = false;
    Log("car: reload flow patch %s (%zu bytes)", ok ? "installed" : "FAILED", xml.size());
    return ok;
}
static StateFn g_origWaitPause;
static uintptr_t WaitPauseHook(void* self) {
    int completed = g_swapActive.exchange(-1);
    if (completed >= 0) {
        g_swapCompleted = completed;
        Log("car: reload flow complete for id %d; gameplay resumed", completed);
    }
    EnterCriticalSection(&g_cs);
    int id = g_swapPending.exchange(-1);
    if (id >= 0) g_swapActive = id;
    LeaveCriticalSection(&g_cs);
    if (id >= 0) {
        uintptr_t obj = 0;
        int before = -1;
        if (ReadChoiceId(&before, &obj)) {
            g_origChoice((void*)obj, id);
            Log("car: dispatching native reload flow %d -> %d", before, id);
            return ((uintptr_t(*)(void*, void*))(GameBase() + 0x1925260))(self, (char*)self + 0x148);
        }
        g_swapFailed = id;
        g_swapActive = -1;
        Log("car: reload request failed: choice object unavailable");
    }
    return g_origWaitPause(self);
}

// Player lookup and PlayState call from DoorsUnlocked 0.1.5.
static std::atomic<void*> g_animationCar{nullptr}, g_animationGraph{nullptr};
static std::atomic<bool> g_animationPending{false};
static std::string g_animationRequest;
static DWORD g_animationRequestedAt = 0;
typedef void (*VehicleUpdateFn)(void*, float, float, uint32_t);
static VehicleUpdateFn g_origVehicleUpdate;
static void* PlayerCar();
static bool PlayAnimation(void* car, const char* state);
static bool PrepareAnimation(void* car) {
    __try {
        uintptr_t ctrl = *(uintptr_t*)((char*)car + 0x4d40);
        void* graph = ctrl ? *(void**)(ctrl + 0x1c8) : nullptr;
        if (!graph) return false;
        int detail = *(int*)((char*)graph + 0x10);
        ((void(*)(void*, int))(GameBase() + 0x16fbe90))(graph, 0);
        g_animationGraph = graph;
        g_animationCar = car;
        Log("animation: full detail selected car=%p graph=%p previous=%d", car, graph, detail);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        Log("animation: detail selection faulted");
        return false;
    }
}
static void MaintainAnimationDetail(void* car) {
    __try {
        uintptr_t ctrl = *(uintptr_t*)((char*)car + 0x4d40);
        void* graph = ctrl ? *(void**)(ctrl + 0x1c8) : nullptr;
        if (!graph || graph != g_animationGraph.load()) {
            g_animationCar = nullptr;
            return;
        }
        if (*(int*)((char*)graph + 0x10) != 0)
            ((void(*)(void*, int))(GameBase() + 0x16fbe90))(graph, 0);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        g_animationCar = nullptr;
        Log("animation: detail maintenance faulted; released");
    }
}
static void VehicleUpdateHook(void* car, float dt, float frameDt, uint32_t frame) {
    g_origVehicleUpdate(car, dt, frameDt, frame);
    if (g_animationPending.load() && PlayerCar() == car) {
        EnterCriticalSection(&g_cs);
        if (!g_animationRequest.empty()) {
            if (!g_animationRequestedAt) {
                if (PrepareAnimation(car)) g_animationRequestedAt = GetTickCount();
                else {
                    Log("animation: request FAILED during branch preparation");
                    g_animationRequest.clear();
                    g_animationPending = false;
                }
            } else if (GetTickCount() - g_animationRequestedAt >= 350) {
                if (car == g_animationCar.load()) {
                    bool ok = PlayAnimation(car, g_animationRequest.c_str());
                    Log("animation: %s %s", g_animationRequest.c_str(), ok ? "submitted on full-detail branch" : "FAILED");
                } else Log("animation: request cancelled because the player car changed");
                g_animationRequest.clear();
                g_animationRequestedAt = 0;
                g_animationPending = false;
            }
        }
        LeaveCriticalSection(&g_cs);
    }
    if (car == g_animationCar.load()) MaintainAnimationDetail(car);
}
static void* PlayerCar() {
    uintptr_t b = GameBase();
    __try {
        void* holder = *(void**)(b + 0x34ABA60);
        if (!holder) return nullptr;
        void* resolved = ((void*(*)(void*, void*))(b + 0x21E0C0))(holder, nullptr);
        if (!resolved) return nullptr;
        void* car = nullptr;
        ((void(*)(void*, void**))(b + 0x2DA060))(resolved, &car);
        return car && *(uintptr_t*)car == b + 0x2C5CB80 ? car : nullptr;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        Log("animation: player lookup faulted");
        return nullptr;
    }
}
static bool PlayAnimation(void* car, const char* state) {
    __try {
        uintptr_t ctrl = *(uintptr_t*)((char*)car + 0x4d40);
        if (!ctrl) {
            Log("animation: no controller for %s", state);
            return false;
        }
        ((void(*)(void*, const char*))(GameBase() + 0xF5D300))(car, state);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        Log("animation: PlayState faulted for %s", state);
        return false;
    }
}
static bool AnimationStates(void* car, char* out, size_t size) {
        __try {
            uintptr_t ctrl = *(uintptr_t*)((char*)car + 0x4d40);
            uintptr_t graph = ctrl ? *(uintptr_t*)(ctrl + 0x1c8) : 0;
            if (!graph) {
                Reply(out, size, "error: this car has no active animation controller");
                return false;
            }
            uintptr_t head = *(uintptr_t*)(graph + 0x268);
            uintptr_t node = *(uintptr_t*)head;
            size_t used = 0;
            for (int count = 0; node != head && count < 256; ++count) {
                size_t len = *(size_t*)(node + 0x20);
                size_t capacity = *(size_t*)(node + 0x28);
                const char* key = capacity < 16 ? (char*)(node + 0x10) : *(char**)(node + 0x10);
                if (len == 0 || len > 127 || used + len + 2 >= size) {
                    Reply(out, size, "error: animation state list is invalid or too large");
                    return false;
                }
                memcpy(out + used, key, len);
                used += len;
                out[used++] = '\n';
                node = *(uintptr_t*)node;
            }
            if (node != head) {
                Reply(out, size, "error: animation state list exceeds 256 entries");
                return false;
            }
            out[used] = 0;
            return true;
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            Reply(out, size, "error: could not read animation graph");
            Log("animation: graph lookup faulted");
            return false;
        }
    }
static void CmdAnimations(char* reply, size_t size) {
        void* car = PlayerCar();
        if (!car) {
            Reply(reply, size, "error: no active player car");
            return;
        }
        AnimationStates(car, reply, size);
    }
static void CmdAnimationInfo(char* reply, size_t size) {
    void* car = PlayerCar();
    if (!car) { Reply(reply, size, "error: no active player car"); return; }
    __try {
        uintptr_t ctrl = *(uintptr_t*)((char*)car + 0x4d40);
        uintptr_t graph = ctrl ? *(uintptr_t*)(ctrl + 0x1c8) : 0;
        if (!graph) { Reply(reply, size, "error: no animation graph"); return; }
        Log("animation: car=%p graph=%p detail=%d", car, (void*)graph, *(int*)(graph + 0x10));
        uintptr_t head = *(uintptr_t*)(graph + 0x228), node = *(uintptr_t*)head;
        for (int i = 0; node != head && i < 256; ++i) {
            size_t len = *(size_t*)(node + 0x20), cap = *(size_t*)(node + 0x28);
            const char* key = cap < 16 ? (char*)(node + 0x10) : *(char**)(node + 0x10);
            if (len > 127) break;
            Log("animation: graph parameter %.*s", (int)len, key);
            node = *(uintptr_t*)node;
        }
        Reply(reply, size, "animation graph diagnostics written to console");
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        Log("animation: graph diagnostics faulted");
        Reply(reply, size, "error: animation graph diagnostics faulted");
    }
}
static void CmdAnimation(const char* args, char* reply, size_t size) {
    size_t n = strlen(args);
    if (!n || n > 127) {
        Reply(reply, size, "error: animation requires a state name, e.g. doorLF:open");
        return;
    }
    void* car = PlayerCar();
    if (!car) {
        Reply(reply, size, "error: no active player car");
        return;
    }
    char states[8192];
    if (!AnimationStates(car, states, sizeof states)) {
        Reply(reply, size, "%s", states);
        return;
    }
    std::string key = std::string(args) + "\n";
    std::string list = std::string("\n") + states;
    if (list.find("\n" + key) == std::string::npos) {
        Reply(reply, size, "error: state %s is not in the active car's animation graph", args);
        return;
    }
    if (!g_origVehicleUpdate) {
        Reply(reply, size, "error: animation update hook unavailable");
        return;
    }
    EnterCriticalSection(&g_cs);
    bool busy = g_animationPending.load();
    if (!busy) {
        g_animationRequest = args;
        g_animationRequestedAt = 0;
        g_animationPending = true;
    }
    LeaveCriticalSection(&g_cs);
    Reply(reply, size, busy ? "error: another animation is preparing" : "animation preparing on full-detail branch: %s", args);
}

// Live swap: put the car into the player's choice object, then press the input that the studio's patched pause
// stateflow (radio_left_flow, see runtime.py) maps to the game's own reload flow. Runs on the state thread.
static bool SetChoiceAndPress(int id) {
    uintptr_t obj = 0;
    int before = -1;
    __try {
        if (!ReadChoiceId(&before, &obj)) return false;
        g_origChoice((void*)obj, id);
    } __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
    Log("car: live swap %d -> %d, pressing action %d", before, id, g_swapAction.load());
    g_padFrames = 3;
    g_padInject = g_swapAction.load();
    return true;
}
static uintptr_t WorldTick(void* a, void* b, void* c, void* d) {
    uintptr_t r = g_origWorld(a, b, c, d);
    return r;
}
// Input::WasPressed(this, action, player, flag) at rva 0x1072510. Used to log action ids and to press one on demand.
typedef bool (*PressFn)(void*, int, int, bool);
static PressFn g_origPressed;
static bool PressedHook(void* self, int action, int player, bool flag) {
    bool r = g_origPressed(self, action, player, flag);
    static unsigned seen = 0;
    if (r && action >= 0 && action < 32 && !(seen & (1u << action))) {
        seen |= 1u << action;
        Log("input: WasPressed action=%d player=%d flag=%d", action, player, (int)flag);
    }
    if (action == g_padInject.load() && g_padFrames.load() > 0) {
        if (--g_padFrames <= 0) g_padInject = -1;
        return true;
    }
    return r;
}
static DWORD WINAPI CarThread(LPVOID) {
    void* b = g_api->wait_for_code(180000);
    if (!b) return 0;
    uintptr_t base = (uintptr_t)b, vt = base + 0x2c5de38;
    g_api->hook_function(g_mod, "Vehicle animation detail", (void*)(base + 0x2a9400),
                       (void*)VehicleUpdateHook, (void**)&g_origVehicleUpdate);
    std::wstring ini = g_dir + L"\\CarPackStudio.ini";
    if (GetPrivateProfileIntW(L"Car", L"ExperimentalReload", 0, ini.c_str())) {
        g_reloadFlowReady = PatchReloadFlow();
        g_api->hook_function(g_mod, "WaitForPause reload dispatch", (void*)(base + 0x964810), (void*)WaitPauseHook, (void**)&g_origWaitPause);
    }
    g_api->hook_function(g_mod, "Input::WasPressed", (void*)(base + 0x1072510), (void*)PressedHook, (void**)&g_origPressed);
    FH2Hook* h2 = g_api->hook_pointer(g_mod, "PlayerCarChoice::Set", (void**)(vt + 0x1b8), (void*)ChoiceHook, (void**)&g_origChoice);
    FH2Hook* h3 = g_api->hook_function(g_mod, "LoadPlayerCar", (void*)(base + RVA_LOAD_CAR), (void*)LoadHook, (void**)&g_origLoad);
    FH2Hook* h4 = g_api->hook_function(g_mod, "UnloadPlayerCar", (void*)(base + RVA_UNLOAD_CAR), (void*)UnloadHook, (void**)&g_origUnload);
    FH2Hook* h5 = g_api->hook_function(g_mod, "SetPlayerCarChoice", (void*)(base + RVA_SETCHOICE), (void*)SetChoiceHook, (void**)&g_origSetChoice);
    FH2Hook* h6 = g_api->hook_function(g_mod, "WorldTick", (void*)(base + 0x384C00), (void*)WorldTick, (void**)&g_origWorld);
    g_api->enable_hooks(g_mod, nullptr);
    Log("car: hooks choice=%d load=%d unload=%d setchoice=%d world=%d force=%d", h2 != nullptr, h3 != nullptr,
        h4 != nullptr, h5 != nullptr, h6 != nullptr, g_forceCar.load());
    return 0;
}
static bool ValidateCarId(int id, char* reply, size_t size) {
    std::wstring path;
    for (const wchar_t* directory : {L"mediaoverride", L"mediapc", L"media"}) {
        std::wstring candidate = std::wstring(g_api->game_dir) + L"\\" + directory + L"\\stripped\\gamedbRC.slt";
        if (GetFileAttributesW(candidate.c_str()) != INVALID_FILE_ATTRIBUTES) {
            path = candidate;
            break;
        }
    }
    if (path.empty()) {
        Reply(reply, size, "error: cannot validate car ID %d: game database was not found; no swap performed", id);
        Log("car: ID validation failed: game database not found");
        return false;
    }
    int bytes = WideCharToMultiByte(CP_UTF8, 0, path.c_str(), -1, nullptr, 0, nullptr, nullptr);
    std::string utf8(bytes, '\0');
    WideCharToMultiByte(CP_UTF8, 0, path.c_str(), -1, &utf8[0], bytes, nullptr, nullptr);
    sqlite3* db = nullptr;
    sqlite3_stmt* statement = nullptr;
    int rc = sqlite3_open_v2(utf8.c_str(), &db, SQLITE_OPEN_READONLY, nullptr);
    if (rc == SQLITE_OK)
        rc = sqlite3_prepare_v2(db, "SELECT 1 FROM Data_Car WHERE Id=? LIMIT 1", -1, &statement, nullptr);
    if (rc == SQLITE_OK) rc = sqlite3_bind_int(statement, 1, id);
    if (rc == SQLITE_OK) rc = sqlite3_step(statement);
    bool found = rc == SQLITE_ROW;
    if (!found) {
        if (rc == SQLITE_DONE)
            Reply(reply, size, "error: car ID %d does not exist in the active game database; no swap performed", id);
        else
            Reply(reply, size, "error: cannot validate car ID %d: %s; no swap performed", id,
                  db ? sqlite3_errmsg(db) : "cannot open database");
        Log("car: %s", reply);
    }
    if (statement) sqlite3_finalize(statement);
    if (db) sqlite3_close(db);
    return found;
}
static void CmdCar(const char* args, char* reply, size_t size) {
    if (!strcmp(args, "status")) {
        int active = g_swapActive.load(), pending = g_swapPending.load();
        if (active >= 0) Reply(reply, size, "swapping %d", active);
        else if (pending >= 0) Reply(reply, size, "queued %d", pending);
        else if (g_swapFailed >= 0) Reply(reply, size, "failed %d", g_swapFailed.load());
        else if (g_swapCompleted >= 0) Reply(reply, size, "complete %d", g_swapCompleted.load());
        else Reply(reply, size, "idle");
        return;
    }
    errno = 0;
    char* end = nullptr;
    long parsed = strtol(args, &end, 10);
    while (end && (*end == ' ' || *end == '\t')) ++end;
    if (errno == ERANGE || end == args || !end || *end || parsed <= 0 || parsed > INT_MAX) {
        Reply(reply, size, "error: invalid car ID; use car <positive integer ID> | status; no swap performed"); return;
    }
    int id = (int)parsed;
    if (!ValidateCarId(id, reply, size)) return;
    if (!g_haveLoad) { Reply(reply, size, "error: no player car has been loaded yet"); return; }
    if (!g_reloadFlowReady || !g_origWaitPause) { Reply(reply, size, "error: live reload is experimental and disabled; use Test in game to restart"); return; }
    EnterCriticalSection(&g_cs);
    bool busy = g_swapActive >= 0 || g_swapPending >= 0;
    if (!busy) {
        g_swapCompleted = g_swapFailed = -1;
        g_swapPending = id;
    }
    LeaveCriticalSection(&g_cs);
    if (busy) { Reply(reply, size, "error: car reload already in progress"); return; }
    Reply(reply, size, "swapping to car %d", id);
}

static void CmdPad(const char* args, char* reply, size_t size) {
    int id = atoi(args);
    if (id < 0 || id > 31 || !*args) { Reply(reply, size, "usage: pad <action 0-31>"); return; }
    g_padFrames = 3;
    g_padInject = id;
    Reply(reply, size, "pressing action %d", id);
}

// ---------------------------------------------------------------- commands
static void CmdHandler(void*, const char* name, const char* args, char* reply, size_t size) {
    if (!strcmp(name, "status")) {
        Reply(reply, size, "crashes=%d recover=%d hold_weather=%d", g_crashCount.load(), (int)g_recover, (int)g_hold);
    } else if (!strcmp(name, "recover")) {
        g_recover = atoi(args) != 0;
        Reply(reply, size, g_recover ? "crash recovery on" : "crash recovery off");
    } else if (!strcmp(name, "echo")) {
        Reply(reply, size, "%s", args);
    } else if (!strcmp(name, "time")) CmdTime(args, reply, size);
    else if (!strcmp(name, "freeze")) CmdFreeze(args, reply, size);
    else if (!strcmp(name, "weather")) CmdWeather(args, reply, size);
    else if (!strcmp(name, "car")) CmdCar(args, reply, size);
    else if (!strcmp(name, "pad")) CmdPad(args, reply, size);
    else if (!strcmp(name, "animation")) CmdAnimation(args, reply, size);
    else if (!strcmp(name, "animations")) CmdAnimations(reply, size);
    else if (!strcmp(name, "animationinfo")) CmdAnimationInfo(reply, size);
    else Reply(reply, size, "error: unknown command");
}

FH2MOD_EXPORT int fh2mod_init(const FH2ModApi* api) {
    InitializeCriticalSection(&g_cs);
    g_api = api;
    FH2ModInfo info = {};
    info.size = sizeof info;
    info.name = "CarPackStudio";
    info.version = "1.0.0";
    info.platform_version = "2.8.0";
    info.description = "Car Pack Studio test bench: crash guard, title skip, time/weather, console.";
    g_mod = api->register_mod(&info);
    if (!g_mod) return 0;

    wchar_t path[MAX_PATH];
    HMODULE self = 0;
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       (LPCWSTR)&fh2mod_init, &self);
    GetModuleFileNameW(self, path, MAX_PATH);
    g_dir = path; g_dir.resize(g_dir.find_last_of(L'\\'));
    g_logPath = g_dir + L"\\CarPackStudio-critical.log";

    std::wstring ini = g_dir + L"\\CarPackStudio.ini";
    g_recover = GetPrivateProfileIntW(L"CrashGuard", L"Recover", 1, ini.c_str()) != 0;
    g_maxRecover = GetPrivateProfileIntW(L"CrashGuard", L"MaxRecoveries", 25, ini.c_str());
    if (GetPrivateProfileIntW(L"CrashGuard", L"Enabled", 1, ini.c_str()))
        AddVectoredExceptionHandler(0, VectoredHandler);
    g_autoStart = GetPrivateProfileIntW(L"Boot", L"AutoStart", 0, ini.c_str()) != 0;
    g_bootDelay = GetPrivateProfileIntW(L"Boot", L"DelayMs", 2000, ini.c_str());
    g_forceCar = GetPrivateProfileIntW(L"Car", L"ForceId", -1, ini.c_str());
    CloseHandle(CreateThread(0, 0, CarThread, 0, 0, 0));
    if (g_autoStart) CloseHandle(CreateThread(0, 0, BootThread, 0, 0, 0));
    CloseHandle(CreateThread(0, 0, HoldThread, 0, 0, 0));

    api->add_command(g_mod, "status", "crash counters", CmdHandler, nullptr);
    api->add_command(g_mod, "recover", "recover <0|1>: crash recovery on/off", CmdHandler, nullptr);
    api->add_command(g_mod, "echo", "echo <text>", CmdHandler, nullptr);
    api->add_command(g_mod, "time", "time [hours 0-24): show or set the time of day", CmdHandler, nullptr);
    api->add_command(g_mod, "freeze", "freeze <0|1>: stop or resume the clock", CmdHandler, nullptr);
    api->add_command(g_mod, "weather", "weather <atmosphere> <rain> <environment> (0-100) | off", CmdHandler, nullptr);
    api->add_command(g_mod, "car", "car <id>: swap the player's car live", CmdHandler, nullptr);
    api->add_command(g_mod, "pad", "pad <action>: press a game input action for a few frames", CmdHandler, nullptr);
    api->add_command(g_mod, "animation", "animation <state>: play a state on the active car (DoorsUnlocked)", CmdHandler, nullptr);
    api->add_command(g_mod, "animations", "list playable states in the active car's animation graph", CmdHandler, nullptr);
    api->add_command(g_mod, "animationinfo", "log animation graph parameters for diagnosis", CmdHandler, nullptr);
    Log("CarPackStudio ready: crash guard %s", g_recover ? "recovering" : "logging");
    return 0;
}
