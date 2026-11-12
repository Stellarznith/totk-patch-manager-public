// One running game per executable directory and profile. Probe modes never take these locks.
#pragma once
#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <stdint.h>
#include <wchar.h>

static HANDLE rt_instance_locks[2];
static int rt_instance_lock_path(const wchar_t *input,int slot) {
    wchar_t path[32768];
    DWORD n=GetFullPathNameW(input,32768,path,NULL);
    if(!n||n>=32768)return -1;
    while(n>3&&(path[n-1]==L'\\'||path[n-1]==L'/'))path[--n]=0;
    for(DWORD i=0;i<n;i++)if(path[i]==L'/')path[i]=L'\\';
    CharLowerBuffW(path,n);
    uint64_t hash=14695981039346656037ull;
    for(DWORD i=0;i<n;i++){hash^=(uint16_t)path[i];hash*=1099511628211ull;}
    wchar_t name[96];
    swprintf(name,96,L"Local\\TotKRecompRun-%d-%016llx",slot,(unsigned long long)hash);
    HANDLE handle=CreateMutexW(NULL,FALSE,name);
    if(!handle)return -1;
    if(GetLastError()==ERROR_ALREADY_EXISTS){CloseHandle(handle);return 0;}
    rt_instance_locks[slot]=handle;
    return 1;
}
static int rt_instance_acquire(const char *profile) {
    wchar_t exe[32768],wide_profile[32768];
    DWORD n=GetModuleFileNameW(NULL,exe,32768);
    if(!n||n>=32768)return -1;
    while(n&&exe[n-1]!=L'\\'&&exe[n-1]!=L'/')--n;
    if(!n)return -1;
    exe[n]=0;
    if(!MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,profile,-1,wide_profile,32768))return -1;
    int result=rt_instance_lock_path(exe,0);
    if(result>0)result=rt_instance_lock_path(wide_profile,1);
    if(result<=0){for(int i=0;i<2;i++)if(rt_instance_locks[i]){CloseHandle(rt_instance_locks[i]);rt_instance_locks[i]=NULL;}}
    return result;
}
#else
static int rt_instance_acquire(const char *profile) { (void)profile;return 1; }
#endif
