#define _GNU_SOURCE
#include <errno.h>
#include <dlfcn.h>
#include <limits.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>
#include "gzguts.h"

static int target_fd = -1;
static unsigned failures = 0;

/* Interpose only the fixture's write; ordinary diagnostic output stays usable. */
ssize_t write(int fd, const void *data, size_t size) {
    if (fd == target_fd) {
        failures++;
        errno = EAGAIN;
        return -1;
    }
    return syscall(SYS_write, fd, data, size);
}

int main(int argc, char **argv) {
    Dl_info owner;
    char loaded[PATH_MAX];
    if (argc != 2 || !dladdr((void *)gzwrite, &owner)
        || !realpath(owner.dli_fname, loaded) || strcmp(loaded, argv[1]) != 0) {
        return 2;
    }
    unsigned char input[1048576];
    uint32_t random = 42;
    for (size_t i = 0; i < sizeof(input); i++) {
        random = random * 1664525U + 1013904223U;
        input[i] = (unsigned char)(random >> 24);
    }
    target_fd = open("/dev/null", O_WRONLY | O_NONBLOCK);
    if (target_fd < 0) return 2;
    gzFile file = gzdopen(target_fd, "w");
    if (file == NULL || gzbuffer(file, 32) != 0) return 2;
    int written = gzwrite(file, input, sizeof(input));
    gz_statep state = (gz_statep)file;
    int reached = failures > 0 && state->again && written >= 0
        && (size_t)written < sizeof(input);
    int safe = state->strm.avail_in == 0 && state->strm.next_in == state->in;
    gzclose(file);
    printf("{\"cve\":\"CVE-2026-85091\",\"library\":\"%s\",\"failureReached\":%s,\"inputReset\":%s}\n",
           loaded, reached ? "true" : "false", safe ? "true" : "false");
    return reached ? (safe ? 0 : 10) : 2;
}
