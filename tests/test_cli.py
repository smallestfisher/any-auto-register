from __future__ import annotations

import json
from pathlib import Path

from application.accounts import AccountsService
from application.config import ConfigService
from application.tasks import create_task
from cli import _build_register_payload, main
from domain.accounts import AccountCreateCommand


class _Args:
    platform = None
    email = None
    password = None
    count = None
    concurrency = None
    proxy = None
    executor_type = None
    captcha_solver = None
    extra = []
    wait = False
    interval = 0.01
    json = False


def test_build_register_payload_from_env(monkeypatch):
    monkeypatch.setenv("AAR_PLATFORM", "chatgpt")
    monkeypatch.setenv("AAR_COUNT", "3")
    monkeypatch.setenv("AAR_EXECUTOR_TYPE", "protocol")
    monkeypatch.setenv("AAR_IDENTITY_PROVIDER", "mailbox")
    monkeypatch.setenv("AAR_EXTRA_MAIL_PROVIDER", "moemail")
    payload = _build_register_payload(_Args())
    assert payload["platform"] == "chatgpt"
    assert payload["count"] == 3
    assert payload["executor_type"] == "protocol"
    assert payload["extra"]["identity_provider"] == "mailbox"
    assert payload["extra"]["mail_provider"] == "moemail"


def test_build_register_payload_cli_overrides_env(monkeypatch):
    monkeypatch.setenv("AAR_PLATFORM", "chatgpt")
    monkeypatch.setenv("AAR_COUNT", "2")

    class Args(_Args):
        platform = "cursor"
        count = 5
        extra = ["identity_provider=oauth_browser"]

    payload = _build_register_payload(Args())
    assert payload["platform"] == "cursor"
    assert payload["count"] == 5
    assert payload["extra"]["identity_provider"] == "oauth_browser"


def test_main_platforms_list_json(capsys):
    exit_code = main(["platforms", "list", "--json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    data = json.loads(captured.out)
    assert any(item["name"] == "chatgpt" for item in data)


def test_main_tasks_list_json(capsys):
    create_task(task_type="register", platform="chatgpt", payload={"platform": "chatgpt"}, progress_total=1)
    exit_code = main(["tasks", "list", "--json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    data = json.loads(captured.out)
    assert data["total"] == 1
    assert data["items"][0]["platform"] == "chatgpt"


def test_main_tasks_cancel_json(capsys):
    task = create_task(task_type="register", platform="chatgpt", payload={"platform": "chatgpt"}, progress_total=1)
    exit_code = main(["tasks", "cancel", task["task_id"], "--json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    data = json.loads(captured.out)
    assert data["status"] == "cancelled"


def test_main_config_set_and_get_json(capsys):
    exit_code = main(["config", "set", "default_executor", "headed", "--json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    result = json.loads(captured.out)
    assert result["ok"] is True

    exit_code = main(["config", "get", "default_executor", "--json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    data = json.loads(captured.out)
    assert data["default_executor"] == "headed"


def test_main_accounts_list_and_stats_json(capsys):
    service = AccountsService()
    service.create_account(AccountCreateCommand(platform="chatgpt", email="foo@example.com", password="secret"))

    exit_code = main(["accounts", "list", "--json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    data = json.loads(captured.out)
    assert data["total"] == 1
    assert data["items"][0]["email"] == "foo@example.com"

    exit_code = main(["accounts", "stats", "--json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    stats = json.loads(captured.out)
    assert stats["total"] == 1
    assert stats["by_platform"]["chatgpt"] == 1


def test_main_accounts_export_json_file(capsys, tmp_path):
    service = AccountsService()
    service.create_account(AccountCreateCommand(platform="chatgpt", email="bar@example.com", password="secret"))
    output = tmp_path / "accounts.json"

    exit_code = main(["accounts", "export", "--format", "json", "--platform", "chatgpt", "--select-all", "-o", str(output), "--json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    meta = json.loads(captured.out)
    assert Path(meta["path"]).exists()
    exported = json.loads(output.read_text())
    assert exported[0]["email"] == "bar@example.com"


def test_main_accounts_create_update_delete_json(capsys):
    exit_code = main(["accounts", "create", "chatgpt", "u@example.com", "pw", "--json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    created = json.loads(captured.out)
    account_id = created["id"]

    exit_code = main(["accounts", "update", str(account_id), "--user-id", "uid-1", "--json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    updated = json.loads(captured.out)
    assert updated["user_id"] == "uid-1"

    exit_code = main(["accounts", "delete", str(account_id), "--json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    deleted = json.loads(captured.out)
    assert deleted["ok"] is True


def test_main_accounts_import_json(capsys, tmp_path):
    source = tmp_path / "import.txt"
    source.write_text("import@example.com secret\n")
    exit_code = main(["accounts", "import", "chatgpt", str(source), "--json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    data = json.loads(captured.out)
    assert data["created"] == 1


def test_main_providers_save_and_list_json(capsys):
    exit_code = main([
        "providers", "save", "mailbox", "moemail",
        "--display-name", "My MoeMail",
        "--config", '{"api_url":"https://mail.example.com"}',
        "--auth", '{"admin_token":"secret-token"}',
        "--default",
        "--json",
    ])
    captured = capsys.readouterr()
    assert exit_code == 0
    saved = json.loads(captured.out)
    assert saved["ok"] is True
    assert saved["item"]["provider_key"] == "moemail"

    exit_code = main(["providers", "settings", "mailbox", "--json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    items = json.loads(captured.out)
    target = next(item for item in items if item["provider_key"] == "moemail")
    assert target["display_name"] == "My MoeMail"
    assert target["is_default"] is True


def test_main_providers_save_with_field_kv(capsys):
    exit_code = main([
        "providers", "save", "mailbox", "cfworker",
        "--display-name", "My CFWorker",
        "--set", "cfworker_api_url=https://apimail.example.com",
        "--set", "cfworker_admin_token=secret-token",
        "--set", "cfworker_domain=example.com",
        "--set", "cfworker_fingerprint=finger-123",
        "--default",
        "--json",
    ])
    captured = capsys.readouterr()
    assert exit_code == 0
    saved = json.loads(captured.out)
    item = saved["item"]
    assert item["provider_key"] == "cfworker"
    assert item["config"]["cfworker_api_url"] == "https://apimail.example.com"
    assert item["config"]["cfworker_domain"] == "example.com"
    assert item["auth"]["cfworker_admin_token"] == "secret-token"
    assert item["auth"]["cfworker_fingerprint"] == "finger-123"


def test_main_provider_drivers_support_sms_and_proxy(capsys):
    exit_code = main(["providers", "drivers", "sms", "--json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    sms_items = json.loads(captured.out)
    assert any(item["provider_key"] == "sms_activate" for item in sms_items)

    exit_code = main(["providers", "drivers", "proxy", "--json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    proxy_items = json.loads(captured.out)
    assert any(item["provider_key"] == "api_extract" for item in proxy_items)


def test_main_config_set_supports_runtime_keys(capsys):
    for key, value in [
        ("cpa_api_url", "https://cpa.example.com"),
        ("any2api_url", "https://any2api.example.com"),
        ("any2api_password", "secret-password"),
        ("sms_activate_api_key", "sms-key"),
        ("proxy_api_url", "https://proxy.example.com/get"),
    ]:
        exit_code = main(["config", "set", key, value, "--json"])
        captured = capsys.readouterr()
        assert exit_code == 0
        result = json.loads(captured.out)
        assert result["ok"] is True

    data = ConfigService().get_config()
    assert data["cpa_api_url"] == "https://cpa.example.com"
    assert data["any2api_url"] == "https://any2api.example.com"
    assert data["any2api_password"] == "secret-password"
    assert data["sms_activate_api_key"] == "sms-key"
    assert data["proxy_api_url"] == "https://proxy.example.com/get"


def test_main_lifecycle_status_json(capsys):
    exit_code = main(["lifecycle", "status", "--json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    data = json.loads(captured.out)
    assert "running" in data
    assert "check_interval_hours" in data
