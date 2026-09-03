[app]
# ---- 基本信息 ----
title = MCTC 电梯测试
# 若打包报编码/Gradle 错，改成纯英文：title = MCTC Elevator Test
package.name = mctctest
package.domain = org.mctc

# ---- 源码 ----
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,otf,ttf,txt,xml
source.exclude_exts = spec,md
source.exclude_dirs = tests, bin, .buildozer, __pycache__
source.include_patterns = assets/*,res/xml/*

# ---- 版本 ----
version = 1.0.0
# 版本号(整数, 每次发版递增)
numeric_version = 1

# ---- 依赖 ----
# 网络 TCP 模式只用 python3 + kivy；USB-OTG 直连需 usb4a/usbserial4a
# (usb4a 依赖 pyjnius 调用安卓 USB Manager，显式列出更稳)
# 固定 kivy 版本，避免拉到与当前 Python 不兼容的新版
# 固定 python3==3.10.13：buildozer 1.6.0 默认拉 CPython 3.12，其
#   Modules/remote_debugging.c 在 NDK target 23(Android 6) 下调 preadv/pwritev，
#   旧 API 头未声明这两个函数，clang 的 -Werror=implicit-function-declaration
#   直接让 python3 recipe 编译失败。3.10 无此问题，且兼容 minapi 23。
# 必须同时固定 hostpython3==3.10.13 与 python3 同版本：p4a 强制要求两者一致，
#   否则报 "python3 should have same version as hostpython3, 3.10.13 != 3.14.2"
#   （buildozer 1.6.0 的默认 hostpython3 是 3.14.2）。
requirements = python3==3.10.13,hostpython3==3.10.13,kivy==2.3.1,pyjnius,usb4a,usbserial4a

# ---- 界面 ----
orientation = portrait
fullscreen = 0

# ---- 图标 / 启动图（512x512 PNG，由 make_icons.py 生成）----
icon.filename = %(source.dir)s/assets/icon.png
presplash.filename = %(source.dir)s/assets/presplash.png
presplash.color = #FFFFFF

# ---- 安卓 ----
# 网络 TCP 模式需要联网与网络状态权限；USB-OTG 直连需要 USB 主机权限
android.permissions = INTERNET,ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE,USB_PERMISSION
# USB 主机能力声明：固定版 p4a(58d21141) 的 apk 子命令不支持 --feature 参数，
# 故改用 android.extra_manifest_xml -> p4a --extra-manifest-xml，
# 该内容被注入到 <manifest> 根层级（<application> 之前），<uses-feature> 在此合法。
# （不能放进 intent_filters 片段，<uses-feature> 在 <activity> 内会导致 AAPT 报错）
android.extra_manifest_xml = manifest/extra_manifest.xml
# 监听 USB 设备插入并弹出授权框（引用 manifest/intent_filters.xml）
android.manifest.intent_filters = manifest/intent_filters.xml

# API 级别：NDK 25b 是 p4a 支持最稳的一版。
# 若报 NDK/Gradle 相关错误，把 android.api 降到 33 再试。
android.api = 34
android.minapi = 23
android.ndk = 25b

# 架构：先只出 arm64（现在主流手机都是 64 位，出包更快）。
# 若现场手机是老 32 位机，改成：arm64-v8a, armeabi-v7a
android.archs = arm64-v8a

# 自动接受 Android SDK 许可，避免云端首次构建卡在确认框
android.accept_licenses = True

android.allow_backup = True
android.enable_androidx = True

# 发布签名（正式发版时填；debug 包不需要）
# android.keystore = ./mctc.keystore
# android.keyalias = mctc

# 自定义 p4a recipe：修复 CPython 3.10 在 NDK r25b 下 grp/crypt 编译失败
p4a.local_recipes = p4a_recipes
# 固定 p4a 到已知可用 commit，避免 master 漂移导致 recipe / 基础补丁不匹配
# （之前每次构建 git clone 最新 master，某次 master 移除了 fix_ensurepip.patch
#   等文件，本地覆盖 recipe 引用它们时直接报 “patch not found”）。
p4a.branch = master
p4a.commit = 58d21141f17c889bf8585f5665921d72028f8831

[buildozer]
log_level = 2
warn_on_root = 1
