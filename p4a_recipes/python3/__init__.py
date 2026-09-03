"""
自定义 python3 recipe 覆盖（继承默认 Python3Recipe，只补两点）：
  * 禁用 crypt 模块：bionic (Android libc) 根本没有 crypt() 函数，_cryptmodule.c
    编译/链接必失败；--without-crypt 直接排除该模块（不影响任何功能）。
  * 编译 CFLAGS 加 -D_GNU_SOURCE（让 grp 系列函数声明可见）+ 兜底
    -Wno-error=implicit-function-declaration（防止其它可选模块踩隐式声明坑）。

关键坑：p4a 的 Python3Recipe.configure_args 在基类是「只读 property」，而基类
build_arch -> set_libs_flags 里会执行 `self.configure_args = list(set(...))` 重新
赋值。若在子类用 @property 暴露 configure_args（只读），该赋值会抛
`AttributeError: can't set attribute 'configure_args'`。

绕过办法：在 __init__ 里读取基类 property 生成的基础参数，追加 --without-crypt
后写入「实例 __dict__」，使其成为可读写的普通实例属性；之后 set_libs_flags 的
赋值就落到实例 dict，不再触发只读 property。
"""
from pythonforandroid.recipes.python3 import Python3Recipe as BasePython3Recipe


class Python3Recipe(BasePython3Recipe):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 走基类 property 拿到默认 configure_args，转 list 后追加 --without-crypt，
        # 存为实例属性（绕过只读 property）。
        base = list(super().configure_args)
        if '--without-crypt' not in base:
            base.append('--without-crypt')
        self.__dict__['configure_args'] = base

    def get_recipe_env(self, arch):
        env = super().get_recipe_env(arch)
        cflags = env.get('CFLAGS', '')
        for extra in ('-D_GNU_SOURCE', '-Wno-error=implicit-function-declaration'):
            if extra not in cflags:
                cflags = (cflags + ' ' + extra).strip()
        env['CFLAGS'] = cflags
        return env


recipe = Python3Recipe()
