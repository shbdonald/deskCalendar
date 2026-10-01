# -*- coding: utf-8 -*-
"""iCloud 日历双向同步（CalDAV VEVENT，支持多日历）。"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .calendar_math import date_key
from .config_store import user_data_dir
from .todo_store import (
    REPEAT_DAILY,
    REPEAT_MONTHLY,
    REPEAT_NONE,
    REPEAT_WEEKLY,
    REPEAT_YEARLY,
    TodoStore,
    parse_ts,
    plan_needs_push,
    utc_now_iso,
)

DEFAULT_CALENDAR_NAME = "桌面计划"
COMPLETED_CALENDAR_NAME = "已完成"
CREDENTIALS_FILE = "icloud_caldav.json"
CALENDARS_CACHE_FILE = "icloud_calendars.json"
X_COMPLETIONS = "X-DESKTOPCAL-COMPLETIONS"
X_EXCEPTIONS = "X-DESKTOPCAL-EXCEPTIONS"
X_DAY_DETAILS = "X-DESKTOPCAL-DAY-DETAILS"
X_KIND = "X-DESKTOPCAL-KIND"
X_UPDATED = "X-DESKTOPCAL-UPDATED"
KIND_PLAN = "plan"
KIND_HOLIDAY = "holiday"
HOLIDAY_UID_PREFIX = "dc-holiday-"


def _ical_text_value(text: str):
    """多行文本写入自定义 X-属性时必须用 vText，否则会触发未转义换行断言。"""
    from icalendar import vText

    return vText(str(text or ""))


def _prop_as_text(raw: Any) -> str:
    """读取 ICS 属性值为普通字符串，并还原 \\n / \\,。"""
    if raw is None:
        return ""
    text = str(raw)
    if hasattr(raw, "to_ical"):
        try:
            text = raw.to_ical().decode("utf-8")
        except Exception:  # noqa: BLE001
            text = str(raw)
    return text.replace("\\n", "\n").replace("\\,", ",").strip()


_REPEAT_TO_FREQ = {
    REPEAT_DAILY: "DAILY",
    REPEAT_WEEKLY: "WEEKLY",
    REPEAT_MONTHLY: "MONTHLY",
    REPEAT_YEARLY: "YEARLY",
}
_FREQ_TO_REPEAT = {v: k for k, v in _REPEAT_TO_FREQ.items()}


@dataclass
class CalendarInfo:
    id: str
    name: str
    writable: bool
    url: str = ""


@dataclass
class RemoteEvent:
    uid: str
    title: str
    start: date
    repeat: str
    completions: list[str]
    status_completed: bool
    kind: str = KIND_PLAN
    calendar_id: str | None = None
    calendar_name: str | None = None
    exceptions: list[str] | None = None
    detail: str = ""
    occurrence_details: dict[str, str] | None = None
    last_modified: datetime | None = None
    etag: str | None = None
    href: str | None = None


def compose_plan_description(plan: dict[str, Any]) -> str:
    """系列备注 + 按日完成情况，写入日历 DESCRIPTION 便于手机查看。"""
    series = str(plan.get("detail") or "").strip()
    occ = plan.get("occurrence_details") or {}
    lines: list[str] = []
    if series:
        lines.append(series)
    if isinstance(occ, dict) and occ:
        if lines:
            lines.append("")
        lines.append("【每日记录】")
        for k in sorted(str(x) for x in occ.keys()):
            text = str(occ.get(k) or "").strip()
            if text:
                lines.append(f"【{k}】{text}")
    return "\n".join(lines)


def is_holiday_uid(uid: str) -> bool:
    """旧版推送的节日事件 UID 前缀；对账时忽略，避免导入成计划。"""
    return str(uid).startswith(HOLIDAY_UID_PREFIX)


def make_calendar_id(url: str) -> str:
    """由日历 URL 生成稳定 id；规范化 scheme/host/path，减少刷新后 id 漂移。"""
    from urllib.parse import urlsplit, urlunsplit

    raw = str(url or "").strip()
    try:
        parts = urlsplit(raw)
        path = (parts.path or "").rstrip("/") or "/"
        netloc = (parts.netloc or "").casefold()
        scheme = (parts.scheme or "https").casefold()
        norm = urlunsplit((scheme, netloc, path, "", ""))
    except Exception:  # noqa: BLE001
        norm = raw.rstrip("/")
    return "cal_" + hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def _is_system_readonly_calendar(name: str, url: str) -> bool:
    """排除通讯录「生日」等系统只读日历；用户自建同名日历仍保留。"""
    u = (url or "").lower()
    # Apple 通讯录生日日历 URL 通常含 birthday(s)
    if "birthday" in u:
        return True
    n = (name or "").strip().casefold()
    # 系统延后收件箱一类
    if n in {"snoozed", "已延后"}:
        return True
    return False


def _calendar_looks_writable(cal: Any) -> bool:
    """尽量用权限探测；失败则视为可写（避免误伤用户日历）。"""
    try:
        from caldav.lib import namespace as ns

        props = cal.get_properties(
            [
                f"{{{ns.DAV}}}current-user-privilege-set",
            ]
        )
        raw = str(props).casefold()
        if "privilege" in raw or "write" in raw or "read" in raw:
            # 明确只有 read、没有 write 相关 → 只读
            has_write = any(
                k in raw
                for k in (
                    "write-content",
                    "write-properties",
                    "bind",
                    "unbind",
                    ">write<",
                    "write-acl",
                )
            )
            has_read = "read" in raw
            if has_read and not has_write and "write" not in raw:
                return False
            if has_write:
                return True
    except Exception:  # noqa: BLE001
        pass
    return True


class ICloudCalendarSync:
    """读写 iCloud 多个日历中的全天 VEVENT，并与 TodoStore 对账。"""

    def __init__(self) -> None:
        self._client = None
        self._principal = None
        self._calendars: dict[str, Any] = {}  # id -> caldav Calendar
        self._calendar_meta: dict[str, CalendarInfo] = {}
        self._recent_push: dict[str, float] = {}
        self._push_guard_seconds = 8.0

    def credentials_path(self):
        return user_data_dir() / CREDENTIALS_FILE

    def calendars_cache_path(self):
        return user_data_dir() / CALENDARS_CACHE_FILE

    def load_credentials(self) -> dict[str, str]:
        path = self.credentials_path()
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def save_credentials(self, *, apple_id: str, app_password: str) -> None:
        path = self.credentials_path()
        path.write_text(
            json.dumps(
                {
                    "apple_id": apple_id.strip(),
                    "app_password": app_password.strip(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def clear_credentials(self) -> None:
        path = self.credentials_path()
        if path.exists():
            path.unlink()
        self.disconnect()

    def disconnect(self) -> None:
        self._client = None
        self._principal = None
        self._calendars.clear()
        self._calendar_meta.clear()

    def _ensure_client(self) -> None:
        if self._client is not None and self._principal is not None:
            return
        from caldav import DAVClient

        creds = self.load_credentials()
        apple_id = str(creds.get("apple_id", "")).strip()
        password = str(creds.get("app_password", "")).strip()
        if not apple_id or not password:
            raise RuntimeError("请先在设置中填写 Apple ID 和应用专用密码")
        self._client = DAVClient(
            url="https://caldav.icloud.com/",
            username=apple_id,
            password=password,
        )
        self._principal = self._client.principal()

    def connect(self, *, calendar_name: str | None = None) -> None:
        """建立连接并刷新日历索引。不再因名称缺失而自动创建日历。"""
        self._ensure_client()
        self.refresh_calendars()
        # calendar_name 仅用于兼容旧调用签名，不自动 ensure

    def ensure_connected(self, *, calendar_name: str | None = None) -> None:
        if self._client is None or self._principal is None:
            self.connect(calendar_name=calendar_name)
        elif not self._calendars:
            self.refresh_calendars()

    def test_connection(self, *, calendar_name: str | None = None) -> str:
        self.connect(calendar_name=calendar_name)
        n = len(self._calendar_meta)
        return f"已连接 iCloud，发现可同步日历 {n} 个"

    @staticmethod
    def _calendar_display_name(cal: Any) -> str:
        try:
            return str(cal.get_display_name() or "").strip()
        except Exception:  # noqa: BLE001
            pass
        try:
            return str(getattr(cal, "name", "") or "").strip()
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _calendar_url(cal: Any) -> str:
        try:
            return str(cal.url)
        except Exception:  # noqa: BLE001
            return str(id(cal))

    def refresh_calendars(self) -> list[CalendarInfo]:
        """从服务器刷新可写日历列表并缓存。"""
        self._ensure_client()
        assert self._principal is not None
        self._calendars.clear()
        self._calendar_meta.clear()
        result: list[CalendarInfo] = []
        for cal in self._principal.calendars():
            name = self._calendar_display_name(cal)
            if not name:
                continue
            url = self._calendar_url(cal)
            if _is_system_readonly_calendar(name, url):
                continue
            writable = _calendar_looks_writable(cal)
            if not writable:
                continue
            cid = make_calendar_id(url)
            info = CalendarInfo(id=cid, name=name, writable=True, url=url)
            self._calendars[cid] = cal
            self._calendar_meta[cid] = info
            result.append(info)
        result.sort(key=lambda c: c.name.casefold())
        self._save_calendars_cache(result)
        return result

    def list_calendars(self, *, use_cache: bool = True) -> list[CalendarInfo]:
        if self._calendar_meta:
            return sorted(self._calendar_meta.values(), key=lambda c: c.name.casefold())
        if use_cache:
            cached = self._load_calendars_cache()
            if cached:
                return cached
        return self.refresh_calendars()

    def _save_calendars_cache(self, items: list[CalendarInfo]) -> None:
        path = self.calendars_cache_path()
        payload = {
            "calendars": [
                {"id": c.id, "name": c.name, "writable": c.writable, "url": c.url}
                for c in items
            ]
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_calendars_cache(self) -> list[CalendarInfo]:
        path = self.calendars_cache_path()
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        items = raw.get("calendars") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return []
        out: list[CalendarInfo] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            cid = str(it.get("id") or "")
            name = str(it.get("name") or "")
            if not cid or not name:
                continue
            out.append(
                CalendarInfo(
                    id=cid,
                    name=name,
                    writable=bool(it.get("writable", True)),
                    url=str(it.get("url") or ""),
                )
            )
        return out

    def ensure_calendar(self, name: str) -> CalendarInfo:
        """按显示名查找日历；没有则创建。"""
        name = name.strip() or DEFAULT_CALENDAR_NAME
        self._ensure_client()
        if not self._calendars:
            self.refresh_calendars()
        for info in self._calendar_meta.values():
            if info.name == name:
                return info
        assert self._principal is not None
        try:
            cal = self._principal.make_calendar(name=name)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"创建日历「{name}」失败：{exc}") from exc
        url = self._calendar_url(cal)
        cid = make_calendar_id(url)
        info = CalendarInfo(id=cid, name=name, writable=True, url=url)
        self._calendars[cid] = cal
        self._calendar_meta[cid] = info
        self._save_calendars_cache(list(self._calendar_meta.values()))
        return info

    def resolve_calendar_id(
        self,
        *,
        calendar_id: str | None = None,
        calendar_name: str | None = None,
        fallback_name: str = DEFAULT_CALENDAR_NAME,
        create_if_missing: bool = False,
    ) -> str:
        """解析日历 id。默认不自动创建；仅 create_if_missing=True 时才会新建。"""
        if calendar_id and calendar_id in self._calendars:
            return calendar_id
        if calendar_id and not self._calendars:
            self.refresh_calendars()
            if calendar_id in self._calendars:
                return calendar_id
        if calendar_id and calendar_id not in self._calendars:
            # id 失效（可能已在 iPhone 删除）— 刷新后再试，仍无则改按名称
            self.refresh_calendars()
            if calendar_id in self._calendars:
                return calendar_id
        name = (calendar_name or fallback_name).strip() or fallback_name
        for cid, info in self._calendar_meta.items():
            if info.name == name and cid in self._calendars:
                return cid
        if create_if_missing:
            return self.ensure_calendar(name).id
        hint = calendar_id or name
        raise RuntimeError(f"日历不存在或已在 iCloud 删除：{hint}")

    def _cal(self, calendar_id: str) -> Any:
        if calendar_id not in self._calendars:
            self.refresh_calendars()
        cal = self._calendars.get(calendar_id)
        if cal is None:
            raise RuntimeError(f"未找到日历 id={calendar_id}（可能已在 iCloud 删除）")
        return cal

    def list_events(self, calendar_id: str | None = None) -> list[RemoteEvent]:
        if calendar_id:
            return self._list_events_for(calendar_id)
        events: list[RemoteEvent] = []
        for cid in list(self._calendars.keys()):
            events.extend(self._list_events_for(cid))
        return events

    def _list_events_for(self, calendar_id: str) -> list[RemoteEvent]:
        cal = self._cal(calendar_id)
        meta = self._calendar_meta.get(calendar_id)
        cname = meta.name if meta else None
        events: list[RemoteEvent] = []
        for item in cal.events():
            try:
                data = item.data
                if data is None:
                    continue
                ical_text = data if isinstance(data, str) else data.decode("utf-8", errors="replace")
                remote = self._parse_vevent(ical_text)
                if remote is None:
                    continue
                remote.calendar_id = calendar_id
                remote.calendar_name = cname
                etag = None
                try:
                    etag = str(item.get_etag() or "") or None
                except Exception:  # noqa: BLE001
                    etag = getattr(item, "etag", None)
                    if etag is not None:
                        etag = str(etag)
                remote.etag = etag
                try:
                    remote.href = str(item.url) if item.url else None
                except Exception:  # noqa: BLE001
                    remote.href = None
                events.append(remote)
            except Exception:  # noqa: BLE001
                continue
        return events

    def upsert_plan(self, plan: dict[str, Any]) -> tuple[str, str | None]:
        """推送计划到其所属日历，返回 (uid, etag)。"""
        from icalendar import Calendar, Event

        calendar_id = self.resolve_calendar_id(
            calendar_id=str(plan.get("calendar_id") or "") or None,
            calendar_name=str(plan.get("calendar_name") or "") or None,
            create_if_missing=False,
        )
        cal_obj = self._cal(calendar_id)

        uid = str(plan.get("caldav_uid") or "").strip() or str(uuid.uuid4())
        start = date.fromisoformat(str(plan["start"]))
        title = str(plan.get("title", "")).strip() or "计划"
        detail_body = compose_plan_description(plan)
        repeat = str(plan.get("repeat", REPEAT_NONE))
        completions = [str(c) for c in plan.get("completions", []) if c]
        exceptions = [str(c) for c in plan.get("exceptions", []) if c]
        updated = parse_ts(plan.get("updated_at")) or datetime.now(timezone.utc)

        cal = Calendar()
        cal.add("prodid", "-//DesktopCalendar//CN")
        cal.add("version", "2.0")
        ev = Event()
        ev.add("uid", uid)
        ev.add("summary", title)
        if detail_body:
            ev.add("description", detail_body)
        occ = plan.get("occurrence_details") or {}
        if isinstance(occ, dict) and occ:
            ev.add(X_DAY_DETAILS, _ical_text_value(json.dumps(occ, ensure_ascii=False)))
        series_only = str(plan.get("detail") or "").strip()
        if series_only:
            ev.add("X-DESKTOPCAL-DETAIL", _ical_text_value(series_only))
        ev.add("dtstart", start)
        ev.add("dtend", start + timedelta(days=1))
        ev.add("dtstamp", datetime.now(timezone.utc))
        ev.add("last-modified", updated)
        ev.add(X_UPDATED, updated.replace(microsecond=0).isoformat())
        freq = _REPEAT_TO_FREQ.get(repeat)
        if freq:
            ev.add("rrule", {"FREQ": freq})
        if completions:
            ev.add(X_COMPLETIONS, ",".join(completions))
        if exceptions:
            ev.add(X_EXCEPTIONS, ",".join(exceptions))
            for ex_s in exceptions:
                try:
                    ev.add("exdate", date.fromisoformat(ex_s))
                except ValueError:
                    continue
        ev.add(X_KIND, KIND_PLAN)
        if repeat == REPEAT_NONE and date_key(start) in completions:
            ev.add("status", "COMPLETED")
        else:
            ev.add("status", "CONFIRMED")
        cal.add_component(ev)
        ical_bytes = cal.to_ical()

        # 改所属日历：先删其它日历上的同 UID，避免 iCloud 412 UID 冲突
        existing_copies = self._find_events_by_uid(uid) if uid else []
        on_target = next(
            (item for cid, item in existing_copies if cid == calendar_id),
            None,
        )
        for cid, old_item in existing_copies:
            if cid == calendar_id:
                continue
            try:
                old_item.delete()
            except Exception:  # noqa: BLE001
                continue

        try:
            if on_target is not None:
                item = self._save_event_overwrite(on_target, ical_bytes, cal_obj)
            else:
                item = self._add_event_bytes(cal_obj, ical_bytes, uid=uid)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(self._friendly_put_error(exc)) from exc

        etag = None
        try:
            etag = str(item.get_etag() or "") or None
        except Exception:  # noqa: BLE001
            etag = None
        self._mark_pushed(uid)
        # 回写计划所属日历 id（调用方可用 set_calendar）
        plan["calendar_id"] = calendar_id
        meta = self._calendar_meta.get(calendar_id)
        if meta:
            plan["calendar_name"] = meta.name
        return uid, etag

    @staticmethod
    def _clear_preconditions(item: Any) -> None:
        """去掉缓存的 ETag / Schedule-Tag，避免陈旧 If-Match 触发 412。"""
        try:
            from caldav.elements import cdav, dav

            props = getattr(item, "props", None)
            if isinstance(props, dict):
                props.pop(dav.GetEtag.tag, None)
                props.pop(cdav.ScheduleTag.tag, None)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _friendly_put_error(exc: BaseException) -> str:
        text = str(exc) or type(exc).__name__
        low = text.lower()
        if "412" in text or "precondition" in low or "etag" in low:
            return (
                "iCloud 写入冲突（412）：远端事件已被其它设备改过。"
                "已自动重试；若仍失败请稍后再同步。"
            )
        if "puterror" in low or "put error" in low:
            return f"写入 iCloud 失败：{text}"
        return text

    def _add_event_bytes(
        self,
        cal_obj: Any,
        ical_bytes: bytes,
        *,
        uid: str | None = None,
    ) -> Any:
        try:
            if hasattr(cal_obj, "add_event"):
                return cal_obj.add_event(ical_bytes)
            return cal_obj.save_event(ical_bytes)
        except Exception as exc:  # noqa: BLE001
            text = str(exc).lower()
            if uid and ("412" in text or "precondition" in text or "uid" in text):
                # 可能仍有残留副本：清掉后重建
                for _cid, old in self._find_events_by_uid(uid):
                    try:
                        old.delete()
                    except Exception:  # noqa: BLE001
                        continue
                if hasattr(cal_obj, "add_event"):
                    return cal_obj.add_event(ical_bytes)
                return cal_obj.save_event(ical_bytes)
            raise

    def _save_event_overwrite(self, item: Any, ical_bytes: bytes, cal_obj: Any) -> Any:
        """更新已有事件；遇 412/ETag 冲突则清 precondition 重试，再不行则删后重建。"""
        from caldav.lib.error import ETagMismatchError, PutError, ScheduleTagMismatchError

        item.data = ical_bytes
        try:
            item.save()
            return item
        except (ETagMismatchError, ScheduleTagMismatchError, PutError):
            pass
        except Exception as exc:  # noqa: BLE001
            # 部分环境下 412 只包成普通异常
            if "412" not in str(exc) and "precondition" not in str(exc).lower():
                raise

        self._clear_preconditions(item)
        item.data = ical_bytes
        try:
            item.save()
            return item
        except Exception:  # noqa: BLE001
            pass

        try:
            item.delete()
        except Exception:  # noqa: BLE001
            pass
        return self._add_event_bytes(cal_obj, ical_bytes)

    def delete_event(self, uid: str, *, calendar_id: str | None = None) -> None:
        item = self._find_event_by_uid(uid, calendar_id=calendar_id)
        if item is not None:
            item.delete()
            self._mark_pushed(uid)

    def reconcile_with_remotes(
        self,
        store: TodoStore,
        remotes: list[RemoteEvent],
        *,
        scope_calendar_ids: set[str] | None = None,
    ) -> bool:
        """对账。scope_calendar_ids 非空时，只处理属于这些日历的本地计划删除判定。
        传入空 set 表示「本次未拉取任何日历」，禁止做删除对账（避免误清空）。
        传入 None 表示不限制日历范围（慎用）。
        """
        by_uid = {r.uid: r for r in remotes}
        changed = False
        now = datetime.now(timezone.utc).timestamp()

        # 空 scope：只合并远端新增/更新，绝不因「没拉到」而删本地
        can_delete_missing = scope_calendar_ids is None or len(scope_calendar_ids) > 0

        for plan in list(store.all_plans()):
            uid = str(plan.get("caldav_uid") or "").strip()
            if not uid:
                continue
            plan_cal = str(plan.get("calendar_id") or "")
            if scope_calendar_ids is not None and plan_cal and plan_cal not in scope_calendar_ids:
                continue
            if not can_delete_missing:
                continue
            if uid not in by_uid:
                if self._was_recently_pushed(uid, now):
                    continue
                if plan_needs_push(plan):
                    continue
                store.delete(plan["id"])
                changed = True

        local_by_uid = {
            str(p.get("caldav_uid")): p
            for p in store.all_plans()
            if p.get("caldav_uid")
        }

        for remote in remotes:
            if remote.kind == KIND_HOLIDAY or is_holiday_uid(remote.uid):
                continue
            if self._was_recently_pushed(remote.uid, now):
                continue
            local = local_by_uid.get(remote.uid)
            remote_iso = (
                remote.last_modified.replace(microsecond=0).isoformat()
                if remote.last_modified
                else utc_now_iso()
            )
            if local is None:
                store.add_from_remote(
                    title=remote.title,
                    start=remote.start,
                    repeat=remote.repeat,
                    completions=remote.completions,
                    caldav_uid=remote.uid,
                    caldav_etag=remote.etag,
                    remote_updated_at=remote_iso,
                    calendar_id=remote.calendar_id,
                    calendar_name=remote.calendar_name,
                    exceptions=list(remote.exceptions or []),
                    detail=remote.detail or "",
                    occurrence_details=dict(remote.occurrence_details or {}),
                )
                changed = True
                continue
            if not self._remote_differs(local, remote):
                # 仅补空的日历字段；勿把本地已改的所属日历盖回远端旧值
                if remote.calendar_id and not local.get("calendar_id"):
                    store.set_calendar(
                        local["id"],
                        calendar_id=remote.calendar_id,
                        calendar_name=remote.calendar_name,
                        touch=False,
                    )
                    changed = True
                continue
            local_ts = parse_ts(local.get("updated_at"))
            remote_ts = remote.last_modified
            if local_ts and remote_ts and local_ts > remote_ts:
                continue
            if plan_needs_push(local) and local_ts and (remote_ts is None or local_ts >= remote_ts):
                continue
            store.apply_remote(
                local["id"],
                title=remote.title,
                start=remote.start,
                repeat=remote.repeat,
                completions=remote.completions,
                caldav_etag=remote.etag,
                remote_updated_at=remote_iso,
                calendar_id=remote.calendar_id,
                calendar_name=remote.calendar_name,
                exceptions=list(remote.exceptions or []),
                detail=remote.detail or "",
                occurrence_details=dict(remote.occurrence_details or {}),
            )
            changed = True

        return changed

    def _was_recently_pushed(self, uid: str, now: float | None = None) -> bool:
        now = now if now is not None else datetime.now(timezone.utc).timestamp()
        ts = self._recent_push.get(uid)
        if ts is None:
            return False
        if now - ts > self._push_guard_seconds:
            self._recent_push.pop(uid, None)
            return False
        return True

    def _mark_pushed(self, uid: str) -> None:
        self._recent_push[uid] = datetime.now(timezone.utc).timestamp()

    def _find_event_by_uid(self, uid: str, *, calendar_id: str | None = None) -> Any | None:
        if calendar_id:
            hits = self._find_events_by_uid(uid, calendar_ids=[calendar_id])
        else:
            hits = self._find_events_by_uid(uid)
        return hits[0][1] if hits else None

    def _find_events_by_uid(
        self,
        uid: str,
        *,
        calendar_ids: list[str] | None = None,
    ) -> list[tuple[str, Any]]:
        """在指定或全部日历中查找同一 UID 的事件（用于换日历迁移）。"""
        if calendar_ids is not None:
            cals: list[tuple[str, Any]] = []
            for cid in calendar_ids:
                try:
                    cals.append((cid, self._cal(cid)))
                except RuntimeError:
                    continue
        else:
            if not self._calendars:
                self.refresh_calendars()
            cals = list(self._calendars.items())

        found: list[tuple[str, Any]] = []
        for cid, cal in cals:
            try:
                item = cal.event_by_uid(uid)
                if item is not None:
                    found.append((cid, item))
                    continue
            except Exception:  # noqa: BLE001
                pass
            try:
                for item in cal.events():
                    data = item.data
                    if data is None:
                        continue
                    ical_text = (
                        data
                        if isinstance(data, str)
                        else data.decode("utf-8", errors="replace")
                    )
                    remote = self._parse_vevent(ical_text)
                    if remote and remote.uid == uid:
                        found.append((cid, item))
                        break
            except Exception:  # noqa: BLE001
                continue
        return found

    @staticmethod
    def _remote_differs(local: dict[str, Any], remote: RemoteEvent) -> bool:
        if str(local.get("title", "")) != remote.title:
            return True
        if str(local.get("start", "")) != date_key(remote.start):
            return True
        if str(local.get("repeat", REPEAT_NONE)) != remote.repeat:
            return True
        if str(local.get("calendar_id") or "") != str(remote.calendar_id or ""):
            return True
        local_comp = [str(c) for c in local.get("completions", [])]
        if sorted(local_comp) != sorted(remote.completions):
            return True
        local_ex = [str(c) for c in local.get("exceptions", [])]
        remote_ex = [str(c) for c in (remote.exceptions or [])]
        if sorted(local_ex) != sorted(remote_ex):
            return True
        if str(local.get("detail") or "").strip() != str(remote.detail or "").strip():
            return True
        local_occ = local.get("occurrence_details") or {}
        remote_occ = remote.occurrence_details or {}
        if not isinstance(local_occ, dict):
            local_occ = {}
        if sorted((str(k), str(v)) for k, v in local_occ.items()) != sorted(
            (str(k), str(v)) for k, v in remote_occ.items()
        ):
            return True
        return False

    def _parse_vevent(self, ical_text: str) -> RemoteEvent | None:
        from icalendar import Calendar

        cal = Calendar.from_ical(ical_text)
        for component in cal.walk():
            if component.name != "VEVENT":
                continue
            uid = str(component.get("uid", "") or "").strip()
            if not uid:
                continue
            summary = str(component.get("summary", "") or "").strip() or "计划"
            detail = ""
            occurrence_details: dict[str, str] = {}
            raw_day = component.get(X_DAY_DETAILS)
            if raw_day is not None:
                text = _prop_as_text(raw_day)
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        for k, v in parsed.items():
                            if k and str(v).strip():
                                occurrence_details[str(k)] = str(v).strip()
                except json.JSONDecodeError:
                    pass
            raw_series = component.get("X-DESKTOPCAL-DETAIL")
            if raw_series is not None:
                detail = _prop_as_text(raw_series)
            raw_desc = component.get("description")
            if raw_desc is not None and not detail and not occurrence_details:
                detail = _prop_as_text(raw_desc)
            elif raw_desc is not None and not detail:
                # 有按日记录时，系列备注可能只在 X-DESKTOPCAL-DETAIL
                pass
            dtstart = component.get("dtstart")
            start = self._as_date(dtstart.dt if dtstart else None)
            if start is None:
                continue
            repeat = REPEAT_NONE
            rrule = component.get("rrule")
            if rrule:
                try:
                    freq = rrule.get("FREQ")
                    if isinstance(freq, list) and freq:
                        freq = freq[0]
                    freq_s = str(freq or "").upper()
                    repeat = _FREQ_TO_REPEAT.get(freq_s, REPEAT_NONE)
                except Exception:  # noqa: BLE001
                    repeat = REPEAT_NONE
            completions: list[str] = []
            raw_x = component.get(X_COMPLETIONS)
            if raw_x is not None:
                text = str(raw_x)
                if hasattr(raw_x, "to_ical"):
                    try:
                        text = raw_x.to_ical().decode("utf-8")
                    except Exception:  # noqa: BLE001
                        text = str(raw_x)
                for part in text.replace("\\,", ",").split(","):
                    part = part.strip()
                    if part:
                        completions.append(part)
            exceptions: list[str] = []
            raw_ex = component.get(X_EXCEPTIONS)
            if raw_ex is not None:
                text = str(raw_ex)
                if hasattr(raw_ex, "to_ical"):
                    try:
                        text = raw_ex.to_ical().decode("utf-8")
                    except Exception:  # noqa: BLE001
                        text = str(raw_ex)
                for part in text.replace("\\,", ",").split(","):
                    part = part.strip()
                    if part:
                        exceptions.append(part)
            try:
                exdates = component.get("exdate")
                if exdates is not None:
                    items = exdates if isinstance(exdates, list) else [exdates]
                    for item in items:
                        dts = getattr(item, "dts", None)
                        if dts:
                            for entry in dts:
                                dd = self._as_date(getattr(entry, "dt", entry))
                                if dd is not None:
                                    exceptions.append(date_key(dd))
                        else:
                            dd = self._as_date(getattr(item, "dt", item))
                            if dd is not None:
                                exceptions.append(date_key(dd))
            except Exception:  # noqa: BLE001
                pass
            seen_ex: set[str] = set()
            uniq_ex: list[str] = []
            for e in exceptions:
                if e not in seen_ex:
                    seen_ex.add(e)
                    uniq_ex.append(e)
            exceptions = uniq_ex
            status = str(component.get("status", "") or "").upper()
            status_completed = status == "COMPLETED"
            if status_completed and repeat == REPEAT_NONE and date_key(start) not in completions:
                completions.append(date_key(start))
            kind = KIND_PLAN
            raw_kind = component.get(X_KIND)
            if raw_kind is not None:
                kind = str(raw_kind).strip().lower() or KIND_PLAN
            if is_holiday_uid(uid):
                kind = KIND_HOLIDAY
            last_modified = None
            raw_updated = component.get(X_UPDATED)
            if raw_updated is not None:
                text = str(raw_updated)
                if hasattr(raw_updated, "to_ical"):
                    try:
                        text = raw_updated.to_ical().decode("utf-8")
                    except Exception:  # noqa: BLE001
                        text = str(raw_updated)
                last_modified = parse_ts(text)
            if last_modified is None:
                lm = component.get("last-modified")
                if lm is not None and hasattr(lm, "dt"):
                    last_modified = parse_ts(lm.dt)
            if last_modified is None:
                ds = component.get("dtstamp")
                if ds is not None and hasattr(ds, "dt"):
                    last_modified = parse_ts(ds.dt)
            return RemoteEvent(
                uid=uid,
                title=summary,
                start=start,
                repeat=repeat,
                completions=completions,
                status_completed=status_completed,
                kind=kind,
                exceptions=exceptions,
                detail=detail,
                occurrence_details=occurrence_details,
                last_modified=last_modified,
            )
        return None

    @staticmethod
    def _as_date(value: Any) -> date | None:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return None
