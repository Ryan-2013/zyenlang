#ifndef ZYENLANG_REQUEST_NATIVE_H
#define ZYENLANG_REQUEST_NATIVE_H

int zl_request_perform(const char* method, const char* url, const char* body, const char* content_type, int timeout_ms);
int zl_request_status(void);
const char* zl_request_body(void);
const char* zl_request_error(void);
int zl_request_save(const char* path);

#endif
