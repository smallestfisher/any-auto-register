from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from application.account_exports import AccountExportsService
from application.account_checks import AccountChecksService
from application.accounts import AccountsService
from application.config import ConfigService
from application.provider_definitions import ProviderDefinitionsService
from application.provider_settings import ProviderSettingsService
from application.platforms import PlatformsService
from application.task_commands import TaskCommandsService
from application.tasks import TERMINAL_TASK_STATUSES
from application.tasks_query import TasksQueryService
from bootstrap import RuntimeManager, initialize_core
from core.lifecycle import check_accounts_validity, flag_expiring_trials, lifecycle_manager, refresh_expiring_tokens
from domain.accounts import AccountCreateCommand, AccountExportSelection, AccountQuery, AccountUpdateCommand

ENV_PREFIX = "AAR_"
ENV_EXTRA_PREFIX = f"{ENV_PREFIX}EXTRA_"
REGISTER_ENV_MAP = {
    "platform": ("PLATFORM", str),
    "email": ("EMAIL", str),
    "password": ("PASSWORD", str),
    "count": ("COUNT", int),
    "concurrency": ("CONCURRENCY", int),
    "proxy": ("PROXY", str),
    "executor_type": ("EXECUTOR_TYPE", str),
    "captcha_solver": ("CAPTCHA_SOLVER", str),
}
REGISTER_EXTRA_ENV_MAP = {
    "identity_provider": "IDENTITY_PROVIDER",
    "oauth_provider": "OAUTH_PROVIDER",
    "oauth_email_hint": "OAUTH_EMAIL_HINT",
    "chrome_user_data_dir": "CHROME_USER_DATA_DIR",
    "chrome_cdp_url": "CHROME_CDP_URL",
    "mail_provider": "MAIL_PROVIDER",
}


def _env_name(name: str) -> str:
    return f"{ENV_PREFIX}{name}"


def _get_env(name: str) -> str | None:
    value = os.getenv(_env_name(name))
    if value is None:
        return None
    text = value.strip()
    return text if text else None


def _parse_scalar(raw: str, caster):
    if caster is int:
        return int(raw)
    return raw


def _parse_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _read_env_defaults() -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, (env_name, caster) in REGISTER_ENV_MAP.items():
        raw = _get_env(env_name)
        if raw is not None:
            payload[key] = _parse_scalar(raw, caster)
    return payload


def _read_env_extras() -> dict[str, str]:
    extra: dict[str, str] = {}
    for key, env_name in REGISTER_EXTRA_ENV_MAP.items():
        value = _get_env(env_name)
        if value is not None:
            extra[key] = value
    for name, value in os.environ.items():
        if not name.startswith(ENV_EXTRA_PREFIX):
            continue
        extra_key = name[len(ENV_EXTRA_PREFIX):].strip().lower()
        if extra_key and value.strip():
            extra[extra_key] = value.strip()
    return extra


def _parse_kv_pairs(items: list[str]) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"额外参数格式错误: {item}，应为 KEY=VALUE")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"额外参数格式错误: {item}，KEY 不能为空")
        pairs[key] = value.strip()
    return pairs


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _format_rows(headers: list[str], rows: list[list[Any]]) -> str:
    widths = [len(header) for header in headers]
    normalized = [["" if value is None else str(value) for value in row] for row in rows]
    for row in normalized:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    lines = ["  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))]
    lines.append("  ".join("-" * widths[index] for index in range(len(headers))))
    for row in normalized:
        lines.append("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))
    return "\n".join(lines)


def _require_task(task_id: str, service: TasksQueryService) -> dict[str, Any]:
    task = service.get_task(task_id)
    if not task:
        raise ValueError(f"任务不存在: {task_id}")
    return task


def _stream_task_logs(task_id: str, *, follow: bool, poll_interval: float = 0.5, emit_logs: bool = True) -> dict[str, Any]:
    query_service = TasksQueryService()
    cursor = 0
    while True:
        response = query_service.list_events(task_id, since=cursor, limit=200)
        for item in response["items"]:
            cursor = max(cursor, int(item["id"] or 0))
            if emit_logs:
                print(item["line"])
        task = _require_task(task_id, query_service)
        if task["status"] in TERMINAL_TASK_STATUSES:
            return task
        if not follow:
            return task
        time.sleep(poll_interval)


def _build_register_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload = {
        "email": None,
        "password": None,
        "count": 1,
        "concurrency": 1,
        "proxy": None,
        "executor_type": "protocol",
        "captcha_solver": "auto",
        "extra": {},
    }
    payload.update(_read_env_defaults())
    extra = _read_env_extras()

    for key in REGISTER_ENV_MAP:
        value = getattr(args, key, None)
        if value is not None:
            payload[key] = value

    extra.update(_parse_kv_pairs(args.extra or []))
    payload["extra"] = extra

    platform = payload.get("platform")
    if not platform:
        raise ValueError(f"缺少平台，请通过 --platform 或 {_env_name('PLATFORM')} 提供")
    return payload


def _parse_json_object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("JSON 参数必须是对象")
    return value


def _write_export_artifact(artifact, output: str | None) -> Path:
    target = Path(output or artifact.filename)
    if target.is_dir():
        target = target / artifact.filename
    target.parent.mkdir(parents=True, exist_ok=True)
    content = artifact.content
    if isinstance(content, io.BytesIO):
        target.write_bytes(content.getvalue())
    elif isinstance(content, bytes):
        target.write_bytes(content)
    else:
        target.write_text(str(content))
    return target


def _print_or_json(args: argparse.Namespace, data: Any, text: str) -> int:
    if getattr(args, "json", False):
        _print_json(data)
    else:
        print(text)
    return 0


def cmd_platforms_list(args: argparse.Namespace) -> int:
    initialize_core(announce=not args.json)
    items = PlatformsService().list_platforms()
    if args.json:
        _print_json(items)
        return 0
    rows = [[item["name"], item["display_name"], item["version"], ",".join(item.get("supported_executors", []))] for item in items]
    print(_format_rows(["name", "display_name", "version", "executors"], rows))
    return 0


def cmd_tasks_list(args: argparse.Namespace) -> int:
    initialize_core(announce=not args.json)
    data = TasksQueryService().list_tasks(platform=args.platform or "", status=args.status or "", page=args.page, page_size=args.page_size)
    if args.json:
        _print_json(data)
        return 0
    rows = [[item["task_id"], item["platform"], item["status"], item["progress"], item["success"], item["error_count"]] for item in data["items"]]
    print(_format_rows(["task_id", "platform", "status", "progress", "success", "errors"], rows))
    print(f"total={data['total']} page={data['page']}")
    return 0


def cmd_tasks_logs(args: argparse.Namespace) -> int:
    initialize_core(announce=not args.json)
    try:
        task = _stream_task_logs(args.task_id, follow=args.follow, poll_interval=args.interval, emit_logs=not args.json)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        _print_json(task)
    return 0 if task["status"] not in {"failed", "interrupted", "cancelled"} else 1


def cmd_tasks_cancel(args: argparse.Namespace) -> int:
    initialize_core(announce=not args.json)
    task = TaskCommandsService().cancel_task(args.task_id)
    if not task:
        print(f"任务不存在: {args.task_id}", file=sys.stderr)
        return 1
    return _print_or_json(args, task, f"已请求取消任务: {task['task_id']} status={task['status']}")


def cmd_accounts_list(args: argparse.Namespace) -> int:
    initialize_core(announce=not args.json)
    data = AccountsService().list_accounts(AccountQuery(platform=args.platform or "", status=args.status or "", email=args.email or "", page=args.page, page_size=args.page_size))
    if args.json:
        _print_json(data)
        return 0
    rows = [[item["id"], item["platform"], item["email"], item["display_status"], item["lifecycle_status"], item["plan_state"]] for item in data["items"]]
    print(_format_rows(["id", "platform", "email", "display_status", "lifecycle", "plan"], rows))
    print(f"total={data['total']} page={data['page']}")
    return 0


def cmd_accounts_stats(args: argparse.Namespace) -> int:
    initialize_core(announce=not args.json)
    data = AccountsService().get_stats()
    if args.json:
        _print_json(data)
        return 0
    print(f"total={data['total']}")
    for label, bucket in (("by_platform", data.get("by_platform", {})), ("by_display_status", data.get("by_display_status", {})), ("by_lifecycle_status", data.get("by_lifecycle_status", {}))):
        print(label)
        if bucket:
            for key in sorted(bucket):
                print(f"  {key}: {bucket[key]}")
        else:
            print("  <empty>")
    return 0


def cmd_accounts_export(args: argparse.Namespace) -> int:
    initialize_core(announce=not args.json)
    selection = AccountExportSelection(platform=args.platform or "", ids=list(args.ids or []), select_all=bool(args.select_all), status_filter=args.status or "", search_filter=args.search or "")
    service = AccountExportsService()
    try:
        if args.format == "json":
            artifact = service.export_chatgpt_json(selection)
        elif args.format == "csv":
            artifact = service.export_chatgpt_csv(selection)
        elif args.format == "sub2api":
            artifact = service.export_chatgpt_sub2api(selection)
        elif args.format == "cpa":
            artifact = service.export_chatgpt_cpa(selection)
        elif args.format == "kiro-go":
            artifact = service.export_kiro_go(selection)
        elif args.format == "any2api":
            artifact = service.export_any2api(selection)
        else:
            raise ValueError(f"不支持的导出格式: {args.format}")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    target = _write_export_artifact(artifact, args.output)
    return _print_or_json(args, {"ok": True, "path": str(target), "filename": artifact.filename, "media_type": artifact.media_type}, f"已导出: {target}")


def cmd_accounts_create(args: argparse.Namespace) -> int:
    initialize_core(announce=not args.json)
    command = AccountCreateCommand(
        platform=args.platform,
        email=args.email,
        password=args.password,
        user_id=args.user_id or "",
        lifecycle_status=args.lifecycle_status,
        overview=_parse_json_object(args.overview),
        credentials=_parse_json_object(args.credentials),
        primary_token=args.primary_token or "",
        cashier_url=args.cashier_url or "",
        region=args.region or "",
        trial_end_time=args.trial_end_time or 0,
    )
    item = AccountsService().create_account(command)
    return _print_or_json(args, item, f"已创建账号: {item['id']} {item['email']}")


def cmd_accounts_update(args: argparse.Namespace) -> int:
    initialize_core(announce=not args.json)
    command = AccountUpdateCommand(
        password=args.password,
        user_id=args.user_id,
        lifecycle_status=args.lifecycle_status,
        overview=_parse_json_object(args.overview) if args.overview is not None else None,
        credentials=_parse_json_object(args.credentials) if args.credentials is not None else None,
        primary_token=args.primary_token,
        cashier_url=args.cashier_url,
        region=args.region,
        trial_end_time=args.trial_end_time,
    )
    item = AccountsService().update_account(args.account_id, command)
    if not item:
        print(f"账号不存在: {args.account_id}", file=sys.stderr)
        return 1
    return _print_or_json(args, item, f"已更新账号: {item['id']} {item['email']}")


def cmd_accounts_delete(args: argparse.Namespace) -> int:
    initialize_core(announce=not args.json)
    result = AccountsService().delete_account(args.account_id)
    if not result["ok"]:
        print(f"账号不存在: {args.account_id}", file=sys.stderr)
        return 1
    return _print_or_json(args, result, f"已删除账号: {args.account_id}")


def cmd_accounts_import(args: argparse.Namespace) -> int:
    initialize_core(announce=not args.json)
    text = Path(args.file).read_text()
    lines = text.splitlines()
    result = AccountsService().import_accounts(args.platform, lines)
    return _print_or_json(args, result, f"已导入账号: {result['created']}")


def cmd_accounts_check(args: argparse.Namespace) -> int:
    initialize_core(announce=not args.json)
    result = AccountChecksService().check_one_async(args.account_id)
    if not result:
        print(f"账号不存在: {args.account_id}", file=sys.stderr)
        return 1
    return _print_or_json(args, result, f"已创建检测任务: {result['task_id']}")


def cmd_accounts_check_all(args: argparse.Namespace) -> int:
    initialize_core(announce=not args.json)
    result = AccountChecksService().check_all_async(args.platform or "")
    return _print_or_json(args, result, f"已创建批量检测任务: {result['task_id']}")


def cmd_config_get(args: argparse.Namespace) -> int:
    initialize_core(announce=not args.json)
    data = ConfigService().get_config()
    if args.key:
        if args.key not in data:
            print(f"配置不存在: {args.key}", file=sys.stderr)
            return 1
        if args.json:
            _print_json({args.key: data[args.key]})
        else:
            print(data[args.key])
        return 0
    if args.json:
        _print_json(data)
        return 0
    rows = [[key, value] for key, value in sorted(data.items())]
    print(_format_rows(["key", "value"], rows))
    return 0


def cmd_config_set(args: argparse.Namespace) -> int:
    initialize_core(announce=not args.json)
    result = ConfigService().update_config({args.key: args.value})
    return _print_or_json(args, result, f"已更新配置: {args.key}")


def cmd_provider_definitions_list(args: argparse.Namespace) -> int:
    initialize_core(announce=not args.json)
    items = ProviderDefinitionsService().list_definitions(args.provider_type, enabled_only=args.enabled_only)
    if args.json:
        _print_json(items)
        return 0
    rows = [[item["id"], item["provider_key"], item["label"], item["driver_type"], item["enabled"]] for item in items]
    print(_format_rows(["id", "provider_key", "label", "driver_type", "enabled"], rows))
    return 0


def cmd_provider_drivers_list(args: argparse.Namespace) -> int:
    initialize_core(announce=not args.json)
    items = ProviderDefinitionsService().list_driver_templates(args.provider_type)
    if args.json:
        _print_json(items)
        return 0
    rows = [[item["driver_type"], item["label"], item.get("default_auth_mode", "")] for item in items]
    print(_format_rows(["driver_type", "label", "default_auth_mode"], rows))
    return 0


def cmd_provider_settings_list(args: argparse.Namespace) -> int:
    initialize_core(announce=not args.json)
    items = ProviderSettingsService().list_settings(args.provider_type)
    if args.json:
        _print_json(items)
        return 0
    rows = [[item["id"], item["provider_key"], item["display_name"], item["auth_mode"], item["enabled"], item["is_default"]] for item in items]
    print(_format_rows(["id", "provider_key", "display_name", "auth_mode", "enabled", "default"], rows))
    return 0


def cmd_provider_settings_save(args: argparse.Namespace) -> int:
    initialize_core(announce=not args.json)
    payload = {
        "id": args.id,
        "provider_type": args.provider_type,
        "provider_key": args.provider_key,
        "display_name": args.display_name or "",
        "auth_mode": args.auth_mode or "",
        "enabled": not args.disable,
        "is_default": bool(args.default),
        "config": _parse_json_object(args.config),
        "auth": _parse_json_object(args.auth),
        "metadata": _parse_json_object(args.metadata),
    }
    try:
        result = ProviderSettingsService().save_setting(payload)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return _print_or_json(args, result, f"已保存 provider setting: {result['item']['id']} {result['item']['provider_key']}")


def cmd_provider_settings_delete(args: argparse.Namespace) -> int:
    initialize_core(announce=not args.json)
    result = ProviderSettingsService().delete_setting(args.setting_id)
    if not result["ok"]:
        print(f"provider setting 不存在: {args.setting_id}", file=sys.stderr)
        return 1
    return _print_or_json(args, result, f"已删除 provider setting: {args.setting_id}")


def cmd_lifecycle_status(args: argparse.Namespace) -> int:
    initialize_core(announce=not args.json)
    data = {
        "running": lifecycle_manager._running,
        "check_interval_hours": lifecycle_manager.check_interval / 3600,
        "refresh_interval_hours": lifecycle_manager.refresh_interval / 3600,
        "warning_hours": lifecycle_manager.warning_hours,
    }
    if args.json:
        _print_json(data)
    else:
        print(_format_rows(["field", "value"], [[k, v] for k, v in data.items()]))
    return 0


def cmd_lifecycle_check(args: argparse.Namespace) -> int:
    initialize_core(announce=not args.json)
    data = check_accounts_validity(platform=args.platform or "", limit=args.limit)
    return _print_or_json(args, {"ok": True, "data": data}, f"检测完成: valid={data['valid']} invalid={data['invalid']} error={data['error']} skipped={data['skipped']}")


def cmd_lifecycle_refresh(args: argparse.Namespace) -> int:
    initialize_core(announce=not args.json)
    data = refresh_expiring_tokens(platform=args.platform or "", limit=args.limit)
    return _print_or_json(args, {"ok": True, "data": data}, f"刷新完成: refreshed={data['refreshed']} failed={data['failed']} skipped={data['skipped']}")


def cmd_lifecycle_warn(args: argparse.Namespace) -> int:
    initialize_core(announce=not args.json)
    data = flag_expiring_trials(hours_warning=args.hours)
    return _print_or_json(args, {"ok": True, "data": data}, f"预警完成: warned={data['warned']} expired={data['expired']} skipped={data['skipped']}")


def _run_register_create(args: argparse.Namespace) -> int:
    payload = _build_register_payload(args)
    task = TaskCommandsService().create_register_task(payload)
    if args.wait:
        final_task = _stream_task_logs(task["task_id"], follow=True, poll_interval=args.interval, emit_logs=not args.json)
        if args.json:
            _print_json(final_task)
        else:
            print(f"任务结束: {final_task['task_id']} status={final_task['status']}")
        return 0 if final_task["status"] == "succeeded" else 1
    return _print_or_json(args, task, f"任务已创建: {task['task_id']} status={task['status']}") if args.json else (print(f"任务已创建: {task['task_id']} status={task['status']}") or print("任务需要常驻运行时处理。可使用 `python cli.py serve`，或在 Web 服务运行时创建任务。") or 0)


def cmd_register_create(args: argparse.Namespace) -> int:
    try:
        if args.wait:
            with RuntimeManager(start_background_services=True, announce=not args.json):
                return _run_register_create(args)
        initialize_core(announce=not args.json)
        return _run_register_create(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def cmd_serve(args: argparse.Namespace) -> int:
    with RuntimeManager(start_background_services=True, announce=True):
        print("CLI runtime 已启动，按 Ctrl+C 退出")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("收到退出信号，正在停止")
            return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Any Auto Register CLI")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True

    platforms_parser = subparsers.add_parser("platforms", help="平台相关命令")
    platforms_sub = platforms_parser.add_subparsers(dest="platforms_command")
    platforms_sub.required = True
    platforms_list = platforms_sub.add_parser("list", help="列出平台")
    platforms_list.add_argument("--json", action="store_true", help="输出 JSON")
    platforms_list.set_defaults(func=cmd_platforms_list)

    tasks_parser = subparsers.add_parser("tasks", help="任务相关命令")
    tasks_sub = tasks_parser.add_subparsers(dest="tasks_command")
    tasks_sub.required = True
    tasks_list = tasks_sub.add_parser("list", help="列出任务")
    tasks_list.add_argument("--platform", default="")
    tasks_list.add_argument("--status", default="")
    tasks_list.add_argument("--page", type=int, default=1)
    tasks_list.add_argument("--page-size", type=int, default=20)
    tasks_list.add_argument("--json", action="store_true", help="输出 JSON")
    tasks_list.set_defaults(func=cmd_tasks_list)
    tasks_logs = tasks_sub.add_parser("logs", help="查看任务日志")
    tasks_logs.add_argument("task_id")
    tasks_logs.add_argument("-f", "--follow", action="store_true", help="持续跟踪到任务结束")
    tasks_logs.add_argument("--interval", type=float, default=0.5, help="轮询间隔秒数")
    tasks_logs.add_argument("--json", action="store_true", help="输出任务 JSON")
    tasks_logs.set_defaults(func=cmd_tasks_logs)
    tasks_cancel = tasks_sub.add_parser("cancel", help="取消任务")
    tasks_cancel.add_argument("task_id")
    tasks_cancel.add_argument("--json", action="store_true", help="输出 JSON")
    tasks_cancel.set_defaults(func=cmd_tasks_cancel)

    accounts_parser = subparsers.add_parser("accounts", help="账号相关命令")
    accounts_sub = accounts_parser.add_subparsers(dest="accounts_command")
    accounts_sub.required = True
    accounts_list = accounts_sub.add_parser("list", help="列出账号")
    accounts_list.add_argument("--platform", default="")
    accounts_list.add_argument("--status", default="")
    accounts_list.add_argument("--email", default="")
    accounts_list.add_argument("--page", type=int, default=1)
    accounts_list.add_argument("--page-size", type=int, default=20)
    accounts_list.add_argument("--json", action="store_true", help="输出 JSON")
    accounts_list.set_defaults(func=cmd_accounts_list)
    accounts_stats = accounts_sub.add_parser("stats", help="账号统计")
    accounts_stats.add_argument("--json", action="store_true", help="输出 JSON")
    accounts_stats.set_defaults(func=cmd_accounts_stats)
    accounts_export = accounts_sub.add_parser("export", help="导出账号")
    accounts_export.add_argument("--format", required=True, choices=["json", "csv", "sub2api", "cpa", "kiro-go", "any2api"])
    accounts_export.add_argument("--platform", default="")
    accounts_export.add_argument("--status", default="")
    accounts_export.add_argument("--search", default="")
    accounts_export.add_argument("--id", dest="ids", action="append", type=int, default=[])
    accounts_export.add_argument("--select-all", action="store_true")
    accounts_export.add_argument("-o", "--output")
    accounts_export.add_argument("--json", action="store_true", help="输出导出结果元数据 JSON")
    accounts_export.set_defaults(func=cmd_accounts_export)
    accounts_create = accounts_sub.add_parser("create", help="创建账号")
    accounts_create.add_argument("platform")
    accounts_create.add_argument("email")
    accounts_create.add_argument("password")
    accounts_create.add_argument("--user-id")
    accounts_create.add_argument("--lifecycle-status", default="registered")
    accounts_create.add_argument("--overview")
    accounts_create.add_argument("--credentials")
    accounts_create.add_argument("--primary-token")
    accounts_create.add_argument("--cashier-url")
    accounts_create.add_argument("--region")
    accounts_create.add_argument("--trial-end-time", type=int)
    accounts_create.add_argument("--json", action="store_true", help="输出 JSON")
    accounts_create.set_defaults(func=cmd_accounts_create)
    accounts_update = accounts_sub.add_parser("update", help="更新账号")
    accounts_update.add_argument("account_id", type=int)
    accounts_update.add_argument("--password")
    accounts_update.add_argument("--user-id")
    accounts_update.add_argument("--lifecycle-status")
    accounts_update.add_argument("--overview")
    accounts_update.add_argument("--credentials")
    accounts_update.add_argument("--primary-token")
    accounts_update.add_argument("--cashier-url")
    accounts_update.add_argument("--region")
    accounts_update.add_argument("--trial-end-time", type=int)
    accounts_update.add_argument("--json", action="store_true", help="输出 JSON")
    accounts_update.set_defaults(func=cmd_accounts_update)
    accounts_delete = accounts_sub.add_parser("delete", help="删除账号")
    accounts_delete.add_argument("account_id", type=int)
    accounts_delete.add_argument("--json", action="store_true", help="输出 JSON")
    accounts_delete.set_defaults(func=cmd_accounts_delete)
    accounts_import = accounts_sub.add_parser("import", help="导入账号")
    accounts_import.add_argument("platform")
    accounts_import.add_argument("file")
    accounts_import.add_argument("--json", action="store_true", help="输出 JSON")
    accounts_import.set_defaults(func=cmd_accounts_import)
    accounts_check = accounts_sub.add_parser("check", help="异步检测单个账号")
    accounts_check.add_argument("account_id", type=int)
    accounts_check.add_argument("--json", action="store_true", help="输出 JSON")
    accounts_check.set_defaults(func=cmd_accounts_check)
    accounts_check_all = accounts_sub.add_parser("check-all", help="异步批量检测账号")
    accounts_check_all.add_argument("--platform", default="")
    accounts_check_all.add_argument("--json", action="store_true", help="输出 JSON")
    accounts_check_all.set_defaults(func=cmd_accounts_check_all)

    config_parser = subparsers.add_parser("config", help="配置相关命令")
    config_sub = config_parser.add_subparsers(dest="config_command")
    config_sub.required = True
    config_get = config_sub.add_parser("get", help="读取配置")
    config_get.add_argument("key", nargs="?")
    config_get.add_argument("--json", action="store_true", help="输出 JSON")
    config_get.set_defaults(func=cmd_config_get)
    config_set = config_sub.add_parser("set", help="写入配置")
    config_set.add_argument("key")
    config_set.add_argument("value")
    config_set.add_argument("--json", action="store_true", help="输出 JSON")
    config_set.set_defaults(func=cmd_config_set)

    providers_parser = subparsers.add_parser("providers", help="provider 配置命令")
    providers_sub = providers_parser.add_subparsers(dest="providers_command")
    providers_sub.required = True
    provider_defs = providers_sub.add_parser("definitions", help="列出 provider definitions")
    provider_defs.add_argument("provider_type", choices=["mailbox", "captcha"])
    provider_defs.add_argument("--enabled-only", action="store_true")
    provider_defs.add_argument("--json", action="store_true", help="输出 JSON")
    provider_defs.set_defaults(func=cmd_provider_definitions_list)
    provider_drivers = providers_sub.add_parser("drivers", help="列出 provider driver 模板")
    provider_drivers.add_argument("provider_type", choices=["mailbox", "captcha"])
    provider_drivers.add_argument("--json", action="store_true", help="输出 JSON")
    provider_drivers.set_defaults(func=cmd_provider_drivers_list)
    provider_settings = providers_sub.add_parser("settings", help="列出 provider settings")
    provider_settings.add_argument("provider_type", choices=["mailbox", "captcha"])
    provider_settings.add_argument("--json", action="store_true", help="输出 JSON")
    provider_settings.set_defaults(func=cmd_provider_settings_list)
    provider_save = providers_sub.add_parser("save", help="保存 provider setting")
    provider_save.add_argument("provider_type", choices=["mailbox", "captcha"])
    provider_save.add_argument("provider_key")
    provider_save.add_argument("--id", type=int)
    provider_save.add_argument("--display-name")
    provider_save.add_argument("--auth-mode")
    provider_save.add_argument("--config")
    provider_save.add_argument("--auth")
    provider_save.add_argument("--metadata")
    provider_save.add_argument("--default", action="store_true")
    provider_save.add_argument("--disable", action="store_true")
    provider_save.add_argument("--json", action="store_true", help="输出 JSON")
    provider_save.set_defaults(func=cmd_provider_settings_save)
    provider_delete = providers_sub.add_parser("delete", help="删除 provider setting")
    provider_delete.add_argument("setting_id", type=int)
    provider_delete.add_argument("--json", action="store_true", help="输出 JSON")
    provider_delete.set_defaults(func=cmd_provider_settings_delete)

    lifecycle_parser = subparsers.add_parser("lifecycle", help="生命周期相关命令")
    lifecycle_sub = lifecycle_parser.add_subparsers(dest="lifecycle_command")
    lifecycle_sub.required = True
    lifecycle_status_p = lifecycle_sub.add_parser("status", help="查看生命周期运行状态")
    lifecycle_status_p.add_argument("--json", action="store_true", help="输出 JSON")
    lifecycle_status_p.set_defaults(func=cmd_lifecycle_status)
    lifecycle_check_p = lifecycle_sub.add_parser("check", help="手动触发账号有效性检测")
    lifecycle_check_p.add_argument("--platform", default="")
    lifecycle_check_p.add_argument("--limit", type=int, default=100)
    lifecycle_check_p.add_argument("--json", action="store_true", help="输出 JSON")
    lifecycle_check_p.set_defaults(func=cmd_lifecycle_check)
    lifecycle_refresh_p = lifecycle_sub.add_parser("refresh", help="手动触发 token 刷新")
    lifecycle_refresh_p.add_argument("--platform", default="")
    lifecycle_refresh_p.add_argument("--limit", type=int, default=50)
    lifecycle_refresh_p.add_argument("--json", action="store_true", help="输出 JSON")
    lifecycle_refresh_p.set_defaults(func=cmd_lifecycle_refresh)
    lifecycle_warn_p = lifecycle_sub.add_parser("warn", help="手动触发 trial 过期预警")
    lifecycle_warn_p.add_argument("--hours", type=int, default=48)
    lifecycle_warn_p.add_argument("--json", action="store_true", help="输出 JSON")
    lifecycle_warn_p.set_defaults(func=cmd_lifecycle_warn)

    register_parser = subparsers.add_parser("register", help="注册任务命令")
    register_sub = register_parser.add_subparsers(dest="register_command")
    register_sub.required = True
    register_create = register_sub.add_parser("create", help="创建注册任务")
    register_create.add_argument("--platform")
    register_create.add_argument("--email")
    register_create.add_argument("--password")
    register_create.add_argument("--count", type=int)
    register_create.add_argument("--concurrency", type=int)
    register_create.add_argument("--proxy")
    register_create.add_argument("--executor-type")
    register_create.add_argument("--captcha-solver")
    register_create.add_argument("--extra", action="append", default=[], metavar="KEY=VALUE", help="附加 extra 字段，可重复")
    register_create.add_argument("--wait", action="store_true", help="启动本地运行时并等待任务结束")
    register_create.add_argument("--interval", type=float, default=0.5, help="等待模式下的日志轮询间隔")
    register_create.add_argument("--json", action="store_true", help="输出 JSON")
    register_create.set_defaults(func=cmd_register_create)

    serve_parser = subparsers.add_parser("serve", help="启动本地任务运行时")
    serve_parser.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
