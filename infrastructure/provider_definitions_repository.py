from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session, select

from core.db import ProviderDefinitionModel, ProviderSettingModel, engine
from core.provider_drivers import (
    get_driver_template,
    list_builtin_provider_definitions,
    list_driver_templates,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProviderDefinitionsRepository:
    def ensure_seeded(self) -> None:
        with Session(engine) as session:
            existing = {
                (item.provider_type, item.provider_key): item
                for item in session.exec(select(ProviderDefinitionModel)).all()
            }
            changed = False
            for provider_type in ("mailbox", "captcha", "sms", "proxy"):
                for template in list_builtin_provider_definitions(provider_type):
                    provider_key = str(template.get("provider_key") or "").strip()
                    driver_type = str(template.get("driver_type") or "").strip()
                    if not provider_key or not driver_type:
                        continue
                    item = existing.get((provider_type, provider_key))
                    is_new = item is None
                    if not item:
                        item = ProviderDefinitionModel(
                            provider_type=provider_type,
                            provider_key=provider_key,
                        )
                        item.created_at = _utcnow()
                        item.enabled = True
                        changed = True
                    if not item.label:
                        item.label = str(template.get("label") or provider_key)
                        changed = True
                    if not item.description:
                        item.description = str(template.get("description") or "")
                        changed = True
                    if not item.driver_type:
                        item.driver_type = driver_type
                        changed = True
                    if not item.default_auth_mode:
                        item.default_auth_mode = str(template.get("default_auth_mode") or "")
                        changed = True
                    if is_new or item.is_builtin:
                        next_auth_modes = list(template.get("auth_modes") or [])
                        next_fields = list(template.get("fields") or [])
                        next_default_auth_mode = str(template.get("default_auth_mode") or "")
                        if item.driver_type != driver_type:
                            item.driver_type = driver_type
                            changed = True
                        if item.default_auth_mode != next_default_auth_mode:
                            item.default_auth_mode = next_default_auth_mode
                            changed = True
                        if item.get_auth_modes() != next_auth_modes:
                            item.set_auth_modes(next_auth_modes)
                            changed = True
                        if item.get_fields() != next_fields:
                            item.set_fields(next_fields)
                            changed = True
                    elif not item.get_auth_modes():
                        item.set_auth_modes(list(template.get("auth_modes") or []))
                        changed = True
                    if not item.get_fields():
                        item.set_fields(list(template.get("fields") or []))
                        changed = True
                    if not item.get_metadata():
                        item.set_metadata(dict(template.get("metadata") or {}))
                        changed = True
                    if not item.is_builtin:
                        item.is_builtin = True
                        changed = True
                    item.updated_at = _utcnow()
                    session.add(item)
            if changed:
                session.commit()

    def list_by_type(self, provider_type: str, *, enabled_only: bool = False) -> list[ProviderDefinitionModel]:
        self.ensure_seeded()
        with Session(engine) as session:
            query = select(ProviderDefinitionModel).where(ProviderDefinitionModel.provider_type == provider_type)
            if enabled_only:
                query = query.where(ProviderDefinitionModel.enabled == True)  # noqa: E712
            return session.exec(query.order_by(ProviderDefinitionModel.id)).all()

    def get_by_key(self, provider_type: str, provider_key: str) -> ProviderDefinitionModel | None:
        self.ensure_seeded()
        with Session(engine) as session:
            return session.exec(
                select(ProviderDefinitionModel)
                .where(ProviderDefinitionModel.provider_type == provider_type)
                .where(ProviderDefinitionModel.provider_key == provider_key)
            ).first()

    def save(
        self,
        *,
        definition_id: int | None,
        provider_type: str,
        provider_key: str,
        label: str,
        description: str,
        driver_type: str,
        enabled: bool,
        default_auth_mode: str = "",
        metadata: dict | None = None,
    ) -> ProviderDefinitionModel:
        template = get_driver_template(provider_type, driver_type)
        if not template:
            raise ValueError(f"未知 provider driver: {provider_type}/{driver_type}")

        with Session(engine) as session:
            if definition_id:
                item = session.get(ProviderDefinitionModel, definition_id)
                if not item:
                    raise ValueError("provider definition 不存在")
            else:
                item = session.exec(
                    select(ProviderDefinitionModel)
                    .where(ProviderDefinitionModel.provider_type == provider_type)
                    .where(ProviderDefinitionModel.provider_key == provider_key)
                ).first()
                if not item:
                    item = ProviderDefinitionModel(
                        provider_type=provider_type,
                        provider_key=provider_key,
                    )
                    item.created_at = _utcnow()

            item.provider_type = provider_type
            item.provider_key = provider_key
            item.label = label or provider_key
            item.description = description or ""
            item.driver_type = driver_type
            item.default_auth_mode = default_auth_mode or item.default_auth_mode or str(template.get("default_auth_mode") or "")
            item.enabled = bool(enabled)
            if not item.get_auth_modes():
                item.set_auth_modes(list(template.get("auth_modes") or []))
            if not item.get_fields():
                item.set_fields(list(template.get("fields") or []))
            item.set_metadata(dict(metadata or {}))
            item.updated_at = _utcnow()
            session.add(item)
            session.commit()
            session.refresh(item)
            return item

    def delete(self, definition_id: int) -> bool:
        with Session(engine) as session:
            item = session.get(ProviderDefinitionModel, definition_id)
            if not item:
                return False
            has_settings = session.exec(
                select(ProviderSettingModel)
                .where(ProviderSettingModel.provider_type == item.provider_type)
                .where(ProviderSettingModel.provider_key == item.provider_key)
            ).first()
            if has_settings:
                raise ValueError("请先删除对应 provider 配置，再删除 definition")
            session.delete(item)
            session.commit()
            return True

    def list_driver_templates(self, provider_type: str) -> list[dict]:
        return list_driver_templates(provider_type)
