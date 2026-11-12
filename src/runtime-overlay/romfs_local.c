// RomFS directory traversal adapted from MacNativeGames tools/extract_romfs.py:index_romfs,
// b75be8cfa704df3701e86130c8ced268e79d6b11 (MIT, Copyright 2026 MacNativeGames contributors).
// Build an index from this player's actual image, without developer-machine image identities.
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "host.h"
#include "platform/platform.h"
#include "romfs_path.h"

typedef struct { uint64_t offset,size; uint32_t name,len; } LocalRow;
typedef struct { LocalRow row; char *path; } LocalFile;
typedef struct { uint32_t offset; char *parent; } LocalDir;
static void local_need(int ok,const char *what){if(!ok)rt_fatal("Local RomFS: %s",what);}
static uint32_t u32(const void *p){uint32_t n;memcpy(&n,p,4);return n;}
static uint64_t u64(const void *p){uint64_t n;memcpy(&n,p,8);return n;}
static void *table(PlatFile *image,uint64_t offset,uint64_t size,uint64_t length){
    local_need(offset<=length&&size<=length-offset&&size<=128u*1024*1024,"table outside image or too large");
    uint8_t *p=malloc((size_t)size+1);local_need(p!=NULL,"table allocation");
    local_need(plat_file_pread(image,p,(uint32_t)size,offset)==(int64_t)size,"truncated table");return p;
}
static char *part(const uint8_t *data,uint64_t size,uint32_t at,uint32_t n){
    local_need(at<=size&&n<=size-at,"name outside table");char *s=malloc((size_t)n+1);local_need(s!=NULL,"name allocation");
    memcpy(s,data+at,n);s[n]=0;local_need(strlen(s)==n&&!strchr(s,'/')&&!strchr(s,'\\')&&strcmp(s,".")&&strcmp(s,".."),"unsafe name");return s;
}
static char *path_join(const char *a,const char *b,int directory){
    size_t an=strlen(a),bn=strlen(b);local_need(an+bn<32768,"path too long");
    char *p=malloc(an+bn+2);local_need(p!=NULL,"path allocation");memcpy(p,a,an);memcpy(p+an,b,bn);p[an+bn]=0;
    if(directory&&bn){p[an+bn]='/';p[an+bn+1]=0;}return p;
}
static int by_path(const void *a,const void *b){return strcmp(((const LocalFile *)a)->path,((const LocalFile *)b)->path);}

uint8_t *romfs_index_from_image(PlatFile *image,uint64_t length,size_t *output_size){
    uint64_t h[10];if(length<sizeof h||plat_file_pread(image,h,sizeof h,0)!=sizeof h||h[0]!=80)return NULL;
    for(int i=1;i<9;i+=2)local_need(h[i]<=length&&h[i+1]<=length-h[i],"header bounds");
    local_need(h[9]<=length&&h[4]>=24,"data or directory bounds");
    uint8_t *dirs=table(image,h[3],h[4],length),*entries=table(image,h[7],h[8],length);
    uint8_t *seen_dirs=calloc((size_t)(h[4]/4+1),1),*seen_files=calloc((size_t)(h[8]/4+1),1);
    local_need(seen_dirs&&seen_files,"visited allocation");
    size_t nd=1,dc=256,nf=0,fc=4096;LocalDir *todo=malloc(dc*sizeof *todo);LocalFile *files=malloc(fc*sizeof *files);
    local_need(todo&&files,"index allocation");todo[0]=(LocalDir){0,strdup("")};
    while(nd){
        LocalDir d=todo[--nd];uint32_t off=d.offset;
        local_need(!(off&3)&&off<=h[4]-24&&!seen_dirs[off/4],"invalid or cyclic directory");seen_dirs[off/4]=1;
        const uint8_t *e=dirs+off;uint32_t sibling=u32(e+4),child=u32(e+8),file=u32(e+12),n=u32(e+20);
        char *name=part(dirs,h[4],off+24,n),*prefix=path_join(d.parent,name,1);free(name);
        if(nd+2>dc){dc*=2;todo=realloc(todo,dc*sizeof *todo);local_need(todo!=NULL,"directory growth");}
        if(sibling!=UINT32_MAX)todo[nd++]=(LocalDir){sibling,strdup(d.parent)};
        if(child!=UINT32_MAX)todo[nd++]=(LocalDir){child,strdup(prefix)};
        free(d.parent);
        while(file!=UINT32_MAX){
            local_need(h[8]>=32&&!(file&3)&&file<=h[8]-32&&!seen_files[file/4],"invalid or cyclic file");seen_files[file/4]=1;
            e=entries+file;uint64_t at=u64(e+8),size=u64(e+16);n=u32(e+28);
            name=part(entries,h[8],file+32,n);local_need(*name!=0,"empty filename");char *path=path_join(prefix,name,0);free(name);
            local_need(romfs_path_valid_relative(path)&&at<=length-h[9]&&size<=length-h[9]-at,"file outside image");
            if(nf==fc){fc*=2;local_need(fc<=1000000,"too many files");files=realloc(files,fc*sizeof *files);local_need(files!=NULL,"file growth");}
            files[nf++]=(LocalFile){{h[9]+at,size,0,(uint32_t)strlen(path)+1},path};file=u32(e+4);
        }
        free(prefix);
    }
    free(dirs);free(entries);free(seen_dirs);free(seen_files);free(todo);qsort(files,nf,sizeof *files,by_path);
    size_t strings=0;for(size_t i=0;i<nf;i++){local_need(!i||strcmp(files[i-1].path,files[i].path),"duplicate filename");strings+=files[i].row.len;}
    uint64_t names=24+nf*24;local_need(names+strings<128u*1024*1024&&nf>0,"index size");
    *output_size=(size_t)names+strings;uint8_t *output=malloc(*output_size);local_need(output!=NULL,"output allocation");
    memcpy(output,"TOTKRFS1",8);uint64_t count=nf;memcpy(output+8,&count,8);memcpy(output+16,&names,8);
    strings=0;for(size_t i=0;i<nf;i++){files[i].row.name=(uint32_t)strings;memcpy(output+24+i*24,&files[i].row,24);memcpy(output+names+strings,files[i].path,files[i].row.len);strings+=files[i].row.len;free(files[i].path);}
    free(files);rt_log("RomFS: indexed this installation's image (%llu files)",(unsigned long long)nf);return output;
}
