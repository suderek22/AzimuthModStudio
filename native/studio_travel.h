static std::atomic<bool> g_tpQueued{false}, g_tpActive{false};
static float g_tpPosition[3] = {};
static ULONGLONG g_tpQueuedAt = 0;
static int g_tpTrack = 542;
static thread_local bool g_tpStoring = false;
static bool g_tpHooks = false;
static StateFn g_origStorePosition;
static void (*g_origStoreVector)(void*, const float*);
static std::atomic<bool> g_tpApplied{false};
static std::atomic<int> g_trackPending{-1}, g_trackActive{-1};
static ULONGLONG g_trackStarted = 0;
static bool g_trackTimedOut = false;
static const char* g_trackStage = "idle";
static char g_trackLastState[192] = {};
static const char* g_trackFailure = nullptr;

static int ActiveTrack() {
    uintptr_t manager = 0;
    int current = -1;
    // States::SwitchOnActiveTrack (0x93f0f0) reads this manager.
    // Its current-track accessor, 0x9247b0, is exactly `mov eax,[rcx+8]; ret`.
    if (Ptr(GameBase() + 0x365ad70, &manager)) Rd(manager + 8, &current, sizeof current);
    return current;
}
static bool TrackSettled() {
    uintptr_t manager = 0;
    int data[4] = {};
    if (!Ptr(GameBase() + 0x365ad70, &manager) || !Rd(manager + 8, data, sizeof data)) return false;
    // Native 0xfdb330: idle when current == target and the pending job field is zero.
    return data[0] == data[1] && data[3] == 0;
}
static void CheckTrackTimeout() {
    EnterCriticalSection(&g_cs);
    if ((g_trackPending >= 0 || g_trackActive >= 0) && !g_trackTimedOut &&
        GetTickCount64() - g_trackStarted >= 90000) {
        g_trackTimedOut = true;
        int target = g_trackActive >= 0 ? g_trackActive.load() : g_trackPending.load();
        if (g_trackPending >= 0) {
            g_trackPending = -1;
            g_trackStage = "queue expired; no travel dispatched";
        }
        Log("track: ERROR 90s timeout: current=%d target=%d settled=%d stage=%s. "
            "An active engine load is NOT force-completed; inspect track status / trace or use Stop.",
            ActiveTrack(), target, (int)TrackSettled(), g_trackStage);
    }
    LeaveCriticalSection(&g_cs);
}
static void CmdTrack(const char* args, char* reply, size_t size) {
    EnterCriticalSection(&g_cs);
    if (!strcmp(args, "status")) {
        snprintf(reply, size, "track current=%d settled=%d queued=%d target=%d stage=%s elapsed_ms=%llu timed_out=%d",
            ActiveTrack(), (int)TrackSettled(), g_trackPending.load(), g_trackActive.load(), g_trackStage,
            g_trackStarted ? GetTickCount64() - g_trackStarted : 0, (int)g_trackTimedOut);
    } else {
        int target = !strcmp(args, "542") ? 542 : !strcmp(args, "549") ? 549 : -1;
        if (target < 0)
            snprintf(reply, size, "error: supported tracks: Anthem (542), TheIsland (549); use track status");
        else if (!g_trackFlowReady || !g_origWaitPause || !g_haveLoad)
            snprintf(reply, size, "error: native track flow unavailable; enable experimental reload and restart the wrapper");
        else if (SpawnBusy())
            snprintf(reply, size, "error: use spawn clear and wait for an empty spawn list before map travel");
        else if (EventBusy() || !TrackSettled() || (ActiveTrack() != 542 && ActiveTrack() != 549) ||
                 g_trackActive >= 0 || g_trackPending >= 0 || g_tpQueued || g_tpActive ||
                 g_swapActive >= 0 || g_swapPending >= 0)
            snprintf(reply, size, "error: wait for settled free roam; map travel or car reload is already in progress");
        else if (ActiveTrack() == target)
            snprintf(reply, size, "track already loaded: %d; no travel performed", target);
        else {
            g_trackPending = target;
            g_trackStarted = GetTickCount64();
            g_trackTimedOut = false;
            g_trackFailure = nullptr;
            g_trackStage = "queued; waiting for free roam";
            Log("track: requested %d -> %d; dispatch queued on native free-roam thread", ActiveTrack(), target);
            snprintf(reply, size, "track queued: %d; native travel will check DLC and reload free roam", target);
        }
    }
    LeaveCriticalSection(&g_cs);
}
static bool DispatchTrack(void* self) {
    EnterCriticalSection(&g_cs);
    if (g_trackActive >= 0) {
        // The pause entity appears before arrival finishes. Use the same native
        // pause-availability predicate as the game's map input, not entity existence.
        bool canPause = ((bool(*)(void*))(GameBase() + 0x963560))(self);
        if (!g_trackFailure && !(canPause && ActiveTrack() == g_trackActive && TrackSettled())) {
            LeaveCriticalSection(&g_cs);
            return false;
        }
        int target = g_trackActive.exchange(-1);
        bool ok = ActiveTrack() == target && TrackSettled();
        g_trackStage = ok ? "complete; gameplay resumed" :
            g_trackFailure ? g_trackFailure : "failed; travel flow returned without reaching target";
        Log("track: %s current=%d requested=%d elapsed=%llu ms", g_trackStage, ActiveTrack(), target,
            GetTickCount64() - g_trackStarted);
    }
    if (g_trackPending >= 0 && !((bool(*)(void*))(GameBase() + 0x963560))(self)) {
        LeaveCriticalSection(&g_cs);
        return false;
    }
    int target = g_trackPending.exchange(-1);
    bool valid = target >= 0 && GetTickCount64() - g_trackStarted < 90000 && TrackSettled() &&
                 ActiveTrack() == (target == 549 ? 542 : 549);
    if (valid) {
        g_trackActive = target;
        g_tpApplied = false;
        g_trackStage = "native travel dispatched";
    } else if (target >= 0) {
        g_trackStage = "cancelled; active map changed before dispatch";
        Log("track: ERROR %s; current=%d target=%d", g_trackStage, ActiveTrack(), target);
    }
    LeaveCriticalSection(&g_cs);
    if (!valid) return false;
    Log("track: dispatching native radio-right travel flow to %d (thread=%lu)", target, GetCurrentThreadId());
    ((uintptr_t(*)(void*, void*))(GameBase() + 0x1925260))(self, (char*)self + 0x178);
    return true;
}
static void StoreVectorHook(void* self, const float* vector) {
    if (g_tpStoring) {
        __declspec(align(16)) float destination[4] = {g_tpPosition[0], g_tpPosition[1], g_tpPosition[2], vector[3]};
        g_origStoreVector(self, destination);
        g_tpApplied = true;
        Log("tp: stored native restore position %.3f %.3f %.3f (previous %.3f %.3f %.3f)",
            destination[0], destination[1], destination[2], vector[0], vector[1], vector[2]);
    } else g_origStoreVector(self, vector);
}
static uintptr_t StorePositionHook(void* self) {
    g_tpStoring = g_tpActive.load();
    uintptr_t result = 0;
    __try { result = g_origStorePosition(self); }
    __finally { g_tpStoring = false; }
    if (g_tpActive && !g_tpApplied)
        Log("tp: ERROR native StoreCarPosition did not produce a position; target was not applied");
    return result;
}
static void CmdTeleport(const char* args, char* reply, size_t size) {
    if (!strcmp(args, "status") || !strcmp(args, "capture")) {
        if (!strcmp(args, "capture") && (EventBusy() || !TrackSettled() || g_trackPending >= 0 || g_trackActive >= 0 ||
            g_swapPending >= 0 || g_swapActive >= 0 || g_tpQueued || g_tpActive)) {
            snprintf(reply, size, "error: wait for settled free roam before capturing a teleport place"); return;
        }
        uintptr_t car = (uintptr_t)PlayerCar(), state = 0;
        float position[3] = {};
        if (!car || !Ptr(car + 0x4c08, &state) || !Rd(state + 0xef0, position, sizeof position)) {
            snprintf(reply, size, "error: no readable player position"); return;
        }
        snprintf(reply, size, "tp track=%d settled=%d queued=%d active=%d applied=%d position=%.3f,%.3f,%.3f",
            ActiveTrack(), (int)TrackSettled(), (int)g_tpQueued.load(), (int)g_tpActive.load(), (int)g_tpApplied.load(),
            position[0], position[1], position[2]);
        return;
    }
    float x, y, z;
    int track = ActiveTrack();
    char tail;
    // Explicit map transport prevents coordinates being applied after a map change.
    bool parsed = sscanf_s(args, "at %d %f %f %f %c", &track, &x, &y, &z, &tail, 1u) == 4;
    if (!parsed) parsed = sscanf_s(args, "%f %f %f %c", &x, &y, &z, &tail, 1u) == 3;
    if (!parsed ||
        !std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z) ||
        fabs(x) > 100000 || fabs(y) > 100000 || fabs(z) > 100000) {
        snprintf(reply, size, "error: use the Studio console: tp <map> <place>"); return;
    }
    if (!g_tpHooks || !g_reloadFlowReady || !g_origWaitPause || !g_haveLoad) {
        snprintf(reply, size, "error: teleport hooks/player car unavailable; restart the wrapper with the updated runtime"); return;
    }
    if (track <= 0 || ActiveTrack() != track || !TrackSettled()) {
        snprintf(reply, size, "error: requested track %d must be loaded and settled; use track to change maps first", track); return;
    }
    EnterCriticalSection(&g_cs);
    bool busy = EventBusy() || SpawnBusy() || g_tpQueued || g_tpActive || g_swapActive >= 0 || g_swapPending >= 0 ||
                g_trackPending >= 0 || g_trackActive >= 0;
    if (!busy) {
        g_tpPosition[0] = x; g_tpPosition[1] = y; g_tpPosition[2] = z;
        g_tpTrack = track;
        g_tpApplied = false;
        g_swapCompleted = g_swapFailed = -1;
        g_tpQueuedAt = GetTickCount64();
        g_tpQueued = true;
    }
    LeaveCriticalSection(&g_cs);
    snprintf(reply, size, busy ? "error: teleport/car reload already in progress" :
        "tp queued: waiting for free roam; native reload/streaming will retain the current car");
}

namespace StudioTrace {
static FILE* file = nullptr;
static unsigned count = 0;
static ULONGLONG started = 0;
static uintptr_t (*original)(void*, void*);
static bool ready = false;
static std::wstring Path() { return g_dir + L"\\CPSMapTrace.jsonl"; }
static void ClassName(void* state, char* out, size_t size) {
    strcpy_s(out, size, "unknown");
    uintptr_t vt = 0, col = 0;
    UINT32 descriptor = 0;
    if (!Ptr((uintptr_t)state, &vt) || !Ptr(vt - 8, &col) || !Rd(col + 12, &descriptor, 4)) return;
    char name[192] = {};
    if (!Rd(GameBase() + descriptor + 16, name, sizeof(name) - 1)) return;
    for (size_t i = 0; name[i]; ++i) {
        if ((unsigned char)name[i] < 32 || (unsigned char)name[i] > 126 || name[i] == '"' || name[i] == '\\') name[i] = '?';
    }
    strcpy_s(out, size, name);
}
static uintptr_t Dispatch(void* self, void* transition) {
    EnterCriticalSection(&g_cs);
    if (g_trackActive >= 0) {
        char name[192]; ClassName(self, name, sizeof name);
        strcpy_s(g_trackLastState, name);
        g_trackStage = g_trackLastState;
        if (!strcmp(name, ".?AVSwapTrack@States@@") && TrackSettled() && ActiveTrack() != g_trackActive)
            g_trackFailure = "failed; native SwapTrack finished on an unexpected track";
        if ((uintptr_t)transition - (uintptr_t)self == 0x58) {
            if (!strcmp(name, ".?AVIsIslandEntitled@States@@"))
                g_trackFailure = "failed; native DLC entitlement check rejected Storm Island";
            else if (!strcmp(name, ".?AVIsIslandInstalled@States@@"))
                g_trackFailure = "failed; native DLC installation check rejected Storm Island; check Azimuth content paths";
            if (g_trackFailure) Log("track: ERROR %s", g_trackFailure);
        }
        Log("track: elapsed=%llu ms current=%d target=%d settled=%d state=%s transition=%lld",
            GetTickCount64() - g_trackStarted, ActiveTrack(), g_trackActive.load(), (int)TrackSettled(),
            name, (long long)((uintptr_t)transition - (uintptr_t)self));
    }
    if (file) {
        char name[192]; ClassName(self, name, sizeof name);
        uintptr_t vt = 0; Rd((uintptr_t)self, &vt, sizeof vt);
        int written = fprintf(file, "{\"ms\":%llu,\"thread\":%lu,\"class\":\"%s\",\"state_vtable_rva\":\"0x%llx\","
            "\"transition_offset\":%lld,\"track_id\":%d,\"car_target\":%d}\n",
            GetTickCount64() - started, GetCurrentThreadId(), name,
            (unsigned long long)(vt - GameBase()), (long long)((uintptr_t)transition - (uintptr_t)self),
            ActiveTrack(), g_swapActive.load());
        if (written < 0 || ++count >= 100000) {
            fclose(file); file = nullptr;
            Log("trace: %s; capture closed at %u transitions", written < 0 ? "ERROR writing capture" : "limit reached", count);
        } else if (fflush(file)) {
            fclose(file); file = nullptr;
            Log("trace: ERROR flushing transition capture");
        }
    }
    LeaveCriticalSection(&g_cs);
    return original(self, transition);
}
static void Command(const char* args, char* reply, size_t size) {
    EnterCriticalSection(&g_cs);
    if (!strcmp(args, "start")) {
        if (!ready) snprintf(reply, size, "error: stateflow transition hook unavailable");
        else if (file) snprintf(reply, size, "error: a map transition capture is already running");
        else if (_wfopen_s(&file, Path().c_str(), L"wb") || !file)
            snprintf(reply, size, "error: cannot create CPSMapTrace.jsonl");
        else {
            started = GetTickCount64(); count = 0;
            if (fprintf(file, "{\"event\":\"capture_start\",\"track_id\":%d,\"format\":1}\n", ActiveTrack()) < 0 || fflush(file)) {
                fclose(file); file = nullptr;
                snprintf(reply, size, "error: cannot write transition capture header");
            } else snprintf(reply, size, "trace started: manually change maps in game, then trace stop");
        }
    } else if (!strcmp(args, "stop")) {
        bool ok = true;
        if (file) { ok = fclose(file) == 0; file = nullptr; }
        snprintf(reply, size, ok ? "trace stopped: %u transitions in CPSMapTrace.jsonl" :
            "error: cannot flush CPSMapTrace.jsonl", count);
    } else if (!strcmp(args, "status"))
        snprintf(reply, size, "trace ready=%d recording=%d transitions=%u", (int)ready, file != nullptr, count);
    else snprintf(reply, size, "error: usage: trace start | stop | status");
    LeaveCriticalSection(&g_cs);
}
}
