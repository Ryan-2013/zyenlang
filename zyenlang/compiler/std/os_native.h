#ifndef ZYENLANG_OS_NATIVE_H
#define ZYENLANG_OS_NATIVE_H

#include <stdbool.h>
#include <stdint.h>
#include "zyenlang_c_abi.h"

ZL_String zy3_os_name(void);
ZL_String zy3_os_architecture(void);
int64_t zy3_os_process_id(void);
ZL_String zy3_os_current_dir(void);
ZL_String zy3_os_home_dir(void);
ZL_String zy3_os_temp_dir(void);
ZL_String zy3_os_hostname(void);
ZL_String zy3_os_env(ZL_String name);
bool zy3_os_has_env(ZL_String name);
int32_t zy3_os_set_env(ZL_String name, ZL_String value);
int32_t zy3_os_unset_env(ZL_String name);
ZL_String zy3_os_error(void);

#endif
