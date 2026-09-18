#ifndef ZYENLANG_V2_SERVER_NATIVE_H
#define ZYENLANG_V2_SERVER_NATIVE_H

#include <stdint.h>
#include "zyenlang_c_abi.h"

int32_t zy2_server_serve(ZL_String host, int32_t port, ZL_String body, int32_t max_requests);
ZL_String zy2_server_error(void);

#endif
