/* Isolated RoutingGeoTile parser, exact library hash in ROUTING-PROGRESS.md.
 * No live application attachment. Caller must verify library SHA and use timeout.
 */
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef struct {void **vtable;} Allocator;
static size_t allocated;
static void *allocate(Allocator *self,size_t n) {
    (void)self;
    if(n>16*1024*1024 || allocated>32*1024*1024-n)return NULL;
    allocated+=n;return calloc(1,n);
}
static uint32_t u32(const void *p){uint32_t v;memcpy(&v,p,4);return v;}
static int64_t i64(const void *p){int64_t v;memcpy(&v,p,8);return v;}
int main(int argc,char **argv){
    setbuf(stdout,NULL);
    if(argc!=5 && argc!=6)return 2;
    FILE *f=fopen(argv[1],"rb");if(!f)return 2;
    if(fseek(f,0,SEEK_END))return 2;
    long size=ftell(f);if(size<17 || size>8*1024*1024)return 2;
    rewind(f);uint8_t *blob=calloc(1,(size_t)size+4096);
    if(!blob || fread(blob,1,(size_t)size,f)!=(size_t)size)return 2;
    fclose(f);
    void *lib=dlopen("libNdsLib.so",RTLD_NOW|RTLD_LOCAL);
    if(!lib){fprintf(stderr,"%s\n",dlerror());return 1;}
    void *sym=dlsym(lib,"nds_open");if(!sym)return 1;
    uintptr_t base=(uintptr_t)sym-0x927e0;
    uint32_t tile=strtoul(argv[2],NULL,0),version[]={strtoul(argv[3],NULL,0),0};
    uint32_t shift=strtoul(argv[4],NULL,0);
    uint32_t stride=argc==6?strtoul(argv[5],NULL,0):1;
    if(!stride)stride=1;
    uint64_t scratch[128]={0};void *vtable[]={allocate};Allocator allocator={vtable};
    uint8_t *(*parse)(void*,uint32_t,uint32_t,uint32_t,uint32_t,void*,void*,void*,void*)=(void*)(base+0xefb34);
    uint8_t *out=parse(blob,(uint32_t)size,tile,0,shift,scratch,version,&allocator,NULL);
    printf("{\"parsed\":%s,\"allocated\":%zu",out?"true":"false",allocated);
    if(out){
        printf(",\"link_count\":%u,\"aux_index_count\":%u,\"internal_geometry_count\":%u,\"coordinate_bits\":%u,\"shift\":%u",
               u32(out+0x58),u32(out+0x40),u32(out+0x80),out[0x15],out[0x16]);
        void (*init)(void*,void*,size_t)=(void*)(base+0x1d8b18);
        int (*points)(void*,uint16_t,uint32_t,void*,void*)=(void*)(base+0xf19cc);
        uint32_t count=u32(out+0x80);if(count>32768)return 1;
        uint8_t *geometry_rows=out+0x78+i64(out+0x78);
        uint32_t sample_count=(count+stride-1)/stride;
        if(count && (count-1)%stride)sample_count++;
        printf(",\"geometry_stride\":%u,\"geometry_sample_count\":%u",stride,sample_count);
        printf(",\"geometries\":[");
        uint32_t emitted=0;
        for(uint32_t sample=0;sample<sample_count;sample++){
            uint32_t i=sample*stride;
            if(i>=count)i=count-1;
            uint64_t reader[4]={0};init(reader,blob,(size_t)size);
            int32_t *vec[3]={0};int ok=points(out,(uint16_t)i,0,reader,vec);
            size_t n=vec[0] && vec[1]?(size_t)(vec[1]-vec[0])/2:0;
            uint16_t source_index;memcpy(&source_index,geometry_rows+i*10+2,2);
            printf("%s{\"ok\":%s,\"source_index\":%u,\"points\":[",emitted?",":"",ok?"true":"false",source_index);
            for(size_t j=0;j<n;j++)printf("%s[%d,%d]",j?",":"",vec[0][2*j],vec[0][2*j+1]);
            printf("]}");free(vec[0]);emitted++;
        }
        printf("],\"link_records\":[");
        count=u32(out+0x58);if(count>32768)return 1;
        uint8_t *rows=out+0x50+i64(out+0x50);
        for(uint32_t i=0;i<count;i++){
            printf("%s\"",i?",":"");for(int j=0;j<8;j++)printf("%02x",rows[i*8+j]);printf("\"");
        }
        printf("]");
        int (*lookup)(void*,uint16_t,int32_t,void*,void*,void*)=(void*)(base+0xf1748);
        printf(",\"link_geometries\":[");
        for(uint32_t i=0;i<count;i++){
            uint32_t cursor=0;uint16_t geometry=0,value=0;unsigned iterations=0;
            printf("%s[",i?",":"");
            do {
                int ok=lookup(out,(uint16_t)i,-1,&cursor,&geometry,&value);
                if(!ok)break;
                printf("%s%u",iterations?",":"",geometry);
                iterations++;
            }while(cursor && iterations<32768);
            printf("]");
        }
        printf("]");
    }
    puts("}");return out?0:1;
}
