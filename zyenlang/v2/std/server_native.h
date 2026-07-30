#ifndef ZYENLANG_V2_SERVER_NATIVE_H
#define ZYENLANG_V2_SERVER_NATIVE_H

#include <stdint.h>

int32_t zy2_server_serve(const char* host, int32_t port, const char* body, int32_t max_requests);
const char* zy2_server_error(void);

#endif
