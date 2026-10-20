/* Read only scalar fields of the observed six-level BMD metadata layout. */
#define _FILE_OFFSET_BITS 64
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
int main(int argc,char **argv){
    if(argc!=2)return 2;
    char path[80],line[1024];snprintf(path,sizeof path,"/proc/%d/maps",atoi(argv[1]));
    FILE *maps=fopen(path,"r");snprintf(path,sizeof path,"/proc/%d/mem",atoi(argv[1]));
    int mem=open(path,O_RDONLY);if(!maps||mem<0)return 1;
    uint8_t buf[65536+256],levels[]={2,3,5,7,9,11};unsigned matches=0;
    while(fgets(line,sizeof line,maps)){
        unsigned long long lo,hi;char perms[5];
        if(sscanf(line,"%llx-%llx %4s",&lo,&hi,perms)!=3||strcmp(perms,"rw-p")||!strstr(line,"libc_malloc"))continue;
        for(uint64_t at=lo;at<hi;at+=65536){
            size_t want=hi-at<sizeof buf?hi-at:sizeof buf;ssize_t n=pread(mem,buf,want,at);
            if(n<240)continue;
            for(size_t i=0;i+240<=(size_t)n;i+=8){
                int ok=1;for(int j=0;j<6;j++)if(buf[i+j*40]!=levels[j]||buf[i+j*40+4]>31)ok=0;
                if(!ok)continue;
                printf("Level table candidate %u\n",++matches);
                for(int j=0;j<6;j++)printf("level=%u shift=%u\n",buf[i+j*40],buf[i+j*40+4]);
            }
        }
    }
    close(mem);fclose(maps);printf("Candidates=%u\n",matches);return 0;
}
