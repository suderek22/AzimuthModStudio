#include <d3d12.h>

namespace StudioPipelines {
static std::atomic<bool> enabled{false}, deviceSeen{false};
static std::atomic<unsigned long> events{0};
static std::atomic<bool> hookedGraphics{false}, hookedCompute{false}, hookedStream{false}, hookedLibrary{false};
static HRESULT (WINAPI* originalDevice)(IUnknown*, D3D_FEATURE_LEVEL, REFIID, void**);
static HRESULT (STDMETHODCALLTYPE* originalGraphics)(ID3D12Device*, const D3D12_GRAPHICS_PIPELINE_STATE_DESC*, REFIID, void**);
static HRESULT (STDMETHODCALLTYPE* originalCompute)(ID3D12Device*, const D3D12_COMPUTE_PIPELINE_STATE_DESC*, REFIID, void**);
static HRESULT (STDMETHODCALLTYPE* originalStream)(ID3D12Device2*, const D3D12_PIPELINE_STATE_STREAM_DESC*, REFIID, void**);
static HRESULT (STDMETHODCALLTYPE* originalLibrary)(ID3D12Device1*, const void*, SIZE_T, REFIID, void**);
static HRESULT (STDMETHODCALLTYPE* originalLoadGraphics)(ID3D12PipelineLibrary*, LPCWSTR, const D3D12_GRAPHICS_PIPELINE_STATE_DESC*, REFIID, void**);
static HRESULT (STDMETHODCALLTYPE* originalLoadCompute)(ID3D12PipelineLibrary*, LPCWSTR, const D3D12_COMPUTE_PIPELINE_STATE_DESC*, REFIID, void**);
static HRESULT (STDMETHODCALLTYPE* originalLoadStream)(ID3D12PipelineLibrary1*, LPCWSTR, const D3D12_PIPELINE_STATE_STREAM_DESC*, REFIID, void**);
static std::atomic<bool> hookedLoadGraphics{false}, hookedLoadCompute{false}, hookedLoadStream{false};
static bool armed = false;
static std::atomic<bool> probedLibrary{false};

static void Event(const char* operation, ULONGLONG start, HRESULT hr, size_t bytes) {
    if (!enabled) return;
    ++events;
    Log("[AzimuthPipeline] thread=%lu %s bytes=%zu elapsed_ms=%llu hr=0x%08lx%s",
        GetCurrentThreadId(), operation, bytes, GetTickCount64() - start, hr, FAILED(hr) ? " ERROR" : "");
}
static bool Hook(std::atomic<bool>& installed, const char* name, void* target, void* replacement, void** original) {
    if (installed.exchange(true)) return true;
    FH2Hook* hook = g_api->hook_function(g_mod, name, target, replacement, original);
    if (hook && g_api->enable_hooks(g_mod, hook)) return true;
    installed = false;
    Log("[AzimuthPipeline] ERROR cannot install %s", name);
    return false;
}
static HRESULT STDMETHODCALLTYPE Graphics(ID3D12Device* self, const D3D12_GRAPHICS_PIPELINE_STATE_DESC* desc, REFIID iid, void** out) {
    ULONGLONG start = GetTickCount64();
    HRESULT hr = originalGraphics(self, desc, iid, out);
    Event("CreateGraphicsPipelineState", start, hr, desc ? desc->VS.BytecodeLength + desc->PS.BytecodeLength : 0);
    return hr;
}
static HRESULT STDMETHODCALLTYPE Compute(ID3D12Device* self, const D3D12_COMPUTE_PIPELINE_STATE_DESC* desc, REFIID iid, void** out) {
    ULONGLONG start = GetTickCount64();
    HRESULT hr = originalCompute(self, desc, iid, out);
    Event("CreateComputePipelineState", start, hr, desc ? desc->CS.BytecodeLength : 0);
    return hr;
}
static HRESULT STDMETHODCALLTYPE Stream(ID3D12Device2* self, const D3D12_PIPELINE_STATE_STREAM_DESC* desc, REFIID iid, void** out) {
    ULONGLONG start = GetTickCount64();
    HRESULT hr = originalStream(self, desc, iid, out);
    Event("CreatePipelineState", start, hr, desc ? desc->SizeInBytes : 0);
    return hr;
}
static HRESULT STDMETHODCALLTYPE LoadGraphics(ID3D12PipelineLibrary* self, LPCWSTR name, const D3D12_GRAPHICS_PIPELINE_STATE_DESC* desc, REFIID iid, void** out) {
    ULONGLONG start = GetTickCount64();
    HRESULT hr = originalLoadGraphics(self, name, desc, iid, out);
    Event("LoadGraphicsPipeline (cache)", start, hr, desc ? desc->VS.BytecodeLength + desc->PS.BytecodeLength : 0);
    return hr;
}
static HRESULT STDMETHODCALLTYPE LoadCompute(ID3D12PipelineLibrary* self, LPCWSTR name, const D3D12_COMPUTE_PIPELINE_STATE_DESC* desc, REFIID iid, void** out) {
    ULONGLONG start = GetTickCount64();
    HRESULT hr = originalLoadCompute(self, name, desc, iid, out);
    Event("LoadComputePipeline (cache)", start, hr, desc ? desc->CS.BytecodeLength : 0);
    return hr;
}
static HRESULT STDMETHODCALLTYPE LoadStream(ID3D12PipelineLibrary1* self, LPCWSTR name, const D3D12_PIPELINE_STATE_STREAM_DESC* desc, REFIID iid, void** out) {
    ULONGLONG start = GetTickCount64();
    HRESULT hr = originalLoadStream(self, name, desc, iid, out);
    Event("LoadPipeline (cache)", start, hr, desc ? desc->SizeInBytes : 0);
    return hr;
}
static HRESULT STDMETHODCALLTYPE Library(ID3D12Device1* self, const void* blob, SIZE_T length, REFIID iid, void** out) {
    ULONGLONG start = GetTickCount64();
    HRESULT hr = originalLibrary(self, blob, length, iid, out);
    Event("CreatePipelineLibrary", start, hr, length);
    if (SUCCEEDED(hr) && out && *out) {
        ID3D12PipelineLibrary* library = nullptr;
        if (SUCCEEDED(((IUnknown*)*out)->QueryInterface(IID_PPV_ARGS(&library)))) {
            void** vt = *(void***)library;
            Hook(hookedLoadGraphics, "D3D12 LoadGraphicsPipeline", vt[9], (void*)LoadGraphics, (void**)&originalLoadGraphics);
            Hook(hookedLoadCompute, "D3D12 LoadComputePipeline", vt[10], (void*)LoadCompute, (void**)&originalLoadCompute);
            ID3D12PipelineLibrary1* newer = nullptr;
            if (SUCCEEDED(library->QueryInterface(IID_PPV_ARGS(&newer)))) {
                Hook(hookedLoadStream, "D3D12 LoadPipeline", (*(void***)newer)[13], (void*)LoadStream, (void**)&originalLoadStream);
                newer->Release();
            }
            library->Release();
        }
    }
    return hr;
}
static HRESULT WINAPI Device(IUnknown* adapter, D3D_FEATURE_LEVEL level, REFIID iid, void** out) {
    HRESULT hr = originalDevice(adapter, level, iid, out);
    if (SUCCEEDED(hr) && out && *out) {
        ID3D12Device* device = nullptr;
        if (SUCCEEDED(((IUnknown*)*out)->QueryInterface(IID_PPV_ARGS(&device)))) {
            deviceSeen = true;
            void** vt = *(void***)device;
            Hook(hookedGraphics, "D3D12 CreateGraphicsPipelineState", vt[10], (void*)Graphics, (void**)&originalGraphics);
            Hook(hookedCompute, "D3D12 CreateComputePipelineState", vt[11], (void*)Compute, (void**)&originalCompute);
            ID3D12Device1* one = nullptr;
            if (SUCCEEDED(device->QueryInterface(IID_PPV_ARGS(&one)))) {
                Hook(hookedLibrary, "D3D12 CreatePipelineLibrary", (*(void***)one)[44], (void*)Library, (void**)&originalLibrary);
                if (!probedLibrary.exchange(true) && hookedLibrary) {
                    ID3D12PipelineLibrary* probe = nullptr;
                    HRESULT probed = one->CreatePipelineLibrary(nullptr, 0, IID_PPV_ARGS(&probe));
                    if (probe) probe->Release();
                    if (FAILED(probed))
                        Log("[AzimuthPipeline] pipeline-library probe unavailable hr=0x%08lx", probed);
                }
                one->Release();
            }
            ID3D12Device2* two = nullptr;
            if (SUCCEEDED(device->QueryInterface(IID_PPV_ARGS(&two)))) {
                Hook(hookedStream, "D3D12 CreatePipelineState", (*(void***)two)[47], (void*)Stream, (void**)&originalStream);
                two->Release();
            }
            device->Release();
            Log("[AzimuthPipeline] D3D12 device observed; pipeline hooks installed");
        }
    }
    return hr;
}
static void Install() {
    HMODULE library = LoadLibraryW(L"d3d12.dll");
    void* target = library ? (void*)GetProcAddress(library, "D3D12CreateDevice") : nullptr;
    FH2Hook* hook = target ? g_api->hook_function(g_mod, "D3D12CreateDevice pipeline diagnostics", target,
        (void*)Device, (void**)&originalDevice) : nullptr;
    armed = hook && g_api->enable_hooks(g_mod, hook);
    Log(armed ? "[AzimuthPipeline] device-creation hook armed" : "[AzimuthPipeline] ERROR cannot arm pipeline tracing");
    // D3D12 devices are process/adapter singletons. A temporary reference also discovers
    // methods when the host created its device before mod initialization.
    if (armed) {
        ID3D12Device* probe = nullptr;
        HRESULT hr = ((decltype(originalDevice))target)(nullptr, D3D_FEATURE_LEVEL_11_0, IID_PPV_ARGS(&probe));
        if (probe) probe->Release();
        if (FAILED(hr)) Log("[AzimuthPipeline] ERROR device discovery hr=0x%08lx", hr);
    }
}
static void Command(const char* args, char* reply, size_t size) {
    if (!strcmp(args, "off")) {
        enabled = false;
        snprintf(reply, size, "Azimuth pipeline diagnostics disabled");
    } else if (!strcmp(args, "on")) {
        enabled = armed;
        snprintf(reply, size, !armed ? "error: pipeline hook unavailable" :
            !deviceSeen ? "Azimuth pipeline hook armed, but no D3D12 device observed; restart may be required" :
            "Azimuth D3D12 pipeline tracing enabled (creation/cache loads, thread, duration, HRESULT)");
    } else if (!strcmp(args, "status")) snprintf(reply, size, "pipeline armed=%d device=%d enabled=%d events=%lu",
        (int)armed, (int)deviceSeen.load(), (int)enabled.load(), events.load());
    else snprintf(reply, size, "error: usage: pipelines on | off | status");
}
}
