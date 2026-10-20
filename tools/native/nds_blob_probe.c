/* Invoke only the observed BMD blob reader, in an isolated process. */
#include <dlfcn.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
typedef struct {void **vtable;} Allocator;
static void *allocate(Allocator *self,size_t bytes){
    (void)self;fprintf(stderr,"BMD allocation: %zu bytes\n",bytes);
    if(bytes>64*1024*1024)return NULL;
    return calloc(1,bytes);
}
int main(int argc,char **argv){
    setbuf(stdout,NULL);
    if(argc<5)return 2;
    void *lib=dlopen("libNdsLib.so",RTLD_NOW|RTLD_LOCAL);
    if(!lib){fprintf(stderr,"%s\n",dlerror());return 1;}
    uintptr_t base=(uintptr_t)dlsym(lib,"nds_open")-0x927e0;
    void (*reader)(void*,void*,size_t)=(void*)(base+0x1d8b18);
    void *(*parse)(void*,void*,uint32_t,uint32_t,uint32_t,void*,void*)=(void*)(base+0x388d4);
    FILE *f=fopen(argv[1],"rb");if(!f)return 1;
    fseek(f,0,SEEK_END);size_t n=ftell(f);rewind(f);
    void *blob=malloc(n);if(fread(blob,1,n,f)!=n)return 1;fclose(f);
    uint64_t tbits[4]={0};reader(tbits,blob,n);
    void (*seek)(void*,uint32_t)=(void*)(base+0x1d8b7c);
    uint32_t (*read16)(void*)=(void*)(base+0x1d9590);
    uint32_t (*readvar)(void*)=(void*)(base+0x1d9508);
    uint32_t (*readbit)(void*)=(void*)(base+0x1d9360);
    uint32_t (*read32)(void*)=(void*)(base+0x1d8cb8);
    uint32_t (*readn)(void*,uint32_t)=(void*)(base+0x1d8be8); // read n bits?
    uint32_t (*read2bits)(void*)=(void*)(base+0x1d93c4);
    uint32_t (*readleb)(void*)=(void*)(base+0x1d9d70);
    uint8_t *b=(uint8_t*)blob;
    if((b[0]&2) && n>9){
        uint32_t aoff=(b[5]<<24)|(b[6]<<16)|(b[7]<<8)|b[8];
        printf("Dynamic aoff=%u\n", aoff);
        seek(tbits, aoff);
        uint32_t gcnt=readvar(tbits);
        uint32_t acnt=readvar(tbits);
        uint32_t atype=read16(tbits);
        printf("At aoff=%u: gcnt=%u, acnt=%u, atype=0x%04x\n", aoff, gcnt, acnt, atype);
    }


    uint32_t version[2]={(uint32_t)strtoul(argv[3],NULL,0),0};
    void *vtable[]={allocate};Allocator alloc={vtable};
    uint32_t tile=strtoul(argv[2],NULL,0),width=strtoul(argv[4],NULL,0);
    printf("Parsing tile %u, model enum %u, width %u, blob %zu\n",tile,version[0],width,n);
    uint64_t bits[4]={0};reader(bits,blob,n);
    uint8_t *out=parse(bits,NULL,tile,0,width,version,&alloc);
    printf("Parsed: %s\n",out?"yes":"no");if(!out)return 1;
    uint32_t counts[3];memcpy(&counts[0],out+0x88,4);memcpy(&counts[1],out+0x70,4);memcpy(&counts[2],out+0x58,4);
    printf("Counts: points=%u lines=%u areas=%u\n",counts[0],counts[1],counts[2]);
    int32_t origin[2];memcpy(origin,out+0x38,8);
    uint32_t geometry_base;memcpy(&geometry_base,out+0x78,4);
    int64_t line_offset;memcpy(&line_offset,out+0x68,8);
    printf("Geometry: bits=%u shift=%u origin=(%d,%d) base_bit=%u\n",out[0x35],out[0x36],origin[0],origin[1],geometry_base);
    printf("out[0x90..0xf0]: ");
    for(int k=0x90;k<0xf0;k++)printf("%02x ",out[k]);
    puts("");
    uint32_t *attr_base = *(uint32_t**)(out + 0xa8);
    int32_t attr_off = *(int32_t*)(out + 0xa8);
    uint32_t attr_cnt = *(uint32_t*)(out + 0xb0);
    printf("attr_offset=%d, attr_count=%u\n", attr_off, attr_cnt);
    uint8_t *attrs = out + 0xa8 + attr_off;
    for(uint32_t k=0;k<attr_cnt && k<10;k++){
        uint16_t type = *(uint16_t*)(attrs + k*8);
        uint32_t val = *(uint32_t*)(attrs + k*8 + 4);
        printf("  attr[%u]: type=0x%04x val=%u (0x%x)\n", k, type, val, val);
    }
    int all=argc>5 && !strcmp(argv[5],"--all");
    for(unsigned i=0;i<counts[1] && (all || i<5);i++){
        uint8_t *line=out+0x68+line_offset+i*20;
        uint32_t bit_offset=(line[13]|line[14]<<8|line[15]<<16)&0x3fffff;
        printf("Line %u header=",i);for(int j=0;j<20;j++)printf("%02x",line[j]);
        printf(" geometry_bit=%u\n",geometry_base+bit_offset);
    }
    uint8_t handle[0xf0]={0};memcpy(handle+8,&out,8);reader(handle+0x10,blob,n);
    void (*line_points)(void*,uint16_t,void*)=dlsym(lib,"nds_tile_getLinePoints");
    for(unsigned i=0;i<counts[1] && (all || i<5);i++){
        int32_t *vec[3]={0};line_points(handle,i,vec);
        size_t count=vec[0] && vec[1] ? (vec[1]-vec[0])/2 : 0;
        printf("Line %u: %zu points",i,count);
        for(size_t j=0;j<count && (all || j<8);j++)printf(" (%d,%d)",vec[0][j*2],vec[0][j*2+1]);
        puts("");free(vec[0]);
    }
    void (*line_names)(void*,uint16_t,void*)=dlsym(lib,"nds_tile_getLineNames");
    if(line_names){
        for(unsigned i=0;i<counts[1] && (all || i<5);i++){
            uint32_t *nvec[3]={0};
            line_names(handle,i,nvec);
            size_t ncount=nvec[0] && nvec[1] ? (nvec[1]-nvec[0]) : 0;
            printf("Line %u names: %zu",i,ncount);
            for(size_t j=0;j<ncount;j++)printf(" [id=%u]",nvec[0][j]);
            puts("");
            if(nvec[0])free(nvec[0]);
        }
    }
    return 0;
}
