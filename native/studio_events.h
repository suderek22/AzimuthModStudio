#include <algorithm>
#include <vector>

namespace StudioEvents {
static std::vector<void*> components;
static void* (*originalConstruct)(void*);
static void (*originalDestroy)(void*);
static StateFn originalStartRace;
static StateFn originalOpenWorld, originalRaceMode;
static bool ready = false;
static int pending = -1, active = -1, sourceTrack = -1;
static bool racing = false, stopPending = false, returning = false, quitting = false;
static bool openWorld = false;
static ULONGLONG started = 0;
static const char* stage = "idle";

static void* Construct(void* self) {
    void* result = originalConstruct(self);
    EnterCriticalSection(&g_cs);
    components.push_back(self);
    LeaveCriticalSection(&g_cs);
    return result;
}
static void Destroy(void* self) {
    EnterCriticalSection(&g_cs);
    components.erase(std::remove(components.begin(), components.end(), self), components.end());
    LeaveCriticalSection(&g_cs);
    originalDestroy(self);
}
static uintptr_t StartRace(void* self) {
    uintptr_t result = originalStartRace(self);
    EnterCriticalSection(&g_cs);
    if (active >= 0) {
        if (openWorld) {
            returning = true;
            stage = "native free-roam game started; waiting for arrival";
            Log("event: race %d reached free-roam StartRace", active);
        } else {
            racing = true;
            stage = "racing";
            Log("event: race %d reached race-mode StartRace; current track=%d", active, ActiveTrack());
        }
    }
    LeaveCriticalSection(&g_cs);
    return result;
}
static uintptr_t OpenWorld(void* self) {
    uintptr_t result = originalOpenWorld(self);
    EnterCriticalSection(&g_cs);
    openWorld = true;
    LeaveCriticalSection(&g_cs);
    return result;
}
static uintptr_t RaceMode(void* self) {
    uintptr_t result = originalRaceMode(self);
    EnterCriticalSection(&g_cs);
    openWorld = false;
    LeaveCriticalSection(&g_cs);
    return result;
}
static bool ReadFlow(void* self) {
    // RaceToRaceComponent::TargetFlow is a property whose string value starts at +0xe8.
    uintptr_t text = (uintptr_t)self + 0xe8, length = 0, capacity = 0;
    if (!Rd(text + 0x10, &length, 8) || !Rd(text + 0x18, &capacity, 8) ||
        length != strlen("race_trigger_activation.activation_flow_race_to_race") || capacity < length)
        return false;
    if (capacity >= 16 && !Ptr(text, &text)) return false;
    char flow[96] = {};
    return Rd(text, flow, length) && !strcmp(flow, "race_trigger_activation.activation_flow_race_to_race");
}
static void Install(uintptr_t base) {
    const unsigned char ctor[] = {0x48,0x89,0x4c,0x24,0x08,0x53,0x48,0x83,0xec,0x70};
    const unsigned char dtor[] = {0x48,0x89,0x5c,0x24,0x08,0x48,0x89,0x74,0x24,0x10};
    const unsigned char launch[] = {0x48,0x8b,0xc4,0x89,0x50,0x10,0x55,0x57,0x41,0x56};
    const unsigned char quit[] = {0x48,0x83,0xec,0x28,0x48,0x8b,0x0d};
    const unsigned char start[] = {0x40,0x57,0x48,0x83,0xec,0x40};
    const unsigned char world[] = {0x48,0x83,0xec,0x28,0xe8,0xd7,0xf8,0x8e,0xff};
    const unsigned char race[] = {0x48,0x83,0xec,0x28,0xe8,0xa7,0xf0,0x8d,0xff};
    if (!Signature(base, 0x64ef30, ctor) || !Signature(base, 0x651020, dtor) ||
        !Signature(base, 0x671320, launch) || !Signature(base, 0x66a2b0, quit) ||
        !Signature(base, 0x93c6f0, start) || !Signature(base, 0x8ebb60, world) ||
        !Signature(base, 0x8fc390, race)) {
        Log("event: ERROR unsupported native event lifecycle; commands disabled");
        return;
    }
    FH2Hook* c = g_api->hook_function(g_mod, "Event transition component creation", (void*)(base + 0x64ef30),
        (void*)Construct, (void**)&originalConstruct);
    FH2Hook* d = g_api->hook_function(g_mod, "Event transition component destruction", (void*)(base + 0x651020),
        (void*)Destroy, (void**)&originalDestroy);
    FH2Hook* r = g_api->hook_function(g_mod, "Event race started", (void*)(base + 0x93c6f0),
        (void*)StartRace, (void**)&originalStartRace);
    FH2Hook* f = g_api->hook_function(g_mod, "Event open-world mode", (void*)(base + 0x8ebb60),
        (void*)OpenWorld, (void**)&originalOpenWorld);
    FH2Hook* m = g_api->hook_function(g_mod, "Event race mode", (void*)(base + 0x8fc390),
        (void*)RaceMode, (void**)&originalRaceMode);
    ready = c && d && r && f && m;
    Log("event: native lifecycle hooks %s", ready ? "installed" : "ERROR unavailable");
}
static bool Busy() {
    EnterCriticalSection(&g_cs);
    bool busy = pending >= 0 || active >= 0;
    LeaveCriticalSection(&g_cs);
    return busy;
}
static void Quit() {
    // The reflected RaceComponent::QuitRace method changes the native game state to quitting.
    ((void(*)(void*))(GameBase() + 0x66a2b0))(nullptr);
    quitting = true;
    stage = "native QuitRace requested; waiting for free roam";
    Log("event: invoked native RaceComponent::QuitRace for race %d", active);
}
static void Dispatch(void* pause) {
    EnterCriticalSection(&g_cs);
    if (active >= 0 && returning && TrackSettled() && ActiveTrack() == sourceTrack &&
        ((bool(*)(void*))(GameBase() + 0x963560))(pause)) {
        Log("event: race %d returned to free roam", active);
        active = -1;
        racing = stopPending = returning = quitting = false;
        stage = "complete; returned to free roam";
    }
    if (stopPending) {
        stopPending = false;
        Quit();
    }
    if (pending >= 0) {
        if (GetTickCount64() - started > 60000) {
            pending = -1;
            stage = "error: request expired before dispatch";
            Log("event: ERROR queued start expired");
        } else if (TrackSettled() && ActiveTrack() == sourceTrack &&
                   ((bool(*)(void*))(GameBase() + 0x963560))(pause)) {
            void* component = nullptr;
            for (void* candidate : components) {
                if (ReadFlow(candidate)) { component = candidate; break; }
            }
            if (!component) {
                pending = -1;
                stage = "error: free-roam race transition component unavailable";
                Log("event: ERROR no free-roam race transition component");
            } else {
                active = pending;
                pending = -1;
                racing = false;
                returning = false;
                stage = "native event activation dispatched";
                Log("event: launching race %d from track %d", active, sourceTrack);
                ((void(*)(void*, int))(GameBase() + 0x671320))(component, active);
            }
        }
    }
    LeaveCriticalSection(&g_cs);
}
}

static bool EventBusy() { return StudioEvents::Busy(); }
static bool ValidateEventId(int id, int* track, char* reply, size_t size);
static void CmdEvent(const char* args, char* reply, size_t size) {
    if (!strcmp(args, "status")) {
        EnterCriticalSection(&g_cs);
        Reply(reply, size, "event queued=%d active=%d racing=%d source=%d track=%d stage=%s elapsed_ms=%llu",
            StudioEvents::pending, StudioEvents::active, (int)StudioEvents::racing, StudioEvents::sourceTrack,
            ActiveTrack(), StudioEvents::stage,
            StudioEvents::started ? GetTickCount64() - StudioEvents::started : 0);
        LeaveCriticalSection(&g_cs);
        return;
    }
    if (!strcmp(args, "stop")) {
        EnterCriticalSection(&g_cs);
        if (!StudioEvents::racing || StudioEvents::active < 0)
            Reply(reply, size, "error: no Studio-started running race; stop is not a loading-screen bypass");
        else if (StudioEvents::stopPending || StudioEvents::quitting || StudioEvents::returning)
            Reply(reply, size, "error: event stop or return to free roam is already in progress");
        else {
            StudioEvents::stopPending = true;
            Reply(reply, size, "event stop queued: native QuitRace");
        }
        LeaveCriticalSection(&g_cs);
        return;
    }
    if (strncmp(args, "start ", 6)) {
        Reply(reply, size, "error: use event start <AnthemEventRaceId> | stop | status | list"); return;
    }
    const char* number = args + 6;
    errno = 0;
    char* end = nullptr;
    long parsed = strtol(number, &end, 10);
    while (end && (*end == ' ' || *end == '\t')) ++end;
    if (errno == ERANGE || end == number || !end || *end || parsed <= 0 || parsed > INT_MAX) {
        Reply(reply, size, "error: race ID must be a positive 32-bit integer from event list"); return;
    }
    int id = (int)parsed, track = -1;
    if (!StudioEvents::ready || !g_origWaitPause || !g_haveLoad) {
        Reply(reply, size, "error: event lifecycle hooks unavailable; start the updated wrapper in free roam"); return;
    }
    if (!ValidateEventId(id, &track, reply, size)) return;
    EnterCriticalSection(&g_cs);
    if (SpawnBusy()) {
        Reply(reply, size, "error: use spawn clear and wait for an empty spawn list before starting an event");
    } else if (EventBusy() || !StudioEvents::openWorld || !TrackSettled() || ActiveTrack() != track || g_trackPending >= 0 || g_trackActive >= 0 ||
        g_tpQueued || g_tpActive || g_swapPending >= 0 || g_swapActive >= 0) {
        Reply(reply, size, "error: load track %d and wait for settled free roam; another operation may be active", track);
    } else {
        StudioEvents::pending = id;
        StudioEvents::sourceTrack = track;
        StudioEvents::started = GetTickCount64();
        StudioEvents::stage = "queued; waiting for free roam";
        Reply(reply, size, "event start queued: race %d (experimental; native race progression/save rules apply)", id);
    }
    LeaveCriticalSection(&g_cs);
}
