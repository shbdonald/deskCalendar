# 桌面日历（Desktop Calendar）

Windows 桌面日历小组件，基于 **Python + PySide6**。  
默认显示本周（周日→周六），可展开为本月；格内可显示计划与多国节假日（出厂默认不勾选任何国家）。窗口置底、可锁定；出厂配置在 `data/`，个人数据（计划、会话、iCloud 凭证）在 `userdata/`（不进 Git）。

**版本改进一览：** 见 [CHANGELOG.md](CHANGELOG.md)。

回滚到加「重复计划」之前的版本：

```bash
git checkout baseline-pre-plans
```

## 环境与运行

- Windows 10 / 11 · Python 3.10+（推荐 3.11+）
- 依赖：`PySide6`、`httpx`、`caldav`、`icalendar`

```bash
cd desktopcalendar
pip install -r requirements.txt
python main.py
```

## 绿色便携包

一键生成解压即用的 zip（无需安装 Python）：

```bash
pip install pyinstaller
python build_portable.py
```

产物：

- 文件夹：`release/DesktopCalendar/`（内含 `DesktopCalendar.exe`）
- 压缩包：`release/DesktopCalendar-portable.zip`（约 46MB）

解压后双击 `DesktopCalendar.exe` 即可运行；配置写在同目录 `data\` 下。

可选单文件打包（启动较慢）：

```bash
pip install pyinstaller
pyinstaller -F -w -n DesktopCalendar main.py
```

---

## 功能与实现逻辑

### 1. 周 / 月视图

| 逻辑 | 说明 | 关键名 / 常量 |
|------|------|----------------|
| 一周起点 | 周日为一周第一天 | `calendar_math.week_start_sunday`、`get_week_dates` |
| 月网格 | 固定 **6 行 × 7 列**，保证展开时格子尺寸稳定 | `get_month_grid` |
| 切换 | `expanded=true` 显示月视图，否则周视图 | 配置键 `expanded`；UI：`▼`/`▲` |
| 翻页 | 周：±7 天；月：上/下月 | 内存：`_ref`、`_month` |

入口：`MainWindow._apply_expanded` / `_prev` / `_next`。

### 2. 计划（可重复）

| 逻辑 | 说明 | 关键名 |
|------|------|--------|
| 存储 | 扁平 `plans` 列表（启动时自动迁移旧按日键格式） | `userdata/todos.json`；`TodoStore` |
| 项结构 | `title` / `detail`（系列）/ `occurrence_details`（按日备注）/ … | 完成时可写当日情况；同步进日历 DESCRIPTION |
| 展开 | `for_date(d)` 按重复规则投影到日历格 | `occurs_on` |
| 同步方向 | 比较 `updated_at` 与远端 `LAST-MODIFIED` / `X-DESKTOPCAL-UPDATED`；手机删除且本地未再改 → 跟删；本地更新过 → 再推 | `plan_needs_push`、`reconcile_with_remotes` |
| 单击 | （格子内计划不可单击打卡；请在「今日待办」窗口勾选） | — |
| 双击计划 | 改标题/日期/重复/日历，或删除整条/当日 | `TodoEditDialog` |
| 双击空白格 | 新建计划（可选日历标签） | `DayCell.day_add` |

### 2.1 iCloud 日历（标签）双向同步

iCloud 里的「个人 / 工作 / 租房」等就是**日历**；本应用把它们当作可选标签：设置里勾选同步哪些，建计划时选择或新建，并与 iPhone 对应日历双向同步。

| 逻辑 | 说明 |
|------|------|
| 协议 | CalDAV，事件为全天 VEVENT（可含 RRULE） |
| 多日历 | 设置中「刷新列表」后多选要同步的日历；可「新建日历」 |
| 默认日历 | 「新建计划默认」下拉；计划也可单独选日历 |
| 凭证 | Apple ID + [应用专用密码](https://appleid.apple.com)，保存在 `userdata/icloud_caldav.json`（不入库） |
| 推送 | 本地新增/编辑/删除/打卡后异步写入所属日历 |
| 拉取 | 对每个已启用日历轮询对账（默认 45 秒） |
| 生日等 | 通讯录系统「生日」日历（URL 含 birthday）不列出；**你自建的同名日历会列出并同步** |
| 节日 | **不同步**到 iCloud；桌面端仍用 Nager 显示公共假日 |
| 格子显示 | 多日历时行内前缀短名，如 `[工作] 开会` |
| 关联字段 | `caldav_uid`、`calendar_id` / `calendar_name`；打卡为 `X-DESKTOPCAL-COMPLETIONS` |

设置路径：齿轮 → 「iCloud 日历（标签）双向同步」→ 填写账号 → **刷新列表并勾选** → 测试连接 / 立即同步 → 勾选启用。

旧配置若只有单一日历名（如「桌面计划」），刷新后会按名称匹配并自动勾选。

iPhone：系统设置 → 日历 → 账户 → iCloud，确保日历开关打开，即可在「日历」App 中看到对应日历。

冲突策略：后写入为准；刚推送后短时间内轮询不会用旧副本覆盖。

### 2.2 今日待办窗口

独立小窗，投影**当天**日历计划；设置中可开关「显示今日待办窗口」（默认关）。关闭小窗仅隐藏。

| 逻辑 | 说明 |
|------|------|
| 数据 | 与日历格子同源（`TodoStore` 当日投影），非独立清单文件 |
| 勾选完成 | 在窗口内打卡；**周期计划会拆成「今日单日任务」**并写入备注，系列跳过当天（互不影响，手机上也是独立事件） |
| 删除 | **仅删除今日**（重复计划写入 exceptions；单次计划整条删除） |
| 添加 | 创建今天的不重复计划（默认日历） |
| 格子 | 仍显示计划；**不能单击打卡**；双击仍可编辑（对话框可删全部周期） |

### 3. 节假日

| 逻辑 | 说明 | 关键名 |
|------|------|--------|
| 数据源 | [Nager.Date](https://date.nager.at) Public Holidays | `HolidayFetchWorker` URL：`/api/v3/PublicHolidays/{year}/{country}` |
| 缓存 | 假日按国家+年；国家名约每月刷新一次 | `data/holidays/{CC}_{year}.json`；`data/countries.json` |
| 多选国家 | 设置勾选；显示 `English (中文)`，按英文排序 | 配置键 `countries`；`country_names.py` |
| 年份覆盖 | 当前周、月网格涉及的年份 | `MainWindow._ensure_holiday_years` |

### 4. 窗口几何（位置 / 大小）

启动时 **只信 JSON**，不临时用代码硬算覆盖（缺字段时才用 `layout_metrics` 回退）。

| 逻辑 | 说明 | 参数名 |
|------|------|--------|
| 屏幕位置 | 左上角 | `x`, `y` |
| 实际窗口 | 上次退出时的客户区大小 | `display_w`, `display_h` |
| 单元格 | 由窗口反推或展开时换算 | `cell_w`, `cell_h` |
| 周基准窗 | 由单元格经 chrome 公式算出，供展开/收起 | `window_w`, `window_h` |
| 换算公式 | 边距、标题栏、格间距 | `layout_metrics`：`MARGIN` `CHROME_H` `MAIN_SPACING` `GRID_SPACING` `WEEKDAY_HEADER_H`；`MIN_WEEK` / `MIN_MONTH` |
| 展开保格 | 切换周月时用当前 `cell_*` 重算窗口 | `week_window_size` / `month_window_size` |
| DPI 安全置底 | 只改 Z 序，**不**把 Qt 坐标送进 `SetWindowPos` | `desktop_embed.send_to_bottom` |

保存时机：退出、最小化到托盘、拖完/缩完、锁定切换、展开切换、设置确认、`aboutToQuit`。

### 5. 配置：`defaults` + `session`

文件：`data/config.json`

```json
{
  "defaults": { "...出厂/可手改默认..." },
  "session":  { "...上次退出完整状态，可为空 {}..." }
}
```

| 规则 | 行为 |
|------|------|
| `session` 为空 `{}` | `ConfigStore.get` 读 `defaults` |
| `session` 有内容 | 有值的键用 `session`，其余回落 `defaults` |
| 运行中修改 | 只写入 `session`（`ConfigStore.set`） |
| 恢复默认 | 将 `session` 设为 `{}` 后重启 |

**状态字段一览（`STATE_KEYS`）**

| 键 | 含义 |
|----|------|
| `x` `y` | 窗口位置 |
| `display_w` `display_h` | 实际窗口宽高（冷启动恢复） |
| `window_w` `window_h` | 周视图基准窗口 |
| `cell_w` `cell_h` | 日期格宽高 |
| `expanded` | 是否月视图 |
| `size_locked` | 是否锁定 |
| `opacity` | 不透明度 0.3–1.0 |
| `theme` | 颜色字典（见下） |
| `countries` | 节假日国家代码列表 |

出厂种子：`FACTORY_DEFAULTS`（尺寸来自 `default_week_window()` / `MIN_WEEK`）。

### 6. 锁定 / 托盘 / 置底

| 逻辑 | 说明 | 相关 |
|------|------|------|
| 锁定 | 不可拖、不可边缘缩放、隐藏最小化/关闭钮；右键菜单不可用 | `size_locked`；`_apply_size_lock` |
| 解锁 | 可拖标题区、边缘缩放；右键：最小化、退出 | |
| 托盘退出 | **锁定时也可退出** | 托盘菜单「退出」→ `_quit` |
| 置底 | 定时 `send_to_bottom`，保持在其它窗口之下仍可点 | `_bottom_timer` 间隔 1500ms |

### 7. 主题配色方案

设置里只选方案，点击即预览主窗口。

| 逻辑 | 说明 | 参数名 |
|------|------|--------|
| 预设表 | `THEME_PRESETS` / `THEME_PRESET_LABELS` | 如 `ink_night`→墨夜蓝、`ocean`→海雾蓝、`paper`→纸白 等 |
| 颜色键 | `bg` `text` `muted` `accent` `holiday` `cell` `cell_today` `btn` `btn_hover` `border` | `THEME_KEYS`；合并：`merge_theme` |
| 预览 | 切换单选即回调主窗口刷新 | `on_theme_preview` → `_apply_style` + `refresh_views` |
| 不透明度 | 滑条实时预览 | `opacity`；`on_opacity_preview` |

### 8. 日切时钟

每 60s 检查；仅当自然日变化时把导航跳回今天（`_today_anchor`），避免翻页被重置。

---

## 界面操作

| 控件 | 作用 |
|------|------|
| `‹` / `›` | 上周/下周 或 上月/下月 |
| `▼` / `▲` | 展开本月 / 收起本周 |
| `🔒` / `🔓` | 锁定 / 解锁 |
| `🔄` | 立即同步 iCloud（须先在设置中启用） |
| `⚙` | 设置（国家、配色方案、不透明度、iCloud） |
| `–` `✕` | 最小化到托盘（仅解锁时显示） |
| 托盘 | 显示 / 退出（锁定也可退出） |

---

## 数据目录

```
desktopcalendar/
  data/                         # 可随版本发布
    config.json                 # 仅 defaults（出厂默认）
    holidays/                   # 各国节假日缓存 CC_YEAR.json
    countries.json              # 国家名缓存
  userdata/                     # 个人数据（勿提交 / 勿打包进发行版）
    session.json                # 上次退出状态（窗口、iCloud 选项等）
    todos.json                  # 计划（含重复与 caldav_uid）
    todolist.json               # 今日待办窗口附属数据
    icloud_caldav.json          # iCloud 凭证
    icloud_calendars.json       # 日历列表缓存
```

旧版把 session / todos / 凭证放在 `data/` 时，启动会自动迁到 `userdata/`，并把 `config.json` 收成仅 `defaults`。  
若曾使用 `%APPDATA%\DesktopCalendar\`，首次启动也会迁入上述目录。

发版前清理个人数据：

```bash
python clean_userdata.py          # 清空 userdata/，并保证 data/config 无 session
python build_portable.py          # 便携包不包含 userdata 内容
```

---

## 项目结构

```
desktopcalendar/
  main.py                      # QApplication 入口（QuitOnLastWindowClosed=False）
  requirements.txt
  build_portable.py            # 绿色版 zip（不含用户数据）
  clean_userdata.py            # 发版前清空 userdata
  data/                        # 出厂配置与节假日缓存
  userdata/                    # 个人数据（gitignore）
  app/
    main_window.py             # 主窗：几何、锁定、托盘、置底、设置联动
    services/
      calendar_math.py         # 周日周起点、月 6 行网格
      layout_metrics.py        # 格↔窗尺寸公式与 MIN_*
      config_store.py          # defaults（data）+ session（userdata）
      theme.py                 # 默认色 + 配色方案
      todo_store.py            # 计划持久化与重复展开
      todo_list_store.py       # 独立待办清单
      icloud_calendar_sync.py  # iCloud CalDAV 双向同步
      holiday_service.py       # Nager 拉取与缓存
      desktop_embed.py         # send_to_bottom（仅 Z 序）
    widgets/
      week_view.py / month_view.py / day_cell.py
      todo_dialog.py / todo_list_window.py / settings_dialog.py
```

---

## 关键类与职责

| 类 / 模块 | 职责 |
|-----------|------|
| `MainWindow` | UI 壳、事件过滤缩放、配置落盘、置底 |
| `ConfigStore` | `get`/`set`/`load`/`save`；session 优先于 defaults |
| `HolidayService` | `ensure_years`、`holidays_for`、`set_countries` |
| `TodoStore` | 计划 CRUD、重复展开、`toggle` 按日完成 |
| `TodoListStore` / `TodoListWindow` | 今日计划操作台（投影当日计划、打卡与删当日） |
| `ICloudCalendarSync` | CalDAV 连接、VEVENT 推送/拉取对账 |
| `SettingsDialog` | 国家、配色、透明度、iCloud 同步 |

改默认外观或首次几何：编辑 `data/config.json` 的 **`defaults`**；删除 `userdata/session.json` 即可下次用默认启动。
