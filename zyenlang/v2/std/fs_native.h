#ifndef ZYENLANG_FS_NATIVE_H
#define ZYENLANG_FS_NATIVE_H

#include <stdint.h>

const char* zy2_fs_read_text(const char* path);
int32_t zy2_fs_write_text(const char* path, const char* value);
int32_t zy2_fs_append_text(const char* path, const char* value);
const char* zy2_fs_tree(const char* path);
const char* zy2_fs_error(void);

#endif
