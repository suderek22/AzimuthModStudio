#include <cmath>

namespace StudioSpawn {
static constexpr int MaxObjects = 4;
struct Shared {
    void* object = nullptr;
    void* control = nullptr;
    Shared() = default;
    Shared(const Shared&) = delete;
    Shared& operator=(const Shared&) = delete;
    Shared(Shared&& other) noexcept : object(other.object), control(other.control) {
        other.object = other.control = nullptr;
    }
    Shared& operator=(Shared&& other) noexcept {
        if (this != &other) {
            Reset();
            object = other.object;
            control = other.control;
            other.object = other.control = nullptr;
        }
        return *this;
    }
    void Reset() {
        if (!control) return;
        auto counts = (volatile LONG*)((char*)control + 8);
        auto table = *(void(***)(void*))control;
        if (!InterlockedDecrement(counts)) {
            table[0](control);
            if (!InterlockedDecrement(counts + 1)) table[1](control);
        }
        object = control = nullptr;
    }
    void Retain(const Shared& other) {
        if (other.control) InterlockedIncrement((volatile LONG*)((char*)other.control + 8));
        Reset();
        object = other.object;
        control = other.control;
    }
    ~Shared() { Reset(); }
};
struct NativeString {
    char buffer[16] = {};
    size_t length = 0, capacity = 15;
};
struct Entry {
    int id, carId, slot;
    void* job;
    int trackId = -1;
    uintptr_t entity = 0;
    void* driver = nullptr;
    bool paused = true, controlDirty = true;
    Shared owner;
    bool remove = false, reported = false, timedOut = false;
    ULONGLONG started = GetTickCount64();
    float target[3] = {};
    float forward[3] = {};
    float rotation[12] = {};
};
static std::vector<Entry> entries;
static int nextId = 1, pending = -1;
static bool pendingAi = false;
static std::string media;
static bool clear = false, ready = false;
static ULONGLONG queuedAt = 0;
static int possessionPending = -1, possessedId = 0;
static void* originalCar = nullptr;
static void* possessionPlayer = nullptr;
static int originalFreezeMode = 0;
static bool refreshHud = false;
static void Release(void* object);
static bool SetFreezeMode(void* car, int mode, int* previous = nullptr) {
    uintptr_t physics = 0, table = 0;
    if (!Ptr((uintptr_t)car + 0x4c08, &physics) || !Ptr(physics, &table) ||
        table != GameBase() + 0x2eb5e18 ||
        (previous && !Rd(physics - 0x1c, previous, sizeof *previous))) return false;
    ((void(*)(void*, int))(*(void***)physics)[12])((void*)physics, mode);
    return true;
}
static bool PointCamera(void* car) {
    refreshHud = true;
    uintptr_t holder = 0;
    if (!Ptr(GameBase() + 0x34aba60, &holder)) return false;
    void* world = ((void*(*)(void*))(GameBase() + 0x21dfc0))((void*)holder);
    if (!world) return false;
    void* cameras = ((void*(*)(void*))(GameBase() + 0x2e1520))(world);
    if (!cameras) return false;
    void* camera = ((void*(*)(void*, int))(GameBase() + 0xf9e6e0))(cameras, 0);
    if (!camera) return false;
    auto table = *(void***)camera;
    ((void(*)(void*, void**, bool))table[6])(camera, &car, true);
    ((void(*)(void*))table[10])(camera);
    return true;
}
static void (*originalHudUpdate)(void*) = nullptr;
static void HudUpdateHook(void* self) {
    EnterCriticalSection(&g_cs);
    if (refreshHud || originalCar) {
        void* car = PlayerCar();
        if (car) {
            struct WeakCar { void* object; void* token; };
            auto current = (WeakCar*)((char*)self + 0xe8);
            if (current->object != car) {
                WeakCar replacement = {car, nullptr};
                // Use the native weak-reference constructor; the HUD owns its token, not the car.
                ((void(*)(WeakCar*))(GameBase() + 0x32df80))(&replacement);
                if (replacement.object == car && replacement.token) {
                    std::swap(*current, replacement);
                    refreshHud = false;
                } else Log("spawn swap: ERROR HUD car weak reference unavailable");
                Release(replacement.token);
            } else refreshHud = false;
            Release(car);
        }
    }
    LeaveCriticalSection(&g_cs);
    originalHudUpdate(self);
}
static void RestorePossession() {
    if (!originalCar) return;
    uintptr_t target = 0;
    for (const auto& entry : entries)
        if (entry.id == possessedId) Ptr((uintptr_t)entry.job + 0x5a0, &target);
    ((void(*)(void*, int))(GameBase() + 0xf77130))(originalCar, 0);
    ((void(*)(void**, void*))(GameBase() + 0xfcdc50))(&originalCar, possessionPlayer);
    if (target) {
        ((void(*)(void*, int))(GameBase() + 0xf77130))((void*)target, 8);
        ((void(*)(void*, bool))(GameBase() + 0xf79f10))((void*)target, false);
    }
    if (!SetFreezeMode(originalCar, originalFreezeMode))
        Log("spawn swap: ERROR original physics unavailable during restore");
    if (!PointCamera(originalCar)) Log("spawn swap: ERROR camera unavailable during restore");
    Log("spawn swap: restored original player car from object %d", possessedId);
    Release(originalCar);
    Release(possessionPlayer);
    originalCar = possessionPlayer = nullptr;
    possessedId = 0;
}
static void DispatchPossession() {
    if (originalCar) {
        bool removing = clear || !StudioEvents::openWorld || !TrackSettled();
        bool found = false;
        for (const auto& entry : entries)
            if (entry.id == possessedId) {
                found = true;
                removing = removing || entry.remove || ActiveTrack() != entry.trackId ||
                    *(int*)((char*)entry.job + 0x598) != 2;
            }
        if (!found || removing || possessionPending >= 0) RestorePossession();
    }
    int id = possessionPending;
    possessionPending = -1;
    if (id <= 0) return;
    auto found = std::find_if(entries.begin(), entries.end(), [id](const Entry& entry) { return entry.id == id; });
    if (found == entries.end() || found->driver || !found->reported || found->remove || clear ||
        !StudioEvents::openWorld || !TrackSettled() || ActiveTrack() != found->trackId) {
        Log("spawn swap: ERROR object %d is not an available driverless car", id);
        return;
    }
    void* target = *(void**)((char*)found->job + 0x5a0);
    void* playerCar = PlayerCar();
    void* player = playerCar ? *(void**)((char*)playerCar + 0x4d00) : nullptr;
    if (!target || !player || target == playerCar || !SetFreezeMode(playerCar, 1, &originalFreezeMode)) {
        Log("spawn swap: ERROR original player car/driver unavailable");
        Release(playerCar);
        return;
    }
    (*(void(***)(void*))player)[2](player);
    // Keep the original car alive while the native binding removes its player association.
    originalCar = playerCar;
    possessionPlayer = player;
    possessedId = id;
    ((void(*)(void*, int))(GameBase() + 0xf77130))(playerCar, 8);
    ((void(*)(void*, int))(GameBase() + 0xf77130))(target, 0);
    ((void(*)(void**, void*))(GameBase() + 0xfcdc50))(&target, player);
    ((void(*)(void*, bool))(GameBase() + 0xf79f10))(target, true);
    if (!SetFreezeMode(target, 0) || !PointCamera(target)) {
        Log("spawn swap: ERROR target physics/camera unavailable; restoring original car");
        RestorePossession();
        return;
    }
    Log("spawn swap: player bound to driverless object %d (carID=%d)", id, found->carId);
}
static void (*originalConfigurePool)(void*, NativeString*, Shared*, int, int) = nullptr;
static void ConfigurePoolHook(void* self, NativeString* name, Shared* owner, int count, int cacheTarget) {
    const char* text = name->capacity < 16 ? name->buffer : *(const char**)name->buffer;
    if (name->length == 8 && !memcmp(text, "Non-LOD0", 8)) {
        uintptr_t begin = 0, end = 0;
        if (Rd((uintptr_t)self + 0x60, &begin, sizeof begin) &&
            Rd((uintptr_t)self + 0x68, &end, sizeof end) && end >= begin) {
            if (begin == end && count >= 0 && count <= 11) {
                count += MaxObjects;
            }
            // Native jobs retain slot addresses, so populated pools must never be resized.
            else if (end > begin) count = (int)((end - begin) / 0x590);
        }
    }
    originalConfigurePool(self, name, owner, count, cacheTarget);
}
static bool SetDriving(Entry& entry, uintptr_t car, void* entity) {
    uintptr_t behavior = 0, table = 0;
    if (!Ptr((uintptr_t)entity + 0x38, &behavior) || !Ptr(behavior, &table) ||
        table != GameBase() + 0x2f5de80) {
        Log("drivatar: ERROR car %d has no native festival route behavior", entry.id);
        return false;
    }
    if (!entry.paused) {
        // Mode zero has no route updater; unfreezing it alone cannot start autonomous driving.
        ((void(*)(void*, int))(GameBase() + 0x18e8320))((void*)behavior, 1);
    }
    ((void(*)(void*, bool))(GameBase() + 0xf79f10))((void*)car, !entry.paused);
    if (!SetFreezeMode((void*)car, entry.paused ? 1 : 0)) {
        Log("drivatar: ERROR car %d has no supported native physics interface", entry.id);
        return false;
    }
    entry.controlDirty = false;
    return true;
}
static void (*originalDeactivate)(void*) = nullptr;
static void DeactivateHook(void* self) {
    bool retained = false;
    EnterCriticalSection(&g_cs);
    for (const auto& entry : entries) {
        if (entry.entity == (uintptr_t)self && entry.reported && !entry.remove && !clear &&
            *(int*)((char*)entry.job + 0x598) == 2 &&
            StudioEvents::openWorld && TrackSettled() && ActiveTrack() == entry.trackId) {
            // Studio cars are explicitly removed; ordinary freeroam distance culling must not park them.
            *((unsigned char*)self + 0x92) = 0;
            *(float*)((char*)self + 0x94) = *(float*)(GameBase() + 0x36e6d40);
            *(float*)((char*)self + 0x98) = 1.f;
            retained = true;
            break;
        }
    }
    LeaveCriticalSection(&g_cs);
    if (!retained) originalDeactivate(self);
}
static void (*originalUpdate)(void*, float, int, int) = nullptr;
static void UpdateHook(void* self, float dt, int flags, int player) {
    uintptr_t carInterface = 0, car = 0;
    if (Ptr((uintptr_t)self + 0x10, &carInterface) && Ptr(carInterface + 0x50, &car)) {
        EnterCriticalSection(&g_cs);
        for (auto& entry : entries) {
            uintptr_t ownedCar = 0;
            if (entry.remove || clear ||
                *(int*)((char*)entry.job + 0x598) != 2 ||
                !Ptr((uintptr_t)entry.job + 0x5a0, &ownedCar) || ownedCar != car) continue;
            if (entry.reported) {
                if (entry.driver && entry.controlDirty) {
                    if (SetDriving(entry, car, self))
                        Log("drivatar: car %d native driving %s", entry.id, entry.paused ? "paused" : "playing");
                    else entry.remove = true;
                }
                break;
            }
            __declspec(align(16)) float position[4] = {entry.target[0], entry.target[1], entry.target[2], 1.f};
            __declspec(align(16)) float rotation[12];
            memcpy(rotation, entry.rotation, sizeof rotation);
            // Activate the owned AI entity as well as its physics; inactive entities are parked every tick.
            if (((bool(*)(void*, const float*, const float*, float, bool, bool))(GameBase() + 0x18ea1c0))(
                    self, position, rotation, 0.f, false, true)) {
                entry.reported = true;
                entry.entity = (uintptr_t)self;
                if (entry.driver) {
                    if (!SetDriving(entry, car, self)) entry.remove = true;
                }
                Log("spawn: car %d activated (carID=%d); %s", entry.id, entry.carId,
                    entry.driver ? "generic native AI attached" : "driverless dummy_car");
            } else {
                entry.remove = true;
                Log("spawn: ERROR native activation failed for car %d; removal queued", entry.id);
            }
            break;
        }
        LeaveCriticalSection(&g_cs);
    }
    originalUpdate(self, dt, flags, player);
}
static void Release(void* object) {
    if (object) (*(void(***)(void*))object)[3](object);
}
static void SetLoadTarget(void* module, int count) {
    auto group = (unsigned char*)module + 0xf0;
    uintptr_t begin = *(uintptr_t*)(group + 0x60), end = *(uintptr_t*)(group + 0x68);
    int capacity = (int)((end - begin) / 0x590);
    *(int*)(group + 0x94) = capacity - MaxObjects + count;
    ((void(*)(void*))(GameBase() + 0x487430))(group);
}
static bool Busy() {
    EnterCriticalSection(&g_cs);
    bool busy = pending >= 0 || !entries.empty() || possessionPending >= 0 || originalCar;
    LeaveCriticalSection(&g_cs);
    return busy;
}
static void Install(uintptr_t base) {
    struct Check { uintptr_t rva; unsigned char bytes[6]; };
    const Check checks[] = {
        {0x4814d0, {0x40,0x53,0x48,0x83,0xec,0x50}},
        {0x481160, {0x48,0x8b,0x02,0x45,0x33,0xc0}},
        {0xf52390, {0x48,0x89,0x4c,0x24,0x08,0x57}},
        {0xf54790, {0x48,0x89,0x4c,0x24,0x08,0x53}},
        {0x1226850, {0x40,0x55,0x56,0x57,0x48,0x8d}},
        {0x104d5f0, {0x44,0x89,0x44,0x24,0x18,0x48}},
        {0x8679f0, {0x40,0x53,0x48,0x83,0xec,0x30}},
        {0x1908670, {0x40,0x53,0x48,0x83,0xec,0x30}},
        {0x426e60, {0x40,0x53,0x48,0x83,0xec,0x30}},
        {0x19242b0, {0x8b,0x0d,0x22,0xa9,0xc2,0x01}},
        {0x12188e0, {0x48,0xc7,0x41,0x04,0xff,0xff}},
        {0x22c300, {0x48,0x89,0x5c,0x24,0x08,0x48}},
        {0x18ef250, {0x48,0x89,0x5c,0x24,0x08,0x48}},
        {0x18ea1c0, {0x48,0x89,0x5c,0x24,0x08,0x48}},
        {0x31f770, {0x48,0x8b,0xc4,0x57,0x41,0x56}},
        {0x32b0b0, {0x48,0x89,0x74,0x24,0x10,0x57}},
        {0xf79f10, {0x48,0x8b,0xc1,0x48,0x8b,0x89}},
        {0x18e8320, {0x48,0x89,0x5c,0x24,0x18,0x57}},
        {0x18d5300, {0x40,0x53,0x48,0x83,0xec,0x20}},
        {0x295940, {0x40,0x53,0x48,0x83,0xec,0x30}},
        {0x487430, {0x48,0x89,0x5c,0x24,0x08,0x48}},
        {0x47a360, {0x4c,0x89,0x44,0x24,0x18,0x56}},
        {0xfcdc50, {0x48,0x89,0x5c,0x24,0x18,0x48}},
        {0xf77130, {0x40,0x57,0x48,0x83,0xec,0x40}},
        {0x21dfc0, {0x48,0x8b,0x41,0x48,0xc3,0xcc}},
        {0x2e1520, {0x48,0x8b,0x81,0x98,0x00,0x00}},
        {0xf9e6e0, {0x48,0x8b,0x81,0xd0,0x02,0x00}},
        {0x783db0, {0x40,0x57,0x48,0x83,0xec,0x20}},
        {0x32df80, {0x40,0x53,0x48,0x83,0xec,0x20}},
    };
    for (const auto& check : checks) {
        if (!Signature(base, check.rva, check.bytes)) {
            Log("spawn: ERROR unsupported native lifecycle at RVA %llx; spawning disabled", check.rva);
            return;
        }
    }
    bool poolHook = g_api->hook_function(g_mod, "Reserve four Studio car slots at initialization",
        (void*)(base + 0x47a360), (void*)&ConfigurePoolHook, (void**)&originalConfigurePool) != nullptr;
    bool retainHook = poolHook && g_api->hook_function(g_mod, "Retain Studio cars until explicit removal",
        (void*)(base + 0x18d5300), (void*)&DeactivateHook, (void**)&originalDeactivate) != nullptr;
    ready = retainHook && g_api->hook_function(g_mod, "Activate Studio-owned cars", (void*)(base + 0x18ef250),
        (void*)&UpdateHook, (void**)&originalUpdate) != nullptr;
    ready = ready && g_api->hook_function(g_mod, "Retarget temporary possession HUD",
        (void*)(base + 0x783db0), (void*)&HudUpdateHook, (void**)&originalHudUpdate) != nullptr;
    if (!ready) {
        Log("spawn: ERROR native owned-car activation hook unavailable");
        return;
    }
    auto options = (unsigned char*)((void*(*)())(base + 0x295940))();
    int originalAi = *(int*)(options + 0x2538);
    *(int*)(options + 0x2538) = originalAi < 0 ? 11 - MaxObjects : (std::min)(originalAi, 11 - MaxObjects);
    Log("spawn: reserving four native AI indices by limiting ordinary AI to %d",
        *(int*)(options + 0x2538));
    Log("spawn: native dummy-car load/unload signatures verified");
}
static void* Module(void* pause, Shared& global, Shared& group, Shared& module) {
    uintptr_t base = GameBase();
    void* context = ((void*(*)(void*))(base + 0x19242b0))(pause);
    ((void(*)(void*, Shared*))(base + 0x8679f0))(context, &global);
    if (!global.object) return nullptr;
    ((void(*)(void*, Shared*))(base + 0x1908670))((char*)global.object + 0x28, &group);
    if (!group.object) return nullptr;
    ((void(*)(void*, Shared*))(base + 0x426e60))(group.object, &module);
    return module.object;
}
static bool Position(float* position, float* forward, float* matrix, int slot) {
    uintptr_t player = (uintptr_t)PlayerCar(), physics = 0, vt = 0;
    // The concrete physics interface stores position at -0x390 and a row-vector rotation at -0x380.
    if (!player || !Ptr(player + 0x4c08, &physics) || !Ptr(physics, &vt) ||
        vt != GameBase() + 0x2eb5e18 || !Rd(physics - 0x390, position, 16) ||
        !Rd(physics - 0x380, matrix, sizeof(float) * 12)) return false;
    for (int i = 0; i < 12; ++i) if (!std::isfinite(matrix[i])) return false;
    for (int i = 0; i < 3; ++i) {
        if (!std::isfinite(position[i])) return false;
        forward[i] = matrix[8 + i];
    }
    float length = forward[0]*forward[0] + forward[1]*forward[1] + forward[2]*forward[2];
    if (length < 0.9f || length > 1.1f) return false;
    float ahead = 8.f + (slot / 3) * 7.f;
    float side = slot % 3 == 0 ? 0.f : slot % 3 == 1 ? -6.f : 6.f;
    for (int i = 0; i < 3; ++i) position[i] += ahead * forward[i] + side * matrix[i];
    position[3] = 1.f;
    return true;
}
static void Dispatch(void* pause) {
    EnterCriticalSection(&g_cs);
    DispatchPossession();
    if (ready && (!entries.empty() || pending >= 0)) {
        for (auto it = entries.begin(); it != entries.end();) {
            int state = *(int*)((char*)it->job + 0x598);
            if (state != 3 && (!StudioEvents::openWorld || !TrackSettled() || ActiveTrack() != it->trackId)) {
                if (!it->remove) Log("spawn: removing car %d because free roam/map changed", it->id);
                it->remove = true;
            }
            if (!it->reported && !it->timedOut && GetTickCount64() - it->started > 90000) {
                it->timedOut = true;
                it->remove = true;
                Log("spawn: ERROR car %d loading timeout (state=%d); removing owned job", it->id, state);
            }
            if (state != 3 && (clear || it->remove)) {
                ((void(*)(void*, void**))(GameBase() + 0x481160))(it->owner.object, &it->job);
                state = *(int*)((char*)it->job + 0x598);
            }
            if (state == 3) {
                void* module = it->owner.object;
                int remaining = (int)std::count_if(entries.begin(), entries.end(),
                    [module, &it](const Entry& entry) { return entry.owner.object == module && &entry != &*it; });
                SetLoadTarget(module, remaining);
                Log("spawn: removed car object=%d carID=%d", it->id, it->carId);
                Release(it->job);
                if (it->driver) {
                    void* players = *(void**)(GameBase() + 0x34b3e18);
                    ((void(*)(void*, void*))(GameBase() + 0x32b0b0))(players, it->driver);
                    Release(it->driver);
                }
                it = entries.erase(it);
                continue;
            }
            ++it;
        }
        if (entries.empty()) clear = false;
        if (pending >= 0) {
            int carId = pending;
            pending = -1;
            Shared global, group, module;
            void* manager = Module(pause, global, group, module);
            if (!manager) {
                Log("spawn: ERROR native CarModule unavailable; no car created");
                LeaveCriticalSection(&g_cs);
                return;
            }
            int slot = 0;
            while (std::any_of(entries.begin(), entries.end(), [slot](const Entry& e) { return e.slot == slot; })) ++slot;
            __declspec(align(16)) float position[4] = {}, forward[4] = {}, rotation[12] = {};
            if (GetTickCount64() - queuedAt > 60000 || !StudioEvents::openWorld || EventBusy() ||
                !TrackSettled() || g_trackActive >= 0 || g_trackPending >= 0 || g_swapActive >= 0 ||
                g_swapPending >= 0 || g_tpActive || g_tpQueued || !Position(position, forward, rotation, slot)) {
                Log("spawn: ERROR player/free-roam state changed or transform unavailable; no car created");
                LeaveCriticalSection(&g_cs);
                return;
            }
            uintptr_t base = GameBase(), database = 0, dbPointer = 0;
            if (!Ptr(base + 0x354f4f8, &database) || !Ptr(database + 0x40, &dbPointer)) {
                Log("spawn: ERROR native database unavailable; no car created");
                LeaveCriticalSection(&g_cs);
                return;
            }
            __declspec(align(16)) unsigned char init[0x550] = {};
            ((void(*)(void*))(base + 0xf52390))(init);
            *(int*)(init + 8) = carId;
            *(int*)(init + 0x488) = pendingAi ? 1 : 8;
            void* driver = nullptr;
            if (pendingAi) {
                void* players = *(void**)(base + 0x34b3e18);
                ((void*(*)(void*, void**, int, int))(base + 0x31f770))(players, &driver, 0, 0);
                if (!driver) {
                    ((void(*)(void*))(base + 0xf54790))(init);
                    Log("spawn: ERROR native generic AI player creation failed");
                    LeaveCriticalSection(&g_cs);
                    return;
                }
                (*(void(***)(void*))driver)[2](driver);
                *(void**)(init + 0x280) = driver;
            }
            ((void(*)(void*, const char*, size_t))(base + 0x22c300))(init + 0x240, media.c_str(), media.size());
            void* db = (void*)dbPointer;
            (*(void(***)(void*))db)[2](db);
            ((void(*)(void*, void**, int))(base + 0x104d5f0))(init + 0x10, &db, carId);
            Release(db);
            unsigned char seed[24] = {};
            ((void*(*)(void*, int))(base + 0x12188e0))(seed, 0);
            ((void(*)(void*, int, void*, int))(base + 0x1226850))(init + 0x1b0, carId, seed, INT_MAX);
            memcpy(init + 0x2b0, position, 16);
            memcpy(init + 0x2c0, forward, 16);
            NativeString label;
            strcpy_s(label.buffer, "StudioSpawn");
            label.length = strlen(label.buffer);
            void* job = nullptr;
            SetLoadTarget(manager, (int)entries.size() + 1);
            ((void*(*)(void*, void**, void*, int, int, NativeString*))(base + 0x4814d0))(
                manager, &job, init, 1, 0, &label);
            ((void(*)(void*))(base + 0xf54790))(init);
            if (job) {
                entries.push_back({nextId++, carId, slot, job});
                entries.back().trackId = ActiveTrack();
                entries.back().driver = driver;
                entries.back().owner.Retain(module);
                memcpy(entries.back().target, position, sizeof entries.back().target);
                memcpy(entries.back().forward, forward, sizeof entries.back().forward);
                memcpy(entries.back().rotation, rotation, sizeof entries.back().rotation);
                Log("spawn: queued owned car object=%d carID=%d slot=%d position=%.3f,%.3f,%.3f forward=%.3f,%.3f,%.3f",
                    entries.back().id, carId, slot, position[0], position[1], position[2], forward[0], forward[1], forward[2]);
            } else {
                SetLoadTarget(manager, (int)entries.size());
                if (driver) {
                    void* players = *(void**)(base + 0x34b3e18);
                    ((void(*)(void*, void*))(base + 0x32b0b0))(players, driver);
                    Release(driver);
                }
                Log("spawn: ERROR native load did not return a job");
            }
        }
    }
    LeaveCriticalSection(&g_cs);
}
static bool ParseId(const char* args, int& id) {
    errno = 0;
    char* end = nullptr;
    long parsed = strtol(args, &end, 10);
    while (end && (*end == ' ' || *end == '\t')) ++end;
    if (errno == ERANGE || end == args || !end || *end || parsed <= 0 || parsed > INT_MAX) return false;
    id = (int)parsed;
    return true;
}
}
static bool SpawnBusy() { return StudioSpawn::Busy(); }
static void CmdSpawn(const char* args, char* reply, size_t size) {
    EnterCriticalSection(&g_cs);
    if (!strcmp(args, "swap") || !strncmp(args, "swap ", 5)) {
        const char* value = args[4] == ' ' ? args + 5 : "";
        int id = 0;
        if (!strcmp(value, "status")) {
            uintptr_t physics = 0;
            float position[3] = {};
            bool located = StudioSpawn::originalCar &&
                Ptr((uintptr_t)StudioSpawn::originalCar + 0x4c08, &physics) &&
                Rd(physics - 0x390, position, sizeof position);
            Reply(reply, size, "spawn swap object=%d queued=%d original=%d original_located=%d original_position=%.3f,%.3f,%.3f",
                StudioSpawn::possessedId, StudioSpawn::possessionPending, StudioSpawn::originalCar != nullptr,
                located, position[0], position[1], position[2]);
        } else if (strcmp(value, "reset") && !StudioSpawn::ParseId(value, id))
            Reply(reply, size, "error: use spawn swap <positive object ID>|reset|status");
        else {
            auto found = std::find_if(StudioSpawn::entries.begin(), StudioSpawn::entries.end(),
                [id](const StudioSpawn::Entry& entry) { return entry.id == id; });
            if (!StudioSpawn::ready || !g_origWaitPause)
                Reply(reply, size, "error: native spawn lifecycle unavailable; restart the updated wrapper");
            else if (StudioSpawn::possessionPending >= 0 || StudioSpawn::pending >= 0)
                Reply(reply, size, "error: wait for the pending spawn/swap operation");
            else if (id && (found == StudioSpawn::entries.end() || found->driver || found->remove ||
                          !found->reported || StudioSpawn::clear))
                Reply(reply, size, "error: swap requires an active driverless spawn car object, not spawn ai");
            else {
                StudioSpawn::possessionPending = id;
                Reply(reply, size, "spawn swap queued: %s", value);
            }
        }
    } else if (!strcmp(args, "status")) {
        int loaded = 0, active = 0, slots = 0, freeSlots = 0;
        for (const auto& entry : StudioSpawn::entries) {
            if (*(int*)((char*)entry.job + 0x598) == 2) ++loaded;
            if (entry.reported) ++active;
        }
        if (!StudioSpawn::entries.empty()) {
            uintptr_t group = (uintptr_t)StudioSpawn::entries.front().owner.object + 0xf0;
            uintptr_t begin = 0, end = 0;
            if (Ptr(group + 0x60, &begin) && Ptr(group + 0x68, &end) && end >= begin)
                slots = (int)((end - begin) / 0x590);
            Rd(group + 0x90, &freeSlots, sizeof freeSlots);
        }
        Reply(reply, size, "spawn objects=%zu loaded=%d activated=%d pending=%d limit=%d slots=%d free=%d",
            StudioSpawn::entries.size(), loaded, active, StudioSpawn::pending, StudioSpawn::MaxObjects, slots, freeSlots);
    } else if (!strcmp(args, "list") || !strncmp(args, "list ", 5)) {
        int page = 1;
        int pages = (std::max)(1, (int)((StudioSpawn::entries.size() + 7) / 8));
        if (strcmp(args, "list") && (!StudioSpawn::ParseId(args + 5, page) || page > pages)) {
            Reply(reply, size, "error: use spawn list <page 1-%d>", pages);
            LeaveCriticalSection(&g_cs);
            return;
        }
        char header[128];
        sprintf_s(header, "Studio spawned cars: %zu/%d, page %d/%d (use object ID for removal):",
            StudioSpawn::entries.size(), StudioSpawn::MaxObjects, page, pages);
        std::string result = header;
        for (size_t index = (page - 1) * 8; index < StudioSpawn::entries.size() && index < (size_t)page * 8; ++index) {
            const auto& entry = StudioSpawn::entries[index];
            char line[512];
            uintptr_t car = 0, physics = 0;
            float position[3] = {};
            float forward[3] = {};
            Ptr((uintptr_t)entry.job + 0x5a0, &car);
            bool located = car && Ptr(car + 0x4c08, &physics) && Rd(physics + 0xef0, position, sizeof position);
            bool oriented = physics && Rd(physics - 0x360, forward, sizeof forward);
            uintptr_t carInterface = 0, entityCar = 0;
            unsigned char active = 0;
            if (entry.entity && Ptr(entry.entity + 0x10, &carInterface) &&
                Ptr(carInterface + 0x50, &entityCar) && entityCar == car)
                Rd(entry.entity + 0x90, &active, sizeof active);
            float dot = oriented ? forward[0]*entry.forward[0] + forward[1]*entry.forward[1] + forward[2]*entry.forward[2] : 0.f;
            int resourceState = -1;
            uintptr_t begin = 0, end = 0;
            uintptr_t group = (uintptr_t)entry.owner.object + 0xf0;
            if (Ptr(group + 0x60, &begin) && Ptr(group + 0x68, &end) && end >= begin && end - begin <= 1024 * 0x590) {
                for (uintptr_t slot = begin; slot < end; slot += 0x590) {
                    uintptr_t job = 0, resource = 0;
                    if (Ptr(slot + 0x558, &job) && job == (uintptr_t)entry.job &&
                        Ptr(slot + 0x560, &resource)) {
                        Rd(resource + 0x20, &resourceState, sizeof resourceState);
                        break;
                    }
                }
            }
            int drivingDisabled = -1;
            int frozen = -1;
            float throttle = 0.f, speed = 0.f;
            if (physics) {
                Rd(physics - 0x1c, &frozen, sizeof frozen);
                Rd(physics + 0xdb4, &throttle, sizeof throttle);
                Rd(physics + 0xe28, &speed, sizeof speed);
            }
            if (car && entry.driver) Rd(car + 0x4d30, &drivingDisabled, sizeof drivingDisabled);
            sprintf_s(line, "\ncar %d carID=%d state=%d active=%d removing=%d located=%d position=%.3f,%.3f,%.3f target=%.3f,%.3f,%.3f facing=%.3f resource=%d ai=%s ai_pending=%d driving=%d possessed=%d frozen=%d throttle=%.3f speed=%.3f",
                entry.id, entry.carId, *(int*)((char*)entry.job + 0x598), (int)active, (int)(entry.remove || StudioSpawn::clear),
                (int)located, position[0], position[1], position[2], entry.target[0], entry.target[1], entry.target[2], dot,
                resourceState, entry.driver ? (entry.paused ? "paused" : "playing") : "none",
                (int)(entry.driver && entry.controlDirty), drivingDisabled < 0 ? -1 : !drivingDisabled,
                (int)(entry.id == StudioSpawn::possessedId), frozen, throttle, speed);
            if (result.size() + strlen(line) >= size) {
                result += "\nMore objects omitted; remove objects or use spawn clear.";
                break;
            }
            result += line;
        }
        Reply(reply, size, "%s", result.c_str());
    } else if (!StudioSpawn::ready || !g_origWaitPause) {
        Reply(reply, size, "error: native spawn lifecycle unavailable; restart the updated wrapper");
    } else if (!strcmp(args, "clear")) {
        StudioSpawn::pending = -1;
        StudioSpawn::possessionPending = StudioSpawn::originalCar ? 0 : -1;
        StudioSpawn::clear = !StudioSpawn::entries.empty();
        Reply(reply, size, "spawn clear queued: only Studio-owned objects");
    } else if (!strncmp(args, "remove car ", 11)) {
        int id = 0;
        auto& entries = StudioSpawn::entries;
        if (!StudioSpawn::ParseId(args + 11, id))
            Reply(reply, size, "error: use spawn remove car <positive object ID from spawn list>");
        else {
            auto entry = std::find_if(entries.begin(), entries.end(), [id](const StudioSpawn::Entry& e) { return e.id == id; });
            if (entry == entries.end()) Reply(reply, size, "error: no Studio-owned car with object ID %d", id);
            else {
                entry->remove = true;
                Reply(reply, size, "spawn remove queued: car %d", id);
            }
        }
    } else {
        int carId = 0;
        bool ai = !strncmp(args, "ai ", 3) || !strncmp(args, "drivatar ", 9);
        size_t prefix = !strncmp(args, "drivatar ", 9) ? 9 : ai ? 3 : 4;
        if ((!ai && strncmp(args, "car ", 4)) || !StudioSpawn::ParseId(args + prefix, carId))
            Reply(reply, size, "error: use spawn car|ai|drivatar <carID> | swap <object ID>|reset|status | remove car <object ID> | clear | list [page] | status");
        else if (EventBusy() || !StudioEvents::openWorld || !TrackSettled() || StudioSpawn::pending >= 0 ||
                 StudioSpawn::clear || StudioSpawn::possessionPending >= 0 || g_trackActive >= 0 || g_trackPending >= 0 ||
                 g_swapActive >= 0 || g_swapPending >= 0 || g_tpActive || g_tpQueued || !PlayerCar())
            Reply(reply, size, "error: wait for settled free roam and pending spawn/clear before spawning");
        else if (std::any_of(StudioSpawn::entries.begin(), StudioSpawn::entries.end(),
                 [](const StudioSpawn::Entry& entry) { return !entry.reported || entry.remove; }))
            Reply(reply, size, "error: wait for the previous car to finish loading/removing; spawn one car at a time");
        else if (StudioSpawn::entries.size() >= StudioSpawn::MaxObjects)
            Reply(reply, size, "error: spawn limit is %d cars; remove an object or use spawn clear", StudioSpawn::MaxObjects);
        else if (ValidateCarId(carId, reply, size, &StudioSpawn::media)) {
            StudioSpawn::pending = carId;
            StudioSpawn::pendingAi = ai;
            StudioSpawn::queuedAt = GetTickCount64();
            Reply(reply, size, "spawn car queued: carID=%d; inspect spawn list for the object ID", carId);
        }
    }
    LeaveCriticalSection(&g_cs);
}
static void CmdDrivatar(const char* args, char* reply, size_t size) {
    if (!_stricmp(args, "swap") || !_strnicmp(args, "swap ", 5)) {
        std::string command = "swap";
        if (args[4] == ' ') command += std::string(" ") + (args + 5);
        CmdSpawn(command.c_str(), reply, size);
        return;
    }
    std::string action(args);
    auto separator = action.find_first_of(" \t");
    int id = 0;
    if (separator != std::string::npos && !_stricmp(args + separator + 1, "swap")) {
        std::string object = action.substr(0, separator);
        if (!StudioSpawn::ParseId(object.c_str(), id))
            Reply(reply, size, "error: use drivatar <positive driverless object ID> swap");
        else CmdSpawn(("swap " + object).c_str(), reply, size);
        return;
    }
    if (separator != std::string::npos) {
        if (!StudioSpawn::ParseId(args + separator + 1, id)) {
            Reply(reply, size, "error: use drivatar pause|play|reset [positive object ID]");
            return;
        }
        action.resize(separator);
    }
    bool paused = !_stricmp(action.c_str(), "pause") || !_stricmp(action.c_str(), "pasue");
    if (!paused && _stricmp(action.c_str(), "play") && _stricmp(action.c_str(), "reset")) {
        Reply(reply, size, "error: use drivatar pause|play|reset [object ID]; follow/chase are not available yet");
        return;
    }
    EnterCriticalSection(&g_cs);
    int count = 0;
    for (auto& entry : StudioSpawn::entries) {
        if (!entry.driver || entry.remove || StudioSpawn::clear || (id && entry.id != id)) continue;
        entry.paused = paused;
        entry.controlDirty = true;
        ++count;
    }
    if (!count) Reply(reply, size, "error: no matching Studio-owned AI car; use spawn ai <carID> and spawn list");
    else Reply(reply, size, "drivatar %s queued: %d Studio-owned AI car(s)", paused ? "pause" : "play", count);
    LeaveCriticalSection(&g_cs);
}
