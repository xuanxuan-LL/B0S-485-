"""
自定义 python3 recipe 覆盖（继承默认 Python3Recipe），一次性解决
CPython 3.10 在 Android NDK r25b（bionic）与 ubuntu-22.04 host（glibc 2.35）
下 grp/crypt 模块编译失败的问题：

  * grp 模块：grpmodule.c 无条件调用 setgrent/getgrent/endgrent，但 bionic 在
    ndk-api < 28 时既不声明也不实现这些函数；通过 grp_android_stub.patch 在
    __ANDROID__ 且 API<28 时提供空实现（grp.getgrall 安全返回空列表）。
  * crypt 模块：CPython 3.10 的 configure 不接受 --without-crypt（会被忽略，
    见 configure: WARNING: unrecognized options），而 bionic 与缺 libcrypt 的
    glibc 都没有 crypt()；通过 crypt_stub.patch 在无 crypt.h 时提供编译期
    stub（crypt.crypt 返回 NULL 并抛 OSError，功能在 Android 上本就无意义）。

关键坑：buildozer 1.6.0 固定的 p4a 版本里 Python3Recipe.configure_args 是只读
property，其 build_arch -> set_libs_flags 会执行 `self.configure_args = list(set(...))`
重新赋值。若在子类用 @property 暴露 configure_args（只读），该赋值抛
AttributeError。绕过：在 __init__ 把基类 configure_args 复制为可读写实例属性。
"""
from pythonforandroid.recipes.python3 import Python3Recipe as BasePython3Recipe


class Python3Recipe(BasePython3Recipe):

    patches = [
        'patches/pyconfig_detection.patch',
        'patches/reproducible-buildinfo.diff',
        'patches/py3.8.1.patch',
        'patches/grp_android_stub.patch',
        'patches/crypt_stub.patch',
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 复制基类 configure_args 为可读写实例属性，绕过只读 property。
        self.__dict__['configure_args'] = list(super().configure_args)

    def get_recipe_env(self, arch):
        env = super().get_recipe_env(arch)
        cflags = env.get('CFLAGS', '')
        for extra in ('-D_GNU_SOURCE', '-Wno-error=implicit-function-declaration'):
            if extra not in cflags:
                cflags = (cflags + ' ' + extra).strip()
        env['CFLAGS'] = cflags
        return env


recipe = Python3Recipe()
