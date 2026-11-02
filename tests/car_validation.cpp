#include "..\native\CarPackStudio.cpp"
#include <cassert>

int wmain(int argc, wchar_t** argv) {
    assert(argc == 2);
    FH2ModApi api{};
    api.game_dir = argv[1];
    g_api = &api;
    InitializeCriticalSection(&g_cs);
    char reply[1024]{};
    assert(ValidateCarId(333, reply, sizeof reply));
    assert(ValidateCarId(20000, reply, sizeof reply));
    assert(!ValidateCarId(999999, reply, sizeof reply));
    assert(strstr(reply, "does not exist") && strstr(reply, "no swap performed"));
    // ID 363 exists only in the base fixture, not the active override.
    assert(!ValidateCarId(363, reply, sizeof reply));
    std::wstring missing = std::wstring(argv[1]) + L"\\missing";
    api.game_dir = missing.c_str();
    assert(!ValidateCarId(333, reply, sizeof reply));
    assert(strstr(reply, "database was not found"));
    api.game_dir = argv[1];
    g_haveLoad = true;
    g_reloadFlowReady = true;
    g_origWaitPause = [](void*) -> uintptr_t { return 0; };
    g_swapCompleted = 333;
    for (const char* input : {"999999", "363", "0", "-1", "abc", "333 extra",
                              "999999999999999999999999999", "2147483648", ""}) {
        CmdCar(input, reply, sizeof reply);
        assert(!strncmp(reply, "error:", 6));
        assert(g_swapPending == -1 && g_swapActive == -1 && g_swapCompleted == 333);
    }
    CmdCar("20000", reply, sizeof reply);
    assert(!strcmp(reply, "swapping to car 20000") && g_swapPending == 20000);
    CmdCar("333", reply, sizeof reply);
    assert(strstr(reply, "already in progress") && g_swapPending == 20000);
    g_swapActive = g_swapPending.exchange(-1);
    g_swapSource = 333;
    g_swapStarted = g_swapProgressAt = GetTickCount64() - 61000;
    g_swapStage = "LoadPlayerCar: waiting for native streaming";
    g_swapJobState = 0;
    CheckSwapTimeout();
    assert(g_swapTimedOut && g_swapActive == 20000 && g_swapPending == -1);
    CmdCar("details", reply, sizeof reply);
    assert(strstr(reply, "source=333 target=20000"));
    assert(strstr(reply, "native_state=0 timed_out=1"));
    CmdCar("333", reply, sizeof reply);
    assert(strstr(reply, "already in progress") && g_swapActive == 20000);
    DeleteCriticalSection(&g_cs);
    puts("PASS: base/custom IDs, active database precedence, invalid IDs leave swap state unchanged, busy guard");
    return 0;
}
