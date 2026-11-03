// SPDX-License-Identifier: GPL-3.0-or-later
#include <laszip/laszip_api.h>
int main()
{
    laszip_U8 major = 0, minor = 0;
    laszip_U16 revision = 0;
    laszip_U32 build = 0;
    return laszip_get_version(&major, &minor, &revision, &build);
}
