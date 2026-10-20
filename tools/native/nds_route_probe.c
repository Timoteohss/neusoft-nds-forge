/* Isolated ROUTE reader for the exact libNdsLib.so recorded in HANDOFF.md.
 * No application attachment, database writes, or navigation state changes.
 * Invoke under timeout. Never use these offsets with another library hash.
 */
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { void **vtable; } Allocator;
static size_t allocated;
static void *allocate(Allocator *self, size_t n) {
    (void)self;
    if (n > 16*1024*1024 || allocated > 32*1024*1024-n) return NULL;
    allocated += n;
    return calloc(1,n);
}
static uint32_t u32(const void *p) { uint32_t v; memcpy(&v,p,4); return v; }
static int64_t i64(const void *p) { int64_t v; memcpy(&v,p,8); return v; }

int main(int argc, char **argv) {
    setbuf(stdout,NULL);
    if (argc != 5 && argc != 6) {
        fprintf(stderr,"usage: %s BLOB TILE MODEL_ENUM SHIFT [--full|--full-all]\n",argv[0]);
        return 2;
    }
    FILE *f=fopen(argv[1],"rb");
    if (!f) return 2;
    if(fseek(f,0,SEEK_END)) return 2;
    long size=ftell(f);
    if(size<2 || size>8*1024*1024) return 2;
    rewind(f);
    /* A guard area contains unchecked native byte readers on malformed input. */
    uint8_t *blob=calloc(1,(size_t)size+4096);
    if(!blob || fread(blob,1,(size_t)size,f)!=(size_t)size) return 2;
    fclose(f);
    void *lib=dlopen("libNdsLib.so",RTLD_NOW|RTLD_LOCAL);
    if(!lib) { fprintf(stderr,"%s\n",dlerror()); return 1; }
    void *open_sym=dlsym(lib,"nds_open");
    if(!open_sym) return 1;
    uintptr_t base=(uintptr_t)open_sym-0x927e0;
    uint32_t version[2]={(uint32_t)strtoul(argv[3],NULL,0),0};
    uint32_t tile=(uint32_t)strtoul(argv[2],NULL,0);
    uint32_t shift=(uint32_t)strtoul(argv[4],NULL,0);
    uint64_t bits[4]={0};
    void (*init)(void*,void*,size_t)=(void*)(base+0x1d8b18);
    int (*header)(void*,void*,void*)=(void*)(base+0xe5e80);
    uint32_t offsets[10]={0};
    init(bits,blob,(size_t)size);
    int ok=header(bits,version,offsets);
    printf("{\"header_ok\":%s,\"tile\":%u,\"bytes\":%ld,\"offsets\":[",ok?"true":"false",tile,size);
    for(int i=0;i<10;i++)printf("%s%u",i?",":"",offsets[i]);
    printf("]");
    unsigned limit=argc==6 && !strcmp(argv[5],"--full-all")?32768:32;
    if(argc==6 && (!strcmp(argv[5],"--full") || !strcmp(argv[5],"--full-all")) && ok) {
        uint64_t scratch[128]={0};
        void *vtable[]={allocate}; Allocator allocator={vtable};
        void *(*parse)(void*,uint32_t,uint32_t,uint32_t,uint32_t,void*,void*,void*)=(void*)(base+0x10e6a0);
        uint8_t *out=parse(blob,(uint32_t)size,tile,0,shift,scratch,version,&allocator);
        printf(",\"parsed\":%s,\"allocated\":%zu",out?"true":"false",allocated);
        if(out) {
            printf(",\"intersection_count\":%u,\"link_count\":%u,\"coordinate_bits\":%u,\"shift\":%u",
                   u32(out+0xf8),u32(out+0x110),out[0x4d],out[0x4e]);
            const unsigned fields[]={0xf0,0x108}; const unsigned strides[]={8,12};
            printf(",\"records\":[");
            for(int k=0;k<2;k++) {
                unsigned field=fields[k], stride=strides[k];
                unsigned count=u32(out+field+8);
                uint8_t *rows=out+field+i64(out+field);
                printf("%s[",k?",":"");
                for(unsigned i=0;i<count && i<limit;i++) {
                    printf("%s\"",i?",":"");
                    for(unsigned j=0;j<stride;j++)printf("%02x",rows[i*stride+j]);
                    printf("\"");
                }
                printf("]");
            }
            printf("]");
            int (*basic)(void*,uint16_t,uint32_t,void*,void*)=(void*)(base+0x11201c);
            unsigned basic_count=u32(out+0x110);
            printf(",\"link_basic\":[");
            for(unsigned i=0;i<basic_count && i<limit;i++) {
                uint64_t reader[4]={0}; init(reader,blob,(size_t)size);
                uint8_t attributes[128]={0};
                int valid=basic(out,(uint16_t)i,0,reader,attributes);
                printf("%s{\"ok\":%s,\"length_raw\":%u,\"road_access_bytes\":\"",
                       i?",":"",valid?"true":"false",u32(attributes+0x28));
                for(unsigned j=0x2f;j<0x34;j++)printf("%02x",attributes[j]);
                printf("\",\"scalars\":[%u,%u,%u]",attributes[0x2c],attributes[0x2d],attributes[0x2e]);
                memset(attributes,0,sizeof(attributes));
                init(reader,blob,(size_t)size);
                valid=basic(out,(uint16_t)i,1,reader,attributes);
                printf(",\"reverse_ok\":%s,\"reverse_road_access_bytes\":\"",valid?"true":"false");
                for(unsigned j=0x2f;j<0x34;j++)printf("%02x",attributes[j]);
                printf("\",\"reverse_scalars\":[%u,%u,%u]}",attributes[0x2c],attributes[0x2d],attributes[0x2e]);
            }
            printf("]");
            void (*seek)(void*,uint32_t)=(void*)(base+0x1d8b7c);
            uint32_t (*read_unsigned)(void*,uint32_t)=(void*)(base+0x1d9768);
            int32_t (*read_signed)(void*,uint32_t)=(void*)(base+0x1d9854);
            int32_t origin[2]; memcpy(origin,out+0x50,8);
            uint8_t *nodes=out+0xf0+i64(out+0xf0);
            unsigned count=u32(out+0xf8);
            printf(",\"nds_coordinates\":[");
            for(unsigned i=0;i<count && i<limit;i++) {
                uint32_t bit=u32(nodes+i*8)&0x1fffff;
                uint64_t reader[4]={0}; init(reader,blob,(size_t)size);
                seek(reader,offsets[1]+bit/8);
                if(bit%8)read_unsigned(reader,bit%8);
                int32_t x=read_signed(reader,out[0x4d]+1);
                int32_t y=read_signed(reader,out[0x4d]+1);
                printf("%s[%lld,%lld]",i?",":"",
                       (long long)origin[0]+(long long)x*(1LL<<shift),
                       (long long)origin[1]+(long long)y*(1LL<<shift));
            }
            printf("]");
        }
    }
    puts("}");
    return ok?0:1;
}
