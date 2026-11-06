/*
 * Lanzador de PopSay (portable).
 * Busca runtime\pythonw.exe y app\run.pyw junto a este .exe y los ejecuta,
 * aislado de cualquier otro Python instalado (-E -s). No necesita instalación.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wchar.h>

#define BUF 4096

static void fail(const wchar_t *msg, const wchar_t *path) {
    wchar_t text[BUF];
    swprintf(text, BUF, L"%ls\n\n%ls\n\nVolvé a descomprimir la carpeta completa de PopSay.", msg, path);
    MessageBoxW(NULL, text, L"PopSay", MB_ICONERROR | MB_OK);
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, PWSTR args, int show) {
    (void)inst; (void)prev; (void)show;
    wchar_t self[BUF], dir[BUF], python[BUF], script[BUF], cmd[BUF * 2];

    DWORD n = GetModuleFileNameW(NULL, self, BUF);
    if (n == 0 || n >= BUF) return 1;
    wcscpy(dir, self);
    wchar_t *slash = wcsrchr(dir, L'\\');
    if (!slash) return 1;
    *slash = L'\0';

    swprintf(python, BUF, L"%ls\\runtime\\pythonw.exe", dir);
    swprintf(script, BUF, L"%ls\\app\\run.pyw", dir);
    if (GetFileAttributesW(python) == INVALID_FILE_ATTRIBUTES) { fail(L"Falta el Python incluido:", python); return 2; }
    if (GetFileAttributesW(script) == INVALID_FILE_ATTRIBUTES) { fail(L"Falta el programa:", script); return 2; }

    SetEnvironmentVariableW(L"POPSAY_LAUNCHER", self);   /* para "Iniciar con Windows" */
    swprintf(cmd, BUF * 2, L"\"%ls\" -E -s \"%ls\" %ls", python, script, args ? args : L"");

    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof si);
    si.cb = sizeof si;
    ZeroMemory(&pi, sizeof pi);
    if (!CreateProcessW(python, cmd, NULL, NULL, FALSE, 0, NULL, dir, &si, &pi)) {
        fail(L"No se pudo iniciar:", python);
        return 3;
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;
}
