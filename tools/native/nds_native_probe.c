/* Isolated read-only test process. No injection into the navigation app. */
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>

int main(int argc,char **argv) {
    setbuf(stdout,NULL);
    if(argc<2)return 2;
    void *lib=dlopen("libNdsLib.so",RTLD_NOW|RTLD_LOCAL);
    if(!lib){fprintf(stderr,"dlopen: %s\n",dlerror());return 1;}
    puts("Library loaded");
    int (*initialize)(const char*)=dlsym(lib,"nds_initialize");
    if(initialize)printf("Storage initialize: %d\n",initialize(NULL));
    void *(*open_db)(const char*,void*,void*,void*)=dlsym(lib,"nds_open");
    void *(*create_bmd)(void*)=dlsym(lib,"nds_createBasicMapDbHandle");
    void *(*tile)(void*,uint32_t,uint32_t)=dlsym(lib,"nds_bmd_createTileHandle");
    unsigned (*lines)(void*)=dlsym(lib,"nds_tile_getNumberOfLines");
    unsigned (*points)(void*)=dlsym(lib,"nds_tile_getNumberOfPoints");
    unsigned (*areas)(void*)=dlsym(lib,"nds_tile_getNumberOfAreas");
    if(!open_db||!create_bmd||!tile||!lines||!points||!areas)return 1;
    void *db=open_db(argv[1],NULL,NULL,NULL);
    printf("Database opened: %s\n",db?"yes":"no");
    if(!db)return 1;
    void *bmd=create_bmd(db);printf("BMD handle: %s\n",bmd?"yes":"no");
    if(!bmd)return 1;
    if(argc>3){
        unsigned ur=strtoul(argv[2],NULL,0),id=strtoul(argv[3],NULL,0);
        void *t=tile(bmd,ur,id);printf("Tile %u UR-index %u: %s\n",id,ur,t?"yes":"no");
        if(t)printf("Counts: points=%u lines=%u areas=%u\n",points(t),lines(t),areas(t));
    }
    return 0;
}
