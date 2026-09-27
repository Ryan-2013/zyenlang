#ifndef ZYENLANG_FS_NATIVE_H
#define ZYENLANG_FS_NATIVE_H

#include <stdint.h>
#include "zyenlang_c_abi.h"

ZL_String zy2_fs_read_text(ZL_String path);
int32_t zy2_fs_write_text(ZL_String path, ZL_String value);
int32_t zy2_fs_append_text(ZL_String path, ZL_String value);
ZL_String zy2_fs_tree(ZL_String path);
ZL_String zy3_fs_list_dir(ZL_String path);
int32_t zy3_fs_create_dir(ZL_String path);
int32_t zy3_fs_create_dirs(ZL_String path);
int32_t zy3_fs_remove_file(ZL_String path);
int32_t zy3_fs_remove_dir(ZL_String path);
int32_t zy3_fs_rename(ZL_String source, ZL_String destination);
int32_t zy3_fs_copy_file(ZL_String source, ZL_String destination);
int64_t zy3_fs_file_size(ZL_String path);
ZL_String zy2_fs_error(void);

#endif
