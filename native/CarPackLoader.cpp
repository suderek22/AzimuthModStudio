// CarPackLoader - loads car packs into the game (FH2 Mod Platform 2.8.0).
// Ships with every pack: <this dll folder>\CarPacks\<pack>\{pack.sql,strings.txt}.
// Studio-only features (crash guard, title skip, time/weather, console) live in CarPackStudio.dll.
#include "fh2mod.h"
#include <windows.h>
#include <string>
#include <fstream>
#include <sstream>
#include <atomic>
#include <cstdio>
#include <cstdarg>

static const FH2ModApi* g_api;
static FH2Mod* g_mod;
static std::wstring g_dir;
static std::atomic<int> g_loadedPacks{0}, g_loadedCars{0};

static std::wstring U2W(const std::string& s) {
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), 0, 0);
    std::wstring w(n, 0);
    MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), &w[0], n);
    return w;
}
static std::string W2U(const std::wstring& w) {
    if (w.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, w.data(), (int)w.size(), 0, 0, 0, 0);
    std::string s(n, 0);
    WideCharToMultiByte(CP_UTF8, 0, w.data(), (int)w.size(), &s[0], n, 0, 0);
    return s;
}
static std::string ReadAll(const std::wstring& p) {
    std::ifstream f(p, std::ios::binary);
    std::stringstream ss; ss << f.rdbuf();
    std::string s = ss.str();
    if (s.size() >= 3 && (unsigned char)s[0] == 0xEF && (unsigned char)s[1] == 0xBB) s.erase(0, 3);
    return s;
}
static void Log(const char* fmt, ...) {
    char buf[1024];
    va_list va; va_start(va, fmt); vsnprintf(buf, sizeof buf, fmt, va); va_end(va);
    if (g_api && g_mod) g_api->log(g_mod, buf);
}

static void LoadPack(const std::wstring& packDir, const std::wstring& name) {
    std::string sql = ReadAll(packDir + L"\\pack.sql");
    if (!sql.empty()) g_api->add_sql(g_mod, sql.c_str());
    std::stringstream ss(ReadAll(packDir + L"\\strings.txt"));
    std::string line; int ns = 0;
    while (std::getline(ss, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        size_t a = line.find('|'), b = line.find('|', a + 1);
        if (a == std::string::npos || b == std::string::npos) continue;
        std::string table = line.substr(0, a);
        int key = atoi(line.substr(a + 1, b - a - 1).c_str());
        g_api->set_string(g_mod, table.c_str(), (uint16_t)key, U2W(line.substr(b + 1)).c_str());
        ns++;
    }
    int cars = 0;
    for (size_t p = 0; (p = sql.find("INSERT INTO Data_Car (", p)) != std::string::npos; p++) cars++;
    g_loadedCars += cars; g_loadedPacks++;
    Log("pack '%s': %d car(s), %d string(s)", W2U(name).c_str(), cars, ns);
}

static void CmdHandler(void*, const char* name, const char*, char* reply, size_t size) {
    char b[128];
    if (!strcmp(name, "status")) snprintf(b, sizeof b, "packs=%d cars=%d", g_loadedPacks.load(), g_loadedCars.load());
    else snprintf(b, sizeof b, "error: unknown command");
    strncpy_s(reply, size, b, _TRUNCATE);
}

FH2MOD_EXPORT int fh2mod_init(const FH2ModApi* api) {
    g_api = api;
    FH2ModInfo info = {};
    info.size = sizeof info;
    info.name = "CarPackLoader";
    info.version = "1.1.0";
    info.platform_version = "2.8.0";
    info.description = "Car Pack Studio: loads car packs (SQL and strings) into the game.";
    g_mod = api->register_mod(&info);
    if (!g_mod) return 0;

    wchar_t path[MAX_PATH];
    HMODULE self = 0;
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       (LPCWSTR)&fh2mod_init, &self);
    GetModuleFileNameW(self, path, MAX_PATH);
    g_dir = path; g_dir.resize(g_dir.find_last_of(L'\\'));

    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW((g_dir + L"\\CarPacks\\*").c_str(), &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            if ((fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) && fd.cFileName[0] != L'.')
                LoadPack(g_dir + L"\\CarPacks\\" + fd.cFileName, fd.cFileName);
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
    api->add_command(g_mod, "status", "loaded pack/car counters", CmdHandler, nullptr);
    Log("CarPackLoader ready: %d pack(s), %d car(s)", g_loadedPacks.load(), g_loadedCars.load());
    return 0;
}
