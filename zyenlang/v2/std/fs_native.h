#ifndef ZYENLANG_FS_NATIVE_H
#define ZYENLANG_FS_NATIVE_H

#include <stdint.h>
#include "zyenlang_c_abi.h"

ZL_String zy2_fs_read_text(ZL_String path);
int32_t zy2_fs_write_text(ZL_String path, ZL_String value);
int32_t zy2_fs_append_text(ZL_String path, ZL_String value);
ZL_String zy2_fs_tree(ZL_String path);
ZL_String zy2_fs_error(void);

#endif
