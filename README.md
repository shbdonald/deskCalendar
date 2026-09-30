# 桌面日历（Desktop Calendar）

Windows 桌面日历小组件，基于 **Python + PySide6**。  
默认显示本周（周日→周六），可展开为本月；格内显示待办与多国节假日。窗口置底、可锁定，配置保存在项目 `data/` 目录。

## 环境与运行

- Windows 10 / 11 · Python 3.10+（推荐 3.11+）
- 依赖：`PySide6`、`httpx`

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

### 2. 待办

| 逻辑 | 说明 | 关键名 |
|------|------|--------|
| 存储 | 按日期 ISO 键写入 JSON | `todos.json`；`TodoStore`；键：`date_key` → `YYYY-MM-DD` |
| 项结构 | `id` / `title` / `done` | `TodoStore.add` / `update` / `toggle` / `delete` |
| 单击 | 220ms 防抖后切换完成 | `TodoLine` 定时器间隔 `220`；信号 `clicked` |
| 双击待办 | 编辑或删除 | 信号 `double_clicked` → `TodoEditDialog` |
| 双击空白格 | 新建 | `DayCell.day_add` |

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
| `⚙` | 设置（国家、配色方案、不透明度） |
| `–` `✕` | 最小化到托盘（仅解锁时显示） |
| 托盘 | 显示 / 退出（锁定也可退出） |

---

## 数据目录

```
desktopcalendar/data/
  config.json      # defaults + session
  todos.json       # 待办
  holidays/        # 各国节假日缓存 CC_YEAR.json
```

若曾使用 `%APPDATA%\DesktopCalendar\`，首次启动会迁移到 `data/`。

---

## 项目结构

```
desktopcalendar/
  main.py                      # QApplication 入口（QuitOnLastWindowClosed=False）
  requirements.txt
  data/                        # 运行时数据（自动生成）
  app/
    main_window.py             # 主窗：几何、锁定、托盘、置底、设置联动
    services/
      calendar_math.py         # 周日周起点、月 6 行网格
      layout_metrics.py        # 格↔窗尺寸公式与 MIN_*
      config_store.py          # defaults/session 读写
      theme.py                 # 默认色 + 配色方案
      todo_store.py            # 待办持久化
      holiday_service.py       # Nager 拉取与缓存
      desktop_embed.py         # send_to_bottom（仅 Z 序）
    widgets/
      week_view.py / month_view.py / day_cell.py
      todo_dialog.py / settings_dialog.py
```

---

## 关键类与职责

| 类 / 模块 | 职责 |
|-----------|------|
| `MainWindow` | UI 壳、事件过滤缩放、配置落盘、置底 |
| `ConfigStore` | `get`/`set`/`load`/`save`；session 优先 |
| `HolidayService` | `ensure_years`、`holidays_for`、`set_countries` |
| `TodoStore` | 按日 CRUD + `toggle` |
| `SettingsDialog` | 国家多选、方案单选实时预览、透明度 |

改默认外观或首次几何：编辑 `data/config.json` 的 **`defaults`**；清空 **`session`** 即可下次用默认启动。
