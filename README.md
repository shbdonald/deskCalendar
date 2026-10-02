# 桌面日历（Desktop Calendar）

Windows 桌面日历小组件：**Python + PySide6**。  
周/月视图、本地与 iCloud 计划、多国节假日；窗口置底可锁定。出厂配置在 `data/`，个人数据在 `userdata/`（不进 Git）。

版本记录：[CHANGELOG.md](CHANGELOG.md) · 仓库：https://github.com/shbdonald/deskCalendar

回滚到「重复计划」之前的基线：

```bash
git checkout baseline-pre-plans
```

---

## 环境与运行

- Windows 10 / 11 · Python 3.10+（推荐 3.11+）
- 依赖：`PySide6`、`httpx`、`caldav`、`icalendar`

```bash
cd desktopcalendar
pip install -r requirements.txt
python main.py
```

开发时改完代码后重启，推荐：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\restart_app.ps1
```

（会先结束旧实例；`main.py` 也会用 `userdata/instance.pid` 保证单实例。）

---

## 绿色便携包

```bash
pip install pyinstaller
python build_portable.py
```

- 文件夹：`release/DesktopCalendar/`（含 `DesktopCalendar.exe`）
- 压缩包：`release/DesktopCalendar-portable.zip`

解压即用；发版前可 `python clean_userdata.py` 清空个人数据。便携包不含 `userdata/`。

---

## 功能概览

### 日历视图

| 能力 | 说明 |
|------|------|
| 周 / 月 | 默认本周；`▼` 展开本月、`▲` 收起 |
| 每周第一天 | 设置 → 外观；默认星期日（可改一～日） |
| 翻页 | `‹` / `›`：周 ±7 天，月上/下月 |
| 回到当日 | 顶栏 **「今」**；跨自然日时也会自动跳回今天 |
| 月网格 | 固定 6×7，格子尺寸稳定 |

### 计划与日历标签

| 能力 | 说明 |
|------|------|
| 本地日历 | 内置「本地日历」，**永不上传**；可不登录 Apple 使用 |
| 显示 / 同步 | 设置里分列勾选：显示=主界面可见；同步=与 iCloud 双向 |
| 新建日历 | 可不登录；登录后也可在 iCloud 侧新建 |
| 默认日历 | 「新建计划默认」下拉；无默认时新建会提示 |
| 重复计划 | 按规则投影到日期格；双击编辑/删除系列或当日 |
| 双击空白格 | 新建计划 |

计划文件：`userdata/todos.json`（`TodoStore`）。

### iCloud（CalDAV）

设置 → **iCloud**（账号 / 同步 / 日历 分区）：

1. 添加 Apple ID + [应用专用密码](https://appleid.apple.com)
2. 校验通过后勾选要**显示**与**同步**的日历
3. 「启用同步」为总开关；打开后按「同步」列与 iCloud 双向同步
4. 轮询间隔可调（默认 45 秒）；顶栏 `🔄` 或「立即同步」可手动拉取

| 说明 | |
|------|--|
| 协议 | 全天 VEVENT（可含 RRULE） |
| 生日等 | 通讯录系统生日日历不列出；自建同名日历可同步 |
| 节日 | **不同步**到 iCloud；桌面仍用 Nager 显示 |
| 凭证 | `userdata/icloud_caldav.json`（不入库） |
| 清除账号 | 可勾选保留哪些日历的本地计划 |

### 今日待办

独立小窗，投影**当天**计划；设置可开关（默认开）。与主日历吸附（上/下/左/右），可拖开。

- 勾选打卡（周期计划可拆「今日单日」或写系列备注）
- 删除仅影响今日（重复写入 exceptions）
- 日历格子**不能**单击打卡；双击仍可编辑

### 节假日

[Nager.Date](https://date.nager.at) 公共假日；设置 → 节假日多选国家（出厂默认不选）。缓存：`data/holidays/`、`data/countries.json`。

### 窗口与系统

| 能力 | 说明 |
|------|------|
| 置底 | 保持在其它窗口之下仍可点 |
| 锁定 | 不可拖/缩/关；托盘仍可退出 |
| 托盘 | 显示 / 退出 |
| 开机自启 | 设置 → 外观（当前用户注册表 Run） |
| 主题 / 透明度 | 外观页即时预览 |

---

## 界面控件

| 控件 | 作用 |
|------|------|
| `‹` / `›` | 上一周期 / 下一周期 |
| **今** | 回到当日所在周或月 |
| `▼` / `▲` | 展开本月 / 收起本周 |
| `🔒` / `🔓` | 锁定 / 解锁 |
| `🔄` | 立即同步 iCloud |
| `⚙` | 设置（外观 / 节假日 / iCloud） |
| `–` `✕` | 最小化到托盘（解锁时） |

---

## 数据目录

```
desktopcalendar/
  data/                         # 可随版本发布
    config.json                 # 仅 defaults（出厂默认）
    holidays/                   # 节假日缓存 CC_YEAR.json
    countries.json
  userdata/                     # 个人数据（gitignore）
    session.json                # 上次窗口/选项等
    todos.json                  # 计划
    todolist.json
    icloud_caldav.json          # 凭证
    icloud_calendars.json       # 日历列表缓存
    instance.pid                # 单实例
```

配置：`data/config.json` 的 `defaults` + `userdata/session.json`。`session` 为空则用 defaults；运行中改动写入 session。删掉 session 即恢复出厂几何与选项。

主要配置键（节选）：`week_starts_on`（0=周一…6=周日）、`expanded`、`opacity`、`theme`、`countries`、`icloud_*`、`local_calendars`、`todolist_*`、`start_with_windows`。

旧版个人文件在 `data/` 或 `%APPDATA%\DesktopCalendar\` 时，启动会自动迁到 `userdata/`。

---

## 项目结构

```
desktopcalendar/
  main.py
  requirements.txt
  build_portable.py / clean_userdata.py
  scripts/restart_app.ps1
  data/ · userdata/
  app/
    main_window.py
    services/
      calendar_math.py          # 周起点、月网格
      local_calendar.py         # 内置本地日历
      layout_metrics.py         # 格↔窗尺寸
      config_store.py           # defaults + session
      theme.py · todo_store.py · todo_list_store.py
      icloud_calendar_sync.py · holiday_service.py
      desktop_embed.py · winamp_dock.py · autostart.py
    widgets/
      week_view.py · month_view.py · day_cell.py
      todo_dialog.py · todo_list_window.py · settings_dialog.py
```

| 模块 | 职责 |
|------|------|
| `MainWindow` | 主窗、导航、托盘、置底、设置联动 |
| `ConfigStore` | get/set；session 优先于 defaults |
| `TodoStore` | 计划 CRUD、重复展开、打卡 |
| `ICloudCalendarSync` | CalDAV 推送/拉取 |
| `local_calendar` | 本地日历 id / 过滤 |
| `SettingsDialog` | 外观 · 节假日 · iCloud |
| `HolidayService` | Nager 拉取与缓存 |

改默认外观或首次几何：编辑 `data/config.json` 的 **`defaults`**。
