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
requirements = python3,kivy==2.3.1,pyjnius,usb4a,usbserial4a

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

# p4a 分支：默认即可；遇到 "recipe 不认识 kivy==x" 时可改成 develop
# p4a.branch = develop

[buildozer]
log_level = 2
warn_on_root = 1
