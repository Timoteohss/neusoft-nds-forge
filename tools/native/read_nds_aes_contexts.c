/* Read-only observer of the Neusoft SDK's 64-bit-word AES schedule.
 * Does not attach, stop, or write to the target process. Emits candidate
 * map keys only to a caller-selected private file, never a memory dump.
 */
#define _FILE_OFFSET_BITS 64
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    char path[80], line[1024];
    snprintf(path, sizeof path, "/proc/%d/maps", atoi(argv[1]));
    FILE *maps = fopen(path, "r");
    snprintf(path, sizeof path, "/proc/%d/mem", atoi(argv[1]));
    int mem = open(path, O_RDONLY);
    int out = open(argv[2], O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (!maps || mem < 0 || out < 0) { perror("open"); return 1; }
    uint8_t buf[65536 + 384]; size_t count = 0, scanned = 0;
    while (fgets(line, sizeof line, maps)) {
        unsigned long long lo, hi; char perms[5];
        if (sscanf(line, "%llx-%llx %4s", &lo, &hi, perms) != 3) continue;
        if (strcmp(perms, "rw-p")) continue;
        if (!strstr(line, "libc_malloc") && !strstr(line, "[heap]")) continue;
        for (uint64_t at = lo; at < hi; at += 65536) {
            size_t want = hi-at < sizeof buf ? hi-at : sizeof buf;
            ssize_t n = pread(mem, buf, want, at);
            if (n < 384) continue;
            scanned += n;
            for (size_t i = 0; i + 356 <= (size_t)n; i += 8) {
                uint64_t w[8]; uint32_t rounds;
                memcpy(w, buf+i, sizeof w); memcpy(&rounds,buf+i+352,4);
                if (rounds != 10) continue;
                int valid = 1;
                for (int j=0;j<8;j++) if (w[j]>>32) valid=0;
                if (!valid || !(w[0]|w[1]|w[2]|w[3])) continue;
                if (w[5] != (w[1]^w[4]) || w[6] != (w[2]^w[5]) || w[7] != (w[3]^w[6])) continue;
                uint8_t key[16];
                for (int j=0;j<4;j++) for(int k=0;k<4;k++) key[j*4+k]=(w[j]>>(24-8*k))&255;
                if (write(out,key,16)!=16) return 1;
                count++;
            }
        }
    }
    close(out); close(mem); fclose(maps);
    printf("Scanned %zu bytes of native malloc memory; %zu schedule candidates.\n",scanned,count);
    return 0;
}
