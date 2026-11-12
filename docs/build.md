# Windows 源码构建 · Build on Windows

## 准备依赖

使用 Windows x64、.NET Framework 4.7.2 或更新的 4.x 运行时、64 位 .NET Framework C# 编译器和 Python 3.14 x64。编译器默认取 `%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe`，可用 `--compiler` 指定。

获取固定的上游 TotK Recomp 0.4.0 源码和相应 Windows kit。核对上游源码提交为 `7a37fc3c346cd6f2b3c80637effb27b880ce817b`。Python 运行目录可用 Windows kit 的 `python` 子目录，或官方 Python 3.14 x64 embeddable 包。解压后的目录应含 `python.exe`、`python314.zip` 和 `python314._pth`。

- [固定上游源码](https://github.com/valentderah/totk-pc-recomp/tree/7a37fc3c346cd6f2b3c80637effb27b880ce817b)
- [上游 0.4.0 Windows kit](https://github.com/valentderah/totk-pc-recomp/releases/tag/v0.4.0)
- [Python 官方 Windows 下载](https://www.python.org/downloads/windows/)

## 构建管理器

从仓库根目录运行以下 PowerShell 命令。将前两个路径改为本机解压目录。

```powershell
$upstream = '../totk-pc-recomp-7a37fc3c346cd6f2b3c80637effb27b880ce817b'
$pythonKit = '../TotK-Setup-0.4.0-windows/python'
& "$pythonKit/python.exe" -X utf8 tools/build_manager.py `
  --upstream $upstream --python-kit $pythonKit --output build/app-1.2.8
```

输出为 `build/app-1.2.8/王国之泪补丁管理器.exe`、后端脚本、版本标记、运行环境和 `recipe.zip`。输出目录必须是新目录。工具按配方成员名和原始内容计算 `recipe_id`，预期为 `67a2bbc520378201d5bd5bcfcec07dcc917c9b86261a2692f9464479b479ff8c`。ZIP 容器的条目顺序和时间戳可与审计候选不同。

可单独生成运行时配方。

```powershell
& "$pythonKit/python.exe" -X utf8 tools/make_recipe.py --upstream $upstream --output build/recipe.zip
```

## 本地游戏与资产

1. 用[上游构建说明](https://github.com/valentderah/totk-pc-recomp/blob/7a37fc3c346cd6f2b3c80637effb27b880ce817b/docs/building.md)从自己的 1.4.3 游戏副本生成官方安装。保留安装的 ExeFS、RomFS 和资源索引。
2. 如持有获准本地使用的 1.2.8 完整包，解压到仓库外。用上面的构建命令加 `--local-payload <完整包解压目录>`，并指定一个新输出目录。工具接入该目录的 `assets.zip`、`prebuilt/` 和可选的旧版配方。管理器在安装时仍核对配方、资产、组件及游戏版本。
3. 使用本地源码重编时，管理器在「安装管理 → 高级安装」中将配方合并到下载的固定版本上游源码。游戏对象由用户本机生成，上游工具根据本地游戏数据生成原版、Xbox、键鼠及适配布局。三套 PS5 专用图标、引导线及宽屏改图仍依赖合法取得的对应素材。更新上游至其他版本前，先查看版本与配方兼容提示。
4. 游戏内按 F1 选择 PS5 按键提示及样式，重启使布局生效。需要触觉反馈时用 USB 连接 DualSense。图形功能取决于驱动、GPU 与用户本地安装的对应 DLL。

仅用公开源文件可以重建管理器与运行时源码配方。完整六组合离线安装、三套 PS5 成品资源和图形 DLL 需要额外的私有本地输入。该仓库没有一键生成全套历史安装包的素材。

## 验证

```powershell
& "$pythonKit/python.exe" -X utf8 tests/test_manager.py
Start-Process -FilePath (Resolve-Path 'build/app-1.2.8/王国之泪补丁管理器.exe') -ArgumentList '--selftest --dpi 144' -Wait
Get-Content build/app-1.2.8/ui-validation.txt
```

回归使用临时目录。默认跳过需要真实资源的独立图标测试。若使用自有测试数据，设置 `TOTK_TEST_ROMFS`、`TOTK_TEST_INDEX` 和含匹配资产的 `TOTK_TEST_APP`。图形自检需要有可用的 Windows 桌面会话。

维护发布分支时，暂存文件后运行 `python tools/check_release.py` 和固定上游的 `tools/check_content.py --staged`。
