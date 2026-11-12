# MOD 管理 · Mod Center

选择现有的 `totk.exe`，打开左侧「MOD 管理」。点击刷新查看游戏已经扫描的目录和状态。外部使用 F1 调整启停或顺序后，退出游戏并刷新。

## 导入与管理

1. 把一个或多个 ZIP、MOD 文件夹拖进列表，或者使用「导入 ZIP / 更新」和「导入文件夹」。
2. 核对目标文件夹、文件清单和潜在覆盖。导入默认跳过现有同名目录，勾选替换选项才会更新不同内容的同名 MOD。
3. 选中项目可查看详情和启停。勾选多个项目可以一起启停或卸载。无搜索或筛选且按加载优先级显示时，可拖动列表项排序。排在上方的 MOD 优先覆盖同一个资源。
4. 关闭并重新启动游戏应用资源变化。F1 的 MOD 标签可查看运行时识别结果。通过管理器启动游戏后，刷新详情也可查看最近日志中的识别状态和 RomFS 文件计数。

可识别直接位于 MOD 文件夹或 TotK Title ID 包装下的 `romfs`、`romfslite`、`exefs` 和 `cheats`。也识别 `atmosphere/exefs_patches` 包装。ExeFS IPS／PCHTXT 及 cheats 是否生效取决于当前游戏构建 ID 和上游运行时。ExeFS 可能需要使用上游流程重新编译。含 `main` 或 `subsdk` 程序模块的 MOD 无法加载。7z 和 RAR 请先解压为目录。

冲突检查比较同一路径的文件内容。游戏原有资源可能由上游按大小写不敏感的路径匹配。上游可合并资源尺寸表，管理器会单独标识。选择的运行时若启用某个内置资源包，覆盖提示会说明相应 MOD 可能影响哪些文件。文件内部的语义冲突及未声明的依赖仍需在游戏内验证。

可选 `mod.json` 或 `metadata.json` 放在 MOD 根目录。支持 `name`、`version`、`author`、`description`、`category`。`dependencies` 是其他 MOD 的文件夹名列表，`game_versions` 和 `recomp_versions` 是版本字符串列表。这些字段用于显示和诊断。日期和来源由管理器在导入时记录，已有目录可直接管理。

## 配置方案与恢复

配置方案保存普通 MOD 的选择和顺序。可以预览并切换「纯净游戏」「PS5 手柄」「画质增强」，也可把当前组合保存为自定义方案。手柄和画质方案按普通 MOD 的「手柄」「画质」分类选择。管理器内置 PS5、触觉、图形和运行时组件继续在原有页面管理。导出的 JSON 可在另一台机器导入，并会提示缺少的 MOD。

导入、替换和卸载先暂存，替换及卸载前保留文件夹备份。可从「撤销 / 恢复」撤销最近操作或恢复中断的事务，也可在方案里选「上次正常组合」恢复切换前的选择。游戏运行时可查看目录，但应退出后再修改。备份目录位于实际 `mods` 目录同级的隐藏工作目录中。

## Compatibility

The manager reads and writes the same `mods_order` and `mods_disabled` settings in `totk.ini` as the game's F1 Mods tab. It honors `mods_dir`. A `TOTK_MODS_*` environment override or an unrecognized runtime interface leaves the center read-only. TotK Recomp 0.4.0 and game data 1.4.3 were exercised together. Other versions are detected at runtime and require their own validation.

Import ZIPs or folders, review files and overlaps, then confirm. Use the list to toggle mods, set priority, uninstall or open their folders. Profiles store choices and order. Close the game before modifying mods and restart it for resource changes to take effect.
