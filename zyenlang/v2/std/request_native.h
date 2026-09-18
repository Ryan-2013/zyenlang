#ifndef ZYENLANG_REQUEST_NATIVE_H
#define ZYENLANG_REQUEST_NATIVE_H
#include "zyenlang_c_abi.h"

int zl_request_perform(ZL_String method, ZL_String url, ZL_String body, ZL_String content_type, int timeout_ms);
int zl_request_status(void);
ZL_String zl_request_body(void);
ZL_String zl_request_error(void);
int zl_request_save(ZL_String path);

#endif
