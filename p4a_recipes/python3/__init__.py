"""
自定义 python3 recipe 覆盖：修复 CPython 3.10 在 Android NDK r25b 下的编译失败。

问题根因：
  * bionic (Android libc) 根本没有 `crypt()` 函数，_cryptmodule.c 编译/链接必失败；
  * bionic 只在定义 `_GNU_SOURCE` 时才声明 getgrent/setgrent/endgrent，
    grpmodule.c 因此触发 CPython 3.10 自带的
    `-Werror=implicit-function-declaration`，编译中断。

修复手段（继承默认 python3 recipe，只补两点）：
  * configure 加 `--without-crypt`：直接禁用 crypt 模块（它在 Android 上本就
    无法链接，禁用不影响任何功能）；
  * 编译 CFLAGS 加 `-D_GNU_SOURCE`：让 grp 系列函数声明可见；
  * 额外加 `-Wno-error=implicit-function-declaration` 作为兜底，防止其它可选
    模块也踩同样的隐式声明坑。
"""
from pythonforandroid.recipes.python3 import Python3Recipe as BasePython3Recipe


class Python3Recipe(BasePython3Recipe):

    def get_recipe_env(self, arch):
        env = super().get_recipe_env(arch)
        cflags = env.get('CFLAGS', '')
        for extra in ('-D_GNU_SOURCE', '-Wno-error=implicit-function-declaration'):
            if extra not in cflags:
                cflags = (cflags + ' ' + extra).strip()
        env['CFLAGS'] = cflags
        return env

    @property
    def configure_args(self):
        args = list(super().configure_args)
        if '--without-crypt' not in args:
            args.append('--without-crypt')
        return args


recipe = Python3Recipe()
