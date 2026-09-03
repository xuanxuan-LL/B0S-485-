"""
自定义 hostpython3 recipe 覆盖（继承默认 HostPython3Recipe）。hostpython3 是
桌面（ubuntu-22.04 / glibc 2.35）Python，用于交叉编译；其 grp 模块需要
-D_GNU_SOURCE 才能让 glibc 声明 setgrent/getgrent/endgrent，crypt 模块在缺
libcrypt-dev 时通过 crypt_stub.patch 提供编译期 stub。
"""
import os

from pythonforandroid.recipes.hostpython3 import (
    HostPython3Recipe as BaseHostPython3Recipe,
)


class HostPython3Recipe(BaseHostPython3Recipe):

    patches = [
        'fix_ensurepip.patch',
        'patches/crypt_stub.patch',
    ]

    def get_recipe_env(self, arch=None):
        env = super().get_recipe_env(arch)
        cflags = env.get('CFLAGS', '')
        if '-D_GNU_SOURCE' not in cflags:
            cflags = (cflags + ' -D_GNU_SOURCE').strip()
        env['CFLAGS'] = cflags
        return env


recipe = HostPython3Recipe()
